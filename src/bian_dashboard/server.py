#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local dashboard server for Binance futures analysis.

- Serves the static dashboard files from the project web directory.
- GET /api/market runs the analyzer with --json and returns latest data.
- Uses a short in-memory cache to avoid repeated Binance calls.
- Falls back to the last successful cache file when Binance/network fails.
"""
import http.server
import asyncio
import copy
import hashlib
from http import cookies
import html
import json
import logging
import os
import subprocess
import sys
import threading
import time
import ipaddress
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse, quote

logging.basicConfig(
    level=getattr(logging, os.environ.get("BIAN_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOG = logging.getLogger("bian-dashboard")

try:
    from binance import ThreadedWebsocketManager
except Exception:
    ThreadedWebsocketManager = None

try:
    import websockets
except Exception:
    websockets = None

try:
    from websockets.legacy.client import connect as websocket_connect
except Exception:
    websocket_connect = websockets.connect if websockets is not None else None

try:
    from .storage import storage
except Exception:
    storage = None
    LOG.exception("optional storage module failed to initialize")

HOST = os.environ.get("BIAN_HOST", "127.0.0.1")
PORT = int(os.environ.get("BIAN_PORT", "8000"))
AUTH_ENABLED = os.environ.get("BIAN_AUTH_ENABLED", "1").lower() not in ("0", "false", "no", "off")
AUTH_COOKIE_NAME = os.environ.get("BIAN_AUTH_COOKIE_NAME", "bian_session")
AUTH_SESSION_TTL_SECONDS = int(os.environ.get("BIAN_AUTH_SESSION_TTL_SECONDS", str(7 * 24 * 3600)))
AUTH_COOKIE_SECURE = os.environ.get("BIAN_AUTH_COOKIE_SECURE", "0").lower() in ("1", "true", "yes", "on")
AUTH_MAX_FAILURES = int(os.environ.get("BIAN_AUTH_MAX_FAILURES", "8"))
AUTH_LOCKOUT_SECONDS = int(os.environ.get("BIAN_AUTH_LOCKOUT_SECONDS", "300"))
AUTH_TRUST_PROXY_HEADERS = os.environ.get("BIAN_AUTH_TRUST_PROXY_HEADERS", "0").lower() in ("1", "true", "yes", "on")
AUTH_SESSION_TOUCH_INTERVAL_SECONDS = int(os.environ.get("BIAN_AUTH_SESSION_TOUCH_INTERVAL_SECONDS", "60"))
AUTH_REQUIRE_SAME_ORIGIN_POST = os.environ.get("BIAN_AUTH_REQUIRE_SAME_ORIGIN_POST", "1").lower() not in ("0", "false", "no", "off")
INTERNAL_ERROR_MESSAGE = "internal server error; check server logs"
EXPOSE_ERROR_DETAILS = os.environ.get("BIAN_EXPOSE_ERROR_DETAILS", "0").lower() in ("1", "true", "yes", "on")
PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(PACKAGE_ROOT))
WEB_ROOT = os.path.join(ROOT, "web")
BIAN = os.path.join(PACKAGE_ROOT, "analyzer.py")
CACHE_FILE = os.path.join(ROOT, "runtime", "market_cache.json")
BACKTEST_CACHE_FILE = os.path.join(ROOT, "runtime", "backtest_cache.json")
BJ_TZ = timezone(timedelta(hours=8))
CACHE_TTL_SECONDS = 30
BACKTEST_CACHE_TTL_SECONDS = 10 * 60
RUN_TIMEOUT_SECONDS = 120
DEFAULT_SYMBOLS = ["DOGEUSDT", "TLMUSDT"]
MAX_SYMBOLS = 8
SIGNAL_REVIEW_DEFAULT_LIMIT = 240
SIGNAL_REVIEW_MAX_LIMIT = 1000
BINANCE_FAPI_BASE = os.environ.get("BIAN_BINANCE_FAPI_BASE", "https://fapi.binance.com").rstrip("/")
SIGNAL_REVIEW_HORIZONS = (("5m", 5 * 60 * 1000), ("15m", 15 * 60 * 1000), ("1h", 60 * 60 * 1000))
SIGNAL_REVIEW_CALIBRATION_HORIZON = "1h"
SIGNAL_REVIEW_MIN_PROFIT_PCT = {"5m": 0.25, "15m": 0.45, "1h": 0.75}
SIGNAL_REVIEW_TRIGGER_MIN_INTERVAL_SECONDS = float(os.environ.get("BIAN_SIGNAL_REVIEW_TRIGGER_MIN_INTERVAL_SECONDS", "20"))
SIGNAL_REVIEW_TAKER_FEE_BPS = float(os.environ.get("BIAN_SIGNAL_REVIEW_TAKER_FEE_BPS", "5"))
SIGNAL_REVIEW_SLIPPAGE_BPS = float(os.environ.get("BIAN_SIGNAL_REVIEW_SLIPPAGE_BPS", "2"))
SSE_MAX_SECONDS = max(60, int(os.environ.get("BIAN_SSE_MAX_SECONDS", str(6 * 60 * 60))))
SSE_HEARTBEAT_SECONDS = 15
REALTIME_IDLE_SECONDS = 30
REALTIME_STALE_SECONDS = max(10, int(os.environ.get("BIAN_REALTIME_STALE_SECONDS", "45")))
MEMORY_CACHE_MAX_ITEMS = 64

_last_payloads = {}
_payload_lock = threading.RLock()
_cache_lock = threading.RLock()
_market_locks = {}
_market_lock_refs = {}
_market_locks_guard = threading.RLock()
_run_semaphore = threading.Semaphore(2)
_realtime_hubs = {}
_realtime_hubs_lock = threading.RLock()
_realtime_sharing_counts = {"exact": 0, "new": 0, "superset": 0}
_realtime_sharing_lock = threading.RLock()
_realtime_storage_lock = threading.RLock()
_realtime_storage_event = threading.Event()
_realtime_storage_pending = {}
_realtime_storage_thread = None
_signal_review_eval_lock = threading.Lock()
_signal_review_trigger_lock = threading.RLock()
_signal_review_eval_state = {
    "trigger_count": 0,
    "thread_started_count": 0,
    "skipped_recent_count": 0,
    "skipped_running_count": 0,
    "last_trigger_at": 0.0,
    "last_started_at": 0.0,
    "last_finished_at": 0.0,
    "last_result": {},
    "last_error": "",
}
SERVER_STARTED_AT = time.time()


class RealtimePriceHub:
    def __init__(self, key):
        self.key = key
        self.lock = threading.RLock()
        self.twm = None
        self.socket_key = None
        self.symbols = []
        self.latest = {}
        self.error = None
        self.direct_stop = None
        self.direct_thread = None
        self.direct_generation = 0
        self.direct_connected = False
        self.connect_count = 0
        self.disconnect_count = 0
        self.last_connected_ms = 0
        self.last_disconnected_ms = 0
        self.last_message_ms = 0
        self.last_price_event_ms = 0
        self.last_depth_event_ms = 0
        self.restart_count = 0
        self.client_count = 0
        self.idle_timer = None

    def acquire(self):
        with self.lock:
            self.client_count += 1
            if self.idle_timer:
                self.idle_timer.cancel()
                self.idle_timer = None

    def release(self):
        with self.lock:
            self.client_count = max(0, self.client_count - 1)
            if self.client_count:
                return
            if self.idle_timer:
                self.idle_timer.cancel()
            self.idle_timer = threading.Timer(REALTIME_IDLE_SECONDS, self.stop_if_idle)
            self.idle_timer.daemon = True
            self.idle_timer.start()

    def stop_if_idle(self):
        with _realtime_hubs_lock:
            with self.lock:
                if self.client_count or _realtime_hubs.get(self.key) is not self:
                    return
                self.stop_locked()
                _realtime_hubs.pop(self.key, None)

    def ensure(self, symbols):
        symbols = sorted(dict.fromkeys(symbols))
        with self.lock:
            if self.symbols == symbols and (
                (self.twm and self.socket_key) or
                (self.direct_thread and self.direct_thread.is_alive())
            ):
                return True
            self.stop_locked()
            self.symbols = symbols
            self.latest = {s: self.latest[s] for s in symbols if s in self.latest}
            self.error = None
            if not symbols:
                return False
            if websocket_connect is not None:
                return self.start_direct_locked(symbols)
            if ThreadedWebsocketManager is None:
                self.error = "python-binance is not installed"
                LOG.error("realtime unavailable: python-binance and websockets are not installed; symbols=%s", symbols)
                return False
            try:
                streams = []
                for symbol in symbols:
                    lower = symbol.lower()
                    streams.append(f"{lower}@bookTicker")
                    streams.append(f"{lower}@depth20@500ms")
                self.twm = ThreadedWebsocketManager()
                self.twm.start()
                self.socket_key = self.twm.start_futures_multiplex_socket(
                    callback=self.handle_message,
                    streams=streams,
                )
                return True
            except Exception as exc:
                self.error = str(exc)
                LOG.exception("realtime python-binance websocket start failed; symbols=%s; fallback=direct", symbols)
                self.stop_locked()
                return self.start_direct_locked(symbols)

    def stop_locked(self):
        old_thread = self.direct_thread
        self.direct_generation += 1
        if self.direct_stop:
            self.direct_stop.set()
        self.direct_stop = None
        self.direct_thread = None
        self.direct_connected = False
        if self.twm:
            try:
                if self.socket_key:
                    self.twm.stop_socket(self.socket_key)
            except Exception:
                pass
            try:
                self.twm.stop()
            except Exception:
                pass
        self.twm = None
        self.socket_key = None
        if old_thread and old_thread.is_alive():
            threading.Thread(target=old_thread.join, args=(1,), daemon=True).start()

    def start_direct_locked(self, symbols):
        if websocket_connect is None:
            self.error = (self.error or "") + "; websockets is not installed"
            LOG.error("realtime direct websocket unavailable: websockets is not installed; symbols=%s", symbols)
            return False
        self.direct_generation += 1
        generation = self.direct_generation
        self.direct_stop = threading.Event()
        self.direct_thread = threading.Thread(
            target=self.run_direct,
            args=(list(symbols), self.direct_stop, generation),
            daemon=True,
        )
        self.direct_thread.start()
        return True

    def _direct_worker_is_current_locked(self, stop_event, generation):
        return self.direct_stop is stop_event and self.direct_generation == generation

    def run_direct(self, symbols, stop_event, generation):
        try:
            asyncio.run(self.direct_loop(symbols, stop_event, generation))
        except Exception as exc:
            with self.lock:
                current = self._direct_worker_is_current_locked(stop_event, generation)
                if current:
                    self.error = str(exc)
                    self.direct_connected = False
            if current:
                LOG.exception("realtime direct websocket loop crashed; symbols=%s", symbols)

    def maintain(self, symbols):
        symbols = sorted(dict.fromkeys(symbols))
        now_ms = int(time.time() * 1000)
        reason = ""
        with self.lock:
            direct_alive = bool(self.direct_thread and self.direct_thread.is_alive())
            if self.direct_thread is not None and not direct_alive:
                reason = "worker_exited"
            elif self.direct_connected:
                activity_ms = max(self.last_message_ms, self.last_connected_ms)
                if activity_ms and now_ms - activity_ms > REALTIME_STALE_SECONDS * 1000:
                    reason = "message_stale"
            if not reason:
                return True
            self.restart_count += 1
            LOG.warning(
                "realtime websocket restarting; reason=%s; symbols=%s; restart_count=%s",
                reason,
                symbols,
                self.restart_count,
            )
            self.stop_locked()
            self.symbols = []
        return self.ensure(symbols)

    async def direct_loop(self, symbols, stop_event, generation):
        parts = []
        for symbol in symbols:
            lower = symbol.lower()
            parts.append(f"{lower}@bookTicker")
            parts.append(f"{lower}@depth20@500ms")
        streams = "/".join(parts)
        url = f"wss://fstream.binance.com/stream?streams={streams}"
        try:
            while not stop_event.is_set():
                try:
                    async with websocket_connect(
                        url,
                        ping_interval=20,
                        ping_timeout=10,
                        open_timeout=10,
                        close_timeout=3,
                    ) as ws:
                        with self.lock:
                            if not self._direct_worker_is_current_locked(stop_event, generation) or stop_event.is_set():
                                break
                            self.error = None
                            self.direct_connected = True
                            self.connect_count += 1
                            connect_count = self.connect_count
                            self.last_connected_ms = int(time.time() * 1000)
                        LOG.info("realtime websocket connected; symbols=%s; connect_count=%s", symbols, connect_count)
                        while not stop_event.is_set():
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1)
                            except asyncio.TimeoutError:
                                continue
                            with self.lock:
                                if not self._direct_worker_is_current_locked(stop_event, generation):
                                    break
                            self.handle_message(json.loads(raw), direct_worker=(stop_event, generation))
                except Exception as exc:
                    error_text = f"{type(exc).__name__}: {exc}".strip()
                    with self.lock:
                        if not self._direct_worker_is_current_locked(stop_event, generation):
                            break
                        self.error = error_text
                        self.direct_connected = False
                        self.disconnect_count += 1
                        disconnect_count = self.disconnect_count
                        self.last_disconnected_ms = int(time.time() * 1000)
                    if stop_event.is_set():
                        break
                    LOG.error(
                        "realtime websocket disconnected; reconnect_in=3s; symbols=%s; disconnect_count=%s; error=%s",
                        symbols,
                        disconnect_count,
                        error_text,
                        exc_info=True,
                    )
                    await asyncio.sleep(3)
        finally:
            with self.lock:
                if self._direct_worker_is_current_locked(stop_event, generation):
                    self.direct_connected = False

    def handle_message(self, msg, direct_worker=None):
        try:
            data = msg.get("data", msg) if isinstance(msg, dict) else {}
            symbol = normalize_symbol(data.get("s"))
            if not symbol:
                return
            event_ms = int(data.get("T") or data.get("E") or int(time.time() * 1000))
            received_ms = int(time.time() * 1000)
            with self.lock:
                if direct_worker and not self._direct_worker_is_current_locked(*direct_worker):
                    return
                self.last_message_ms = received_ms
            bids_raw = data.get("b")
            asks_raw = data.get("a")
            is_depth = isinstance(bids_raw, list) or data.get("e") == "depthUpdate"
            if is_depth:
                bids = [(float(price), float(qty)) for price, qty in (bids_raw or [])[:20]]
                asks = [(float(price), float(qty)) for price, qty in (asks_raw or [])[:20]]
                if not bids or not asks:
                    return
                bid = bids[0][0]
                ask = asks[0][0]
                mid = (bid + ask) / 2.0 if bid and ask else 0.0
                bid_top5 = sum(price * qty for price, qty in bids[:5])
                ask_top5 = sum(price * qty for price, qty in asks[:5])
                bid_top20 = sum(price * qty for price, qty in bids)
                ask_top20 = sum(price * qty for price, qty in asks)
                total = bid_top20 + ask_top20
                depth_update = {
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "event_ms": event_ms,
                    "received_ms": received_ms,
                    "depth_event_ms": event_ms,
                    "depth_received_ms": received_ms,
                    "depth_ok": bool(bid_top5 and ask_top5),
                    "depth_imbalance": (bid_top20 - ask_top20) / total if total else 0.0,
                    "bid_depth_top5_usd": bid_top5,
                    "ask_depth_top5_usd": ask_top5,
                    "bid_depth_top20_usd": bid_top20,
                    "ask_depth_top20_usd": ask_top20,
                    "depth_ladder": {
                        "bids": [[p, q, p * q] for p, q in bids[:10]],
                        "asks": [[p, q, p * q] for p, q in asks[:10]],
                    },
                    "depth_source": "futures_depth20",
                }
                with self.lock:
                    if direct_worker and not self._direct_worker_is_current_locked(*direct_worker):
                        return
                    item = dict(self.latest.get(symbol, {}))
                    has_book_ticker = "bookTicker" in str(item.get("source") or "")
                    item.update(depth_update)
                    # depth20 is a fresh partial-book snapshot, so its top of
                    # book is also a fresh midpoint price rather than a reason
                    # to keep an older bookTicker midpoint alive.
                    item["price"] = mid
                    item["price_event_ms"] = event_ms
                    item["price_received_ms"] = received_ms
                    item["price_kind"] = "book_mid"
                    item["source"] = "futures_bookTicker+depth20" if has_book_ticker else "futures_depth20"
                    self.latest[symbol] = item
                    self.last_depth_event_ms = event_ms
                    self.error = None
                persist_realtime_later(symbol, item)
                return
            price = data.get("p") or data.get("c")
            bid = bids_raw
            ask = asks_raw
            if price is None and bid is not None and ask is not None:
                price = (float(bid) + float(ask)) / 2
            if price is None:
                return
            item = {
                "symbol": symbol,
                "price": float(price),
                "bid": float(bid) if bid is not None else None,
                "ask": float(ask) if ask is not None else None,
                "event_ms": event_ms,
                "received_ms": received_ms,
                "price_event_ms": event_ms,
                "price_received_ms": received_ms,
                "price_kind": "book_mid",
                "source": "futures_bookTicker",
            }
            with self.lock:
                if direct_worker and not self._direct_worker_is_current_locked(*direct_worker):
                    return
                old = dict(self.latest.get(symbol, {}))
                old.update(item)
                if old.get("depth_ok"):
                    old["source"] = "futures_bookTicker+depth20"
                self.latest[symbol] = old
                self.last_price_event_ms = event_ms
                self.error = None
            persist_realtime_later(symbol, old)
        except Exception as exc:
            with self.lock:
                if direct_worker and not self._direct_worker_is_current_locked(*direct_worker):
                    return
                self.error = str(exc)
            LOG.exception("realtime websocket message parse failed; error=%s; message=%r", exc, msg)

    def snapshot(self, symbols):
        with self.lock:
            return {
                "prices": [self.latest[s] for s in symbols if s in self.latest],
                "symbols": symbols,
                "error": self.error,
                "connected": bool((self.twm and self.socket_key) or self.direct_connected),
            }

    def is_active_for(self, symbols):
        requested = set(symbols or [])
        if not requested:
            return False
        with self.lock:
            active = bool((self.twm and self.socket_key) or self.direct_connected)
            return active and requested.issubset(set(self.symbols or []))


class BadRequestError(ValueError):
    pass


def now_bj(timestamp=None) -> str:
    current = datetime.fromtimestamp(timestamp, BJ_TZ) if timestamp is not None else datetime.now(BJ_TZ)
    return current.strftime("%Y-%m-%d %H:%M:%S")


def normalize_symbol(raw):
    symbol = "".join(ch for ch in str(raw or "").upper().strip() if ch.isalnum())
    if symbol and len(symbol) <= 8 and "USDT" not in symbol:
        symbol += "USDT"
    return symbol


def normalize_symbols(raw_parts, fallback=None, limit=MAX_SYMBOLS, reject_overflow=False):
    symbols = []
    seen = set()
    for raw in raw_parts or []:
        symbol = normalize_symbol(raw)
        if not symbol or symbol in seen:
            continue
        if len(symbols) >= limit:
            if reject_overflow:
                raise BadRequestError(f"at most {limit} symbols are allowed")
            break
        symbols.append(symbol)
        seen.add(symbol)
    return symbols or list(fallback or [])


def parse_query_symbols(query, fallback=None, limit=MAX_SYMBOLS, reject_overflow=False):
    params = parse_qs(query or "")
    raw_parts = list(params.get("symbol", []))
    for item in params.get("symbols", []):
        raw_parts.extend(item.split(","))
    return normalize_symbols(raw_parts, fallback=fallback, limit=limit, reject_overflow=reject_overflow)


def parse_symbols(query, reject_overflow=False):
    return parse_query_symbols(query, fallback=DEFAULT_SYMBOLS, reject_overflow=reject_overflow)


def cache_key(symbols):
    return ",".join(sorted(normalize_symbols(symbols)))


def realtime_cache_key(symbols):
    return ",".join(sorted(normalize_symbols(symbols)))


def market_lock_for(key):
    with _market_locks_guard:
        lock = _market_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _market_locks[key] = lock
        _market_lock_refs[key] = int(_market_lock_refs.get(key, 0)) + 1
        return lock


def release_market_lock(key, lock):
    with _market_locks_guard:
        if _market_locks.get(key) is not lock:
            return
        refs = max(0, int(_market_lock_refs.get(key, 0)) - 1)
        if refs:
            _market_lock_refs[key] = refs
        else:
            _market_lock_refs.pop(key, None)


def prune_memory_cache(now=None):
    now = now or time.time()
    stale = []
    with _payload_lock:
        for key, entry in list(_last_payloads.items()):
            if now - entry.get("ts", 0) > CACHE_TTL_SECONDS * 4:
                stale.append(key)
        for key in stale:
            _last_payloads.pop(key, None)
        if len(_last_payloads) > MEMORY_CACHE_MAX_ITEMS:
            ordered = sorted(_last_payloads.items(), key=lambda kv: kv[1].get("ts", 0))
            for key, _ in ordered[: len(_last_payloads) - MEMORY_CACHE_MAX_ITEMS]:
                _last_payloads.pop(key, None)
        active_keys = set(_last_payloads.keys())
    with _market_locks_guard:
        for key in list(_market_locks.keys()):
            if key not in active_keys and not _market_lock_refs.get(key):
                _market_locks.pop(key, None)


def _realtime_storage_worker():
    while True:
        _realtime_storage_event.wait()
        time.sleep(0.05)
        with _realtime_storage_lock:
            pending = dict(_realtime_storage_pending)
            _realtime_storage_pending.clear()
            _realtime_storage_event.clear()
        if storage is None:
            continue
        for symbol, item in pending.items():
            try:
                storage.set_realtime_price(symbol, item)
            except Exception:
                LOG.exception("realtime Redis persistence failed; symbol=%s", symbol)


def persist_realtime_later(symbol, item):
    global _realtime_storage_thread
    if storage is None or not symbol or not item:
        return
    with _realtime_storage_lock:
        _realtime_storage_pending[symbol] = dict(item)
        if _realtime_storage_thread is None or not _realtime_storage_thread.is_alive():
            _realtime_storage_thread = threading.Thread(
                target=_realtime_storage_worker,
                name="bian-realtime-storage",
                daemon=True,
            )
            _realtime_storage_thread.start()
        _realtime_storage_event.set()


def realtime_hub_for(symbols):
    key = realtime_cache_key(symbols)
    with _realtime_hubs_lock:
        hub = _realtime_hubs.get(key)
        if hub is None:
            hub = RealtimePriceHub(key)
            _realtime_hubs[key] = hub
        return hub


def realtime_hub_for_request(symbols):
    requested = sorted(normalize_symbols(symbols))
    key = realtime_cache_key(requested)
    with _realtime_hubs_lock:
        exact = _realtime_hubs.get(key)
        if exact is not None:
            exact.acquire()
            return exact, requested, "exact"
        requested_set = set(requested)
        reusable = []
        for hub in _realtime_hubs.values():
            with hub.lock:
                hub_symbols = list(hub.symbols or [])
                active = bool((hub.twm and hub.socket_key) or hub.direct_connected)
            if active and requested_set.issubset(set(hub_symbols)):
                reusable.append((len(hub_symbols), hub.key, hub, hub_symbols))
        if reusable:
            reusable.sort(key=lambda item: (item[0], item[1]))
            _, _, hub, hub_symbols = reusable[0]
            hub.acquire()
            return hub, hub_symbols, "superset"
        hub = RealtimePriceHub(key)
        _realtime_hubs[key] = hub
        hub.acquire()
        return hub, requested, "new"


def record_realtime_sharing(mode):
    mode = mode if mode in _realtime_sharing_counts else "new"
    with _realtime_sharing_lock:
        _realtime_sharing_counts[mode] = int(_realtime_sharing_counts.get(mode, 0)) + 1


def realtime_sharing_stats():
    with _realtime_sharing_lock:
        counts = {key: int(_realtime_sharing_counts.get(key, 0)) for key in ("exact", "new", "superset")}
    total = sum(counts.values())
    saved = counts.get("exact", 0) + counts.get("superset", 0)
    return {
        "counts": counts,
        "total_requests": total,
        "reused_requests": saved,
        "reuse_rate_pct": round((saved / total) * 100.0, 2) if total else 0.0,
    }


def payload_matches(payload, symbols):
    if not isinstance(payload, dict):
        return False
    expected_key = cache_key(symbols)
    if cache_key(payload.get("symbols") or []) == expected_key:
        return True
    data = payload.get("data")
    if not isinstance(data, list):
        return False
    returned = [item.get("symbol") for item in data if isinstance(item, dict)]
    return cache_key(returned) == expected_key


def load_cache(symbols):
    with _cache_lock:
        if not os.path.exists(CACHE_FILE):
            return None
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:
            return None
    key = cache_key(symbols)
    if isinstance(cache, dict) and isinstance(cache.get("payloads"), dict):
        payload = cache["payloads"].get(key)
        if payload and payload_matches(payload, symbols):
            return payload
        return None
    if isinstance(cache, dict) and payload_matches(cache, symbols):
        return cache
    return None


def payload_age_seconds(payload, now=None):
    if not isinstance(payload, dict) or not payload.get("generated_at"):
        return None
    snapshot_ms = parse_snapshot_ms(payload.get("generated_at"), fallback=-1)
    if snapshot_ms <= 0:
        return None
    return max(0.0, (now or time.time()) - snapshot_ms / 1000.0)


def fresh_disk_cache(symbols, now=None):
    now = now or time.time()
    payload = load_cache(symbols)
    age = payload_age_seconds(payload, now)
    if payload and age is not None and age <= CACHE_TTL_SECONDS:
        return payload, age
    return None, None


def save_cache(symbols, payload):
    key = cache_key(symbols)
    with _cache_lock:
        cache = {"version": 2, "last_key": key, "payloads": {}}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
                if isinstance(existing, dict) and isinstance(existing.get("payloads"), dict):
                    cache = existing
                elif isinstance(existing, dict):
                    old_symbols = [item.get("symbol") for item in existing.get("data", []) if isinstance(item, dict)]
                    if old_symbols:
                        cache["payloads"][cache_key(old_symbols)] = existing
            except Exception:
                pass
        cache["version"] = 2
        cache["last_key"] = key
        payloads = cache.setdefault("payloads", {})
        payloads[key] = payload
        if len(payloads) > MEMORY_CACHE_MAX_ITEMS:
            ordered = sorted(
                payloads,
                key=lambda item_key: parse_snapshot_ms(
                    (payloads.get(item_key) or {}).get("generated_at"),
                    fallback=0,
                ),
            )
            for stale_key in ordered[: len(payloads) - MEMORY_CACHE_MAX_ITEMS]:
                payloads.pop(stale_key, None)
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
            os.replace(tmp, CACHE_FILE)
        except Exception:
            LOG.exception("market disk cache write failed; key=%s", key)


def run_bian_json(symbols):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    with _run_semaphore:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                BIAN,
                "--symbols",
                ",".join(symbols),
                "--json",
                "--backtest-cache-file",
                BACKTEST_CACHE_FILE,
                "--backtest-cache-ttl",
                str(BACKTEST_CACHE_TTL_SECONDS),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=RUN_TIMEOUT_SECONDS,
        )


def classify_api_error(error, stderr=""):
    text = f"{error}\n{stderr or ''}"
    if "HTTP 400" in text or "Invalid symbol" in text or "illegal" in text.lower():
        return "bad_request", 400, False, "请求参数或交易对无效"
    if "HTTP 418" in text or "HTTP 429" in text or "rate limited" in text.lower():
        return "rate_limited", 429, True, "Binance 限流，返回旧快照"
    if "HTTP 5" in text:
        return "upstream_5xx", 502, False, "Binance 上游异常"
    if "timeout" in text.lower():
        return "timeout", 504, True, "分析超时，返回旧快照"
    if "Network error" in text or "urlopen" in text or "timed out" in text.lower():
        return "network", 502, True, "网络异常，返回旧快照"
    return "hard_error", 502, False, "分析失败"


def symbols_from_market_data(data):
    if not isinstance(data, list):
        return set()
    symbols = set()
    for item in data:
        if isinstance(item, dict):
            symbol = normalize_symbol(item.get("symbol"))
            if symbol:
                symbols.add(symbol)
    return symbols


def parse_symbol_filter(query):
    return parse_query_symbols(query)


def parse_signal_review_limit(query):
    params = parse_qs(query or "")
    raw = params.get("limit", [str(SIGNAL_REVIEW_DEFAULT_LIMIT)])[0]
    text = str(raw or "").strip()
    if not text:
        return SIGNAL_REVIEW_DEFAULT_LIMIT
    try:
        limit = int(text)
    except (TypeError, ValueError):
        raise BadRequestError("limit must be an integer")
    if not 1 <= limit <= SIGNAL_REVIEW_MAX_LIMIT:
        raise BadRequestError(f"limit must be between 1 and {SIGNAL_REVIEW_MAX_LIMIT}")
    return limit


def parse_snapshot_ms(text, fallback=None):
    if text:
        raw = str(text).strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return int(datetime.strptime(raw[:19], fmt).replace(tzinfo=BJ_TZ).timestamp() * 1000)
            except Exception:
                pass
        try:
            return int(datetime.fromisoformat(raw).timestamp() * 1000)
        except Exception:
            pass
    return int((fallback or time.time()) * 1000)


def side_from_bias_text(text):
    value = str(text or "").lower()
    if "偏空" in value or "short" in value or "bear" in value:
        return "short"
    if "偏多" in value or "long" in value or "bull" in value:
        return "long"
    return "wait"


def float_or_zero(value):
    try:
        num = float(value)
        return num if num == num and abs(num) != float("inf") else 0.0
    except Exception:
        return 0.0


def signal_review_roundtrip_cost_pct():
    return max(0.0, 2.0 * (SIGNAL_REVIEW_TAKER_FEE_BPS + SIGNAL_REVIEW_SLIPPAGE_BPS) / 100.0)


def price_key(value):
    return f"{float_or_zero(value):.8g}"


def build_signal_key(symbol, advice_name, side, entry, stop, snapshot_ms):
    bucket = int(snapshot_ms or 0) // (5 * 60 * 1000)
    raw = "|".join([symbol, advice_name, side, price_key(entry), price_key(stop), str(bucket)])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def invalid_signal_evaluation(reason):
    return {
        "invalid": {
            "status": "done",
            "failure_reason": reason,
            "evaluated_at_ms": int(time.time() * 1000),
        }
    }


def bucket_atr_regime(atr_pct):
    value = float_or_zero(atr_pct)
    if value >= 12:
        return "extreme"
    if value >= 8:
        return "high"
    if value >= 3:
        return "normal"
    return "low"


def bucket_boll_width_regime(width_pct):
    value = float_or_zero(width_pct)
    if value >= 45:
        return "extreme"
    if value >= 25:
        return "wide"
    if value >= 8:
        return "normal"
    return "tight"


def bucket_boll_position_regime(position_pct):
    value = float_or_zero(position_pct)
    if value >= 90:
        return "upper_extreme"
    if value >= 70:
        return "upper"
    if value <= 10:
        return "lower_extreme"
    if value <= 30:
        return "lower"
    return "middle"


def build_market_regime(report, advice):
    if not isinstance(report, dict) or not isinstance(advice, dict):
        return {}
    anchor_interval = str(advice.get("anchor_interval") or "").strip()
    indicators = report.get("indicators") if isinstance(report.get("indicators"), dict) else {}
    anchor = indicators.get(anchor_interval) if anchor_interval else None
    if not isinstance(anchor, dict):
        return {"anchor_interval": anchor_interval} if anchor_interval else {}
    atr_pct = float_or_zero(anchor.get("atr14_pct"))
    boll_width = float_or_zero(anchor.get("boll_bandwidth_pct"))
    boll_position = float_or_zero(anchor.get("boll_position_pct"))
    return {
        "anchor_interval": anchor_interval,
        "trend_regime": str(anchor.get("emt") or "-"),
        "atr_regime": bucket_atr_regime(atr_pct),
        "boll_width_regime": bucket_boll_width_regime(boll_width),
        "boll_position_regime": bucket_boll_position_regime(boll_position),
        "atr_pct": round(atr_pct, 4),
        "boll_bandwidth_pct": round(boll_width, 4),
        "boll_position_pct": round(boll_position, 4),
    }


def build_signal_review_records(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    generated_at = payload.get("analysis_completed_at") or payload.get("generated_at") or now_bj()
    try:
        snapshot_ms = int(payload.get("published_at_ms") or 0)
    except (TypeError, ValueError):
        snapshot_ms = 0
    if snapshot_ms <= 0:
        snapshot_ms = parse_snapshot_ms(generated_at)
    records = []
    for report in payload.get("data") or []:
        if not isinstance(report, dict):
            continue
        symbol = normalize_symbol(report.get("symbol"))
        if not symbol:
            continue
        publication_price = float_or_zero(report.get("publication_price"))
        publication_price_ms = int(report.get("publication_price_observed_at_ms") or 0)
        if publication_price_ms <= 0:
            publication_price = float_or_zero(report.get("last"))
            publication_price_ms = int(report.get("price_observed_at_ms") or 0)
        price_age_ms = snapshot_ms - publication_price_ms if publication_price_ms > 0 else -1
        snapshot_price = publication_price if 0 <= price_age_ms <= 15_000 else 0.0
        for advice in report.get("timeframe_advice") or []:
            if not isinstance(advice, dict):
                continue
            side = side_from_bias_text(advice.get("bias"))
            if side not in ("long", "short"):
                continue
            entry_key = "short_entry" if side == "short" else "long_entry"
            entry = float_or_zero(advice.get(entry_key))
            stop = float_or_zero(advice.get("stop_hint"))
            status = "pending"
            failure_reason = ""
            evaluation = {}
            if entry <= 0:
                status = "evaluated"
                failure_reason = "entry_invalid"
                evaluation = invalid_signal_evaluation(failure_reason)
            elif stop <= 0:
                status = "evaluated"
                failure_reason = "stop_invalid"
                evaluation = invalid_signal_evaluation(failure_reason)
            elif side == "long" and stop >= entry:
                status = "evaluated"
                failure_reason = "stop_invalid"
                evaluation = invalid_signal_evaluation("long_stop_not_below_entry")
            elif side == "short" and stop <= entry:
                status = "evaluated"
                failure_reason = "stop_invalid"
                evaluation = invalid_signal_evaluation("short_stop_not_above_entry")
            trigger = advice.get("trigger_check") if isinstance(advice.get("trigger_check"), dict) else {}
            market_regime = build_market_regime(report, advice)
            signal_key = build_signal_key(symbol, str(advice.get("name") or ""), side, entry, stop, snapshot_ms)
            records.append(
                {
                    "signal_key": signal_key,
                    "symbol": symbol,
                    "advice_name": str(advice.get("name") or ""),
                    "side": side,
                    "entry_price": entry,
                    "stop_price": stop,
                    "snapshot_price": snapshot_price,
                    "snapshot_at_ms": snapshot_ms,
                    "snapshot_at": generated_at,
                    "confidence": int(float_or_zero(advice.get("confidence"))),
                    "direction_score": int(float_or_zero(advice.get("direction_score"))),
                    "execution_score": int(float_or_zero(advice.get("execution_score", advice.get("confidence")))),
                    "risk_gate": str(advice.get("risk_gate") or ""),
                    "candle_state": str(advice.get("candle_state") or ""),
                    "trigger_status": str(trigger.get("status") or ""),
                    "market_regime": market_regime,
                    "status": status,
                    "failure_reason": failure_reason,
                    "evaluation": evaluation,
                    "payload": {
                        "generated_at": generated_at,
                        "report_bias": report.get("bias"),
                        "pct_24h": report.get("pct_24h"),
                        "funding_rate": report.get("funding_rate"),
                        "snapshot_price_observed_at_ms": publication_price_ms,
                        "snapshot_price_age_ms": price_age_ms,
                        "snapshot_price_source": report.get("publication_price_source") or "analysis_ticker",
                        "market_regime": market_regime,
                        "advice": advice,
                    },
                }
            )
    return records


def request_binance_json(path, params, timeout=20):
    query = urllib.parse.urlencode(params)
    url = f"{BINANCE_FAPI_BASE}{path}?{query}"
    last_error = None
    for attempt in range(1, 3):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Binance API HTTP {exc.code}: {body[:300]}")
            if exc.code in (400, 404):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"Binance review data fetch failed: {last_error}")


def fetch_publication_prices(symbols):
    requested = set(normalize_symbols(symbols))
    data = request_binance_json("/fapi/v1/ticker/bookTicker", {}, timeout=5)
    observed_at_ms = int(time.time() * 1000)
    prices = {}
    for item in data if isinstance(data, list) else [data]:
        if not isinstance(item, dict):
            continue
        symbol = normalize_symbol(item.get("symbol"))
        if symbol not in requested:
            continue
        bid = float_or_zero(item.get("bidPrice"))
        ask = float_or_zero(item.get("askPrice"))
        if bid <= 0 or ask <= 0:
            continue
        prices[symbol] = {
            "price": (bid + ask) / 2.0,
            "bid": bid,
            "ask": ask,
            "observed_at_ms": observed_at_ms,
            "source": "futures_bookTicker_publication",
        }
    return prices


def fetch_review_klines(symbol, start_ms, end_ms):
    snapshot_ms = max(0, int(start_ms or 0))
    start_ms = snapshot_ms + (-snapshot_ms % 60_000)
    end_ms = max(snapshot_ms + 60_000, int(end_ms or 0))
    minutes = max(1, int((end_ms - start_ms) / 60_000) + 3)
    data = request_binance_json(
        "/fapi/v1/klines",
        {
            "symbol": normalize_symbol(symbol),
            "interval": "1m",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(1000, minutes),
        },
        timeout=20,
    )
    return list(data or [])


def evaluate_horizon_from_klines(record, horizon, horizon_ms, candles):
    side = str(record.get("side") or "")
    entry = float_or_zero(record.get("entry_price"))
    stop = float_or_zero(record.get("stop_price"))
    snapshot_price = float_or_zero(record.get("snapshot_price"))
    snapshot_ms = int(record.get("snapshot_at_ms") or 0)
    end_ms = snapshot_ms + int(horizon_ms)
    usable = []
    for raw in candles or []:
        try:
            open_ms = int(raw[0])
            close_ms = int(raw[6]) if len(raw) > 6 else open_ms + 59_999
            if open_ms < snapshot_ms or close_ms > end_ms:
                continue
            usable.append(
                {
                    "open_ms": open_ms,
                    "high": float(raw[2]),
                    "low": float(raw[3]),
                    "close": float(raw[4]),
                    "close_ms": close_ms,
                }
            )
        except Exception:
            continue
    if side not in ("long", "short") or entry <= 0 or stop <= 0:
        return {"status": "done", "failure_reason": "invalid_signal", "horizon": horizon}
    if not usable:
        return {"status": "done", "failure_reason": "no_market_data", "horizon": horizon, "bars": 0}

    profit_floor = SIGNAL_REVIEW_MIN_PROFIT_PCT.get(horizon, 0.4)
    entry_reached = (side == "long" and snapshot_price and snapshot_price <= entry) or (
        side == "short" and snapshot_price and snapshot_price >= entry
    )
    entry_time_ms = snapshot_ms if entry_reached else 0
    max_profit_pct = 0.0
    max_drawdown_pct = 0.0
    stopped = False
    ambiguous = False
    stop_time_ms = 0
    stop_distance_pct = abs((entry - stop) / entry * 100.0) if entry else 0.0
    close_price = usable[-1]["close"]
    estimated_cost_pct = signal_review_roundtrip_cost_pct()

    def favorable_pct(bar):
        if side == "long":
            return (bar["high"] - entry) / entry * 100.0
        return (entry - bar["low"]) / entry * 100.0

    def adverse_pct(bar):
        if side == "long":
            return (bar["low"] - entry) / entry * 100.0
        return (entry - bar["high"]) / entry * 100.0

    def bar_hits_entry(bar):
        return bar["low"] <= entry if side == "long" else bar["high"] >= entry

    def bar_hits_stop(bar):
        return bar["low"] <= stop if side == "long" else bar["high"] >= stop

    if entry_reached and snapshot_price:
        if (side == "long" and snapshot_price <= stop) or (side == "short" and snapshot_price >= stop):
            stopped = True
            stop_time_ms = snapshot_ms
            ambiguous = True

    for bar in usable:
        if stopped:
            break
        triggered_this_bar = False
        if not entry_reached and bar_hits_entry(bar):
            entry_reached = True
            entry_time_ms = max(snapshot_ms, bar["open_ms"])
            triggered_this_bar = True
        if not entry_reached:
            continue

        if bar_hits_stop(bar):
            stopped = True
            stop_time_ms = max(snapshot_ms, bar["open_ms"])
            if triggered_this_bar:
                ambiguous = True
            break
        # A 1m OHLC bar cannot reveal whether its high/low occurred before the
        # entry touch. Do not claim MFE/MAE from the trigger bar.
        if not triggered_this_bar:
            max_drawdown_pct = min(max_drawdown_pct, adverse_pct(bar))
            max_profit_pct = max(max_profit_pct, favorable_pct(bar))

    if not entry_reached:
        failure_reason = "entry_too_far"
        if side == "long" and usable[-1]["close"] < snapshot_price:
            failure_reason = "not_triggered_adverse_move"
        if side == "short" and usable[-1]["close"] > snapshot_price:
            failure_reason = "not_triggered_adverse_move"
        return {
            "status": "done",
            "horizon": horizon,
            "bars": len(usable),
            "entry_reached": False,
            "stop_hit": False,
            "failure_reason": failure_reason,
            "max_profit_pct": 0.0,
            "gross_max_profit_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "outcome_pct": 0.0,
            "gross_outcome_pct": 0.0,
            "estimated_cost_pct": 0.0,
            "close_price": close_price,
            "evaluated_at_ms": int(time.time() * 1000),
        }

    if stopped:
        gross_outcome_pct = (stop - entry) / entry * 100.0 if side == "long" else (entry - stop) / entry * 100.0
        net_outcome_pct = gross_outcome_pct - estimated_cost_pct
        net_max_profit_pct = max(0.0, max_profit_pct - estimated_cost_pct)
        if stop_distance_pct <= 0.35:
            failure_reason = "stop_too_tight"
        elif ambiguous:
            failure_reason = "same_bar_stop"
        elif net_max_profit_pct < profit_floor:
            failure_reason = "stop_hit_first"
        else:
            failure_reason = "stop_after_profit"
        return {
            "status": "done",
            "horizon": horizon,
            "bars": len(usable),
            "entry_reached": True,
            "entry_time_ms": entry_time_ms,
            "stop_hit": True,
            "stop_hit_first": net_max_profit_pct < profit_floor,
            "same_bar_ambiguous": ambiguous,
            "stop_time_ms": stop_time_ms,
            "failure_reason": failure_reason,
            "max_profit_pct": round(net_max_profit_pct, 4),
            "gross_max_profit_pct": round(max(0.0, max_profit_pct), 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "outcome_pct": round(net_outcome_pct, 4),
            "gross_outcome_pct": round(gross_outcome_pct, 4),
            "estimated_cost_pct": round(estimated_cost_pct, 4),
            "close_price": close_price,
            "evaluated_at_ms": int(time.time() * 1000),
        }

    if side == "long":
        gross_outcome_pct = (close_price - entry) / entry * 100.0
    else:
        gross_outcome_pct = (entry - close_price) / entry * 100.0
    net_outcome_pct = gross_outcome_pct - estimated_cost_pct
    net_max_profit_pct = max(0.0, max_profit_pct - estimated_cost_pct)
    if net_outcome_pct < -0.1:
        failure_reason = "direction_wrong"
    elif net_max_profit_pct < profit_floor:
        failure_reason = "no_follow_through"
    else:
        failure_reason = "ok"
    return {
        "status": "done",
        "horizon": horizon,
        "bars": len(usable),
        "entry_reached": True,
        "entry_time_ms": entry_time_ms,
        "stop_hit": False,
        "stop_hit_first": False,
        "failure_reason": failure_reason,
        "max_profit_pct": round(net_max_profit_pct, 4),
        "gross_max_profit_pct": round(max(0.0, max_profit_pct), 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "outcome_pct": round(net_outcome_pct, 4),
        "gross_outcome_pct": round(gross_outcome_pct, 4),
        "estimated_cost_pct": round(estimated_cost_pct, 4),
        "close_price": close_price,
        "evaluated_at_ms": int(time.time() * 1000),
    }


def summarize_review_failure(evaluation):
    if not isinstance(evaluation, dict):
        return ""
    invalid = evaluation.get("invalid")
    if isinstance(invalid, dict) and invalid.get("failure_reason"):
        return str(invalid.get("failure_reason"))
    for horizon in ("1h", "15m", "5m"):
        item = evaluation.get(horizon)
        if isinstance(item, dict) and item.get("status") == "done":
            reason = str(item.get("failure_reason") or "")
            if reason and reason != "ok":
                return reason
    for horizon in ("1h", "15m", "5m"):
        item = evaluation.get(horizon)
        if isinstance(item, dict) and item.get("status") == "done":
            return str(item.get("failure_reason") or "ok")
    return ""


def evaluate_signal_review_record(record, now_ms):
    evaluation = record.get("evaluation") if isinstance(record.get("evaluation"), dict) else {}
    snapshot_ms = int(record.get("snapshot_at_ms") or 0)
    if snapshot_ms <= 0:
        evaluation["invalid"] = {"status": "done", "failure_reason": "missing_snapshot_time"}
        return "evaluated", "missing_snapshot_time", evaluation, True
    changed = False
    for horizon, horizon_ms in SIGNAL_REVIEW_HORIZONS:
        existing = evaluation.get(horizon)
        if isinstance(existing, dict) and existing.get("status") == "done":
            continue
        if now_ms < snapshot_ms + horizon_ms:
            continue
        try:
            candles = fetch_review_klines(record.get("symbol"), snapshot_ms, snapshot_ms + horizon_ms)
            evaluation[horizon] = evaluate_horizon_from_klines(record, horizon, horizon_ms, candles)
            changed = True
        except Exception as exc:
            LOG.warning(
                "signal review horizon evaluation failed; symbol=%s; key=%s; horizon=%s; error=%s",
                record.get("symbol"),
                record.get("signal_key"),
                horizon,
                exc,
            )
            evaluation[horizon] = {
                "status": "pending",
                "horizon": horizon,
                "last_error": str(exc)[:240],
                "last_error_at_ms": int(time.time() * 1000),
            }
            changed = True
            break
    complete = all(
        isinstance(evaluation.get(horizon), dict) and evaluation[horizon].get("status") == "done"
        for horizon, _ in SIGNAL_REVIEW_HORIZONS
    ) or (isinstance(evaluation.get("invalid"), dict) and evaluation["invalid"].get("status") == "done")
    status = "evaluated" if complete else "partial"
    return status, summarize_review_failure(evaluation), evaluation, changed


def evaluate_due_signal_reviews(max_records=40, blocking=False):
    if storage is None:
        return {"evaluated": 0, "updated": 0, "skipped": 0}
    acquired = _signal_review_eval_lock.acquire(blocking)
    if not acquired:
        return {"evaluated": 0, "updated": 0, "skipped": 1}
    try:
        now_ms = int(time.time() * 1000)
        due = storage.load_due_signal_reviews(now_ms, max_rows=max_records, all_users=auth_enabled())
        updated = 0
        evaluated = 0
        deferred = 0
        evaluated_by_key = {}
        for record in due:
            signal_key = str(record.get("signal_key") or "")
            cached_result = evaluated_by_key.get(signal_key)
            if cached_result is None:
                status, failure_reason, evaluation, changed = evaluate_signal_review_record(record, now_ms)
                evaluated_by_key[signal_key] = (
                    status,
                    failure_reason,
                    copy.deepcopy(evaluation),
                    changed,
                )
            else:
                status, failure_reason, evaluation, changed = cached_result
                evaluation = copy.deepcopy(evaluation)
            if not changed:
                if storage.defer_signal_review(
                    record.get("signal_key"),
                    user_id=record.get("storage_user_id"),
                ):
                    deferred += 1
                continue
            if storage.update_signal_review_evaluation(
                record.get("signal_key"),
                status,
                failure_reason,
                evaluation,
                user_id=record.get("storage_user_id"),
            ):
                updated += 1
                if status == "evaluated":
                    evaluated += 1
        if updated:
            LOG.info("signal reviews evaluated; updated=%s; evaluated=%s; due=%s", updated, evaluated, len(due))
        result = {
            "evaluated": evaluated,
            "updated": updated,
            "deferred": deferred,
            "due": len(due),
            "skipped": 0,
        }
        with _signal_review_trigger_lock:
            _signal_review_eval_state["last_finished_at"] = time.time()
            _signal_review_eval_state["last_result"] = result
            _signal_review_eval_state["last_error"] = ""
        return result
    finally:
        _signal_review_eval_lock.release()


def signal_review_eval_status(now=None):
    now = now or time.time()
    with _signal_review_trigger_lock:
        state = dict(_signal_review_eval_state)
    return {
        "running": _signal_review_eval_lock.locked(),
        "trigger_min_interval_seconds": SIGNAL_REVIEW_TRIGGER_MIN_INTERVAL_SECONDS,
        "trigger_count": int(state.get("trigger_count") or 0),
        "thread_started_count": int(state.get("thread_started_count") or 0),
        "skipped_recent_count": int(state.get("skipped_recent_count") or 0),
        "skipped_running_count": int(state.get("skipped_running_count") or 0),
        "last_trigger_age_seconds": round(max(0.0, now - float(state.get("last_trigger_at") or 0.0)), 3)
        if state.get("last_trigger_at") else None,
        "last_started_age_seconds": round(max(0.0, now - float(state.get("last_started_at") or 0.0)), 3)
        if state.get("last_started_at") else None,
        "last_finished_age_seconds": round(max(0.0, now - float(state.get("last_finished_at") or 0.0)), 3)
        if state.get("last_finished_at") else None,
        "last_result": state.get("last_result") or {},
        "last_error": state.get("last_error") or "",
    }


def trigger_signal_review_evaluation(force=False):
    if storage is None:
        return {"scheduled": False, "reason": "storage_unavailable"}
    now = time.time()
    with _signal_review_trigger_lock:
        _signal_review_eval_state["trigger_count"] = int(_signal_review_eval_state.get("trigger_count") or 0) + 1
        last_trigger = float(_signal_review_eval_state.get("last_trigger_at") or 0.0)
        if _signal_review_eval_lock.locked():
            _signal_review_eval_state["skipped_running_count"] = int(_signal_review_eval_state.get("skipped_running_count") or 0) + 1
            return {"scheduled": False, "reason": "already_running"}
        if not force and last_trigger and now - last_trigger < SIGNAL_REVIEW_TRIGGER_MIN_INTERVAL_SECONDS:
            _signal_review_eval_state["skipped_recent_count"] = int(_signal_review_eval_state.get("skipped_recent_count") or 0) + 1
            return {"scheduled": False, "reason": "recently_triggered"}
        _signal_review_eval_state["last_trigger_at"] = now
        _signal_review_eval_state["thread_started_count"] = int(_signal_review_eval_state.get("thread_started_count") or 0) + 1

    def worker():
        with _signal_review_trigger_lock:
            _signal_review_eval_state["last_started_at"] = time.time()
        try:
            evaluate_due_signal_reviews(max_records=24, blocking=False)
        except Exception as exc:
            with _signal_review_trigger_lock:
                _signal_review_eval_state["last_finished_at"] = time.time()
                _signal_review_eval_state["last_error"] = str(exc)[-300:]
            LOG.exception("signal review evaluation worker failed")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return {"scheduled": True, "reason": "started"}


def normalize_signal_review_result(item):
    if not isinstance(item, dict):
        return item
    out = dict(item)
    if out.get("status") != "done":
        return out
    if out.get("estimated_cost_pct") is not None:
        return out
    gross_max_profit = float_or_zero(out.get("max_profit_pct"))
    gross_outcome = float_or_zero(out.get("outcome_pct"))
    entry_reached = bool(out.get("entry_reached"))
    estimated_cost = signal_review_roundtrip_cost_pct() if entry_reached else 0.0
    out["gross_max_profit_pct"] = round(gross_max_profit, 4)
    out["gross_outcome_pct"] = round(gross_outcome, 4)
    out["estimated_cost_pct"] = round(estimated_cost, 4)
    if entry_reached:
        out["max_profit_pct"] = round(max(0.0, gross_max_profit - estimated_cost), 4)
        out["outcome_pct"] = round(gross_outcome - estimated_cost, 4)
    return out


def normalize_signal_review_evaluation(evaluation):
    if not isinstance(evaluation, dict):
        return {}
    normalized = {}
    for key, value in evaluation.items():
        normalized[key] = normalize_signal_review_result(value) if isinstance(value, dict) else value
    return normalized


def compact_signal_review_record(item):
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    market_regime = item.get("market_regime") if isinstance(item.get("market_regime"), dict) else payload.get("market_regime")
    evaluation = normalize_signal_review_evaluation(item.get("evaluation"))
    return {
        "signal_key": item.get("signal_key"),
        "symbol": item.get("symbol"),
        "advice_name": item.get("advice_name"),
        "side": item.get("side"),
        "entry_price": item.get("entry_price"),
        "stop_price": item.get("stop_price"),
        "snapshot_price": item.get("snapshot_price"),
        "snapshot_at_ms": item.get("snapshot_at_ms"),
        "snapshot_at": item.get("snapshot_at"),
        "confidence": item.get("confidence"),
        "direction_score": item.get("direction_score"),
        "execution_score": item.get("execution_score"),
        "risk_gate": item.get("risk_gate"),
        "candle_state": item.get("candle_state"),
        "trigger_status": item.get("trigger_status"),
        "market_regime": market_regime if isinstance(market_regime, dict) else {},
        "status": item.get("status"),
        "failure_reason": item.get("failure_reason"),
        "evaluation": evaluation,
        "created_at_ms": item.get("created_at_ms"),
    }


def build_signal_review_stats(records):
    min_calibration_samples = 30
    min_calibration_triggered = 10
    now_ms = int(time.time() * 1000)
    due_cutoff_ms = now_ms - 5 * 60 * 1000

    def new_review_bucket():
        return {
            "sample_count": 0,
            "triggered_count": 0,
            "signal_keys": set(),
            "triggered_signal_keys": set(),
            "win_count": 0,
            "stop_count": 0,
            "not_triggered_count": 0,
            "invalid_count": 0,
            "sum_max_profit_pct": 0.0,
            "sum_max_drawdown_pct": 0.0,
            "sum_outcome_pct": 0.0,
            "failure_reasons": {},
        }

    def add_review_bucket(bucket, item, signal_key=""):
        bucket["sample_count"] += 1
        if signal_key:
            bucket["signal_keys"].add(str(signal_key))
        reason = str(item.get("failure_reason") or "unknown")
        bucket["failure_reasons"][reason] = bucket["failure_reasons"].get(reason, 0) + 1
        if item.get("entry_reached"):
            bucket["triggered_count"] += 1
            if signal_key:
                bucket["triggered_signal_keys"].add(str(signal_key))
            if item.get("stop_hit"):
                bucket["stop_count"] += 1
            elif str(item.get("failure_reason") or "") == "ok" and float_or_zero(item.get("outcome_pct")) > 0:
                bucket["win_count"] += 1
        else:
            bucket["not_triggered_count"] += 1
        bucket["sum_max_profit_pct"] += float_or_zero(item.get("max_profit_pct"))
        bucket["sum_max_drawdown_pct"] += float_or_zero(item.get("max_drawdown_pct"))
        bucket["sum_outcome_pct"] += float_or_zero(item.get("outcome_pct"))
        return reason

    def add_invalid_bucket(bucket, item, signal_key=""):
        bucket["sample_count"] += 1
        if signal_key:
            bucket["signal_keys"].add(str(signal_key))
        bucket["invalid_count"] += 1
        reason = str(item.get("failure_reason") or "invalid_signal")
        bucket["failure_reasons"][reason] = bucket["failure_reasons"].get(reason, 0) + 1
        return reason

    def finalize_review_bucket(bucket):
        sample = bucket["sample_count"]
        triggered = bucket["triggered_count"]
        market_sample = max(0, sample - bucket["invalid_count"])
        return {
            "sample_count": sample,
            "triggered_count": triggered,
            "unique_signal_count": len(bucket["signal_keys"]),
            "unique_triggered_count": len(bucket["triggered_signal_keys"]),
            "invalid_count": bucket["invalid_count"],
            "hit_rate_pct": round(bucket["win_count"] / triggered * 100.0, 2) if triggered else 0.0,
            "stop_rate_pct": round(bucket["stop_count"] / triggered * 100.0, 2) if triggered else 0.0,
            "not_triggered_rate_pct": round(bucket["not_triggered_count"] / market_sample * 100.0, 2) if market_sample else 0.0,
            "invalid_rate_pct": round(bucket["invalid_count"] / sample * 100.0, 2) if sample else 0.0,
            "avg_max_profit_pct": round(bucket["sum_max_profit_pct"] / market_sample, 4) if market_sample else 0.0,
            "avg_max_drawdown_pct": round(bucket["sum_max_drawdown_pct"] / market_sample, 4) if market_sample else 0.0,
            "avg_outcome_pct": round(bucket["sum_outcome_pct"] / market_sample, 4) if market_sample else 0.0,
            "failure_reasons": bucket["failure_reasons"],
        }

    def segment_value(record, field):
        if field in ("anchor_interval", "trend_regime", "atr_regime", "boll_width_regime", "boll_position_regime"):
            regime = record.get("market_regime") if isinstance(record.get("market_regime"), dict) else {}
            value = str(regime.get(field) or "-").strip()
            return value[:64] or "-"
        value = str(record.get(field) or "-").strip()
        return value[:64] or "-"

    stats = {
        "records": len(records or []),
        "pending": 0,
        "evaluated_records": 0,
        "invalid_records": 0,
        "due_pending": 0,
        "per_horizon": {},
        "top_failures": {},
        "calibration": {},
        "calibration_horizon": SIGNAL_REVIEW_CALIBRATION_HORIZON,
        "segments": {
            "risk_gate": {},
            "trigger_status": {},
            "candle_state": {},
            "anchor_interval": {},
            "trend_regime": {},
            "atr_regime": {},
            "boll_width_regime": {},
            "boll_position_regime": {},
        },
    }
    buckets = {
        horizon: new_review_bucket()
        for horizon, _ in SIGNAL_REVIEW_HORIZONS
    }
    segment_buckets = {name: {} for name in stats["segments"]}
    calibration_segment_buckets = {name: {} for name in stats["segments"]}
    for record in records or []:
        evaluation = normalize_signal_review_evaluation(record.get("evaluation"))
        done_any = False
        invalid = evaluation.get("invalid")
        if isinstance(invalid, dict) and invalid.get("status") == "done":
            done_any = True
            stats["invalid_records"] += 1
            reason = str(invalid.get("failure_reason") or record.get("failure_reason") or "invalid_signal")
            invalid_item = dict(invalid)
            invalid_item["failure_reason"] = reason
            stats["top_failures"][reason] = stats["top_failures"].get(reason, 0) + 1
            for segment_name in segment_buckets:
                key = segment_value(record, segment_name)
                add_invalid_bucket(
                    segment_buckets[segment_name].setdefault(key, new_review_bucket()),
                    invalid_item,
                    record.get("signal_key"),
                )
                add_invalid_bucket(
                    calibration_segment_buckets[segment_name].setdefault(key, new_review_bucket()),
                    invalid_item,
                    record.get("signal_key"),
                )
        for horizon in buckets:
            item = evaluation.get(horizon)
            if not isinstance(item, dict) or item.get("status") != "done":
                continue
            done_any = True
            reason = add_review_bucket(buckets[horizon], item, record.get("signal_key"))
            stats["top_failures"][reason] = stats["top_failures"].get(reason, 0) + 1
            for segment_name in segment_buckets:
                key = segment_value(record, segment_name)
                add_review_bucket(
                    segment_buckets[segment_name].setdefault(key, new_review_bucket()),
                    item,
                    record.get("signal_key"),
                )
                if horizon == SIGNAL_REVIEW_CALIBRATION_HORIZON:
                    add_review_bucket(
                        calibration_segment_buckets[segment_name].setdefault(key, new_review_bucket()),
                        item,
                        record.get("signal_key"),
                    )
        if done_any or record.get("status") == "evaluated":
            stats["evaluated_records"] += 1
        else:
            stats["pending"] += 1
            if str(record.get("side") or "") in ("long", "short"):
                if int(record.get("snapshot_at_ms") or 0) <= due_cutoff_ms:
                    stats["due_pending"] += 1
    for horizon, bucket in buckets.items():
        stats["per_horizon"][horizon] = finalize_review_bucket(bucket)
    for segment_name, values in segment_buckets.items():
        ordered = sorted(values.items(), key=lambda kv: kv[1]["sample_count"], reverse=True)
        stats["segments"][segment_name] = {key: finalize_review_bucket(bucket) for key, bucket in ordered}
    calibration_segments = {}
    for segment_name, values in calibration_segment_buckets.items():
        ordered = sorted(values.items(), key=lambda kv: kv[1]["sample_count"], reverse=True)
        calibration_segments[segment_name] = {key: finalize_review_bucket(bucket) for key, bucket in ordered}
    stats["calibration"] = build_signal_calibration_summary(
        calibration_segments,
        min_calibration_samples,
        min_calibration_triggered,
    )
    stats["calibration"]["horizon"] = SIGNAL_REVIEW_CALIBRATION_HORIZON
    return stats


def build_signal_calibration_summary(segments, min_samples=30, min_triggered=10):
    watched_segments = (
        "risk_gate",
        "trigger_status",
        "candle_state",
        "atr_regime",
        "trend_regime",
        "boll_width_regime",
    )
    candidates = []
    eligible_count = 0
    insufficient_count = 0
    for segment_name in watched_segments:
        group = segments.get(segment_name) if isinstance(segments, dict) else {}
        if not isinstance(group, dict):
            continue
        for key, metric in group.items():
            if not isinstance(metric, dict):
                continue
            sample = int(metric.get("unique_signal_count") or metric.get("sample_count") or 0)
            triggered = int(metric.get("unique_triggered_count") or metric.get("triggered_count") or 0)
            if sample < min_samples or triggered < min_triggered:
                insufficient_count += 1
                continue
            eligible_count += 1
            hit = float_or_zero(metric.get("hit_rate_pct"))
            stop = float_or_zero(metric.get("stop_rate_pct"))
            invalid = float_or_zero(metric.get("invalid_rate_pct"))
            outcome = float_or_zero(metric.get("avg_outcome_pct"))
            action = ""
            reason = ""
            if invalid >= 20:
                action = "downgrade"
                reason = "invalid_rate_high"
            elif stop >= 45:
                action = "downgrade"
                reason = "stop_rate_high"
            elif hit < 40:
                action = "downgrade"
                reason = "hit_rate_low"
            elif outcome <= -0.20:
                action = "downgrade"
                reason = "expectancy_negative"
            elif hit >= 60 and stop <= 25 and outcome >= 0.15:
                action = "support"
                reason = "historically_supported"
            if action:
                candidates.append(
                    {
                        "segment": segment_name,
                        "key": key,
                        "action": action,
                        "reason": reason,
                        "sample_count": sample,
                        "triggered_count": triggered,
                        "evaluation_count": int(metric.get("sample_count") or 0),
                        "triggered_evaluation_count": int(metric.get("triggered_count") or 0),
                        "hit_rate_pct": round(hit, 2),
                        "stop_rate_pct": round(stop, 2),
                        "invalid_rate_pct": round(invalid, 2),
                        "avg_outcome_pct": round(outcome, 4),
                    }
                )
    candidates.sort(
        key=lambda item: (
            0 if item["action"] == "downgrade" else 1,
            -item["sample_count"],
            item["segment"],
            str(item["key"]),
        )
    )
    if not eligible_count:
        status = "insufficient_data"
    elif candidates:
        status = "needs_review"
    else:
        status = "stable"
    return {
        "status": status,
        "min_sample_count": min_samples,
        "min_triggered_count": min_triggered,
        "eligible_segment_count": eligible_count,
        "insufficient_segment_count": insufficient_count,
        "candidates": candidates[:12],
    }


def signal_review_diagnostics(check_storage=True, limit=500):
    base = {
        "available": storage is not None,
        "estimated_roundtrip_cost_pct": round(signal_review_roundtrip_cost_pct(), 4),
        "taker_fee_bps": SIGNAL_REVIEW_TAKER_FEE_BPS,
        "slippage_bps": SIGNAL_REVIEW_SLIPPAGE_BPS,
        "sampled_records": 0,
        "pending": 0,
        "evaluated_records": 0,
        "invalid_records": 0,
        "due_pending": 0,
        "calibration_status": "unknown",
        "top_failures": {},
    }
    if storage is None:
        base["calibration_status"] = "storage_unavailable"
        return base
    if not check_storage:
        base["calibration_status"] = "skipped"
        return base
    try:
        records = storage.load_signal_reviews(limit=limit)
        compact = [compact_signal_review_record(item) for item in records]
        stats = build_signal_review_stats(compact)
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - 5 * 60 * 1000
        due_pending = 0
        for item in compact:
            if item.get("status") == "evaluated":
                continue
            if str(item.get("side") or "") not in ("long", "short"):
                continue
            if int(item.get("snapshot_at_ms") or 0) <= cutoff_ms:
                due_pending += 1
        base.update(
            {
                "sampled_records": len(compact),
                "pending": stats.get("pending", 0),
                "evaluated_records": stats.get("evaluated_records", 0),
                "invalid_records": stats.get("invalid_records", 0),
                "due_pending": stats.get("due_pending", due_pending),
                "calibration_status": (stats.get("calibration") or {}).get("status", "unknown"),
                "top_failures": dict(list((stats.get("top_failures") or {}).items())[:8]),
                "evaluator": signal_review_eval_status(),
            }
        )
    except Exception as exc:
        LOG.warning("signal review diagnostics failed: %s", exc)
        base["error"] = str(exc)[-300:]
        base["calibration_status"] = "error"
    return base


_auth_bootstrap_done = False
_auth_bootstrap_lock = threading.RLock()
_login_failures = {}
_login_failures_lock = threading.RLock()


def auth_enabled():
    return AUTH_ENABLED


def health_payload(check_storage=True):
    mysql_configured = bool(storage and getattr(storage, "mysql_configured", False))
    redis_configured = bool(storage and getattr(storage, "redis_configured", False))
    mysql_available = bool(storage and storage.mysql_available()) if check_storage else None
    redis_available = bool(storage and storage.redis_available()) if check_storage else None
    auth_ready = True
    auth_info = {
        "enabled": auth_enabled(),
        "ready": True,
        "login_ready": True,
        "mysql_available": mysql_available,
        "user_count_known": False,
        "has_users": None,
        "first_admin_secret_configured": bool(
            (os.environ.get("BIAN_AUTH_BOOTSTRAP_USER") or "admin").strip()
            and os.environ.get("BIAN_AUTH_BOOTSTRAP_PASSWORD", "").strip()
        ),
        "can_create_first_admin": False,
        "issue": "",
        "cookie_secure": AUTH_COOKIE_SECURE,
        "trust_proxy_headers": AUTH_TRUST_PROXY_HEADERS,
        "session_touch_interval_seconds": AUTH_SESSION_TOUCH_INTERVAL_SECONDS,
        "require_same_origin_post": AUTH_REQUIRE_SAME_ORIGIN_POST,
    }
    if auth_enabled():
        auth_ready = None
        auth_info["login_ready"] = None
        auth_info["issue"] = "not_checked"
        if storage is None:
            auth_ready = False
            auth_info.update({"ready": False, "login_ready": False, "issue": "storage_unavailable"})
        elif check_storage:
            storage_auth = storage.auth_status()
            auth_info.update(storage_auth)
            auth_ready = bool(storage_auth.get("login_ready"))
        auth_info["ready"] = auth_ready
    analyzer_ok = os.path.exists(BIAN)
    web_ok = os.path.isdir(WEB_ROOT)
    payload = {
        "ok": bool(analyzer_ok and web_ok and auth_ready is not False),
        "time": now_bj(),
        "uptime_seconds": round(max(0.0, time.time() - SERVER_STARTED_AT), 3),
        "service": "bian-dashboard",
        "bind": {"host": HOST, "port": PORT},
        "paths": {
            "web_root": web_ok,
            "analyzer": analyzer_ok,
            "cache_file": CACHE_FILE,
        },
        "auth": auth_info,
        "storage": {
            "mysql": {"configured": mysql_configured, "available": mysql_available},
            "redis": {"configured": redis_configured, "available": redis_available},
        },
        "runtime": {
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "backtest_cache_ttl_seconds": BACKTEST_CACHE_TTL_SECONDS,
            "market_cache_items": len(_last_payloads),
            "realtime_hubs": len(_realtime_hubs),
        },
    }
    return payload


def diagnostics_payload(check_storage=True):
    now = time.time()
    with _payload_lock:
        memory_cache = []
        for key, entry in sorted(_last_payloads.items()):
            payload = entry.get("payload") if isinstance(entry, dict) else {}
            data = payload.get("data") if isinstance(payload, dict) else []
            memory_cache.append(
                {
                    "key": key,
                    "age_seconds": round(max(0.0, now - float(entry.get("ts", 0.0))), 3),
                    "generated_at": payload.get("generated_at") if isinstance(payload, dict) else "",
                    "symbols": payload.get("symbols") if isinstance(payload, dict) else [],
                    "data_count": len(data) if isinstance(data, list) else 0,
                    "stale": bool(payload.get("stale")) if isinstance(payload, dict) else False,
                    "cache_hit": bool(payload.get("cache_hit")) if isinstance(payload, dict) else False,
                }
            )
    with _market_locks_guard:
        market_lock_keys = sorted(_market_locks.keys())
    realtime = []
    with _realtime_hubs_lock:
        hubs = list(_realtime_hubs.items())
    for key, hub in hubs:
        with hub.lock:
            realtime.append(
                {
                    "key": key,
                    "symbols": list(hub.symbols),
                    "client_count": hub.client_count,
                    "connected": bool((hub.twm and hub.socket_key) or hub.direct_connected),
                    "direct_connected": hub.direct_connected,
                    "latest_count": len(hub.latest),
                    "connect_count": hub.connect_count,
                    "disconnect_count": hub.disconnect_count,
                    "restart_count": hub.restart_count,
                    "last_connected_ms": hub.last_connected_ms,
                    "last_disconnected_ms": hub.last_disconnected_ms,
                    "last_message_ms": hub.last_message_ms,
                    "last_price_event_ms": hub.last_price_event_ms,
                    "last_depth_event_ms": hub.last_depth_event_ms,
                    "has_error": bool(hub.error),
                    "error": str(hub.error)[-300:] if hub.error else "",
                }
            )
    if storage is not None and check_storage:
        storage_status = storage.status()
    else:
        storage_status = {
            "mysql": {"configured": bool(storage and getattr(storage, "mysql_configured", False))},
            "redis": {"configured": bool(storage and getattr(storage, "redis_configured", False))},
        }
    return {
        "ok": True,
        "time": now_bj(),
        "uptime_seconds": round(max(0.0, now - SERVER_STARTED_AT), 3),
        "cache": {
            "ttl_seconds": CACHE_TTL_SECONDS,
            "memory_items": len(memory_cache),
            "memory": memory_cache,
            "market_lock_count": len(market_lock_keys),
            "market_lock_keys": market_lock_keys[:32],
        },
        "analyzer": {
            "path_exists": os.path.exists(BIAN),
            "run_timeout_seconds": RUN_TIMEOUT_SECONDS,
            "max_parallel_runs": 2,
            "backtest_cache_ttl_seconds": BACKTEST_CACHE_TTL_SECONDS,
            "expose_error_details": EXPOSE_ERROR_DETAILS,
        },
        "realtime": {
            "hub_count": len(realtime),
            "idle_seconds": REALTIME_IDLE_SECONDS,
            "sse_max_seconds": SSE_MAX_SECONDS,
            "sharing": realtime_sharing_stats(),
            "hubs": realtime,
        },
        "signal_review": signal_review_diagnostics(check_storage=check_storage),
        "storage": storage_status,
    }


def ensure_auth_ready():
    global _auth_bootstrap_done
    if not auth_enabled() or storage is None:
        return True
    with _auth_bootstrap_lock:
        if _auth_bootstrap_done:
            return True
        _auth_bootstrap_done = bool(storage.ensure_auth_bootstrap())
        return _auth_bootstrap_done


def is_public_path(path):
    if not auth_enabled():
        return True
    if path in ("/login", "/api/login", "/api/health", "/favicon.ico"):
        return True
    if path == "/assets/favicon.svg":
        return True
    return False


def safe_next_path(next_path):
    text = str(next_path or "").strip()
    if not text:
        return "/binance-futures-dashboard.html"
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        return "/binance-futures-dashboard.html"
    if not parsed.path.startswith("/") or parsed.path.startswith("//") or "\\" in parsed.path:
        return "/binance-futures-dashboard.html"
    if parsed.path.startswith("/api/") or parsed.path == "/api":
        return "/binance-futures-dashboard.html"
    query = ("?" + parsed.query) if parsed.query else ""
    return parsed.path + query


def _format_host_port(host, port):
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    return f"{host}:{port}" if port else host


def normalize_host_port(value, scheme=""):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "," in text:
        text = text.split(",", 1)[0].strip()
    try:
        parsed = urlparse("//" + text)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except Exception:
        return ""
    if not host:
        return ""
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    if default_port and port == default_port:
        port = None
    return _format_host_port(host, port)


def request_source_host(source_url):
    parsed = urlparse(str(source_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return normalize_host_port(parsed.netloc, parsed.scheme.lower())


def same_origin_allowed(source_url, host_header, forwarded_host=""):
    source_text = str(source_url or "").strip()
    if not source_text:
        return True
    parsed = urlparse(source_text)
    source_host = request_source_host(source_url)
    if not source_host:
        return False
    source_scheme = parsed.scheme.lower()
    allowed = {
        normalize_host_port(host_header, source_scheme),
        normalize_host_port(forwarded_host, source_scheme),
    }
    allowed.discard("")
    return source_host in allowed


def same_origin_allowed_for_peer(source_url, host_header, forwarded_host, peer_ip):
    trusted_forwarded_host = forwarded_host if is_trusted_proxy_peer(peer_ip) else ""
    return same_origin_allowed(source_url, host_header, trusted_forwarded_host)


def login_page(next_path="", error=""):
    next_url = safe_next_path(next_path)
    safe_next = html.escape(next_url, quote=True)
    safe_error = html.escape(error or "", quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bian 登录</title>
<link rel="icon" href="./assets/favicon.svg" type="image/svg+xml">
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#050a14;color:#e8f1ff;font-family:Arial,'Microsoft YaHei',sans-serif}}
.box{{width:min(420px,calc(100vw - 36px));background:#0b182b;border:1px solid #1a2c4a;border-radius:14px;padding:28px;box-shadow:0 16px 42px rgba(0,0,0,.38)}}
.logo{{width:54px;height:54px;border-radius:14px;background:linear-gradient(135deg,#00e5ff,#00ff9d);display:grid;place-items:center;color:#06111f;font-weight:800;font-size:24px;margin-bottom:18px}}
h1{{margin:0 0 8px;font-size:22px;font-weight:700}}
p{{margin:0 0 22px;color:#8aa0c0;font-size:13px;line-height:1.6}}
label{{display:block;margin:14px 0 7px;color:#b8c8e0;font-size:13px}}
input{{width:100%;box-sizing:border-box;border:1px solid #1a2c4a;border-radius:10px;background:#07111f;color:#e8f1ff;padding:12px 13px;font-size:15px;outline:none}}
input:focus{{border-color:#00e5ff;box-shadow:0 0 0 3px rgba(0,229,255,.14)}}
button{{width:100%;margin-top:20px;border:0;border-radius:10px;padding:12px 14px;background:linear-gradient(135deg,#00e5ff,#00ff9d);color:#06111f;font-size:15px;font-weight:800;cursor:pointer}}
.err{{display:none;margin-top:14px;color:#ff6b82;background:rgba(255,59,92,.1);border:1px solid rgba(255,59,92,.28);border-radius:10px;padding:10px;font-size:13px}}
.err.show{{display:block}}
</style>
</head>
<body>
<form class="box" id="login-form">
  <div class="logo">↗</div>
  <h1>Bian 合约助手</h1>
  <p>请输入数据库账号登录。登录后会写入 HttpOnly 会话 Cookie。</p>
  <label for="username">账号</label>
  <input id="username" name="username" autocomplete="username" autofocus>
  <label for="password">密码</label>
  <input id="password" name="password" type="password" autocomplete="current-password">
  <button type="submit">登录</button>
  <div class="err{(' show' if error else '')}" id="err">{safe_error}</div>
</form>
<script>
var form=document.getElementById('login-form');
var err=document.getElementById('err');
form.addEventListener('submit',function(e){{
  e.preventDefault();
  err.className='err';
  fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{
    username:document.getElementById('username').value,
    password:document.getElementById('password').value
  }}),cache:'no-store'}}).then(function(res){{return res.json().then(function(body){{return {{ok:res.ok,body:body}};}});}})
  .then(function(result){{
    if(result.ok&&result.body.authenticated) window.location.href={json.dumps(next_url)};
    else throw new Error((result.body&&result.body.error)||'登录失败');
  }}).catch(function(ex){{err.textContent=ex.message;err.className='err show';}});
}});
</script>
</body>
</html>"""


