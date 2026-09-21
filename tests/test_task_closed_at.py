"""A finished task carries the moment it finished, stamped once."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from api.routes.tasks import _serialize_listed_task
from core import config
from core.agent_loop.liveness import record_task_heartbeat
from core.models.message import HUMAN_SENDER_ID
from db.connection import _apply_migrations, get_connection


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


def _task(title: str = "Ship it"):
    return db.create_task(title=title, created_by=HUMAN_SENDER_ID)


def test_an_open_task_has_no_finish_time() -> None:
    task = _task()
    assert task.closed_at is None
    assert db.update_task(task.id, status="accepted").closed_at is None


def test_finishing_stamps_once_and_nothing_moves_it() -> None:
    task = _task()
    db.update_task(task.id, status="accepted")
    done = db.update_task(task.id, status="complete")
    assert done.closed_at is not None
    # The identity update and a late heartbeat both bump last_activity; neither
    # may move the finish time, or a finished task jumps back into "Today".
    db.update_task(task.id, status="complete")
    record_task_heartbeat(task.id)
    again = db.get_task(task.id)
    assert again.closed_at == done.closed_at
    assert again.last_activity >= done.closed_at


@pytest.mark.parametrize("status", ["cancelled", "abandoned", "declined"])
def test_every_way_of_ending_stamps(status: str) -> None:
    task = _task()
    assert db.update_task(task.id, status=status).closed_at is not None


def test_migration_backfills_finished_rows_from_last_activity() -> None:
    open_task = _task("open")
    done = _task("done")
    db.update_task(done.id, status="accepted")
    db.update_task(done.id, status="complete")
    con = get_connection()
    con.execute("UPDATE tasks SET closed_at = NULL")  # rows from before the column
    _apply_migrations(con)
    backfilled = db.get_task(done.id)
    assert backfilled.closed_at == backfilled.last_activity
    assert db.get_task(open_task.id).closed_at is None


def test_migration_adds_the_column_to_an_old_table() -> None:
    con = get_connection()
    con.execute("ALTER TABLE tasks DROP COLUMN closed_at")
    _apply_migrations(con)
    columns = {row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "closed_at" in columns


def test_the_list_route_serializes_closed_at() -> None:
    task = _task()
    db.update_task(task.id, status="cancelled")
    row = _serialize_listed_task(db.get_task(task.id), {}, None)
    assert row["closed_at"] is not None
