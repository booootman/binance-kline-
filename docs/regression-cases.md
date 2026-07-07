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
