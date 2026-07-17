# Regression Cases

1. Start the server with `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`.
2. Open `http://127.0.0.1:8000/binance-futures-dashboard.html`.
3. Confirm the dashboard loads fonts, ECharts, and `web/assets/charts.js`.
4. Call `http://127.0.0.1:8000/api/market?symbols=DOGEUSDT,TLMUSDT` and confirm JSON data is returned.
5. Confirm `runtime/market_cache.json` updates after a successful market request.
6. Confirm `/api/realtime-prices?symbols=DOGEUSDT,TLMUSDT` streams SSE price payloads.
7. Confirm `python bian.py --symbols DOGEUSDT,TLMUSDT --json` still returns analyzer JSON.
8. Confirm each JSON report includes `backtests.long.windows` and `backtests.short.windows` with `5m`, `15m`, and `1h` entries.
9. Confirm each `timeframe_advice` item includes `candle_state`, `risk_gate`, `backtest`, and `trigger_check`.
10. Confirm TLM-style extreme volatility does not produce `stop_hint: 0` or grid `stop: 0`.
11. Confirm an extreme TLM report is downgraded to `禁止半仓` or `禁止开仓` when ATR/BOLL conditions are extreme.
12. Confirm DOGE-style short signals can still show a high historical score while entry trigger remains blocked if 1m volume or retest confirmation is weak.
13. Confirm DOGE-style direction quality can stay high while `execution_score` drops when price has not reached the ATR-based trigger zone.
14. Confirm TLM-style global `禁止开仓` appears on every timeframe advice card, not only the top-level signal.
15. Confirm dashboard position sizing and confidence bar use `execution_score`, while direction quality is shown separately.
16. Confirm backtest windows include `stopped_out_count`, `stop_rate`, `avg_loss_pct`, `net_expectancy_pct`, and `estimated_cost_pct`.
17. Confirm an unclosed 1m candle cannot produce `trigger_check.status: confirmed`; it must stay `watch`, `waiting`, or `blocked`.
18. Confirm `signal_quality.trigger_check` includes adaptive `spread_threshold_pct`, depth imbalance, and top5 bid/ask depth fields.
19. Confirm abnormal funding rate contributes to `risk_gate` downgrade when simulated or observed in API output.
20. Confirm the dashboard top signal banner shows a strong warning for `禁止开仓`, `禁止半仓`, realtime-prejudge, and position-direction conflicts.
21. Confirm historical backtest side selection uses the same threshold as online `bias_from_score` (`SIGNAL_SIDE_THRESHOLD`).
22. Confirm `expectancy_pct` and `net_expectancy_pct` differ when estimated cost is non-zero.
23. Confirm `avg_max_drawdown_pct` is price-path drawdown and is not reduced by fee/slippage cost.
24. Confirm backtest windows expose `filtered_out_count` and the dashboard labels samples as ATR/volume filtered when applicable.
25. Confirm `runtime/backtest_cache.json` is created and reused by analyzer/server calls.
26. Confirm `/api/realtime-prices?symbols=DOGEUSDT` streams `depth_imbalance`, `bid_depth_top5_usd`, and `ask_depth_top5_usd`.
27. Confirm invalid symbols return HTTP 400 instead of stale cache.
28. Confirm a signal published mid-minute ignores that minute's full OHLC high/low, while a candle opening exactly at publication remains eligible.
29. Confirm `/api/preferences` rejects a missing, zero, or negative `revision` and that the storage layer cannot upsert without a positive revision.
30. Confirm `python scripts/deploy.py --dry-run --allow-dirty --public-port 9000` writes `BIAN_PUBLIC_PORT=9000` into the remote release `.env` before Compose validation.
31. Confirm the deployment archive cleanup appears after `/api/health`, so a failed deploy command can retry with the same uploaded archive.
32. Confirm the default deploy rejects a dirty worktree and Git-ignored files never appear in the archive manifest.
33. Confirm a preference response with `saved=true, applied=false` fetches current server preferences, drops a same-field stale patch, and retries only fields unchanged since the request's base snapshot.
34. Confirm HTTP 200 with `saved=false` is treated as a retryable storage outage rather than leaving the patch idle.
35. Confirm an open SSE carrying `connected=false`, no fresh price, and an upstream error renders offline rather than connecting forever.
36. Confirm removing both default symbols leaves capacity for eight server-saved custom symbols during boot.
37. Confirm `--allow-dirty` fails closed when Git cannot read a global ignore source.
38. Confirm Docker Compose passes `BIAN_REDIS_PASSWORD` to both services, enables `requirepass`, and authenticates the Redis healthcheck.
39. Confirm configured MySQL preference storage returns HTTP 503 while unavailable and does not trigger a full localStorage snapshot writeback.
40. Confirm a stopped realtime worker that finishes connecting late cannot change connection counters, errors, timestamps, prices, or the current hub's connected state.
41. Confirm missing Origin/Referer headers remain CLI-compatible, while `Origin: null` and malformed source headers are rejected.
42. Confirm mixed unload work sends one ordered batch containing the active in-flight patch at its original revision and newer pending fields at a later revision.
43. Confirm a rejected patch is persisted before conflict reconciliation completes, survives pagehide/runtime memory loss, and retries another GET before any safe field is written.
44. Confirm `storage.mysql.configured=false` disables server preference sync while localStorage continues to work without recurring POST retries.
45. Confirm password verification locks the user row, password hash update and other-session revocation use one transaction, and a revocation failure rolls back the password update.
46. Confirm HTTP 400/401/403 preference POST and recovery GET failures remain pending without scheduling another request, while 429/503 failures continue to retry with backoff.
47. Confirm an ordered preference batch skips a stale first revision, applies a newer second revision, and rolls back all entries when a later write fails.
48. Confirm a newer same-key edit replaces the persisted recovery value and an older reconciliation completion cannot clear or replay the superseded value.
49. Confirm `round_to_tick` outputs legal tick multiples and `down <= input <= up` for fine and non-power-of-ten ticks.
50. Confirm releasing an old backtest cache lock cannot delete a replacement lock with a different owner token.
51. Confirm older bookTicker/depth exchange timestamps cannot roll back price, bid/ask, depth, or freshness, while a newer depth event can advance independently.
52. Confirm a realtime execution-score drop from 75 to 67 caps a 30% backend position at 22%, and a score below 45 produces 0%.
53. Confirm two concurrent add-symbol actions reserve capacity and a late response rechecks the eight-symbol limit before mutating dashboard data.
54. Confirm unavailable or ambiguous `/api/auth/me` stops boot and preference sync, while only `auth_enabled=false` selects local user scope.
55. Confirm a partial record with only 5m complete stays pending, is not due at 10m, and becomes due for its missing 15m result after 15m.
56. Confirm file-fallback signal reviews are merged into MySQL by user/key after recovery and removed from the file only after successful upsert.
57. Confirm slow request bodies time out, active HTTP handlers cannot exceed the configured slots, and excess SSE clients receive HTTP 503.
58. Confirm Compose binds `127.0.0.1:8000` by default, remote deploy forces loopback plus Secure cookies, and the public HTTPS proxy can reconnect EventSource after the five-minute SSE rotation.
