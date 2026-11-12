"""The boss's DM line for a checklist check-off (core/agent_loop/checklist_notices.py).

A check-off is an operator-only ``checklist`` notification: the operator's
DM transcript and notifications list show it, and no agent-facing read of
``db.list_notifications`` ever returns it (the communication snapshot, the
prompt notifications). A database from before the kind existed is rebuilt
at boot keeping every row. Everything runs against the isolated test
database (conftest.py). No LLM.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.checklist_notices import checklist_change_line, note_checklist_change
from core.agent_loop.communication import build_communication_snapshot
from core.agent_loop.prompt_history import _load_prompt_notifications
from core.models import Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.models.notification import OPERATOR_ONLY_NOTIFICATION_KINDS
from core.models.task import TaskChecklistItem
from db import connection as db_connection

# The notifications table as it was before ``'checklist'`` (after ``'memory'``).
_OLD_NOTIFICATIONS = """
CREATE TABLE notifications (
    id                VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    agent_id          VARCHAR NOT NULL REFERENCES agents(id),
    task_id           VARCHAR REFERENCES tasks(id),
    activity_id       VARCHAR REFERENCES activities(id),
    kind              VARCHAR NOT NULL
                         CHECK (kind IN ('receipt', 'completion', 'blocked', 'handoff', 'abandoned', 'task_update', 'host_path_consent', 'cli_approval', 'queue_visibility', 'memory')),
    content           TEXT NOT NULL,
    source_channel    VARCHAR NOT NULL,
    policy            VARCHAR NOT NULL
                         CHECK (policy IN ('none', 'completion_blocked', 'all')),
    chat_visible      BOOLEAN DEFAULT TRUE,
    prompt_visibility BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMP DEFAULT current_timestamp
)
"""

_AUDIT = [
    TaskChecklistItem(text="Audit core/tasking", status="done", note="2 issues"),
    TaskChecklistItem(text="Audit db/tasks.py", status="done", note="3 issues, in /projects/app/audit.md"),
    TaskChecklistItem(text="Audit the plugins", status="skipped", note="the boss dropped them"),
    TaskChecklistItem(text="Audit the UI", status="done", note=None),
    TaskChecklistItem(text="Write the summary", status="open", note=None),
]


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


def _ava() -> Agent:
    return db.create_agent("Ava", role="Auditor")


def _task(agent: Agent, title: str = "Backend audit") -> Task:
    return db.create_task(
        title=title,
        description="Audit the backend.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        created_by=HUMAN_SENDER_ID,
    )


def _note(agent: Agent, *, kind: str, content: str, task_id: str | None, prompt_visibility: bool = False):
    return db.create_notification(
        agent_id=agent.id, task_id=task_id, activity_id=None, kind=kind, content=content,
        source_channel="task", policy="all", chat_visible=True, prompt_visibility=prompt_visibility,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


# ─── the line ───


def test_checklist_is_the_only_operator_only_kind() -> None:
    assert OPERATOR_ONLY_NOTIFICATION_KINDS == frozenset({"checklist"})


def test_each_resolution_has_its_sentence_verbatim() -> None:
    ava = _ava()
    task = _task(ava)
    assert checklist_change_line(ava, task, 2, _AUDIT) == (
        'Ava checked off item 2 of 5 on "Backend audit": Audit db/tasks.py — 3 issues, in /projects/app/audit.md'
    )
    assert checklist_change_line(ava, task, 4, _AUDIT) == 'Ava checked off item 4 of 5 on "Backend audit": Audit the UI'
    assert checklist_change_line(ava, task, 3, _AUDIT) == (
        'Ava skipped item 3 of 5 on "Backend audit": Audit the plugins — the boss dropped them'
    )
    # No truncation: a long note is posted whole.
    long_note = "found " + "x" * 2000
    items = [TaskChecklistItem(text="Audit core", status="done", note=long_note)]
    assert checklist_change_line(ava, task, 1, items).endswith(f"Audit core — {long_note}")


def test_an_open_item_or_a_number_past_the_end_is_a_caller_bug() -> None:
    ava = _ava()
    task = _task(ava)
    with pytest.raises(ValueError, match="only a done or skipped item"):
        checklist_change_line(ava, task, 5, _AUDIT)
    for number in (0, 6):
        with pytest.raises(IndexError):
            checklist_change_line(ava, task, number, _AUDIT)
    with pytest.raises(IndexError):
        note_checklist_change(ava, task, 0, _AUDIT)
    assert db.list_notifications(agent_id=ava.id, include_operator_only=True) == []


def test_the_note_persists_one_operator_only_row_in_the_assignees_dm() -> None:
    ava = _ava()
    task = _task(ava)
    posted = note_checklist_change(ava, task, 2, _AUDIT)["chat_message"]
    line = 'Ava checked off item 2 of 5 on "Backend audit": Audit db/tasks.py — 3 issues, in /projects/app/audit.md'
    assert (posted["agent_id"], posted["content"], posted["notification_kind"], posted["task_id"]) == (
        ava.id, line, "checklist", task.id,
    )
    assert posted["from_type"] == "system"
    (row,) = db.list_notifications(agent_id=ava.id, include_operator_only=True)
    assert (row.id, row.kind, row.content, row.task_id) == (posted["message_id"], "checklist", line, task.id)
    assert (row.source_channel, row.policy, row.chat_visible, row.prompt_visibility) == ("task", "all", True, False)
    # A repeated check-off is a real action: its own line, no dedupe.
    note_checklist_change(ava, task, 2, _AUDIT)
    assert [r.content for r in db.list_notifications(agent_id=ava.id, include_operator_only=True)] == [line, line]


# ─── never in an agent's context ───


def test_list_notifications_leaves_check_offs_out_unless_asked() -> None:
    ava = _ava()
    task = _task(ava)
    kept = _note(ava, kind="task_update", content="Ava Accepted: Backend audit", task_id=task.id)
    check_off = note_checklist_change(ava, task, 1, _AUDIT)["chat_message"]
    assert [row.id for row in db.list_notifications(agent_id=ava.id)] == [kept.id]
    assert [row.id for row in db.list_notifications(agent_id=ava.id, chat_visible=True)] == [kept.id]
    both = {row.id for row in db.list_notifications(agent_id=ava.id, include_operator_only=True)}
    assert both == {kept.id, check_off["message_id"]}


def test_check_offs_never_push_a_done_line_out_of_the_communication_snapshot() -> None:
    ava = _ava()
    task = _task(ava)
    done = _note(ava, kind="completion", content="Ava Done: Backend audit", task_id=task.id)
    # Older than every check-off, so without the filter the five newest rows would all be check-offs.
    _sql("UPDATE notifications SET created_at = $1 WHERE id = $2", ["2026-01-01 00:00:00", done.id])
    lines = [note_checklist_change(ava, task, number, _AUDIT)["chat_message"]["content"] for number in (1, 2, 3, 4, 1)]
    unfiltered = db.list_notifications(agent_id=ava.id, limit=5, include_operator_only=True)
    assert done.id not in {row.id for row in unfiltered}

    snapshot = build_communication_snapshot(
        agent=ava, state=db.get_agent_state(ava.id), trigger={"type": "human_chat", "content": "Status?"},
    )

    updates = snapshot["recent_task_updates"]
    assert [(item["kind"], item["content"]) for item in updates] == [("completion", "Ava Done: Backend audit")]
    assert not any(item["content"] in lines for item in updates)


def test_prompt_notifications_never_carry_a_check_off() -> None:
    ava = _ava()
    task = _task(ava)
    note_checklist_change(ava, task, 1, _AUDIT)
    # Even a check-off row marked prompt-visible is left out by its kind.
    _note(ava, kind="checklist", content="forced prompt-visible check-off", task_id=task.id, prompt_visibility=True)
    visible = _note(ava, kind="task_update", content="Ava Accepted: Backend audit", task_id=task.id,
                    prompt_visibility=True)
    policy = db.get_agent_prompt_history_policy(ava.id)
    assert policy is not None

    rows = _load_prompt_notifications(ava.id, policy)

    assert [row.id for row in rows] == [visible.id]


# ─── operator views ───


def test_the_dm_transcript_and_the_notifications_list_show_check_offs() -> None:
    ava = _ava()
    task = _task(ava)
    line = note_checklist_change(ava, task, 3, _AUDIT)["chat_message"]["content"]
    client = _client()

    messages = client.get(f"/api/agents/{ava.id}/messages")
    assert messages.status_code == 200
    (row,) = [item for item in messages.json() if item["content"] == line]
    assert (row["from"], row["notification_kind"], row["task_id"]) == ("system", "checklist", task.id)

    notifications = client.get(f"/api/agents/{ava.id}/notifications")
    assert notifications.status_code == 200
    assert [(item["kind"], item["content"], item["task_id"]) for item in notifications.json()] == [
        ("checklist", line, task.id),
    ]


# ─── the migration ───


def test_a_fresh_database_accepts_a_check_off_row() -> None:
    assert "'checklist'" in _table_sql("notifications")
    ava = _ava()
    row = _note(ava, kind="checklist", content="Ava checked off item 1 of 1", task_id=None)
    assert row.kind == "checklist"


def test_an_old_database_is_rebuilt_keeping_its_rows_and_index_and_then_takes_a_check_off() -> None:
    ava = _ava()
    task = _task(ava)
    kept = _note(ava, kind="memory", content="Ava saved a memory", task_id=None)
    kept_task = _note(ava, kind="task_update", content="Ava Accepted: Backend audit", task_id=task.id)
    db.create_notification_link(notification_id=kept.id, target_kind="memory", target_path="4", label="Memory")
    before = {
        row[0]: tuple(row)
        for row in _sql("SELECT id, agent_id, task_id, kind, content, source_channel, policy, chat_visible, "
                        "prompt_visibility, created_at FROM notifications").fetchall()
    }
    _sql("PRAGMA foreign_keys = OFF")
    try:
        _sql("ALTER TABLE notifications RENAME TO notifications__old")
        _sql(_OLD_NOTIFICATIONS)
        _sql("INSERT INTO notifications SELECT * FROM notifications__old")
        _sql("DROP TABLE notifications__old")
    finally:
        _sql("PRAGMA foreign_keys = ON")
    assert "'checklist'" not in _table_sql("notifications")
    assert not _has_index("idx_notifications_created")
    with pytest.raises(sqlite3.IntegrityError):
        _sql(
            "INSERT INTO notifications (agent_id, kind, content, source_channel, policy) "
            "VALUES ($1, 'checklist', 'refused', 'task', 'all')",
            [ava.id],
        )

    db.close_connection()
    db.init_db()

    assert "'checklist'" in _table_sql("notifications")
    assert _has_index("idx_notifications_created")
    after = {
        row[0]: tuple(row)
        for row in _sql("SELECT id, agent_id, task_id, kind, content, source_channel, policy, chat_visible, "
                        "prompt_visibility, created_at FROM notifications").fetchall()
    }
    assert after == before
    assert set(after) == {kept.id, kept_task.id}
    link = db.list_notification_links([kept.id])[kept.id]
    assert (link.target_kind, link.target_path) == ("memory", "4")
    posted = note_checklist_change(ava, task, 1, _AUDIT)["chat_message"]
    stored = {row.id: row.kind for row in db.list_notifications(agent_id=ava.id, include_operator_only=True)}
    assert stored[posted["message_id"]] == "checklist"


def test_the_migration_is_idempotent() -> None:
    ava = _ava()
    kept = _note(ava, kind="task_update", content="Ava Accepted: Backend audit", task_id=None)
    before = _table_sql("notifications")
    db_connection._ensure_checklist_notification_kind(db.get_connection())
    db.close_connection()
    db.init_db()
    assert _table_sql("notifications") == before
    assert [row.id for row in db.list_notifications(agent_id=ava.id)] == [kept.id]
