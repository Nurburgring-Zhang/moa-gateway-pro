"""moa_gateway.database — 数据库引擎工厂

支持双后端:
- SQLite (开发/测试默认) — 零配置,文件数据库
- PostgreSQL (生产) — 连接池,高并发,事务隔离

通过环境变量 DATABASE_URL 切换:
  SQLite:      sqlite:///./data/config.db  (默认)
  PostgreSQL:  postgresql+psycopg2://user:pass@localhost:5432/moa_gateway

连接池参数(仅 PostgreSQL 生效):
  DB_POOL_SIZE=20        核心连接数
  DB_MAX_OVERFLOW=10     突发溢出连接数
  DB_POOL_TIMEOUT=30     获取连接超时(秒)
  DB_POOL_RECYCLE=3600   连接回收周期(秒)
  DB_POOL_PRE_PING=true  连接健康检查
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ============ Configuration ============

# 默认使用 SQLite (向后兼容)
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# PostgreSQL 连接池参数
POOL_CONFIG = {
    "pool_size": int(os.getenv("DB_POOL_SIZE", "20")),
    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
    "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
    "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "3600")),
    "pool_pre_ping": os.getenv("DB_POOL_PRE_PING", "true").lower() in ("true", "1", "yes"),
}


def is_postgres(url: str = "") -> bool:
    """判断是否使用 PostgreSQL 后端"""
    target = url or DATABASE_URL
    return target.startswith("postgresql")


# ============ SQLAlchemy Engine (PostgreSQL) ============

_sa_engine = None
_sa_lock = threading.Lock()


def get_sqlalchemy_engine(url: str = ""):
    """获取或创建 SQLAlchemy 引擎 (用于 PostgreSQL 后端)

    连接池参数从环境变量读取,生产级配置:
    - pool_size=20: 维持20个长连接
    - max_overflow=10: 峰值可临时扩到30
    - pool_pre_ping=True: 借出连接前检测存活
    - pool_recycle=3600: 每小时回收,避免服务端断连
    """
    global _sa_engine
    if _sa_engine is not None:
        return _sa_engine

    with _sa_lock:
        if _sa_engine is not None:
            return _sa_engine

        try:
            from sqlalchemy import create_engine
        except ImportError as e:
            raise ImportError(
                "PostgreSQL backend requires sqlalchemy. "
                "Install with: pip install sqlalchemy[asyncio] psycopg2-binary"
            ) from e

        target_url = url or DATABASE_URL
        logger.info(
            "Initializing PostgreSQL engine (pool_size=%d, max_overflow=%d)",
            POOL_CONFIG["pool_size"],
            POOL_CONFIG["pool_overflow" if False else "max_overflow"],
        )

        _sa_engine = create_engine(
            target_url,
            pool_size=POOL_CONFIG["pool_size"],
            max_overflow=POOL_CONFIG["max_overflow"],
            pool_timeout=POOL_CONFIG["pool_timeout"],
            pool_recycle=POOL_CONFIG["pool_recycle"],
            pool_pre_ping=POOL_CONFIG["pool_pre_ping"],
            echo=os.getenv("DB_ECHO", "false").lower() in ("true", "1"),
        )
        return _sa_engine


# ============ Async Engine (for future async migration) ============

_async_engine = None
_async_lock = threading.Lock()


def get_async_engine(url: str = ""):
    """获取异步引擎 (asyncpg for PostgreSQL, aiosqlite for SQLite)

    用于未来将 Storage 层迁移为全异步时使用。
    当前版本 Storage 仍为同步,此接口为前瞻性设计。
    """
    global _async_engine
    if _async_engine is not None:
        return _async_engine

    with _async_lock:
        if _async_engine is not None:
            return _async_engine

        try:
            from sqlalchemy.ext.asyncio import create_async_engine
        except ImportError as e:
            raise ImportError(
                "Async engine requires sqlalchemy[asyncio]. "
                "Install with: pip install sqlalchemy[asyncio] asyncpg aiosqlite"
            ) from e

        target_url = url or DATABASE_URL
        # 转换 URL 为异步驱动格式
        if target_url.startswith("postgresql://"):
            async_url = target_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif target_url.startswith("postgresql+psycopg2://"):
            async_url = target_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        elif target_url.startswith("postgresql+asyncpg://"):
            async_url = target_url
        elif target_url.startswith("sqlite:///"):
            async_url = target_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        else:
            async_url = target_url

        engine_kwargs: dict[str, Any] = {
            "echo": os.getenv("DB_ECHO", "false").lower() in ("true", "1"),
        }

        # PostgreSQL 连接池参数
        if async_url.startswith("postgresql"):
            engine_kwargs.update({
                "pool_size": POOL_CONFIG["pool_size"],
                "max_overflow": POOL_CONFIG["max_overflow"],
                "pool_timeout": POOL_CONFIG["pool_timeout"],
                "pool_recycle": POOL_CONFIG["pool_recycle"],
                "pool_pre_ping": POOL_CONFIG["pool_pre_ping"],
            })

        _async_engine = create_async_engine(async_url, **engine_kwargs)
        return _async_engine


# ============ SQLite Backend (保持向后兼容) ============


class SQLiteBackend:
    """SQLite 连接管理器 — 与原 Storage.conn() 行为完全一致

    保持原有 PRAGMA 配置:
    - journal_mode=WAL (Write-Ahead Logging)
    - synchronous=NORMAL
    - busy_timeout=5000
    - foreign_keys=ON
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn_lock = threading.RLock()
        self._persistent_conn: sqlite3.Connection | None = None

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        """获取带事务的 SQLite 连接 (与原 Storage.conn() 行为一致)"""
        with self._conn_lock:
            c = sqlite3.connect(str(self.db_path), timeout=30)
            c.row_factory = sqlite3.Row
            self._apply_pragmas(c)
            try:
                yield c
                c.commit()
            finally:
                c.close()

    def get_conn(self) -> sqlite3.Connection:
        """持久连接 (避免频繁 open/close)"""
        if self._persistent_conn is None:
            self._persistent_conn = sqlite3.connect(str(self.db_path), timeout=30)
            self._persistent_conn.row_factory = sqlite3.Row
            self._apply_pragmas(self._persistent_conn)
        return self._persistent_conn

    def execute_script(self, sql: str) -> None:
        """执行多语句 SQL 脚本 (建表等)"""
        with self.conn() as c:
            c.executescript(sql)

    def close(self) -> None:
        """关闭持久连接"""
        if self._persistent_conn:
            self._persistent_conn.close()
            self._persistent_conn = None

    @staticmethod
    def _apply_pragmas(c: sqlite3.Connection) -> None:
        """应用 SQLite 优化 PRAGMA"""
        for pragma, val in [
            ("journal_mode", "WAL"),
            ("synchronous", "NORMAL"),
            ("busy_timeout", "5000"),
            ("foreign_keys", "ON"),
        ]:
            try:
                c.execute(f"PRAGMA {pragma}={val}")
            except Exception as e:
                logger.warning("Failed to set PRAGMA %s: %s", pragma, e)


