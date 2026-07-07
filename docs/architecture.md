# Architecture

## Runtime Flow

1. `server.py` starts the local HTTP server through `src/bian_dashboard/server.py`.
2. `GET /binance-futures-dashboard.html` serves the dashboard from `web/`.
3. `GET /api/market` runs `src/bian_dashboard/analyzer.py --json` in a subprocess.
4. Successful strategy snapshots are cached in memory and in `runtime/market_cache.json`.
5. `GET /api/realtime-prices` streams futures bookTicker prices through SSE.
6. `web/assets/charts.js` renders strategy snapshots, realtime price age, risk alerts, and signal history.

## Directory Responsibilities

```text
src/bian_dashboard/
  analyzer.py   REST data fetch, indicators, strategy advice JSON
  server.py     HTTP server, API, cache, realtime SSE

web/
  binance-futures-dashboard.html
  assets/
    charts.js   frontend app logic
    data.js     static fallback data
  _shared/      bundled fonts and ECharts

runtime/
  market_cache.json

scripts/
  start.ps1
  verify.ps1

backups/
  old generated files and manual restore points

archive/
  old full project copies
```

## Compatibility

The root-level `bian.py` and `server.py` files are intentionally kept as stable entry points. External scripts and manual commands can continue using the old names while the implementation lives under `src/`.

