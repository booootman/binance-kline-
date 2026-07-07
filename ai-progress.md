# AI Progress

## 2026-07-07 Project restructure

- Reorganized the dashboard into a source/web/runtime/scripts/docs layout.
- Moved Python implementation to `src/bian_dashboard/`.
- Moved frontend files to `web/`.
- Moved runtime cache to `runtime/market_cache.json`.
- Moved older restore files and generated artifacts to `backups/`.
- Moved the old full project copy to `archive/binance-kline-legacy/`.
- Kept root compatibility entries: `bian.py` and `server.py`.
- Added verification and startup scripts under `scripts/`.

## Verification

- Run `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Restart the local server after this restructure so it serves files from `web/`.

## 2026-07-07 Strategy trust upgrade

- Added directional historical signal backtest windows for 5m, 15m, and 1h max profit, max drawdown, hit rate, expectancy, and profit/drawdown ratio.
- Calibrated displayed confidence with historical quality score plus risk-gate penalties.
- Marked each advice candle as `实时预判` or `收盘确认`.
- Added small-coin/extreme-volatility gates: `正常`, `禁止半仓`, and `禁止开仓`.
- Added entry trigger confirmation using 1m volume, 1m structure, distance to entry, and book spread.
- Made stop hints and grid stops legal prices using tick size, recent high/low, and ATR so `stop_hint` should not become 0.
- Surfaced K-line state, risk gate, backtest summary, and trigger status in the dashboard advice area.

## Strategy Verification

- Passed `node --check web/assets/charts.js`.
- Passed `python -B -m py_compile src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `python -B bian.py --symbols DOGEUSDT,TLMUSDT --json` with TLM risk gate at `禁止开仓` and `zeroStop=false`.
- Restarted the local server on `127.0.0.1:8000`; dashboard, JS, and `/api/market?symbols=DOGEUSDT,TLMUSDT` returned HTTP 200.
- GitNexus change detection was attempted but unavailable because the local GitNexus index does not include the `bian` repository label.

## 2026-07-07 Confidence split and global gate fix

- Split signal scoring into `direction_score` and `execution_score`.
- Kept `confidence` as the executable/opening score so position sizing no longer uses pure direction quality.
- Tightened entry trigger confirmation with an ATR-based near-entry threshold plus 1m retest/touch confirmation.
- Propagated a global `禁止开仓` risk gate to every timeframe advice card.
- Updated dashboard labels to show direction quality and opening execution separately.

## Confidence Split Verification

- Passed `python -B -m py_compile src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Smoke test showed DOGE direction score can remain high while execution score is lowered when the entry trigger has not arrived.
- Smoke test showed TLM inherits `禁止开仓` across all timeframe advice cards.

## 2026-07-07 P0 trust hardening

- Tightened historical backtest accounting so samples walk future bars in order, stop out before later profit, and subtract estimated taker fee plus slippage.
- Added backtest fields for stop-out count/rate, average loss, net expectancy, and estimated round-trip cost.
- Added `--fees-bps` and `--slippage-bps` CLI options for conservative backtest cost assumptions.
- Added closed-candle indicator references so realtime trigger logic can avoid treating the moving 1m candle as settled.
- Forced unclosed 1m trigger checks to `watch` instead of `confirmed`.
- Added Binance depth top5/top20 collection; trigger confirmation now checks adaptive spread threshold, top5 depth, and direction-supporting depth imbalance.
- Added funding-rate crowding into the risk gate.
- Strengthened the dashboard signal banner for `禁止开仓`, `禁止半仓`, realtime-prejudge, and position-conflict cases.

## P0 Trust Verification

- Passed `python -B -m py_compile src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `python -B bian.py --symbols DOGEUSDT,TLMUSDT --json`; smoke output showed TLM `risk_gate=禁止开仓`, `execution_score=24`, non-zero stop hints, and backtest cost/stop fields.
- GitNexus status was checked; this repository is not indexed, so change detection remains unavailable until `npx gitnexus analyze` is run.

## 2026-07-07 Backtest/server correction pass

