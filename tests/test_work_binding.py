"""The single "what live work is this turn bound to?" seam (plan Revision 3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from tests._connections import model_connection
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.work_binding import (
    WorkBinding,
    bind_turn,
    bound_activity,
    bound_task_id,
    bound_work,
    bound_work_activity,
    is_detached,
)
from core.loop_breathing import off_request_loop
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task

_DETACHED = {"type": "extension_event", "source_channel": "system", "task_id": None}
_ATTACHED = {"type": "human_chat", "source_channel": "chat", "task_id": None}


def setup_function() -> None:
    runtime_events.set_sink(NullRuntimeEventSink())
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


def _working_agent(name: str):
    """An agent whose live work activity is bound to a task."""
    agent = db.create_agent(name, role="Engineer", connection_id=model_connection("test/mock"))
    task = create_or_bind_task(
        title=f"{name}'s task",
        description="Do the work.",
        project=None,
        assigned_to=agent.id,
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
    ).task
    activity = activity_runtime.activate_work_activity(agent.id, task)
    assert activity is not None
    return agent, task, activity


def _real(agent_id: str) -> WorkBinding:
    return WorkBinding(
        activity=activity_runtime.get_active_activity(agent_id),
        work_activity=activity_runtime.get_active_work_activity(agent_id),
        task_id=activity_runtime.get_active_task_id(agent_id),
    )


def test_outside_any_scope_the_binding_is_the_real_lookups() -> None:
    agent, task, activity = _working_agent("Charles")

    binding = bound_work(agent.id)

    assert binding == _real(agent.id)
    assert binding.task_id == task.id and binding.work_activity.id == activity.id
    assert bound_task_id(agent.id) == task.id
    assert bound_work_activity(agent.id).id == activity.id
    assert bound_activity(agent.id).id == activity.id


def test_an_attached_scope_gives_the_real_lookups() -> None:
    agent, task, _activity = _working_agent("Charles")

    with bind_turn(agent.id, _ATTACHED):
        assert bound_work(agent.id) == _real(agent.id)
        assert bound_task_id(agent.id) == task.id


def test_a_detached_scope_unbinds_only_its_own_agent() -> None:
    agent, _task, _activity = _working_agent("Charles")
    other, other_task, _other_activity = _working_agent("Iris")

    with bind_turn(agent.id, _DETACHED):
        assert bound_work(agent.id) == WorkBinding(activity=None, work_activity=None, task_id=None)
        assert bound_task_id(agent.id) is None
        assert bound_work_activity(agent.id) is None
        assert bound_activity(agent.id) is None
        # Another agent's lookups inside the scope stay real.
        assert bound_work(other.id) == _real(other.id)
        assert bound_task_id(other.id) == other_task.id
    # The low-level lookups were never masked.
    assert activity_runtime.get_active_task_id(agent.id) is not None


def test_the_scope_resets_after_the_block_and_on_an_exception() -> None:
    agent, task, _activity = _working_agent("Charles")

    with bind_turn(agent.id, _DETACHED):
        assert bound_task_id(agent.id) is None
    assert bound_task_id(agent.id) == task.id

    with pytest.raises(RuntimeError, match="turn failed"):
        with bind_turn(agent.id, _DETACHED):
            raise RuntimeError("turn failed")
    assert bound_task_id(agent.id) == task.id


def test_a_nested_scope_wins_and_the_outer_one_is_restored() -> None:
    agent, task, _activity = _working_agent("Charles")

    with bind_turn(agent.id, _DETACHED):
        with bind_turn(agent.id, _ATTACHED):
            assert bound_task_id(agent.id) == task.id
        assert bound_task_id(agent.id) is None


@pytest.mark.asyncio
async def test_the_scope_propagates_into_off_request_loop() -> None:
    agent, task, _activity = _working_agent("Charles")

    with bind_turn(agent.id, _DETACHED):
        assert await off_request_loop(bound_task_id, agent.id) is None
    with bind_turn(agent.id, _ATTACHED):
        assert await off_request_loop(bound_task_id, agent.id) == task.id


def test_is_detached_reads_the_policy_or_a_literal_detached_origin() -> None:
    assert is_detached(_DETACHED) is True
    assert is_detached(_ATTACHED) is False
    assert is_detached({"type": "cli_approval_resolved", "detached_origin": True}) is True
    assert is_detached({"type": "cli_approval_resolved", "detached_origin": "true"}) is False
    assert is_detached({"type": "cli_approval_resolved"}) is False
