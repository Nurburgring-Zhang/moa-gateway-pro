"""moa_gateway.storage — SQLite 持久化层
负责存储:
- 用户/鉴权(管理员、WebUI 登录 session)
- API Key 与 API Key 配额
- 模型端点(增删改)+ API Key 密文
- 调用日志(请求/响应/成本/延迟)
- 路由决策日志(可观测性)
- 限流计数器
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os  # 修 P0-3: _get_or_create_fernet 用 os.open/os.chmod/os.stat
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import bcrypt
import logging
from cryptography.fernet import Fernet

from .config import DATA_DIR, Settings, get_settings

logger = logging.getLogger(__name__)  # 修 P0-10: logger 必须在 _get_or_create_fernet 之前定义


def _bcrypt_hash(password: str) -> str:
    """bcrypt 原生 API,避免 passlib 兼容问题"""
    pwd = password.encode("utf-8")[:72]   # bcrypt 硬上限 72 字节
    return bcrypt.hashpw(pwd, bcrypt.gensalt(rounds=12)).decode("utf-8")


def _bcrypt_verify(password: str, hashed: str) -> bool:
    pwd = password.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(pwd, hashed.encode("utf-8"))
    except Exception:
        return False


# ========== 异步包装(修11: bcrypt 不再阻塞 event loop) ==========
async def async_bcrypt_hash(password: str) -> str:
    """在线程池里跑 bcrypt.hash,避免阻塞 event loop"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _bcrypt_hash, password)


async def async_bcrypt_verify(password: str, hashed: str) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _bcrypt_verify, password, hashed)


# 加密用 key(在 data/.fernet_key 里)
_FERNET_PATH = DATA_DIR / ".fernet_key"
_FERNET_SINGLEFLIGHT = threading.Lock()
_FERNET_INSTANCE: Fernet | None = None


def _get_or_create_fernet() -> Fernet:
    """获取或创建 Fernet key(用于加密数据库里存储的 API key)

    修 P0-3:
    - 单例 + 进程内 lock(单次生成/读取,不在每次 encrypt 时都判断)
    - 原子创建 (O_CREAT | O_EXCL),别人先创就直接读
    - 启动时检查文件权限,mode & 0o077 != 0 直接报错(不再 silent pass)
    """
    global _FERNET_INSTANCE
    if _FERNET_INSTANCE is not None:
        return _FERNET_INSTANCE
    with _FERNET_SINGLEFLIGHT:
        if _FERNET_INSTANCE is not None:
            return _FERNET_INSTANCE
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # 修 P0-3: 启动时检查权限
        if _FERNET_PATH.exists():
            try:
                st = os.stat(_FERNET_PATH)
                # Windows 上 st_mode 可能不准确,只在有 group/other 读权限时报警
                if hasattr(os, "getuid") and (st.st_mode & 0o077):
                    raise RuntimeError(
                        f".fernet_key has overly permissive mode {oct(st.st_mode & 0o777)}, "
                        f"expected 0o600. Run: chmod 600 {_FERNET_PATH}"
                    )
            except OSError:
                pass  # 平台不支持
            key = _FERNET_PATH.read_bytes()
        else:
            key = Fernet.generate_key()
            # 修 P0-3: 原子创建,避免 TOCTOU
            try:
                fd = os.open(str(_FERNET_PATH),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o600)
                try:
                    os.write(fd, key)
                finally:
                    os.close(fd)
            except FileExistsError:
                # 别人先创了,直接读
                key = _FERNET_PATH.read_bytes()
            else:
                # 收紧权限(P2-2 加固,Windows 上 best-effort)
                with suppress(Exception):
                    os.chmod(_FERNET_PATH, 0o600)
        _FERNET_INSTANCE = Fernet(key)
        return _FERNET_INSTANCE


