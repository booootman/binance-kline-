# Docker deployment

This project can run as a Docker Compose stack with three isolated services:

- `bian-dashboard`: Python HTTP dashboard and API.
- `bian-dashboard-mysql`: optional preference and strategy snapshot storage.
- `bian-dashboard-redis`: optional hot cache for market and realtime data.

## Start

```bash
cp .env.example .env
docker compose up -d --build
```

Edit `.env` before starting the stack. With `BIAN_AUTH_ENABLED=1`, a fresh
database must have `BIAN_AUTH_BOOTSTRAP_PASSWORD` set so the first admin user
can be created. If the users table is empty and that value is blank, `/api/health`
returns HTTP 503 with `auth.issue=first_admin_secret_missing`, and the dashboard
container is expected to become unhealthy instead of silently showing an
unusable login page.

Compose binds the dashboard upstream to `127.0.0.1:8000` by default. Production
traffic must terminate TLS at a reverse proxy or load balancer and forward to
that loopback address. The application port is not exposed directly to the
internet.

For local HTTP-only development, explicitly set `BIAN_AUTH_COOKIE_SECURE=0`.
Do not use that override on a remote or shared host.

## One-command deploy from Windows/macOS/Linux

From the project root:

```bash
python scripts/deploy.py
```

The defaults target `root@159.223.91.36`, use the private key at `~/Desktop/id_ed25519`,
require a clean Git worktree, upload only selected Git files, preserve the remote
`.env` if it already exists, and run:

```bash
docker compose up -d --build
```

Useful options:

```bash
python scripts/deploy.py --host 159.223.91.36 --key C:\Users\WIN11\Desktop\id_ed25519
python scripts/deploy.py --retries 5 --retry-delay 20 --scp-limit-kbps 1024
python scripts/deploy.py --public-port 9000 --check-market
python scripts/deploy.py --no-ufw
python scripts/deploy.py --allow-dirty --dry-run
```

The deployed Compose port remains bound to loopback even if UFW has a matching
rule. Configure the public firewall for the TLS proxy's port 443, not the
dashboard upstream port.
`--check-market` only curls `/api/market` when auth is disabled; with auth enabled,
verify market data after logging in.

The default deployment refuses modified or untracked files. `--allow-dirty` is
an explicit development override that includes modified tracked files and
untracked non-ignored files; Git-ignored files and the local `.env` are never
packaged. Commit and review production changes before deploying whenever
possible.

The dirty-worktree override keeps repository, global, and Git-info ignore rules
enabled. If Git reports that an ignore source cannot be read, packaging fails
closed instead of treating globally ignored files as upload candidates.

The remote archive is extracted into a temporary release directory. The
previous application directory remains as `/opt/bian-dashboard.previous` until
the new Compose stack passes `/api/health`, and the uploaded archive is deleted
only after success so a failed SSH deployment step can reuse it. `--public-port`
updates `BIAN_PUBLIC_PORT` in the preserved remote `.env`, so later manual
Compose restarts keep the selected port.

## Environment