def login_failure_key(client_ip, username):
    return (client_ip or "", (username or "").strip().lower())


def login_locked(client_ip, username):
    key = login_failure_key(client_ip, username)
    now = time.time()
    with _login_failures_lock:
        item = _login_failures.get(key)
        if not item:
            return False
        until = float(item.get("until", 0) or 0)
        if until and until <= now:
            _login_failures.pop(key, None)
            return False
        return until > now


def record_login_failure(client_ip, username):
    key = login_failure_key(client_ip, username)
    now = time.time()
    with _login_failures_lock:
        retention = max(3600.0, AUTH_LOCKOUT_SECONDS * 2.0)
        if len(_login_failures) >= 4096:
            for stale_key, stale_item in list(_login_failures.items()):
                if now - float(stale_item.get("last_at", 0) or 0) > retention:
                    _login_failures.pop(stale_key, None)
            if len(_login_failures) >= 4096:
                oldest = sorted(_login_failures, key=lambda item_key: _login_failures[item_key].get("last_at", 0))
                for stale_key in oldest[: len(_login_failures) - 4095]:
                    _login_failures.pop(stale_key, None)
        item = _login_failures.get(key, {"count": 0, "until": 0, "last_at": 0})
        if now - float(item.get("last_at", 0) or 0) > retention:
            item = {"count": 0, "until": 0, "last_at": 0}
        count = int(item.get("count", 0)) + 1
        until = now + AUTH_LOCKOUT_SECONDS if count >= AUTH_MAX_FAILURES else 0
        _login_failures[key] = {"count": count, "until": until, "last_at": now}