# ============ PostgreSQL Backend ============


class PostgreSQLBackend:
    """PostgreSQL 连接管理器 — 使用 SQLAlchemy 连接池

    提供与 SQLiteBackend 相同的 conn() 接口,
    返回的连接对象支持 .execute()/.fetchone()/.fetchall()
    """

    def __init__(self, url: str):
        self.url = url
        self._engine = get_sqlalchemy_engine(url)
        self._schema_initialized = False

    @contextmanager
    def conn(self) -> Iterator[Any]:
        """获取 PostgreSQL 连接(兼容 sqlite3.Row 风格)"""
        connection = self._engine.connect()
        try:
            # 包装为兼容接口
            wrapper = _PGConnectionWrapper(connection)
            yield wrapper
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute_script(self, sql: str) -> None:
        """执行建表脚本 (转换 SQLite DDL → PostgreSQL DDL)"""
        pg_sql = self._convert_ddl(sql)
        with self._engine.connect() as conn:
            from sqlalchemy import text
            for statement in self._split_statements(pg_sql):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))
            conn.commit()
        self._schema_initialized = True

    def close(self) -> None:
        """关闭引擎连接池"""
        if self._engine:
            self._engine.dispose()

    @staticmethod
    def _convert_ddl(sql: str) -> str:
        """SQLite DDL → PostgreSQL DDL 转换"""
        import re
        # AUTOINCREMENT → SERIAL (PostgreSQL 使用 SERIAL 自增)
        result = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "SERIAL PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )
        # REAL → DOUBLE PRECISION
        result = re.sub(r"\bREAL\b", "DOUBLE PRECISION", result)
        # INTEGER DEFAULT 0/1 for boolean → keep as INTEGER (PG compatible)
        # BLOB → BYTEA
        result = re.sub(r"\bBLOB\b", "BYTEA", result)
        # INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE
        # (this is handled at query time, not DDL)
        # Remove SQLite-specific PRAGMA statements
        result = re.sub(r"PRAGMA\s+[^;]+;?", "", result, flags=re.IGNORECASE)
        # CREATE INDEX IF NOT EXISTS — PostgreSQL supports this
        return result

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        """Split SQL script into individual statements"""
        statements = []
        current = []
        for line in sql.split("\n"):
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                stmt = "\n".join(current).strip().rstrip(";").strip()
                if stmt:
                    statements.append(stmt)
                current = []
        if current:
            stmt = "\n".join(current).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
        return statements


