#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance USD-M Futures market helper.

This script fetches Binance futures candles, calculates common indicators, and
prints plain Chinese trading/grid guidance so you do not need to read raw RSI,
EMA, MACD, ATR, or volume data yourself.

Default symbols: DOGEUSDT and TLMUSDT.
Run:
  python bian.py
  python bian.py DOGEUSDT TLMUSDT
  python bian.py --symbols DOGEUSDT,TLMUSDT --json
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence

BASE_URL = "https://fapi.binance.com"
DEFAULT_SYMBOLS = ["DOGEUSDT", "TLMUSDT"]
INTERVALS = ["1m", "5m", "15m", "1h", "4h", "8h", "1d"]
INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "8h": 8 * 60 * 60,
    "1d": 24 * 60 * 60,
}
DEFAULT_TAKER_FEE_BPS = 5.0
DEFAULT_SLIPPAGE_BPS = 2.0
TAKER_FEE_BPS = DEFAULT_TAKER_FEE_BPS
SLIPPAGE_BPS = DEFAULT_SLIPPAGE_BPS
SIGNAL_SIDE_THRESHOLD = 5
BACKTEST_ATR_FILTER_PCT = 18.0
BACKTEST_VOLUME_FILTER_MIN = 0.55
BACKTEST_SAMPLE_FILTER_NOTE = "5m代理回测；样本经ATR/量能过滤，不等同于线上多周期执行策略"
BACKTEST_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "runtime", "backtest_cache.json")
BACKTEST_CACHE_TTL_SECONDS = 10 * 60
BACKTEST_MODEL_VERSION = 2
EXCHANGE_INFO_CACHE_TTL_SECONDS = int(os.environ.get("BIAN_EXCHANGE_INFO_CACHE_TTL_SECONDS", "3600"))
_EXCHANGE_INFO_CACHE: Dict[str, object] = {"ts": 0.0, "data": None}
_EXCHANGE_INFO_LOCK = threading.RLock()


@dataclass
class CandleIndicators:
    interval: str
    open_time_utc: str
    close_time_utc: str
    is_closed: bool
    candle_state: str
    progress_pct: float
    age_seconds: float
    open: float
    high: float
    low: float
    close: float
    ema20: float
    ema50: float
    ema20_closed: float
    ema50_closed: float
    rsi14: float
    rsi14_closed: float
    macd: float
    macd_signal: float
    macd_hist: float
    macd_hist_closed: float
    atr14: float
    atr14_pct: float
    vol_ratio_20: float
    recent_high_20: float
    recent_low_20: float
    boll_mid: float
    boll_upper: float
    boll_lower: float
    boll_position_pct: float
    boll_bandwidth_pct: float
    mt_pct_vs_ema20: float
    emt: str
    change_pct: float


@dataclass
class SymbolMeta:
    tick_size: float
    price_precision: int
    tick_size_verified: bool


@dataclass
class BacktestMetric:
    horizon: str
    sample_count: int
    hit_rate: float
    avg_max_profit_pct: float
    avg_max_drawdown_pct: float
    profit_drawdown_ratio: float
    expectancy_pct: float
    stopped_out_count: int
    stop_rate: float
    avg_loss_pct: float
    net_expectancy_pct: float
    estimated_cost_pct: float
    filtered_out_count: int
    sample_filter_note: str


@dataclass
class DirectionalBacktest:
    side: str
    sample_count: int
    quality_score: int
    grade: str
    windows: List[BacktestMetric]


@dataclass
class TriggerCheck:
    status: str
    label: str
    near_entry: bool
    distance_pct: float
    spread_pct: float
    spread_threshold_pct: float
    volume_ratio_1m: float
    depth_imbalance: float
    bid_depth_top5_usd: float
    ask_depth_top5_usd: float
    depth_ok: bool
    structure: str
    reasons: List[str]


@dataclass
class TimeframeAdvice:
    name: str
    horizon: str
    bias: str
    confidence: int
    raw_confidence: int
    direction_score: int
    execution_score: int
    confidence_note: str
    execution_note: str
    anchor_interval: str
    candle_state: str
    risk_gate: str
    action: str
    long_entry: float
    short_entry: float
    stop_hint: float
    backtest: DirectionalBacktest
    trigger_check: TriggerCheck
    risk_sizing: Dict[str, object]
    reasons: List[str]


@dataclass
class SymbolReport:
    symbol: str
    last: float
    price_observed_at_ms: int
    pct_24h: float
    high_24h: float
    low_24h: float
    quote_volume_24h: float
    funding_rate: float
    mark_price: float
    index_price: float
    next_funding_time_ms: int
    indicators: Dict[str, CandleIndicators]
    bias: str
    confidence: int
    summary: str
    long_grid: Dict[str, float]
    short_grid: Dict[str, float]
    risks: List[str]
    backtests: Dict[str, DirectionalBacktest]
    signal_quality: Dict[str, object]
    timeframe_advice: List[TimeframeAdvice]


