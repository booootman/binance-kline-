# Regression Cases

1. Start the server with `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`.
2. Open `http://127.0.0.1:8000/binance-futures-dashboard.html`.
3. Confirm the dashboard loads fonts, ECharts, and `web/assets/charts.js`.
4. Call `http://127.0.0.1:8000/api/market?symbols=DOGEUSDT,TLMUSDT` and confirm JSON data is returned.
5. Confirm `runtime/market_cache.json` updates after a successful market request.
6. Confirm `/api/realtime-prices?symbols=DOGEUSDT,TLMUSDT` streams SSE price payloads.
7. Confirm `python bian.py --symbols DOGEUSDT,TLMUSDT --json` still returns analyzer JSON.

