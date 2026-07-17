#!/usr/bin/env python3
"""Package and deploy the dashboard over SSH using only standard tools."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import posixpath
import secrets
import shlex
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".gitnexus",
    ".claude",
    "__pycache__",
    "archive",
    "backups",
    "node_modules",
}
REQUIRED_DEPLOY_FILES = {
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "scripts/deploy.py",
    "src/bian_dashboard/server.py",
    "web/binance-futures-dashboard.html",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Deploy Binance dashboard with ssh, scp, and Docker Compose.")
    parser.add_argument("--host", default="159.223.91.36", help="SSH host")
    parser.add_argument("--user", default="root", help="SSH user")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--key", default=str(Path.home() / "Desktop" / "id_ed25519"), help="SSH private key")
    parser.add_argument("--remote-dir", default="/opt/bian-dashboard", help="Remote application directory")
    parser.add_argument("--public-port", type=int, default=8000, help="Published dashboard port")
    parser.add_argument("--public-url", default="", help="Public HTTPS dashboard URL verified after deployment")
    parser.add_argument(
        "--allow-no-public-url",
        action="store_true",
        help="Skip the public HTTPS health check for an explicitly local development deployment",
    )
    parser.add_argument("--retries", type=int, default=3, help="Attempts for each SSH/SCP step")
    parser.add_argument("--retry-delay", type=float, default=10, help="Seconds between retries")
    parser.add_argument("--scp-limit-kbps", type=int, default=0, help="Optional SCP bandwidth cap")
    parser.add_argument("--check-market", action="store_true", help="Check /api/market when auth is disabled")
    parser.add_argument("--no-ufw", action="store_true", help="Do not add a source-IP UFW rule")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Deploy modified and untracked non-ignored files instead of requiring a clean Git worktree",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without connecting")
    return parser.parse_args()


def normalize_public_url(value):
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("--public-url must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("--public-url must contain only the HTTPS origin, without credentials, path, query, or fragment")
    hostname = str(parsed.hostname or "").strip().lower()
    if hostname == "localhost":
        raise ValueError("--public-url must not use a loopback or private address")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("--public-url must not use a loopback or private address")
    return f"https://{parsed.netloc}"


def validate_deploy_args(args):
    try:
        args.public_url = normalize_public_url(getattr(args, "public_url", ""))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not args.public_url and not getattr(args, "allow_no_public_url", False):
        raise SystemExit("--public-url is required; use --allow-no-public-url only for an explicitly local development deployment")


def archive_release_id(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(secrets.token_bytes(32))
    return digest.hexdigest()


def release_health_url(public_url, release_id):
    return public_url.rstrip("/") + "/api/health?" + urlencode({"release_id": release_id})


def verify_public_health(args, release_id):
    if not args.public_url:
        return
    url = release_health_url(args.public_url, release_id)
    if args.dry_run:
        print("GET " + url)
        return
    last_error = None
    attempts = max(1, int(args.retries))
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
            with urlopen(request, timeout=15) as response:
                final_url = urlsplit(response.geturl())
                payload = json.loads(response.read().decode("utf-8"))
            expected_origin = urlsplit(args.public_url)
            if (
                final_url.scheme.lower() != expected_origin.scheme.lower()
                or final_url.netloc.lower() != expected_origin.netloc.lower()
                or not isinstance(payload, dict)
                or payload.get("ok") is not True
                or payload.get("release_match") is not True
                or payload.get("release_id") != release_id
            ):
                raise RuntimeError("public health returned a different release")
            return
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(max(0, args.retry_delay))
    raise SystemExit(f"public HTTPS health verification failed for release {release_id}: {last_error}")


def run_checked(command, retries, retry_delay, dry_run=False):
    printable = subprocess.list2cmdline(command)
    if dry_run:
        print(printable)
        return
    last_error = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            subprocess.run(command, check=True)
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if attempt >= max(1, retries):
                break
            print(f"command failed (attempt {attempt}); retrying in {retry_delay:g}s: {printable}")
            time.sleep(max(0, retry_delay))
    raise SystemExit(last_error.returncode if last_error else 1)


def git_output(*args, require_ignore_integrity=False):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    warning = result.stderr.decode("utf-8", errors="replace").strip()
    if require_ignore_integrity and "unable to access" in warning.lower() and "ignore" in warning.lower():
        raise RuntimeError("cannot safely read Git ignore configuration: " + warning)
    return result.stdout


def worktree_changes():
    return git_output("status", "--porcelain=v1", "--untracked-files=all").decode("utf-8", errors="replace").strip()


def deployment_files(include_untracked=False):
    args = ["ls-files", "--cached"]
    if include_untracked:
        args.extend(["--others", "--exclude-standard"])
    args.append("-z")
    names = git_output(*args, require_ignore_integrity=include_untracked).decode("utf-8", errors="surrogateescape").split("\0")
    files = []
    for name in names:
        if not name:
            continue
        parts = Path(name).parts
        if any(part in EXCLUDED_PARTS for part in parts):
            continue
        if parts and parts[-1] == ".env":
            continue
        if "runtime" in parts or name.endswith((".pyc", ".pyo", ".tmp", ".log")):
            continue
        source = ROOT / name
        if source.is_file() or source.is_symlink():
            files.append(name.replace("\\", "/"))
    missing = sorted(REQUIRED_DEPLOY_FILES.difference(files))
    if missing:
        raise RuntimeError("required deployment files are not tracked or selected: " + ", ".join(missing))
    return sorted(set(files))


def build_archive(destination, include_untracked=False):
    with tarfile.open(destination, "w:gz") as archive:
        for name in deployment_files(include_untracked=include_untracked):
            archive.add(ROOT / name, arcname=name, recursive=False)


def ssh_base(args):
    command = ["ssh", "-p", str(args.port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if args.key:
        command.extend(["-i", str(Path(args.key).expanduser())])
    return command


def scp_base(args):
    command = ["scp", "-P", str(args.port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if args.key:
        command.extend(["-i", str(Path(args.key).expanduser())])
    if args.scp_limit_kbps > 0:
        command.extend(["-l", str(args.scp_limit_kbps)])
    return command


def remote_script(args, remote_archive, release_id):
    remote_dir = shlex.quote(args.remote_dir)
    remote_parent = shlex.quote(posixpath.dirname(args.remote_dir.rstrip("/")) or "/")
    release_template = shlex.quote(args.remote_dir.rstrip("/") + ".release.XXXXXX")
    backup_dir = shlex.quote(args.remote_dir.rstrip("/") + ".previous")
    archive = shlex.quote(remote_archive)
    release_id = str(release_id or "").strip()
    if len(release_id) != 64 or any(char not in "0123456789abcdef" for char in release_id.lower()):
        raise ValueError("release_id must be a 64-character hexadecimal digest")
    release_id = release_id.lower()
    public_url = normalize_public_url(getattr(args, "public_url", ""))
    public_check = ""
    if public_url:
        public_health_url = shlex.quote(release_health_url(public_url, release_id))
        public_check = f"curl -fsS --retry 6 --retry-delay 5 --max-time 15 --proto '=https' --tlsv1.2 {public_health_url} >/dev/null"
    market_check = ""
    if args.check_market:
        market_check = f"""
