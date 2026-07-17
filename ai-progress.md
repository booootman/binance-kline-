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

## 2026-07-08 Live signal review loop

- Added persistent live signal review records for every directional timeframe advice after a successful `/api/market` strategy snapshot.
- Added MySQL table `bian_signal_reviews` with a `runtime/signal_reviews.json` fallback when MySQL is not configured.
- Added background evaluation for due signals using Binance USD-M 1m klines after 5m, 15m, and 1h horizons.
- Evaluation records entry reached, stop hit, same-bar stop ambiguity, max profit, max drawdown, outcome, and failure reason such as `entry_too_far`, `stop_hit_first`, `direction_wrong`, and `no_follow_through`.
- Added `GET /api/signal-reviews` for current review records and aggregate stats without blocking the request on slow Binance evaluation.
- Added a dashboard "实盘信号复盘" panel showing real sample count, hit rate, stop rate, not-triggered rate, average max profit/drawdown/outcome, and recent signal outcomes for the active symbol.

## Live Signal Review Verification

- Passed `python -B -m py_compile src/bian_dashboard/storage.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed offline evaluation smoke test for a long signal reaching entry and producing correct 5m max profit/drawdown/outcome.

## 2026-07-08 Frontend risk label correction

- Fixed frontend fallback risk labeling so low execution score, not-near-entry, or unclosed K-line states show as `等待确认`/high risk instead of hard `禁止开仓`.
- Kept true hard blocks for backend `risk_gate=禁止开仓`, account fuse, invalid entry/stop, and extreme frontend fallback conditions.

## Frontend Risk Label Verification

- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.
- The opening guard refreshes lightly with realtime price updates without interrupting manual fuse input.

## 2026-07-08 Autonomous iteration round 1 - auth hardening

- Observed backlog candidates: auth edge cases, live-signal-review calibration, deployment docs, and market refresh cost.
- Selected auth hardening as the highest-value first round because it protects the deployed multi-user dashboard without changing strategy math.
- Sanitized login `next` redirects so external URLs, protocol-relative URLs, backslash paths, and `/api/*` targets fall back to the dashboard.
- Added `BIAN_AUTH_TRUST_PROXY_HEADERS`, default off; `X-Forwarded-For` is only used when enabled and the peer is a local/private proxy.
- Added `BIAN_AUTH_SESSION_TOUCH_INTERVAL_SECONDS`, default 60 seconds, to avoid writing `last_seen_at` on every protected request.
- Hardened concurrent admin user creation so duplicate usernames return the normal duplicate-user error instead of surfacing a 500.
- Updated Docker/env/deploy documentation for the new auth controls.

## Autonomous Round 1 Verification

- Passed `python -B -m py_compile src/bian_dashboard/storage.py src/bian_dashboard/server.py scripts/deploy.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed safe login redirect smoke checks for `//evil.com`, `https://evil.com/x`, `/api/market`, and a valid dashboard URL.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 1

- Round 2 candidate: add small focused regression tests or smoke helpers for auth redirects/session behavior and signal-review record/evaluation helpers.
- Round 3 candidate: improve live signal review calibration display once enough samples exist, without changing thresholds blindly.
- Round 4 candidate: reduce `/api/market` refresh cost by separating static analysis from lightweight realtime refresh where possible.

## 2026-07-08 Autonomous iteration round 2 - offline smoke tests

- Added `scripts/smoke.py` with offline regression checks for safe login redirects, password hashing, duplicate-key detection, signal-review record creation, horizon evaluation, and aggregate stats.
- Wired the smoke tests into `scripts/verify.ps1` so the project verification now covers behavior, not only Python/JS syntax.
- Fixed `scripts/verify.ps1` native command handling: Python/Node failures now throw instead of allowing PowerShell 5 to continue to `verify ok`.
- Fixed the new smoke test's timestamp fixture so review candles line up with the generated strategy snapshot time.

## Autonomous Round 2 Verification

- Passed `python -B scripts/smoke.py`.
- Passed `python -B -m py_compile scripts/smoke.py scripts/deploy.py src/bian_dashboard/storage.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.
- Passed `git diff --check` with only CRLF conversion warnings.

## Autonomous Backlog After Round 2

- Round 3 candidate: improve startup/server observability and API health details so deployment issues are easier to diagnose.
- Round 4 candidate: add live-signal-review sample quality badges in the UI once sample counts are low/high enough to interpret.
- Round 5 candidate: reduce `/api/market` refresh cost by caching symbol metadata and separating heavy strategy refresh from light UI refresh.

## 2026-07-08 Autonomous iteration round 3 - health diagnostics

- Enhanced public `/api/health` from a plain `ok` heartbeat into a non-secret deployment diagnostic payload.
- Health now reports service uptime, bind host/port, required runtime paths, auth readiness, cookie/proxy/session-touch settings, MySQL/Redis configured/available flags, cache counts, and realtime hub count.
- `/api/health` now returns HTTP 503 when auth is enabled but MySQL is not available, making deployment health checks catch login-system outages.
- Added smoke coverage to ensure health payload shape is stable and does not expose password/bootstrap details.
- Updated Docker deployment docs to include `/api/health` verification and expected 503 behavior.

## Autonomous Round 3 Verification

- Passed `python -B scripts/smoke.py`.
- Passed `python -B -m py_compile scripts/smoke.py scripts/deploy.py src/bian_dashboard/storage.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.
- Passed `feature_list.json` parse check.

## Autonomous Backlog After Round 3

- Round 4 candidate: add live-signal-review sample quality badges and clearer low-sample warnings in the UI.
- Round 5 candidate: reduce `/api/market` refresh cost by caching symbol metadata and separating heavy strategy refresh from light UI refresh.
- Round 6 candidate: add a small authenticated diagnostics endpoint for deeper operator-only state if needed.

## 2026-07-08 Autonomous iteration round 4 - opening-guard wording

- Observed the live usability complaint that the frontend can look like every symbol is `禁止开仓`, even when the backend only means `等待确认`, `执行分低`, or `本次入场触发失败`.
- Selected opening-guard wording as the highest-value fix because it improves trading-assistant clarity without loosening backend risk gates or strategy math.
- Kept true hard blocks for account fuse, backend `risk_gate=禁止开仓`, invalid entry price, invalid stop price, and extreme frontend fallback risk.
- Changed trigger-confirmation failure from a hard red block to a wait-state warning: `本次入场触发失败，等下一次结构确认`.
- Changed the generic zero-size fallback note from `禁止开仓` to `当前不给仓位`, while preserving backend notes such as `风控阀门禁止开仓` when they are truly returned.

## Autonomous Round 4 Verification

- Passed `node --check web/assets/charts.js`.
- Passed `python -B scripts/smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 4

- Round 5 candidate: add live-signal-review sample quality badges and clearer low-sample warnings in the UI.
- Round 6 candidate: reduce `/api/market` refresh cost by caching symbol metadata and separating heavy strategy refresh from light UI refresh.
- Round 7 candidate: add an authenticated diagnostics endpoint for deeper operator-only state if deployment debugging needs it.

## 2026-07-08 Autonomous iteration round 5 - exchangeInfo cache

- Observed that one `/api/market` analyzer subprocess fetched Binance `/fapi/v1/exchangeInfo` once per symbol even though tick-size metadata is shared for all symbols in the same response.
- Selected per-process `exchangeInfo` caching as the highest-value refresh-cost improvement because it reduces Binance REST calls for multi-symbol dashboards without changing strategy scoring.
- Added `fetch_exchange_info()` with `BIAN_EXCHANGE_INFO_CACHE_TTL_SECONDS` defaulting to 3600 seconds.
- Reused a stale in-process `exchangeInfo` response if a later refresh fails, so tick-size lookup is more resilient inside the same analyzer process.
- Updated `fetch_symbol_meta()` to read from the shared exchange-info cache instead of calling Binance directly for every symbol.
- Added an offline smoke regression that monkeypatches the Binance request helper and proves DOGE/TLM metadata lookup only calls `exchangeInfo` once.

## Autonomous Round 5 Verification

- Passed `python -B scripts/smoke.py`.
- Passed `python -B -m py_compile scripts/smoke.py src/bian_dashboard/analyzer.py src/bian_dashboard/server.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 5

- Round 6 candidate: reduce `/api/market` repeated refresh cost further by serving fresh-enough disk/Redis snapshots before spawning the analyzer when appropriate.
- Round 7 candidate: add authenticated operator diagnostics for cache hit/miss, analyzer runtime, and last market error details.
- Round 8 candidate: continue refining live-signal-review calibration once enough real samples accumulate.

## 2026-07-08 Autonomous iteration round 6 - git ignore hygiene

- Observed that `.trae-html-share-packages/` was generated locally and already excluded from Docker builds, but not ignored by git, so it stayed as an untracked directory.
- Selected repo hygiene because it reduces accidental commits and keeps human git review focused on real source/deployment changes.
- Added `.trae-html-share-packages/`, common Python tool caches, and `runtime/*.lock` to `.gitignore`.
- Confirmed `.trae-html-share-packages/` disappeared from `git status --short` while intended untracked source/docs/deploy files remained visible for human review.

## Autonomous Round 6 Verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.
- `git status --short` no longer lists `.trae-html-share-packages/`.

## Autonomous Backlog After Round 6

- Round 7 candidate: add authenticated operator diagnostics for cache hit/miss, analyzer runtime, and last market error details.
- Round 8 candidate: reduce `/api/market` repeated refresh cost further by serving fresh-enough disk/Redis snapshots before spawning the analyzer when appropriate.
- Round 9 candidate: continue refining live-signal-review calibration once enough real samples accumulate.

## 2026-07-08 Autonomous iteration round 7 - authenticated diagnostics

- Observed that public `/api/health` is intentionally shallow, while deployment debugging often needs cache, analyzer, and realtime WebSocket state after login.
- Selected an authenticated diagnostics endpoint as the highest-value reliability improvement because it helps distinguish cache misses, analyzer pressure, storage state, and WebSocket issues without exposing secrets publicly.
- Added `diagnostics_payload()` with memory cache ages, market lock keys, analyzer path/runtime settings, realtime hub/client/error state, and optional storage status.
- Added authenticated `GET /api/diagnostics`; it is routed after `require_auth`, unlike public `/api/health`.
- Added offline smoke coverage for diagnostics payload shape and secret-word leakage checks.
- Updated Docker deployment docs to clarify that `/api/health` is public, while `/api/market`, `/api/storage-status`, and `/api/diagnostics` require a logged-in dashboard session.

## Autonomous Round 7 Verification

- Passed `python -B -m py_compile scripts/smoke.py src/bian_dashboard/server.py src/bian_dashboard/analyzer.py bian.py server.py`.
- Passed `python -B scripts/smoke.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 7

- Round 8 candidate: reduce `/api/market` repeated refresh cost further by serving fresh-enough disk snapshots before spawning the analyzer when appropriate.
- Round 9 candidate: add frontend/admin access to diagnostics if operator UX needs it, otherwise keep endpoint-only.
- Round 10 candidate: continue refining live-signal-review calibration once enough real samples accumulate.

## 2026-07-08 Autonomous iteration round 8 - fresh disk cache reuse

- Observed that after a server restart the in-memory cache is empty, even if `runtime/market_cache.json` contains a snapshot generated only seconds ago.
- Selected fresh disk cache reuse as a safe refresh-cost improvement because it avoids unnecessary analyzer subprocesses immediately after restart without weakening stale-data rules.
- Added `payload_age_seconds()` and `fresh_disk_cache()` helpers.
- `/api/market` now checks memory cache, Redis cache, then fresh disk cache before spawning the analyzer.
- Disk cache is only treated as fresh when `generated_at` parses and age is within `CACHE_TTL_SECONDS`; older disk cache remains fallback-only for allowed error paths.
- Returned fresh disk cache responses include `cache_hit=true` and `disk_hit=true`.
- Added offline smoke coverage for payload age calculation.

## Autonomous Round 8 Verification

- Passed `python -B -m py_compile scripts/smoke.py src/bian_dashboard/server.py src/bian_dashboard/analyzer.py bian.py server.py`.
- Passed `python -B scripts/smoke.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 8

- Round 9 candidate: add frontend/admin access to diagnostics if operator UX needs it, otherwise keep endpoint-only.
- Round 10 candidate: improve realtime WebSocket sharing model if different users watch overlapping but non-identical symbol lists.
- Round 11 candidate: continue refining live-signal-review calibration once enough real samples accumulate.

## 2026-07-08 Autonomous iteration round 9 - realtime hub key normalization

- Observed that realtime hubs already sort symbols inside `ensure()`, but `_realtime_hubs` was keyed by the original request order.
- Selected symbol-set key normalization because two users watching the same symbols in different orders should not open duplicate Binance WebSocket streams.
- Added `realtime_cache_key()` that sorts and deduplicates symbols for the hub dictionary key.
- Updated `realtime_hub_for()` to use the normalized realtime key while preserving the requested symbol order in each SSE snapshot response.
- Added offline smoke coverage proving `DOGEUSDT,TLMUSDT` and `TLMUSDT,DOGEUSDT` share the same realtime key.

## Autonomous Round 9 Verification

- Passed `python -B -m py_compile scripts/smoke.py src/bian_dashboard/server.py src/bian_dashboard/analyzer.py bian.py server.py`.
- Passed `python -B scripts/smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 9

- Round 10 candidate: evaluate deeper realtime sharing for overlapping but non-identical symbol lists; defer if risk is too high for one round.
- Round 11 candidate: add frontend/admin access to diagnostics if operator UX needs it, otherwise keep endpoint-only.
- Round 12 candidate: continue refining live-signal-review calibration once enough real samples accumulate.

## 2026-07-08 Autonomous iteration round 10 - admin diagnostics entry

- Evaluated deeper realtime sharing for overlapping but non-identical symbol lists and deferred it because dynamic stream merge/split is higher risk than a one-round change.
- Selected admin diagnostics access as the next highest-value operator UX improvement because `/api/diagnostics` existed but had no discoverable dashboard entry.
- Added `is_admin_user()` and restricted `GET /api/diagnostics` to admin role when auth is enabled.
- Added an admin-only `诊断` button in the header that opens `/api/diagnostics` in a new tab using the existing session cookie.
- Added frontend binding for the diagnostics button and smoke coverage for the admin-role helper.
- Updated Docker deployment docs to state that `/api/diagnostics` requires admin role.

## Autonomous Round 10 Verification

- Passed `python -B -m py_compile scripts/smoke.py src/bian_dashboard/server.py src/bian_dashboard/analyzer.py bian.py server.py`.
- Passed `python -B scripts/smoke.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 10

- Round 11 candidate: continue refining live-signal-review calibration once enough real samples accumulate.
- Round 12 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 13 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.

## 2026-07-08 Autonomous iteration round 11 - frontend debug cleanup

- Ran a lightweight risk scan for TODO/FIXME/debug logging/secrets after the diagnostics and cache changes.
- Observed one leftover frontend `console.log('[dashboard] live data + custom symbols loaded @', GEN)` in the dashboard boot path.
- Removed the production debug log so browser consoles stay focused on real errors.

## Autonomous Round 11 Verification

- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.
- Confirmed `rg -n "console\\.log|debugger" web/assets/charts.js` returns no matches.

## Autonomous Backlog After Round 11

- Round 12 candidate: continue refining live-signal-review calibration once enough real samples accumulate.
- Round 13 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 14 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.

## 2026-07-08 Autonomous iteration round 12 - deploy check-market auth fix

- Observed during deployment-script review that `scripts/deploy.py --check-market` still curls protected `/api/market` directly.
- Since auth is enabled by default, that check can return 401 and falsely fail an otherwise healthy deployment.
- Updated the remote deploy script to run `/api/market` curl only when `BIAN_AUTH_ENABLED` is disabled.
- When auth is enabled, deploy now prints a skip message and leaves market verification to a logged-in browser/session-cookie check.
- Updated Docker deployment docs to document the `--check-market` behavior.

## Autonomous Round 12 Verification

- Passed `python -B -m py_compile scripts/deploy.py scripts/smoke.py src/bian_dashboard/server.py src/bian_dashboard/analyzer.py bian.py server.py`.
- Passed `node --check web/assets/charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 12

- Round 13 candidate: continue refining live-signal-review calibration once enough real samples accumulate.
- Round 14 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 15 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.

## 2026-07-08 Autonomous iteration round 13 - README refresh

- Observed that the README lagged behind the current project shape: it did not clearly mention realtime prices, K-line reference, live signal review, login, MySQL/Redis storage, smoke tests, or Docker deployment.
- Selected README refresh because it improves first-run usability and human review without touching runtime behavior.
- Updated the README with concise Chinese sections for quick start, project structure, login/storage, verification, deployment, and trading-risk positioning.

## Autonomous Round 13 Verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 13

- Round 14 candidate: continue refining live-signal-review calibration once enough real samples accumulate.
- Round 15 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 16 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.

## 2026-07-08 Autonomous iteration round 14 - verify script coverage

- Observed that `scripts/verify.ps1` runs smoke tests but did not include `scripts/deploy.py` or `scripts/smoke.py` in the Python compile list.
- Selected verification coverage because deployment script changes should be caught by the main project verification command, not only by manual checks.
- Added `scripts/deploy.py` and `scripts/smoke.py` to the `python -B -m py_compile` step in `scripts/verify.ps1`.

## Autonomous Round 14 Verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`.

## Autonomous Backlog After Round 14

- Round 15 candidate: continue refining live-signal-review calibration once enough real samples accumulate.
- Round 16 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 17 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.

## 2026-07-08 Autonomous iteration round 15 - first-run auth readiness

- Observed that direct `docker compose up` without a prepared `.env` can enable auth with an empty first-admin password.
- In a fresh MySQL database, that creates an unusable login page: no auth users exist, and the server cannot create the first admin account.
- Selected first-run auth readiness as the highest-value reliability fix because it prevents deployment deadlock without changing strategy math.
- Reworked storage auth status to report non-secret readiness fields: `has_users`, `first_admin_secret_configured`, `can_create_first_admin`, `login_ready`, and `issue`.
- Removed `bootstrap_user` from storage/diagnostic auth status so diagnostics do not expose the bootstrap username.
- Updated `/api/health` so auth readiness is false when auth is enabled and the system has neither existing users nor a first-admin secret.
- The Docker healthcheck now catches that state through HTTP 503 and reports `auth.issue=first_admin_secret_missing`.
- Added smoke coverage for the auth-readiness failure shape and redaction checks.
- Updated `.env.example`, `README.md`, `docs/docker-deploy.md`, and `feature_list.json` with the first-run auth requirement.

## Autonomous Round 15 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\storage.py src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 15

- Round 16 candidate: continue refining live-signal-review calibration once enough real samples accumulate.
- Round 17 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 18 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.

## 2026-07-08 Autonomous iteration round 16 - live review sample quality

- Observed that the live signal review panel already shows sample-quality warnings, but a tiny sample could still render a green hit rate.
- Also observed that if only one horizon had samples, the overall quality could look stronger than it should because zero-sample horizons were ignored.
- Selected this frontend trust fix because it directly addresses real-money decision confidence without changing strategy thresholds.
- Updated overall review quality so incomplete 5m/15m/1h horizon coverage shows `周期样本不全`.
- Gated green hit-rate coloring behind both enough samples and enough triggered entries: at least 20 samples and 10 triggered signals.
- Added triggered count and per-horizon quality text to each review stat card so users can see whether the rate is evidence or just early cases.
- Updated `feature_list.json` to document the low-sample/incomplete-horizon guardrail.

## Autonomous Round 16 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 16

- Round 17 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 18 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.
- Round 19 candidate: continue live signal review calibration by grouping results by risk gate / trigger status / ATR regime once enough samples exist.

## 2026-07-08 Autonomous iteration round 17 - live review segmented calibration

- Observed that live signal review stats were only aggregated by horizon, which makes it hard to tell whether failures cluster in specific risk gates, trigger states, or candle states.
- Selected segmented calibration as the highest-value next step because it helps improve signal trust with evidence instead of guessing new thresholds.
- Added backend `stats.segments` under `/api/signal-reviews`, grouped by `risk_gate`, `trigger_status`, and `candle_state`.
- Each segment reports sample count, triggered count, hit rate, stop rate, not-triggered rate, average max profit/drawdown/outcome, and failure reasons.
- Preserved the existing `per_horizon` response shape for compatibility.
- Added frontend `分桶校准` chips below the horizon summary, showing the largest trigger/risk/K-line buckets with sample, trigger, hit, and outcome values.
- Added smoke coverage to ensure risk-gate and trigger-status segments are counted.

## Autonomous Round 17 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 17

- Round 18 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 19 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.
- Round 20 candidate: extend live-review calibration with ATR/trend buckets after confirming the analyzer payload stores enough regime fields.

## 2026-07-08 Autonomous iteration round 18 - invalid signal review accounting

- Observed that invalid review records such as illegal entry/stop appear in the recent-record list but were not counted in `top_failures` or summary quality.
- Selected this fix because hiding invalid generated signals makes the live review look cleaner than reality.
- Added `invalid_records` at the top-level live-review stats.
- Added `invalid_count` and `invalid_rate_pct` to finalized review buckets and segmented calibration buckets.
- Kept invalid records out of 5m/15m/1h horizon hit-rate math, because they fail before market-outcome evaluation rather than after a horizon expires.
- Ensured segment average profit/drawdown/outcome denominators exclude invalid records so invalid signals do not become artificial 0% trades.
- Updated the live-review panel to show invalid signal count in the quality note and invalid rate inside segment chips.
- Added smoke coverage proving invalid stop failures are counted in `top_failures`.

## Autonomous Round 18 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 18

- Round 19 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 20 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.
- Round 21 candidate: extend live-review calibration with ATR/trend buckets after confirming the analyzer payload stores enough regime fields.

## 2026-07-08 Autonomous iteration round 19 - structured market regime buckets

- Investigated ATR/trend calibration feasibility and confirmed analyzer reports already include structured indicators, but review records did not persist a structured regime.
- Avoided parsing Chinese reason strings because that would make calibration brittle.
- Added `anchor_interval` to each analyzer timeframe advice so a review record knows which indicator timeframe the advice was anchored to.
- Added `market_regime` to new signal review records, derived from the anchor interval indicators.
- Regime fields include anchor interval, trend, ATR bucket, BOLL bandwidth bucket, BOLL position bucket, and raw ATR/BOLL percentages.
- Extended `/api/signal-reviews` segmented calibration to include `anchor_interval`, `trend_regime`, `atr_regime`, `boll_width_regime`, and `boll_position_regime`.
- Updated frontend segment chips to show the largest ATR/trend/BOLL buckets along with trigger/risk/K-line buckets.
- Added smoke coverage for structured market regime capture and ATR/trend segment counts.

## Autonomous Round 19 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\analyzer.py src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 19

- Round 20 candidate: add admin diagnostics UI rendering if raw JSON becomes inconvenient.
- Round 21 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.
- Round 22 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.

## 2026-07-08 Autonomous iteration round 20 - admin diagnostics modal

- Observed that the admin diagnostics endpoint existed, but the dashboard button opened raw JSON directly.
- Selected diagnostics UI because it improves deployment and runtime troubleshooting without changing trading logic.
- Added an admin diagnostics modal that summarizes service status, MySQL/Redis/Auth readiness, memory cache, analyzer settings, and realtime WebSocket hubs.
- Kept a raw `/api/diagnostics` JSON button inside the modal for deep inspection.
- Reused the existing modal system and admin-only button behavior.
- Updated `feature_list.json` to document the readable diagnostics modal.

## Autonomous Round 20 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 20

- Round 21 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.
- Round 22 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.
- Round 23 candidate: add CSV export for live signal review once the current stats shape stabilizes.

## 2026-07-08 Autonomous iteration round 21 - live review CSV export

- Selected live signal review CSV export because manual review in Excel/sheets is a practical way to audit signal quality before changing strategy thresholds.
- Added a `导出 CSV` button to the live signal review panel.
- Export uses the active symbol's currently loaded review records, avoiding a new backend endpoint or extra database query.
- CSV includes signal snapshot, symbol, advice, side, entry/stop/snapshot price, scores, risk/candle/trigger fields, structured market regime fields, invalid reason, and 5m/15m/1h outcomes.
- Added UTF-8 BOM so Chinese fields open correctly in common spreadsheet tools.

## Autonomous Round 21 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 21

- Round 22 candidate: revisit overlapping realtime WebSocket sharing with a dedicated design if connection count becomes a real bottleneck.
- Round 23 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.
- Round 24 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.

## 2026-07-08 Autonomous iteration round 22 - realtime subset hub reuse

- Observed that realtime hubs already normalize exact symbol sets, but overlapping requests can still open duplicate Binance WebSocket streams.
- Chose a low-risk subset-only reuse improvement instead of dynamic hub merge/split, because full migration would be higher risk.
- Added `realtime_hub_for_request()` so a request such as `DOGEUSDT` can reuse an already-active `DOGEUSDT,TLMUSDT` hub.
- Kept exact hub behavior unchanged and avoided reusing inactive supersets.
- SSE payloads now include `hub_key` and `sharing` (`exact`, `new`, or `superset`) for diagnostics.
- Added offline smoke coverage proving active superset reuse, exact-match reuse, and inactive-superset rejection.

## Autonomous Round 22 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 22

- Round 23 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.
- Round 24 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 25 candidate: add a lightweight diagnostics counter for realtime sharing modes if operator visibility is still insufficient.

## 2026-07-08 Autonomous iteration round 23 - ATR-adaptive signal proximity alerts

- Observed that the main signal banner still used fixed percentage thresholds for near-entry, near-stop, and chase warnings.
- Selected ATR-adaptive frontend warnings because DOGE/TLM-style volatility differences make fixed 0.25%/0.7%/1% thresholds either too noisy or too late.
- Added a shared frontend distance model using the selected advice ATR, with capped thresholds for stop danger, near-entry, watch-entry, chase, and strategy snapshot drift.
- Entry distance now shows both raw percent and ATR multiple.
- Main signal alerts and the opening-guard panel warn when realtime price has drifted too far from the strategy snapshot, reducing stale-signal confidence without changing backend strategy math.

## Autonomous Round 23 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `python -B scripts\smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 23

- Round 24 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.
- Round 25 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 26 candidate: add a lightweight diagnostics counter for realtime sharing modes if operator visibility is still insufficient.

## 2026-07-08 Autonomous iteration round 24 - realtime sharing diagnostics

- Observed that SSE payloads reported the current sharing mode, but the admin diagnostics view had no aggregate signal for whether multiple users were actually reusing WebSocket hubs.
- Selected realtime sharing diagnostics because it helps operate the deployed dashboard and verify that two users do not unnecessarily create duplicate Binance connections.
- Added process-local counters for `exact`, `new`, and `superset` realtime sharing modes.
- `/api/diagnostics` now reports realtime sharing counts, total SSE requests, reused requests, and reuse rate.
- Added offline smoke coverage for the diagnostics field and sharing-rate calculation.

## Autonomous Round 24 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 24

- Round 25 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.
- Round 26 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 27 candidate: add a small admin UI row for realtime sharing counts if raw diagnostics visibility is not enough.

## 2026-07-08 Autonomous iteration round 25 - diagnostics sharing UI

- Observed that realtime sharing counters were available in `/api/diagnostics`, but the readable admin diagnostics modal still hid them unless the operator opened raw JSON.
- Selected the UI display because it makes multi-user Binance WebSocket reuse visible during normal admin checks.
- Added realtime reuse rate and exact/new/superset mode counts to the diagnostics modal realtime card.
- Kept this as display-only; no realtime connection behavior changed.

## Autonomous Round 25 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `python -B scripts\smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 25

- Round 26 candidate: use accumulated live-review segment stats to propose threshold changes, but only after enough real samples exist.
- Round 27 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 28 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.

## 2026-07-08 Autonomous iteration round 26 - live-review calibration guardrails

- Observed that live-review segments show useful buckets, but the dashboard still did not clearly separate "enough evidence to calibrate" from "early cases only".
- Selected calibration guardrails because they reduce false confidence without changing strategy thresholds.
- Added backend `stats.calibration` under `/api/signal-reviews`.
- Calibration candidates are emitted only for segment buckets with at least 30 samples and 10 triggered signals.
- Weak buckets can be marked for manual downgrade review when invalid rate, stop rate, hit rate, or expectancy is poor; strong buckets can support confidence only when hit rate, stop rate, and expectancy are all favorable.
- Added a frontend calibration panel that says "校准样本不足" until those evidence thresholds are met.
- Added smoke coverage for insufficient-sample behavior and candidate generation.

## Autonomous Round 26 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 26

- Round 27 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 28 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 29 candidate: add a small admin/runtime note for signal-review evaluation backlog if due records start accumulating.

## 2026-07-08 Autonomous iteration round 27 - signal-review diagnostics

- Observed that the live-review UI can look empty or low-sample while the backend may have pending records waiting for horizon evaluation.
- Selected diagnostics visibility because it helps distinguish "no signals yet" from "evaluation backlog" without changing strategy math.
- Added `signal_review_diagnostics()` to report sampled review records, pending/evaluated/invalid counts, due-pending backlog, calibration status, and top failures.
- `/api/diagnostics` now includes the signal-review diagnostics block without triggering network evaluation.
- The admin diagnostics modal now shows signal-review record count, pending count, due backlog, and calibration status.
- Added smoke coverage with fake storage to verify due-pending and invalid-review diagnostics.

## Autonomous Round 27 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 27

- Round 28 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 29 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 30 candidate: add a small stale/evaluation-age warning inside the live-review panel if due-pending backlog becomes visible in diagnostics.

## 2026-07-08 Autonomous iteration round 28 - live-review due backlog visibility

- Observed that due-pending review backlog was visible in admin diagnostics but not in the live-review panel itself.
- Selected panel visibility because users need to distinguish "no samples yet" from "signals are due but background evaluation has not caught up".
- Added `due_pending` to `/api/signal-reviews` stats.
- The live-review quality note now warns when records are old enough to evaluate but still pending.
- Diagnostics reuses the same stats value for consistency.

## Autonomous Round 28 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 28

- Round 29 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 30 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 31 candidate: inspect runtime evaluation scheduling if due-pending backlog appears during live use.

## 2026-07-08 Autonomous iteration round 29 - signal-review evaluator debounce

- Observed that every market refresh and signal-review API read could start a background evaluation thread even when another evaluator had just started or was still running.
- Selected evaluator debounce because multi-user page refreshes should not create redundant background threads just to immediately skip on the evaluation lock.
- Added `BIAN_SIGNAL_REVIEW_TRIGGER_MIN_INTERVAL_SECONDS`, default 20 seconds.
- `trigger_signal_review_evaluation()` now skips when an evaluator is already running or was triggered recently, while still allowing forced triggers for tests/tools.
- Added evaluator counters and last-run state to diagnostics: trigger count, started thread count, recent/running skips, last result, and running status.
- The admin diagnostics modal now shows evaluator state under the live-review diagnostics card.
- Added smoke coverage for debounce behavior.

## Autonomous Round 29 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.

## Autonomous Backlog After Round 29

- Round 30 candidate: add alert/notification controls for realtime entry/stop proximity if UX priority rises.
- Round 31 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 32 candidate: inspect runtime evaluation scheduling again if due-pending backlog appears despite debounce.

## 2026-07-08 Autonomous iteration round 30 - signal alert sound toggle

- Observed that the dashboard already shows ATR-adaptive entry/stop warnings, but a trader could miss them while watching another part of the screen.
- Selected a default-off browser-local alert toggle because it improves practical watch usability without making signals more aggressive or changing strategy math.
- Added an `ALERT ON/OFF` control to the main signal banner.
- When enabled, visible near-entry or near-stop signal warnings can play short Web Audio cues.
- Added per-symbol/message cooldown and de-duplication so high-frequency realtime price updates do not spam sounds.
- Kept the preference in browser localStorage only; it is not shared across users or persisted to server preferences.

## Autonomous Round 30 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 30

- Round 31 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 32 candidate: inspect runtime evaluation scheduling again if due-pending backlog appears despite debounce.
- Round 33 candidate: add a visual mute/alert state to diagnostics only if operators need to audit browser-side settings.

## 2026-07-08 Autonomous iteration round 31 - manual live-review evaluation trigger

- Observed that due-pending live-review backlog is visible, but users had no explicit action to request a catch-up evaluation.
- Selected a manual evaluation trigger because it improves operational control without changing signal scoring or strategy thresholds.
- Added `POST /api/signal-reviews/evaluate` to force one background live-review evaluation attempt and return evaluator status.
- Kept the evaluator lock strict: forced triggers bypass recent-trigger debounce but still skip when another evaluator is already running.
- Added a `评估` button to the live-review panel; it triggers the backend evaluation and then refreshes the active symbol review data.
- Added smoke coverage for the forced-trigger/running-lock behavior.

## Autonomous Round 31 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 31

- Round 32 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 33 candidate: inspect runtime evaluation scheduling again if due-pending backlog appears despite debounce.
- Round 34 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.

## 2026-07-08 Autonomous iteration round 32 - live-review evaluator status in panel

- Observed that `/api/signal-reviews` returns evaluator scheduling status, but the live-review panel only showed sample quality and backlog counts.
- Selected a frontend visibility improvement so users can tell whether review evaluation started, is cooling down, is already running, or cannot run because storage is unavailable.
- Added a compact evaluator-status sentence to the live-review quality note.
- Included last due/updated counts when the backend has a previous evaluation result.
- Kept this display-only; no signal math, thresholds, or backend scheduling behavior changed.

## Autonomous Round 32 Verification

- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `python -B scripts\smoke.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 32

- Round 33 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 34 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 35 candidate: improve frontend copy consistency if the remaining mojibake text is traced to source encoding rather than terminal rendering.

## 2026-07-08 Autonomous iteration round 33 - net-cost live-review outcomes

- Observed that live signal review outcomes used raw price movement, while historical backtest already subtracts estimated fee/slippage.
- Selected net-cost review outcomes because gross-only live review can overstate practical signal quality and make calibration too optimistic.
- Added live-review round-trip cost settings: `BIAN_SIGNAL_REVIEW_TAKER_FEE_BPS` and `BIAN_SIGNAL_REVIEW_SLIPPAGE_BPS`, defaulting to 5bps and 2bps one-way.
- `evaluate_horizon_from_klines()` now reports `max_profit_pct` and `outcome_pct` net of estimated round-trip cost after entry is reached.
- Preserved `gross_max_profit_pct`, `gross_outcome_pct`, and `estimated_cost_pct` for audit/export.
- Updated live-review CSV export to include gross and estimated-cost fields.
- Updated review panel labels to show `净最大利` and `净结果`.
- Added smoke coverage proving net/gross review fields and cost subtraction.

## Autonomous Round 33 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 33

- Round 34 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 35 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 36 candidate: document live-review cost assumptions in user-facing docs if deployment operators need to tune them.

## 2026-07-08 Autonomous iteration round 34 - live-review cost observability

- Observed that live-review outcomes now subtract estimated fee/slippage, but operators could not see or tune those assumptions from diagnostics/deploy config.
- Selected cost observability because hidden cost assumptions make signal-review calibration harder to audit.
- Added `estimated_roundtrip_cost_pct`, `taker_fee_bps`, and `slippage_bps` to signal-review diagnostics.
- Displayed the live-review cost assumptions in the admin diagnostics modal.
- Added `BIAN_SIGNAL_REVIEW_TAKER_FEE_BPS` and `BIAN_SIGNAL_REVIEW_SLIPPAGE_BPS` to `.env.example`.
- Documented those variables in `docs/docker-deploy.md`.
- Added smoke coverage for diagnostics cost fields.

## Autonomous Round 34 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 34

- Round 35 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 36 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 37 candidate: add a docs note explaining that live-review net results still do not model funding, liquidation, partial fills, or order-book queue position.

## 2026-07-08 Autonomous iteration round 35 - legacy live-review net normalization

- Observed that existing stored live-review records may predate net-cost fields, so old gross `outcome_pct` values could mix with new net outcomes in stats.
- Selected legacy normalization because mixed gross/net samples would inflate hit rate and calibration quality.
- Added read-time normalization for live-review evaluation items that do not contain `estimated_cost_pct`.
- Legacy records now preserve `gross_max_profit_pct` and `gross_outcome_pct`, while `max_profit_pct` and `outcome_pct` are normalized to net values for API output and statistics.
- Kept normalization in memory only; existing MySQL/runtime records are not rewritten.
- Added smoke coverage proving a legacy +0.10% gross outcome becomes net negative after estimated cost and no longer counts as a hit.

## Autonomous Round 35 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 35

- Round 36 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 37 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 38 candidate: add a docs note explaining that live-review net results still do not model funding, liquidation, partial fills, or order-book queue position.

## 2026-07-08 Autonomous iteration round 36 - same-origin POST hardening

- Observed that the deployed dashboard has cookie-backed auth and several state-changing POST APIs, but no explicit Origin/Referer host check on authenticated POST requests.
- Selected same-origin POST hardening because it reduces CSRF-style risk for the public multi-user deployment without touching trading strategy logic.
- Added `BIAN_AUTH_REQUIRE_SAME_ORIGIN_POST`, default enabled.
- Authenticated POST APIs now reject browser requests whose `Origin`/`Referer` host does not match `Host` or `X-Forwarded-Host`.
- Kept compatibility for non-browser/CLI calls that do not send Origin or Referer headers.
- Exposed the setting through health auth diagnostics.
- Added `.env.example` and Docker deployment documentation for the new auth control.
- Added smoke coverage for same-origin, forwarded-host, cross-origin, and missing-source cases.

## Autonomous Round 36 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 36

- Round 37 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 38 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 39 candidate: add a docs note explaining that live-review net results still do not model funding, liquidation, partial fills, or order-book queue position.

## 2026-07-08 Autonomous iteration round 37 - live-review limitation docs

- Observed that live-review outcomes are now net of estimated fee/slippage, but users still need a clear boundary around what the review does not simulate.
- Selected a documentation round because over-trusting review metrics can lead to false confidence even when the math is more conservative.
- Added a live-review cost assumptions section to `docs/docker-deploy.md`.
- Clarified that gross price-path fields remain available in CSV export for audit.
- Documented that net live-review results still do not model funding payments, liquidation, partial fills, queue position, exchange outages, margin mode, or fast-market slippage expansion.
- Added the same limitation to `feature_list.json`.

## Autonomous Round 37 Verification

- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 37

- Round 38 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 39 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 40 candidate: add a small export/readme note for interpreting gross vs net review CSV fields if user confusion appears.

## 2026-07-08 Autonomous iteration round 38 - same-origin port matching fix

- Observed that the new same-origin POST helper compared only host names and ignored non-default ports.
- Selected this fix immediately because `127.0.0.1:9000` should not be treated as the same origin as `127.0.0.1:8000`.
- Replaced host-only comparison with normalized host plus non-default port comparison.
- Kept default port compatibility: `http://example.com` matches `example.com:80`, and `https://example.com` matches `example.com:443`.
- Added smoke coverage for different non-default ports and default 80/443 equivalence.

## Autonomous Round 38 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 38

- Round 39 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 40 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 41 candidate: add a small export/readme note for interpreting gross vs net review CSV fields if user confusion appears.

## 2026-07-08 Autonomous iteration round 39 - login POST same-origin coverage

- Observed that same-origin POST hardening covered authenticated POST routes, but `/api/login` was still routed before the origin check.
- Selected this as the highest-value follow-up because login CSRF can still create confusing or unsafe browser session state even when later APIs are protected.
- Moved same-origin POST validation ahead of the `/api/login` route.
- The login API now shares the same default Origin/Referer host check as other dashboard POST APIs.
- Kept non-browser/CLI compatibility because requests without Origin or Referer still pass the same-origin helper.
- Updated Docker deployment documentation and feature metadata to describe dashboard POST APIs, including login, rather than authenticated POST APIs only.

## Autonomous Round 39 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 39

- Round 40 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 41 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 42 candidate: add a small export/readme note for interpreting gross vs net review CSV fields if user confusion appears.

## 2026-07-08 Autonomous iteration round 40 - trusted forwarded-host boundary

- Observed that same-origin POST validation accepted `X-Forwarded-Host` directly, while `X-Forwarded-For` was already limited to trusted local/private proxies.
- Selected this as a deployment hardening fix because reverse-proxy headers should share the same trust boundary.
- Added `same_origin_allowed_for_peer()` so forwarded host is considered only when `BIAN_AUTH_TRUST_PROXY_HEADERS=1` and the peer IP is loopback/private/link-local.
- Updated the request handler to use the trusted-forwarded-host helper.
- Added smoke coverage proving forwarded host is ignored by default, accepted from trusted local proxies when enabled, and ignored from public peers.
- Updated Docker deployment docs and feature metadata to describe the trusted forwarded-host behavior.

## Autonomous Round 40 Verification

- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `python -B scripts\smoke.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 40

- Round 41 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 42 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 43 candidate: add a small export/readme note for interpreting gross vs net review CSV fields if user confusion appears.

## 2026-07-08 Autonomous iteration round 41 - gross versus net CSV guidance

- Observed that live-review CSV export now includes both net and gross fields, which can be misread during manual spreadsheet review.
- Selected a small documentation clarification because users should make hit-rate and expectancy decisions from net fields after estimated execution cost.
- Updated `docs/docker-deploy.md` to say `max_profit_pct` and `outcome_pct` are the decision fields, while `gross_max_profit_pct` and `gross_outcome_pct` are for raw price-path audit.
- Updated `feature_list.json` so the capability is tracked.

## Autonomous Round 41 Verification

- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing CRLF conversion warnings.

## Autonomous Backlog After Round 41

- Round 42 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 43 candidate: add endpoint-level smoke coverage around authenticated POST routes if a lightweight handler harness is introduced.
- Round 44 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal output.

## 2026-07-08 Autonomous iteration round 42 - POST route smoke harness

- Observed that same-origin/auth helpers were covered, but real `Handler.do_POST` route ordering still had limited direct smoke coverage.
- Selected endpoint-level smoke coverage because login, preference saves, and manual signal-review evaluation are state-changing routes that can regress through route order changes.
- Added a lightweight in-process `SmokeHandler` harness that calls `Handler.do_POST` without starting a server.
- Covered cross-origin `/api/login` blocking before login handling, same-origin protected POST auth rejection, authenticated `/api/preferences` storage saves, and missing-Origin CLI-compatible `/api/signal-reviews/evaluate` triggering.
- Updated `feature_list.json` to track real POST route smoke coverage.

## Autonomous Round 42 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile scripts\smoke.py src\bian_dashboard\server.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 42

- Round 43 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 44 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal output.
- Round 45 candidate: review API error responses for raw exception leakage and replace user-facing internals with generic messages plus server logs where safe.

## 2026-07-08 Autonomous iteration round 43 - API 500 error sanitization

- Observed several dashboard API 500 handlers still returned `str(exc)` directly to the browser.
- Selected public error sanitization because the deployed dashboard is login-protected but still browser-facing, and raw exception strings can expose internal paths, DSNs, dependency errors, or operational details.
- Added a shared `INTERNAL_ERROR_MESSAGE` and kept detailed exceptions in `LOG.exception`.
- Sanitized unexpected 500 responses for login, password change, user creation, preference saving, manual signal-review evaluation, and signal-review listing.
- Added smoke coverage proving storage/evaluator exceptions with internal text return only the generic browser-safe error.
- Updated `feature_list.json` to track the sanitized API error behavior.

## Autonomous Round 43 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 43

- Round 44 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 45 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal output.
- Round 46 candidate: review `/api/market` error detail/stderr exposure and decide whether to gate detailed analyzer errors behind an explicit debug setting.

## 2026-07-08 Autonomous iteration round 44 - market API error detail gate

- Observed that `/api/market` error responses could include analyzer `detail` and `stderr`, including when stale cache was returned.
- Selected this public API hardening because upstream/analyzer failures can contain internal paths or operational details that should not be browser-visible by default.
- Added `BIAN_EXPOSE_ERROR_DETAILS`, default `0`.
- Changed `send_cached_or_error()` so `detail` and `stderr` are only included when the debug setting is enabled.
- Exposed the setting in admin diagnostics under `analyzer.expose_error_details`.
- Added `.env.example` and Docker deployment documentation for the new setting.
- Added smoke coverage for default hidden market error details and explicit debug exposure.
- Updated `feature_list.json` to track the behavior.

## Autonomous Round 44 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 44

- Round 45 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 46 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 47 candidate: reduce smoke-test log noise from expected cross-origin blocking while preserving coverage.

## 2026-07-08 Autonomous iteration round 45 - smoke output cleanup

- Observed that the POST route smoke test intentionally triggered cross-origin blocking, which printed a warning during every verification run.
- Selected test-output cleanup because noisy expected logs make it harder to notice real verification failures.
- Temporarily disabled the project logger only inside the expected error-path smoke tests, restoring it afterward.
- Kept all same-origin, auth, preference, manual evaluation, and market-error-detail coverage unchanged.
- Updated `feature_list.json` to track clean smoke output for expected error paths.

## Autonomous Round 45 Verification

- Passed `python -B scripts\smoke.py` with clean `smoke ok` output.
- Passed `python -B -m py_compile scripts\smoke.py src\bian_dashboard\server.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with clean `smoke ok` and `verify ok` output.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 45

- Round 46 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 47 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 48 candidate: add lightweight smoke coverage for invalid JSON request bodies so bad client input returns controlled 400 responses instead of generic 500s.

## 2026-07-08 Autonomous iteration round 46 - bad JSON POST handling

- Observed that malformed JSON request bodies could be caught by broad endpoint exception handlers and returned as generic 500 errors.
- Selected controlled client-input errors because bad request bodies should not look like server faults and should not trigger noisy internal-error paths.
- Added `BadRequestError` for request-body parsing failures.
- Changed `read_json_body()` to classify invalid `Content-Length`, oversized bodies, and malformed JSON as bad requests.
- Added 400 handling for login, password change, user creation, and preferences POST APIs that parse JSON.
- Extended the smoke handler to support raw request bodies.
- Added smoke coverage for malformed JSON and invalid `Content-Length` returning explicit 400 responses.
- Updated `feature_list.json` to track controlled bad-JSON POST behavior.

## Autonomous Round 46 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1`.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 46

- Round 47 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 48 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 49 candidate: add a small maximum-symbol/request validation smoke check around `/api/market` parsing and realtime key normalization.

## 2026-07-14 Autonomous iteration round 47 - unified symbol request limits

- Observed that `/api/market` and realtime SSE capped their symbol lists, while `/api/signal-reviews` used a separate unbounded parser; realtime hub keys also assumed callers had already normalized symbols.
- Selected this L2 API/cache hardening because oversized or differently spelled requests could expand a storage query or fragment realtime WebSocket hubs.
- Added shared symbol normalization for query values and direct symbol collections, including uppercase conversion, bare-symbol `USDT` completion, deduplication, and the existing eight-symbol cap.
- Applied it to market requests, signal-review filtering, realtime cache keys, and realtime hub selection without changing strategy calculations or frontend behavior.
- Added offline smoke coverage for mixed request spellings, duplicate removal, empty-query fallback behavior, the signal-review cap, and canonical realtime cache keys.
- Updated `feature_list.json` to track the shared request normalization contract.

## Autonomous Round 47 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `node -e "JSON.parse(require('fs').readFileSync('feature_list.json','utf8')); console.log('feature_list.json ok')"`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 47

- Round 48 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 49 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 50 candidate: add a bounded `limit` parser for `/api/signal-reviews` so malformed, negative, or oversized record limits cannot become uncontrolled storage queries.

## 2026-07-14 Autonomous iteration round 48 - signal-review list limit contract

- Observed that the storage layer clamps review records to `1..1000`, but `/api/signal-reviews?limit=abc` raised a broad exception and incorrectly returned a generic 500 response.
- Selected API input validation because malformed client parameters must not look like a server/storage outage.
- Added an explicit `limit` parser with the existing default of 240 and an accepted range of `1..1000`.
- Invalid, zero, negative, and oversized values now return a controlled 400 response before signal-review evaluation or storage reads begin.
- Added offline smoke coverage for default/empty values, the upper boundary, and invalid integer/range values.
- Updated `feature_list.json` to record the bounded signal-review list contract.

## Autonomous Round 48 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed route-level smoke coverage for `GET /api/signal-reviews?limit=abc`, which returns 400 before storage/evaluation work begins.
- Passed final `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 48

- Round 49 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 50 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 51 candidate: add direct GET-route smoke coverage for invalid `/api/signal-reviews` parameters, not just parser-level coverage.

## 2026-07-14 Autonomous iteration round 49 - invalid UTF-8 request handling

- Observed that request-body parsing classified malformed JSON and invalid content lengths, but an invalid UTF-8 byte sequence still escaped during decoding and could become a generic 500 response.
- Selected this HTTP input hardening because browser-facing APIs should classify all malformed JSON transport forms as client errors.
- Changed JSON body decoding to return a controlled `request body must be UTF-8` bad-request error.
- Added POST-route smoke coverage using an invalid byte sequence on authenticated preference save.
- Updated `feature_list.json` to include invalid UTF-8 in the malformed-request contract.

## Autonomous Round 49 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed final `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 49

- Round 50 candidate: continue reducing false confidence in low-sample live-review buckets once more real records exist.
- Round 51 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 52 candidate: verify the signal-review invalid-limit path stays route-covered after future auth/router edits.

## 2026-07-14 Autonomous iteration round 50 - independent live-review calibration samples

- Observed that calibration segment counts added the 5m, 15m, and 1h outcomes of one signal as separate samples, so the 30-sample calibration threshold could be reached with roughly 10 independent trading signals.
- Selected this correctness fix because inflated sample counts can make a low-evidence segment look suitable for confidence support or threshold changes.
- Added independent signal and independent triggered-signal counters to review buckets while preserving horizon-level evaluation metrics for the dashboard.
- Calibration eligibility now uses independent counts; candidate payloads retain both independent and evaluation counts for audit.
- Updated the dashboard calibration copy to label candidate counts as independent samples/triggers.
- Added a regression that proves 90 repeated horizon outcomes from 30 signals cannot satisfy a 31-signal/21-trigger threshold.
- Updated `feature_list.json` to describe the independent-sample calibration rule.

## Autonomous Round 50 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed final `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 50

- Round 51 candidate: inspect frontend encoding/copy consistency if user-facing mojibake is confirmed in browser rather than only terminal rendering.
- Round 52 candidate: track calibration metrics by a single declared outcome horizon as well as independent signals, so hit/stop rates cannot mix overlapping 5m/15m/1h outcomes.
- Round 53 candidate: verify the signal-review invalid-limit path stays route-covered after future auth/router edits.

## 2026-07-14 Autonomous iteration round 51 - terminal-horizon calibration metrics

- Observed that independent-signal thresholds fixed sample-count inflation, but calibration hit/stop/outcome rates still mixed the overlapping 5m, 15m, and 1h result paths for each signal.
- Selected a declared terminal horizon because threshold calibration needs one comparable outcome per signal, while the dashboard can still show all shorter-horizon review data.
- Added `SIGNAL_REVIEW_CALIBRATION_HORIZON=1h`; calibration segments now use only the 1h evaluation for valid signals and count an invalid signal once.
- Kept full multi-horizon metrics in the review display, exposed the calibration horizon in the API payload, and labeled the dashboard calibration heading with the active horizon.
- Added regression coverage proving 30 completed 5m-only reviews do not qualify as 1h calibration evidence.
- Updated `feature_list.json` to describe the independent 1h calibration contract.

## Autonomous Round 51 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 51

- Round 52 candidate: inspect frontend encoding/copy consistency in an actual browser if user-facing mojibake is confirmed outside terminal rendering.
- Round 53 candidate: extend live-review calibration by advice horizon once enough 4h/8h outcome data is recorded; do not infer it from a 1h proxy.
- Round 54 candidate: verify the signal-review invalid-limit path stays route-covered after future auth/router edits.

## 2026-07-14 Autonomous iteration round 52 - authenticated storage isolation

- Observed a P0 multi-user issue: `DashboardStorage.user_id` came only from `BIAN_STORAGE_USER_ID` (normally `default`), while authenticated request users were not applied to preference, snapshot, or signal-review storage calls.
- This allowed separate dashboard accounts to share preferences and signal-review records; the background evaluator also selected only the default user's pending rows.
- Added immutable request-scoped storage views keyed as `auth:<database-user-id>`, so concurrent requests never mutate global storage state.
- Routed authenticated preference reads/writes, strategy snapshot writes, and signal-review reads/writes through the request scope; market and realtime Redis cache remains intentionally shared because it contains public market data only.
- Updated the background evaluator to fetch due MySQL records across users, retain each row's owning storage key, and update the matching user row.
- Applied the same user scope to runtime signal-review file fallback, including duplicate keys and evaluation updates.
- Added smoke coverage for independent scopes, file fallback isolation, authenticated preference scope selection, due-row owner propagation, and scoped/all-user SQL parameter order.

## Autonomous Round 52 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\storage.py src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed final `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 52

- Round 53 candidate: capture a user-scoped signal-review record when an authenticated user receives an existing fresh market cache, so a newly logged-in viewer does not wait for the next analyzer cache miss.
- Round 54 candidate: inspect frontend encoding/copy consistency in an actual browser when an authenticated local server session is available.
- Round 55 candidate: extend live-review calibration by advice horizon once enough 4h/8h outcomes are recorded; do not infer them from a 1h proxy.

## 2026-07-14 Autonomous iteration round 53 - cached market review capture

- Observed that user-scoped records were written only on an analyzer cache miss. A second authenticated viewer receiving a fresh memory, Redis, or disk market cache would see the market payload but wait for a later refresh before receiving any personal review records.
- Added a deduplicated signal-review capture step to fresh memory, Redis, and disk cache responses, while preserving shared public market-cache behavior and avoiding another analyzer subprocess.
- The capture helper only schedules background evaluation when storage reports new review records; duplicate cache responses remain cheap and do not create evaluator churn.
- Storage capture exceptions are logged but cannot turn a usable cached market response into an API failure.
- Added offline coverage for cached response review creation, duplicate handling, and evaluation trigger suppression on duplicate records.

## Autonomous Round 53 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\storage.py src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed final `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 53

- Round 54 candidate: inspect frontend encoding/copy consistency in an actual browser when an authenticated local server session is available.
- Round 55 candidate: add retention/cleanup for per-user strategy snapshots, which now grow with multiple dashboard users.
- Round 56 candidate: extend live-review calibration by advice horizon once enough 4h/8h outcomes are recorded; do not infer them from a 1h proxy.

## 2026-07-14 Autonomous iteration round 54 - per-user strategy snapshot retention

- Observed that every analyzer cache miss inserts a strategy snapshot and the per-user snapshot table had no retention, which would cause unbounded MySQL growth in a long-running multi-user dashboard.
- Added `BIAN_STRATEGY_SNAPSHOT_LIMIT`, default 1000 with a minimum of 10.
- Each snapshot insert now finds the user's newest retained boundary and deletes only older rows for that same user in the existing transaction.
- Updated the new-table index definition for the `(user_id, created_at, id)` retention query, and documented the retention setting in `.env.example` and storage documentation.
- Added offline SQL-shape coverage proving insert, scoped retention lookup, scoped delete, and commit occur in the expected order.

## Autonomous Round 54 Verification

- Passed `python -B scripts\smoke.py`.
- Passed `python -B -m py_compile src\bian_dashboard\storage.py src\bian_dashboard\server.py scripts\smoke.py bian.py server.py`.
- Passed `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed final `git diff --check` with only existing LF-to-CRLF conversion warnings.

## Autonomous Backlog After Round 54

- Round 55 candidate: inspect frontend encoding/copy consistency in an actual browser when an authenticated local server session is available.
- Round 56 candidate: add bounded retention for the runtime signal-review fallback file per user, not just globally.
- Round 57 candidate: extend live-review calibration by advice horizon once enough 4h/8h outcomes are recorded; do not infer them from a 1h proxy.

## 2026-07-14 Comprehensive correctness review fixes

- Fixed interval confirmation for the 24/7 crypto market: strategy direction now uses the latest completed interval K line while realtime price/book data continue updating. UI wording now says `实时K线` / `已完成K线`, not market close.
- Prevented the TLM extreme-volatility downgrade from turning a bullish score into an executable short; it now becomes a non-executable wait state.
- Made moderate risk penalties effective without double-counting the base `禁止半仓` / `禁止开仓` score caps.
- Changed ATR to Wilder RMA, trigger volume to a completed 1m candle against prior full candles, and retest confirmation to the latest completed 1m bar rather than any touch in the previous 20 bars.
- Made missing exchange tick metadata fail closed for position sizing.
- Reclassified the historical result as a non-overlapping 5m proxy using next-bar-open fills. It is visible for context but no longer calibrates or inflates the live opening score.
- Made live-review hit rate require a valid follow-through result and removed unknowable trigger-bar MFE/MAE from OHLC evaluation.
- Added market-lock reference tracking, moved database work out of the global payload lock, isolated optional snapshot failures, deduplicated all-user review evaluation, and added a due-query index/fair retry ordering.
- Moved realtime Redis persistence off the WebSocket callback into a coalescing background writer.
- Added one-time schema initialization, expired-session cleanup, bounded login-failure memory, bounded disk market cache, and a working cumulative login lockout.
- Scoped browser preferences by authenticated user, discarded stale signal-review responses, marked failed/stale SSE as offline, and prevented custom-symbol refresh resurrection.
- Parallelized Binance REST requests with bounded workers. A live DOGE/TLM check improved from about 48 seconds to about 24 seconds in the same environment.

## Correctness Review Verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed `node --check web\assets\charts.js` and parsed `feature_list.json`.
- Live Binance DOGE/TLM validation returned verified tick sizes, positive stops, four `已完成K线` advice states, and conservative TLM gates.
- Real MySQL/Redis integration and authenticated browser rendering still require deployment-environment verification; no service was started during this review.

## 2026-07-14 Realtime freshness and review-timing fixes

- Replaced stale depth-price retention with a fresh depth20 midpoint and separate price/depth event timestamps.
- Changed market cache TTL, strategy `generated_at`, and live-review `snapshot_at_ms` to start at analyzer completion/publication; added observable analysis start/completion/duration fields.
- Added a conservative frontend realtime trigger overlay using executable bid/ask, live spread, depth freshness, top5 depth, and imbalance. It never upgrades an old unconfirmed 1m structure and expires confirmations after 90 seconds.
- Added strategy-age risk gates: warn after 7 minutes and prohibit new entries after 12 minutes, even when realtime prices remain connected.
- Added Binance `markPrice`, `indexPrice`, and `nextFundingTime`; the funding countdown now follows the exchange timestamp and updates every second.
- Added bounded preference-sync retry behavior and fixed the mobile header/status overflow found during browser verification.
- Added offline regressions for depth/price timestamps, executable short bid distance, and analysis-completion publication timing.

## Realtime Freshness Verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed `git diff --check`; only existing LF-to-CRLF conversion warnings remain.
- Browser verification passed at 1280x720 and 390x844 with no key-panel or page-level horizontal overflow after the mobile fix.
- Static fallback correctly showed an expired-strategy hard block and a live funding countdown without JavaScript errors.
- Live Binance DOGE/TLM validation was attempted twice but the current machine timed out reaching Binance; deployment-network validation remains required.

## Next Review

- Validate the full `/api/market` plus SSE flow against Binance when network access recovers, including `published_at_ms`, price/depth ages, and `next_funding_time_ms`.
- Consider a shared realtime 1m kline stream if trigger confirmations need to upgrade between five-minute strategy snapshots; do not infer fresh 1m volume/structure from order-book data alone.

## 2026-07-14 Realtime connection-state stabilization

- Identified a deterministic LIVE-badge flap: while Binance WebSocket was disconnected, the SSE heartbeat still carried the last cached prices; the frontend briefly marked each stale heartbeat online before the 20-second freshness timer marked it offline again.
- Split browser realtime state into SSE transport, Binance upstream connection, and active-symbol price freshness. LIVE now requires all three conditions; fresh data during a transport reconnect is shown as `重连`, and stale data remains `离线`.
- Strategy REST success/failure no longer changes the realtime-price badge, so a successful cached `/api/market` response cannot impersonate a healthy WebSocket.
- Added `X-Accel-Buffering: no`, a one-second EventSource retry hint, and configurable `BIAN_SSE_MAX_SECONDS` with a six-hour default instead of an intentional reconnect every 30 minutes.
- Added per-hub connect/disconnect counters and last connected/disconnected/message/price/depth timestamps to admin diagnostics, plus explicit WebSocket connect/disconnect logs.
- Browser fallback verification held `data-state=offline` continuously across EventSource retries instead of toggling green.

## Realtime Stabilization Verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok` and `verify ok`.
- Passed `node --check web\assets\charts.js` and `git diff --check` apart from existing LF-to-CRLF warnings.
- SSH and public-browser access to `159.223.91.36` timed out from the current environment, so deployed-container logs could not be inspected and the rebuilt server still needs deployment verification.

## 2026-07-16 Three-round review remediation

- Aligned live-review price and time by recording the analyzer ticker observation time, refreshing a Binance futures book midpoint immediately before publication, and refusing to treat a stale analysis-start price as the publication price.
- Changed 1m review sampling to fetch the signal's current minute, include only bars that close on or before the exact 5m/15m/1h horizon, and prevent recorded entry/stop times from predating publication.
- Added realtime WebSocket supervision for exited workers and stale upstream messages, observable restart counters, and atomic hub acquisition/idle cleanup to prevent orphaned duplicate Binance connections.
- Canonicalized market cache keys as sorted symbol sets and made market/realtime APIs reject more than eight unique symbols instead of silently truncating them.
- Deferred waiting `partial` review rows to the back of the queue and added fair update-time ordering for the runtime-file fallback so old 15m/1h waits cannot starve newer due records.
- Made Redis read exceptions degrade to cache misses with cooldown; added bounded MySQL/Redis connection and read/write waits plus expiring health caches.
- Serialized frontend preference writes and added monotonic per-user MySQL revisions. Page unload replays unconfirmed patches with `sendBeacon`, while stale server state can no longer overwrite a newer browser revision.
- Fixed Chinese `偏多` / `偏空` signal-history rendering, HTML-escaped stored history labels, enforced the eight-symbol UI limit, based LIVE freshness on local receipt of a new event identity, and made missing strategy timestamps fail closed.
- Restored `.env.example`, `scripts/smoke.py`, `scripts/deploy.py`, `scripts/frontend-smoke.js`, and `docs/storage.md`; verification now executes both Python and frontend behavior regressions.

## Three-round remediation verification

- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Passed `python -B bian.py --symbols DOGEUSDT,TLMUSDT --json`; both reports included `price_observed_at_ms`, verified tick metadata, positive stops, and complete strategy output.
- Passed local unauthenticated `GET /api/market?symbols=DOGEUSDT,TLMUSDT`: `stale=false`, publication prices were present for both symbols, analysis took about 2.84 seconds, and publication enrichment took about 0.42 seconds.
- Local SSE HTTP framing and retry events worked, but the Binance WebSocket upstream timed out during the 12-second observation and reported `connected=false`; deployment-network validation remains required.
- Docker Compose validation was skipped because Docker is not installed on this Windows machine.
- In-app visual browser verification was skipped because the browser security policy rejected the local `127.0.0.1:8876` target; frontend production functions were still executed by `scripts/frontend-smoke.js`.
- Real MySQL/Redis transaction and timeout behavior remains to be verified against deployed services; offline fake-connection regressions cover revision ordering, timeout wiring, health-cache expiry, Redis degradation, and review ownership.

## Next review

- Rebuild the deployment and verify MySQL preference revision ordering with two concurrent authenticated sessions.
- Verify Redis failover by interrupting the deployed Redis container while `/api/market` is requested.
- Observe a healthy Binance WebSocket long enough to confirm `restart_count` recovery after an intentional upstream interruption.

## 2026-07-16 Review bug remediation

- Excluded a signal's partial publication minute from 1m OHLC evaluation, so pre-publication high/low cannot be relabeled as a post-publication entry or stop. Publications exactly on a minute boundary still use that complete candle.
- Required a positive integer revision in both the preference HTTP API and MySQL storage method; missing, boolean, fractional, zero, and negative revisions are rejected before any upsert.
- Replaced directory-wide deployment packaging with a Git-derived manifest. Clean worktrees are required by default, `--allow-dirty` is explicit, and ignored files plus the local `.env` remain excluded.
- Changed remote deployment to validate a temporary release, preserve the prior directory and uploaded archive until health succeeds, and persist `--public-port` in the remote `.env`.
- Updated storage/deployment documentation, regression cases, risk/release evidence, and `feature_list.json` to match the stricter contracts.

## Review bug remediation verification

- Passed `python -B scripts\smoke.py`, including partial-minute entry/stop, aligned-minute, invalid revision, deploy manifest, release staging, port, and retry-order regressions.
- Passed `python -B -m py_compile scripts\deploy.py scripts\smoke.py src\bian_dashboard\server.py src\bian_dashboard\storage.py`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Passed deployment subprocess checks proving dirty worktrees are rejected by default, `--allow-dirty` dry-run succeeds, port 9000 is persisted, and archive cleanup follows health verification.
- Passed `node --check web\assets\charts.js`, `feature_list.json` parsing, and `git diff --check` apart from existing LF-to-CRLF warnings.
- Local Bash/Docker and real MySQL/Redis were unavailable, so generated remote-shell syntax, container release switching, and live database transaction behavior still require deployment-environment verification.

## 2026-07-16 Follow-up review remediation

- Preference sync now treats `applied=false` as a revision conflict, preserves the exact patch, advances beyond the returned server revision, and retries. Temporary HTTP 200 `saved=false` storage responses also retry with backoff.
- Realtime badge selection now reports offline for an explicit upstream error without a fresh price instead of remaining in connecting state while SSE transport is open.
- Server preference normalization applies removed symbols before calculating active/default capacity, so removing DOGE/TLM leaves room for all eight custom symbols.
- Deployment no longer disables the global Git excludes file. Dirty packaging preserves all Git ignore sources and fails closed when an ignore source cannot be read.
- Docker Compose now passes `BIAN_REDIS_PASSWORD` to the dashboard and Redis services, enables `requirepass` for non-empty secrets, and authenticates its healthcheck. Fresh scripted deployments generate the Redis password.
- Updated storage/deployment docs, regression and risk evidence, delivery review, and `feature_list.json`.

## Follow-up remediation verification

- Passed `python -B scripts\smoke.py` with deployment ignore-integrity and Redis Compose contract coverage.
- Passed `node scripts\frontend-smoke.js` with conflict retry, storage retry, upstream-error badge, and eight-custom-symbol restore regressions.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Confirmed an actual `--allow-dirty --dry-run` fails closed in the current environment because the configured global Git ignore is unreadable.
- Confirmed the running server on port 8876 serves the updated conflict-retry, offline-error, and symbol-normalization frontend code.
- Passed `feature_list.json` parsing and `git diff --check` apart from existing LF-to-CRLF warnings.
- Docker, Bash, a YAML parser, and real MySQL/Redis are unavailable locally, so Compose parsing, Redis authentication, remote shell execution, and deployed multi-session preference behavior remain release-environment checks.

## 2026-07-17 Concurrency and preference consistency remediation

- Replaced unconditional preference-conflict promotion with server refresh plus three-way field reconciliation. Same-field server changes win; only fields unchanged from the request base snapshot retry.
- Stopped treating configured MySQL preference outages as an empty revision-zero server. Preference GET now returns HTTP 503 and the browser keeps local fallback state without writing a full stale snapshot.
- In-flight-only unload beacons reuse the active request revision instead of promoting an unconfirmed patch.
- Added direct WebSocket worker generations and guarded connect, disconnect, error, message, counter, and timestamp mutations against stopped workers.
- Changed same-origin validation to allow only a truly absent browser source header for CLI compatibility and reject `Origin: null` or malformed values.
- Added deterministic Python and VM frontend regressions for same-field and non-overlapping preference conflicts, outage writeback suppression, unload revision reuse, delayed WebSocket connections, and invalid source headers.

## Concurrency remediation verification

- Passed `python -B scripts\smoke.py` with stopped-worker, unavailable preference storage, and invalid Origin regressions.
- Passed `node scripts\frontend-smoke.js` with preference three-way reconciliation, storage-outage, retry, and unload revision regressions.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Passed Python compilation, frontend syntax, `feature_list.json` parsing, and `git diff --check` apart from existing LF-to-CRLF warnings.
- GitNexus detected 182 changed symbols and 150 affected processes across the full pre-existing worktree, retaining an overall `critical` review classification.
- Restarted the local development backend on `127.0.0.1:8876`; one listener remained, health was OK, and the served frontend contained `reconcilePreferenceConflict`.
- In-app browser navigation to the local URL was blocked by the browser security policy, so browser console/render verification was skipped rather than bypassed.
- Real multi-session MySQL ordering and a healthy Binance WebSocket restart remain deployment-environment checks.

## 2026-07-17 Preference recovery and password transaction remediation

- Added a dedicated preference conflict-recovery queue. A failed reconciliation GET now retries the GET with backoff and never promotes an unresolved stale patch into a POST.
- Disabled server preference synchronization when the API explicitly reports MySQL as unconfigured, preserving localStorage fallback without permanent retry traffic.
- Split pagehide delivery into an original-revision in-flight patch and a separate newer pending patch, preventing old fields from inheriting the pending revision.
- Moved password hash update and other-session revocation into one MySQL transaction and pass the current session token into that transaction.
- Added deterministic regressions for reconciliation read recovery, unconfigured storage fallback, mixed unload beacons, atomic commit, and rollback on session-revocation failure.

## Preference recovery remediation verification

- Passed `python -B scripts\smoke.py`, including atomic password/session commit and rollback assertions.
- Passed `node scripts\frontend-smoke.js`, including failed reconciliation GET recovery, mixed unload separation, and unconfigured MySQL fallback assertions.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Passed Python compilation, frontend syntax, `feature_list.json` parsing, and `git diff --check` apart from existing LF-to-CRLF warnings.
- GitNexus detected 57 changed symbols and 11 affected processes for this patch, with `high` overall impact because preference readiness participates in boot and conflict flows.
- Real MySQL transaction failure injection, two-session browser ordering, and pagehide network delivery remain deployment-environment checks.

## 2026-07-17 Review findings remediation

- Stopped automatic preference retries after non-retryable HTTP failures while retaining the pending local patch for a later explicit change or session recovery.
- Persisted conflict-recovery patches and base snapshots in user-scoped localStorage, restored them during boot, and prevented unresolved server fields from overwriting those local values before reconciliation.
- Replaced separate mixed pagehide beacons with one ordered preference batch. The API validates strictly increasing revisions and MySQL skips stale entries while applying later entries under one revision lock and transaction.
- Added `FOR UPDATE` to password verification so concurrent password changes serialize before accepting the old password.

## Review findings remediation verification

- Passed `python -B scripts\smoke.py`, including ordered batch skip, batch rollback, API validation, and password row-lock assertions.
- Passed `node scripts\frontend-smoke.js`, including persisted recovery reload, one-beacon unload batching, and repeated-timer checks after HTTP 401.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Passed frontend syntax, Python compilation, `feature_list.json` parsing, and `git diff --check` apart from line-ending warnings.
- GitNexus classified the uncommitted impact as `critical` across 41 execution flows because shared preference save behavior participates in dashboard boot and multiple controls.
- Restarted one local development backend on `127.0.0.1:8876` with authentication disabled for the unconfigured-MySQL fallback; health and dashboard assets returned HTTP 200 with the new batch and recovery code.
- Real MySQL concurrency, browser pagehide delivery, and deployed multi-session ordering remain release-environment checks.

## 2026-07-17 Preference recovery lifecycle remediation

- Persisted rejected preference patches before reconciliation starts, so pagehide during an unresolved GET cannot lose all recovery ownership.
- Merged later preference edits into the durable recovery record and used recovery object identity to prevent older promise completions from clearing newer state.
- Preserved reconciliation HTTP status and stopped automatic recovery retries for 400/401/403 while keeping bounded backoff for network, 408/429, and 5xx failures.
- Added VM regressions for pagehide during reconciliation, same-key recovery supersession across memory reload, and non-retryable recovery GET behavior.

## Preference recovery lifecycle verification

- Passed `node scripts\frontend-smoke.js` and `node --check web\assets\charts.js`.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- Passed `feature_list.json` parsing and `git diff --check` apart from line-ending warnings.
- Real browser pagehide delivery and deployed multi-session ordering remain release-environment checks.

## 2026-07-17 Full review remediation

- Replaced coarse post-rounding with Decimal tick-grid rounding and added owner tokens to backtest cache locks so directional prices stay legal and an old owner cannot remove a replacement lock.
- Rejected out-of-order price/depth events independently in the realtime hub and browser, recalculated frontend risk sizing from the current execution-score tier, and reserved symbol capacity across concurrent add requests.
- Made dashboard boot fail closed on ambiguous or unavailable authentication. Only an explicit `auth_enabled=false` response can enter local scope; transient failures retry with bounded backoff and HTTP 401 redirects to login.
- Reconciled file-fallback signal reviews into MySQL by `(storage_user_id, signal_key)` after recovery, merging terminal status and completed horizons before idempotent upsert and deleting file rows only after commit.
- Classified partial signal reviews from their record status and next missing horizon deadline instead of treating the first completed horizon as terminal.
- Bounded HTTP handler concurrency, socket waits, SSE clients, and SSE lifetime. Compose and remote deployment now bind the application to loopback and default authentication cookies to `Secure`, requiring an HTTPS reverse proxy for production access.

## Full review remediation verification

- Passed targeted `python -B scripts\smoke.py`, `node scripts\frontend-smoke.js`, frontend syntax, Python compilation, and `git diff --check` apart from existing line-ending warnings.
- Passed `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` with `smoke ok`, `frontend smoke ok`, and `verify ok`.
- GitNexus detected 126 changed symbols and 92 affected execution flows across the full pre-existing worktree, retaining the expected `critical` L3 impact classification.
- Offline tests cover tick monotonicity/divisibility, stale-lock ownership, exchange-event ordering, score-tier sizing, symbol reservations, auth failure isolation, partial review deadlines, fallback reconciliation, request timeouts, HTTP slots, and SSE rejection at capacity.
- Restarted the single local backend on `127.0.0.1:8876` as PID 15520 with auth disabled; health, explicit local auth identity, updated frontend asset markers, and SSE diagnostics (`300s`, `12` clients) passed.
- In-app browser navigation to the local URL was rejected by browser security policy, so DOM/render verification was skipped rather than bypassed.
- Docker CLI is unavailable locally. Real MySQL 8.4 reconciliation/upsert, Docker Compose parsing/startup, HTTPS proxy/certificate behavior, and concurrent network saturation remain release-environment checks.
