# Acceptance Checklist

- [x] Root directory no longer mixes source, static web files, runtime cache, backups, and archived copies.
- [x] `python bian.py --help` still works through the compatibility CLI entry.
- [x] `python server.py` remains the compatibility server entry.
- [x] Dashboard URL remains `/binance-futures-dashboard.html`.
- [x] Static resources resolve from `web/assets/` and `web/_shared/`.
- [x] Runtime cache path is isolated under `runtime/market_cache.json`.

