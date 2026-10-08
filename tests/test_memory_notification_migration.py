"""notifications / notification_links widen to allow a ``memory`` kind and link target.

A database from before agent memory notes has CHECKs without ``'memory'``.
The boot migration rebuilds each table, keeps every row, and the next
``memory`` note then inserts.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import db
from core import config
from db import connection as db_connection

_OLD_NOTIFICATIONS = """
CREATE TABLE notifications (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id          VARCHAR NOT NULL REFERENCES agents(id),
    task_id           VARCHAR REFERENCES tasks(id),
    activity_id       VARCHAR REFERENCES activities(id),
    kind              VARCHAR NOT NULL
                         CHECK (kind IN ('receipt', 'completion', 'blocked', 'handoff', 'abandoned', 'task_update', 'host_path_consent', 'cli_approval', 'queue_visibility')),
    content           TEXT NOT NULL,
    source_channel    VARCHAR NOT NULL,
    policy            VARCHAR NOT NULL
                         CHECK (policy IN ('none', 'completion_blocked', 'all')),
    chat_visible      BOOLEAN DEFAULT TRUE,
    prompt_visibility BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMP DEFAULT current_timestamp
)
"""

_OLD_LINKS = """
CREATE TABLE notification_links (
    notification_id VARCHAR PRIMARY KEY REFERENCES notifications(id),
    target_kind     VARCHAR NOT NULL
                       CHECK (target_kind IN ('desk', 'host_path_consent', 'cli_approval')),
    target_path     VARCHAR NOT NULL,
    label           VARCHAR NOT NULL DEFAULT 'Open in Desk',
    created_at      TIMESTAMP DEFAULT current_timestamp
)
"""


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


def _sql(sql: str, params: list | None = None):
    return db.get_connection().execute(sql, params or [])


def _table_sql(table: str) -> str:
    return str(_sql("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = $1", [table]).fetchone()[0])


def _has_index(name: str) -> bool:
    return _sql("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = $1", [name]).fetchone() is not None


def _to_pre_change_shape() -> None:
    """Rebuild both tables as they were before ``'memory'``, keeping their rows."""
    _sql("PRAGMA foreign_keys = OFF")
    try:
        for table, ddl in (("notifications", _OLD_NOTIFICATIONS), ("notification_links", _OLD_LINKS)):
            _sql(f"ALTER TABLE {table} RENAME TO {table}__old")
            _sql(ddl)
            _sql(f"INSERT INTO {table} SELECT * FROM {table}__old")
            _sql(f"DROP TABLE {table}__old")
    finally:
        _sql("PRAGMA foreign_keys = ON")
    assert "'memory'" not in _table_sql("notifications")
    assert "'memory'" not in _table_sql("notification_links")


def test_a_fresh_database_already_allows_memory() -> None:
    assert "'memory'" in _table_sql("notifications")
    assert "'memory'" in _table_sql("notification_links")


def test_an_old_database_is_rebuilt_keeping_its_rows_and_then_takes_a_memory_note() -> None:
    agent = db.create_agent("Tyler", role="Analyst")
    kept = db.create_notification(
        agent_id=agent.id, task_id=None, activity_id=None, kind="task_update",
        content="Tyler scheduled a check", source_channel="chat", policy="all",
        chat_visible=True, prompt_visibility=False,
    )
    db.create_notification_link(
        notification_id=kept.id, target_kind="desk", target_path="/me/report.md", label="open",
    )
    _to_pre_change_shape()
    with pytest.raises(sqlite3.IntegrityError):
        _sql(
            "INSERT INTO notifications (agent_id, kind, content, source_channel, policy) "
            "VALUES ($1, 'memory', 'refused', 'chat', 'all')",
            [agent.id],
        )

    db.close_connection()
    db.init_db()

    assert "'memory'" in _table_sql("notifications")
    assert "'memory'" in _table_sql("notification_links")
    assert _has_index("idx_notifications_created")
    rows = db.list_notifications(agent_id=agent.id, limit=10)
    assert [(row.id, row.kind, row.content, row.created_at) for row in rows] == [
        (kept.id, "task_update", "Tyler scheduled a check", kept.created_at)
    ]
    link = db.list_notification_links([kept.id])[kept.id]
    assert (link.target_kind, link.target_path, link.label) == ("desk", "/me/report.md", "open")

    note = db.create_notification(
        agent_id=agent.id, task_id=None, activity_id=None, kind="memory",
        content="Tyler saved a memory", source_channel="chat", policy="all",
        chat_visible=True, prompt_visibility=False,
    )
    db.create_notification_link(
        notification_id=note.id, target_kind="memory", target_path="4", label="Memory",
    )
    link = db.list_notification_links([note.id])[note.id]
    assert (note.kind, link.target_kind, link.target_path) == ("memory", "memory", "4")


def test_the_migration_is_idempotent() -> None:
    before = (_table_sql("notifications"), _table_sql("notification_links"))
    db_connection._ensure_memory_notification_schema(db.get_connection())
    assert (_table_sql("notifications"), _table_sql("notification_links")) == before