# ========== Schema ==========
SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at REAL NOT NULL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0,
    window_start REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id TEXT UNIQUE NOT NULL,
    key_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    quota_rpm INTEGER DEFAULT 60,
    quota_daily_tokens INTEGER DEFAULT 5000000,
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    last_used REAL,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS model_endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    tier TEXT NOT NULL,
    api_base TEXT,
    api_key_encrypted BLOB,
    api_key_env TEXT,
    cost_per_1k_input REAL DEFAULT 0.001,
    cost_per_1k_output REAL DEFAULT 0.002,
    max_tokens INTEGER DEFAULT 8192,
    timeout INTEGER DEFAULT 120,
    weight INTEGER DEFAULT 100,
    enabled INTEGER DEFAULT 0,
    tags TEXT,
    extra TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    api_key_id TEXT,
    timestamp REAL NOT NULL,
    model_requested TEXT,
    model_used TEXT,
    preset TEXT,
    strategy TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    status TEXT,
    error TEXT,
    consensus_score REAL,
    fallback_used INTEGER DEFAULT 0,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_request_logs_apikey ON request_logs(api_key_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_status_ts ON request_logs(status, timestamp);
CREATE INDEX IF NOT EXISTS idx_request_logs_model_ts ON request_logs(model_used, timestamp);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash) WHERE enabled = 1;

CREATE TABLE IF NOT EXISTS config_overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ratelimit_buckets (
    api_key_id TEXT NOT NULL,
    bucket TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (api_key_id, bucket)
);

