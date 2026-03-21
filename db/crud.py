"""BossMod AI — Hardened reusable CRUD query helpers.

All database access flows through these functions. They handle connection
acquisition, parameterized queries, row-to-dict conversion, and Pydantic
model validation in one place.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from db.connection import get_connection

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(
    description: list[tuple[str, ...]],
    row: tuple[Any, ...],
) -> dict[str, Any]:
    """Zip a cursor description with a row tuple into a dict."""
    return {col[0]: val for col, val in zip(description, row)}


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def fetch_one(sql: str, params: list[Any], model_cls: type[T]) -> T | None:
    """Execute SQL, fetch one row, validate as a Pydantic model.

    Returns ``None`` if no row is found.
    """
    con = get_connection()
    result = con.execute(sql, params)
    row = result.fetchone()
    if row is None:
        return None
    return model_cls.model_validate(_row_to_dict(result.description, row))


def fetch_all(
    sql: str,
    params: list[Any] | None = None,
    model_cls: type[T] = None,
) -> list[T]:
    """Execute SQL, fetch all rows, validate each as a Pydantic model."""
    con = get_connection()
    result = con.execute(sql, params or [])
    return [
        model_cls.model_validate(_row_to_dict(result.description, r))
        for r in result.fetchall()
    ]


def query_one(sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    """Execute any SELECT and return one row as a dict, or ``None``."""
    con = get_connection()
    result = con.execute(sql, params or [])
    row = result.fetchone()
    if row is None:
        return None
    return _row_to_dict(result.description, row)


def query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Execute any SELECT and return all rows as dicts.

    General-purpose read for custom JOINs, aggregates, and one-off queries.
    """
    con = get_connection()
    result = con.execute(sql, params or [])
    return [
        _row_to_dict(result.description, r)
        for r in result.fetchall()
    ]


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def execute(sql: str, params: list[Any] | None = None) -> None:
    """Execute a non-returning write (DELETE, raw UPDATE, etc.)."""
    con = get_connection()
    con.execute(sql, params or [])


def insert_returning(
    sql: str,
    params: list[Any],
    model_cls: type[T],
) -> T:
    """Execute INSERT ... RETURNING and validate the returned row as a model."""
    con = get_connection()
    result = con.execute(sql, params)
    row = result.fetchone()
    return model_cls.model_validate(_row_to_dict(result.description, row))


def insert_returning_dict(sql: str, params: list[Any]) -> dict[str, Any]:
    """Execute INSERT ... RETURNING and return the row as a dict."""
    con = get_connection()
    result = con.execute(sql, params)
    return _row_to_dict(result.description, result.fetchone())


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------

def build_update(
    table: str,
    id_column: str,
    id_value: str,
    fields: dict[str, Any],
    valid_columns: set[str],
) -> bool:
    """Hardened UPDATE: whitelist columns, parameterized SET clause.

    Returns ``True`` if fields were applied, ``False`` if nothing to update
    (all fields filtered out or empty).
    """
    filtered = {k: v for k, v in fields.items() if k in valid_columns}
    if not filtered:
        return False

    parts: list[str] = []
    values: list[Any] = []
    for key, value in filtered.items():
        parts.append(f"{key} = ${len(values) + 1}")
        values.append(value)

    values.append(id_value)
    idx = len(values)

    execute(
        f"UPDATE {table} SET {', '.join(parts)} WHERE {id_column} = ${idx}",
        values,
    )
    return True


def build_update_returning(
    table: str,
    id_column: str,
    id_value: str,
    fields: dict[str, Any],
    valid_columns: set[str],
    returning: str,
    model_cls: type[T],
) -> T | None:
    """Hardened UPDATE with RETURNING clause, validated as a Pydantic model."""
    filtered = {k: v for k, v in fields.items() if k in valid_columns}
    if not filtered:
        return None

    parts: list[str] = []
    values: list[Any] = []
    for key, value in filtered.items():
        parts.append(f"{key} = ${len(values) + 1}")
        values.append(value)

    values.append(id_value)
    idx = len(values)

    con = get_connection()
    result = con.execute(
        f"UPDATE {table} SET {', '.join(parts)} "
        f"WHERE {id_column} = ${idx} RETURNING {returning}",
        values,
    )
    row = result.fetchone()
    if row is None:
        return None
    return model_cls.model_validate(_row_to_dict(result.description, row))
