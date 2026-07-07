# Risk Checklist

- Static path risk: mitigated by serving `web/` as the server directory and keeping the original dashboard URL.
- CLI compatibility risk: mitigated by root-level `bian.py` and `server.py` wrappers.
- Cache migration risk: mitigated by moving the existing cache to `runtime/market_cache.json` and updating the server cache path.
- Running-server risk: a server started before this restructure must be restarted to use the new `web/` and `src/` paths.
- Historical-backtest risk: the current backtest is still a rolling signal-quality estimate; it now shares the same side threshold as online scoring, models stop-first path plus estimated fee/slippage, but does not model funding over the holding window, liquidation, partial fills, or account margin.
- Backtest-sample risk: historical samples are filtered by ATR and volume before being counted; displayed win rate only represents signals that pass those filters.
- Realtime-candle risk: `实时预判` candles can change before close; backend trigger confirmation must not emit `confirmed` from an unclosed 1m candle.
- Funding-risk: abnormal funding now participates in the risk gate; `abs(funding_rate) >= 0.1%` should downgrade at least to `禁止半仓`, and `>= 0.2%` should force `禁止开仓`.
- Small-coin risk: when ATR/BOLL expands extremely, the analyzer should downgrade to `禁止半仓` or `禁止开仓`; do not override this for illiquid contracts without manual review.
- Trigger risk: entry levels are not executable instructions until 1m volume, structure, spread, distance, and order-book depth/imbalance checks confirm.
- Depth risk: if Binance depth is unavailable or top5 depth is too thin, trigger confirmation should be conservative instead of treating missing depth as pass; SSE now streams depth20 but still does not model order placement queue position.
- Cache/error risk: `/api/market` separates bad requests, rate limits, timeouts, and hard upstream failures; only rate-limit/timeout/network paths should return stale cache.
- Score-interpretation risk: `direction_score` measures directional quality, while `execution_score` measures whether it is tradable now; only `execution_score` should drive position sizing.
- Global-gate risk: when top-level risk is `禁止开仓`, all timeframe cards must inherit it even if a lower timeframe looks directional.
- Stop-risk: stops are tick-size legal hints based on recent high/low and ATR, not exchange-submitted stop orders.
- Git review risk: review `git status --short` and `git diff` before staging because strategy and UI fields changed together in this feature.