if grep -Eq '^BIAN_AUTH_ENABLED=(0|false|no|off)$' .env 2>/dev/null; then
  curl -fsS --max-time 120 'http://127.0.0.1:{args.public_port}/api/market?symbols=DOGEUSDT,TLMUSDT' >/dev/null
else
  echo 'market check skipped because authentication is enabled'
fi
"""
    ufw = ""
    if not args.no_ufw:
        ufw = f"""
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  client_ip=${{SSH_CLIENT%% *}}
  if [ -n "$client_ip" ]; then
    ufw allow from "$client_ip" to any port {args.public_port} proto tcp >/dev/null
  fi
fi
"""
    return f"""set -eu
mkdir -p {remote_parent}
release_dir=$(mktemp -d {release_template})
trap 'if [ -n "$release_dir" ]; then rm -rf "$release_dir"; fi' EXIT
tar -xzf {archive} -C "$release_dir"
if [ -f {remote_dir}/.env ]; then
  chmod 600 {remote_dir}/.env
fi
if [ -f {backup_dir}/.env ]; then
  chmod 600 {backup_dir}/.env
fi
if [ -f {remote_dir}/.env ]; then
  cp -p {remote_dir}/.env "$release_dir/.env"
elif [ -f {backup_dir}/.env ]; then
  cp -p {backup_dir}/.env "$release_dir/.env"
else
  cp "$release_dir/.env.example" "$release_dir/.env"
  bootstrap_password=$(openssl rand -hex 18)
  mysql_password=$(openssl rand -hex 18)
  mysql_root_password=$(openssl rand -hex 18)
  redis_password=$(openssl rand -hex 18)
  sed -i "s/^BIAN_AUTH_BOOTSTRAP_PASSWORD=.*/BIAN_AUTH_BOOTSTRAP_PASSWORD=$bootstrap_password/" "$release_dir/.env"
  sed -i "s/^BIAN_MYSQL_PASSWORD=.*/BIAN_MYSQL_PASSWORD=$mysql_password/" "$release_dir/.env"
  sed -i "s/^BIAN_MYSQL_ROOT_PASSWORD=.*/BIAN_MYSQL_ROOT_PASSWORD=$mysql_root_password/" "$release_dir/.env"
  sed -i "s/^BIAN_REDIS_PASSWORD=.*/BIAN_REDIS_PASSWORD=$redis_password/" "$release_dir/.env"
  echo "first admin password: $bootstrap_password"