- Aligned historical signal side selection with online scoring via a shared `SIGNAL_SIDE_THRESHOLD`.
- Added explicit ATR/volume filtered sample counts and frontend wording so backtest win rate is not mistaken for all-market performance.
- Split `expectancy_pct` into gross expectancy and `net_expectancy_pct` after estimated fee/slippage.
- Kept drawdown as price-path drawdown instead of subtracting execution cost from it.
- Added reusable analyzer backtest cache at `runtime/backtest_cache.json`, and passed it through `server.py`.
- Extended realtime SSE from bookTicker-only to bookTicker plus futures `depth20@500ms`, streaming depth imbalance and top5/top20 depth.
- Added `/api/market` error classification: invalid symbols return 400, rate-limit/timeout/network paths may return stale cache, hard upstream errors return error status.

## Backtest/server Correction Verification

- Passed `python -B -m py_compile src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `python -B bian.py --symbols DOGEUSDT,TLMUSDT --json`; output includes `filtered_out_count`, gross/net expectancy, and unchanged price-path drawdown semantics.
- Restarted local server as PID `57788`.
- `/api/market?symbols=DOGEUSDT,TLMUSDT` returned HTTP 200.
- `/api/market?symbols=NOTAREALUSDT` returned HTTP 400.
- `/api/realtime-prices?symbols=DOGEUSDT` streamed `depth_imbalance`, `bid_depth_top5_usd`, and `ask_depth_top5_usd`.

## 2026-07-07 Review fix pass

- Fixed `/api/market` partial-symbol false success: requested symbols are now compared with returned analyzer symbols, and missing symbols return an error instead of a cached HTTP 200 success.
- Added `missing_symbols` and `returned_symbols` metadata to partial-symbol API errors.
- Hardened `runtime/backtest_cache.json` writes with a lock file and per-process temp filenames so concurrent analyzer subprocesses do not overwrite the shared temp file.
- Moved TLM extreme-rally downgrade before side/backtest/stop selection and capped downgraded direction scores, so a downgraded bearish/observe signal no longer reuses a previous long-side backtest or long stop.

## Review Fix Verification

- Passed `python -B -m py_compile src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `python -B bian.py --symbols DOGEUSDT,TLMUSDT --json`.
- Restarted local server as PID `40332`.
- `/api/market?symbols=DOGEUSDT,TLMUSDT` returned HTTP 200 with two data rows.
- `/api/market?symbols=NOTAREALUSDT` returned HTTP 400.
- `/api/market?symbols=DOGEUSDT,NOTAREALUSDT` returned HTTP 400 with `missing_symbols=["NOTAREALUSDT"]` and `returned_symbols=["DOGEUSDT"]`.
- `git diff --check` reported only existing CRLF conversion warnings.

## 2026-07-07 Account risk and sizing pass

- Added backend risk-budget sizing for each timeframe advice and top signal quality: `risk_budget_pct`, `stop_distance_pct`, `suggested_size_pct`, `max_size_pct`, `max_loss_pct`, `allowed`, and explanatory note.
- Changed frontend position display to use backend `suggested_size_pct` percentages instead of score-to-star position labels.
- Added browser-local account risk fuse controls for daily loss, consecutive losses, and max single loss.
- Account fuse now blocks all frontend opening suggestions at `dailyLossPct >= 3`, `consecutiveLosses >= 3`, or `singleLossPct >= 1.5`, lasting until the next local day unless manually reset.
- Added same-side altcoin correlation exposure warning across loaded symbols.
- Frontend now consumes `payload.stale`, `warning`, and `error_type`; stale snapshots turn the LIVE badge into delayed status and show warnings in the signal/risk areas.
- Fixed realtime bookTicker updates so missing depth fields no longer overwrite the latest depth20 snapshot.
- Preserved realtime bid/ask/depth fields across strategy refreshes to avoid brief depth flicker after DATA is rebuilt.

## Account Risk Verification

