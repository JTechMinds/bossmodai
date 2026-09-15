"""Shared-thread handoff: peer-invisible /me Done is rejected or steered."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.runtime_core import format_runtime_core_block
from core.agent_loop.shared_handoff import (
    PEER_INVISIBLE_HANDOFF_LINE,
    PEER_INVISIBLE_HANDOFF_MESSAGE,
    is_peer_invisible_path,
    is_shared_thread_origin,
)
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task


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


def _write_virtual(storage_key: str, virtual_path: str, content: str) -> str:
    resolved = resolve_cli_path(storage_key, "/", virtual_path)
    assert resolved.real_path is not None
    resolved.real_path.parent.mkdir(parents=True, exist_ok=True)
    resolved.real_path.write_text(content, encoding="utf-8")
    return resolved.virtual_path


def _thread_task(*, assignee_id: str, channel_id: str, title: str = "Requirements note"):
    return create_or_bind_task(
        title=title,
        description="Write requirements peers can open.",
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def _chat_task(*, assignee_id: str, title: str = "Private draft"):
    return create_or_bind_task(
        title=title,
        description="A 1:1 Focus note.",
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="chat",
        notification_policy="completion_blocked",
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def test_me_paths_are_peer_invisible() -> None:
    assert is_peer_invisible_path("/me/requirements-llm-helper-bugfix.md") is True
    assert is_peer_invisible_path("/me") is True
    assert is_peer_invisible_path("/projects/shared/note.md") is False
    assert is_peer_invisible_path("/home/jordan/Desktop/Projects/llm_helper/docs/note.md") is False


def test_runtime_core_steers_thread_origin_handoff_off_me() -> None:
    agent = db.create_agent("Debra", role="Requirements Analyst", desk_x=1, desk_y=1)
    block = format_runtime_core_block(agent)
    assert "Thread-origin work" in block
    assert "peers can open" in block
    assert "/me is desk-private scratch, not a handoff" in block


@pytest.mark.asyncio
async def test_thread_origin_done_at_me_is_rejected() -> None:
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=1, desk_y=1)
    jim = db.create_agent("Jim", role="Engineer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Gerry, Debra, Jim",
        member_agent_ids=[debra.id, jim.id],
        created_by=HUMAN_SENDER_ID,
    )
    state = db.get_agent_state(debra.id)
    assert state is not None
    path = _write_virtual(debra.storage_key, "/me/requirements-note.md", "criteria")
    creation = _thread_task(assignee_id=debra.id, channel_id=channel.id)
    assert is_shared_thread_origin(creation.task) is True
    activate_work_activity(debra.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Requirements are ready.",
            "doneClaim": {"type": "artifact", "path": path},
            "followUpMessage": "Requirements note is ready.",
        },
        debra,
        state,
    )
    assert result["event"] == "world_feedback"
    assert result["detail"] == PEER_INVISIBLE_HANDOFF_MESSAGE
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    messages = db.list_channel_messages(channel.id)
    assert any(PEER_INVISIBLE_HANDOFF_LINE in (item.content or "") for item in messages)


@pytest.mark.asyncio
async def test_thread_origin_done_at_projects_succeeds() -> None:
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=1, desk_y=1)
    jim = db.create_agent("Jim", role="Engineer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Shared",
        member_agent_ids=[debra.id, jim.id],
        created_by=HUMAN_SENDER_ID,
    )
    state = db.get_agent_state(debra.id)
    assert state is not None
    path = _write_virtual(debra.storage_key, "/projects/shared/requirements-note.md", "criteria")
    creation = _thread_task(assignee_id=debra.id, channel_id=channel.id)
    activate_work_activity(debra.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Requirements are ready.",
            "doneClaim": {"type": "artifact", "path": path},
            "followUpMessage": "Requirements note is ready.",
        },
        debra,
        state,
    )
    assert result["event"] == "status_changed"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


@pytest.mark.asyncio
async def test_thread_origin_done_at_host_grant_succeeds(tmp_path: Path) -> None:
    host = tmp_path / "llm_helper"
    docs = host / "docs"
    docs.mkdir(parents=True)
    note = docs / "requirements-note.md"
    note.write_text("criteria\n", encoding="utf-8")
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()

    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=1, desk_y=1)
    jim = db.create_agent("Jim", role="Engineer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Shared",
        member_agent_ids=[debra.id, jim.id],
        created_by=HUMAN_SENDER_ID,
    )
    state = db.get_agent_state(debra.id)
    assert state is not None
    creation = _thread_task(assignee_id=debra.id, channel_id=channel.id)
    activate_work_activity(debra.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Requirements landed on the shared grant.",
            "doneClaim": {"type": "artifact", "path": str(note)},
            "followUpMessage": "Requirements note is on the host docs path.",
        },
        debra,
        state,
    )
    assert result["event"] == "status_changed"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


@pytest.mark.asyncio
async def test_focus_origin_done_at_me_still_succeeds() -> None:
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=1, desk_y=1)
    state = db.get_agent_state(debra.id)
    assert state is not None
    path = _write_virtual(debra.storage_key, "/me/private-draft.md", "scratch")
    creation = _chat_task(assignee_id=debra.id)
    assert is_shared_thread_origin(creation.task) is False
    activate_work_activity(debra.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Private draft is ready.",
            "doneClaim": {"type": "artifact", "path": path},
            "followUpMessage": "Draft is in Focus.",
        },
        debra,
        state,
    )
    assert result["event"] == "status_changed"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"
