"""Soft blocked one-liners for wait-without-task and guardian no_progress."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from api.routes.needs import _blocked_needs
from core import config
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity, refresh_agent_status
from core.agent_loop.activity_scheduler import ensure_live_work_continuation, prepare_trigger_context
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.guardian import check_no_progress
from core.agent_loop.policies import TriggerPolicy
from core.agent_loop.soft_blocks import (
    NO_PROGRESS_LINE,
    WAITING_WITHOUT_TASK_CODE,
    WAITING_WITHOUT_TASK_LINE,
    apply_no_progress_block,
    clear_soft_block_for_live_work,
)
from core.agent_loop.turn_rules import validate_action_for_turn
from core.models.message import HUMAN_SENDER_ID
from core.tasking.board import build_task_board
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task


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


def _thread_task(
    *,
    assignee_id: str,
    channel_id: str,
    title: str = "Spec",
    requester_id: str = HUMAN_SENDER_ID,
):
    return create_or_bind_task(
        title=title,
        description="Author the spec.",
        project=None,
        assigned_to=assignee_id,
        requester_id=requester_id,
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
    expected = f"{gerry.name} {WAITING_WITHOUT_TASK_LINE}"
    lines = [
        item.content
        for item in db.list_channel_messages(channel.id)
        if item.author_type == "system" and (item.content or "").strip() == expected
    ]
    assert lines == [expected]


def test_no_progress_blocks_and_tags_next_owner() -> None:
    gerry = db.create_agent("Gerry", role="Implementation Spec Author", desk_x=1, desk_y=1)
    debra = db.create_agent("Debra", role="Requirements Analyst", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Gerry, Debra",
        member_agent_ids=[gerry.id, debra.id],
        created_by=HUMAN_SENDER_ID,
    )
    # Debra asked for the spec, so she is the next owner by precedence.
    creation = _thread_task(assignee_id=gerry.id, channel_id=channel.id, requester_id=debra.id)
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
        (item.content or "").startswith(f"{gerry.name} {NO_PROGRESS_LINE}")
        and "@Debra" in (item.content or "")
        for item in messages
    )


def test_no_progress_without_task_posts_blocked_line() -> None:
    gerry = db.create_agent("Gerry", role="Implementation Spec Author", desk_x=1, desk_y=1)
    result = apply_no_progress_block(gerry, {"type": "human_chat"})
    assert result["event"] == "status_changed"
    assert NO_PROGRESS_LINE in result["detail"]
    notes = db.list_notifications(agent_id=gerry.id, limit=8)
    assert any((note.content or "").startswith(f"{gerry.name} {NO_PROGRESS_LINE}") for note in notes)


def _live_blocked_task():
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    peer = db.create_agent("Bea", role="Reviewer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ada, Bea",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _thread_task(assignee_id=agent.id, channel_id=channel.id, title="Write notes")
    activate_work_activity(agent.id, creation.task)
    transition_task(
        creation.task.id,
        "blocked",
        reason="Blocked — no progress. @Bea",
        actor="BossMod",
        status_note="Blocked — no progress. @Bea",
    )
    return agent, creation.task


def _board_blocked_ids(agent_id: str) -> set[str]:
    board = build_task_board(agent_id, scope="self")
    return {task.id for task in board["sections"]["my_blocked_tasks"]}


def _need_blocked_ids() -> set[str]:
    return {item["id"] for item in _blocked_needs({})}


def test_live_work_clears_sticky_soft_block_on_refresh() -> None:
    agent, task = _live_blocked_task()
    assert db.get_task(task.id).status == "blocked"
    assert task.id in _board_blocked_ids(agent.id)
    assert task.id in _need_blocked_ids()

    state = refresh_agent_status(agent.id)
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert state is not None
    assert state.status == "work_active"
    assert task.id not in _board_blocked_ids(agent.id)
    assert task.id not in _need_blocked_ids()


def test_activity_resumed_while_working_demotes_soft_block() -> None:
    agent, task = _live_blocked_task()
    prepare_trigger_context(agent.id, {"type": "activity_resumed", "task_id": task.id})
    assert db.get_task(task.id).status == "active"
    assert task.id not in _board_blocked_ids(agent.id)


def test_status_reply_does_not_leave_sticky_block_when_work_is_live() -> None:
    agent, task = _live_blocked_task()
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "I'm on it next. Not waiting on anyone.",
        },
        agent,
        state,
        {"type": "human_chat", "content": "Status?", "from_name": "Human Operator"},
    )
    assert result["event"] == "decision_applied"
    assert db.get_task(task.id).status == "active"
    assert task.id not in _board_blocked_ids(agent.id)
    assert task.id not in _need_blocked_ids()
    # The decision no longer queues the resume itself; the dispatcher's
    # continuation invariant does, once the turn ends.
    assert not any(item.get("trigger_type") == "activity_resumed" for item in result["trigger_requests"])
    spec = ensure_live_work_continuation(agent.id)
    assert spec is not None and spec["task_id"] == task.id


def test_status_reply_resumes_paused_no_progress_soft_block() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    peer = db.create_agent("Bea", role="Reviewer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ada, Bea",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _thread_task(assignee_id=agent.id, channel_id=channel.id, title="Write notes")
    activate_work_activity(agent.id, creation.task)
    apply_no_progress_block(agent, {"type": "channel_response", "channel_id": channel.id})
    assert db.get_task(creation.task.id).status == "blocked"
    assert db.get_active_activity(agent.id) is None
    assert creation.task.id in _need_blocked_ids()

    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "I'm on it next. Not waiting on anyone.",
        },
        agent,
        state,
        {"type": "human_chat", "content": "Status?", "from_name": "Human Operator"},
    )
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    live = db.get_active_activity(agent.id)
    assert live is not None
    assert live.kind == "work"
    assert live.task_id == creation.task.id
    assert creation.task.id not in _board_blocked_ids(agent.id)
    assert not any(item.get("trigger_type") == "activity_resumed" for item in result["trigger_requests"])
    spec = ensure_live_work_continuation(agent.id)
    assert spec is not None and spec["task_id"] == creation.task.id


def test_soft_block_stays_when_work_is_not_live() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    peer = db.create_agent("Bea", role="Reviewer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ada, Bea",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = _thread_task(assignee_id=agent.id, channel_id=channel.id)
    activate_work_activity(agent.id, creation.task)
    apply_no_progress_block(agent, {"type": "channel_response", "channel_id": channel.id})
    assert clear_soft_block_for_live_work(agent.id) is None
    state = refresh_agent_status(agent.id)
    assert db.get_task(creation.task.id).status == "blocked"
    assert state is not None
    assert state.status == "blocked"
    assert creation.task.id in _board_blocked_ids(agent.id)
    needs = _blocked_needs({})
    match = next(item for item in needs if item["id"] == creation.task.id)
    assert match["kind"] == "blocked"
    assert "blocked" in match["title"].lower()