- Passed `python -B -m py_compile src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `python -B bian.py --symbols DOGEUSDT,TLMUSDT --json`; output includes `risk_sizing` for top signal and every timeframe advice.
- Restarted local server as PID `25728`.
- `/api/market?symbols=DOGEUSDT,TLMUSDT` returned HTTP 200 with `risk_sizing` fields.
- `/api/market?symbols=DOGEUSDT,NOTAREALUSDT` still returned HTTP 400 with `missing_symbols`.

## 2026-07-07 TradingView K-line panel

- Added a TradingView Advanced Chart panel below the main signal/KPI row.
- The panel maps the active dashboard symbol to Binance USDT perpetual TradingView symbols such as `BINANCE:DOGEUSDT.P`.
- Added 1m, 5m, 15m, 1h, 4h, and 1D interval controls, persisted in browser localStorage.
- Added direct external links for opening the active symbol on TradingView and Binance futures.
- Kept the panel as a visual K-line reference only; strategy math still comes from local Binance API analysis.

## TradingView K-line Verification

- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## 2026-07-07 Optional MySQL/Redis storage

- Added `src/bian_dashboard/storage.py` with optional MySQL and Redis integration.
- Added MySQL persistence for dashboard preferences: custom symbols, removed symbols, position state, account risk, signal history, and TradingView interval.
- Added MySQL strategy snapshot writes after successful `/api/market` analysis.
- Added Redis short cache for market payloads and latest realtime price/depth snapshots.
- Added `GET /api/preferences`, `POST /api/preferences`, and `GET /api/storage-status`.
- Updated frontend preference reads/writes to sync with the backend while keeping localStorage fallback.
- Added `docs/storage.md` with MySQL/Redis environment variable configuration.

## Optional Storage Verification

- Passed `python -B -m py_compile src/bian_dashboard/storage.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## 2026-07-07 Docker deployment

- Added Docker deployment files for the dashboard API, isolated MySQL storage, and isolated Redis cache.
- Added `BIAN_HOST` and `BIAN_PORT` server configuration so the app can bind to `0.0.0.0` inside a container while keeping the local default at `127.0.0.1:8000`.
- Added Docker deployment notes under `docs/docker-deploy.md`.

## Docker Deployment Verification

- Passed `python -B -m py_compile src/bian_dashboard/storage.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Local `docker compose config` was skipped because Docker is not installed on this Windows machine.
- Server `docker compose config` passed under `/opt/bian-dashboard`.
- Server `docker compose up -d --build` started `bian-dashboard`, `bian-dashboard-mysql`, and `bian-dashboard-redis`; all containers reported healthy.
- Public `GET http://159.223.91.36:8000/binance-futures-dashboard.html` returned HTTP 200.
- Public `GET http://159.223.91.36:8000/api/storage-status` reported MySQL and Redis available.
- Public `GET http://159.223.91.36:8000/api/market?symbols=DOGEUSDT,TLMUSDT` returned `stale=false`.
- Public `GET http://159.223.91.36:8000/api/realtime-prices?symbols=DOGEUSDT` streamed SSE data with `connected=true`.

## 2026-07-07 Database-backed login gate

- Added MySQL-backed auth tables for dashboard users and sessions.
- Passwords are stored as PBKDF2-SHA256 hashes; session cookies store only random tokens, with hashed tokens persisted in MySQL.
- Added `/login`, `/api/login`, `/api/logout`, and `/api/health`.
- Added server-side auth checks for dashboard pages, APIs, and realtime SSE endpoints.
- Added a dashboard logout button.
- Added `/api/auth/password` plus a dashboard password-change modal; the current password is required, the new password is stored as a fresh hash, and other sessions for the same user are invalidated.
- Added admin-only `/api/auth/users` plus a dashboard registration modal for creating additional `user` or `admin` accounts; public anonymous registration is intentionally not exposed.
- Added per-IP login failure lockout.
- Docker and the Python deploy script now configure a bootstrap admin user only when the auth users table is empty.

## Login Gate Verification

- Passed `python -B -m py_compile src/bian_dashboard/storage.py src/bian_dashboard/server.py scripts/deploy.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed password-hash smoke test: generated PBKDF2-SHA256 hash validates the right password and rejects a wrong password.
- Passed auth username validation smoke test for allowed and rejected account names.
- Passed local smoke test: `/api/health` returns 200 without auth, dashboard HTML redirects to `/login`, and `/login` renders.

## 2026-07-07 Opening guard panel upgrade

- Reworked the former manual-only account fuse panel into an automatic opening-risk decision panel.
- The panel now shows allow/wait/block status based on current strategy risk, trigger confirmation, candle state, stop validity, realtime price freshness, and manual fuse status.
- Kept daily-loss/consecutive-loss/single-loss fields as a collapsed manual fuse fallback because the app is not connected to exchange account equity.
- The opening guard refreshes lightly with realtime price updates without interrupting manual fuse input.
