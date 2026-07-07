#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional MySQL and Redis storage for the dashboard.

The dashboard remains fully usable without these dependencies. When MySQL or
Redis environment variables are present and the matching Python driver is
installed, this module persists browser preferences and caches hot market data.
"""
from __future__ import annotations

import json
import logging
import os
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse


LOG = logging.getLogger("bian-dashboard.storage")
PASSWORD_HASH_ITERATIONS = 260000


def _utc_datetime(value=None):
    return value or datetime.now(timezone.utc)


def _mysql_dt(value):
    return _utc_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt.encode("ascii"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password, stored_hash):
    try:
        algo, iterations, salt, expected = str(stored_hash or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            salt.encode("ascii"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def session_token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


class DashboardStorage:
    def __init__(self):
        self.user_id = os.environ.get("BIAN_STORAGE_USER_ID", "default").strip() or "default"
        self.mysql_configured = bool(os.environ.get("BIAN_MYSQL_URL") or os.environ.get("BIAN_MYSQL_HOST"))
        self.redis_configured = bool(os.environ.get("BIAN_REDIS_URL") or os.environ.get("BIAN_REDIS_HOST"))
        self._mysql_driver = None
        self._redis_client = None
        self._mysql_checked = False
        self._redis_checked = False
        self._redis_block_until = 0.0

    def status(self):
        return {
            "user_id": self.user_id,
            "mysql": {
                "configured": self.mysql_configured,
                "available": self.mysql_available(),
                "driver": self._mysql_driver,
            },
            "redis": {
                "configured": self.redis_configured,
                "available": self.redis_available(),
            },
            "auth": self.auth_status(),
        }

    def auth_status(self):
        configured = bool(os.environ.get("BIAN_AUTH_ENABLED", "1").lower() not in ("0", "false", "no", "off"))
        return {
            "enabled": configured,
            "mysql_available": self.mysql_available(),
            "bootstrap_user": os.environ.get("BIAN_AUTH_BOOTSTRAP_USER", "admin"),
        }

    def mysql_available(self):
        if not self.mysql_configured:
            return False
        if self._mysql_checked and self._mysql_driver:
            return True
        try:
            conn = self._mysql_connect()
            conn.close()
            return True
        except Exception as exc:
            LOG.warning("mysql storage unavailable: %s", exc)
            return False

    def redis_available(self):
        if not self.redis_configured:
            return False
        if self._redis_client is not None:
            return True
        try:
            client = self._redis_connect()
            client.ping()
            self._redis_client = client
            return True
        except Exception as exc:
            LOG.warning("redis cache unavailable: %s", exc)
            return False

    def load_preferences(self):
        if not self.mysql_available():
            return {}
        conn = self._mysql_connect()
        try:
            self._ensure_mysql_schema(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT item_key, value_json FROM bian_dashboard_kv WHERE user_id=%s",
                (self.user_id,),
            )
            prefs = {}
            for key, raw in cur.fetchall():
                try:
                    prefs[key] = json.loads(raw)
                except Exception:
                    prefs[key] = None
            return prefs
        finally:
            conn.close()

    def save_preferences(self, prefs):
        if not isinstance(prefs, dict) or not prefs:
            return False
        if not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_mysql_schema(conn)
            cur = conn.cursor()
            rows = [
                (self.user_id, str(key), json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                for key, value in prefs.items()
            ]
            cur.executemany(
                """
                INSERT INTO bian_dashboard_kv (user_id, item_key, value_json)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE value_json=VALUES(value_json), updated_at=CURRENT_TIMESTAMP
                """,
                rows,
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def save_strategy_snapshot(self, symbols, payload):
        if not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_mysql_schema(conn)
            cur = conn.cursor()
            symbols_json = json.dumps(symbols or [], ensure_ascii=False, separators=(",", ":"))
            payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
            cur.execute(
                """
                INSERT INTO bian_strategy_snapshots
                  (user_id, symbols_key, symbols_json, generated_at, payload_json)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    self.user_id,
                    ",".join(symbols or []),
                    symbols_json,
                    (payload or {}).get("generated_at"),
                    payload_json,
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def ensure_auth_bootstrap(self):
        if not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM bian_auth_users")
            count = int(cur.fetchone()[0])
            if count > 0:
                return True
            username = (os.environ.get("BIAN_AUTH_BOOTSTRAP_USER") or "admin").strip()
            password = os.environ.get("BIAN_AUTH_BOOTSTRAP_PASSWORD", "").strip()
            if not username or not password:
                LOG.warning("auth has no users and BIAN_AUTH_BOOTSTRAP_PASSWORD is empty; login is locked")
                return False
            cur.execute(
                """
                INSERT INTO bian_auth_users (username, password_hash, role, disabled)
                VALUES (%s, %s, %s, 0)
                """,
                (username, hash_password(password), "admin"),
            )
            conn.commit()
            LOG.warning("created bootstrap auth user: %s", username)
            return True
        finally:
            conn.close()

    def verify_auth_user(self, username, password):
        if not self.mysql_available():
            return None
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, username, password_hash, role, disabled
                FROM bian_auth_users
                WHERE username=%s
                LIMIT 1
                """,
                ((username or "").strip(),),
            )
            row = cur.fetchone()
            if not row or int(row[4] or 0):
                return None
            if not verify_password(password, row[2]):
                return None
            cur.execute("UPDATE bian_auth_users SET last_login_at=UTC_TIMESTAMP() WHERE id=%s", (row[0],))
            conn.commit()
            return {"id": int(row[0]), "username": row[1], "role": row[3] or "user"}
        finally:
            conn.close()

    def create_auth_user(self, username, password, role="user"):
        if not self.mysql_available():
            return None, "auth database is not ready"
        username = (username or "").strip()
        role = "admin" if str(role or "").strip().lower() == "admin" else "user"
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute("SELECT id FROM bian_auth_users WHERE username=%s LIMIT 1", (username,))
            if cur.fetchone():
                return None, "username already exists"
            cur.execute(
                """
                INSERT INTO bian_auth_users (username, password_hash, role, disabled)
                VALUES (%s, %s, %s, 0)
                """,
                (username, hash_password(password), role),
            )
            conn.commit()
            return {"id": int(cur.lastrowid), "username": username, "role": role}, ""
        finally:
            conn.close()

    def create_auth_session(self, user_id, user_agent="", client_ip="", ttl_seconds=7 * 24 * 3600):
        if not self.mysql_available():
            return None
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(ttl_seconds))
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO bian_auth_sessions
                  (token_hash, user_id, expires_at, user_agent, client_ip)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    session_token_hash(token),
                    int(user_id),
                    _mysql_dt(expires_at),
                    str(user_agent or "")[:255],
                    str(client_ip or "")[:64],
                ),
            )
            conn.commit()
            return {"token": token, "expires_at": expires_at}
        finally:
            conn.close()

    def user_for_session(self, token):
        if not token or not self.mysql_available():
            return None
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT u.id, u.username, u.role, s.expires_at
                FROM bian_auth_sessions s
                JOIN bian_auth_users u ON u.id=s.user_id
                WHERE s.token_hash=%s
                  AND s.expires_at > UTC_TIMESTAMP()
                  AND u.disabled=0
                LIMIT 1
                """,
                (session_token_hash(token),),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "UPDATE bian_auth_sessions SET last_seen_at=UTC_TIMESTAMP() WHERE token_hash=%s",
                (session_token_hash(token),),
            )
            conn.commit()
            return {"id": int(row[0]), "username": row[1], "role": row[2] or "user"}
        finally:
            conn.close()

    def delete_auth_session(self, token):
        if not token or not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute("DELETE FROM bian_auth_sessions WHERE token_hash=%s", (session_token_hash(token),))
            conn.commit()
            return True
        finally:
            conn.close()

    def change_auth_password(self, user_id, current_password, new_password):
        if not user_id or not self.mysql_available():
            return False, "auth database is not ready"
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT password_hash, disabled
                FROM bian_auth_users
                WHERE id=%s
                LIMIT 1
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
            if not row or int(row[1] or 0):
                return False, "user is disabled or missing"
            if not verify_password(current_password, row[0]):
                return False, "current password is incorrect"
            cur.execute(
                """
                UPDATE bian_auth_users
                SET password_hash=%s, updated_at=UTC_TIMESTAMP()
                WHERE id=%s
                """,
                (hash_password(new_password), int(user_id)),
            )
            conn.commit()
            return True, ""
        finally:
            conn.close()

    def delete_other_auth_sessions(self, user_id, keep_token=""):
        if not user_id or not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            cur = conn.cursor()
            keep_hash = session_token_hash(keep_token) if keep_token else ""
            if keep_hash:
                cur.execute(
                    "DELETE FROM bian_auth_sessions WHERE user_id=%s AND token_hash<>%s",
                    (int(user_id), keep_hash),
                )
            else:
                cur.execute("DELETE FROM bian_auth_sessions WHERE user_id=%s", (int(user_id),))
            conn.commit()
            return True
        finally:
            conn.close()

    def get_market_payload(self, key):
        client = self._redis_safe_client()
        if client is None:
            return None
        raw = client.get("bian:market:" + key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set_market_payload(self, key, payload, ttl):
        client = self._redis_safe_client()
        if client is None:
            return False
        try:
            client.setex("bian:market:" + key, int(ttl), json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return True
        except Exception as exc:
            self._block_redis(exc)
            return False

    def set_realtime_price(self, symbol, item, ttl=90):
        client = self._redis_safe_client()
        if client is None or not symbol or not item:
            return False
        try:
            client.setex(
                "bian:realtime:" + symbol,
                int(ttl),
                json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            )
            return True
        except Exception as exc:
            self._block_redis(exc)
            return False

    def _redis_safe_client(self):
        if not self.redis_configured or time.time() < self._redis_block_until:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            client = self._redis_connect()
            client.ping()
            self._redis_client = client
            return client
        except Exception as exc:
            self._block_redis(exc)
            return None

    def _block_redis(self, exc):
        self._redis_client = None
        self._redis_block_until = time.time() + 30
        LOG.warning("redis operation failed; disabled for 30s: %s", exc)

    def _mysql_connect(self):
        config = self._mysql_config()
        try:
            import pymysql

            self._mysql_driver = "pymysql"
            self._mysql_checked = True
            return pymysql.connect(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                charset="utf8mb4",
                autocommit=False,
            )
        except ImportError:
            pass

        try:
            import mysql.connector

            self._mysql_driver = "mysql.connector"
            self._mysql_checked = True
            return mysql.connector.connect(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                charset="utf8mb4",
            )
        except ImportError as exc:
            self._mysql_checked = True
            raise RuntimeError("install pymysql or mysql-connector-python to enable MySQL storage") from exc

    def _redis_connect(self):
        try:
            import redis
        except ImportError as exc:
            self._redis_checked = True
            raise RuntimeError("install redis to enable Redis cache") from exc

        self._redis_checked = True
        url = os.environ.get("BIAN_REDIS_URL", "").strip()
        if url:
            return redis.Redis.from_url(url, decode_responses=True)
        return redis.Redis(
            host=os.environ.get("BIAN_REDIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("BIAN_REDIS_PORT", "6379")),
            db=int(os.environ.get("BIAN_REDIS_DB", "0")),
            password=os.environ.get("BIAN_REDIS_PASSWORD") or None,
            decode_responses=True,
        )

    def _mysql_config(self):
        url = os.environ.get("BIAN_MYSQL_URL", "").strip()
        if url:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            return {
                "host": parsed.hostname or "127.0.0.1",
                "port": parsed.port or 3306,
                "user": parsed.username or "",
                "password": parsed.password or "",
                "database": (parsed.path or "/").lstrip("/"),
                "charset": query.get("charset", ["utf8mb4"])[0],
            }
        return {
            "host": os.environ.get("BIAN_MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("BIAN_MYSQL_PORT", "3306")),
            "user": os.environ.get("BIAN_MYSQL_USER", ""),
            "password": os.environ.get("BIAN_MYSQL_PASSWORD", ""),
            "database": os.environ.get("BIAN_MYSQL_DATABASE", "bian_dashboard"),
            "charset": "utf8mb4",
        }

    def _ensure_mysql_schema(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bian_dashboard_kv (
              id BIGINT NOT NULL AUTO_INCREMENT,
              user_id VARCHAR(64) NOT NULL,
              item_key VARCHAR(128) NOT NULL,
              value_json LONGTEXT NOT NULL,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              UNIQUE KEY uniq_bian_dashboard_kv_user_key (user_id, item_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bian_strategy_snapshots (
              id BIGINT NOT NULL AUTO_INCREMENT,
              user_id VARCHAR(64) NOT NULL,
              symbols_key VARCHAR(512) NOT NULL,
              symbols_json LONGTEXT NOT NULL,
              generated_at VARCHAR(32) NULL,
              payload_json LONGTEXT NOT NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_bian_strategy_snapshots_user_created (user_id, created_at),
              KEY idx_bian_strategy_snapshots_symbols (symbols_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()

    def _ensure_auth_schema(self, conn):
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bian_auth_users (
              id BIGINT NOT NULL AUTO_INCREMENT,
              username VARCHAR(64) NOT NULL,
              password_hash VARCHAR(255) NOT NULL,
              role VARCHAR(32) NOT NULL DEFAULT 'user',
              disabled TINYINT NOT NULL DEFAULT 0,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              last_login_at TIMESTAMP NULL DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uniq_bian_auth_users_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bian_auth_sessions (
              id BIGINT NOT NULL AUTO_INCREMENT,
              token_hash CHAR(64) NOT NULL,
              user_id BIGINT NOT NULL,
              expires_at DATETIME NOT NULL,
              user_agent VARCHAR(255) NULL,
              client_ip VARCHAR(64) NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              last_seen_at TIMESTAMP NULL DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uniq_bian_auth_sessions_token (token_hash),
              KEY idx_bian_auth_sessions_user (user_id),
              KEY idx_bian_auth_sessions_expires (expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()


storage = DashboardStorage()
