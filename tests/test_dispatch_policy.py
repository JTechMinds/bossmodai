"""HA-CORR-P1-07 — task_assigned may claim during a live conversation."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import build_task_assigned_trigger, can_dispatch_trigger
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.policies import get_trigger_policy
from core.models.message import HUMAN_SENDER_ID
from core.tasking import create_or_bind_task


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


def _create_assigned_task(agent_id: str, title: str = "Ship notes"):
    return create_or_bind_task(
        title=title,
        description="Write the notes",
        project=None,
        assigned_to=agent_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def _enqueue_assignment(agent_id: str, title: str = "Ship notes"):
    creation = _create_assigned_task(agent_id, title=title)
    spec = build_task_assigned_trigger(creation.task)
    return db.create_agent_trigger(**spec), creation.task


def test_task_assigned_policy_does_not_block_on_live_activity() -> None:
    policy = get_trigger_policy("task_assigned")
    assert policy.blocks_on_in_transit is True
    assert policy.blocks_on_active_activity is False


def test_can_dispatch_task_assigned_during_conversation() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    activity_runtime.start_conversation_activity(agent.id, title="Standup chat")
    state = db.get_agent_state(agent.id)
    active = activity_runtime.get_active_activity(agent.id)

    assert state is not None
    assert active is not None
    assert active.kind == "conversation"
    assert can_dispatch_trigger(
        trigger_type="task_assigned",
        state=state,
        active_activity=active,
    )


def test_claim_task_assigned_during_conversation_in_one_drain() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    activity_runtime.start_conversation_activity(agent.id, title="Standup chat")
    trigger, task = _enqueue_assignment(agent.id)

    claimed = TurnDispatcher()._claim_available_trigger()
    assert claimed is not None
    assert claimed.id == trigger.id
    assert claimed.trigger_type == "task_assigned"
    assert claimed.task_id == task.id
    assert claimed.status == "claimed"


def test_can_dispatch_task_assigned_blocked_while_in_transit() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    activity_runtime.start_movement_activity(agent.id, destination="Kitchen")
    state = db.get_agent_state(agent.id)
    active = activity_runtime.get_active_activity(agent.id)

    assert state is not None
    assert state.status == "in_transit"
    assert active is not None
    assert active.kind == "movement"
    assert not can_dispatch_trigger(
        trigger_type="task_assigned",
        state=state,
        active_activity=active,
    )


def test_claim_task_assigned_waits_for_arrival() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    activity_runtime.start_movement_activity(agent.id, destination="Kitchen")
    trigger, _task = _enqueue_assignment(agent.id)

    claimed = TurnDispatcher()._claim_available_trigger()
    assert claimed is None

    rows = db.list_agent_triggers(agent.id)
    queued = next(row for row in rows if row["id"] == trigger.id)
    assert queued["status"] == "queued"
