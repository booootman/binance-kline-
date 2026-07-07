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
from http import cookies
import html
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
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
            LOG.exception("realtime direct websocket loop crashed; symbols=%s", symbols)

    async def direct_loop(self, symbols, stop_event):
        parts = []
        for symbol in symbols:
            lower = symbol.lower()
            parts.append(f"{lower}@bookTicker")
            parts.append(f"{lower}@depth20@500ms")
        streams = "/".join(parts)
        url = f"wss://fstream.binance.com/stream?streams={streams}"
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
                LOG.error(
                    "realtime websocket disconnected; reconnect_in=3s; symbols=%s; error=%s",
                    symbols,
                    exc,
                    exc_info=True,
                )
                await asyncio.sleep(3)

    def handle_message(self, msg):
        try:
            data = msg.get("data", msg) if isinstance(msg, dict) else {}
            symbol = normalize_symbol(data.get("s"))
            if not symbol:
                return
            event_ms = int(data.get("T") or data.get("E") or int(time.time() * 1000))
            received_ms = int(time.time() * 1000)
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
                    item = dict(self.latest.get(symbol, {}))
                    item.update(depth_update)
                    item.setdefault("price", mid)
                    item["source"] = "futures_bookTicker+depth20" if item.get("price") else "futures_depth20"
                    self.latest[symbol] = item
                    self.error = None
                if storage is not None:
                    storage.set_realtime_price(symbol, item)
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
                "source": "futures_bookTicker",
            }
            with self.lock:
                old = dict(self.latest.get(symbol, {}))
                old.update(item)
                if old.get("depth_ok"):
                    old["source"] = "futures_bookTicker+depth20"
                self.latest[symbol] = old
                self.error = None
            if storage is not None:
                storage.set_realtime_price(symbol, old)
        except Exception as exc:
            with self.lock:
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


_auth_bootstrap_done = False
_auth_bootstrap_lock = threading.RLock()
_login_failures = {}
_login_failures_lock = threading.RLock()


def auth_enabled():
    return AUTH_ENABLED


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


def login_page(next_path="", error=""):
    next_url = next_path if str(next_path or "").startswith("/") else "/binance-futures-dashboard.html"
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
        if item.get("until", 0) <= now:
            _login_failures.pop(key, None)
            return False
        return True


def record_login_failure(client_ip, username):
    key = login_failure_key(client_ip, username)
    now = time.time()
    with _login_failures_lock:
        item = _login_failures.get(key, {"count": 0, "until": 0})
        count = int(item.get("count", 0)) + 1
        until = now + AUTH_LOCKOUT_SECONDS if count >= AUTH_MAX_FAILURES else 0
        _login_failures[key] = {"count": count, "until": until}


def clear_login_failures(client_ip, username):
    with _login_failures_lock:
        _login_failures.pop(login_failure_key(client_ip, username), None)


def valid_auth_username(username):
    text = str(username or "").strip()
    if len(text) < 3 or len(text) > 64:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-@")
    return all(ch in allowed for ch in text)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_ROOT, **kw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json(200, {"ok": True})
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
        elif parsed.path == "/api/auth/me":
            user = self.current_user()
            self.send_json(200, {"authenticated": bool(user), "user": user})
        elif parsed.path == "/api/storage-status":
            self.serve_storage_status()
        elif parsed.path in ("", "/", "/index.html"):
            self.path = "/binance-futures-dashboard.html"
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
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
        else:
            self.send_json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return self.client_address[0] if self.client_address else ""

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
        if not auth_enabled():
            return {"id": 0, "username": "local", "role": "admin"}
        if storage is None:
            return None
        ensure_auth_ready()
        return storage.user_for_session(self.auth_token())

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
        if self.current_user():
            self.send_response(302)
            self.send_header("Location", next_path or "/binance-futures-dashboard.html")
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
        except Exception as exc:
            LOG.exception("login failed")
            self.send_json(500, {"authenticated": False, "error": str(exc)})

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
            ok, error = storage.change_auth_password(user["id"], current_password, new_password)
            if not ok:
                self.send_json(401, {"changed": False, "error": error or "password change failed"})
                return
            storage.delete_other_auth_sessions(user["id"], token)
            LOG.warning("auth password changed; user=%s; client_ip=%s", user.get("username"), self.client_ip())
            self.send_json(200, {"changed": True, "user": {"username": user.get("username"), "role": user.get("role")}})
        except Exception as exc:
            LOG.exception("password change failed")
            self.send_json(500, {"changed": False, "error": str(exc)})

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
        except Exception as exc:
            LOG.exception("create auth user failed")
            self.send_json(500, {"created": False, "error": str(exc)})

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

        if storage is not None:
            redis_cached = storage.get_market_payload(key)
            if redis_cached and payload_matches(redis_cached, symbols):
                cached = dict(redis_cached)
                cached["cache_hit"] = True
                cached["redis_hit"] = True
                with _payload_lock:
                    _last_payloads[key] = {"ts": now, "payload": redis_cached}
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
            if storage is not None:
                storage.set_market_payload(cache_key(symbols), payload, CACHE_TTL_SECONDS)
                storage.save_strategy_snapshot(symbols, payload)
            self.send_json(200, payload)
        except subprocess.TimeoutExpired:
            LOG.error("market api analyzer timeout; symbols=%s; timeout=%ss", symbols, RUN_TIMEOUT_SECONDS)
            self.send_cached_or_error(f"bian.py timeout ({RUN_TIMEOUT_SECONDS}s)", "", symbols)
        except Exception as exc:
            LOG.exception("market api uncached analysis failed; symbols=%s", symbols)
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

    def serve_preferences(self):
        prefs = storage.load_preferences() if storage is not None else {}
        self.send_json(200, {
            "preferences": prefs,
            "storage": storage.status() if storage is not None else {"mysql": {"configured": False}, "redis": {"configured": False}},
        })

    def save_preferences_api(self):
        try:
            body = self.read_json_body()
            prefs = body.get("preferences") if isinstance(body, dict) and isinstance(body.get("preferences"), dict) else body
            if not isinstance(prefs, dict):
                self.send_json(400, {"saved": False, "error": "preferences must be a JSON object"})
                return
            saved = storage.save_preferences(prefs) if storage is not None else False
            self.send_json(200, {
                "saved": bool(saved),
                "storage": storage.status() if storage is not None else {"mysql": {"configured": False}, "redis": {"configured": False}},
            })
        except Exception as exc:
            LOG.exception("save preferences failed")
            self.send_json(500, {"saved": False, "error": str(exc)})

    def serve_storage_status(self):
        self.send_json(200, storage.status() if storage is not None else {"mysql": {"configured": False}, "redis": {"configured": False}})

    def read_json_body(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        if length > 1024 * 1024:
            raise ValueError("request body too large")
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

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
            if stderr:
                payload["stderr"] = stderr
            self.send_json(200, payload)
            return
        body = {
            "error": user_message,
            "detail": error,
            "stderr": stderr,
            "symbols": symbols,
            "stale": False,
            "error_type": error_type,
        }
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



