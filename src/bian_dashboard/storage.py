#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional MySQL and Redis storage for the dashboard.

The dashboard remains fully usable without these dependencies. When MySQL or
Redis environment variables are present and the matching Python driver is
installed, this module persists browser preferences and caches hot market data.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse


LOG = logging.getLogger("bian-dashboard.storage")
PASSWORD_HASH_ITERATIONS = 260000
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIGNAL_REVIEW_FILE = os.environ.get(
    "BIAN_SIGNAL_REVIEW_FILE",
    os.path.join(ROOT, "runtime", "signal_reviews.json"),
)
SIGNAL_REVIEW_LIMIT = int(os.environ.get("BIAN_SIGNAL_REVIEW_LIMIT", "5000"))
STRATEGY_SNAPSHOT_LIMIT = max(10, int(os.environ.get("BIAN_STRATEGY_SNAPSHOT_LIMIT", "1000")))
MYSQL_CONNECT_TIMEOUT_SECONDS = max(1, int(os.environ.get("BIAN_MYSQL_CONNECT_TIMEOUT_SECONDS", "3")))
MYSQL_READ_TIMEOUT_SECONDS = max(1, int(os.environ.get("BIAN_MYSQL_READ_TIMEOUT_SECONDS", "5")))
MYSQL_WRITE_TIMEOUT_SECONDS = max(1, int(os.environ.get("BIAN_MYSQL_WRITE_TIMEOUT_SECONDS", "5")))
REDIS_CONNECT_TIMEOUT_SECONDS = max(1, int(os.environ.get("BIAN_REDIS_CONNECT_TIMEOUT_SECONDS", "2")))
REDIS_READ_TIMEOUT_SECONDS = max(1, int(os.environ.get("BIAN_REDIS_READ_TIMEOUT_SECONDS", "2")))
STORAGE_HEALTH_TTL_SECONDS = 5
PREFERENCE_REVISION_KEY = "__preference_revision__"


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


def _is_duplicate_key_error(exc):
    text = str(exc)
    code = getattr(exc, "args", [None])[0] if getattr(exc, "args", None) else None
    return code == 1062 or "Duplicate entry" in text or "UNIQUE constraint" in text