class _PGConnectionWrapper:
    """PostgreSQL 连接包装器 — 提供 sqlite3.Connection 兼容接口

    将 SQLAlchemy Connection 包装为支持:
    - .execute(sql, params) → 自动将 ? 占位符转为 :p1, :p2 ...
    - .fetchone() / .fetchall() → 返回 dict-like Row 对象
    """

    def __init__(self, sa_conn):
        self._conn = sa_conn
        self._last_result = None

    def execute(self, sql: str, params=None):
        from sqlalchemy import text

        # 转换 ? 占位符为 :p1, :p2, ... (命名参数)
        converted_sql, converted_params = self._convert_placeholders(sql, params)
        self._last_result = self._conn.execute(text(converted_sql), converted_params or {})
        return _PGCursorWrapper(self._last_result)

    def executescript(self, sql: str):
        """执行多语句脚本"""
        from sqlalchemy import text
        for stmt in PostgreSQLBackend._split_statements(sql):
            if stmt.strip():
                self._conn.execute(text(stmt))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    @staticmethod
    def _convert_placeholders(sql: str, params=None):
        """将 SQLite ? 占位符转为 SQLAlchemy :named 参数

        Examples:
            "WHERE id = ? AND name = ?" + (1, "foo")
            → "WHERE id = :p1 AND name = :p2" + {"p1": 1, "p2": "foo"}
        """
        if params is None:
            return sql, {}

        if isinstance(params, dict):
            return sql, params

        # 处理 BEGIN/COMMIT/ROLLBACK (无参数)
        sql_upper = sql.strip().upper()
        if sql_upper in ("BEGIN IMMEDIATE", "BEGIN", "COMMIT", "ROLLBACK"):
            return sql, {}

        # 逐个替换 ? 为 :p1, :p2, ...
        converted = []
        param_dict = {}
        param_idx = 0
        i = 0
        while i < len(sql):
            if sql[i] == "?" and (i == 0 or sql[i - 1] != "\\"):
                param_idx += 1
                name = f"p{param_idx}"
                converted.append(f":{name}")
                if param_idx <= len(params):
                    param_dict[name] = params[param_idx - 1]
            else:
                converted.append(sql[i])
            i += 1

        return "".join(converted), param_dict


