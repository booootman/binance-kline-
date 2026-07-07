# Binance Dashboard Project Notes

## Scope

This project is a local Binance futures analysis dashboard.

## Important Paths

- `src/bian_dashboard/analyzer.py`: Binance REST analyzer and strategy snapshot generator.
- `src/bian_dashboard/server.py`: local HTTP API, static file server, cache, and realtime price SSE.
- `web/binance-futures-dashboard.html`: dashboard page.
- `web/assets/charts.js`: dashboard behavior and chart rendering.
- `runtime/market_cache.json`: runtime fallback cache.
- `scripts/verify.ps1`: local verification script.

## Commands

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
python bian.py --symbols DOGEUSDT,TLMUSDT --json
```

## Rules

- Preserve root compatibility entries `bian.py` and `server.py`.
- Keep runtime data under `runtime/`.
- Keep frontend static resources under `web/`.
- Do not delete files from `backups/` or `archive/` without explicit user approval.
- Avoid changing strategy math and UI structure in the same change unless the user asks for both.

