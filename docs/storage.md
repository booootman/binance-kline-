# Optional storage

The dashboard runs without MySQL or Redis. MySQL enables authentication,
per-user preferences, strategy snapshots, and live signal reviews. Redis is a
short-lived shared cache for public market and realtime payloads.

## MySQL

Configure either `BIAN_MYSQL_URL` or the individual host variables:

```text
BIAN_MYSQL_HOST=127.0.0.1
BIAN_MYSQL_PORT=3306
BIAN_MYSQL_USER=bian
BIAN_MYSQL_PASSWORD=change_me
BIAN_MYSQL_DATABASE=bian_dashboard
```

Connection, read, and write waits are bounded by
`BIAN_MYSQL_CONNECT_TIMEOUT_SECONDS`, `BIAN_MYSQL_READ_TIMEOUT_SECONDS`, and
`BIAN_MYSQL_WRITE_TIMEOUT_SECONDS`. Defaults are 3, 5, and 5 seconds.

Every preference write must use the `{ "preferences": {...}, "revision": N }`
request shape with a positive monotonic revision. Missing or invalid revisions
return HTTP 400. The server locks the per-user revision row in the same
transaction as the value updates, so an old client, late request, or
page-unload beacon cannot bypass the ordering guard and overwrite newer state.
The browser treats `applied: false` as a revision conflict, advances to the
server revision, and retries the preserved patch. A temporary HTTP 200 response
with `saved: false` is also retried with backoff instead of leaving the patch
queued indefinitely.

## Redis

Configure either `BIAN_REDIS_URL` or the individual host variables:

```text
BIAN_REDIS_HOST=127.0.0.1
BIAN_REDIS_PORT=6379
BIAN_REDIS_DB=0
```

`BIAN_REDIS_CONNECT_TIMEOUT_SECONDS` and `BIAN_REDIS_READ_TIMEOUT_SECONDS`
default to 2 seconds. A failed Redis read is treated as a cache miss and starts
a short cooldown; the request continues through memory, disk, or fresh Binance
analysis instead of failing solely because Redis is unavailable.

In Docker Compose, `BIAN_REDIS_PASSWORD` is passed to both the dashboard and
Redis containers. A non-empty value enables Redis `requirepass`, and the Redis
healthcheck authenticates with the same secret.

## Verify

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

Real MySQL and Redis integration still needs a configured deployment. The
offline smoke suite uses fake connections to verify transaction ordering,
timeouts, cache degradation, and per-user review ownership without external
services.
