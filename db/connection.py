"""BossMod AI — DuckDB connection management.

Provides a single-writer connection to ``bossmod.db`` at the project root
and auto-initialises the schema on first access.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import duckdb

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = str(_PROJECT_ROOT / "bossmod.db")
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

_root_connection: duckdb.DuckDBPyConnection | None = None
_connection_lock = threading.RLock()
_thread_local = threading.local()
_thread_connections: dict[int, duckdb.DuckDBPyConnection] = {}


def _close_safely(con: duckdb.DuckDBPyConnection | None) -> None:
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


def _ensure_root_connection() -> duckdb.DuckDBPyConnection:
    """Return the root DuckDB connection used to derive per-thread cursors."""
    global _root_connection
    with _connection_lock:
        if _root_connection is not None:
            try:
                _root_connection.execute("SELECT 1")
            except Exception:
                logger.warning("DuckDB root connection health check failed — reconnecting")
                _invalidate_all_thread_connections_unlocked()
                _close_safely(_root_connection)
                _root_connection = None
        if _root_connection is None:
            _root_connection = duckdb.connect(_DB_PATH)
            _apply_schema(_root_connection)
            logger.info("DuckDB root connection opened: %s", _DB_PATH)
        return _root_connection


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a thread-local DuckDB cursor connection.

    DuckDB supports concurrent work inside a single process when each thread
    uses its own cursor/connection derived from a shared root connection.
    """
    con = getattr(_thread_local, "connection", None)
    if con is not None:
        try:
            con.execute("SELECT 1")
        except Exception:
            logger.warning("DuckDB thread connection health check failed — recreating")
            close_thread_connection()
            con = None

    if con is None:
        root = _ensure_root_connection()
        con = root.cursor()
        _thread_local.connection = con
        with _connection_lock:
            _thread_connections[threading.get_ident()] = con
    return con


@contextmanager
def transaction() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Context manager that wraps a block in BEGIN / COMMIT / ROLLBACK.

    Usage::

        with transaction() as con:
            con.execute("INSERT INTO ...", [...])
            con.execute("INSERT INTO ...", [...])
    """
    con = get_connection()
    con.execute("BEGIN TRANSACTION")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Execute the DDL in schema.sql to ensure all tables exist."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    con.execute(sql)
    logger.info("Schema applied from %s", _SCHEMA_PATH)


def close_thread_connection() -> None:
    """Close the current thread's DuckDB cursor connection."""
    con = getattr(_thread_local, "connection", None)
    if con is None:
        return
    with _connection_lock:
        _thread_connections.pop(threading.get_ident(), None)
    _close_safely(con)
    _thread_local.connection = None


def interrupt_thread_connection(thread_id: int | None) -> None:
    """Interrupt the active DuckDB query for a specific thread, if present."""
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
    """Close all DuckDB connections for this process (e.g. during shutdown)."""
    global _root_connection
    with _connection_lock:
        _invalidate_all_thread_connections_unlocked()
        _close_safely(_root_connection)
        _root_connection = None
    logger.info("DuckDB connections closed")


def init_db() -> None:
    """Explicitly initialise the database and seed default settings.

    Safe to call multiple times — every ``CREATE TABLE`` uses
    ``IF NOT EXISTS``. Seeds default settings on first run.
    """
    con = get_connection()
    _apply_schema(con)

    from db.settings import prune_obsolete_settings, seed_defaults
    seed_defaults()
    prune_obsolete_settings()

    from db.ai_personalities import seed_default_personalities
    seed_default_personalities()

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
    with _connection_lock:
        global _root_connection
        _root_connection = duckdb.connect(_DB_PATH)
    init_db()
