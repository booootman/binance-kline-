# Risk Checklist

- Static path risk: mitigated by serving `web/` as the server directory and keeping the original dashboard URL.
- CLI compatibility risk: mitigated by root-level `bian.py` and `server.py` wrappers.
- Cache migration risk: mitigated by moving the existing cache to `runtime/market_cache.json` and updating the server cache path.
- Running-server risk: a server started before this restructure must be restarted to use the new `web/` and `src/` paths.
- Git history risk: the project root is not currently a valid git repository, so changes cannot be reviewed with normal git status/diff from this folder.

