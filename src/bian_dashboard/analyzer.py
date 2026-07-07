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
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence

BASE_URL = "https://fapi.binance.com"
DEFAULT_SYMBOLS = ["DOGEUSDT", "TLMUSDT"]
INTERVALS = ["1m", "5m", "15m", "1h", "4h", "8h", "1d"]


@dataclass
class CandleIndicators:
    interval: str
    open_time_utc: str
    close_time_utc: str
    age_seconds: float
    close: float
    ema20: float
    ema50: float
    rsi14: float
    macd: float
    macd_signal: float
    macd_hist: float
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
class TimeframeAdvice:
    name: str
    horizon: str
    bias: str
    confidence: int
    action: str
    long_entry: float
    short_entry: float
    stop_hint: float
    reasons: List[str]


@dataclass
class SymbolReport:
    symbol: str
    last: float
    pct_24h: float
    high_24h: float
    low_24h: float
    quote_volume_24h: float
    funding_rate: float
    indicators: Dict[str, CandleIndicators]
    bias: str
    confidence: int
    summary: str
    long_grid: Dict[str, float]
    short_grid: Dict[str, float]
    risks: List[str]
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
    return sma(trs, period)


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


def fetch_indicators(symbol: str, interval: str, limit: int = 220) -> CandleIndicators:
    data = request_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    candles = list(data)
    latest = candles[-1]
    open_time_ms = int(latest[0])
    close_time_ms = int(latest[6])
    now_ms = int(time.time() * 1000)
    highs = [float(item[2]) for item in candles]
    lows = [float(item[3]) for item in candles]
    closes = [float(item[4]) for item in candles]
    volumes = [float(item[5]) for item in candles]

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

    return CandleIndicators(
        interval=interval,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        close_time_utc=datetime.fromtimestamp(close_time_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        age_seconds=max(0.0, (now_ms - open_time_ms) / 1000.0),
        close=close,
        ema20=ema20,
        ema50=ema50,
        rsi14=rsi(closes, 14),
        macd=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        atr14=atr14,
        atr14_pct=atr14 / close * 100.0,
        vol_ratio_20=volumes[-1] / sma(volumes, 20),
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


def bias_from_score(score: int) -> tuple[str, int]:
    if score >= 5:
        return "偏多", min(90, 55 + score * 4)
    if score <= -5:
        return "偏空", min(90, 55 + abs(score) * 4)
    return "观望", 50 + min(10, abs(score) * 2)


def build_timeframe_advice(symbol: str, pct_24h: float, ind: Dict[str, CandleIndicators]) -> List[TimeframeAdvice]:
    configs = [
        ("超短线1-15分钟", "适合看1m、5m、15m，只适合快进快出", [("1m", 2), ("5m", 4), ("15m", 2), ("1h", 1)], "5m"),
        ("短线1小时", "适合看5m、15m和1h，偏快进快出", [("5m", 1), ("15m", 3), ("1h", 3), ("4h", 1)], "1h"),
        ("波段1-2天", "适合看1h、4h、8h", [("1h", 2), ("4h", 3), ("8h", 2), ("1d", 1)], "4h"),
        ("一周", "适合看4h、8h、1d", [("4h", 2), ("8h", 3), ("1d", 3)], "8h"),
    ]
    advice: List[TimeframeAdvice] = []
    for name, horizon, weights, anchor_interval in configs:
        score = score_timeframe(pct_24h, ind, weights)
        bias, confidence = bias_from_score(score)
        anchor = ind[anchor_interval]
        reasons = [
            f"{anchor_interval}趋势{anchor.emt}，RSI {anchor.rsi14:.1f}",
            f"BOLL位置{anchor.boll_position_pct:.0f}%，带宽{anchor.boll_bandwidth_pct:.2f}%",
            f"ATR波动{anchor.atr14_pct:.2f}%",
            f"K线开盘{anchor.open_time_utc}，已运行约{anchor.age_seconds:.0f}秒",
        ]
        if anchor.boll_position_pct >= 90:
            reasons.append("靠近BOLL上轨，追多要谨慎")
        elif anchor.boll_position_pct <= 10:
            reasons.append("靠近BOLL下轨，追空要谨慎")
        if anchor.atr14_pct >= 8:
            reasons.append("波动偏大，建议低杠杆和宽区间")
        if pct_24h >= 30:
            reasons.append("24h涨幅过大，防止高位回撤")

        if bias == "偏多":
            action = "等回踩做多网格，避免贴近上轨追多。"
            stop_hint = min(anchor.recent_low_20, anchor.boll_lower) - 0.8 * anchor.atr14
        elif bias == "偏空":
            action = "等反弹做空网格，避免贴近下轨追空。"
            stop_hint = max(anchor.recent_high_20, anchor.boll_upper) + 0.5 * anchor.atr14
        else:
            action = "观望或只在支撑/压力附近轻仓试网格。"
            stop_hint = min(anchor.recent_low_20, anchor.boll_lower) - 0.8 * anchor.atr14

        if symbol.upper().startswith("TLM") and pct_24h > 25 and bias == "偏多":
            bias = "观望偏空"
            confidence = max(confidence, 65)
            action = "暴涨后不追多，优先等反弹高位空网格或观望。"

        advice.append(
            TimeframeAdvice(
                name=name,
                horizon=horizon,
                bias=bias,
                confidence=confidence,
                action=action,
                long_entry=round_price(min(anchor.close, anchor.boll_mid, anchor.ema20 + 0.2 * anchor.atr14)),
                short_entry=round_price(max(anchor.close, anchor.boll_mid, anchor.ema20 + 1.2 * anchor.atr14)),
                stop_hint=round_price(stop_hint),
                reasons=reasons,
            )
        )
    return advice


def build_grid(last: float, item_1h: CandleIndicators, side: str) -> Dict[str, float]:
    atr_value = item_1h.atr14
    if side == "long":
        lower = min(item_1h.recent_low_20, item_1h.boll_lower, last - 1.6 * atr_value)
        entry = min(last, item_1h.ema20 + 0.2 * atr_value, item_1h.boll_mid)
        upper = max(item_1h.recent_high_20, item_1h.boll_upper, last + 2.0 * atr_value)
        stop = lower - 0.8 * atr_value
    else:
        lower = min(item_1h.recent_low_20, item_1h.boll_lower, last - 2.0 * atr_value)
        entry = max(last, item_1h.ema20 + 1.2 * atr_value, item_1h.boll_mid)
        upper = max(item_1h.recent_high_20, item_1h.boll_upper, entry + 1.0 * atr_value)
        stop = upper + 0.5 * atr_value
    return {
        "lower": round_price(lower),
        "entry": round_price(entry),
        "upper": round_price(upper),
        "stop": round_price(stop),
    }


def explain(symbol: str, pct_24h: float, ind: Dict[str, CandleIndicators], score: int) -> tuple[str, int, str, List[str]]:
    risks: List[str] = []
    i1m = ind["1m"]
    i5 = ind["5m"]
    i15 = ind["15m"]
    i1h = ind["1h"]
    i4h = ind["4h"]
    i1d = ind["1d"]

    if i1d.rsi14 >= 75 or pct_24h >= 30:
        risks.append("日线或24小时涨幅过热，追多容易被回撤扫掉。")
    if i1h.atr14_pct >= 8 or i4h.atr14_pct >= 15:
        risks.append("波动率很高，合约网格应降低杠杆和仓位。")
    if i1h.boll_bandwidth_pct >= 12 or i4h.boll_bandwidth_pct >= 25:
        risks.append("BOLL带宽很大，价格容易快速穿越网格区间。")
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
        summary = "暴涨后偏过热，更适合反弹高位空网格或观望，追多风险大。"
        if bias == "偏多":
            bias = "观望偏空"
            confidence = max(confidence, 65)

    return bias, confidence, summary, risks


def analyze_symbol(symbol: str) -> SymbolReport:
    symbol = symbol.upper().strip()
    ticker = request_json("/fapi/v1/ticker/24hr", {"symbol": symbol})
    premium = request_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    indicators = {interval: fetch_indicators(symbol, interval) for interval in INTERVALS}

    last = float(ticker["lastPrice"])
    pct_24h = float(ticker["priceChangePercent"])
    score = score_symbol(pct_24h, indicators)
    bias, confidence, summary, risks = explain(symbol, pct_24h, indicators, score)
    timeframe_advice = build_timeframe_advice(symbol, pct_24h, indicators)

    return SymbolReport(
        symbol=symbol,
        last=last,
        pct_24h=pct_24h,
        high_24h=float(ticker["highPrice"]),
        low_24h=float(ticker["lowPrice"]),
        quote_volume_24h=float(ticker["quoteVolume"]),
        funding_rate=float(premium["lastFundingRate"]),
        indicators=indicators,
        bias=bias,
        confidence=confidence,
        summary=summary,
        long_grid=build_grid(last, indicators["1h"], "long"),
        short_grid=build_grid(last, indicators["1h"], "short"),
        risks=risks,
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
        f"结论: {report.bias} | 置信度: {report.confidence}/100",
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
            f"  {item.name}: {item.bias}({item.confidence}/100) | {item.action} | 多入场 {item.long_entry} | 空入场 {item.short_entry} | 风控 {item.stop_hint}"
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
    parser = argparse.ArgumentParser(description="Analyze Binance USD-M futures market and print grid guidance.")
    parser.add_argument("positional_symbols", nargs="*", help="Symbols such as DOGEUSDT TLMUSDT")
    parser.add_argument("--symbols", help="Comma-separated symbols, for example DOGEUSDT,TLMUSDT")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of Chinese text report")
    args = parser.parse_args(argv)

    symbols = parse_symbols(args)
    reports: List[SymbolReport] = []
    for symbol in symbols:
        try:
            reports.append(analyze_symbol(symbol))
            time.sleep(0.15)
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