def clear_login_failures(client_ip, username):
    with _login_failures_lock:
        _login_failures.pop(login_failure_key(client_ip, username), None)


def valid_auth_username(username):
    text = str(username or "").strip()
    if len(text) < 3 or len(text) > 64:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-@")
    return all(ch in allowed for ch in text)


def is_admin_user(user):
    return bool(user and user.get("role") == "admin")


def storage_for_user(user):
    if storage is None or not auth_enabled():
        return storage
    user_id = str((user or {}).get("id") or "").strip()
    if not user_id or not hasattr(storage, "for_user"):
        return None
    return storage.for_user("auth:" + user_id)


def capture_signal_reviews_for_user(user_storage, payload):
    if user_storage is None or not isinstance(payload, dict):
        return None
    records = build_signal_review_records(payload)
    if not records:
        return None
    try:
        result = user_storage.save_signal_reviews(records)
        if int((result or {}).get("inserted") or 0) > 0:
            trigger_signal_review_evaluation()
        return result
    except Exception:
        LOG.exception("signal review capture failed")
        return None


def is_trusted_proxy_peer(peer_ip):
    if not AUTH_TRUST_PROXY_HEADERS:
        return False
    try:
        addr = ipaddress.ip_address(str(peer_ip or "").split("%", 1)[0])
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        self._current_user_loaded = False
        self._current_user_cache = None
        super().__init__(*a, directory=WEB_ROOT, **kw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            payload = health_payload(check_storage=True)
            self.send_json(200 if payload.get("ok") else 503, payload)
            return
        if parsed.path == "/login":
            params = parse_qs(parsed.query)
            self.serve_login(params.get("next", ["/binance-futures-dashboard.html"])[0])
            return
        if not self.require_auth(parsed.path):
            return
        if parsed.path == "/api/market":
            self.serve_api(parsed.query)
        elif parsed.path == "/api/realtime-prices":
            self.serve_realtime_prices(parsed.query)
        elif parsed.path == "/api/preferences":
            self.serve_preferences()
        elif parsed.path == "/api/signal-reviews":
            self.serve_signal_reviews(parsed.query)
        elif parsed.path == "/api/auth/me":
            user = self.current_user()
            self.send_json(200, {"authenticated": bool(user), "user": user})
        elif parsed.path == "/api/storage-status":
            self.serve_storage_status()
        elif parsed.path == "/api/diagnostics":
            self.serve_diagnostics()
        elif parsed.path in ("", "/", "/index.html"):
            self.path = "/binance-futures-dashboard.html"
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self.require_same_origin_post():
            return
        if parsed.path == "/api/login":
            self.login_api()
        elif not self.require_auth(parsed.path):
            return
        elif parsed.path == "/api/logout":
            self.logout_api()
        elif parsed.path == "/api/auth/password":
            self.change_password_api()
        elif parsed.path == "/api/auth/users":
            self.create_user_api()
        elif parsed.path == "/api/preferences":
            self.save_preferences_api()
        elif parsed.path == "/api/signal-reviews/evaluate":
            self.evaluate_signal_reviews_api()
        else:
            self.send_json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def require_same_origin_post(self):
        if not auth_enabled() or not AUTH_REQUIRE_SAME_ORIGIN_POST:
            return True
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        source = origin or referer
        peer_ip = self.client_address[0] if self.client_address else ""
        if same_origin_allowed_for_peer(source, self.headers.get("Host", ""), self.headers.get("X-Forwarded-Host", ""), peer_ip):
            return True
        LOG.warning(
            "blocked cross-origin POST; path=%s; origin=%s; referer=%s; host=%s; client_ip=%s",
            self.path,
            origin,
            referer,
            self.headers.get("Host", ""),
            self.client_ip(),
        )
        self.send_json(403, {"error": "same-origin POST required"})
        return False

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        peer_ip = self.client_address[0] if self.client_address else ""
        if forwarded and is_trusted_proxy_peer(peer_ip):
            return forwarded.split(",", 1)[0].strip()
        return peer_ip

    def auth_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return ""
        item = jar.get(AUTH_COOKIE_NAME)
        return item.value if item else ""

    def current_user(self):
        if self._current_user_loaded:
            return self._current_user_cache
        if not auth_enabled():
            user = {"id": 0, "username": "local", "role": "admin"}
        elif storage is None:
            user = None
        else:
            ensure_auth_ready()
            user = storage.user_for_session(self.auth_token(), AUTH_SESSION_TOUCH_INTERVAL_SECONDS)
        self._current_user_cache = user
        self._current_user_loaded = True
        return user

    def request_storage(self):
        return storage_for_user(self.current_user())

    def require_auth(self, path):
        if is_public_path(path):
            return True
        user = self.current_user()
        if user:
            return True
        if path.startswith("/api/"):
            self.send_json(401, {"authenticated": False, "error": "login required"})
        else:
            self.send_response(302)
            self.send_header("Location", "/login?next=" + quote(self.path or "/", safe=""))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        return False

    def serve_login(self, next_path="/binance-futures-dashboard.html", error=""):
        next_path = safe_next_path(next_path)
        if self.current_user():
            self.send_response(302)
            self.send_header("Location", next_path)
            self.end_headers()
            return
        body = login_page(next_path, error).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def login_api(self):
        try:
            if not auth_enabled():
                self.send_json(200, {"authenticated": True, "user": {"username": "local"}})
                return
            if storage is None or not ensure_auth_ready():
                self.send_json(503, {"authenticated": False, "error": "auth database is not ready"})
                return
            body = self.read_json_body()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            client_ip = self.client_ip()
            if login_locked(client_ip, username):
                self.send_json(429, {"authenticated": False, "error": "登录失败过多，请稍后再试"})
                return
            user = storage.verify_auth_user(username, password)
            if not user:
                record_login_failure(client_ip, username)
                self.send_json(401, {"authenticated": False, "error": "账号或密码错误"})
                return
            clear_login_failures(client_ip, username)
            session = storage.create_auth_session(
                user["id"],
                self.headers.get("User-Agent", ""),
                client_ip,
                AUTH_SESSION_TTL_SECONDS,
            )
            if not session:
                self.send_json(503, {"authenticated": False, "error": "session create failed"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            cookie = (
                f"{AUTH_COOKIE_NAME}={session['token']}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={AUTH_SESSION_TTL_SECONDS}"
            )
            if AUTH_COOKIE_SECURE:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps({"authenticated": True, "user": user}, ensure_ascii=False).encode("utf-8"))
        except BadRequestError as exc:
            self.send_json(400, {"authenticated": False, "error": str(exc)})
        except Exception as exc:
            LOG.exception("login failed")
            self.send_json(500, {"authenticated": False, "error": INTERNAL_ERROR_MESSAGE})

    def logout_api(self):
        token = self.auth_token()
        if storage is not None:
            storage.delete_auth_session(token)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", f"{AUTH_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b'{"authenticated":false}')

    def change_password_api(self):
        try:
            if not auth_enabled():
                self.send_json(200, {"changed": True})
                return
            if storage is None or not ensure_auth_ready():
                self.send_json(503, {"changed": False, "error": "auth database is not ready"})
                return
            token = self.auth_token()
            user = self.current_user()
            if not user:
                self.send_json(401, {"changed": False, "error": "login required"})
                return
            body = self.read_json_body()
            current_password = str(body.get("current_password", ""))
            new_password = str(body.get("new_password", ""))
            confirm_password = str(body.get("confirm_password", ""))
            if not current_password or not new_password:
                self.send_json(400, {"changed": False, "error": "current and new password are required"})
                return
            if confirm_password and confirm_password != new_password:
                self.send_json(400, {"changed": False, "error": "new password confirmation does not match"})
                return
            if len(new_password) < 8:
                self.send_json(400, {"changed": False, "error": "new password must be at least 8 characters"})
                return
            if len(new_password) > 128:
                self.send_json(400, {"changed": False, "error": "new password is too long"})
                return
            if new_password == current_password:
                self.send_json(400, {"changed": False, "error": "new password must be different"})
                return
            ok, error = storage.change_auth_password(user["id"], current_password, new_password, keep_token=token)
            if not ok:
                self.send_json(401, {"changed": False, "error": error or "password change failed"})
                return
            LOG.warning("auth password changed; user=%s; client_ip=%s", user.get("username"), self.client_ip())
            self.send_json(200, {"changed": True, "user": {"username": user.get("username"), "role": user.get("role")}})
        except BadRequestError as exc:
            self.send_json(400, {"changed": False, "error": str(exc)})
        except Exception as exc:
            LOG.exception("password change failed")
            self.send_json(500, {"changed": False, "error": INTERNAL_ERROR_MESSAGE})

    def create_user_api(self):
        try:
            if not auth_enabled():
                self.send_json(200, {"created": True, "user": {"username": "local", "role": "admin"}})
                return
            if storage is None or not ensure_auth_ready():
                self.send_json(503, {"created": False, "error": "auth database is not ready"})
                return
            current = self.current_user()
            if not current:
                self.send_json(401, {"created": False, "error": "login required"})
                return
            if current.get("role") != "admin":
                self.send_json(403, {"created": False, "error": "admin role required"})
                return
            body = self.read_json_body()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            role = "admin" if str(body.get("role", "user")).strip().lower() == "admin" else "user"
            if not valid_auth_username(username):
                self.send_json(400, {
                    "created": False,
                    "error": "username must be 3-64 characters: letters, numbers, dot, dash, underscore or @",
                })
                return
            if len(password) < 8:
                self.send_json(400, {"created": False, "error": "password must be at least 8 characters"})
                return
            if len(password) > 128:
                self.send_json(400, {"created": False, "error": "password is too long"})
                return
            user, error = storage.create_auth_user(username, password, role)
            if not user:
                status = 409 if error == "username already exists" else 400
                self.send_json(status, {"created": False, "error": error or "create user failed"})
                return
            LOG.warning(
                "auth user created; username=%s; role=%s; operator=%s; client_ip=%s",
                user.get("username"),
                user.get("role"),
                current.get("username"),
                self.client_ip(),
            )
            self.send_json(200, {"created": True, "user": user})
        except BadRequestError as exc:
            self.send_json(400, {"created": False, "error": str(exc)})
        except Exception as exc:
            LOG.exception("create auth user failed")
            self.send_json(500, {"created": False, "error": INTERNAL_ERROR_MESSAGE})

    def serve_api(self, query):
        try:
            symbols = parse_symbols(query, reject_overflow=True)
        except BadRequestError as exc:
            self.send_json(400, {"error": str(exc), "error_type": "bad_request"})
            return
        user_storage = self.request_storage()
        key = cache_key(symbols)
        now = time.time()
        prune_memory_cache(now)
        cached = None
        with _payload_lock:
            entry = _last_payloads.get(key)
            if entry and now - entry["ts"] <= CACHE_TTL_SECONDS:
                cached = dict(entry["payload"])
                cached["cache_hit"] = True
        if cached is not None:
            self.send_cached_payload(cached, user_storage)
            return

        if storage is not None:
            redis_cached = storage.get_market_payload(key)
            redis_age = payload_age_seconds(redis_cached, now)
            if redis_cached and payload_matches(redis_cached, symbols) and redis_age is not None and redis_age <= CACHE_TTL_SECONDS:
                cached = dict(redis_cached)
                cached["cache_hit"] = True
                cached["redis_hit"] = True
                with _payload_lock:
                    _last_payloads[key] = {"ts": now - redis_age, "payload": redis_cached}
                self.send_cached_payload(cached, user_storage)
                return

        disk_cached, disk_age = fresh_disk_cache(symbols, now)
        if disk_cached:
            cached = dict(disk_cached)
            cached["cache_hit"] = True
            cached["disk_hit"] = True
            with _payload_lock:
                _last_payloads[key] = {"ts": now - float(disk_age or 0.0), "payload": disk_cached}
            self.send_cached_payload(cached, user_storage)
            return

        lock = market_lock_for(key)
        cached = None
        try:
            with lock:
                now = time.time()
                with _payload_lock:
                    entry = _last_payloads.get(key)
                    if entry and now - entry["ts"] <= CACHE_TTL_SECONDS:
                        cached = dict(entry["payload"])
                        cached["cache_hit"] = True
                if cached is None:
                    disk_cached, disk_age = fresh_disk_cache(symbols, now)
                    if disk_cached:
                        cached = dict(disk_cached)
                        cached["cache_hit"] = True
                        cached["disk_hit"] = True
                        with _payload_lock:
                            _last_payloads[key] = {"ts": now - float(disk_age or 0.0), "payload": disk_cached}
                if cached is None:
                    self.run_api_uncached(symbols, now, user_storage)
                    return
        finally:
            release_market_lock(key, lock)
        self.send_cached_payload(cached, user_storage)

    def send_cached_payload(self, cached, user_storage):
        review_result = capture_signal_reviews_for_user(user_storage, cached)
        if review_result is not None:
            cached["signal_review"] = review_result
        self.send_json(200, cached)

    def run_api_uncached(self, symbols, now, user_storage=None):
        try:
            analysis_started_at = time.time()
            result = run_bian_json(symbols)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            if result.returncode != 0:
                self.send_cached_or_error("bian.py failed", stderr[-3000:], symbols)
                return
            if not stdout.strip():
                self.send_cached_or_error("bian.py returned empty stdout", stderr[-3000:], symbols)
                return
            data = json.loads(stdout)
            if not isinstance(data, list):
                self.send_cached_or_error("bian.py returned malformed market data", stderr[-3000:], symbols)
                return
            returned_symbols = symbols_from_market_data(data)
            missing_symbols = [symbol for symbol in symbols if symbol not in returned_symbols]
            if missing_symbols:
                self.send_cached_or_error(
                    "bian.py returned partial market data; missing symbols: " + ",".join(missing_symbols),
                    stderr[-3000:],
                    symbols,
                    {
                        "missing_symbols": missing_symbols,
                        "returned_symbols": sorted(returned_symbols),
                    },
                )
                return
            analysis_completed_at = time.time()
            try:
                publication_prices = fetch_publication_prices(symbols)
            except Exception as exc:
                publication_prices = {}
                LOG.warning("publication price refresh failed; symbols=%s; error=%s", symbols, exc)
            for report in data:
                if not isinstance(report, dict):
                    continue
                report["analysis_price"] = float_or_zero(report.get("last"))
                price = publication_prices.get(normalize_symbol(report.get("symbol")))
                if price:
                    report["publication_price"] = price["price"]
                    report["publication_bid"] = price["bid"]
                    report["publication_ask"] = price["ask"]
                    report["publication_price_observed_at_ms"] = price["observed_at_ms"]
                    report["publication_price_source"] = price["source"]
            published_at = time.time()
            generated_at = now_bj(published_at)
            payload = {
                "generated_at": generated_at,
                "published_at_ms": int(published_at * 1000),
                "analysis_started_at": now_bj(analysis_started_at),
                "analysis_completed_at": now_bj(analysis_completed_at),
                "analysis_duration_ms": max(0, round((analysis_completed_at - analysis_started_at) * 1000)),
                "publication_delay_ms": max(0, round((published_at - analysis_completed_at) * 1000)),
                "symbols": symbols,
                "data": data,
                "stale": False,
                "cache_hit": False,
                "warning": None,
            }
            with _payload_lock:
                _last_payloads[cache_key(symbols)] = {"ts": published_at, "payload": payload}
            save_cache(symbols, payload)
            response_payload = dict(payload)
            if storage is not None:
                storage.set_market_payload(cache_key(symbols), payload, CACHE_TTL_SECONDS)
                if user_storage is not None:
                    try:
                        user_storage.save_strategy_snapshot(symbols, payload)
                    except Exception:
                        LOG.exception("strategy snapshot save failed; symbols=%s", symbols)
                try:
                    review_result = capture_signal_reviews_for_user(user_storage, response_payload)
                    if review_result is not None:
                        response_payload["signal_review"] = review_result
                except Exception:
                    LOG.exception("signal review capture failed; symbols=%s", symbols)
            self.send_json(200, response_payload)
        except subprocess.TimeoutExpired:
            LOG.error("market api analyzer timeout; symbols=%s; timeout=%ss", symbols, RUN_TIMEOUT_SECONDS)
            self.send_cached_or_error(f"bian.py timeout ({RUN_TIMEOUT_SECONDS}s)", "", symbols)
        except Exception as exc:
            LOG.exception("market api uncached analysis failed; symbols=%s", symbols)
            self.send_cached_or_error(str(exc), "", symbols)

    def serve_realtime_prices(self, query):
        try:
            symbols = parse_symbols(query, reject_overflow=True)
        except BadRequestError as exc:
            self.send_json(400, {"error": str(exc), "error_type": "bad_request"})
            return
        hub, ensure_symbols, sharing = realtime_hub_for_request(symbols)
        record_realtime_sharing(sharing)
        try:
            hub.ensure(ensure_symbols)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        except Exception:
            hub.release()
            raise

        last_payload = None
        last_emit = 0
        next_maintenance = 0
        started = time.time()
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while time.time() - started < SSE_MAX_SECONDS:
                now = time.time()
                if now >= next_maintenance:
                    hub.maintain(ensure_symbols)
                    next_maintenance = now + 5
                snap = hub.snapshot(symbols)
                snap["hub_key"] = hub.key
                snap["sharing"] = sharing
                payload = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
                if payload != last_payload or now - last_emit >= SSE_HEARTBEAT_SECONDS:
                    self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    last_payload = payload
                    last_emit = now
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            hub.release()

    def serve_preferences(self):
        user_storage = self.request_storage()
        fallback_status = {"mysql": {"configured": False, "available": False}, "redis": {"configured": False, "available": False}}
        try:
            if user_storage is not None and getattr(user_storage, "mysql_configured", False):
                if not user_storage.mysql_available():
                    self.send_json(503, {
                        "error": "preference storage is unavailable",
                        "storage": user_storage.status() if hasattr(user_storage, "status") else fallback_status,
                    })
                    return
            if user_storage is not None:
                prefs, revision = user_storage.load_preferences_with_revision()
                storage_status = user_storage.status() if hasattr(user_storage, "status") else fallback_status
            else:
                prefs, revision = {}, 0
                storage_status = fallback_status
            self.send_json(200, {
                "preferences": prefs,
                "revision": revision,
                "storage": storage_status,
            })
        except Exception:
            LOG.exception("load preferences failed")
            self.send_json(503, {"error": "preference storage is unavailable", "storage": fallback_status})

    def save_preferences_api(self):
        try:
            body = self.read_json_body()
            if not isinstance(body, dict):
                self.send_json(400, {"saved": False, "error": "request body must be a JSON object"})
                return
            if "patches" in body:
                raw_patches = body.get("patches")
                if not isinstance(raw_patches, list) or not raw_patches or len(raw_patches) > 8:
                    self.send_json(400, {"saved": False, "error": "patches must contain between 1 and 8 items"})
                    return
                patches = []
                previous_revision = 0
                for entry in raw_patches:
                    prefs = entry.get("preferences") if isinstance(entry, dict) else None
                    raw_revision = entry.get("revision") if isinstance(entry, dict) else None
                    if not isinstance(prefs, dict) or not prefs:
                        self.send_json(400, {"saved": False, "error": "each patch must contain preferences"})
                        return
                    try:
                        if isinstance(raw_revision, bool):
                            raise ValueError
                        revision = int(raw_revision)
                        if revision <= 0 or (isinstance(raw_revision, float) and raw_revision != revision):
                            raise ValueError
                    except (TypeError, ValueError):
                        self.send_json(400, {"saved": False, "error": "revision must be a positive integer"})
                        return
                    if revision <= previous_revision:
                        self.send_json(400, {"saved": False, "error": "batch revisions must be strictly increasing"})
                        return
                    patches.append({"preferences": prefs, "revision": revision})
                    previous_revision = revision
                user_storage = self.request_storage()
                result = user_storage.save_preference_batch(patches) if user_storage is not None else False
                response_revision = previous_revision
            else:
                prefs = body.get("preferences")
                if not isinstance(prefs, dict):
                    self.send_json(400, {"saved": False, "error": "preferences must be a JSON object"})
                    return
                raw_revision = body.get("revision")
                try:
                    if isinstance(raw_revision, bool):
                        raise ValueError
                    revision = int(raw_revision)
                    if revision <= 0 or (isinstance(raw_revision, float) and raw_revision != revision):
                        raise ValueError
                except (TypeError, ValueError):
                    self.send_json(400, {"saved": False, "error": "revision must be a positive integer"})
                    return
                user_storage = self.request_storage()
                result = user_storage.save_preferences(prefs, revision=revision) if user_storage is not None else False
                response_revision = revision
            saved = bool(result.get("saved")) if isinstance(result, dict) else bool(result)
            self.send_json(200, {
                "saved": saved,
                "applied": bool(result.get("applied", saved)) if isinstance(result, dict) else saved,
                "revision": int(result.get("revision") or response_revision or 0) if isinstance(result, dict) else int(response_revision or 0),
                "storage": user_storage.status() if user_storage is not None else {"mysql": {"configured": False}, "redis": {"configured": False}},
            })
        except ValueError as exc:
            self.send_json(400, {"saved": False, "error": str(exc)})
        except BadRequestError as exc:
            self.send_json(400, {"saved": False, "error": str(exc)})
        except Exception as exc:
            LOG.exception("save preferences failed")
            self.send_json(500, {"saved": False, "error": INTERNAL_ERROR_MESSAGE})

    def serve_storage_status(self):
        self.send_json(200, storage.status() if storage is not None else {"mysql": {"configured": False}, "redis": {"configured": False}})

    def serve_diagnostics(self):
        user = self.current_user()
        if auth_enabled() and not is_admin_user(user):
            self.send_json(403, {"error": "admin role required"})
            return
        self.send_json(200, diagnostics_payload(check_storage=True))

    def evaluate_signal_reviews_api(self):
        try:
            user = self.current_user()
            if auth_enabled() and not is_admin_user(user):
                self.send_json(403, {"error": "admin role required"})
                return
            trigger_state = trigger_signal_review_evaluation(force=True)
            eval_state = {
                "scheduled": bool(trigger_state.get("scheduled")),
                "reason": trigger_state.get("reason"),
                "status": signal_review_eval_status(),
            }
            status_code = 202 if eval_state["scheduled"] else 200
            if trigger_state.get("reason") == "storage_unavailable":
                status_code = 503
            self.send_json(status_code, {"evaluation": eval_state})
        except Exception as exc:
            LOG.exception("signal review manual evaluation trigger failed")
            self.send_json(500, {"error": INTERNAL_ERROR_MESSAGE, "evaluation": {"scheduled": False, "reason": "error"}})

    def serve_signal_reviews(self, query):
        try:
            symbols = parse_symbol_filter(query)
            limit = parse_signal_review_limit(query)
            trigger_state = trigger_signal_review_evaluation()
            eval_state = {
                "scheduled": bool(trigger_state.get("scheduled")),
                "reason": trigger_state.get("reason"),
                "status": signal_review_eval_status(),
            }
            user_storage = self.request_storage()
            records = user_storage.load_signal_reviews(symbols or None, limit=limit) if user_storage is not None else []
            compact = [compact_signal_review_record(item) for item in records]
            self.send_json(200, {
                "symbols": symbols,
                "records": compact,
                "stats": build_signal_review_stats(compact),
                "evaluation": eval_state,
                "storage": storage.status() if storage is not None else {"mysql": {"configured": False}, "redis": {"configured": False}},
            })
        except BadRequestError as exc:
            self.send_json(400, {"error": str(exc), "records": [], "stats": build_signal_review_stats([])})
        except Exception as exc:
            LOG.exception("signal review api failed")
            self.send_json(500, {"error": INTERNAL_ERROR_MESSAGE, "records": [], "stats": build_signal_review_stats([])})

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except (TypeError, ValueError):
            raise BadRequestError("invalid Content-Length")
        if length <= 0:
            return {}
        if length > 1024 * 1024:
            raise BadRequestError("request body too large")
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            raise BadRequestError("request body must be UTF-8")
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            raise BadRequestError("invalid JSON body")

    def send_cached_or_error(self, error, stderr, symbols, extra=None):
        extra = extra or {}
        error_type, status, allow_stale, user_message = classify_api_error(error, stderr)
        cached = load_cache(symbols) if allow_stale else None
        LOG.error(
            "market api error; symbols=%s; error_type=%s; status=%s; stale_allowed=%s; stale_used=%s; detail=%s; stderr=%s",
            symbols,
            error_type,
            status,
            allow_stale,
            bool(cached and cached.get("data")),
            error,
            (stderr or "")[-500:],
        )
        if cached and cached.get("data"):
            payload = dict(cached)
            payload["stale"] = True
            payload["cache_hit"] = True
            payload["warning"] = user_message
            payload["error_type"] = error_type
            payload.update(extra)
            if EXPOSE_ERROR_DETAILS and stderr:
                payload["stderr"] = stderr
            self.send_json(200, payload)
            return
        body = {
            "error": user_message,
            "symbols": symbols,
            "stale": False,
            "error_type": error_type,
        }
        if EXPOSE_ERROR_DETAILS:
            body["detail"] = error
            body["stderr"] = stderr
        body.update(extra)
        self.send_json(status, body)

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


class ThreadingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def main():
    with ThreadingServer((HOST, PORT), Handler) as httpd:
        print("=" * 52)
        print("  Binance futures dashboard server started")
        print("  Bind: %s:%d" % (HOST, PORT))
        print("  Page: http://%s:%d/binance-futures-dashboard.html" % (HOST, PORT))
        print("  API : http://%s:%d/api/market" % (HOST, PORT))
        print("  Web root: %s" % WEB_ROOT)
        print("  Cache TTL: %ds, fallback file: %s" % (CACHE_TTL_SECONDS, CACHE_FILE))
        print("  Press Ctrl+C to stop")
        print("=" * 52)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()