def request_json(path: str, params: Dict[str, str | int | float]) -> object:
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{query}"
    last_error: Exception | None = None
    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in (418, 429):
                raise RuntimeError(f"Binance API HTTP {exc.code}: rate limited or banned: {body}") from exc
            last_error = RuntimeError(f"Binance API HTTP {exc.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(0.7 * attempt)
    raise RuntimeError(f"Network error while calling Binance after retries: {last_error}")


def fetch_exchange_info() -> Dict[str, object]:
    with _EXCHANGE_INFO_LOCK:
        now = time.time()
        cached = _EXCHANGE_INFO_CACHE.get("data")
        cached_ts = float(_EXCHANGE_INFO_CACHE.get("ts") or 0.0)
        if isinstance(cached, dict) and now - cached_ts <= EXCHANGE_INFO_CACHE_TTL_SECONDS:
            return cached
        try:
            fresh = request_json("/fapi/v1/exchangeInfo", {})
        except Exception:
            if isinstance(cached, dict):
                return cached
            raise
        if isinstance(fresh, dict):
            _EXCHANGE_INFO_CACHE["data"] = fresh
            _EXCHANGE_INFO_CACHE["ts"] = now
            return fresh
        return {}


def fetch_klines(symbol: str, interval: str, limit: int = 220) -> List[list]:
    data = request_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return list(data)


def fetch_book_ticker(symbol: str) -> Dict[str, float]:
    fallback = {
        "bid": 0.0,
        "ask": 0.0,
        "mid": 0.0,
        "spread_pct": 0.0,
        "depth_ok": False,
        "depth_imbalance": 0.0,
        "bid_depth_top5_usd": 0.0,
        "ask_depth_top5_usd": 0.0,
        "bid_depth_top20_usd": 0.0,
        "ask_depth_top20_usd": 0.0,
    }
    try:
        data = request_json("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        bid = float(data.get("bidPrice", 0.0))
        ask = float(data.get("askPrice", 0.0))
        mid = (bid + ask) / 2.0 if bid and ask else 0.0
        spread_pct = (ask - bid) / mid * 100.0 if mid else 0.0
        fallback.update({"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct})
    except Exception:
        return fallback

    try:
        depth = request_json("/fapi/v1/depth", {"symbol": symbol, "limit": 20})
        bids = [(float(price), float(qty)) for price, qty in depth.get("bids", [])[:20]]
        asks = [(float(price), float(qty)) for price, qty in depth.get("asks", [])[:20]]
        bid_top5 = sum(price * qty for price, qty in bids[:5])
        ask_top5 = sum(price * qty for price, qty in asks[:5])
        bid_top20 = sum(price * qty for price, qty in bids)
        ask_top20 = sum(price * qty for price, qty in asks)
        total = bid_top20 + ask_top20
        imbalance = (bid_top20 - ask_top20) / total if total else 0.0
        # top10 明细 ladder,供前端 DOM 小图渲染
        depth_ladder = {
            "bids": [[p, q, p * q] for p, q in bids[:10]],
            "asks": [[p, q, p * q] for p, q in asks[:10]],
        }
        fallback.update(
            {
                "depth_ok": bool(bid_top5 and ask_top5),
                "depth_imbalance": imbalance,
                "bid_depth_top5_usd": bid_top5,
                "ask_depth_top5_usd": ask_top5,
                "bid_depth_top20_usd": bid_top20,
                "ask_depth_top20_usd": ask_top20,
                "depth_ladder": depth_ladder,
            }
        )
    except Exception:
        fallback["depth_ok"] = False
    return fallback


def fetch_symbol_meta(symbol: str) -> SymbolMeta:
    try:
        data = fetch_exchange_info()
        for item in data.get("symbols", []):
            if item.get("symbol") != symbol:
                continue
            tick_size = 0.0
            for flt in item.get("filters", []):
                if flt.get("filterType") == "PRICE_FILTER":
                    tick_size = float(flt.get("tickSize", 0.0))
                    break
            precision = int(item.get("pricePrecision", 8))
            if tick_size > 0:
                return SymbolMeta(tick_size=tick_size, price_precision=precision, tick_size_verified=True)
    except Exception:
        pass
    return SymbolMeta(tick_size=0.0, price_precision=8, tick_size_verified=False)


def ema(values: Sequence[float], period: int) -> List[float]:
    k = 2.0 / (period + 1)
    current = values[0]
    out = []
    for value in values:
        current = value * k + current * (1 - k)
        out.append(current)
    return out


def sma(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} values")
    return sum(values[-period:]) / period


def stddev(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} values")
    window = values[-period:]
    mean = sum(window) / period
    return math.sqrt(sum((value - mean) ** 2 for value in window) / period)


def rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError("not enough values for RSI")
    gain = 0.0
    loss = 0.0
    for idx in range(1, period + 1):
        delta = values[idx] - values[idx - 1]
        if delta >= 0:
            gain += delta
        else:
            loss -= delta
    avg_gain = gain / period
    avg_loss = loss / period
    for idx in range(period + 1, len(values)):
        delta = values[idx] - values[idx - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float:
    trs: List[float] = []
    for idx in range(1, len(closes)):
        trs.append(
            max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1]),
            )
        )
    if len(trs) < period:
        raise ValueError(f"need at least {period + 1} values for ATR")
    # TradingView ta.atr uses Wilder's RMA rather than a rolling SMA.
    value = sum(trs[:period]) / period
    for true_range in trs[period:]:
        value = (value * (period - 1) + true_range) / period
    return value


def macd(values: Sequence[float]) -> tuple[float, float, float]:
    fast = ema(values, 12)
    slow = ema(values, 26)
    line = [fast[idx] - slow[idx] for idx in range(len(values))]
    signal = ema(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def round_price(value: float) -> float:
    value = max(0.0, value)
    if value >= 1:
        return round(value, 4)
    if value >= 0.01:
        return round(value, 6)
    return round(value, 8)


def guess_tick_size(price: float) -> float:
    if price >= 100:
        return 0.01
    if price >= 1:
        return 0.0001
    if price >= 0.01:
        return 0.000001
    return 0.00000001


def round_to_tick(value: float, tick_size: float, mode: str = "nearest") -> float:
    tick = Decimal(str(tick_size if tick_size > 0 else guess_tick_size(value)))
    decimal_value = max(tick, Decimal(str(value)))
    units = decimal_value / tick
    if mode == "down":
        units = units.to_integral_value(rounding=ROUND_FLOOR)
    elif mode == "up":
        units = units.to_integral_value(rounding=ROUND_CEILING)
    else:
        units = units.to_integral_value(rounding=ROUND_HALF_EVEN)
    return float(max(tick, units * tick))


def legal_stop(raw: float, side: str, entry: float, anchor: CandleIndicators, tick_size: float) -> float:
    tick = tick_size if tick_size > 0 else guess_tick_size(entry)
    if side == "short":
        fallback = max(anchor.recent_high_20, entry + 1.2 * anchor.atr14, anchor.close + 1.0 * anchor.atr14)
        stop = raw if raw > entry + tick else fallback
        return round_to_tick(max(stop, entry + tick), tick, "up")

    fallback = min(anchor.recent_low_20, anchor.close) - max(0.6 * anchor.atr14, 2 * tick)
    stop = raw if raw > tick else fallback
    stop = min(stop, entry - tick)
    if stop <= tick:
        stop = max(tick, min(anchor.recent_low_20 - 2 * tick, entry * 0.72))
    return round_to_tick(max(tick, stop), tick, "down")


def legal_zone_price(raw: float, tick_size: float, mode: str = "nearest") -> float:
    return round_to_tick(max(raw, tick_size), tick_size, mode)


def build_indicators(interval: str, candles: Sequence[list]) -> CandleIndicators:
    latest = candles[-1]
    open_time_ms = int(latest[0])
    close_time_ms = int(latest[6])
    now_ms = int(time.time() * 1000)
    highs = [float(item[2]) for item in candles]
    lows = [float(item[3]) for item in candles]
    closes = [float(item[4]) for item in candles]
    volumes = [float(item[5]) for item in candles]
    average_prior_volume = sma(volumes[:-1], 20)

    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    macd_line, macd_signal, macd_hist = macd(closes)
    atr14 = atr(highs, lows, closes, 14)
    boll_mid = sma(closes, 20)
    boll_std = stddev(closes, 20)
    boll_upper = boll_mid + 2.0 * boll_std
    boll_lower = boll_mid - 2.0 * boll_std
    close = closes[-1]
    boll_range = boll_upper - boll_lower
    boll_position_pct = 50.0 if boll_range == 0 else (close - boll_lower) / boll_range * 100.0
    boll_bandwidth_pct = 0.0 if boll_mid == 0 else boll_range / boll_mid * 100.0
    emt = "bull" if close > ema20 > ema50 else "bear" if close < ema20 < ema50 else "mixed"

    open_price = float(latest[1])
    change_pct = (close - open_price) / open_price * 100.0 if open_price else 0.0
    interval_seconds = INTERVAL_SECONDS.get(interval, 60)
    progress_pct = min(100.0, max(0.0, (now_ms - open_time_ms) / (interval_seconds * 1000.0) * 100.0))
    is_closed = now_ms > close_time_ms
    closed_closes = closes if is_closed or len(closes) < 60 else closes[:-1]
    ema20_closed = ema(closed_closes, 20)[-1]
    ema50_closed = ema(closed_closes, 50)[-1]
    rsi14_closed = rsi(closed_closes, 14)
    _, _, macd_hist_closed = macd(closed_closes)

    return CandleIndicators(
        interval=interval,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        close_time_utc=datetime.fromtimestamp(close_time_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        is_closed=is_closed,
        candle_state="已完成K线" if is_closed else "实时K线",
        progress_pct=progress_pct,
        age_seconds=max(0.0, (now_ms - open_time_ms) / 1000.0),
        open=open_price,
        high=highs[-1],
        low=lows[-1],
        close=close,
        ema20=ema20,
        ema50=ema50,
        ema20_closed=ema20_closed,
        ema50_closed=ema50_closed,
        rsi14=rsi(closes, 14),
        rsi14_closed=rsi14_closed,
        macd=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        macd_hist_closed=macd_hist_closed,
        atr14=atr14,
        atr14_pct=atr14 / close * 100.0,
        vol_ratio_20=volumes[-1] / average_prior_volume if average_prior_volume > 0 else 0.0,
        recent_high_20=max(highs[-20:]),
        recent_low_20=min(lows[-20:]),
        boll_mid=boll_mid,
        boll_upper=boll_upper,
        boll_lower=boll_lower,
        boll_position_pct=boll_position_pct,
        boll_bandwidth_pct=boll_bandwidth_pct,
        mt_pct_vs_ema20=(close / ema20 - 1.0) * 100.0,
        emt=emt,
        change_pct=change_pct,
    )


def fetch_indicators(symbol: str, interval: str, limit: int = 220) -> CandleIndicators:
    return build_indicators(interval, fetch_klines(symbol, interval, limit))


def score_timeframe(pct_24h: float, ind: Dict[str, CandleIndicators], weights: Sequence[tuple[str, int]]) -> int:
    score = 0
    for interval, weight in weights:
        item = ind[interval]
        if item.emt == "bull":
            score += weight
        elif item.emt == "bear":
            score -= weight
        if item.rsi14 >= 78:
            score -= weight
        elif item.rsi14 <= 25:
            score += weight
        if item.boll_position_pct >= 95:
            score -= weight
        elif item.boll_position_pct <= 5:
            score += weight
        if item.macd_hist > 0:
            score += 1
        elif item.macd_hist < 0:
            score -= 1
    if pct_24h > 30:
        score -= 2
    elif pct_24h < -20:
        score += 2
    return score


def score_symbol(pct_24h: float, ind: Dict[str, CandleIndicators]) -> int:
    return score_timeframe(
        pct_24h,
        ind,
        [("5m", 1), ("15m", 1), ("1h", 2), ("4h", 2), ("8h", 1), ("1d", 1)],
    )


def side_from_score(score: int) -> str:
    if score >= SIGNAL_SIDE_THRESHOLD:
        return "long"
    if score <= -SIGNAL_SIDE_THRESHOLD:
        return "short"
    return "wait"


def historical_side_with_filter(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    idx: int,
) -> tuple[str, bool, str]:
    if idx < 80:
        return "wait", False, "wait"
    window = closes[:idx + 1]
    high_window = highs[:idx + 1]
    low_window = lows[:idx + 1]
    volume_window = volumes[:idx + 1]
    ema20 = ema(window, 20)[-1]
    ema50 = ema(window, 50)[-1]
    rsi14 = rsi(window, 14)
    macd_line, macd_signal, macd_hist = macd(window)
    atr14 = atr(high_window, low_window, window, 14)
    boll_mid = sma(window, 20)
    boll_std = stddev(window, 20)
    boll_upper = boll_mid + 2.0 * boll_std
    boll_lower = boll_mid - 2.0 * boll_std
    close = closes[idx]
    boll_range = boll_upper - boll_lower
    boll_pos = 50.0 if boll_range == 0 else (close - boll_lower) / boll_range * 100.0
    atr_pct = atr14 / close * 100.0 if close else 0.0
    average_prior_volume = sma(volume_window[:-1], 20)
    vol_ratio = volume_window[-1] / average_prior_volume if average_prior_volume > 0 else 0.0
    score = 0
    if close > ema20 > ema50:
        score += 4
    elif close < ema20 < ema50:
        score -= 4
    if rsi14 <= 25:
        score += 3
    elif rsi14 >= 78:
        score -= 3
    if boll_pos <= 5:
        score += 3
    elif boll_pos >= 95:
        score -= 3
    if macd_hist > 0:
        score += 1
    elif macd_hist < 0:
        score -= 1
    raw_side = side_from_score(score)
    filtered = raw_side != "wait" and (atr_pct >= BACKTEST_ATR_FILTER_PCT or vol_ratio < BACKTEST_VOLUME_FILTER_MIN)
    if filtered:
        return "wait", True, raw_side
    return raw_side, False, raw_side


def historical_side(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    idx: int,
) -> str:
    return historical_side_with_filter(closes, highs, lows, volumes, idx)[0]


def estimated_roundtrip_cost_pct() -> float:
    return 2.0 * (TAKER_FEE_BPS + SLIPPAGE_BPS) / 100.0


def empty_metric(horizon: str) -> BacktestMetric:
    return BacktestMetric(
        horizon=horizon,
        sample_count=0,
        hit_rate=0.0,
        avg_max_profit_pct=0.0,
        avg_max_drawdown_pct=0.0,
        profit_drawdown_ratio=0.0,
        expectancy_pct=0.0,
        stopped_out_count=0,
        stop_rate=0.0,
        avg_loss_pct=0.0,
        net_expectancy_pct=0.0,
        estimated_cost_pct=estimated_roundtrip_cost_pct(),
        filtered_out_count=0,
        sample_filter_note=BACKTEST_SAMPLE_FILTER_NOTE,
    )


def metric_from_records(
    horizon: str,
    records: Sequence[tuple[float, float, bool, float, float]],
    filtered_out_count: int = 0,
) -> BacktestMetric:
    if not records:
        metric = empty_metric(horizon)
        metric.filtered_out_count = filtered_out_count
        return metric
    profits = [item[0] for item in records]
    drawdowns = [item[1] for item in records]
    stopped = [item for item in records if item[2]]
    gross_outcomes = [item[3] for item in records]
    net_outcomes = [item[4] for item in records]
    losses = [item for item in net_outcomes if item < 0]
    hits = [1 for item in net_outcomes if item > 0]
    avg_profit = sum(profits) / len(profits)
    avg_drawdown = sum(drawdowns) / len(drawdowns)
    avg_abs_drawdown = abs(avg_drawdown)
    ratio = avg_profit / avg_abs_drawdown if avg_abs_drawdown else avg_profit
    expectancy = sum(gross_outcomes) / len(gross_outcomes)
    net_expectancy = sum(net_outcomes) / len(net_outcomes)
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return BacktestMetric(
        horizon=horizon,
        sample_count=len(records),
        hit_rate=len(hits) / len(records) * 100.0,
        avg_max_profit_pct=avg_profit,
        avg_max_drawdown_pct=avg_drawdown,
        profit_drawdown_ratio=ratio,
        expectancy_pct=expectancy,
        stopped_out_count=len(stopped),
        stop_rate=len(stopped) / len(records) * 100.0,
        avg_loss_pct=avg_loss,
        net_expectancy_pct=net_expectancy,
        estimated_cost_pct=estimated_roundtrip_cost_pct(),
        filtered_out_count=filtered_out_count,
        sample_filter_note=BACKTEST_SAMPLE_FILTER_NOTE,
    )


def grade_quality(score: int) -> str:
    if score >= 78:
        return "A"
    if score >= 66:
        return "B"
    if score >= 54:
        return "C"
    if score >= 42:
        return "D"
    return "E"


def quality_from_windows(windows: Sequence[BacktestMetric]) -> int:
    usable = [w for w in windows if w.sample_count]
    if not usable:
        return 45
    primary = usable[-1]
    hit_component = (primary.hit_rate - 45.0) * 0.75
    rr_component = min(18.0, max(-16.0, (primary.profit_drawdown_ratio - 1.0) * 16.0))
    exp_component = min(14.0, max(-14.0, primary.net_expectancy_pct * 9.0))
    sample_penalty = 10 if primary.sample_count < 12 else 5 if primary.sample_count < 24 else 0
    return max(20, min(88, round(55 + hit_component + rr_component + exp_component - sample_penalty)))


def historical_stop(
    side: str,
    entry: float,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    idx: int,
) -> float:
    start = max(0, idx - 20)
    atr14 = atr(highs[:idx + 1], lows[:idx + 1], closes[:idx + 1], 14)
    if side == "short":
        stop = max(highs[start:idx + 1]) + 0.5 * atr14
        return max(stop, entry + atr14 * 0.6)
    stop = min(lows[start:idx + 1]) - 0.8 * atr14
    return min(stop, entry - atr14 * 0.6)


def simulate_backtest_trade(
    side: str,
    entry: float,
    stop: float,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    idx: int,
    count: int,
) -> tuple[float, float, bool, float, float]:
    cost = estimated_roundtrip_cost_pct()
    max_profit = 0.0
    max_drawdown = 0.0
    stopped = False
    gross_outcome = 0.0
    end = min(len(closes) - 1, idx + count)
    for step in range(idx + 1, end + 1):
        if side == "long":
            bar_drawdown = (lows[step] - entry) / entry * 100.0
            max_drawdown = min(max_drawdown, bar_drawdown)
            if lows[step] <= stop:
                stopped = True
                gross_outcome = (stop - entry) / entry * 100.0
                break
            max_profit = max(max_profit, (highs[step] - entry) / entry * 100.0)
        else:
            bar_drawdown = (entry - highs[step]) / entry * 100.0
            max_drawdown = min(max_drawdown, bar_drawdown)
            if highs[step] >= stop:
                stopped = True
                gross_outcome = (entry - stop) / entry * 100.0
                break
            max_profit = max(max_profit, (entry - lows[step]) / entry * 100.0)
    if not stopped:
        if side == "long":
            gross_outcome = (closes[end] - entry) / entry * 100.0
        else:
            gross_outcome = (entry - closes[end]) / entry * 100.0
    net_outcome = gross_outcome - cost
    return max(0.0, max_profit - cost), max_drawdown, stopped, gross_outcome, net_outcome


def build_directional_backtests(candles: Sequence[list]) -> Dict[str, DirectionalBacktest]:
    opens = [float(item[1]) for item in candles]
    highs = [float(item[2]) for item in candles]
    lows = [float(item[3]) for item in candles]
    closes = [float(item[4]) for item in candles]
    volumes = [float(item[5]) for item in candles]
    lookaheads = [("5m", 1), ("15m", 3), ("1h", 12)]
    records: Dict[str, Dict[str, List[tuple[float, float, bool, float, float]]]] = {
        "long": {h: [] for h, _ in lookaheads},
        "short": {h: [] for h, _ in lookaheads},
    }
    filtered_counts = {"long": 0, "short": 0}
    next_allowed_idx = {"long": 0, "short": 0}
    max_lookahead = max(count for _, count in lookaheads)
    start = max(80, len(candles) - 260)
    end = len(candles) - max_lookahead - 1
    for idx in range(start, max(start, end)):
        side, filtered, raw_side = historical_side_with_filter(closes, highs, lows, volumes, idx)
        if filtered and raw_side in filtered_counts:
            filtered_counts[raw_side] += 1
        if side not in ("long", "short"):
            continue
        if idx < next_allowed_idx[side]:
            continue
        entry = opens[idx + 1]
        if entry <= 0:
            continue
        stop = historical_stop(side, entry, highs, lows, closes, idx)
        for horizon, count in lookaheads:
            records[side][horizon].append(
                simulate_backtest_trade(side, entry, stop, highs, lows, closes, idx, count)
            )
        next_allowed_idx[side] = idx + max_lookahead + 1

    out: Dict[str, DirectionalBacktest] = {}
    for side in ("long", "short"):
        windows = [metric_from_records(horizon, records[side][horizon], filtered_counts[side]) for horizon, _ in lookaheads]
        quality = quality_from_windows(windows)
        sample_count = max((w.sample_count for w in windows), default=0)
        out[side] = DirectionalBacktest(
            side=side,
            sample_count=sample_count,
            quality_score=quality,
            grade=grade_quality(quality),
            windows=windows,
        )
    return out


def backtest_cache_key(symbol: str, candles: Sequence[list]) -> str:
    if not candles:
        return f"{symbol}:empty"
    usable_idx = max(0, len(candles) - 13)
    marker = candles[usable_idx][6] if len(candles[usable_idx]) > 6 else candles[-1][0]
    return (
        f"v{BACKTEST_MODEL_VERSION}:{symbol.upper()}:{len(candles)}:{marker}:"
        f"{TAKER_FEE_BPS:.4f}:{SLIPPAGE_BPS:.4f}"
    )


def backtest_from_dict(data: Dict[str, object]) -> DirectionalBacktest:
    windows = []
    for item in data.get("windows", []):
        if not isinstance(item, dict):
            continue
        base = asdict(empty_metric(str(item.get("horizon", ""))))
        base.update(item)
        windows.append(BacktestMetric(**base))
    return DirectionalBacktest(
        side=str(data.get("side", "wait")),
        sample_count=int(data.get("sample_count", 0) or 0),
        quality_score=int(data.get("quality_score", 45) or 45),
        grade=str(data.get("grade", "D")),
        windows=windows,
    )


def read_backtest_cache(cache_key_value: str) -> Dict[str, DirectionalBacktest] | None:
    if not BACKTEST_CACHE_FILE:
        return None
    try:
        with open(BACKTEST_CACHE_FILE, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
        item = cache.get("items", {}).get(cache_key_value)
        if not item:
            return None
        if time.time() - float(item.get("ts", 0.0)) > BACKTEST_CACHE_TTL_SECONDS:
            return None
        raw = item.get("backtests")
        if not isinstance(raw, dict):
            return None
        return {side: backtest_from_dict(value) for side, value in raw.items() if isinstance(value, dict)}
    except Exception:
        return None


def _try_lock_backtest_cache_fd(fd: int) -> str:
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size < 1:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return "msvcrt"

        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return "fcntl"
    except (ImportError, OSError):
        return ""


def acquire_backtest_cache_lock(timeout_seconds: float = 5.0) -> tuple[int, str] | None:
    if not BACKTEST_CACHE_FILE:
        return None
    lock_path = BACKTEST_CACHE_FILE + ".lock"
    deadline = time.time() + timeout_seconds
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return None
        backend = _try_lock_backtest_cache_fd(fd)
        if backend:
            return fd, backend
        try:
            os.close(fd)
        except OSError:
            pass
        if time.time() >= deadline:
            return None
        time.sleep(0.05)


def release_backtest_cache_lock(lock: tuple[int, str] | None) -> None:
    if lock is None:
        return
    fd, backend = lock
    try:
        if backend == "msvcrt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        elif backend == "fcntl":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def write_backtest_cache(cache_key_value: str, backtests: Dict[str, DirectionalBacktest]) -> None:
    if not BACKTEST_CACHE_FILE:
        return
    cache_lock = acquire_backtest_cache_lock()
    if cache_lock is None:
        return
    tmp = ""
    try:
        cache_dir = os.path.dirname(BACKTEST_CACHE_FILE)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        cache = {"version": 1, "items": {}}
        if os.path.exists(BACKTEST_CACHE_FILE):
            try:
                with open(BACKTEST_CACHE_FILE, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
                if isinstance(existing, dict) and isinstance(existing.get("items"), dict):
                    cache = existing
            except Exception:
                pass
        now = time.time()
        items = cache.setdefault("items", {})
        for key, item in list(items.items()):
            if now - float(item.get("ts", 0.0)) > BACKTEST_CACHE_TTL_SECONDS * 3:
                items.pop(key, None)
        items[cache_key_value] = {
            "ts": now,
            "backtests": {side: asdict(value) for side, value in backtests.items()},
        }
        tmp = f"{BACKTEST_CACHE_FILE}.{os.getpid()}.{time.time_ns()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, BACKTEST_CACHE_FILE)
    except Exception:
        pass
    finally:
        if tmp:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
        release_backtest_cache_lock(cache_lock)


def load_or_build_directional_backtests(symbol: str, candles: Sequence[list]) -> Dict[str, DirectionalBacktest]:
    key = backtest_cache_key(symbol, candles)
    cached = read_backtest_cache(key)
    if cached and "long" in cached and "short" in cached:
        return cached
    backtests = build_directional_backtests(candles)
    write_backtest_cache(key, backtests)
    return backtests


def bias_from_score(score: int) -> tuple[str, int]:
    side = side_from_score(score)
    if side == "long":
        return "偏多", min(90, 55 + score * 4)
    if side == "short":
        return "偏空", min(90, 55 + abs(score) * 4)
    return "观望", 50 + min(10, abs(score) * 2)


def downgrade_extreme_symbol_bias(symbol: str, pct_24h: float, bias: str, confidence: int) -> tuple[str, int, bool]:
    if symbol.upper().startswith("TLM") and pct_24h > 25 and bias == "偏多":
        return "观望", min(confidence, 45), True
    return bias, confidence, False


def side_from_bias(bias: str) -> str:
    if "观望" in bias:
        return "wait"
    if "偏多" in bias:
        return "long"
    if "偏空" in bias:
        return "short"
    return "wait"


def neutral_backtest() -> DirectionalBacktest:
    windows = [empty_metric("5m"), empty_metric("15m"), empty_metric("1h")]
    return DirectionalBacktest(side="wait", sample_count=0, quality_score=45, grade="D", windows=windows)


def risk_gate(
    anchor: CandleIndicators,
    ind: Dict[str, CandleIndicators],
    pct_24h: float,
    funding_rate: float = 0.0,
) -> tuple[str, List[str], int]:
    warnings: List[str] = []
    penalty = 0
    max_atr = max(item.atr14_pct for item in ind.values())
    max_boll = max(item.boll_bandwidth_pct for item in ind.values())
    if anchor.atr14_pct >= 12 or anchor.boll_bandwidth_pct >= 45:
        warnings.append("当前周期波动极端，禁止半仓，只能观察或小仓验证")
        penalty += 28
    elif anchor.atr14_pct >= 8 or anchor.boll_bandwidth_pct >= 25:
        warnings.append("当前周期波动很高，禁止半仓，最多轻仓")
        penalty += 18
    if max_atr >= 20 or max_boll >= 100:
        warnings.append("跨周期出现极端波动，优先保护本金")
        penalty += 18
    if pct_24h >= 25 or pct_24h <= -18:
        warnings.append("24小时涨跌幅过大，信号需要降级")
        penalty += 10
    if anchor.boll_lower <= 0:
        warnings.append("BOLL下轨失真，止损改用recent low/ATR/tick size")
        penalty += 12
    funding_pct = abs(funding_rate) * 100.0
    if funding_pct >= 0.2:
        warnings.append(f"资金费率{funding_rate * 100:.4f}%极端拥挤，禁止开仓，防止反向踩踏")
        penalty += 36
    elif funding_pct >= 0.1:
        warnings.append(f"资金费率{funding_rate * 100:.4f}%过高，禁止半仓")
        penalty += 22
    elif funding_pct >= 0.05:
        warnings.append(f"资金费率{funding_rate * 100:.4f}%偏高，信号需要降级")
        penalty += 10
    if penalty >= 36:
        return "禁止开仓", warnings, penalty
    if penalty >= 18:
        return "禁止半仓", warnings, penalty
    return "正常", warnings, penalty


def gate_rank(gate: str) -> int:
    if gate == "禁止开仓":
        return 2
    if gate == "禁止半仓":
        return 1
    return 0


def gate_floor_penalty(gate: str) -> int:
    if gate == "禁止开仓":
        return 36
    if gate == "禁止半仓":
        return 18
    return 0


def merge_global_gate(
    local_gate: str,
    local_warnings: List[str],
    local_penalty: int,
    global_gate: str,
) -> tuple[str, List[str], int]:
    if gate_rank(global_gate) <= gate_rank(local_gate):
        return local_gate, local_warnings, local_penalty
    warnings = [f"全局风控为{global_gate}，所有周期同步降级"] + local_warnings
    return global_gate, warnings, max(local_penalty, gate_floor_penalty(global_gate))


def direction_quality(raw: int, side: str, backtest: DirectionalBacktest) -> tuple[int, str]:
    if side == "wait":
        return min(raw, 58), "观望信号不做胜率放大"
    score = max(20, min(88, raw))
    note = (
        f"方向规则{raw}/100；5m代理回测质量{backtest.quality_score}/100"
        f"({backtest.grade})，样本{backtest.sample_count}，不参与开仓分"
    )
    if backtest.sample_count < 20:
        note += "，样本偏少"
    return score, note


def execution_confidence(
    direction_score: int,
    side: str,
    trigger_check: TriggerCheck,
    gate: str,
    candle_state: str,
    risk_penalty: int = 0,
) -> tuple[int, str]:
    score = direction_score
    notes: List[str] = []
    if side == "wait":
        score = min(score, 45)
        notes.append("观望方向不转为开仓分")
    if gate != "正常":
        notes.append(gate)
    if gate == "禁止开仓":
        score = min(score, 29)
    elif gate == "禁止半仓":
        score = min(score, 54)
    else:
        status = trigger_check.status
        if status == "waiting" and side != "wait":
            score = min(score, 55) - 8
            notes.append("入场未到触发区")
        elif status == "blocked":
            score = min(score, 45)
            notes.append("触发确认不通过")
        elif status == "watch":
            score = min(score, 64)
            notes.append("到位但还要确认")
        elif status == "confirmed":
            notes.append("触发确认通过")
    effective_penalty = max(0, risk_penalty - gate_floor_penalty(gate))
    if effective_penalty > 0:
        score -= effective_penalty
        notes.append(f"风险扣分{effective_penalty}")
    if candle_state == "实时K线":
        score -= 5
        notes.append("当前周期K线仍在形成")
    score = max(10, min(88, round(score)))
    return score, "；".join(notes) if notes else "触发与风控正常"


def risk_budget_from_confidence(confidence: int, gate: str, side: str) -> float:
    if side == "wait" or gate == "禁止开仓" or confidence < 45:
        return 0.0
    if confidence >= 75:
        budget = 0.5
    elif confidence >= 65:
        budget = 0.35
    elif confidence >= 55:
        budget = 0.25
    else:
        budget = 0.15
    if gate == "禁止半仓":
        budget = min(budget, 0.2)
    return budget


def build_risk_sizing(
    side: str,
    entry: float,
    stop: float,
    confidence: int,
    gate: str,
    trigger_check: TriggerCheck | None = None,
    tick_size_verified: bool = True,
) -> Dict[str, object]:
    budget_pct = risk_budget_from_confidence(confidence, gate, side)
    allowed = True
    note = "按风险预算计算仓位"
    if side == "wait":
        allowed = False
        note = "观望信号不建议开仓"
    elif gate == "禁止开仓":
        allowed = False
        note = "风控阀门禁止开仓"
    elif confidence < 45:
        allowed = False
        note = "执行分过低，不给开仓仓位"
    trigger_status = trigger_check.status if trigger_check else ""
    if trigger_status == "blocked":
        allowed = False
        note = "入场触发确认不通过，不给开仓仓位"
    stop_distance_pct = 0.0
    if entry > 0 and stop > 0:
        stop_distance_pct = abs(entry - stop) / entry * 100.0
    stop_legal = bool(entry > 0 and stop > 0 and stop_distance_pct > 0)
    if side == "long" and stop >= entry:
        stop_legal = False
    if side == "short" and stop <= entry:
        stop_legal = False
    if not tick_size_verified:
        stop_legal = False
    if not stop_legal:
        allowed = False
        note = "交易所tick size缺失，无法确认止损合法性" if not tick_size_verified else "止损不合法，不给开仓仓位"
    max_size_pct = 0.0
    if allowed:
        if confidence >= 75:
            max_size_pct = 30.0
        elif confidence >= 65:
            max_size_pct = 22.0
        elif confidence >= 55:
            max_size_pct = 15.0
        else:
            max_size_pct = 8.0
        if gate == "禁止半仓":
            max_size_pct = min(max_size_pct, 10.0)
    raw_size_pct = budget_pct * 100.0 / stop_distance_pct if allowed and stop_distance_pct > 0 else 0.0
    suggested_size_pct = min(raw_size_pct, max_size_pct) if allowed else 0.0
    if allowed and trigger_status == "waiting":
        note = "计划仓位，等待价格接近入场区和1m触发确认"
    elif allowed and trigger_status == "watch":
        note = "计划仓位，价格到位但还要等最后确认"
    elif allowed and trigger_status == "confirmed":
        note = "触发确认通过后的风险预算仓位"
    return {
        "side": side,
        "risk_budget_pct": round(budget_pct, 3),
        "stop_distance_pct": round(stop_distance_pct, 3),
        "suggested_size_pct": round(max(0.0, suggested_size_pct), 2),
        "raw_size_pct": round(max(0.0, raw_size_pct), 2),
        "max_size_pct": round(max_size_pct, 2),
        "max_loss_pct": round(suggested_size_pct * stop_distance_pct / 100.0, 3),
        "stop_legal": stop_legal,
        "allowed": allowed,
        "note": note,
    }


def build_trigger_check(
    side: str,
    entry: float,
    ind: Dict[str, CandleIndicators],
    book: Dict[str, float],
    confirmed_ind: Dict[str, CandleIndicators] | None = None,
) -> TriggerCheck:
    live_one_min = ind["1m"]
    one_min = (confirmed_ind or ind)["1m"]
    bid = float(book.get("bid", 0.0) or 0.0)
    ask = float(book.get("ask", 0.0) or 0.0)
    if side == "long" and ask:
        price = ask
    elif side == "short" and bid:
        price = bid
    else:
        price = (bid + ask) / 2.0 if bid and ask else live_one_min.close
    spread_pct = float(book.get("spread_pct", 0.0) or 0.0)
    spread_threshold = max(0.08, one_min.atr14_pct * 0.15)
    depth_imbalance = float(book.get("depth_imbalance", 0.0) or 0.0)
    bid_depth_top5 = float(book.get("bid_depth_top5_usd", 0.0) or 0.0)
    ask_depth_top5 = float(book.get("ask_depth_top5_usd", 0.0) or 0.0)
    min_depth_usd = min(5000.0, max(500.0, price * 50000.0)) if price else 5000.0
    depth_ready = bool(book.get("depth_ok")) and bid_depth_top5 > 0 and ask_depth_top5 > 0
    depth_size_ok = depth_ready and min(bid_depth_top5, ask_depth_top5) >= min_depth_usd
    vol_ratio = one_min.vol_ratio_20
    distance_pct = abs(entry - price) / price * 100.0 if price else 999.0
    near_threshold = min(0.7, max(0.25, one_min.atr14_pct * 2.0))
    near = distance_pct <= near_threshold
    reasons: List[str] = []

    def result(status: str, label: str, is_near: bool, structure: str, items: List[str], depth_pass: bool) -> TriggerCheck:
        return TriggerCheck(
            status,
            label,
            is_near,
            distance_pct,
            spread_pct,
            spread_threshold,
            vol_ratio,
            depth_imbalance,
            bid_depth_top5,
            ask_depth_top5,
            depth_pass,
            structure,
            items,
        )

    if side == "wait":
        return result("waiting", "观望信号，无入场触发", False, "wait", reasons, False)
    if not near:
        reasons.append(f"距离入场价约{distance_pct:.2f}%，超过{near_threshold:.2f}%触发阈值")
        return result("waiting", "等待价格接近入场位", False, "far", reasons, depth_size_ok)

    volume_ok = vol_ratio >= 0.8
    spread_ok = bool(book.get("bid", 0.0) and book.get("ask", 0.0)) and spread_pct <= spread_threshold
    if side == "short":
        imbalance_ok = depth_size_ok and depth_imbalance <= -0.15
    else:
        imbalance_ok = depth_size_ok and depth_imbalance >= 0.15
    touch_tolerance = max(0.0015, near_threshold / 100.0 * 0.7)
    ema20_ref = one_min.ema20
    macd_hist_ref = one_min.macd_hist
    if side == "short":
        retest_ok = one_min.high >= entry * (1.0 - touch_tolerance)
        structure_ok = retest_ok and one_min.close < ema20_ref and macd_hist_ref <= 0
        structure = "反抽失败" if structure_ok else "反抽未确认"
    else:
        retest_ok = one_min.low <= entry * (1.0 + touch_tolerance)
        structure_ok = retest_ok and one_min.close > ema20_ref and macd_hist_ref >= 0
        structure = "突破站稳" if structure_ok else "突破未确认"
    if not volume_ok:
        reasons.append(f"1m量能{vol_ratio:.2f}x偏弱")
    if not spread_ok:
        reasons.append(f"盘口价差{spread_pct:.3f}%超过{spread_threshold:.3f}%阈值或盘口缺失")
    if not depth_ready:
        reasons.append("未拿到盘口深度，触发确认按保守处理")
    elif not depth_size_ok:
        reasons.append(f"top5深度不足，买盘{bid_depth_top5:.0f}U/卖盘{ask_depth_top5:.0f}U")
    elif not imbalance_ok:
        reasons.append(f"盘口不平衡未支持方向，imbalance {depth_imbalance:+.2f}")
    if not retest_ok:
        reasons.append("1m尚未触及反抽区" if side == "short" else "1m尚未触及回踩区")
    if not structure_ok:
        reasons.append(structure)
    depth_pass = depth_size_ok and imbalance_ok
    if volume_ok and spread_ok and structure_ok and depth_pass:
        return result("confirmed", "入场触发已确认", True, structure, ["量能、价差、深度和1m结构均通过"], depth_pass)
    if spread_ok and depth_pass and (volume_ok or structure_ok):
        return result("watch", "到位但等最后确认", True, structure, reasons, depth_pass)
    return result("blocked", "到位但不建议动手", True, structure, reasons, depth_pass)


def build_timeframe_advice(
    symbol: str,
    pct_24h: float,
    funding_rate: float,
    ind: Dict[str, CandleIndicators],
    meta: SymbolMeta,
    book: Dict[str, float],
    backtests: Dict[str, DirectionalBacktest],
    global_gate: str,
    confirmed_ind: Dict[str, CandleIndicators] | None = None,
) -> List[TimeframeAdvice]:
    signal_ind = confirmed_ind or ind
    configs = [
        ("超短线1-15分钟", "适合看1m、5m、15m，只适合快进快出", [("1m", 2), ("5m", 4), ("15m", 2), ("1h", 1)], "5m"),
        ("短线1小时", "适合看5m、15m和1h，偏快进快出", [("5m", 1), ("15m", 3), ("1h", 3), ("4h", 1)], "1h"),
        ("波段1-2天", "适合看1h、4h、8h", [("1h", 2), ("4h", 3), ("8h", 2), ("1d", 1)], "4h"),
        ("一周", "适合看4h、8h、1d", [("4h", 2), ("8h", 3), ("1d", 3)], "8h"),
    ]
    advice: List[TimeframeAdvice] = []
    for name, horizon, weights, anchor_interval in configs:
        score = score_timeframe(pct_24h, signal_ind, weights)
        bias, raw_confidence = bias_from_score(score)
        bias, raw_confidence, extreme_downgraded = downgrade_extreme_symbol_bias(symbol, pct_24h, bias, raw_confidence)
        anchor = signal_ind[anchor_interval]
        gate, gate_warnings, gate_penalty = risk_gate(ind[anchor_interval], ind, pct_24h, funding_rate)
        gate, gate_warnings, gate_penalty = merge_global_gate(gate, gate_warnings, gate_penalty, global_gate)
        side = side_from_bias(bias)
        selected_backtest = backtests.get(side, neutral_backtest()) if side != "wait" else neutral_backtest()
        direction_score, confidence_note = direction_quality(raw_confidence, side, selected_backtest)
        if extreme_downgraded:
            direction_score = min(direction_score, 59)
            confidence_note += "，TLM极端波动降级"
        reasons = [
            f"{anchor_interval}趋势{anchor.emt}，RSI {anchor.rsi14:.1f}",
            f"BOLL位置{anchor.boll_position_pct:.0f}%，带宽{anchor.boll_bandwidth_pct:.2f}%",
            f"ATR波动{anchor.atr14_pct:.2f}%",
            f"{anchor.candle_state}：K线已运行{anchor.progress_pct:.0f}%",
            confidence_note,
        ]
        if anchor.boll_position_pct >= 90:
            reasons.append("靠近BOLL上轨，追多要谨慎")
        elif anchor.boll_position_pct <= 10:
            reasons.append("靠近BOLL下轨，追空要谨慎")
        if anchor.atr14_pct >= 8:
            reasons.append("波动偏大，建议低杠杆和宽区间")
        if pct_24h >= 30:
            reasons.append("24h涨幅过大，防止高位回撤")
        reasons.extend(gate_warnings)

        if side == "long":
            action = "等回踩做多网格，避免贴近上轨追多。"
            long_entry = min(anchor.close, anchor.boll_mid, anchor.ema20 + 0.2 * anchor.atr14)
            short_entry = max(anchor.close, anchor.boll_mid, anchor.ema20 + 1.2 * anchor.atr14)
            stop_hint = legal_stop(min(anchor.recent_low_20, anchor.boll_lower) - 0.8 * anchor.atr14, "long", long_entry, anchor, meta.tick_size)
        elif side == "short":
            action = "等反弹做空网格，避免贴近下轨追空。"
            long_entry = min(anchor.close, anchor.boll_mid, anchor.ema20 + 0.2 * anchor.atr14)
            short_entry = max(anchor.close, anchor.boll_mid, anchor.ema20 + 1.2 * anchor.atr14)
            stop_hint = legal_stop(max(anchor.recent_high_20, anchor.boll_upper) + 0.5 * anchor.atr14, "short", short_entry, anchor, meta.tick_size)
        else:
            action = "观望或只在支撑/压力附近轻仓试网格。"
            long_entry = min(anchor.close, anchor.boll_mid, anchor.ema20 + 0.2 * anchor.atr14)
            short_entry = max(anchor.close, anchor.boll_mid, anchor.ema20 + 1.2 * anchor.atr14)
            stop_hint = legal_stop(min(anchor.recent_low_20, anchor.boll_lower) - 0.8 * anchor.atr14, "long", long_entry, anchor, meta.tick_size)

        if extreme_downgraded:
            action = "暴涨后不追多，也不自动反手做空；等待新方向确认。"
            reasons.append("TLM暴涨后按妖币风控降级为观望，禁止把追多或自动反手当成开仓信号")
        if gate == "禁止开仓":
            action = "全局或当前周期极端波动，禁止新开仓；只允许观察或处理已有仓位。"
        elif gate == "禁止半仓" and direction_score >= 60:
            action = action + " 当前禁止半仓，最多轻仓验证。"

        long_entry = legal_zone_price(long_entry, meta.tick_size)
        short_entry = legal_zone_price(short_entry, meta.tick_size)
        trigger_side = "wait" if gate == "禁止开仓" else side
        trigger_entry = short_entry if trigger_side == "short" else long_entry
        trigger_check = build_trigger_check(trigger_side, trigger_entry, ind, book, signal_ind)
        confidence, execution_note = execution_confidence(
            direction_score, side, trigger_check, gate, anchor.candle_state, gate_penalty
        )
        sizing_entry = short_entry if side == "short" else long_entry
        risk_sizing = build_risk_sizing(
            side, sizing_entry, stop_hint, confidence, gate, trigger_check, meta.tick_size_verified
        )
        reasons.append(execution_note)
        reasons.append(
            f"风险预算仓位：{risk_sizing['suggested_size_pct']}%权益，"
            f"单笔风险预算{risk_sizing['risk_budget_pct']}%，止损距离{risk_sizing['stop_distance_pct']}%"
        )

        advice.append(
            TimeframeAdvice(
                name=name,
                horizon=horizon,
                bias=bias,
                confidence=confidence,
                raw_confidence=raw_confidence,
                direction_score=direction_score,
                execution_score=confidence,
                confidence_note=confidence_note,
                execution_note=execution_note,
                anchor_interval=anchor_interval,
                candle_state=anchor.candle_state,
                risk_gate=gate,
                action=action,
                long_entry=long_entry,
                short_entry=short_entry,
                stop_hint=stop_hint,
                backtest=selected_backtest,
                trigger_check=trigger_check,
                risk_sizing=risk_sizing,
                reasons=reasons,
            )
        )
    return advice


def build_grid(last: float, item_1h: CandleIndicators, side: str, meta: SymbolMeta) -> Dict[str, float]:
    atr_value = item_1h.atr14
    if side == "long":
        lower = min(item_1h.recent_low_20, item_1h.boll_lower, last - 1.6 * atr_value)
        entry = min(last, item_1h.ema20 + 0.2 * atr_value, item_1h.boll_mid)
        upper = max(item_1h.recent_high_20, item_1h.boll_upper, last + 2.0 * atr_value)
        entry = legal_zone_price(entry, meta.tick_size)
        lower = legal_zone_price(lower if lower > meta.tick_size else item_1h.recent_low_20 - 0.6 * atr_value, meta.tick_size, "down")
        upper = legal_zone_price(upper, meta.tick_size, "up")
        stop = legal_stop(lower - 0.8 * atr_value, "long", entry, item_1h, meta.tick_size)
    else:
        lower = min(item_1h.recent_low_20, item_1h.boll_lower, last - 2.0 * atr_value)
        entry = max(last, item_1h.ema20 + 1.2 * atr_value, item_1h.boll_mid)
        upper = max(item_1h.recent_high_20, item_1h.boll_upper, entry + 1.0 * atr_value)
        entry = legal_zone_price(entry, meta.tick_size)
        lower = legal_zone_price(lower if lower > meta.tick_size else item_1h.recent_low_20 - 0.6 * atr_value, meta.tick_size, "down")
        upper = legal_zone_price(upper, meta.tick_size, "up")
        stop = legal_stop(upper + 0.5 * atr_value, "short", entry, item_1h, meta.tick_size)
    return {
        "lower": lower,
        "entry": entry,
        "upper": upper,
        "stop": stop,
    }


def explain(symbol: str, pct_24h: float, ind: Dict[str, CandleIndicators], score: int) -> tuple[str, int, str, List[str]]:
    risks: List[str] = []
    i1m = ind["1m"]
    i5 = ind["5m"]
    i15 = ind["15m"]
    i1h = ind["1h"]
    i4h = ind["4h"]
    i8h = ind["8h"]
    i1d = ind["1d"]

    if i1d.rsi14 >= 75 or pct_24h >= 30:
        risks.append("日线或24小时涨幅过热，追多容易被回撤扫掉。")
    if i1h.atr14_pct >= 8 or i4h.atr14_pct >= 15:
        risks.append("波动率很高，合约网格应降低杠杆和仓位。")
    if i1h.atr14_pct >= 12 or i4h.atr14_pct >= 18 or i8h.atr14_pct >= 22:
        risks.append("ATR极端放大，禁止半仓，未触发确认前不要新开仓。")
    if i1h.boll_bandwidth_pct >= 12 or i4h.boll_bandwidth_pct >= 25:
        risks.append("BOLL带宽很大，价格容易快速穿越网格区间。")
    if i1h.boll_bandwidth_pct >= 30 or i4h.boll_bandwidth_pct >= 45 or i8h.boll_bandwidth_pct >= 90:
        risks.append("BOLL带宽极端，网格容易被单边穿越，优先禁止开仓。")
    if i1h.boll_position_pct >= 90:
        risks.append("价格接近1小时BOLL上轨，追多风险偏高。")
    elif i1h.boll_position_pct <= 10:
        risks.append("价格接近1小时BOLL下轨，追空风险偏高。")
    if i1m.emt != i5.emt or i5.emt != i15.emt:
        risks.append("1分钟/5分钟/15分钟方向不一致，超短线容易来回扫。")
    if i15.emt != i1h.emt:
        risks.append("15分钟和1小时方向不一致，短线可能震荡。")
    if i1d.emt == "bear" and i1h.emt == "bull":
        risks.append("小时级别反弹，但日线仍偏弱，不适合无止损长扛多。")

    bias, confidence = bias_from_score(score)
    if bias == "偏多":
        summary = "适合等回踩做多网格，不建议在短线压力位一次性追满。"
    elif bias == "偏空":
        summary = "适合等反弹做空网格，不建议在急跌后低位追空。"
    else:
        summary = "方向不够干净，更适合等价格靠近支撑/压力后再开网格。"

    if symbol.upper().startswith("TLM") and pct_24h > 25:
        summary = "暴涨后偏过热，禁止追多，也不自动反手做空，等待新方向确认。"
        bias, confidence, _ = downgrade_extreme_symbol_bias(symbol, pct_24h, bias, confidence)

    return bias, confidence, summary, risks


def analyze_symbol(symbol: str) -> SymbolReport:
    symbol = symbol.upper().strip()
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix=f"bian-{symbol}") as pool:
        ticker_future = pool.submit(request_json, "/fapi/v1/ticker/24hr", {"symbol": symbol})
        premium_future = pool.submit(request_json, "/fapi/v1/premiumIndex", {"symbol": symbol})
        meta_future = pool.submit(fetch_symbol_meta, symbol)
        book_future = pool.submit(fetch_book_ticker, symbol)
        candle_futures = {
            interval: pool.submit(fetch_klines, symbol, interval, 420 if interval == "5m" else 220)
            for interval in INTERVALS
        }
        ticker = ticker_future.result()
        price_observed_at_ms = int(time.time() * 1000)
        premium = premium_future.result()
        meta = meta_future.result()
        book = book_future.result()
        candles_by_interval = {interval: candle_futures[interval].result() for interval in INTERVALS}
    indicators = {interval: build_indicators(interval, candles_by_interval[interval]) for interval in INTERVALS}
    confirmed_indicators = {
        interval: build_indicators(
            interval,
            candles_by_interval[interval]
            if indicators[interval].is_closed
            else candles_by_interval[interval][:-1],
        )
        for interval in INTERVALS
    }
    backtests = load_or_build_directional_backtests(symbol, candles_by_interval["5m"])

    last = float(ticker["lastPrice"])
    pct_24h = float(ticker["priceChangePercent"])
    funding_rate = float(premium["lastFundingRate"])
    score = score_symbol(pct_24h, confirmed_indicators)
    bias, raw_confidence, summary, risks = explain(symbol, pct_24h, indicators, score)
    top_extreme_downgraded = symbol.upper().startswith("TLM") and pct_24h > 25 and bias == "观望"
    top_side = side_from_bias(bias)
    top_backtest = backtests.get(top_side, neutral_backtest()) if top_side != "wait" else neutral_backtest()
    top_gate, top_gate_warnings, top_gate_penalty = risk_gate(indicators["1h"], indicators, pct_24h, funding_rate)
    direction_score, direction_note = direction_quality(raw_confidence, top_side, top_backtest)
    if top_extreme_downgraded:
        direction_score = min(direction_score, 59)
        direction_note += "，TLM极端波动降级"
    long_grid = build_grid(last, confirmed_indicators["1h"], "long", meta)
    short_grid = build_grid(last, confirmed_indicators["1h"], "short", meta)
    top_entry = short_grid["entry"] if top_side == "short" else long_grid["entry"]
    top_stop = short_grid["stop"] if top_side == "short" else long_grid["stop"]
    top_trigger_side = "wait" if top_gate == "禁止开仓" else top_side
    top_trigger = build_trigger_check(top_trigger_side, top_entry, indicators, book, confirmed_indicators)
    confidence, execution_note = execution_confidence(
        direction_score,
        top_side,
        top_trigger,
        top_gate,
        confirmed_indicators["1h"].candle_state,
        top_gate_penalty,
    )
    top_risk_sizing = build_risk_sizing(
        top_side, top_entry, top_stop, confidence, top_gate, top_trigger, meta.tick_size_verified
    )
    confidence_note = f"{direction_note}；{execution_note}"
    risks.extend(x for x in top_gate_warnings if x not in risks)
    timeframe_advice = build_timeframe_advice(
        symbol,
        pct_24h,
        funding_rate,
        indicators,
        meta,
        book,
        backtests,
        top_gate,
        confirmed_indicators,
    )

    return SymbolReport(
        symbol=symbol,
        last=last,
        price_observed_at_ms=price_observed_at_ms,
        pct_24h=pct_24h,
        high_24h=float(ticker["highPrice"]),
        low_24h=float(ticker["lowPrice"]),
        quote_volume_24h=float(ticker["quoteVolume"]),
        funding_rate=funding_rate,
        mark_price=float(premium.get("markPrice") or last),
        index_price=float(premium.get("indexPrice") or last),
        next_funding_time_ms=int(premium.get("nextFundingTime") or 0),
        indicators=indicators,
        bias=bias,
        confidence=confidence,
        summary=summary,
        long_grid=long_grid,
        short_grid=short_grid,
        risks=risks,
        backtests=backtests,
        signal_quality={
            "raw_confidence": raw_confidence,
            "direction_score": direction_score,
            "execution_score": confidence,
            "calibrated_confidence": None,
            "calibration_status": "5m_proxy_not_live_isomorphic",
            "confidence_source": "rules+risk+trigger; 5m proxy backtest is informational only",
            "confidence_note": confidence_note,
            "direction_note": direction_note,
            "execution_note": execution_note,
            "risk_gate": top_gate,
            "trigger_check": top_trigger,
            "risk_sizing": top_risk_sizing,
            "tick_size": meta.tick_size,
            "tick_size_verified": meta.tick_size_verified,
            "bid": book.get("bid", 0.0),
            "ask": book.get("ask", 0.0),
            "spread_pct": book.get("spread_pct", 0.0),
            "depth_ok": book.get("depth_ok", False),
            "depth_imbalance": book.get("depth_imbalance", 0.0),
            "bid_depth_top5_usd": book.get("bid_depth_top5_usd", 0.0),
            "ask_depth_top5_usd": book.get("ask_depth_top5_usd", 0.0),
            "depth_ladder": book.get("depth_ladder", {"bids": [], "asks": []}),
        },
        timeframe_advice=timeframe_advice,
    )


def format_report(report: SymbolReport) -> str:
    i1m = report.indicators["1m"]
    i5 = report.indicators["5m"]
    i15 = report.indicators["15m"]
    i1h = report.indicators["1h"]
    i4h = report.indicators["4h"]
    i1d = report.indicators["1d"]
    lines = [
        f"\n=== {report.symbol} ===",
        f"现价: {round_price(report.last)} | 24h: {report.pct_24h:+.2f}% | 高/低: {round_price(report.high_24h)} / {round_price(report.low_24h)} | 资金费率: {report.funding_rate * 100:.4f}%",
        f"结论: {report.bias} | 开仓执行分: {report.confidence}/100 | 方向质量分: {report.signal_quality.get('direction_score', report.confidence)}/100",
        f"建议: {report.summary}",
        "",
        "网格参考:",
        f"  做多网格: 下沿 {report.long_grid['lower']} | 入场 {report.long_grid['entry']} | 上沿 {report.long_grid['upper']} | 停止/止损 {report.long_grid['stop']}",
        f"  做空网格: 下沿 {report.short_grid['lower']} | 入场 {report.short_grid['entry']} | 上沿 {report.short_grid['upper']} | 停止/止损 {report.short_grid['stop']}",
        "",
        "按持仓周期:",
    ]
    for item in report.timeframe_advice:
        lines.append(
            f"  {item.name}: {item.bias}(方向{item.direction_score}/执行{item.execution_score}) | {item.risk_gate} | {item.action} | 多入场 {item.long_entry} | 空入场 {item.short_entry} | 风控 {item.stop_hint}"
        )
        lines.append(f"    原因: {'；'.join(item.reasons)}")
    lines.extend([
        "",
        "指标翻译:",
        f"  1m : 趋势 {i1m.emt}, RSI {i1m.rsi14:.1f}, 量能 {i1m.vol_ratio_20:.2f}x, EMA20偏离 {i1m.mt_pct_vs_ema20:+.2f}%, BOLL位置 {i1m.boll_position_pct:.0f}%, K线已运行{i1m.age_seconds:.0f}秒",
        f"  5m : 趋势 {i5.emt}, RSI {i5.rsi14:.1f}, 量能 {i5.vol_ratio_20:.2f}x, EMA20偏离 {i5.mt_pct_vs_ema20:+.2f}%, BOLL位置 {i5.boll_position_pct:.0f}%, K线已运行{i5.age_seconds:.0f}秒",
        f"  15m: 趋势 {i15.emt}, RSI {i15.rsi14:.1f}, 量能 {i15.vol_ratio_20:.2f}x, EMA20偏离 {i15.mt_pct_vs_ema20:+.2f}%, BOLL位置 {i15.boll_position_pct:.0f}%, K线已运行{i15.age_seconds:.0f}秒",
        f"  1h : 趋势 {i1h.emt}, RSI {i1h.rsi14:.1f}, 量能 {i1h.vol_ratio_20:.2f}x, ATR {i1h.atr14_pct:.2f}%, BOLL {round_price(i1h.boll_lower)}/{round_price(i1h.boll_mid)}/{round_price(i1h.boll_upper)} 位置{i1h.boll_position_pct:.0f}% 带宽{i1h.boll_bandwidth_pct:.2f}%",
        f"  4h : 趋势 {i4h.emt}, RSI {i4h.rsi14:.1f}, ATR {i4h.atr14_pct:.2f}%, BOLL位置 {i4h.boll_position_pct:.0f}% 带宽{i4h.boll_bandwidth_pct:.2f}%",
        f"  1d : 趋势 {i1d.emt}, RSI {i1d.rsi14:.1f}, EMA20偏离 {i1d.mt_pct_vs_ema20:+.2f}%, BOLL位置 {i1d.boll_position_pct:.0f}%",
    ])
    if report.risks:
        lines.append("")
        lines.append("风险:")
        lines.extend(f"  - {risk}" for risk in report.risks)
    return "\n".join(lines)


def parse_symbols(args: argparse.Namespace) -> List[str]:
    raw: List[str] = []
    if args.symbols:
        raw.extend(part.strip() for part in args.symbols.split(","))
    raw.extend(args.positional_symbols)
    symbols = [item.upper() for item in raw if item.strip()]
    return symbols or DEFAULT_SYMBOLS


def to_jsonable(report: SymbolReport) -> Dict[str, object]:
    data = asdict(report)
    data["indicators"] = {key: asdict(value) for key, value in report.indicators.items()}
    return data


def main(argv: Sequence[str]) -> int:
    global TAKER_FEE_BPS, SLIPPAGE_BPS, BACKTEST_CACHE_FILE, BACKTEST_CACHE_TTL_SECONDS
    parser = argparse.ArgumentParser(description="Analyze Binance USD-M futures market and print grid guidance.")
    parser.add_argument("positional_symbols", nargs="*", help="Symbols such as DOGEUSDT TLMUSDT")
    parser.add_argument("--symbols", help="Comma-separated symbols, for example DOGEUSDT,TLMUSDT")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of Chinese text report")
    parser.add_argument("--fees-bps", type=float, default=DEFAULT_TAKER_FEE_BPS, help="One-way taker fee in basis points, default 5")
    parser.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS, help="One-way estimated slippage in basis points, default 2")
    parser.add_argument("--backtest-cache-file", default=BACKTEST_CACHE_FILE, help="Path for reusable backtest cache")
    parser.add_argument("--backtest-cache-ttl", type=int, default=BACKTEST_CACHE_TTL_SECONDS, help="Backtest cache TTL seconds")
    args = parser.parse_args(argv)
    TAKER_FEE_BPS = max(0.0, args.fees_bps)
    SLIPPAGE_BPS = max(0.0, args.slippage_bps)
    BACKTEST_CACHE_FILE = args.backtest_cache_file or ""
    BACKTEST_CACHE_TTL_SECONDS = max(0, args.backtest_cache_ttl)

    symbols = parse_symbols(args)
    reports: List[SymbolReport] = []
    with ThreadPoolExecutor(max_workers=min(2, len(symbols)), thread_name_prefix="bian-symbol") as pool:
        futures = {symbol: pool.submit(analyze_symbol, symbol) for symbol in symbols}
        for symbol in symbols:
            try:
                reports.append(futures[symbol].result())
            except Exception as exc:  # Keep one failed symbol from hiding the rest.
                print(f"ERROR {symbol}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps([to_jsonable(report) for report in reports], ensure_ascii=True, indent=2))
    else:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"Binance 合约行情分析 - {now}")
        print("说明: 这是技术指标辅助，不是保证盈利的交易指令。合约请低杠杆、设止损。")
        for report in reports:
            print(format_report(report))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))






