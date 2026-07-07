# Delivery Review

## Change Summary

The project root was reorganized into a maintainable dashboard project layout while preserving the old root commands. The strategy layer now also exposes historical signal-quality checks, risk gates, trigger confirmation, and legal stop hints so the dashboard is more honest about when to wait.

This pass tightens the P0 trust issues from `docs/improvement-suggestions.md`: historical backtest accounting is less optimistic, realtime candles cannot silently become executable confirmations, funding participates in the risk gate, trigger confirmation checks order-book depth instead of only the best bid/ask spread, and server refresh/runtime behavior is less fragile.

## Primary Changes

- Python implementation moved to `src/bian_dashboard/`.
- Frontend files moved to `web/`.
- Runtime cache moved to `runtime/`.
- Restore files moved to `backups/`.
- Historical full copy moved to `archive/`.
- Startup and verification scripts added to `scripts/`.
- Analyzer adds 5m/15m/1h historical signal backtest metrics for long and short signals.
- Confidence is calibrated by historical quality and risk-gate penalties.
- Advice marks unclosed candles as `实时预判` and closed candles as `收盘确认`.
- Extreme ATR/BOLL conditions can downgrade to `禁止半仓` or `禁止开仓`.
- Entry trigger confirmation checks 1m volume, 1m structure, book spread, and distance to entry.
- Stops are rounded with tick size and recent high/low/ATR guards to avoid invalid 0 stop hints.
- Dashboard advice cards surface K-line state, risk gate, backtest summary, and trigger status.
- Direction quality and executable opening score are separated; `confidence` remains the execution score for position sizing.
- Global `禁止开仓` now propagates into every timeframe advice card.
- Entry trigger confirmation now uses an ATR-based near-entry threshold and 1m retest/touch check.
- Backtest windows now model stop-first trade paths, estimated taker fees/slippage, stop-out count/rate, average loss, and net expectancy.
- `build_trigger_check` now uses an ATR-adaptive spread threshold plus depth top5 USD and depth imbalance checks.
- Unclosed 1m trigger candles are forced to `watch` instead of `confirmed`.
- Funding-rate crowding now downgrades risk gates.
- Dashboard top banner now shows stronger `禁止开仓`, `禁止半仓`, realtime-prejudge, and position-conflict warnings.
- Historical backtest side selection now shares the same threshold as online scoring.
- Backtest `expectancy_pct` is gross outcome; `net_expectancy_pct` is after estimated fee/slippage.
- Backtest drawdown remains price-path drawdown and is no longer reduced by fee/slippage cost.
- Backtest windows expose `filtered_out_count` and sample-filter notes for ATR/volume-filtered samples.
- Analyzer/server calls reuse `runtime/backtest_cache.json` for 5-10 minute rolling backtest results.
- Realtime SSE now subscribes to futures `depth20@500ms` and streams depth imbalance plus top5/top20 depth fields.
- `/api/market` now classifies invalid requests, rate limits, timeouts, network errors, and upstream hard errors.

## Verification Plan

- Compile Python files.
- Check frontend JavaScript syntax.
- Confirm analyzer CLI help works through the root compatibility entry.
- Smoke test `python bian.py --symbols DOGEUSDT,TLMUSDT --json`.
- Confirm TLM-style extreme volatility is downgraded and stop hints remain non-zero.
- Confirm DOGE can keep high direction quality while execution score is reduced before trigger.
- Confirm TLM global `禁止开仓` is inherited across all timeframe cards.
- Confirm backtest output includes stop rate, net expectancy, and estimated cost fields.
- Confirm trigger output includes adaptive spread threshold and depth fields.
- Confirm frontend syntax after the stronger banner warnings.
- Confirm realtime SSE includes depth imbalance and top5 depth.
- Confirm invalid symbols return HTTP 400 rather than stale cache.
- Restart local server and verify dashboard/API paths.

## Human Review Notes

- Historical quality is a more conservative signal-quality score, but still not a full execution-grade backtest with funding windows, liquidation, partial fills, account equity, queue position, or portfolio exposure.
- Human review should check `src/bian_dashboard/analyzer.py`, `web/assets/charts.js`, and the updated docs before staging.