CREATE TABLE IF NOT EXISTS ratelimit_tokens (
    api_key_id TEXT NOT NULL,
    day TEXT NOT NULL,            -- YYYYMMDD
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (api_key_id, day)
);
"""


class Storage:
    """SQLite 存储管理器(单实例,内部用连接池)"""
    _instance: Storage | None = None
    _lock = threading.Lock()

    def __init__(self, db_path: Path | None = None):
        settings = get_settings()
        self.db_path = Path(db_path or (DATA_DIR / settings.storage.db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 让 fastapi 多线程可访问
        self._conn_lock = threading.RLock()
        self._init_schema()
        self._bootstrap_admin(settings)

    @classmethod
    def instance(cls) -> Storage:
        with cls._lock:
            if cls._instance is None:
                cls._instance = Storage()
            return cls._instance

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._conn_lock:
            c = sqlite3.connect(str(self.db_path), timeout=30)
            c.row_factory = sqlite3.Row
            # WAL 模式 + busy_timeout(防高并发锁表)
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA synchronous=NORMAL")
                c.execute("PRAGMA busy_timeout=5000")
                c.execute("PRAGMA foreign_keys=ON")
            except Exception:
                pass
            try:
                yield c
                c.commit()
            finally:
                c.close()

    def get_conn(self) -> sqlite3.Connection:
        """修 P0-6: 返回持久连接,避免每次 open+close 1ms 浪费。
        使用方法:
            conn = storage.get_conn()
            try:
                cur = conn.execute(...)
                ...
            finally:
                pass  # conn 由 storage 持有
        """
        if self._persistent_conn is None:
            self._persistent_conn = sqlite3.connect(str(self.db_path), timeout=30)
            self._persistent_conn.row_factory = sqlite3.Row
            try:
                self._persistent_conn.execute("PRAGMA journal_mode=WAL")
                self._persistent_conn.execute("PRAGMA synchronous=NORMAL")
                self._persistent_conn.execute("PRAGMA busy_timeout=5000")
            except Exception:
                pass
        return self._persistent_conn

    def _init_schema(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)

    # ========== Admin / Auth ==========
    def _bootstrap_admin(self, settings: Settings) -> None:
        """首次启动创建默认管理员(密码不能为空,空就拒绝启动)"""
        # 修26: 安全加固 — config.yaml 里 admin_password 必须是真值,不能是默认 'admin'
        if not settings.auth.admin_password or settings.auth.admin_password.strip() == "":
            raise RuntimeError(
                "config.yaml: auth.admin_password 不能为空!\n"
                "请设置一个 ≥6 位的强密码(必须含字母+数字),示例:\n"
                "  auth:\n"
                "    admin_password: 'YourStrong#Pass1'\n"
                "或用环境变量覆盖:\n"
                "  export MOA_ADMIN_PASSWORD='YourStrong#Pass1'"
            )
        with self.conn() as c:
            row = c.execute("SELECT id FROM admin_users WHERE username = ?",
                            (settings.auth.admin_username,)).fetchone()
            if not row:
                h = _bcrypt_hash(settings.auth.admin_password)
                c.execute(
                    "INSERT INTO admin_users (username, password_hash, role, created_at) "
                    "VALUES (?, ?, 'admin', ?)",
                    (settings.auth.admin_username, h, time.time())
                )
                logger.info("Bootstrap admin user: %s", settings.auth.admin_username)

    def verify_admin(self, username: str, password: str) -> dict[str, Any] | None:
        """修17: 检测默认 admin/admin 是否在用,返回 must_change_password 标记
        WebUI 拿到这个标记后强制弹改密窗口。"""
        with self.conn() as c:
            row = c.execute("SELECT * FROM admin_users WHERE username = ?",
                            (username,)).fetchone()
            if not row:
                return None
            if not _bcrypt_verify(password, row["password_hash"]):
                return None
            c.execute("UPDATE admin_users SET last_login = ? WHERE id = ?",
                      (time.time(), row["id"]))
            # 检查是否还在用默认密码
            settings = get_settings()
            must_change = (
                username == settings.auth.admin_username
                and password == settings.auth.admin_password
                and password != ""  # 不是空密码(空密码也视为"安全")
            )
            return {
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "must_change_password": must_change,
            }

    def change_admin_password(self, username: str, new_password: str) -> bool:
        # 修11: bcrypt rounds=12 大约 300ms,在 async 路径会阻塞 event loop。
        # 调用方(auth.change_password)应使用 run_in_executor 异步化。
        # 这里仅做密码长度校验(72 字节限制),不阻断哈希过程。
        if len(new_password.encode("utf-8")) > 72:
            raise ValueError("password too long (>72 bytes); bcrypt will silently truncate")
        with self.conn() as c:
            cur = c.execute(
                "UPDATE admin_users SET password_hash = ? WHERE username = ?",
                (_bcrypt_hash(new_password), username)
            )
            return cur.rowcount > 0

    # ========== API Keys ==========
    @staticmethod
    def _hash_key(k: str) -> str:
        return hashlib.sha256(k.encode("utf-8")).hexdigest()

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def create_api_key(self, name: str, quota_rpm: int = 60,
                       quota_daily_tokens: int = 5_000_000) -> dict[str, Any]:
        """创建 API Key,返回明文 key(只此一次)"""
        raw = "mgw-" + secrets.token_urlsafe(24)
        key_id = "key_" + secrets.token_hex(6)
        with self.conn() as c:
            c.execute(
                "INSERT INTO api_keys (key_id, key_hash, name, quota_rpm, "
                "quota_daily_tokens, enabled, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (key_id, self._hash_key(raw), name, quota_rpm,
                 quota_daily_tokens, time.time())
            )
        return {"key_id": key_id, "key": raw, "name": name,
                "quota_rpm": quota_rpm, "quota_daily_tokens": quota_daily_tokens}

    def revoke_api_key(self, key_id: str) -> bool:
        with self.conn() as c:
            cur = c.execute("UPDATE api_keys SET enabled = 0 WHERE key_id = ?", (key_id,))
            return cur.rowcount > 0

    def delete_api_key(self, key_id: str) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))
            return cur.rowcount > 0

    def update_api_key(self, key_id: str, **fields) -> bool:
        if not fields:
            return False
        cols = ", ".join(f"{k} = ?" for k in fields)
        args = list(fields.values()) + [key_id]
        with self.conn() as c:
            cur = c.execute(f"UPDATE api_keys SET {cols} WHERE key_id = ?", args)
            return cur.rowcount > 0

    def find_api_key(self, raw: str) -> dict[str, Any] | None:
        h = self._hash_key(raw)
        with self.conn() as c:
            row = c.execute("SELECT * FROM api_keys WHERE key_hash = ? AND enabled = 1",
                            (h,)).fetchone()
            if not row:
                return None
            d = dict(row)
            c.execute("UPDATE api_keys SET last_used = ? WHERE key_id = ?",
                      (time.time(), d["key_id"]))
            return d

    # ========== Model Endpoints ==========
    def _encrypt(self, plain: str) -> bytes | None:
        if not plain:
            return None
        return _get_or_create_fernet().encrypt(plain.encode("utf-8"))

    def _decrypt(self, blob: bytes | None) -> str:
        if not blob:
            return ""
        try:
            return _get_or_create_fernet().decrypt(blob).decode("utf-8")
        except Exception as e:
            logger.warning("decrypt failed: %s", e)
            return ""

    def upsert_endpoint(self, ep: dict[str, Any]) -> dict[str, Any]:
        """插入/更新模型端点。ep 必须含 endpoint_id。api_key 明文可选。"""
        eid = ep["endpoint_id"]
        api_key_plain = ep.pop("api_key_plain", None)
        api_key_enc = self._encrypt(api_key_plain) if api_key_plain else ep.get("api_key_encrypted")

        now = time.time()
        with self.conn() as c:
            row = c.execute("SELECT id FROM model_endpoints WHERE endpoint_id = ?",
                            (eid,)).fetchone()
            if row:
                updates = []
                args = []
                for k in ["provider", "model", "tier", "api_base",
                          "cost_per_1k_input", "cost_per_1k_output",
                          "max_tokens", "timeout", "weight", "enabled",
                          "api_key_env", "tags", "extra"]:
                    if k in ep and ep[k] is not None:
                        v = ep[k]
                        if k in ("tags", "extra") and not isinstance(v, str):
                            v = json.dumps(v, ensure_ascii=False)
                        updates.append(f"{k} = ?")
                        args.append(v)
                if api_key_enc is not None:
                    updates.append("api_key_encrypted = ?")
                    args.append(api_key_enc)
                updates.append("updated_at = ?")
                args.append(now)
                args.append(eid)
                c.execute(f"UPDATE model_endpoints SET {', '.join(updates)} "
                          f"WHERE endpoint_id = ?", args)
            else:
                c.execute(
                    "INSERT INTO model_endpoints "
                    "(endpoint_id, provider, model, tier, api_base, "
                    " api_key_encrypted, api_key_env, "
                    " cost_per_1k_input, cost_per_1k_output, max_tokens, "
                    " timeout, weight, enabled, tags, extra, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (eid,
                     ep["provider"], ep["model"], ep["tier"], ep.get("api_base", ""),
                     api_key_enc, ep.get("api_key_env", ""),
                     ep.get("cost_per_1k_input", 0.001),
                     ep.get("cost_per_1k_output", 0.002),
                     ep.get("max_tokens", 8192),
                     ep.get("timeout", 120),
                     ep.get("weight", 100),
                     1 if ep.get("enabled", True) else 0,
                     json.dumps(ep.get("tags", []), ensure_ascii=False),
                     json.dumps(ep.get("extra", {}), ensure_ascii=False),
                     now, now)
                )
        return self.get_endpoint(eid) or {}

    def get_endpoint(self, eid: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM model_endpoints WHERE endpoint_id = ?",
                            (eid,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["api_key"] = self._decrypt(d.pop("api_key_encrypted"))
            for f in ("tags", "extra"):
                if d.get(f):
                    with suppress(Exception):
                        d[f] = json.loads(d[f])
            d["enabled"] = bool(d.get("enabled"))
            return d

    def list_endpoints(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM model_endpoints ORDER BY endpoint_id").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["api_key"] = "***" if d.get("api_key_encrypted") else ""  # 列表不暴露
                d.pop("api_key_encrypted", None)
                for f in ("tags", "extra"):
                    if d.get(f):
                        with suppress(Exception):
                            d[f] = json.loads(d[f])
                d["enabled"] = bool(d.get("enabled"))
                result.append(d)
            return result

    def delete_endpoint(self, eid: str) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM model_endpoints WHERE endpoint_id = ?", (eid,))
            return cur.rowcount > 0

    # ========== Request Logs ==========
    def log_request(self, log: dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO request_logs "
                "(request_id, api_key_id, timestamp, model_requested, model_used, "
                " preset, strategy, prompt_tokens, completion_tokens, total_tokens, "
                " cost, latency_ms, status, error, consensus_score, fallback_used, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (log.get("request_id", ""), log.get("api_key_id"),
                 log.get("timestamp", time.time()),
                 log.get("model_requested"), log.get("model_used"),
                 log.get("preset"), log.get("strategy"),
                 log.get("prompt_tokens", 0), log.get("completion_tokens", 0),
                 log.get("total_tokens", 0),
                 log.get("cost", 0.0), log.get("latency_ms", 0.0),
                 log.get("status", "ok"), log.get("error", ""),
                 log.get("consensus_score"),
                 1 if log.get("fallback_used") else 0,
                 json.dumps(log.get("metadata", {}), ensure_ascii=False))
            )

    def list_logs(self, limit: int = 100, api_key_id: str | None = None) -> list[dict[str, Any]]:
        with self.conn() as c:
            if api_key_id:
                rows = c.execute("SELECT * FROM request_logs WHERE api_key_id = ? "
                                 "ORDER BY timestamp DESC LIMIT ?",
                                 (api_key_id, limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM request_logs ORDER BY timestamp DESC LIMIT ?",
                                 (limit,)).fetchall()
            return [dict(r) for r in rows]

    def aggregate_stats(self, since_ts: float = 0) -> dict[str, Any]:
        with self.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) as n, "
                "COALESCE(SUM(total_tokens), 0) as total_tokens, "
                "COALESCE(SUM(cost), 0) as total_cost, "
                "COALESCE(AVG(latency_ms), 0) as avg_latency "
                "FROM request_logs WHERE timestamp >= ?",
                (since_ts,)
            ).fetchone()
            by_status = c.execute(
                "SELECT status, COUNT(*) as n FROM request_logs "
                "WHERE timestamp >= ? GROUP BY status",
                (since_ts,)
            ).fetchall()
            by_model = c.execute(
                "SELECT model_used as model, COUNT(*) as n, COALESCE(SUM(cost), 0) as cost "
                "FROM request_logs WHERE timestamp >= ? AND model_used IS NOT NULL "
                "GROUP BY model_used ORDER BY n DESC LIMIT 10",
                (since_ts,)
            ).fetchall()
            return {
                "total_requests": row["n"],
                "total_tokens": row["total_tokens"],
                "total_cost": round(row["total_cost"], 6),
                "avg_latency_ms": round(row["avg_latency"], 1),
                "by_status": [dict(r) for r in by_status],
                "top_models": [dict(r) for r in by_model]
            }

    def cleanup_old_logs(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        with self.conn() as c:
            cur = c.execute("DELETE FROM request_logs WHERE timestamp < ?", (cutoff,))
            return cur.rowcount

    # ========== Config overrides ==========
    def get_config_overrides(self) -> dict[str, Any]:
        with self.conn() as c:
            rows = c.execute("SELECT key, value FROM config_overrides").fetchall()
            result = {}
            for r in rows:
                try:
                    result[r["key"]] = json.loads(r["value"])
                except Exception:
                    result[r["key"]] = r["value"]
            return result

    def set_config_override(self, key: str, value: Any) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO config_overrides (key, value, updated_at) "
                      "VALUES (?, ?, ?)", (key, json.dumps(value, ensure_ascii=False), time.time()))

    # ========== RateLimit counters ==========
    def incr_rpm(self, api_key_id: str, bucket: str) -> int:
        """修 P0-1: 用 BEGIN IMMEDIATE 保证 inc+select 原子,避免 lost-update 限流穿透
        原: 两步 (INSERT ON CONFLICT + SELECT) 在 sqlite 并发下不安全
        """
        with self.conn() as c:
            # 修 P0-1: BEGIN IMMEDIATE 拿写锁,事务内 inc+select 原子
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    "INSERT INTO ratelimit_buckets (api_key_id, bucket, count, updated_at) "
                    "VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(api_key_id, bucket) DO UPDATE SET count = count + 1, updated_at = ?",
                    (api_key_id, bucket, time.time(), time.time())
                )
                row = c.execute(
                    "SELECT count FROM ratelimit_buckets WHERE api_key_id = ? AND bucket = ?",
                    (api_key_id, bucket)
                ).fetchone()
                result = int(row["count"]) if row else 0
                c.execute("COMMIT")
                return result
            except Exception:
                c.execute("ROLLBACK")
                raise

    def incr_daily_tokens(self, api_key_id: str, day: str, tokens: int) -> int:
        """修 P0-1: atomic inc+select 防 lost-update"""
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    "INSERT INTO ratelimit_tokens (api_key_id, day, tokens) VALUES (?, ?, ?) "
                    "ON CONFLICT(api_key_id, day) DO UPDATE SET tokens = tokens + ?",
                    (api_key_id, day, tokens, tokens)
                )
                row = c.execute(
                    "SELECT tokens FROM ratelimit_tokens WHERE api_key_id = ? AND day = ?",
                    (api_key_id, day)
                ).fetchone()
                result = int(row["tokens"]) if row else tokens
                c.execute("COMMIT")
                return result
            except Exception:
                c.execute("ROLLBACK")
                raise

    def get_daily_tokens(self, api_key_id: str, day: str) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT tokens FROM ratelimit_tokens WHERE api_key_id = ? AND day = ?",
                (api_key_id, day)
            ).fetchone()
            return int(row["tokens"]) if row else 0


def get_storage() -> Storage:
    return Storage.instance()
