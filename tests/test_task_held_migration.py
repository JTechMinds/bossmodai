"""The ``held`` task status migration: tasks is rebuilt from schema.sql, every value kept.

SQLite cannot add ``'held'`` to the ``tasks.status`` CHECK in place, so
``_ensure_task_status_values`` rebuilds the table from the ``tasks``
statement in schema.sql and copies every column the old and new tables share.
The old rebuild copied a hard-coded, stale column list; this one must keep the
late columns (``closed_at``, ``schedule_id``, ``severity``,
``origin_attachment_ids``) and run once. Against the isolated test database
(conftest.py) only.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

import db
from core import config
from db.connection import _schema_tasks_ddl, _table_sql, get_connection

_ROW_ID = "task-from-before-held"
_LATE_VALUES = {
    "closed_at": "2026-10-01 09:30:00+00:00",
    "schedule_id": "schedule-weekly",
    "severity": "P1",
    "origin_attachment_ids": '["att-1", "att-2"]',
}


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _table_info(table: str) -> list[tuple]:
    return [tuple(row) for row in get_connection().execute(f"PRAGMA table_info({table})").fetchall()]


def _rewind_to_before_held(*, drop_lines: tuple[str, ...] = ()) -> None:
    """Make this database look like one from before ``held``: old CHECK, no edge table.

    ``drop_lines`` removes column lines from the old table too, for a
    database older than those columns.
    """
    ddl = _schema_tasks_ddl()
    assert ddl.count("'pending', 'held', ") == 1
    old = ddl.replace("'pending', 'held', ", "'pending', ").replace(
        "CREATE TABLE IF NOT EXISTS tasks (", "CREATE TABLE tasks__old (", 1,
    )
    lines = [line for line in old.split("\n") if not (drop_lines and line.strip().startswith(drop_lines))]
    con = get_connection()
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("DROP TABLE task_dependencies")
        con.execute("\n".join(lines))
        con.execute("DROP TABLE tasks")
        con.execute("ALTER TABLE tasks__old RENAME TO tasks")
    finally:
        con.execute("PRAGMA foreign_keys = ON")
    assert "'held'" not in _table_sql(con, "tasks")


def _insert_old_row(columns: dict[str, str]) -> None:
    names = ", ".join(columns)
    marks = ", ".join(f"${index + 1}" for index in range(len(columns)))
    get_connection().execute(f"INSERT INTO tasks ({names}) VALUES ({marks})", list(columns.values()))


def _stored_row() -> dict:
    row = db.query_one(
        "SELECT title, status, closed_at, schedule_id, severity, origin_attachment_ids FROM tasks WHERE id = $1",
        [_ROW_ID],
    )
    assert row is not None
    return row


def test_the_rebuild_keeps_every_late_column_and_matches_a_fresh_table(caplog: pytest.LogCaptureFixture) -> None:
    fresh_columns = _table_info("tasks")
    _rewind_to_before_held()
    _insert_old_row({"id": _ROW_ID, "title": "Old report", "status": "cancelled", **_LATE_VALUES})

    with caplog.at_level(logging.INFO, logger="db.connection"):
        db.init_db()

    con = get_connection()
    assert "'held'" in _table_sql(con, "tasks")
    assert "Migration: rebuilt tasks to add the held status" in caplog.text
    assert _table_info("tasks") == fresh_columns
    row = _stored_row()
    assert (row["title"], row["status"]) == ("Old report", "cancelled")
    assert str(row["closed_at"]) == "2026-10-01 09:30:00+00:00"
    assert (row["schedule_id"], row["severity"], row["origin_attachment_ids"]) == (
        "schedule-weekly", "P1", '["att-1", "att-2"]',
    )
    # The edge table and the hot-path indexes the drop took are back.
    assert _table_sql(con, "task_dependencies")
    indexes = {
        str(item[0]) for item in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    assert {"idx_tasks_status", "idx_tasks_assigned_status", "idx_tasks_owner_status"} <= indexes
    # Other tables still reference tasks, not a renamed-away copy.
    assert "REFERENCES tasks(id)" in _table_sql(con, "task_work_contracts")
    # The new status is storable.
    assert db.update_task(_ROW_ID, status_note="still readable") is not None
    held = db.create_task("New work", created_by="pytest")
    con.execute("UPDATE tasks SET status = 'held' WHERE id = ?", [held.id])
    assert db.get_task(held.id).status == "held"


def test_a_second_boot_is_a_no_op(caplog: pytest.LogCaptureFixture) -> None:
    _rewind_to_before_held()
    _insert_old_row({"id": _ROW_ID, "title": "Old report", "status": "complete", **_LATE_VALUES})
    db.init_db()
    ddl = _table_sql(get_connection(), "tasks")
    before = _stored_row()

    with caplog.at_level(logging.INFO, logger="db.connection"):
        db.init_db()

    assert "rebuilt tasks" not in caplog.text
    assert _table_sql(get_connection(), "tasks") == ddl
    assert _stored_row() == before


def test_a_database_older_than_the_late_columns_gets_their_defaults() -> None:
    fresh_columns = _table_info("tasks")
    _rewind_to_before_held(drop_lines=("severity", "origin_attachment_ids", "-- P0 critical"))
    _insert_old_row({"id": _ROW_ID, "title": "Ancient report", "status": "pending"})

    db.init_db()

    assert _table_info("tasks") == fresh_columns
    row = _stored_row()
    assert (row["title"], row["severity"], row["origin_attachment_ids"]) == ("Ancient report", "P3", None)