class DashboardStorage:
    def __init__(self):
        self.user_id = os.environ.get("BIAN_STORAGE_USER_ID", "default").strip() or "default"
        self.mysql_configured = bool(os.environ.get("BIAN_MYSQL_URL") or os.environ.get("BIAN_MYSQL_HOST"))
        self.redis_configured = bool(os.environ.get("BIAN_REDIS_URL") or os.environ.get("BIAN_REDIS_HOST"))
        self._mysql_driver = None
        self._redis_client = None
        self._mysql_checked = False
        self._redis_checked = False
        self._mysql_available_until = 0.0
        self._mysql_block_until = 0.0
        self._redis_available_until = 0.0
        self._redis_block_until = 0.0
        self._availability_lock = threading.RLock()
        self._signal_review_lock = threading.RLock()
        self._schema_lock = threading.RLock()
        self._mysql_schema_ready = False
        self._signal_review_schema_ready = False
        self._auth_schema_ready = False
        self._last_session_cleanup = 0.0

    def for_user(self, user_id):
        """Return a request-scoped view without mutating shared storage state."""
        scoped = copy.copy(self)
        scoped._scope_parent = self
        value = str(user_id or "").strip()
        scoped.user_id = value or self.user_id
        return scoped

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
        first_admin_secret_configured = bool(
            (os.environ.get("BIAN_AUTH_BOOTSTRAP_USER") or "admin").strip()
            and os.environ.get("BIAN_AUTH_BOOTSTRAP_PASSWORD", "").strip()
        )
        status = {
            "enabled": configured,
            "mysql_available": False,
            "user_count_known": False,
            "has_users": None,
            "first_admin_secret_configured": first_admin_secret_configured,
            "can_create_first_admin": False,
            "login_ready": not configured,
            "issue": "" if configured else "disabled",
        }
        if not configured:
            return status
        if not self.mysql_available():
            status["issue"] = "mysql_unavailable"
            return status
        status["mysql_available"] = True
        conn = self._mysql_connect()
        try:
            count, count_error = self._auth_user_count(conn)
        finally:
            conn.close()
        if count is None:
            status["issue"] = count_error or "user_count_unknown"
            return status
        status["user_count_known"] = True
        status["has_users"] = count > 0
        status["can_create_first_admin"] = count == 0 and first_admin_secret_configured
        status["login_ready"] = bool(count > 0 or first_admin_secret_configured)
        if not status["login_ready"]:
            status["issue"] = "first_admin_secret_missing"
        return status

    def mysql_available(self):
        if not self.mysql_configured:
            return False
        shared = getattr(self, "_scope_parent", self)
        now = time.time()
        with shared._availability_lock:
            if now < shared._mysql_block_until:
                return False
            if now < shared._mysql_available_until:
                return True
        try:
            conn = self._mysql_connect()
            conn.close()
            with shared._availability_lock:
                shared._mysql_available_until = time.time() + STORAGE_HEALTH_TTL_SECONDS
                shared._mysql_block_until = 0.0
            return True
        except Exception as exc:
            with shared._availability_lock:
                shared._mysql_available_until = 0.0
                shared._mysql_block_until = time.time() + STORAGE_HEALTH_TTL_SECONDS
            LOG.warning("mysql storage unavailable: %s", exc)
            return False

    def redis_available(self):
        if not self.redis_configured:
            return False
        shared = getattr(self, "_scope_parent", self)
        now = time.time()
        with shared._availability_lock:
            if now < shared._redis_block_until:
                return False
            if now < shared._redis_available_until:
                return True
        try:
            client = shared._redis_client or self._redis_connect()
            client.ping()
            shared._redis_client = client
            with shared._availability_lock:
                shared._redis_available_until = time.time() + STORAGE_HEALTH_TTL_SECONDS
            return True
        except Exception as exc:
            self._block_redis(exc)
            return False

    def load_preferences(self):
        return self.load_preferences_with_revision()[0]

    def load_preferences_with_revision(self):
        if not self.mysql_available():
            return {}, 0
        conn = self._mysql_connect()
        try:
            self._ensure_mysql_schema(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT item_key, value_json FROM bian_dashboard_kv WHERE user_id=%s",
                (self.user_id,),
            )
            prefs = {}
            revision = 0
            for key, raw in cur.fetchall():
                if key == PREFERENCE_REVISION_KEY:
                    try:
                        revision = max(0, int(json.loads(raw)))
                    except Exception:
                        revision = 0
                    continue
                try:
                    prefs[key] = json.loads(raw)
                except Exception:
                    prefs[key] = None
            return prefs, revision
        finally:
            conn.close()

    def save_preferences(self, prefs, revision=None):
        if not isinstance(prefs, dict) or not prefs:
            return False
        try:
            raw_revision = revision
            if isinstance(raw_revision, bool):
                raise ValueError
            normalized_revision = int(revision)
            if normalized_revision <= 0 or (isinstance(raw_revision, float) and raw_revision != normalized_revision):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("preference revision must be a positive integer") from exc
        if not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_mysql_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT IGNORE INTO bian_dashboard_kv (user_id, item_key, value_json)
                VALUES (%s, %s, '0')
                """,
                (self.user_id, PREFERENCE_REVISION_KEY),
            )
            cur.execute(
                """
                SELECT value_json
                FROM bian_dashboard_kv
                WHERE user_id=%s AND item_key=%s
                FOR UPDATE
                """,
                (self.user_id, PREFERENCE_REVISION_KEY),
            )
            row = cur.fetchone()
            try:
                current_revision = max(0, int(json.loads(row[0] if row else "0")))
            except Exception:
                current_revision = 0
            if normalized_revision <= current_revision:
                conn.commit()
                return {"saved": True, "applied": False, "revision": current_revision}
            rows = [
                (self.user_id, str(key), json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                for key, value in prefs.items()
                if str(key) != PREFERENCE_REVISION_KEY
            ]
            if not rows:
                conn.rollback()
                return False
            cur.executemany(
                """
                INSERT INTO bian_dashboard_kv (user_id, item_key, value_json)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE value_json=VALUES(value_json), updated_at=CURRENT_TIMESTAMP
                """,
                rows,
            )
            cur.execute(
                """
                UPDATE bian_dashboard_kv
                SET value_json=%s, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=%s AND item_key=%s
                """,
                (json.dumps(normalized_revision), self.user_id, PREFERENCE_REVISION_KEY),
            )
            conn.commit()
            return {"saved": True, "applied": True, "revision": normalized_revision}
        finally:
            conn.close()

    def save_preference_batch(self, patches):
        if not isinstance(patches, list) or not patches:
            raise ValueError("preference patches must be a non-empty list")
        normalized = []
        previous_revision = 0
        for entry in patches:
            prefs = entry.get("preferences") if isinstance(entry, dict) else None
            raw_revision = entry.get("revision") if isinstance(entry, dict) else None
            if not isinstance(prefs, dict) or not prefs:
                raise ValueError("each preference patch must contain preferences")
            try:
                if isinstance(raw_revision, bool):
                    raise ValueError
                revision = int(raw_revision)
                if revision <= 0 or (isinstance(raw_revision, float) and raw_revision != revision):
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError("preference revision must be a positive integer") from exc
            if revision <= previous_revision:
                raise ValueError("preference batch revisions must be strictly increasing")
            rows = [
                (self.user_id, str(key), json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                for key, value in prefs.items()
                if str(key) != PREFERENCE_REVISION_KEY
            ]
            if not rows:
                raise ValueError("each preference patch must contain writable preferences")
            normalized.append((revision, rows))
            previous_revision = revision
        if not self.mysql_available():
            return False
        conn = self._mysql_connect()
        try:
            self._ensure_mysql_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT IGNORE INTO bian_dashboard_kv (user_id, item_key, value_json)
                VALUES (%s, %s, '0')
                """,
                (self.user_id, PREFERENCE_REVISION_KEY),
            )
            cur.execute(
                """
                SELECT value_json
                FROM bian_dashboard_kv
                WHERE user_id=%s AND item_key=%s
                FOR UPDATE
                """,
                (self.user_id, PREFERENCE_REVISION_KEY),
            )
            row = cur.fetchone()
            try:
                current_revision = max(0, int(json.loads(row[0] if row else "0")))
            except Exception:
                current_revision = 0
            applied_count = 0
            for revision, rows in normalized:
                if revision <= current_revision:
                    continue
                cur.executemany(
                    """
                    INSERT INTO bian_dashboard_kv (user_id, item_key, value_json)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE value_json=VALUES(value_json), updated_at=CURRENT_TIMESTAMP
                    """,
                    rows,
                )
                current_revision = revision
                applied_count += 1
            if applied_count:
                cur.execute(
                    """
                    UPDATE bian_dashboard_kv
                    SET value_json=%s, updated_at=CURRENT_TIMESTAMP
                    WHERE user_id=%s AND item_key=%s
                    """,
                    (json.dumps(current_revision), self.user_id, PREFERENCE_REVISION_KEY),
                )
            conn.commit()
            return {
                "saved": True,
                "applied": applied_count > 0,
                "revision": current_revision,
                "applied_count": applied_count,
            }
        except Exception:
            conn.rollback()
            raise
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
            self._trim_strategy_snapshots_mysql(cur)
            conn.commit()
            return True
        finally:
            conn.close()

    def _trim_strategy_snapshots_mysql(self, cur):
        cur.execute(
            """
            SELECT id
            FROM bian_strategy_snapshots
            WHERE user_id=%s
            ORDER BY created_at DESC, id DESC
            LIMIT %s, 1
            """,
            (self.user_id, STRATEGY_SNAPSHOT_LIMIT - 1),
        )
        cutoff = cur.fetchone()
        if not cutoff:
            return 0
        cur.execute(
            "DELETE FROM bian_strategy_snapshots WHERE user_id=%s AND id<%s",
            (self.user_id, int(cutoff[0])),
        )
        return max(0, int(cur.rowcount or 0))

    def save_signal_reviews(self, records):
        if not isinstance(records, list) or not records:
            return {"backend": "none", "inserted": 0, "skipped": 0}
        if self.mysql_available():
            try:
                self._reconcile_signal_review_file_to_mysql()
                return self._save_signal_reviews_mysql(records)
            except Exception as exc:
                LOG.warning("mysql signal review save failed; fallback=file; error=%s", exc)
        return self._save_signal_reviews_file(records)

    def load_signal_reviews(self, symbols=None, limit=200):
        symbols = {str(item).upper() for item in (symbols or []) if item}
        limit = max(1, min(int(limit or 200), 1000))
        if self.mysql_available():
            try:
                self._reconcile_signal_review_file_to_mysql()
                return self._load_signal_reviews_mysql(symbols, limit)
            except Exception as exc:
                LOG.warning("mysql signal review load failed; fallback=file; error=%s", exc)
        return self._load_signal_reviews_file(symbols, limit)

    def load_due_signal_reviews(self, now_ms, max_rows=50, all_users=False):
        max_rows = max(1, min(int(max_rows or 50), 200))
        cutoff_ms = int(now_ms or 0) - 5 * 60 * 1000
        if cutoff_ms <= 0:
            return []
        if self.mysql_available():
            try:
                self._reconcile_signal_review_file_to_mysql(all_users=all_users)
                return self._load_due_signal_reviews_mysql(cutoff_ms, max_rows, all_users=all_users)
            except Exception as exc:
                LOG.warning("mysql due signal review load failed; fallback=file; error=%s", exc)
        records = self._load_signal_reviews_file(set(), SIGNAL_REVIEW_LIMIT, all_users=all_users)
        due = []
        for item in records:
            if item.get("status") == "evaluated":
                continue
            if str(item.get("side") or "") not in ("long", "short"):
                continue
            if int(item.get("snapshot_at_ms") or 0) <= cutoff_ms:
                due.append(item)
        due.sort(
            key=lambda item: (
                int(item.get("updated_at_ms") or item.get("created_at_ms") or item.get("snapshot_at_ms") or 0),
                int(item.get("snapshot_at_ms") or 0),
            )
        )
        return due[:max_rows]

    def update_signal_review_evaluation(self, signal_key, status, failure_reason, evaluation, user_id=None):
        signal_key = str(signal_key or "")
        if not signal_key:
            return False
        status = str(status or "partial")
        failure_reason = str(failure_reason or "")[:64]
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        if self.mysql_available():
            try:
                self._reconcile_signal_review_file_to_mysql(all_users=bool(user_id and str(user_id) != self.user_id))
                return self._update_signal_review_mysql(signal_key, status, failure_reason, evaluation, user_id=user_id)
            except Exception as exc:
                LOG.warning("mysql signal review update failed; fallback=file; error=%s", exc)
        return self._update_signal_review_file(signal_key, status, failure_reason, evaluation, user_id=user_id)

    def defer_signal_review(self, signal_key, user_id=None):
        signal_key = str(signal_key or "")
        if not signal_key:
            return False
        if self.mysql_available():
            try:
                self._reconcile_signal_review_file_to_mysql(all_users=bool(user_id and str(user_id) != self.user_id))
                return self._defer_signal_review_mysql(signal_key, user_id=user_id)
            except Exception as exc:
                LOG.warning("mysql signal review defer failed; fallback=file; error=%s", exc)
        return self._defer_signal_review_file(signal_key, user_id=user_id)

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

    def _auth_user_count(self, conn):
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM bian_auth_users")
            return int(cur.fetchone()[0]), ""
        except Exception as exc:
            text = str(exc).lower()
            args = getattr(exc, "args", ()) or ()
            code = args[0] if args else None
            sqlstate = str(args[1] if len(args) > 1 else "")
            if code in (1146, "1146") or sqlstate == "42S02" or "doesn't exist" in text or "no such table" in text:
                return 0, "auth_table_missing"
            LOG.warning("auth user count check failed: %s", exc)
            return None, "user_count_unavailable"

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
            try:
                conn.commit()
                return {"id": int(cur.lastrowid), "username": username, "role": role}, ""
            except Exception as exc:
                conn.rollback()
                if _is_duplicate_key_error(exc):
                    return None, "username already exists"
                raise
        except Exception as exc:
            if _is_duplicate_key_error(exc):
                try:
                    conn.rollback()
                except Exception:
                    pass
                return None, "username already exists"
            raise
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

    def user_for_session(self, token, touch_interval_seconds=60):
        if not token or not self.mysql_available():
            return None
        conn = self._mysql_connect()
        try:
            self._ensure_auth_schema(conn)
            self._cleanup_expired_sessions_if_due(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT u.id, u.username, u.role, s.expires_at, s.last_seen_at
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
            should_touch = True
            last_seen = row[4]
            min_interval = max(0, int(touch_interval_seconds or 0))
            if min_interval and last_seen:
                try:
                    if last_seen.tzinfo is None:
                        last_seen = last_seen.replace(tzinfo=timezone.utc)
                    should_touch = (datetime.now(timezone.utc) - last_seen).total_seconds() >= min_interval
                except Exception:
                    should_touch = True
            if should_touch:
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

    def change_auth_password(self, user_id, current_password, new_password, keep_token=""):
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
                FOR UPDATE
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
            keep_hash = session_token_hash(keep_token) if keep_token else ""
            if keep_hash:
                cur.execute(
                    "DELETE FROM bian_auth_sessions WHERE user_id=%s AND token_hash<>%s",
                    (int(user_id), keep_hash),
                )
            else:
                cur.execute("DELETE FROM bian_auth_sessions WHERE user_id=%s", (int(user_id),))
            conn.commit()
            return True, ""
        except Exception:
            conn.rollback()
            raise
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
        try:
            raw = client.get("bian:market:" + key)
        except Exception as exc:
            self._block_redis(exc)
            return None
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

    def _save_signal_reviews_mysql(self, records):
        conn = self._mysql_connect()
        try:
            self._ensure_signal_review_schema(conn)
            cur = conn.cursor()
            rows = []
            for item in records:
                if not isinstance(item, dict) or not item.get("signal_key"):
                    continue
                rows.append(self._signal_review_mysql_values(item, self.user_id))
            if not rows:
                return {"backend": "mysql", "inserted": 0, "skipped": 0}
            cur.executemany(
                """
                INSERT IGNORE INTO bian_signal_reviews
                  (user_id, signal_key, symbol, advice_name, side, entry_price, stop_price,
                   snapshot_price, snapshot_at_ms, snapshot_at, confidence, direction_score,
                   execution_score, risk_gate, candle_state, trigger_status, status,
                   failure_reason, payload_json, evaluation_json)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )
            conn.commit()
            inserted = max(0, int(cur.rowcount or 0))
            return {"backend": "mysql", "inserted": inserted, "skipped": max(0, len(rows) - inserted)}
        finally:
            conn.close()

    def _signal_review_mysql_values(self, item, user_id):
        return (
            str(user_id or self.user_id)[:64],
            str(item.get("signal_key"))[:191],
            str(item.get("symbol") or "")[:32],
            str(item.get("advice_name") or "")[:96],
            str(item.get("side") or "")[:12],
            float(item.get("entry_price") or 0.0),
            float(item.get("stop_price") or 0.0),
            float(item.get("snapshot_price") or 0.0),
            int(item.get("snapshot_at_ms") or 0),
            str(item.get("snapshot_at") or "")[:64],
            int(item.get("confidence") or 0),
            int(item.get("direction_score") or 0),
            int(item.get("execution_score") or 0),
            str(item.get("risk_gate") or "")[:64],
            str(item.get("candle_state") or "")[:64],
            str(item.get("trigger_status") or "")[:32],
            str(item.get("status") or "pending")[:32],
            str(item.get("failure_reason") or "")[:64],
            json.dumps(item.get("payload") or {}, ensure_ascii=False, separators=(",", ":")),
            json.dumps(item.get("evaluation") or {}, ensure_ascii=False, separators=(",", ":")),
        )

    def _merge_signal_review_records(self, database_record, file_record):
        status_rank = {"pending": 1, "partial": 2, "evaluated": 3}

        def rank(item):
            evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), dict) else {}
            done = sum(1 for value in evaluation.values() if isinstance(value, dict) and value.get("status") == "done")
            return (
                status_rank.get(str(item.get("status") or "pending"), 0),
                done,
                int(item.get("updated_at_ms") or item.get("created_at_ms") or 0),
            )

        preferred, other = (file_record, database_record) if rank(file_record) > rank(database_record) else (database_record, file_record)
        merged = dict(other)
        merged.update(preferred)
        merged_evaluation = {}
        for source in (other, preferred):
            evaluation = source.get("evaluation") if isinstance(source.get("evaluation"), dict) else {}
            for horizon, value in evaluation.items():
                current = merged_evaluation.get(horizon)
                if current is None or (isinstance(value, dict) and value.get("status") == "done"):
                    merged_evaluation[horizon] = value
        merged["evaluation"] = merged_evaluation
        merged["status"] = max(
            (str(database_record.get("status") or "pending"), str(file_record.get("status") or "pending")),
            key=lambda value: status_rank.get(value, 0),
        )
        created_values = [int(item.get("created_at_ms") or 0) for item in (database_record, file_record)]
        created_values = [value for value in created_values if value > 0]
        if created_values:
            merged["created_at_ms"] = min(created_values)
        merged["updated_at_ms"] = max(
            int(database_record.get("updated_at_ms") or database_record.get("created_at_ms") or 0),
            int(file_record.get("updated_at_ms") or file_record.get("created_at_ms") or 0),
        )
        return merged

    def _upsert_signal_review_records_mysql(self, records):
        conn = self._mysql_connect()
        try:
            self._ensure_signal_review_schema(conn)
            cur = conn.cursor()
            candidates = {}
            for item in records:
                if not isinstance(item, dict) or not item.get("signal_key"):
                    continue
                user_id = str(item.get("storage_user_id") or self.user_id)
                candidates[(user_id, str(item.get("signal_key")))] = dict(item, storage_user_id=user_id)
            existing = {}
            by_user = {}
            for user_id, signal_key in candidates:
                by_user.setdefault(user_id, []).append(signal_key)
            for user_id, signal_keys in by_user.items():
                for offset in range(0, len(signal_keys), 200):
                    chunk = signal_keys[offset:offset + 200]
                    placeholders = ",".join(["%s"] * len(chunk))
                    cur.execute(
                        f"""
                        SELECT user_id, signal_key, symbol, advice_name, side, entry_price, stop_price,
                               snapshot_price, snapshot_at_ms, snapshot_at, confidence,
                               direction_score, execution_score, risk_gate, candle_state,
                               trigger_status, status, failure_reason, payload_json,
                               evaluation_json, UNIX_TIMESTAMP(created_at) * 1000,
                               UNIX_TIMESTAMP(updated_at) * 1000
                        FROM bian_signal_reviews
                        WHERE user_id=%s AND signal_key IN ({placeholders})
                        """,
                        tuple([user_id] + chunk),
                    )
                    for row in cur.fetchall():
                        item = self._signal_review_from_row(row[1:21])
                        item["storage_user_id"] = str(row[0])
                        item["updated_at_ms"] = int(row[21] or 0)
                        existing[(str(row[0]), str(row[1]))] = item
            rows = []
            for key, item in candidates.items():
                if key in existing:
                    item = self._merge_signal_review_records(existing[key], item)
                rows.append(self._signal_review_mysql_values(item, key[0]))
            if not rows:
                return 0
            cur.executemany(
                """
                INSERT INTO bian_signal_reviews
                  (user_id, signal_key, symbol, advice_name, side, entry_price, stop_price,
                   snapshot_price, snapshot_at_ms, snapshot_at, confidence, direction_score,
                   execution_score, risk_gate, candle_state, trigger_status, status,
                   failure_reason, payload_json, evaluation_json)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  symbol=VALUES(symbol), advice_name=VALUES(advice_name), side=VALUES(side),
                  entry_price=VALUES(entry_price), stop_price=VALUES(stop_price),
                  snapshot_price=VALUES(snapshot_price), snapshot_at_ms=VALUES(snapshot_at_ms),
                  snapshot_at=VALUES(snapshot_at), confidence=VALUES(confidence),
                  direction_score=VALUES(direction_score), execution_score=VALUES(execution_score),
                  risk_gate=VALUES(risk_gate), candle_state=VALUES(candle_state),
                  trigger_status=VALUES(trigger_status), status=VALUES(status),
                  failure_reason=VALUES(failure_reason), payload_json=VALUES(payload_json),
                  evaluation_json=VALUES(evaluation_json), updated_at=CURRENT_TIMESTAMP,
                  evaluated_at=CASE WHEN VALUES(status)='evaluated'
                                    THEN COALESCE(evaluated_at, UTC_TIMESTAMP())
                                    ELSE evaluated_at END
                """,
                rows,
            )
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def _reconcile_signal_review_file_to_mysql(self, all_users=False):
        with self._signal_review_lock:
            data = self._read_signal_review_file()
            records = data.get("records", [])
            candidates = [
                item for item in records
                if isinstance(item, dict)
                and item.get("signal_key")
                and (all_users or str(item.get("storage_user_id") or "default") == self.user_id)
            ]
            if not candidates:
                return 0
            migrated = self._upsert_signal_review_records_mysql(candidates)
            migrated_keys = {
                (str(item.get("storage_user_id") or "default"), str(item.get("signal_key")))
                for item in candidates
            }
            data["records"] = [
                item for item in records
                if not isinstance(item, dict)
                or (str(item.get("storage_user_id") or "default"), str(item.get("signal_key"))) not in migrated_keys
            ]
            self._write_signal_review_file(data)
            return migrated

    def _load_signal_reviews_mysql(self, symbols, limit):
        conn = self._mysql_connect()
        try:
            self._ensure_signal_review_schema(conn)
            cur = conn.cursor()
            params = [self.user_id]
            where = "user_id=%s"
            if symbols:
                placeholders = ",".join(["%s"] * len(symbols))
                where += f" AND symbol IN ({placeholders})"
                params.extend(sorted(symbols))
            params.append(int(limit))
            cur.execute(
                f"""
                SELECT signal_key, symbol, advice_name, side, entry_price, stop_price,
                       snapshot_price, snapshot_at_ms, snapshot_at, confidence,
                       direction_score, execution_score, risk_gate, candle_state,
                       trigger_status, status, failure_reason, payload_json,
                       evaluation_json, UNIX_TIMESTAMP(created_at) * 1000
                FROM bian_signal_reviews
                WHERE {where}
                ORDER BY snapshot_at_ms DESC, id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return [self._signal_review_from_row(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def _load_due_signal_reviews_mysql(self, cutoff_ms, max_rows, all_users=False):
        conn = self._mysql_connect()
        try:
            self._ensure_signal_review_schema(conn)
            cur = conn.cursor()
            where_user = "" if all_users else "AND user_id=%s"
            params = [int(cutoff_ms), int(max_rows)]
            if not all_users:
                params.insert(1, self.user_id)
            cur.execute(
                f"""
                SELECT user_id, signal_key, symbol, advice_name, side, entry_price, stop_price,
                       snapshot_price, snapshot_at_ms, snapshot_at, confidence,
                       direction_score, execution_score, risk_gate, candle_state,
                       trigger_status, status, failure_reason, payload_json,
                       evaluation_json, UNIX_TIMESTAMP(created_at) * 1000
                FROM bian_signal_reviews
                WHERE status IN ('pending', 'partial')
                  AND side IN ('long', 'short')
                  AND snapshot_at_ms <= %s
                  {where_user}
                ORDER BY updated_at ASC, snapshot_at_ms ASC, id ASC
                LIMIT %s
                """,
                tuple(params),
            )
            records = []
            for row in cur.fetchall():
                record = self._signal_review_from_row(row[1:])
                record["storage_user_id"] = row[0]
                records.append(record)
            return records
        finally:
            conn.close()

    def _update_signal_review_mysql(self, signal_key, status, failure_reason, evaluation, user_id=None):
        conn = self._mysql_connect()
        try:
            self._ensure_signal_review_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE bian_signal_reviews
                SET status=%s,
                    failure_reason=%s,
                    evaluation_json=%s,
                    updated_at=CURRENT_TIMESTAMP,
                    evaluated_at=CASE WHEN %s='evaluated' THEN UTC_TIMESTAMP() ELSE evaluated_at END
                WHERE user_id=%s AND signal_key=%s
                """,
                (
                    status[:32],
                    failure_reason[:64],
                    json.dumps(evaluation, ensure_ascii=False, separators=(",", ":")),
                    status,
                    str(user_id or self.user_id),
                    signal_key[:191],
                ),
            )
            conn.commit()
            return bool(cur.rowcount)
        finally:
            conn.close()

    def _defer_signal_review_mysql(self, signal_key, user_id=None):
        conn = self._mysql_connect()
        try:
            self._ensure_signal_review_schema(conn)
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE bian_signal_reviews
                SET updated_at=CURRENT_TIMESTAMP
                WHERE user_id=%s AND signal_key=%s AND status IN ('pending', 'partial')
                """,
                (str(user_id or self.user_id), signal_key[:191]),
            )
            conn.commit()
            return bool(cur.rowcount)
        finally:
            conn.close()

    def _signal_review_from_row(self, row):
        payload = {}
        evaluation = {}
        try:
            payload = json.loads(row[17] or "{}")
        except Exception:
            payload = {}
        try:
            evaluation = json.loads(row[18] or "{}")
        except Exception:
            evaluation = {}
        return {
            "signal_key": row[0],
            "symbol": row[1],
            "advice_name": row[2],
            "side": row[3],
            "entry_price": float(row[4] or 0.0),
            "stop_price": float(row[5] or 0.0),
            "snapshot_price": float(row[6] or 0.0),
            "snapshot_at_ms": int(row[7] or 0),
            "snapshot_at": row[8] or "",
            "confidence": int(row[9] or 0),
            "direction_score": int(row[10] or 0),
            "execution_score": int(row[11] or 0),
            "risk_gate": row[12] or "",
            "candle_state": row[13] or "",
            "trigger_status": row[14] or "",
            "status": row[15] or "pending",
            "failure_reason": row[16] or "",
            "payload": payload,
            "evaluation": evaluation,
            "created_at_ms": int(row[19] or 0),
        }

    def _read_signal_review_file(self):
        with self._signal_review_lock:
            if not os.path.exists(SIGNAL_REVIEW_FILE):
                return {"version": 1, "records": []}
            try:
                with open(SIGNAL_REVIEW_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("records"), list):
                    return data
            except Exception as exc:
                LOG.warning("signal review file read failed: %s", exc)
            return {"version": 1, "records": []}

    def _write_signal_review_file(self, data):
        with self._signal_review_lock:
            os.makedirs(os.path.dirname(SIGNAL_REVIEW_FILE), exist_ok=True)
            tmp = SIGNAL_REVIEW_FILE + f".{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, SIGNAL_REVIEW_FILE)

    def _save_signal_reviews_file(self, records):
        with self._signal_review_lock:
            data = self._read_signal_review_file()
            items = data.setdefault("records", [])
            existing = {
                (str(item.get("storage_user_id") or "default"), str(item.get("signal_key")))
                for item in items
                if isinstance(item, dict)
            }
            inserted = 0
            skipped = 0
            now_ms = int(time.time() * 1000)
            for item in records:
                if not isinstance(item, dict) or not item.get("signal_key"):
                    continue
                key = str(item.get("signal_key"))
                scoped_key = (self.user_id, key)
                if scoped_key in existing:
                    skipped += 1
                    continue
                clone = dict(item)
                clone["storage_user_id"] = self.user_id
                clone["created_at_ms"] = now_ms
                clone["updated_at_ms"] = now_ms
                items.append(clone)
                existing.add(scoped_key)
                inserted += 1
            items.sort(key=lambda x: int(x.get("snapshot_at_ms") or 0), reverse=True)
            retained = []
            per_user_counts = {}
            for item in items:
                user_id = str(item.get("storage_user_id") or "default")
                count = per_user_counts.get(user_id, 0)
                if count >= SIGNAL_REVIEW_LIMIT:
                    continue
                retained.append(item)
                per_user_counts[user_id] = count + 1
            items[:] = retained
            self._write_signal_review_file(data)
            return {"backend": "file", "inserted": inserted, "skipped": skipped}

    def _load_signal_reviews_file(self, symbols, limit, all_users=False):
        data = self._read_signal_review_file()
        out = []
        for item in data.get("records", []):
            if not isinstance(item, dict):
                continue
            if not all_users and str(item.get("storage_user_id") or "default") != self.user_id:
                continue
            if symbols and str(item.get("symbol") or "").upper() not in symbols:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def _update_signal_review_file(self, signal_key, status, failure_reason, evaluation, user_id=None):
        with self._signal_review_lock:
            data = self._read_signal_review_file()
            updated = False
            expected_user_id = str(user_id or self.user_id)
            for item in data.get("records", []):
                if (
                    not isinstance(item, dict)
                    or str(item.get("signal_key")) != signal_key
                    or str(item.get("storage_user_id") or "default") != expected_user_id
                ):
                    continue
                item["status"] = status
                item["failure_reason"] = failure_reason
                item["evaluation"] = evaluation
                item["updated_at_ms"] = int(time.time() * 1000)
                if status == "evaluated":
                    item["evaluated_at_ms"] = int(time.time() * 1000)
                updated = True
                break
            if updated:
                self._write_signal_review_file(data)
            return updated

    def _defer_signal_review_file(self, signal_key, user_id=None):
        with self._signal_review_lock:
            data = self._read_signal_review_file()
            expected_user_id = str(user_id or self.user_id)
            updated = False
            for item in data.get("records", []):
                if (
                    isinstance(item, dict)
                    and str(item.get("signal_key")) == signal_key
                    and str(item.get("storage_user_id") or "default") == expected_user_id
                    and item.get("status") in ("pending", "partial")
                ):
                    item["updated_at_ms"] = int(time.time() * 1000)
                    updated = True
                    break
            if updated:
                self._write_signal_review_file(data)
            return updated

    def _redis_safe_client(self):
        shared = getattr(self, "_scope_parent", self)
        if not self.redis_configured or time.time() < shared._redis_block_until:
            return None
        if shared._redis_client is not None:
            return shared._redis_client
        try:
            client = self._redis_connect()
            client.ping()
            shared._redis_client = client
            shared._redis_available_until = time.time() + STORAGE_HEALTH_TTL_SECONDS
            return client
        except Exception as exc:
            self._block_redis(exc)
            return None

    def _block_redis(self, exc):
        shared = getattr(self, "_scope_parent", self)
        shared._redis_client = None
        shared._redis_available_until = 0.0
        shared._redis_block_until = time.time() + 30
        LOG.warning("redis operation failed; disabled for 30s: %s", exc)

    def _mysql_connect(self):
        config = self._mysql_config()
        shared = getattr(self, "_scope_parent", self)
        try:
            import pymysql

            self._mysql_driver = "pymysql"
            self._mysql_checked = True
            shared._mysql_driver = "pymysql"
            shared._mysql_checked = True
            return pymysql.connect(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=MYSQL_CONNECT_TIMEOUT_SECONDS,
                read_timeout=MYSQL_READ_TIMEOUT_SECONDS,
                write_timeout=MYSQL_WRITE_TIMEOUT_SECONDS,
            )
        except ImportError:
            pass

        try:
            import mysql.connector

            self._mysql_driver = "mysql.connector"
            self._mysql_checked = True
            shared._mysql_driver = "mysql.connector"
            shared._mysql_checked = True
            return mysql.connector.connect(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                charset="utf8mb4",
                connection_timeout=MYSQL_CONNECT_TIMEOUT_SECONDS,
                read_timeout=MYSQL_READ_TIMEOUT_SECONDS,
                write_timeout=MYSQL_WRITE_TIMEOUT_SECONDS,
            )
        except ImportError as exc:
            self._mysql_checked = True
            shared._mysql_checked = True
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
            return redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=REDIS_READ_TIMEOUT_SECONDS,
                health_check_interval=30,
            )
        return redis.Redis(
            host=os.environ.get("BIAN_REDIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("BIAN_REDIS_PORT", "6379")),
            db=int(os.environ.get("BIAN_REDIS_DB", "0")),
            password=os.environ.get("BIAN_REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=REDIS_READ_TIMEOUT_SECONDS,
            health_check_interval=30,
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
        shared = getattr(self, "_scope_parent", self)
        if shared._mysql_schema_ready:
            return
        with shared._schema_lock:
            if shared._mysql_schema_ready:
                return
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
                  KEY idx_bian_strategy_snapshots_user_created (user_id, created_at, id),
                  KEY idx_bian_strategy_snapshots_symbols (symbols_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            self._ensure_signal_review_schema(conn, commit=False)
            conn.commit()
            shared._mysql_schema_ready = True

    def _ensure_signal_review_schema(self, conn, commit=True):
        shared = getattr(self, "_scope_parent", self)
        if shared._signal_review_schema_ready:
            return
        with shared._schema_lock:
            if shared._signal_review_schema_ready:
                return
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bian_signal_reviews (
                  id BIGINT NOT NULL AUTO_INCREMENT,
                  user_id VARCHAR(64) NOT NULL,
                  signal_key VARCHAR(191) NOT NULL,
                  symbol VARCHAR(32) NOT NULL,
                  advice_name VARCHAR(96) NOT NULL,
                  side VARCHAR(12) NOT NULL,
                  entry_price DOUBLE NOT NULL DEFAULT 0,
                  stop_price DOUBLE NOT NULL DEFAULT 0,
                  snapshot_price DOUBLE NOT NULL DEFAULT 0,
                  snapshot_at_ms BIGINT NOT NULL DEFAULT 0,
                  snapshot_at VARCHAR(64) NULL,
                  confidence INT NOT NULL DEFAULT 0,
                  direction_score INT NOT NULL DEFAULT 0,
                  execution_score INT NOT NULL DEFAULT 0,
                  risk_gate VARCHAR(64) NULL,
                  candle_state VARCHAR(64) NULL,
                  trigger_status VARCHAR(32) NULL,
                  status VARCHAR(32) NOT NULL DEFAULT 'pending',
                  failure_reason VARCHAR(64) NULL,
                  payload_json LONGTEXT NULL,
                  evaluation_json LONGTEXT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  evaluated_at TIMESTAMP NULL DEFAULT NULL,
                  PRIMARY KEY (id),
                  UNIQUE KEY uniq_bian_signal_reviews_user_key (user_id, signal_key),
                  KEY idx_bian_signal_reviews_user_symbol_time (user_id, symbol, snapshot_at_ms),
                  KEY idx_bian_signal_reviews_due (user_id, status, snapshot_at_ms),
                  KEY idx_bian_signal_reviews_due_all (status, updated_at, snapshot_at_ms)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            self._ensure_mysql_index(
                cur,
                "bian_signal_reviews",
                "idx_bian_signal_reviews_due_all",
                "status, updated_at, snapshot_at_ms",
            )
            if commit:
                conn.commit()
            shared._signal_review_schema_ready = True

    def _ensure_auth_schema(self, conn):
        shared = getattr(self, "_scope_parent", self)
        if shared._auth_schema_ready:
            return
        with shared._schema_lock:
            if shared._auth_schema_ready:
                return
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
            shared._auth_schema_ready = True

    def _ensure_mysql_index(self, cur, table_name, index_name, columns):
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s
            """,
            (table_name, index_name),
        )
        row = cur.fetchone()
        if not row or int(row[0] or 0) == 0:
            cur.execute(f"ALTER TABLE {table_name} ADD INDEX {index_name} ({columns})")

    def _cleanup_expired_sessions_if_due(self, conn, interval_seconds=3600):
        shared = getattr(self, "_scope_parent", self)
        now = time.time()
        if now - float(shared._last_session_cleanup or 0.0) < interval_seconds:
            return
        with shared._schema_lock:
            if now - float(shared._last_session_cleanup or 0.0) < interval_seconds:
                return
            cur = conn.cursor()
            cur.execute("DELETE FROM bian_auth_sessions WHERE expires_at<=UTC_TIMESTAMP()")
            conn.commit()
            shared._last_session_cleanup = now


storage = DashboardStorage()