- `BIAN_BIND_ADDRESS`: host bind address, production default `127.0.0.1`. Do not change it to `0.0.0.0` for an authenticated deployment.
- `BIAN_PUBLIC_PORT`: loopback upstream port used by the TLS proxy, default `8000`.
- `BIAN_LOG_LEVEL`: Python log level, default `INFO`.
- `BIAN_STORAGE_USER_ID`: preference namespace, default `default`.
- `BIAN_AUTH_ENABLED`: enable server-side login gate, default `1`.
- `BIAN_AUTH_BOOTSTRAP_USER` / `BIAN_AUTH_BOOTSTRAP_PASSWORD`: only used when the MySQL users table is empty, to create the first admin account. Accounts and sessions are stored in MySQL after that. Do not leave `BIAN_AUTH_BOOTSTRAP_PASSWORD` blank on a fresh deployment.
- `BIAN_AUTH_SESSION_TTL_SECONDS`: session lifetime, default `604800`.
- `BIAN_AUTH_COOKIE_SECURE`: default `1`; authenticated production deployments require HTTPS.
- `BIAN_AUTH_MAX_FAILURES` / `BIAN_AUTH_LOCKOUT_SECONDS`: basic per-IP login failure lockout.
- `BIAN_AUTH_TRUST_PROXY_HEADERS`: default `0`; set to `1` only when the app is behind a trusted local/private reverse proxy that sets `X-Forwarded-For`.
- `BIAN_AUTH_SESSION_TOUCH_INTERVAL_SECONDS`: minimum interval for updating session `last_seen_at`, default `60`, to avoid writing MySQL on every protected request.
- `BIAN_AUTH_REQUIRE_SAME_ORIGIN_POST`: default `1`; dashboard POST APIs, including login, reject browser requests whose `Origin`/`Referer` host does not match `Host` or a trusted `X-Forwarded-Host`. `X-Forwarded-Host` is trusted only when `BIAN_AUTH_TRUST_PROXY_HEADERS=1` and the peer is a local/private reverse proxy.
- `BIAN_EXPOSE_ERROR_DETAILS`: default `0`; keep disabled on public deployments so `/api/market` returns only classified user-facing errors instead of analyzer `detail`/`stderr`. Temporarily set to `1` only while debugging server logs and upstream failures.
- `BIAN_SSE_MAX_SECONDS`: maximum lifetime of one browser SSE response, default `300`. EventSource reconnects automatically.
- `BIAN_SSE_MAX_CLIENTS`: maximum simultaneous SSE clients, default `12`.
- `BIAN_HTTP_MAX_CONCURRENT_REQUESTS` / `BIAN_HTTP_REQUEST_QUEUE_SIZE`: cap active request handlers and the socket backlog; defaults are `32` / `64`.
- `BIAN_HTTP_REQUEST_TIMEOUT_SECONDS`: socket read/write timeout, default `15` seconds.
- `BIAN_REALTIME_STALE_SECONDS`: restart a connected realtime worker after this many seconds without a Binance message, default `45`.
- `BIAN_MYSQL_CONNECT_TIMEOUT_SECONDS` / `BIAN_MYSQL_READ_TIMEOUT_SECONDS` / `BIAN_MYSQL_WRITE_TIMEOUT_SECONDS`: bound MySQL stalls; defaults are `3` / `5` / `5` seconds.
- `BIAN_REDIS_CONNECT_TIMEOUT_SECONDS` / `BIAN_REDIS_READ_TIMEOUT_SECONDS`: bound Redis stalls; both default to `2` seconds. Failed reads degrade to the next cache/analyzer layer.
- `BIAN_REDIS_PASSWORD`: optional Redis authentication secret. A non-empty value is passed to the dashboard, enables Redis `requirepass`, and is used by the container healthcheck. The deployment script generates it on a fresh remote `.env`.
- `BIAN_STRATEGY_SNAPSHOT_LIMIT`: retained strategy snapshots per authenticated storage user, default `1000`.
- `BIAN_SIGNAL_REVIEW_TAKER_FEE_BPS` / `BIAN_SIGNAL_REVIEW_SLIPPAGE_BPS`: one-way fee/slippage assumptions used when live-review outcomes are shown net of estimated round-trip cost; defaults are `5` and `2`.
- `BIAN_MYSQL_USER`, `BIAN_MYSQL_PASSWORD`, `BIAN_MYSQL_ROOT_PASSWORD`, `BIAN_MYSQL_DATABASE`.

## Login

After configuring TLS termination, open:

```text
https://YOUR_DASHBOARD_HOST/login
```

The deployment script prints the first bootstrap password when it has to create one.
After the first login account exists, credentials live in MySQL as password hashes and sessions live in MySQL as hashed session tokens.
After logging in, use the top-right `改密码` button to change the current user's password. The old password is required, and other sessions for the same user are invalidated after a successful change.
Admin users can use the top-right `注册账号` button to create additional `user` or `admin` accounts. Public anonymous registration is intentionally disabled.

## Verify

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
```

`/api/health` is public and returns only non-secret diagnostics such as uptime,
auth readiness, MySQL/Redis availability, cache counts, and whether required
runtime paths exist. It returns HTTP 503 when auth is enabled but MySQL is not
available, which is intentional for deployment health checks.

Example Nginx location for the HTTPS virtual host:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_buffering off;
    proxy_read_timeout 10m;
}
```

After logging in, verify the protected dashboard APIs from the browser, or use
curl with the dashboard session cookie:

- `/api/market?symbols=DOGEUSDT,TLMUSDT`
- `/api/storage-status`
- `/api/diagnostics` (admin role required)

These endpoints are intentionally behind the dashboard session cookie.

## Live-review cost assumptions

Live signal review shows `max_profit_pct` and `outcome_pct` net of the configured
estimated round-trip fee/slippage. The raw price-path values are still exported
as `gross_max_profit_pct` and `gross_outcome_pct` for audit.

When reviewing exported CSV files, use the net fields (`max_profit_pct` and
`outcome_pct`) for hit-rate and expectancy decisions. Use the gross fields only
to inspect the raw price path before estimated execution cost.

This is still an assisted review metric, not a full execution PnL simulator. It
does not model funding payments, liquidation, partial fills, order-book queue
position, exchange outages, margin mode, or slippage expansion during fast moves.
Use the numbers to compare signal quality, not as guaranteed trade results.
