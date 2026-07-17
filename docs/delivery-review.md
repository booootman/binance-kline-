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
- Execution confidence uses rule direction, trigger state, and effective risk-gate penalties. The 5m proxy backtest is displayed for context but does not calibrate the live opening score.
- Advice uses the latest completed interval candle (`已完成K线`) for direction while realtime prices and the forming 1m candle remain available for monitoring. This is interval completion in a 24/7 market, not a market close.
- Extreme ATR/BOLL conditions can downgrade to `禁止半仓` or `禁止开仓`.
- Entry trigger confirmation checks 1m volume, 1m structure, book spread, and distance to entry.
- Stops are rounded with tick size and recent high/low/ATR guards to avoid invalid 0 stop hints.
- Dashboard advice cards surface K-line state, risk gate, backtest summary, and trigger status.
- Direction quality and executable opening score are separated; `confidence` remains the execution score for position sizing.
- Global `禁止开仓` now propagates into every timeframe advice card.
- Entry trigger confirmation now uses an ATR-based near-entry threshold and 1m retest/touch check.
- Backtest windows now model stop-first trade paths, estimated taker fees/slippage, stop-out count/rate, average loss, and net expectancy.
- `build_trigger_check` now uses an ATR-adaptive spread threshold plus depth top5 USD and depth imbalance checks.
- Trigger confirmation uses the latest completed 1m candle plus the current futures book price, so the always-forming live candle does not make confirmation permanently unreachable.
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

- Historical quality is a non-overlapping 5m proxy using next-bar-open fills. It is not a full execution-grade or live-isomorphic backtest and does not change the opening score.
- Human review should check `src/bian_dashboard/analyzer.py`, `web/assets/charts.js`, and the updated docs before staging.

## 2026-07-16 Review Bug Remediation

- Live review now starts with the first complete 1m candle at or after publication; a mid-minute candle cannot reuse pre-publication high/low as a later entry or stop.
- Preference POSTs require a positive revision at both the HTTP and MySQL storage boundaries.
- Deployment packages use a Git-derived manifest, reject dirty worktrees by default, and never include the local `.env` or ignored files.
- Remote deployment validates a temporary release, preserves the previous directory until health passes, keeps the uploaded archive for retries, and persists `--public-port` in the remote `.env`.
- Human release review should inspect the server/storage API contract, deploy dry-run output, and all new smoke assertions before staging.

## 2026-07-16 Follow-up Bug Remediation

- Preference conflicts are reconciled against current server state; same-field stale values are discarded and only unchanged fields retry. Temporary storage failures still preserve the exact patch for retry.
- Realtime status reports offline when the upstream WebSocket has an explicit error and no fresh price, even if SSE transport remains open.
- Server preferences remove hidden default symbols before calculating capacity for up to eight custom symbols.
- Dirty deployment preserves all Git ignore sources and fails closed when an ignore file cannot be read.
- Redis password configuration now reaches the dashboard client, Redis `requirepass`, first-deploy secret generation, and authenticated healthcheck.

## 2026-07-17 Concurrency And Storage Remediation

- Preference conflict recovery now performs a three-way field comparison instead of promoting every rejected patch. In-flight-only unload beacons reuse their original revision.
- Configured preference storage returns HTTP 503 while MySQL is unavailable, so revision zero cannot trigger a stale full-browser writeback.
- Direct WebSocket workers carry a generation token; stopped workers cannot publish connection state, errors, counters, timestamps, or messages after replacement.
- Same-origin POST validation distinguishes an absent CLI source header from a present invalid source and rejects `Origin: null` or malformed values.
- Human release review should exercise two authenticated browser sessions, a MySQL interruption, and a real Binance WebSocket restart before deployment approval.

## 2026-07-17 Preference Recovery And Auth Atomicity

- Failed preference conflict reads retain a dedicated recovery record and retry GET reconciliation with backoff; unresolved patches are never promoted into POST writes.
- Explicitly unconfigured MySQL disables server preference synchronization while preserving browser localStorage behavior.
- Page unload sends the active in-flight patch and newer pending patch separately, preserving the old revision and preventing old fields from inheriting a newer revision.
- Password hash update and other-session revocation now share one MySQL transaction; session-revocation failure rolls back the password change.
- Human release review should still validate two real authenticated sessions, forced MySQL failure during password change, and browser pagehide delivery ordering.