class _PGCursorWrapper:
    """PostgreSQL 游标包装器 — 模拟 sqlite3.Cursor 接口"""

    def __init__(self, result):
        self._result = result
        self._rowcount = result.rowcount if hasattr(result, "rowcount") else 0

    @property
    def rowcount(self) -> int:
        return self._rowcount

    def fetchone(self):
        row = self._result.fetchone()
        if row is None:
            return None
        return _PGRowWrapper(row, self._result.keys())

    def fetchall(self):
        rows = self._result.fetchall()
        keys = self._result.keys()
        return [_PGRowWrapper(r, keys) for r in rows]


class _PGRowWrapper:
    """模拟 sqlite3.Row — 支持 dict(row) 和 row["column"] 访问"""

    def __init__(self, row, keys):
        self._data = dict(zip(keys, row))

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data.values())

    def __len__(self):
        return len(self._data)


# ============ Database Engine Factory ============


class DatabaseEngine:
    """数据库引擎工厂 — 统一入口

    使用方式:
        engine = DatabaseEngine.create()
        with engine.conn() as c:
            c.execute("SELECT ...")

    环境变量:
        DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db  → PostgreSQL
        DATABASE_URL=  (空/未设置)                                  → SQLite (默认)
    """

    _instance: DatabaseEngine | None = None
    _lock = threading.Lock()

    def __init__(self, backend):
        self.backend = backend
        self.backend_type = "postgresql" if isinstance(backend, PostgreSQLBackend) else "sqlite"

    @classmethod
    def create(cls, db_path: Path | None = None, url: str = "") -> DatabaseEngine:
        """创建数据库引擎

        Args:
            db_path: SQLite 数据库路径 (仅 SQLite 后端使用)
            url: 数据库 URL,覆盖环境变量

        Returns:
            DatabaseEngine 实例
        """
        target_url = url or DATABASE_URL
        if is_postgres(target_url):
            backend = PostgreSQLBackend(target_url)
            logger.info(
                "Database engine: PostgreSQL (pool_size=%d, max_overflow=%d)",
                POOL_CONFIG["pool_size"],
                POOL_CONFIG["max_overflow"],
            )
        else:
            if db_path is None:
                from .config import DATA_DIR, get_settings
                settings = get_settings()
                db_path = DATA_DIR / settings.storage.db_path
            backend = SQLiteBackend(db_path)
            logger.info("Database engine: SQLite (%s)", db_path)
        return cls(backend)

    @contextmanager
    def conn(self) -> Iterator[Any]:
        """获取数据库连接 (context manager)"""
        with self.backend.conn() as c:
            yield c

    def execute_script(self, sql: str) -> None:
        """执行 DDL 脚本"""
        self.backend.execute_script(sql)

    def close(self) -> None:
        """关闭数据库连接/连接池"""
        self.backend.close()

    @property
    def is_postgres(self) -> bool:
        return self.backend_type == "postgresql"

    @property
    def is_sqlite(self) -> bool:
        return self.backend_type == "sqlite"


# ============ Module-level helpers ============


def get_database_info() -> dict[str, Any]:
    """返回当前数据库配置信息 (用于 /health 端点)"""
    url = DATABASE_URL
    if is_postgres(url):
        # 脱敏: 隐藏密码
        import re
        sanitized = re.sub(r"://[^:]+:[^@]+@", "://***:***@", url)
        return {
            "backend": "postgresql",
            "url": sanitized,
            "pool_size": POOL_CONFIG["pool_size"],
            "max_overflow": POOL_CONFIG["max_overflow"],
            "pool_timeout": POOL_CONFIG["pool_timeout"],
            "pool_recycle": POOL_CONFIG["pool_recycle"],
            "pool_pre_ping": POOL_CONFIG["pool_pre_ping"],
        }
    return {
        "backend": "sqlite",
        "pool_size": 1,
        "note": "Single-file database, suitable for development/testing",
    }