fi
if grep -q '^BIAN_PUBLIC_PORT=' "$release_dir/.env"; then
  sed -i "s/^BIAN_PUBLIC_PORT=.*/BIAN_PUBLIC_PORT={args.public_port}/" "$release_dir/.env"
else
  printf '\nBIAN_PUBLIC_PORT={args.public_port}\n' >> "$release_dir/.env"
fi
if grep -q '^BIAN_BIND_ADDRESS=' "$release_dir/.env"; then
  sed -i 's/^BIAN_BIND_ADDRESS=.*/BIAN_BIND_ADDRESS=127.0.0.1/' "$release_dir/.env"
else
  printf 'BIAN_BIND_ADDRESS=127.0.0.1\n' >> "$release_dir/.env"
fi
if grep -q '^BIAN_AUTH_COOKIE_SECURE=' "$release_dir/.env"; then
  sed -i 's/^BIAN_AUTH_COOKIE_SECURE=.*/BIAN_AUTH_COOKIE_SECURE=1/' "$release_dir/.env"
else
  printf 'BIAN_AUTH_COOKIE_SECURE=1\n' >> "$release_dir/.env"
fi
if grep -q '^BIAN_RELEASE_ID=' "$release_dir/.env"; then
  sed -i 's/^BIAN_RELEASE_ID=.*/BIAN_RELEASE_ID={release_id}/' "$release_dir/.env"
else
  printf 'BIAN_RELEASE_ID={release_id}\n' >> "$release_dir/.env"
fi
chmod 600 "$release_dir/.env"
cd "$release_dir"
docker compose config >/dev/null
if [ -d {remote_dir} ]; then
  if [ -d {backup_dir} ]; then
    rm -rf {remote_dir}
  else
    mv {remote_dir} {backup_dir}
  fi
fi
if [ -f {backup_dir}/.env ]; then
  chmod 600 {backup_dir}/.env
fi
mv "$release_dir" {remote_dir}
release_dir=''
cd {remote_dir}
docker compose up -d --build
{ufw}
curl -fsS --retry 12 --retry-delay 5 --max-time 10 'http://127.0.0.1:{args.public_port}/api/health?release_id={release_id}' >/dev/null
{public_check}
{market_check}
docker compose ps
trap - EXIT
"""


def finalize_remote_script(args, remote_archive):
    backup_dir = shlex.quote(args.remote_dir.rstrip("/") + ".previous")
    archive = shlex.quote(remote_archive)
    return f"""set -eu
rm -rf {backup_dir}
rm -f {archive}
"""


def main():
    args = parse_args()
    validate_deploy_args(args)
    changes = worktree_changes()
    if changes and not args.allow_dirty:
        raise SystemExit("refusing to deploy a dirty Git worktree; review/commit changes or pass --allow-dirty explicitly")
    if changes:
        print("warning: deploying modified and untracked non-ignored files because --allow-dirty was specified")
    target = f"{args.user}@{args.host}"
    remote_archive = f"/tmp/bian-dashboard-{os.getpid()}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="bian-deploy-") as temp_dir:
        archive_path = Path(temp_dir) / "bian-dashboard.tar.gz"
        build_archive(archive_path, include_untracked=args.allow_dirty)
        release_id = archive_release_id(archive_path)
        mkdir_command = ssh_base(args) + [target, "mkdir -p /tmp"]
        upload_command = scp_base(args) + [str(archive_path), f"{target}:{remote_archive}"]
        deploy_command = ssh_base(args) + [target, "bash -lc " + shlex.quote(remote_script(args, remote_archive, release_id))]
        finalize_command = ssh_base(args) + [target, "bash -lc " + shlex.quote(finalize_remote_script(args, remote_archive))]
        run_checked(mkdir_command, args.retries, args.retry_delay, args.dry_run)
        run_checked(upload_command, args.retries, args.retry_delay, args.dry_run)
        run_checked(deploy_command, args.retries, args.retry_delay, args.dry_run)
        verify_public_health(args, release_id)
        run_checked(finalize_command, args.retries, args.retry_delay, args.dry_run)


if __name__ == "__main__":
    main()
