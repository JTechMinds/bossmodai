"""Complete Board tasks must not crash wake/pause/activate paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.activity_runtime import (
    TERMINAL_WAKE_FEEDBACK_CODE,
    TERMINAL_WAKE_LINE,
    activate_work_activity,
    get_active_work_activity,
    pause_active_work,
    transition_live_work_task,
)
from core.agent_loop.activity_scheduler import prepare_trigger_context
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.dispatcher import TurnDispatcher
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import IllegalTaskTransition, transition_task


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


def _new_assigned_task(agent_id: str, *, title: str = "Ship notes"):
    return create_or_bind_task(
        title=title,
        description="Write the notes.",
        project=None,
        assigned_to=agent_id,
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
    ).task


def _complete_board_leave_activity(agent_id: str, task_id: str) -> None:
    """Mark the Board task complete while leaving the work activity active."""
    transition_task(task_id, "complete", reason="finished", actor="pytest")
    active = db.get_active_activity(agent_id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == task_id
    assert db.get_task(task_id).status == "complete"


def test_pause_active_work_noops_complete_task_status() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    task = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, task)
    _complete_board_leave_activity(agent.id, task.id)

    paused = pause_active_work(agent.id, "Paused for newer work.")
    assert paused is None
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"
    assert get_active_work_activity(agent.id) is None
    assert db.get_resumable_work_activity(agent.id, task.id) is None


def test_activate_work_activity_does_not_revive_complete_task() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    task = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, task)
    _complete_board_leave_activity(agent.id, task.id)

    activity = activate_work_activity(agent.id, db.get_task(task.id), task_status="active")
    assert activity is None or activity.status != "active" or activity.task_id != task.id
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"
    live = get_active_work_activity(agent.id)
    assert live is None or live.task_id != task.id


def test_activate_live_task_closes_complete_sibling_without_pending_jump() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    done = _new_assigned_task(agent.id, title="Finished slice")
    activate_work_activity(agent.id, done)
    _complete_board_leave_activity(agent.id, done.id)
    nxt = _new_assigned_task(agent.id, title="Next slice")

    activity = activate_work_activity(
        agent.id,
        nxt,
        task_status="accepted",
        supersede_note="Paused for newer accepted work.",
    )
    assert activity is not None
    assert activity.task_id == nxt.id
    assert db.get_task(done.id).status == "complete"
    assert db.get_task(nxt.id).status == "accepted"
    live = get_active_work_activity(agent.id)
    assert live is not None
    assert live.task_id == nxt.id


def test_prepare_activity_resumed_complete_task_soft_blocks() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    task = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, task)
    _complete_board_leave_activity(agent.id, task.id)

    prepared = prepare_trigger_context(
        agent.id,
        {"type": "activity_resumed", "task_id": task.id},
    )
    assert prepared is None or prepared.task_id != task.id or prepared.status != "active"
    assert db.get_task(task.id).status == "complete"
    notes = db.list_notifications(agent_id=agent.id, limit=8)
    assert any(TERMINAL_WAKE_LINE in (note.content or "") for note in notes)


def test_prepare_activity_resumed_complete_task_retargets_live() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    done = _new_assigned_task(agent.id, title="Finished slice")
    activate_work_activity(agent.id, done)
    _complete_board_leave_activity(agent.id, done.id)
    live_task = _new_assigned_task(agent.id, title="Open slice")

    trigger = {"type": "activity_resumed", "task_id": done.id}
    prepared = prepare_trigger_context(agent.id, trigger)
    assert prepared is not None
    assert prepared.task_id == live_task.id
    assert trigger["task_id"] == live_task.id
    assert db.get_task(done.id).status == "complete"
    assert db.get_task(live_task.id).status == "active"


def test_prepare_consent_grant_does_not_revive_complete_task() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    task = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, task)
    _complete_board_leave_activity(agent.id, task.id)

    prepare_trigger_context(
        agent.id,
        {
            "type": "host_path_consent_resolved",
            "status": "enabled",
            "task_id": task.id,
        },
    )
    assert db.get_task(task.id).status == "complete"


def test_apply_decision_accepts_new_work_while_complete_task_is_active() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    done = _new_assigned_task(agent.id, title="Finished slice")
    activate_work_activity(agent.id, done)
    _complete_board_leave_activity(agent.id, done.id)
    state = db.get_agent_state(agent.id)
    assert state is not None

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Next slice",
            "taskDescription": "Keep going.",
            "reply": "I'll take the next slice.",
        },
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Please do the next slice.",
            "from_name": "Human Operator",
        },
    )
    assert result["event"] == "decision_applied"
    assert db.get_task(done.id).status == "complete"
    live = get_active_work_activity(agent.id)
    assert live is not None
    assert live.task_id != done.id
    bound = db.get_task(live.task_id)
    assert bound is not None
    assert bound.status == "accepted"
    assert bound.title == "Next slice"


def test_apply_decision_soft_blocks_when_bound_task_is_complete() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    done = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, done)
    _complete_board_leave_activity(agent.id, done.id)
    state = db.get_agent_state(agent.id)
    assert state is not None

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": done.title,
            "reply": "I'll keep going.",
        },
        agent,
        state,
        {
            "type": "task_assigned",
            "task_id": done.id,
            "content": done.description,
            "from_name": "Human Operator",
        },
    )
    assert result["event"] == "world_feedback"
    assert result["feedback_code"] == TERMINAL_WAKE_FEEDBACK_CODE
    assert result["detail"] == TERMINAL_WAKE_LINE
    assert db.get_task(done.id).status == "complete"


@pytest.mark.asyncio
async def test_dispatcher_drain_skips_complete_activity_resume() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    task = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, task)
    _complete_board_leave_activity(agent.id, task.id)
    trigger = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Resume work."},
        task_id=task.id,
    )

    dispatcher = TurnDispatcher()
    dispatcher._running = True
    await dispatcher._drain_queue()

    row = db.get_agent_trigger(trigger.id)
    assert row is not None
    assert row.status == "completed"
    assert db.get_task(task.id).status == "complete"
    assert agent.id not in dispatcher._active_turns


def test_transition_task_still_rejects_complete_to_pending() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    task = _new_assigned_task(agent.id)
    activate_work_activity(agent.id, task)
    transition_task(task.id, "complete", reason="finished", actor="pytest")

    with pytest.raises(IllegalTaskTransition) as exc:
        transition_task(task.id, "pending", reason="illegal revive", actor="pytest")
    assert exc.value.from_status == "complete"
    assert exc.value.to_status == "pending"
    assert db.get_task(task.id).status == "complete"

    same = transition_live_work_task(task.id, "pending", reason="wake no-op")
    assert same is not None
    assert same.status == "complete"
