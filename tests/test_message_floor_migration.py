"""messages.floor_id: added once, backfilled once, in one transaction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.models.message import HUMAN_SENDER_ID
from db import connection as db_connection
from db.floors import LOBBY_ID, create_floor

INDEX = "idx_messages_floor_created"


def _fresh_db() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def setup_function() -> None:
    _fresh_db()


def teardown_function() -> None:
    db.close_connection()


def _sql(sql: str, params: list | None = None):
    return db.get_connection().execute(sql, params or [])


def _columns() -> set[str]:
    return {row[1] for row in _sql("PRAGMA table_info(messages)").fetchall()}


def _has_index() -> bool:
    return _sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = $1", [INDEX],
    ).fetchone() is not None


def _to_pre_change_shape() -> None:
    """Make `messages` look as it did before this change: no floor_id."""
    _sql(f"DROP INDEX IF EXISTS {INDEX}")
    _sql("ALTER TABLE messages DROP COLUMN floor_id")
    assert "floor_id" not in _columns()


def _old_row(from_agent: str, to_agent: str | None, content: str) -> str:
    """Insert a row the way the pre-change code did, with no floor."""
    return _sql(
        "INSERT INTO messages (from_agent, to_agent, content, message_type) "
        "VALUES ($1, $2, $3, 'social') RETURNING id",
        [from_agent, to_agent, content],
    ).fetchone()[0]


def _floor_of(message_id: str) -> str | None:
    return _sql("SELECT floor_id FROM messages WHERE id = $1", [message_id]).fetchone()[0]


def _reboot() -> None:
    db.close_connection()
    db.init_db()


def _send_on_vacation(agent_id: str) -> None:
    _sql(
        "UPDATE agents SET floor_id = NULL, vacation_since = current_timestamp WHERE id = $1",
        [agent_id],
    )


def test_a_fresh_database_has_the_column_and_the_index() -> None:
    assert "floor_id" in _columns()
    assert _has_index()


def test_the_backfill_files_each_old_row_under_the_senders_else_the_recipients_floor() -> None:
    finance = create_floor("Finance")
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    bob = db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    cy = db.create_agent("Cy", role="Eng", desk_x=3, desk_y=1, floor_id=finance.id)
    vic = db.create_agent("Vic", role="Eng", desk_x=4, desk_y=1)
    wes = db.create_agent("Wes", role="Eng", desk_x=5, desk_y=1)
    _to_pre_change_shape()
    _send_on_vacation(vic.id)
    _send_on_vacation(wes.id)

    shared = _old_row(ada.id, bob.id, "same floor")
    split = _old_row(cy.id, ada.id, "different floors")
    from_vacation = _old_row(vic.id, bob.id, "sender on vacation")
    from_deleted = _old_row("agent-that-was-deleted", cy.id, "sender deleted")
    nowhere = _old_row(vic.id, wes.id, "neither has a floor")
    to_human = _old_row(ada.id, HUMAN_SENDER_ID, "to the operator")
    from_human = _old_row(HUMAN_SENDER_ID, ada.id, "from the operator")
    work = _old_row(ada.id, None, "work output")

    _reboot()

    assert "floor_id" in _columns()
    assert _has_index()
    assert _floor_of(shared) == LOBBY_ID
    assert _floor_of(split) == finance.id
    assert _floor_of(from_vacation) == LOBBY_ID
    assert _floor_of(from_deleted) == finance.id
    assert _floor_of(nowhere) is None
    assert _floor_of(to_human) is None
    assert _floor_of(from_human) is None
    assert _floor_of(work) is None


def test_the_backfill_runs_once_and_never_rewrites_a_stamped_row() -> None:
    finance = create_floor("Finance")
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    bob = db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    _to_pre_change_shape()
    reset_later = _old_row(ada.id, bob.id, "stamped, then cleared")
    kept = _old_row(bob.id, ada.id, "stamped and kept")
    _reboot()
    assert _floor_of(reset_later) == LOBBY_ID
    assert _floor_of(kept) == LOBBY_ID

    # A second boot finds the column and backfills nothing: a NULL stays NULL,
    # and a stamped row is not refiled after its sender moves.
    _sql("UPDATE messages SET floor_id = NULL WHERE id = $1", [reset_later])
    floorless = _old_row(ada.id, bob.id, "written with no floor after the migration")
    db.update_agent(bob.id, floor_id=finance.id)
    _reboot()

    assert _floor_of(reset_later) is None
    assert _floor_of(floorless) is None
    assert _floor_of(kept) == LOBBY_ID
    assert _has_index()


def test_a_failed_backfill_leaves_no_column_so_the_next_boot_retries(monkeypatch) -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    bob = db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    _to_pre_change_shape()
    row = _old_row(ada.id, bob.id, "waiting for a floor")

    real_execute = db_connection.SQLiteCompatConnection.execute

    def failing_execute(self, sql, params=None):
        if "UPDATE messages SET floor_id" in sql:
            raise RuntimeError("backfill exploded")
        return real_execute(self, sql, params)

    monkeypatch.setattr(db_connection.SQLiteCompatConnection, "execute", failing_execute)
    db.close_connection()
    with pytest.raises(RuntimeError, match="backfill exploded"):
        db.init_db()
    assert "floor_id" not in _columns()
    assert not _has_index()

    monkeypatch.setattr(db_connection.SQLiteCompatConnection, "execute", real_execute)
    _reboot()
    assert "floor_id" in _columns()
    assert _floor_of(row) == LOBBY_ID
