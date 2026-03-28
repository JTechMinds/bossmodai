"""BossMod AI — SQLite connection management.

Provides per-thread SQLite connections to the project database file and
initialises the schema on first access.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = os.environ.get("BOSSMOD_DB_PATH", str(_PROJECT_ROOT / "bossmod.sqlite3"))
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_SHOW_TABLES_SQL = """
SELECT name
FROM sqlite_master
WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
ORDER BY name
"""
_SQL_CAST_RE = re.compile(r"::[A-Za-z_][A-Za-z0-9_]*")
_SQL_PARAM_RE = re.compile(r"\$(\d+)\b")
_SQLITE_HEADER = b"SQLite format 3\x00"

_connection_lock = threading.RLock()
_thread_local = threading.local()
_thread_connections: dict[int, "SQLiteCompatConnection"] = {}


def _adapt_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat(sep=" ")
    return value.astimezone(timezone.utc).isoformat(sep=" ")


def _convert_timestamp(raw: bytes) -> datetime:
    value = datetime.fromisoformat(raw.decode())
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("BOOLEAN", lambda raw: bool(int(raw.decode())))
sqlite3.register_converter("TIMESTAMP", _convert_timestamp)


class SQLiteCompatConnection:
    """Small compatibility wrapper around sqlite3 for the existing DB layer."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None):
        normalized_sql, normalized_params = _normalize_statement(sql, params)
        if normalized_params is None:
            return self._raw.execute(normalized_sql)
        return self._raw.execute(normalized_sql, normalized_params)

    def executescript(self, sql: str):
        return self._raw.executescript(sql)

    def interrupt(self) -> None:
        self._raw.interrupt()

    def close(self) -> None:
        self._raw.close()


def _utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_statement(
    sql: str,
    params: list[Any] | tuple[Any, ...] | dict[str, Any] | None,
) -> tuple[str, list[Any] | tuple[Any, ...] | dict[str, Any] | None]:
    normalized = sql.strip()
    if normalized.rstrip(";").upper() == "SHOW TABLES":
        return _SHOW_TABLES_SQL, None

    normalized = _SQL_CAST_RE.sub("", normalized)
    normalized = re.sub(r"\bILIKE\b", "LIKE", normalized, flags=re.IGNORECASE)

    if params is None:
        return normalized, None
    if isinstance(params, dict):
        return normalized, params
    if not params:
        return normalized, None
    if not _SQL_PARAM_RE.search(normalized):
        return normalized, params

    bound = {f"p{index + 1}": value for index, value in enumerate(params)}
    rewritten = _SQL_PARAM_RE.sub(lambda match: f":p{match.group(1)}", normalized)
    return rewritten, bound


def _close_safely(con: SQLiteCompatConnection | None) -> None:
    if con is None:
        return
    try:
        con.close()
    except Exception:
        pass


def _invalidate_all_thread_connections_unlocked() -> None:
    for ident, con in list(_thread_connections.items()):
        _close_safely(con)
        _thread_connections.pop(ident, None)
    if hasattr(_thread_local, "connection"):
        _thread_local.connection = None


def _create_raw_connection() -> sqlite3.Connection:
    db_path = Path(_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and db_path.stat().st_size > 0:
        header = db_path.read_bytes()[: len(_SQLITE_HEADER)]
        if header != _SQLITE_HEADER:
            raise RuntimeError(
                f"Configured database file is not an SQLite database: {db_path}. "
                "Use BOSSMOD_DB_PATH to point at a fresh SQLite file, or remove the old path."
            )
    raw = sqlite3.connect(
        db_path,
        timeout=30.0,
        isolation_level=None,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("PRAGMA synchronous = NORMAL")
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA busy_timeout = 5000")
    raw.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
    raw.create_function("now", 0, _utc_now_sql)
    return raw


def get_connection() -> SQLiteCompatConnection:
    """Return a thread-local SQLite connection wrapper."""
    con = getattr(_thread_local, "connection", None)
    if con is not None:
        try:
            con.execute("SELECT 1")
        except Exception:
            logger.warning("SQLite thread connection health check failed — recreating")
            close_thread_connection()
            con = None

    if con is None:
        con = SQLiteCompatConnection(_create_raw_connection())
        with _connection_lock:
            _thread_local.connection = con
            _thread_connections[threading.get_ident()] = con
    return con


@contextmanager
def transaction() -> Generator[SQLiteCompatConnection, None, None]:
    """Wrap a block in BEGIN / COMMIT / ROLLBACK."""
    con = get_connection()
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _apply_schema(con: SQLiteCompatConnection) -> None:
    """Execute the DDL in schema.sql to ensure all tables exist."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.executescript(sql)
    logger.info("Schema applied from %s", _SCHEMA_PATH)


def _apply_migrations(con: SQLiteCompatConnection) -> None:
    """Apply additive column migrations for existing databases."""
    _add_column_if_missing(
        con, "bm_cli_events", "approval_request_id",
        "VARCHAR REFERENCES cli_approval_requests(id)",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "category",
        "VARCHAR NOT NULL DEFAULT 'general'",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "usage_syntax", "VARCHAR",
    )
    _add_column_if_missing(
        con, "cli_policy_rules", "help_text", "TEXT",
    )


def _add_column_if_missing(
    con: SQLiteCompatConnection, table: str, column: str, definition: str,
) -> None:
    """Add a column to an existing table if it doesn't already exist."""
    result = con.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in result.fetchall()}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info("Migration: added column %s.%s", table, column)


def close_thread_connection() -> None:
    """Close the current thread's SQLite connection."""
    con = getattr(_thread_local, "connection", None)
    if con is None:
        return
    with _connection_lock:
        _thread_connections.pop(threading.get_ident(), None)
    _close_safely(con)
    _thread_local.connection = None


def interrupt_thread_connection(thread_id: int | None) -> None:
    """Interrupt the active SQLite query for a specific thread, if present."""
    if thread_id is None:
        return
    with _connection_lock:
        con = _thread_connections.get(thread_id)
    if con is None:
        return
    try:
        con.interrupt()
    except Exception:
        pass


def close_connection() -> None:
    """Close all SQLite connections for this process."""
    with _connection_lock:
        _invalidate_all_thread_connections_unlocked()
    logger.info("SQLite connections closed")


def init_db() -> None:
    """Initialise the database and seed default settings."""
    con = get_connection()
    _apply_schema(con)
    _apply_migrations(con)

    from db.settings import prune_obsolete_settings, seed_defaults
    seed_defaults()
    prune_obsolete_settings()

    from db.ai_personalities import seed_default_personalities
    seed_default_personalities()

    from db.cli_policy_rules import seed_default_rules
    seed_default_rules()

    from db.agent_storage_identities import ensure_all_agent_storage_identities
    ensure_all_agent_storage_identities()

    from db.agent_storage import normalize_agent_personal_storage_roots
    normalize_agent_personal_storage_roots()

    logger.info("Database initialised")


def reset_database() -> None:
    """Recreate the database file from the current schema and seed data."""
    close_connection()
    db_path = Path(_DB_PATH)
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    init_db()
