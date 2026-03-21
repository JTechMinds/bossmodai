"""BossMod AI — DuckDB connection management.

Provides a single-writer connection to ``bossmod.db`` at the project root
and auto-initialises the schema on first access.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = str(_PROJECT_ROOT / "bossmod.db")
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

_connection: duckdb.DuckDBPyConnection | None = None


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return the module-level DuckDB connection, creating it on first call."""
    global _connection
    if _connection is None:
        _connection = duckdb.connect(_DB_PATH)
        _apply_schema(_connection)
        logger.info("DuckDB connection opened: %s", _DB_PATH)
    return _connection


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

    from db.settings import seed_defaults
    seed_defaults()

    from db.ai_personalities import seed_default_personalities
    seed_default_personalities()

    logger.info("Database initialised")
