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

The dashboard listens on `http://SERVER_IP:8000/binance-futures-dashboard.html` by default.

## One-command deploy from Windows/macOS/Linux

From the project root:

```bash
python scripts/deploy.py
```

The defaults target `root@159.223.91.36`, use the private key at `~/Desktop/id_ed25519`,
upload a clean project package, preserve the remote `.env` if it already exists, and run:

```bash
docker compose up -d --build
```

Useful options:

```bash
python scripts/deploy.py --host 159.223.91.36 --key C:\Users\WIN11\Desktop\id_ed25519
python scripts/deploy.py --retries 5 --retry-delay 20 --scp-limit-kbps 1024
python scripts/deploy.py --public-port 8000 --check-market
python scripts/deploy.py --no-ufw
```

By default, when UFW is active on the server, the script only allows the current SSH client IP to access the dashboard port. It does not open port `8000` to the whole internet.
`--check-market` only curls `/api/market` when auth is disabled; with auth enabled,
verify market data after logging in.

## Environment

- `BIAN_PUBLIC_PORT`: public host port, default `8000`.
- `BIAN_LOG_LEVEL`: Python log level, default `INFO`.
- `BIAN_STORAGE_USER_ID`: preference namespace, default `default`.
- `BIAN_AUTH_ENABLED`: enable server-side login gate, default `1`.
- `BIAN_AUTH_BOOTSTRAP_USER` / `BIAN_AUTH_BOOTSTRAP_PASSWORD`: only used when the MySQL users table is empty, to create the first admin account. Accounts and sessions are stored in MySQL after that. Do not leave `BIAN_AUTH_BOOTSTRAP_PASSWORD` blank on a fresh deployment.
- `BIAN_AUTH_SESSION_TTL_SECONDS`: session lifetime, default `604800`.
- `BIAN_AUTH_COOKIE_SECURE`: set to `1` when running behind HTTPS.
- `BIAN_AUTH_MAX_FAILURES` / `BIAN_AUTH_LOCKOUT_SECONDS`: basic per-IP login failure lockout.
- `BIAN_AUTH_TRUST_PROXY_HEADERS`: default `0`; set to `1` only when the app is behind a trusted local/private reverse proxy that sets `X-Forwarded-For`.
- `BIAN_AUTH_SESSION_TOUCH_INTERVAL_SECONDS`: minimum interval for updating session `last_seen_at`, default `60`, to avoid writing MySQL on every protected request.
- `BIAN_AUTH_REQUIRE_SAME_ORIGIN_POST`: default `1`; dashboard POST APIs, including login, reject browser requests whose `Origin`/`Referer` host does not match `Host` or a trusted `X-Forwarded-Host`. `X-Forwarded-Host` is trusted only when `BIAN_AUTH_TRUST_PROXY_HEADERS=1` and the peer is a local/private reverse proxy.
- `BIAN_EXPOSE_ERROR_DETAILS`: default `0`; keep disabled on public deployments so `/api/market` returns only classified user-facing errors instead of analyzer `detail`/`stderr`. Temporarily set to `1` only while debugging server logs and upstream failures.
- `BIAN_SIGNAL_REVIEW_TAKER_FEE_BPS` / `BIAN_SIGNAL_REVIEW_SLIPPAGE_BPS`: one-way fee/slippage assumptions used when live-review outcomes are shown net of estimated round-trip cost; defaults are `5` and `2`.
- `BIAN_MYSQL_USER`, `BIAN_MYSQL_PASSWORD`, `BIAN_MYSQL_ROOT_PASSWORD`, `BIAN_MYSQL_DATABASE`.

## Login

Open:

```text
http://SERVER_IP:8000/login
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
