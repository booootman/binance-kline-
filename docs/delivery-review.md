# Delivery Review

## Change Summary

The project root was reorganized into a maintainable dashboard project layout while preserving the old root commands.

## Primary Changes

- Python implementation moved to `src/bian_dashboard/`.
- Frontend files moved to `web/`.
- Runtime cache moved to `runtime/`.
- Restore files moved to `backups/`.
- Historical full copy moved to `archive/`.
- Startup and verification scripts added to `scripts/`.

## Verification Plan

- Compile Python files.
- Check frontend JavaScript syntax.
- Confirm analyzer CLI help works through the root compatibility entry.
- Restart local server and verify dashboard/API paths.

## Human Review Notes

- Root git metadata is not valid, so normal git diff/status is unavailable from `C:\code\bian`.
- Review file layout directly before creating a fresh repository or importing this project into source control.

