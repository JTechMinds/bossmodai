"""BossMod AI — DuckDB connection management.

Provides a single-writer connection to ``bossmod.db`` at the project root
and auto-initialises the schema on first access.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import duckdb

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = str(_PROJECT_ROOT / "bossmod.db")
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

_connection: duckdb.DuckDBPyConnection | None = None


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return the module-level DuckDB connection, creating it on first call.

    Includes a lightweight health check — if the existing connection is broken
    it is replaced with a fresh one.
    """
    global _connection
    if _connection is not None:
        try:
            _connection.execute("SELECT 1")
        except Exception:
            logger.warning("DuckDB connection health check failed — reconnecting")
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None
    if _connection is None:
        _connection = duckdb.connect(_DB_PATH)
        _apply_schema(_connection)
        logger.info("DuckDB connection opened: %s", _DB_PATH)
    return _connection


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


def close_connection() -> None:
    """Close the module-level connection (e.g. during shutdown)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("DuckDB connection closed")


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
    global _connection
    close_connection()
    db_path = Path(_DB_PATH)
    if db_path.exists():
        db_path.unlink()
    _connection = duckdb.connect(_DB_PATH)
    init_db()
