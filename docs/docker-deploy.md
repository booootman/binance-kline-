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

## Environment

- `BIAN_PUBLIC_PORT`: public host port, default `8000`.
- `BIAN_LOG_LEVEL`: Python log level, default `INFO`.
- `BIAN_STORAGE_USER_ID`: preference namespace, default `default`.
- `BIAN_AUTH_ENABLED`: enable server-side login gate, default `1`.
- `BIAN_AUTH_BOOTSTRAP_USER` / `BIAN_AUTH_BOOTSTRAP_PASSWORD`: only used when the MySQL users table is empty, to create the first admin account. Accounts and sessions are stored in MySQL after that.
- `BIAN_AUTH_SESSION_TTL_SECONDS`: session lifetime, default `604800`.
- `BIAN_AUTH_COOKIE_SECURE`: set to `1` when running behind HTTPS.
- `BIAN_AUTH_MAX_FAILURES` / `BIAN_AUTH_LOCKOUT_SECONDS`: basic per-IP login failure lockout.
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
curl -fsS http://127.0.0.1:8000/api/storage-status
curl -fsS "http://127.0.0.1:8000/api/market?symbols=DOGEUSDT,TLMUSDT"
```
