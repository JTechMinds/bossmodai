"""Soft blocked one-liners for wait-without-task and guardian no_progress."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.guardian import check_no_progress
from core.agent_loop.policies import TriggerPolicy
from core.agent_loop.soft_blocks import (
    NO_PROGRESS_LINE,
    WAITING_WITHOUT_TASK_CODE,
    WAITING_WITHOUT_TASK_LINE,
    apply_no_progress_block,
)
from core.agent_loop.turn_rules import validate_action_for_turn
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


def _thread_task(*, assignee_id: str, channel_id: str, title: str = "Spec"):
    return create_or_bind_task(
        title=title,
        description="Author the spec.",
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


def test_waiting_without_task_is_not_a_turn_validation_error() -> None:
    policy = TriggerPolicy(trigger_type="channel_response")
    error = validate_action_for_turn(
        {"action": "waiting", "reason": "Need the spec."},
        policy,
        None,
        None,
    )
    assert error is None


@pytest.mark.asyncio
async def test_waiting_without_task_is_a_soft_blocked_one_liner() -> None:
    gerry = db.create_agent("Gerry", role="Implementation Spec Author", desk_x=1, desk_y=1)
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Gerry, Debra, Jim",
        member_agent_ids=[gerry.id, debra.id],
        created_by=HUMAN_SENDER_ID,
    )
    state = db.get_agent_state(gerry.id)
    assert state is not None

    first = await execute_action(
        {"action": "waiting", "reason": "Need an active task."},
        gerry,
        state,
        trigger={"type": "channel_response", "channel_id": channel.id},
    )
    second = await execute_action(
        {"action": "waiting", "reason": "Need an active task."},
        gerry,
        state,
        trigger={"type": "channel_response", "channel_id": channel.id},
    )
    assert first["event"] == "world_feedback"
    assert first["feedback_code"] == WAITING_WITHOUT_TASK_CODE
    assert first["detail"] == WAITING_WITHOUT_TASK_LINE
    assert second["event"] == "world_feedback"
    lines = [
        item.content
        for item in db.list_channel_messages(channel.id)
        if item.author_type == "system" and (item.content or "").strip() == WAITING_WITHOUT_TASK_LINE
    ]
    assert lines == [WAITING_WITHOUT_TASK_LINE]


def test_no_progress_blocks_and_tags_next_owner() -> None:
    gerry = db.create_agent("Gerry", role="Implementation Spec Author", desk_x=1, desk_y=1)
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Gerry, Debra",
        member_agent_ids=[gerry.id, debra.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _thread_task(assignee_id=gerry.id, channel_id=channel.id)
    activate_work_activity(gerry.id, creation.task)
    gerry.guardian_no_progress_threshold = 2
    assert check_no_progress(gerry, 1) is None
    violation = check_no_progress(gerry, 2)
    assert violation is not None
    assert violation.rule == "no_progress"

    result = apply_no_progress_block(
        gerry,
        {"type": "channel_response", "channel_id": channel.id},
    )
    assert result["event"] == "status_changed"
    assert result["feedback_code"] == "no_progress_block"
    assert NO_PROGRESS_LINE in result["detail"]
    assert "@Debra" in result["detail"]
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "blocked"
    messages = db.list_channel_messages(channel.id)
    assert any(
        NO_PROGRESS_LINE in (item.content or "") and "@Debra" in (item.content or "")
        for item in messages
    )


def test_no_progress_without_task_posts_blocked_line() -> None:
    gerry = db.create_agent("Gerry", role="Implementation Spec Author", desk_x=1, desk_y=1)
    result = apply_no_progress_block(gerry, {"type": "human_chat"})
    assert result["event"] == "status_changed"
    assert NO_PROGRESS_LINE in result["detail"]
    notes = db.list_notifications(agent_id=gerry.id, limit=8)
    assert any(NO_PROGRESS_LINE in (note.content or "") for note in notes)
