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
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

try:
    from binance import ThreadedWebsocketManager
except Exception:
    ThreadedWebsocketManager = None

try:
    import websockets
except Exception:
    websockets = None

PORT = 8000
PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(PACKAGE_ROOT))
WEB_ROOT = os.path.join(ROOT, "web")
BIAN = os.path.join(PACKAGE_ROOT, "analyzer.py")
CACHE_FILE = os.path.join(ROOT, "runtime", "market_cache.json")
BJ_TZ = timezone(timedelta(hours=8))
CACHE_TTL_SECONDS = 30
RUN_TIMEOUT_SECONDS = 120
DEFAULT_SYMBOLS = ["DOGEUSDT", "TLMUSDT"]
MAX_SYMBOLS = 8
SSE_MAX_SECONDS = 30 * 60
SSE_HEARTBEAT_SECONDS = 15
REALTIME_IDLE_SECONDS = 30
MEMORY_CACHE_MAX_ITEMS = 64

_last_payloads = {}
_payload_lock = threading.RLock()
_cache_lock = threading.RLock()
_market_locks = {}
_market_locks_guard = threading.RLock()
_run_semaphore = threading.Semaphore(2)
_realtime_hubs = {}
_realtime_hubs_lock = threading.RLock()


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
        self.direct_connected = False
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
        with self.lock:
            if self.client_count:
                return
            self.stop_locked()
        with _realtime_hubs_lock:
            if _realtime_hubs.get(self.key) is self:
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
            if websockets is not None:
                return self.start_direct_locked(symbols)
            if ThreadedWebsocketManager is None:
                self.error = "python-binance is not installed"
                return False
            try:
                streams = [f"{symbol.lower()}@bookTicker" for symbol in symbols]
                self.twm = ThreadedWebsocketManager()
                self.twm.start()
                self.socket_key = self.twm.start_futures_multiplex_socket(
                    callback=self.handle_message,
                    streams=streams,
                )
                return True
            except Exception as exc:
                self.error = str(exc)
                self.stop_locked()
                return self.start_direct_locked(symbols)

    def stop_locked(self):
        old_thread = self.direct_thread
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
        if websockets is None:
            self.error = (self.error or "") + "; websockets is not installed"
            return False
        self.direct_stop = threading.Event()
        self.direct_thread = threading.Thread(
            target=self.run_direct,
            args=(list(symbols), self.direct_stop),
            daemon=True,
        )
        self.direct_thread.start()
        return True

    def run_direct(self, symbols, stop_event):
        try:
            asyncio.run(self.direct_loop(symbols, stop_event))
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.direct_connected = False

    async def direct_loop(self, symbols, stop_event):
        streams = "/".join(f"{symbol.lower()}@bookTicker" for symbol in symbols)
        url = f"wss://fstream.binance.com/stream?streams={streams}"
        while not stop_event.is_set():
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    open_timeout=10,
                    close_timeout=3,
                ) as ws:
                    with self.lock:
                        self.error = None
                        self.direct_connected = True
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                        self.handle_message(json.loads(raw))
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                    self.direct_connected = False
                if stop_event.is_set():
                    break
                await asyncio.sleep(3)

    def handle_message(self, msg):
        try:
            data = msg.get("data", msg) if isinstance(msg, dict) else {}
            symbol = normalize_symbol(data.get("s"))
            price = data.get("p") or data.get("c")
            bid = data.get("b")
            ask = data.get("a")
            if price is None and bid is not None and ask is not None:
                price = (float(bid) + float(ask)) / 2
            if not symbol or price is None:
                return
            event_ms = int(data.get("T") or data.get("E") or int(time.time() * 1000))
            item = {
                "symbol": symbol,
                "price": float(price),
                "bid": float(bid) if bid is not None else None,
                "ask": float(ask) if ask is not None else None,
                "event_ms": event_ms,
                "received_ms": int(time.time() * 1000),
                "source": "futures_bookTicker",
            }
            with self.lock:
                self.latest[symbol] = item
                self.error = None
        except Exception as exc:
            with self.lock:
                self.error = str(exc)

    def snapshot(self, symbols):
        with self.lock:
            return {
                "prices": [self.latest[s] for s in symbols if s in self.latest],
                "symbols": symbols,
                "error": self.error,
                "connected": bool((self.twm and self.socket_key) or self.direct_connected),
            }


def now_bj() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def normalize_symbol(raw):
    symbol = "".join(ch for ch in str(raw or "").upper().strip() if ch.isalnum())
    if symbol and len(symbol) <= 8 and "USDT" not in symbol:
        symbol += "USDT"
    return symbol


