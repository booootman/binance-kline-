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

