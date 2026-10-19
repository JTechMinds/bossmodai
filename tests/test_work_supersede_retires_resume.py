"""Superseding live work retires the old task's queued resumes.

Replays the incident where a stale ``activity_resumed`` for paused task A,
queued before the agent accepted task B, was claimed first and switched the
agent back to A. The DB is the conftest temp file (``BOSSMOD_DB_PATH``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import db
from core import config
from core.agent_loop.activity_runtime import activate_work_activity, complete_activity, get_active_work_activity
from core.agent_loop.activity_scheduler import (
    build_activity_resume_trigger,
    next_work_after_end,
    prepare_trigger_context,
)
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from core.tasking.transitions import transition_task

_ACCEPT_NOTE = "Paused for newer accepted work."


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


def _task(agent_id: str, *, title: str):
    return create_or_bind_task(
        title=title,
        description="Do the work.",
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


def _queue_resume(agent_id: str, task_id: str):
    """Queue the resume the dispatcher's live-work continuation would write."""
    active = get_active_work_activity(agent_id)
    assert active is not None and active.task_id == task_id
    spec = build_activity_resume_trigger(active, reason="Resume work.")
    return db.create_agent_trigger(
        agent_id=spec["agent_id"],
        trigger_type=spec["trigger_type"],
        source_channel=spec["source_channel"],
        payload=spec["payload"],
        task_id=spec["task_id"],
    )


def _queued(agent_id: str, *, trigger_type: str | None = None, task_id: str | None = None) -> list[dict]:
    rows = db.list_agent_triggers(agent_id, status="queued", limit=500)
    return [
        row
        for row in rows
        if (trigger_type is None or row["trigger_type"] == trigger_type)
        and (task_id is None or row["task_id"] == task_id)
    ]


def _agent_with_live_work_on_a():
    agent = db.create_agent("Charles", role="Engineer", desk_x=1, desk_y=1)
    task_a = _task(agent.id, title="Contrast fix")
    activate_work_activity(agent.id, task_a, task_status="active")
    return agent, task_a


def test_accepting_new_work_drops_old_tasks_queued_resume() -> None:
    agent, task_a = _agent_with_live_work_on_a()
    _queue_resume(agent.id, task_a.id)
    task_b = _task(agent.id, title="Commit and push")

    activate_work_activity(agent.id, task_b, task_status="accepted", supersede_note=_ACCEPT_NOTE)

    assert _queued(agent.id, trigger_type="activity_resumed", task_id=task_a.id) == []
    assert db.get_task(task_a.id).status == "pending"
    assert db.get_resumable_work_activity(agent.id, task_a.id) is not None
    live = get_active_work_activity(agent.id)
    assert live is not None and live.task_id == task_b.id


def test_supersede_keeps_old_tasks_follow_up_and_other_tasks_resumes() -> None:
    agent, task_a = _agent_with_live_work_on_a()
    task_c = _task(agent.id, title="Unrelated task")
    follow_up = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="task_follow_up",
        source_channel="work",
        payload={"content": "Sarah asks about A."},
        task_id=task_a.id,
    )
    other_resume = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Resume C."},
        task_id=task_c.id,
    )
    task_b = _task(agent.id, title="Commit and push")

    activate_work_activity(agent.id, task_b, task_status="accepted", supersede_note=_ACCEPT_NOTE)

    remaining = {row["id"] for row in _queued(agent.id)}
    assert follow_up.id in remaining
    assert other_resume.id in remaining


def test_paused_work_resumes_after_new_work_ends() -> None:
    agent, task_a = _agent_with_live_work_on_a()
    _queue_resume(agent.id, task_a.id)
    task_b = _task(agent.id, title="Commit and push")
    activate_work_activity(agent.id, task_b, task_status="accepted", supersede_note=_ACCEPT_NOTE)

    transition_task(task_b.id, "active", reason="started", actor="pytest")
    transition_task(task_b.id, "complete", reason="finished", actor="pytest")
    live = get_active_work_activity(agent.id)
    assert live is not None and live.task_id == task_b.id
    complete_activity(live.id, detail="Done.")

    wakes = next_work_after_end(agent.id, ended_task_id=task_b.id)

    assert [(wake["trigger_type"], wake["task_id"]) for wake in wakes] == [("activity_resumed", task_a.id)]


def test_delete_queued_triggers_task_filter() -> None:
    agent = db.create_agent("Charles", role="Engineer", desk_x=1, desk_y=1)
    task_a = _task(agent.id, title="A")
    task_c = _task(agent.id, title="C")
    queued_a = db.create_agent_trigger(agent.id, "activity_resumed", "work", {}, task_id=task_a.id)
    claimed_a = db.create_agent_trigger(agent.id, "activity_resumed", "work", {}, task_id=task_a.id)
    assert db.claim_trigger(claimed_a.id) is not None
    queued_c = db.create_agent_trigger(agent.id, "activity_resumed", "work", {}, task_id=task_c.id)

    deleted = db.delete_queued_triggers(agent.id, trigger_types=["activity_resumed"], task_id=task_a.id)

    assert deleted == 1
    assert db.get_agent_trigger(queued_a.id) is None
    claimed = db.get_agent_trigger(claimed_a.id)
    assert claimed is not None and claimed.status == "claimed"
    assert db.get_agent_trigger(queued_c.id) is not None


def test_incident_replay_stale_resume_cannot_pause_accepted_work() -> None:
    agent, task_a = _agent_with_live_work_on_a()
    # The A turn yielded to the operator; the continuation queued A's resume.
    _queue_resume(agent.id, task_a.id)
    # The decision turn accepts B, then its own resume for B is persisted.
    task_b = _task(agent.id, title="Commit and push")
    activate_work_activity(agent.id, task_b, task_status="accepted", supersede_note=_ACCEPT_NOTE)
    _queue_resume(agent.id, task_b.id)

    queued = sorted(_queued(agent.id), key=lambda item: (item["created_at"], item["id"]))
    assert [(row["trigger_type"], row["task_id"]) for row in queued] == [("activity_resumed", task_b.id)]
    # Every queued trigger, claimed oldest first, keeps B as the live work.
    for row in queued:
        assert row["task_id"] != task_a.id
        assert db.claim_trigger(row["id"]) is not None
        payload = json.loads(row["payload"]) if row["payload"] else {}
        trigger = {**payload, "type": row["trigger_type"], "task_id": row["task_id"], "trigger_id": row["id"]}
        prepare_trigger_context(agent.id, trigger)
        live = get_active_work_activity(agent.id)
        assert live is not None and live.task_id == task_b.id

    assert db.get_task(task_a.id).status == "pending"
    # B's own resume finds B already live and leaves it accepted, never paused.
    assert db.get_task(task_b.id).status == "accepted"