def parse_symbols(query):
    params = parse_qs(query)
    raw_parts = []
    for item in params.get("symbol", []):
        raw_parts.append(item)
    for item in params.get("symbols", []):
        raw_parts.extend(item.split(","))
    symbols = []
    seen = set()
    for raw in raw_parts:
        symbol = normalize_symbol(raw)
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
        if len(symbols) >= MAX_SYMBOLS:
            break
    return symbols or list(DEFAULT_SYMBOLS)


def cache_key(symbols):
    return ",".join(symbols)


def market_lock_for(key):
    with _market_locks_guard:
        lock = _market_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _market_locks[key] = lock
        return lock


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
            if key not in active_keys:
                _market_locks.pop(key, None)


def realtime_hub_for(symbols):
    key = cache_key(symbols)
    with _realtime_hubs_lock:
        hub = _realtime_hubs.get(key)
        if hub is None:
            hub = RealtimePriceHub(key)
            _realtime_hubs[key] = hub
        return hub


def payload_matches(payload, symbols):
    if not isinstance(payload, dict):
        return False
    if payload.get("symbols") == symbols:
        return True
    data = payload.get("data")
    if not isinstance(data, list):
        return False
    return [item.get("symbol") for item in data if isinstance(item, dict)] == symbols


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
        cache.setdefault("payloads", {})[key] = payload
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
            os.replace(tmp, CACHE_FILE)
        except Exception:
            pass


def run_bian_json(symbols):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    with _run_semaphore:
        return subprocess.run(
            [sys.executable, "-B", BIAN, "--symbols", ",".join(symbols), "--json"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=RUN_TIMEOUT_SECONDS,
        )


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_ROOT, **kw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/market":
            self.serve_api(parsed.query)
        elif parsed.path == "/api/realtime-prices":
            self.serve_realtime_prices(parsed.query)
        elif parsed.path in ("", "/", "/index.html"):
            self.path = "/binance-futures-dashboard.html"
            super().do_GET()
        else:
            super().do_GET()

    def serve_api(self, query):
        symbols = parse_symbols(query)
        key = cache_key(symbols)
        now = time.time()
        prune_memory_cache(now)
        with _payload_lock:
            entry = _last_payloads.get(key)
            if entry and now - entry["ts"] <= CACHE_TTL_SECONDS:
                cached = dict(entry["payload"])
                cached["cache_hit"] = True
                self.send_json(200, cached)
                return

        lock = market_lock_for(key)
        with lock:
            now = time.time()
            with _payload_lock:
                entry = _last_payloads.get(key)
                if entry and now - entry["ts"] <= CACHE_TTL_SECONDS:
                    cached = dict(entry["payload"])
                    cached["cache_hit"] = True
                    self.send_json(200, cached)
                    return
            self.run_api_uncached(symbols, now)

    def run_api_uncached(self, symbols, now):
        try:
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
            payload = {
                "generated_at": now_bj(),
                "symbols": symbols,
                "data": data,
                "stale": False,
                "cache_hit": False,
                "warning": None,
            }
            with _payload_lock:
                _last_payloads[cache_key(symbols)] = {"ts": now, "payload": payload}
            save_cache(symbols, payload)
            self.send_json(200, payload)
        except subprocess.TimeoutExpired:
            self.send_cached_or_error(f"bian.py timeout ({RUN_TIMEOUT_SECONDS}s)", "", symbols)
        except Exception as exc:
            self.send_cached_or_error(str(exc), "", symbols)

    def serve_realtime_prices(self, query):
        symbols = parse_symbols(query)
        hub = realtime_hub_for(symbols)
        hub.acquire()
        hub.ensure(symbols)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        last_payload = None
        last_emit = 0
        started = time.time()
        try:
            while time.time() - started < SSE_MAX_SECONDS:
                snap = hub.snapshot(symbols)
                payload = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
                now = time.time()
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

    def send_cached_or_error(self, error, stderr, symbols):
        cached = load_cache(symbols)
        if cached and cached.get("data"):
            payload = dict(cached)
            payload["stale"] = True
            payload["cache_hit"] = True
            payload["warning"] = error
            if stderr:
                payload["stderr"] = stderr
            self.send_json(200, payload)
            return
        self.send_json(502, {"error": error, "stderr": stderr, "symbols": symbols, "stale": False})

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
    with ThreadingServer(("127.0.0.1", PORT), Handler) as httpd:
        print("=" * 52)
        print("  Binance futures dashboard server started")
        print("  Page: http://127.0.0.1:%d/binance-futures-dashboard.html" % PORT)
        print("  API : http://127.0.0.1:%d/api/market" % PORT)
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



