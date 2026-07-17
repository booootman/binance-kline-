#!/usr/bin/env python3
"""Offline regression checks for the dashboard's high-risk shared paths."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import io
import json
import os
import sys
import threading
import time
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["BIAN_AUTH_ENABLED"] = "0"

from bian_dashboard import analyzer  # noqa: E402
from bian_dashboard import server  # noqa: E402
from bian_dashboard import storage as storage_module  # noqa: E402
import deploy  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def candle(open_ms, open_price, high, low, close, close_ms=None):
    return [
        open_ms,
        str(open_price),
        str(high),
        str(low),
        str(close),
        "0",
        close_ms if close_ms is not None else open_ms + 59_999,
    ]


def test_cache_and_symbol_contracts():
    check(server.cache_key(["TLMUSDT", "DOGEUSDT"]) == "DOGEUSDT,TLMUSDT", "cache key must be canonical")
    payload = {"symbols": ["DOGEUSDT", "TLMUSDT"], "data": []}
    check(server.payload_matches(payload, ["TLMUSDT", "DOGEUSDT"]), "cache payload must match symbol permutations")
    query = "symbols=" + ",".join(f"S{i}USDT" for i in range(server.MAX_SYMBOLS + 1))
    try:
        server.parse_symbols(query, reject_overflow=True)
    except server.BadRequestError:
        pass
    else:
        raise AssertionError("oversized symbol requests must be rejected")


def test_tick_rounding_and_cache_lock_ownership():
    cases = (
        (1.00001, 0.00001),
        (1.00009, 0.00001),
        (0.1234567, 0.000001),
        (123.456, 0.05),
    )
    for value, tick in cases:
        down = analyzer.round_to_tick(value, tick, "down")
        nearest = analyzer.round_to_tick(value, tick)
        up = analyzer.round_to_tick(value, tick, "up")
        check(down <= value <= up, f"directional tick rounding crossed input for {value}/{tick}")
        check(down <= nearest <= up, f"nearest tick rounding left directional bounds for {value}/{tick}")
        for result in (down, nearest, up):
            units = Decimal(str(result)) / Decimal(str(tick))
            check(units == units.to_integral_value(), f"tick rounding produced an illegal price: {result}/{tick}")

    original_cache_file = analyzer.BACKTEST_CACHE_FILE
    lock_path = ROOT / "runtime" / f"smoke-backtest-{os.getpid()}-{time.time_ns()}.json.lock"
    analyzer.BACKTEST_CACHE_FILE = str(lock_path)[:-5]
    try:
        lock = analyzer.acquire_backtest_cache_lock()
        check(lock is not None, "backtest cache lock must be acquired")
        check(analyzer.acquire_backtest_cache_lock(0.05) is None, "a held OS cache lock must reject a second owner")
        original_unlink = analyzer.os.unlink
        analyzer.os.unlink = lambda path: (_ for _ in ()).throw(AssertionError(f"cache lock release must not unlink {path}"))
        try:
            analyzer.release_backtest_cache_lock(lock)
        finally:
            analyzer.os.unlink = original_unlink
        replacement = analyzer.acquire_backtest_cache_lock(0.05)
        check(replacement is not None, "cache lock must be acquirable after the owner releases it")
        analyzer.release_backtest_cache_lock(replacement)
    finally:
        analyzer.BACKTEST_CACHE_FILE = original_cache_file
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def test_snapshot_fallback_and_logout_cookie():
    original_time = server.time.time
    server.time.time = lambda: 123.0
    try:
        check(server.parse_snapshot_ms("", fallback=None) == 123_000, "None fallback must use current time")
        check(server.parse_snapshot_ms("invalid", fallback=0) == 0, "zero timestamp fallback must not become current time")
        check(server.parse_snapshot_ms("", fallback=-1) == -1_000, "negative timestamp fallback must be preserved")
        valid = server.parse_snapshot_ms("2026-07-17 00:00:00", fallback=0)
        check(valid != 0, "a valid timestamp must win over its fallback")
    finally:
        server.time.time = original_time

    class BrokenLogoutStorage:
        def delete_auth_session(self, token):
            check(token == "A" * 43, "logout must delete the current session token")
            raise TimeoutError("database unavailable")

    handler = object.__new__(server.Handler)
    response = {"headers": []}
    handler.auth_token = lambda: "A" * 43
    handler.send_response = lambda status: response.update(status=status)
    handler.send_header = lambda name, value: response["headers"].append((name, value))
    handler.end_headers = lambda: response.update(ended=True)
    handler.wfile = io.BytesIO()
    original_storage = server.storage
    original_auth_enabled = server.AUTH_ENABLED
    previous_disabled = server.LOG.disabled
    server.storage = BrokenLogoutStorage()
    server.AUTH_ENABLED = True
    server.LOG.disabled = True
    try:
        handler.logout_api()
    finally:
        server.storage = original_storage
        server.AUTH_ENABLED = original_auth_enabled
        server.LOG.disabled = previous_disabled
    check(response.get("status") == 503 and response.get("ended"), "logout must report a non-durable session revocation")
    cookies = [value for name, value in response["headers"] if name.lower() == "set-cookie"]
    check(cookies and "Max-Age=0" in cookies[0], "logout failure path must still expire the browser cookie")
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    check(payload.get("authenticated") is False and payload.get("revoked") is False, "logout failure must not claim durable revocation")

    local_response = {"headers": []}
    handler.send_response = lambda status: local_response.update(status=status)
    handler.send_header = lambda name, value: local_response["headers"].append((name, value))
    handler.end_headers = lambda: local_response.update(ended=True)
    handler.wfile = io.BytesIO()
    server.storage = BrokenLogoutStorage()
    server.AUTH_ENABLED = False
    try:
        handler.logout_api()
    finally:
        server.storage = original_storage
        server.AUTH_ENABLED = original_auth_enabled
    local_payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    check(local_response.get("status") == 200 and local_payload.get("revoked") is True, "auth-disabled logout must ignore stale cookies")

    route_handler = object.__new__(server.Handler)
    route_handler.path = "/api/logout"
    route_handler.require_same_origin_post = lambda: True
    route_handler.require_auth = lambda _path: (_ for _ in ()).throw(AssertionError("logout must not require a live database session"))
    route_called = []
    route_handler.logout_api = lambda: route_called.append(True)
    route_handler.do_POST()
    check(route_called == [True], "logout routing must reach revocation before current-session authentication")
    check(server.valid_auth_session_token("A" * 43), "generated URL-safe session token shape must be accepted")
    check(not server.valid_auth_session_token("会" * 43), "non-ASCII cookie text must not create revocation tombstones")


def test_logout_revocation_tombstone():
    original_file = storage_module.AUTH_SESSION_REVOCATION_FILE
    test_file = ROOT / "runtime" / f"smoke-auth-revocations-{os.getpid()}-{time.time_ns()}.json"
    storage_module.AUTH_SESSION_REVOCATION_FILE = str(test_file)
    token = "mysql-outage-session"
    try:
        store = storage_module.DashboardStorage()
        store.mysql_configured = True
        store.mysql_available = lambda: False
        check(store.delete_auth_session(token), "logout must be durable when MySQL is down but the tombstone file is writable")
        with store._auth_revocation_lock:
            revocations = store._load_auth_session_revocations_locked()
        token_hash = storage_module.session_token_hash(token)
        check(token_hash in revocations, "MySQL outage logout must persist the token hash before database deletion")
        if os.name != "nt":
            check(os.stat(test_file).st_mode & 0o777 == 0o600, "auth revocation file must be private")

        class FakeCursor:
            def __init__(self):
                self.statements = []

            def execute(self, sql, params=()):
                self.statements.append((" ".join(str(sql).split()), params))

        class FakeConnection:
            def __init__(self):
                self.cursor_value = FakeCursor()
                self.commits = 0
                self.rollbacks = 0

            def cursor(self):
                return self.cursor_value

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

            def close(self):
                pass

        connection = FakeConnection()
        store.mysql_available = lambda: True
        store._mysql_connect = lambda: connection
        store._ensure_auth_schema = lambda _conn: None
        check(store.user_for_session(token) is None, "a tombstoned token must be rejected after MySQL recovers")
        deletes = [item for item in connection.cursor_value.statements if item[0].startswith("DELETE FROM bian_auth_sessions")]
        check(deletes and deletes[0][1] == (token_hash,), "recovery must delete the tombstoned database session")
        check(connection.commits == 1 and connection.rollbacks == 0, "tombstone reconciliation must commit the session delete")
        with store._auth_revocation_lock:
            check(store._load_auth_session_revocations_locked() == {}, "committed database revocation must clear the tombstone")
    finally:
        storage_module.AUTH_SESSION_REVOCATION_FILE = original_file
        try:
            test_file.unlink()
        except FileNotFoundError:
            pass


def test_review_time_boundaries():
    captured = {}
    original = server.request_binance_json
    try:
        def fake_request(path, params, timeout=20):
            captured.update(params)
            return []

        server.request_binance_json = fake_request
        snapshot_ms = 1_700_000_030_000
        end_ms = snapshot_ms + 5 * 60_000
        server.fetch_review_klines("DOGEUSDT", snapshot_ms, end_ms)
        expected_start_ms = snapshot_ms + (-snapshot_ms % 60_000)
        check(captured["startTime"] == expected_start_ms, "review fetch must start with the first full post-publication minute")
        check(captured["endTime"] == end_ms, "review fetch must stop at the exact horizon")
    finally:
        server.request_binance_json = original

    snapshot_ms = 90_000
    record = {
        "side": "long",
        "entry_price": 99,
        "stop_price": 90,
        "snapshot_price": 0,
        "snapshot_at_ms": snapshot_ms,
    }
    candles = [
        candle(60_000, 100, 101, 98, 100, 119_999),
        candle(120_000, 100, 103, 100, 102, 179_999),
        candle(360_000, 102, 999, 1, 500, 419_999),
    ]
    result = server.evaluate_horizon_from_klines(record, "5m", 5 * 60_000, candles)
    check(result["bars"] == 1, "partial start and post-horizon bars must be excluded")
    check(result["gross_max_profit_pct"] < 10, "post-horizon high must not leak into review profit")
    check(result["entry_reached"] is False, "pre-publication low must not create a post-publication entry")

    entered_record = dict(record, snapshot_price=99)
    stop_result = server.evaluate_horizon_from_klines(
        entered_record,
        "5m",
        5 * 60_000,
        [
            candle(60_000, 99, 100, 89, 99, 119_999),
            candle(120_000, 99, 101, 95, 100, 179_999),
        ],
    )
    check(stop_result["entry_reached"] is True, "publication price should still establish an immediate entry")
    check(stop_result["stop_hit"] is False, "pre-publication low must not create a post-publication stop")

    aligned_record = dict(record, snapshot_at_ms=120_000)
    aligned_result = server.evaluate_horizon_from_klines(
        aligned_record,
        "5m",
        5 * 60_000,
        [candle(120_000, 100, 101, 98, 100, 179_999)],
    )
    check(aligned_result["entry_reached"] is True, "a candle starting exactly at publication remains usable")
    check(aligned_result["entry_time_ms"] == 120_000, "aligned entry time must equal publication time")


def sample_market_payload(observed_at_ms):
    return {
        "published_at_ms": 100_000,
        "generated_at": "2026-07-16 12:00:00",
        "data": [
            {
                "symbol": "DOGEUSDT",
                "last": 100,
                "price_observed_at_ms": 10_000,
                "publication_price": 101,
                "publication_price_observed_at_ms": observed_at_ms,
                "publication_price_source": "test",
                "bias": "偏多",
                "pct_24h": 1,
                "funding_rate": 0,
                "indicators": {},
                "timeframe_advice": [
                    {
                        "name": "短线",
                        "bias": "偏多",
                        "long_entry": 100,
                        "stop_hint": 95,
                        "confidence": 60,
                        "direction_score": 65,
                        "execution_score": 60,
                        "risk_gate": "正常",
                        "candle_state": "已完成K线",
                        "trigger_check": {"status": "watch"},
                    }
                ],
            }
        ],
    }


def test_publication_price_alignment():
    fresh = server.build_signal_review_records(sample_market_payload(99_500))[0]
    check(fresh["snapshot_price"] == 101, "fresh publication price must anchor live review")
    check(fresh["snapshot_at_ms"] == 100_000, "review must start at publication time")
    stale = server.build_signal_review_records(sample_market_payload(70_000))[0]
    check(stale["snapshot_price"] == 0, "stale analysis price must not impersonate publication price")


def test_realtime_hub_recovery_and_idle_cleanup():
    class DeadThread:
        def is_alive(self):
            return False

    hub = server.RealtimePriceHub("dead")
    hub.symbols = ["DOGEUSDT"]
    hub.direct_thread = DeadThread()
    calls = []
    hub.ensure = lambda symbols: calls.append(list(symbols)) or True
    previous_disabled = server.LOG.disabled
    server.LOG.disabled = True
    try:
        restarted = hub.maintain(["DOGEUSDT"])
    finally:
        server.LOG.disabled = previous_disabled
    check(restarted, "dead realtime worker should restart")
    check(calls == [["DOGEUSDT"]] and hub.restart_count == 1, "realtime restart must be observable")

    with server._realtime_hubs_lock:
        server._realtime_hubs.clear()


def test_realtime_events_never_roll_back():
    hub = server.RealtimePriceHub("ordering")
    original_persist = server.persist_realtime_later
    server.persist_realtime_later = lambda *args, **kwargs: None
    try:
        hub.handle_message({"data": {"e": "bookTicker", "s": "DOGEUSDT", "T": 200, "b": "100", "a": "102"}})
        hub.handle_message({"data": {"e": "bookTicker", "s": "DOGEUSDT", "T": 100, "b": "50", "a": "52"}})
        check(hub.latest["DOGEUSDT"]["price"] == 101, "older bookTicker must not roll back price")
        check(hub.latest["DOGEUSDT"]["price_event_ms"] == 200, "older bookTicker must not roll back source time")

        hub.handle_message({"data": {"e": "depthUpdate", "s": "DOGEUSDT", "T": 300, "b": [["103", "2"]], "a": [["105", "2"]]}})
        hub.handle_message({"data": {"e": "depthUpdate", "s": "DOGEUSDT", "T": 250, "b": [["60", "2"]], "a": [["62", "2"]]}})
        check(hub.latest["DOGEUSDT"]["price"] == 104, "older depth snapshot must not roll back price")
        check(hub.latest["DOGEUSDT"]["depth_event_ms"] == 300, "older depth snapshot must not roll back depth time")

        hub.handle_message({"data": {"e": "bookTicker", "s": "DOGEUSDT", "T": 400, "b": "110", "a": "112"}})
        hub.handle_message({"data": {"e": "depthUpdate", "s": "DOGEUSDT", "T": 350, "b": [["106", "3"]], "a": [["108", "3"]]}})
        check(hub.latest["DOGEUSDT"]["price"] == 111, "new depth data must not overwrite a newer price stream")
        check(hub.latest["DOGEUSDT"]["bid"] == 110 and hub.latest["DOGEUSDT"]["ask"] == 112, "new depth data must not roll back a newer top of book")
        check(hub.latest["DOGEUSDT"]["depth_event_ms"] == 350, "newer depth data should still advance independently")
    finally:
        server.persist_realtime_later = original_persist
    acquired, _, _ = server.realtime_hub_for_request(["DOGEUSDT"])
    check(acquired.client_count == 1, "hub selection must acquire before releasing the registry lock")
    acquired.stop_if_idle()
    check(server._realtime_hubs.get(acquired.key) is acquired, "an acquired hub must survive idle cleanup")
    acquired.release()
    with acquired.lock:
        if acquired.idle_timer:
            acquired.idle_timer.cancel()
            acquired.idle_timer = None
    acquired.stop_if_idle()
    check(acquired.key not in server._realtime_hubs, "an actually idle hub must be removed")


def test_realtime_stopped_worker_cannot_publish_state():
    entered = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    call_lock = threading.Lock()
    call_count = 0

    class FakeConnection:
        def __init__(self, index):
            self.index = index

        async def __aenter__(self):
            entered[self.index].set()
            while not releases[self.index].is_set():
                await asyncio.sleep(0.01)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def recv(self):
            await asyncio.sleep(10)

    def fake_connect(*_args, **_kwargs):
        nonlocal call_count
        with call_lock:
            index = call_count
            call_count += 1
        return FakeConnection(index)

    original_connect = server.websocket_connect
    hub = server.RealtimePriceHub("worker-generation")
    old_thread = None
    new_thread = None
    try:
        server.websocket_connect = fake_connect
        with hub.lock:
            hub.symbols = ["OLDUSDT"]
            hub.start_direct_locked(hub.symbols)
            old_thread = hub.direct_thread
        check(entered[0].wait(2), "old realtime worker must begin connecting")

        with hub.lock:
            hub.stop_locked()
            hub.symbols = ["NEWUSDT"]
            hub.start_direct_locked(hub.symbols)
            new_thread = hub.direct_thread
        check(entered[1].wait(2), "replacement realtime worker must begin connecting")

        releases[0].set()
        if old_thread:
            old_thread.join(2)
        snapshot = hub.snapshot(["NEWUSDT"])
        check(not snapshot["connected"], "stopped worker must not mark the replacement connection as connected")
        check(hub.connect_count == 0, "stopped worker must not increment the current hub connect count")
    finally:
        with hub.lock:
            hub.stop_locked()
        releases[0].set()
        releases[1].set()
        if old_thread:
            old_thread.join(2)
        if new_thread:
            new_thread.join(2)
        server.websocket_connect = original_connect


def test_due_review_deferral():
    now_ms = int(time.time() * 1000)

    class FakeStorage:
        def __init__(self):
            self.deferred = []

        def load_due_signal_reviews(self, _now_ms, max_rows=40, all_users=False):
            return [{
                "signal_key": "wait-for-15m",
                "symbol": "DOGEUSDT",
                "side": "long",
                "entry_price": 100,
                "stop_price": 95,
                "snapshot_price": 100,
                "snapshot_at_ms": now_ms - 10 * 60_000,
                "evaluation": {"5m": {"status": "done", "failure_reason": "ok"}},
                "storage_user_id": "u1",
            }]

        def defer_signal_review(self, signal_key, user_id=None):
            self.deferred.append((signal_key, user_id))
            return True

        def update_signal_review_evaluation(self, *args, **kwargs):
            raise AssertionError("waiting record must not be rewritten as evaluated")

    fake = FakeStorage()
    original_storage = server.storage
    try:
        server.storage = fake
        result = server.evaluate_due_signal_reviews(max_records=1, blocking=True)
    finally:
        server.storage = original_storage
    check(result["deferred"] == 1, "waiting partial record must be moved behind other due work")
    check(fake.deferred == [("wait-for-15m", "u1")], "deferral must preserve owning user")


def test_partial_review_stats_follow_record_status_and_next_deadline():
    now_ms = int(time.time() * 1000)
    partial = {
        "signal_key": "partial-5m",
        "side": "long",
        "status": "partial",
        "snapshot_at_ms": now_ms - 10 * 60_000,
        "evaluation": {"5m": {"status": "done", "failure_reason": "ok"}},
    }
    stats = server.build_signal_review_stats([partial])
    check(stats["pending"] == 1, "partial record must remain pending after its first completed horizon")
    check(stats["evaluated_records"] == 0, "partial record must not be counted as fully evaluated")
    check(stats["due_pending"] == 0, "partial 5m record must wait until the missing 15m horizon is due")
    partial["snapshot_at_ms"] = now_ms - 16 * 60_000
    stats = server.build_signal_review_stats([partial])
    check(stats["due_pending"] == 1, "partial record must become due when its next missing horizon expires")


def test_redis_read_degrades():
    class BrokenRedis:
        def get(self, key):
            raise TimeoutError(key)

    store = storage_module.DashboardStorage()
    store.redis_configured = True
    store._redis_client = BrokenRedis()
    previous_disabled = storage_module.LOG.disabled
    storage_module.LOG.disabled = True
    try:
        cached = store.get_market_payload("DOGEUSDT")
    finally:
        storage_module.LOG.disabled = previous_disabled
    check(cached is None, "Redis read failure must degrade to a cache miss")
    check(store._redis_client is None and store._redis_block_until > time.time(), "Redis failures must start a cooldown")


class FakePreferenceCursor:
    def __init__(self, revision):
        self.revision = revision
        self.rowcount = 1
        self.executemany_rows = []
        self.last_sql = ""

    def execute(self, sql, params=()):
        self.last_sql = " ".join(sql.split())

    def executemany(self, sql, rows):
        self.last_sql = " ".join(sql.split())
        self.executemany_rows.extend(rows)

    def fetchone(self):
        return (str(self.revision),)


class FakePreferenceConnection:
    def __init__(self, revision):
        self.cursor_obj = FakePreferenceCursor(revision)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_preference_revision_guard():
    store = storage_module.DashboardStorage()
    store.mysql_configured = True
    store.mysql_available = lambda: True
    store._ensure_mysql_schema = lambda conn: None

    stale_conn = FakePreferenceConnection(5)
    store._mysql_connect = lambda: stale_conn
    stale = store.save_preferences({"custom_symbols": ["DOGEUSDT"]}, revision=4)
    check(stale == {"saved": True, "applied": False, "revision": 5}, "late preference write must be rejected")
    check(not stale_conn.cursor_obj.executemany_rows, "rejected preference write must not touch values")

    fresh_conn = FakePreferenceConnection(5)
    store._mysql_connect = lambda: fresh_conn
    fresh = store.save_preferences({"custom_symbols": ["TLMUSDT"]}, revision=6)
    check(fresh == {"saved": True, "applied": True, "revision": 6}, "newer preference revision must apply")
    check(fresh_conn.cursor_obj.executemany_rows, "accepted preference write must upsert values")

    batch_conn = FakePreferenceConnection(5)
    store._mysql_connect = lambda: batch_conn
    batch = store.save_preference_batch([
        {"preferences": {"custom_symbols": ["OLDUSDT"]}, "revision": 4},
        {"preferences": {"account_risk": {"daily_loss_pct": 2}}, "revision": 6},
    ])
    check(batch == {"saved": True, "applied": True, "revision": 6, "applied_count": 1}, "batch must skip stale entries and apply later revisions")
    batch_keys = [row[1] for row in batch_conn.cursor_obj.executemany_rows]
    check(batch_keys == ["account_risk"], "stale batch fields must not overwrite storage")
    check(batch_conn.commits == 1, "ordered preference batch must use one commit")

    fresh_batch_conn = FakePreferenceConnection(0)
    store._mysql_connect = lambda: fresh_batch_conn
    fresh_batch = store.save_preference_batch([
        {"preferences": {"custom_symbols": ["TLMUSDT"]}, "revision": 10},
        {"preferences": {"account_risk": {"daily_loss_pct": 3}}, "revision": 11},
    ])
    check(fresh_batch == {"saved": True, "applied": True, "revision": 11, "applied_count": 2}, "fresh batch must apply every ordered patch")
    check([row[1] for row in fresh_batch_conn.cursor_obj.executemany_rows] == ["custom_symbols", "account_risk"], "fresh batch must preserve patch order")

    class FailingBatchCursor(FakePreferenceCursor):
        def __init__(self, revision):
            super().__init__(revision)
            self.write_count = 0

        def executemany(self, sql, rows):
            self.write_count += 1
            if self.write_count == 2:
                raise TimeoutError("second batch write failed")
            super().executemany(sql, rows)

    class FailingBatchConnection(FakePreferenceConnection):
        def __init__(self, revision):
            super().__init__(revision)
            self.cursor_obj = FailingBatchCursor(revision)

    failed_batch_conn = FailingBatchConnection(5)
    store._mysql_connect = lambda: failed_batch_conn
    try:
        store.save_preference_batch([
            {"preferences": {"custom_symbols": ["TLMUSDT"]}, "revision": 6},
            {"preferences": {"account_risk": {"daily_loss_pct": 3}}, "revision": 7},
        ])
    except TimeoutError:
        pass
    else:
        raise AssertionError("preference batch failure must abort the transaction")
    check(failed_batch_conn.commits == 0 and failed_batch_conn.rollbacks == 1, "partial preference batch failure must roll back every patch")

    try:
        store.save_preference_batch([
            {"preferences": {"custom_symbols": ["TLMUSDT"]}, "revision": 7},
            {"preferences": {"account_risk": {"daily_loss_pct": 3}}, "revision": 7},
        ])
    except ValueError:
        pass
    else:
        raise AssertionError("preference batch must reject non-increasing revisions")

    for invalid_revision in (None, 0, -1, True, 1.5):
        try:
            store.save_preferences({"custom_symbols": ["OLDUSDT"]}, revision=invalid_revision)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid preference revision must be rejected: {invalid_revision!r}")

    handler = object.__new__(server.Handler)
    response = {}
    handler.read_json_body = lambda: {"preferences": {"custom_symbols": ["OLDUSDT"]}}
    handler.send_json = lambda status, payload: response.update(status=status, payload=payload)
    handler.save_preferences_api()
    check(response.get("status") == 400, "preference API must reject a missing revision")
    check("revision" in response.get("payload", {}).get("error", ""), "missing revision error must be explicit")

    handler.read_json_body = lambda: {"preferences": {"custom_symbols": ["OLDUSDT"]}, "revision": True}
    handler.save_preferences_api()
    check(response.get("status") == 400, "preference API must reject boolean revisions")

    class BatchPreferenceStorage:
        def __init__(self):
            self.patches = None

        def save_preference_batch(self, patches):
            self.patches = patches
            return {"saved": True, "applied": True, "revision": patches[-1]["revision"]}

        def status(self):
            return {"mysql": {"configured": True, "available": True}, "redis": {"configured": False, "available": False}}

    batch_storage = BatchPreferenceStorage()
    handler.request_storage = lambda: batch_storage
    handler.read_json_body = lambda: {
        "patches": [
            {"preferences": {"account_risk": {"daily_loss_pct": 2}}, "revision": 10},
            {"preferences": {"tv_kline_interval": "60"}, "revision": 11},
        ]
    }
    handler.save_preferences_api()
    check(response.get("status") == 200 and response.get("payload", {}).get("revision") == 11, "preference API must accept an ordered unload batch")
    check(batch_storage.patches and len(batch_storage.patches) == 2, "preference API must preserve the ordered batch")

    handler.read_json_body = lambda: {
        "patches": [
            {"preferences": {"account_risk": {"daily_loss_pct": 2}}, "revision": 11},
            {"preferences": {"tv_kline_interval": "60"}, "revision": 10},
        ]
    }
    handler.save_preferences_api()
    check(response.get("status") == 400, "preference API must reject decreasing batch revisions")

    class UnavailablePreferenceStorage:
        mysql_configured = True

        def mysql_available(self):
            return False

        def status(self):
            return {"mysql": {"configured": True, "available": False}, "redis": {"configured": False, "available": False}}

    handler.request_storage = lambda: UnavailablePreferenceStorage()
    handler.serve_preferences()
    check(response.get("status") == 503, "preference GET must fail when configured MySQL is unavailable")


def test_same_origin_rejects_invalid_source_headers():
    host = "dashboard.example.com"
    check(server.same_origin_allowed("", host), "missing browser source header must preserve CLI compatibility")
    check(not server.same_origin_allowed("null", host), "Origin:null must not bypass same-origin checks")
    check(not server.same_origin_allowed("not-a-url", host), "malformed Origin must not bypass same-origin checks")
    check(server.same_origin_allowed("https://dashboard.example.com", host), "matching Origin must remain allowed")


def test_password_change_revokes_sessions_atomically():
    current_hash = storage_module.hash_password("old-password")

    class AuthCursor:
        def __init__(self, fail_delete=False):
            self.fail_delete = fail_delete
            self.statements = []

        def execute(self, sql, params=()):
            normalized = " ".join(sql.split())
            self.statements.append((normalized, params))
            if self.fail_delete and normalized.startswith("DELETE FROM bian_auth_sessions"):
                raise TimeoutError("session revoke failed")

        def fetchone(self):
            return current_hash, 0

    class AuthConnection:
        def __init__(self, fail_delete=False):
            self.cursor_obj = AuthCursor(fail_delete=fail_delete)
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    store = storage_module.DashboardStorage()
    store.mysql_configured = True
    store.mysql_available = lambda: True
    store._ensure_auth_schema = lambda conn: None
    success_conn = AuthConnection()
    store._mysql_connect = lambda: success_conn
    changed = store.change_auth_password(7, "old-password", "new-password", keep_token="current-token")
    check(changed == (True, ""), "password change and session revoke must succeed together")
    sql = [statement for statement, _params in success_conn.cursor_obj.statements]
    check(any(statement.startswith("SELECT password_hash") and "FOR UPDATE" in statement for statement in sql), "password verification must lock the user row")
    check(any(statement.startswith("UPDATE bian_auth_users") for statement in sql), "password hash must be updated")
    check(any(statement.startswith("DELETE FROM bian_auth_sessions") for statement in sql), "other sessions must be revoked")
    check(success_conn.commits == 1, "password and session changes must share one commit")

    failed_conn = AuthConnection(fail_delete=True)
    store._mysql_connect = lambda: failed_conn
    try:
        store.change_auth_password(7, "old-password", "new-password", keep_token="current-token")
    except TimeoutError:
        pass
    else:
        raise AssertionError("session revoke failure must fail the password transaction")
    check(failed_conn.commits == 0 and failed_conn.rollbacks == 1, "failed session revoke must roll back the password update")

    class AtomicAuthStorage:
        def __init__(self):
            self.keep_token = None

        def change_auth_password(self, user_id, current_password, new_password, keep_token=""):
            self.keep_token = keep_token
            return True, ""

        def delete_other_auth_sessions(self, *_args, **_kwargs):
            raise AssertionError("handler must not use a second session transaction")

    fake_storage = AtomicAuthStorage()
    response = {}
    handler = object.__new__(server.Handler)
    handler.auth_token = lambda: "current-token"
    handler.current_user = lambda: {"id": 7, "username": "alice", "role": "user"}
    handler.read_json_body = lambda: {
        "current_password": "old-password",
        "new_password": "new-password",
        "confirm_password": "new-password",
    }
    handler.client_ip = lambda: "127.0.0.1"
    handler.send_json = lambda status, payload: response.update(status=status, payload=payload)
    original_enabled = server.AUTH_ENABLED
    original_storage = server.storage
    original_ready = server.ensure_auth_ready
    try:
        server.AUTH_ENABLED = True
        server.storage = fake_storage
        server.ensure_auth_ready = lambda: True
        handler.change_password_api()
    finally:
        server.AUTH_ENABLED = original_enabled
        server.storage = original_storage
        server.ensure_auth_ready = original_ready
    check(response.get("status") == 200 and response.get("payload", {}).get("changed"), "atomic password change must report success")
    check(fake_storage.keep_token == "current-token", "current session token must be preserved inside the atomic transaction")


def test_storage_timeouts_are_wired():
    captured = {}

    class FakeConnection:
        def close(self):
            pass

    fake_pymysql = types.SimpleNamespace(connect=lambda **kwargs: captured.update(kwargs) or FakeConnection())
    previous = sys.modules.get("pymysql")
    sys.modules["pymysql"] = fake_pymysql
    try:
        store = storage_module.DashboardStorage()
        store._mysql_connect().close()
    finally:
        if previous is None:
            sys.modules.pop("pymysql", None)
        else:
            sys.modules["pymysql"] = previous
    check(captured.get("connect_timeout") == storage_module.MYSQL_CONNECT_TIMEOUT_SECONDS, "MySQL connect timeout missing")
    check(captured.get("read_timeout") == storage_module.MYSQL_READ_TIMEOUT_SECONDS, "MySQL read timeout missing")
    check(captured.get("write_timeout") == storage_module.MYSQL_WRITE_TIMEOUT_SECONDS, "MySQL write timeout missing")


def test_http_and_sse_resource_bounds():
    class FakeSocket:
        def __init__(self):
            self.timeout = None

        def settimeout(self, value):
            self.timeout = value

        def makefile(self, *args, **kwargs):
            return io.BytesIO()

    handler = object.__new__(server.Handler)
    handler.request = FakeSocket()
    server.Handler.setup(handler)
    check(handler.request.timeout == server.HTTP_REQUEST_TIMEOUT_SECONDS, "handler sockets must enforce the configured request timeout")

    timeout_handler = object.__new__(server.Handler)
    timeout_handler.headers = {"Content-Length": "10"}
    timeout_handler.rfile = types.SimpleNamespace(read=lambda _length: (_ for _ in ()).throw(TimeoutError("slow client")))
    try:
        timeout_handler.read_json_body()
    except server.BadRequestError as exc:
        check("timeout" in str(exc), "slow request body must be classified as a timeout")
    else:
        raise AssertionError("slow request body must not block indefinitely")

    bounded = server.ThreadingServer(("127.0.0.1", 0), server.Handler, bind_and_activate=False)
    try:
        acquired = [bounded._request_slots.acquire(blocking=False) for _ in range(server.HTTP_MAX_CONCURRENT_REQUESTS)]
        check(all(acquired), "configured HTTP request slots must be available")
        check(not bounded._request_slots.acquire(blocking=False), "HTTP request slots must reject unbounded growth")
        for _ in acquired:
            bounded._request_slots.release()
    finally:
        bounded.server_close()

    class FullSseSlots:
        def acquire(self, blocking=False):
            return False

        def release(self):
            raise AssertionError("unacquired SSE slot must not be released")

    original_slots = server._sse_client_slots
    response = {}
    try:
        server._sse_client_slots = FullSseSlots()
        sse_handler = object.__new__(server.Handler)
        sse_handler.send_json = lambda status, payload: response.update(status=status, payload=payload)
        sse_handler.serve_realtime_prices("symbols=DOGEUSDT")
    finally:
        server._sse_client_slots = original_slots
    check(response.get("status") == 503, "SSE clients above the configured limit must receive HTTP 503")


def test_mysql_health_cache_expires():
    class FakeConnection:
        def close(self):
            pass

    store = storage_module.DashboardStorage()
    store.mysql_configured = True
    store._mysql_connect = lambda: FakeConnection()
    check(store.mysql_available(), "initial MySQL health check should succeed")
    store._mysql_available_until = 0
    store._mysql_connect = lambda: (_ for _ in ()).throw(TimeoutError("mysql unavailable"))
    previous_disabled = storage_module.LOG.disabled
    storage_module.LOG.disabled = True
    try:
        available = store.mysql_available()
    finally:
        storage_module.LOG.disabled = previous_disabled
    check(not available, "expired MySQL health cache must perform a real connection check")
    check(store._mysql_block_until > time.time(), "failed MySQL health check must start a short cooldown")


def test_auth_revocation_readiness_and_health_cache():
    original_file = storage_module.AUTH_SESSION_REVOCATION_FILE
    original_auth_env = os.environ.get("BIAN_AUTH_ENABLED")
    original_server_storage = server.storage
    original_server_auth = server.AUTH_ENABLED
    test_file = ROOT / "runtime" / f"smoke-auth-readiness-{os.getpid()}-{time.time_ns()}.json"
    storage_module.AUTH_SESSION_REVOCATION_FILE = str(test_file)
    os.environ["BIAN_AUTH_ENABLED"] = "1"
    try:
        test_file.write_text("not-json", encoding="utf-8")
        broken = storage_module.DashboardStorage()
        broken.mysql_configured = True
        broken.mysql_available = lambda: True
        broken._mysql_connect = lambda: (_ for _ in ()).throw(AssertionError("broken revocation readiness must fail before MySQL"))
        status = broken.auth_status()
        check(not status["login_ready"], "corrupt revocation state must make authentication unready")
        check(status["issue"] == "auth_revocation_store_unavailable", "health must classify revocation storage failure")
        check(broken.create_auth_session(1) is None, "login must not issue a session while revocation storage is unusable")

        server.storage = broken
        server.AUTH_ENABLED = True
        health = server.health_payload(check_storage=True)
        check(not health["ok"] and health["auth"]["revocation_store_ready"] is False, "public health must fail when session revocation cannot be enforced")

        test_file.write_text('{"version":1,"revocations":[]}', encoding="utf-8")
        healthy = storage_module.DashboardStorage()
        healthy.mysql_configured = True
        healthy._mysql_available_until = time.time() + 60
        connection_count = {"value": 0}

        class CountConnection:
            def close(self):
                pass

        def connect_once_per_ttl():
            connection_count["value"] += 1
            return CountConnection()

        healthy._mysql_connect = connect_once_per_ttl
        healthy._auth_user_count = lambda _conn: (1, "")
        server.storage = healthy
        for _ in range(5):
            check(server.health_payload(check_storage=True)["ok"], "healthy cached auth readiness must stay ready")
        check(connection_count["value"] == 1, "public health must cache the full auth database readiness check within its TTL")
    finally:
        storage_module.AUTH_SESSION_REVOCATION_FILE = original_file
        if original_auth_env is None:
            os.environ.pop("BIAN_AUTH_ENABLED", None)
        else:
            os.environ["BIAN_AUTH_ENABLED"] = original_auth_env
        server.storage = original_server_storage
        server.AUTH_ENABLED = original_server_auth
        try:
            test_file.unlink()
        except FileNotFoundError:
            pass


def test_signal_review_file_reconciliation():
    original_file = storage_module.SIGNAL_REVIEW_FILE
    test_file = ROOT / "runtime" / f"smoke-signal-reviews-{os.getpid()}-{time.time_ns()}.json"
    storage_module.SIGNAL_REVIEW_FILE = str(test_file)
    try:
        store = storage_module.DashboardStorage()
        store.user_id = "u1"
        record = {
            "signal_key": "fallback-key",
            "symbol": "DOGEUSDT",
            "side": "long",
            "status": "partial",
            "snapshot_at_ms": 100,
            "evaluation": {"5m": {"status": "done", "failure_reason": "ok"}},
        }
        store._save_signal_reviews_file([record])
        captured = []
        store._upsert_signal_review_records_mysql = lambda records: captured.extend(records) or len(records)
        migrated = store._reconcile_signal_review_file_to_mysql()
        check(migrated == 1 and captured[0]["signal_key"] == "fallback-key", "fallback record must be offered to MySQL reconciliation")
        check(store._read_signal_review_file()["records"] == [], "committed fallback record must be removed from the file")

        database_record = {
            "signal_key": "merge-key",
            "status": "evaluated",
            "updated_at_ms": 100,
            "evaluation": {"5m": {"status": "done", "failure_reason": "ok"}},
        }
        file_record = {
            "signal_key": "merge-key",
            "status": "partial",
            "updated_at_ms": 200,
            "evaluation": {"15m": {"status": "done", "failure_reason": "ok"}},
        }
        merged = store._merge_signal_review_records(database_record, file_record)
        check(merged["status"] == "evaluated", "fallback reconciliation must not downgrade a terminal database record")
        check(set(merged["evaluation"]) == {"5m", "15m"}, "fallback reconciliation must preserve completed horizons from both backends")
    finally:
        storage_module.SIGNAL_REVIEW_FILE = original_file
        try:
            test_file.unlink()
        except FileNotFoundError:
            pass


def test_signal_review_file_retention_is_per_user():
    original_file = storage_module.SIGNAL_REVIEW_FILE
    original_limit = storage_module.SIGNAL_REVIEW_LIMIT
    test_file = ROOT / "runtime" / f"smoke-signal-retention-{os.getpid()}-{time.time_ns()}.json"
    storage_module.SIGNAL_REVIEW_FILE = str(test_file)
    storage_module.SIGNAL_REVIEW_LIMIT = 2
    try:
        store = storage_module.DashboardStorage()

        def records(prefix, start):
            return [
                {
                    "signal_key": f"{prefix}-{index}",
                    "symbol": "DOGEUSDT",
                    "side": "long",
                    "status": "pending",
                    "snapshot_at_ms": start + index,
                }
                for index in range(3)
            ]

        store.user_id = "u1"
        store._save_signal_reviews_file(records("u1", 100))
        store.user_id = "u2"
        store._save_signal_reviews_file(records("u2", 200))
        saved = store._read_signal_review_file()["records"]
        by_user = {}
        for item in saved:
            by_user.setdefault(item["storage_user_id"], []).append(item["signal_key"])
        check(set(by_user) == {"u1", "u2"}, "one user's fallback writes must not evict another user")
        check(by_user["u1"] == ["u1-2", "u1-1"], "u1 must retain its two newest fallback records")
        check(by_user["u2"] == ["u2-2", "u2-1"], "u2 must retain its two newest fallback records")
        due = store.load_due_signal_reviews(int(time.time() * 1000), max_rows=2, all_users=True)
        check([item["storage_user_id"] for item in due] == ["u1", "u1"], "all-user due scans must sort before applying the global work limit")

        migrated = []
        store.user_id = "u1"
        store._upsert_signal_review_records_mysql = lambda items: migrated.extend(items) or len(items)
        check(store._reconcile_signal_review_file_to_mysql() == 2, "scoped reconciliation must migrate the current user's retained records")
        remaining = store._read_signal_review_file()["records"]
        check({item["storage_user_id"] for item in remaining} == {"u2"}, "scoped reconciliation must preserve other users' fallback records")
        check(store._reconcile_signal_review_file_to_mysql(all_users=True) == 2, "all-user reconciliation must migrate remaining user scopes")
        check(store._read_signal_review_file()["records"] == [], "all-user reconciliation must remove only committed records")
    finally:
        storage_module.SIGNAL_REVIEW_FILE = original_file
        storage_module.SIGNAL_REVIEW_LIMIT = original_limit
        try:
            test_file.unlink()
        except FileNotFoundError:
            pass


def test_required_assets_and_report_contract():
    for relative in ("scripts/deploy.py", "scripts/frontend-smoke.js", ".env.example"):
        check((ROOT / relative).is_file(), f"required project asset missing: {relative}")
    fields = analyzer.SymbolReport.__dataclass_fields__
    check("price_observed_at_ms" in fields, "analysis price observation time must be serialized")
    original_release_id = server.RELEASE_ID
    try:
        server.RELEASE_ID = "a" * 64
        check(server.health_payload(check_storage=False, expected_release_id="a" * 64)["ok"], "matching release health must remain ready")
        mismatch = server.health_payload(check_storage=False, expected_release_id="b" * 64)
        check(not mismatch["ok"] and mismatch["release_match"] is False, "health must fail on a different release id")
    finally:
        server.RELEASE_ID = original_release_id


def test_deploy_contracts():
    selected = sorted(deploy.REQUIRED_DEPLOY_FILES | {".env", "runtime/secret.json", ".git/config"})
    original_git_output = deploy.git_output
    deploy.git_output = lambda *args, **kwargs: ("\0".join(selected) + "\0").encode("utf-8")
    try:
        files = set(deploy.deployment_files(include_untracked=True))
    finally:
        deploy.git_output = original_git_output
    check(deploy.REQUIRED_DEPLOY_FILES.issubset(files), "deploy archive must contain required application files")
    check(".env" not in files, "deploy archive must never contain the local .env")
    check(not any(name.startswith((".git/", "runtime/")) for name in files), "deploy archive must exclude Git and runtime files")

    class IgnoreWarningResult:
        stdout = b""
        stderr = b"warning: unable to access global git ignore: Permission denied"

    original_run = deploy.subprocess.run
    deploy.subprocess.run = lambda *args, **kwargs: IgnoreWarningResult()
    try:
        try:
            deploy.git_output("ls-files", "--others", require_ignore_integrity=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unreadable global Git ignore must fail closed")
    finally:
        deploy.subprocess.run = original_run

    args = types.SimpleNamespace(
        remote_dir="/opt/bian-dashboard",
        check_market=False,
        no_ufw=True,
        public_port=9000,
        public_url="https://dashboard.example.com",
    )
    release_id = "a" * 64
    remote = deploy.remote_script(args, "/tmp/bian-dashboard-test.tar.gz", release_id)
    finalize = deploy.finalize_remote_script(args, "/tmp/bian-dashboard-test.tar.gz", release_id)
    health_check = f"http://127.0.0.1:9000/api/health?release_id={release_id}"
    archive_cleanup = "rm -f /tmp/bian-dashboard-test.tar.gz"
    check("BIAN_PUBLIC_PORT=9000" in remote, "public port must be persisted into the remote .env")
    check("BIAN_BIND_ADDRESS=127.0.0.1" in remote, "remote deployment must bind the application upstream to loopback")
    check("BIAN_AUTH_COOKIE_SECURE=1" in remote, "remote deployment must require secure authentication cookies")
    check('chmod 600 "$release_dir/.env"' in remote, "remote deployment must restrict secret file permissions")
    check(remote.index('chmod 600 "$release_dir/.env"') > remote.rindex("BIAN_AUTH_COOKIE_SECURE"), "secret permissions must be hardened after every .env edit")
    check("chmod 600 /opt/bian-dashboard/.env" in remote, "existing remote secrets must be hardened before they are copied")
    check("chmod 600 /opt/bian-dashboard.previous/.env" in remote, "retained previous secrets must be hardened")
    check("BIAN_REDIS_PASSWORD=$redis_password" in remote, "first deploy must generate a Redis password")
    check(f"BIAN_RELEASE_ID={release_id}" in remote, "release id must be persisted into the remote environment")
    check("mktemp -d" in remote and 'mv "$release_dir" /opt/bian-dashboard' in remote, "deploy must replace from a staging directory")
    release_backup = f"/opt/bian-dashboard.previous.{release_id}"
    check(release_backup in remote, "deploy must retain a release-specific previous directory until health passes")
    check(health_check in remote, "loopback health must verify the new release id")
    check("flock -w 30 9" in remote and "flock -w 30 9" in finalize, "deploy switching and cleanup must share a remote lock")
    check(remote.index("mkdir -p /opt") < remote.index("exec 9>/opt/bian-dashboard.deploy.lock"), "remote parent must exist before the deployment lock is opened")
    check(archive_cleanup not in remote and f"rm -rf {release_backup}" not in remote, "deploy phase must retain rollback assets for external verification")
    check(archive_cleanup in finalize and f"rm -rf {release_backup}" in finalize, "finalize phase must clean only its release-specific rollback asset")
    other_release_id = "b" * 64
    other_finalize = deploy.finalize_remote_script(args, "/tmp/bian-dashboard-other.tar.gz", other_release_id)
    check(release_backup not in other_finalize, "one release finalizer must not delete another release's rollback directory")
    check(deploy.remote_archive_path(release_id) != deploy.remote_archive_path(other_release_id), "concurrent releases must not share an uploaded archive path")
    check('echo "first admin password: $bootstrap_password"' not in remote, "deployment output must not expose the bootstrap admin password")
    check("first admin password stored in the release .env" in remote, "deployment output should identify the secure password location")
    public_health_check = f"https://dashboard.example.com/api/health?release_id={release_id}"
    check(public_health_check in remote, "deployment must verify the configured public HTTPS health endpoint")
    check(remote.index(health_check) < remote.index(public_health_check), "public HTTPS must run only after loopback verifies the same release")

    first_release_id = deploy.archive_release_id(ROOT / "Dockerfile")
    second_release_id = deploy.archive_release_id(ROOT / "Dockerfile")
    check(len(first_release_id) == 64 and first_release_id != second_release_id, "each deploy attempt must receive a unique release id")

    class PublicHealthResponse:
        def __init__(self, url, payload):
            self.url = url
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return self.url

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    health_args = types.SimpleNamespace(public_url=args.public_url, dry_run=False, retries=1, retry_delay=0)
    original_urlopen = deploy.urlopen
    try:
        expected_url = deploy.release_health_url(args.public_url, release_id)
        deploy.urlopen = lambda _request, timeout=0: PublicHealthResponse(expected_url, {
            "ok": True,
            "release_match": True,
            "release_id": release_id,
        })
        deploy.verify_public_health(health_args, release_id)
        deploy.urlopen = lambda _request, timeout=0: PublicHealthResponse(expected_url, {
            "ok": True,
            "release_match": False,
            "release_id": "b" * 64,
        })
        try:
            deploy.verify_public_health(health_args, release_id)
        except SystemExit:
            pass
        else:
            raise AssertionError("external verification must reject an old or different release")
        deploy.urlopen = lambda _request, timeout=0: PublicHealthResponse("https://other.example.com/api/health", {
            "ok": True,
            "release_match": True,
            "release_id": release_id,
        })
        try:
            deploy.verify_public_health(health_args, release_id)
        except SystemExit:
            pass
        else:
            raise AssertionError("external verification must reject a redirect to a different origin")
    finally:
        deploy.urlopen = original_urlopen

    for invalid_url in (
        "http://dashboard.example.com",
        "https://user:secret@dashboard.example.com",
        "https://dashboard.example.com/app",
        "https://127.0.0.1",
        "https://192.168.1.10",
        "https://100.64.0.1",
        "https://localhost",
    ):
        try:
            deploy.normalize_public_url(invalid_url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid public deployment URL must be rejected: {invalid_url}")
    try:
        deploy.validate_deploy_args(types.SimpleNamespace(public_url="", allow_no_public_url=False))
    except SystemExit:
        pass
    else:
        raise AssertionError("production deployment must require a public HTTPS URL")
    local_args = types.SimpleNamespace(public_url="", allow_no_public_url=True)
    deploy.validate_deploy_args(local_args)
    check(local_args.public_url == "", "explicit local deployment override must remain available")

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    check("${BIAN_BIND_ADDRESS:-127.0.0.1}:${BIAN_PUBLIC_PORT:-8000}:8000" in compose, "Compose must not expose plaintext authentication publicly by default")
    check("BIAN_AUTH_COOKIE_SECURE: ${BIAN_AUTH_COOKIE_SECURE:-1}" in compose, "Compose must default authentication cookies to Secure")
    check(compose.count("BIAN_REDIS_PASSWORD") >= 5, "Compose must wire the Redis password through app, server, and healthcheck")
    check("--requirepass" in compose and "REDISCLI_AUTH" in compose, "Redis password must be enforced and health-checked")


def main():
    test_cache_and_symbol_contracts()
    test_tick_rounding_and_cache_lock_ownership()
    test_snapshot_fallback_and_logout_cookie()
    test_logout_revocation_tombstone()
    test_review_time_boundaries()
    test_publication_price_alignment()
    test_realtime_hub_recovery_and_idle_cleanup()
    test_realtime_events_never_roll_back()
    test_realtime_stopped_worker_cannot_publish_state()
    test_due_review_deferral()
    test_partial_review_stats_follow_record_status_and_next_deadline()
    test_redis_read_degrades()
    test_preference_revision_guard()
    test_same_origin_rejects_invalid_source_headers()
    test_password_change_revokes_sessions_atomically()
    test_storage_timeouts_are_wired()
    test_http_and_sse_resource_bounds()
    test_mysql_health_cache_expires()
    test_auth_revocation_readiness_and_health_cache()
    test_signal_review_file_reconciliation()
    test_signal_review_file_retention_is_per_user()
    test_required_assets_and_report_contract()
    test_deploy_contracts()
    print("smoke ok")


if __name__ == "__main__":
    main()
