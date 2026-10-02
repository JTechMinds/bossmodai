"""Fix A — live work always has a next execution turn after any turn ends.

The dispatcher, not each decision branch, queues the resume. It is skipped
while an operator gate is open, while the task waits or is blocked, and when
a resume is already queued.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from tests._connections import model_connection
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import ensure_live_work_continuation
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task


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


def _task(agent_id: str, *, title: str = "Execute the 9 fixes", channel_id: str | None = None):
    return create_or_bind_task(
        title=title,
        description="Apply the review fixes.",
        project=None,
        assigned_to=agent_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel" if channel_id else None,
        notification_policy="completion_blocked" if channel_id else None,
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _working_agent(name: str = "Charles", *, model: str | None = "test/mock", channel_id: str | None = None):
    agent = db.create_agent(name, role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection(model))
    task = _task(agent.id, channel_id=channel_id)
    activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    return agent, task


def _resumes(agent_id: str, task_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id, status="queued")
        if row.get("trigger_type") == "activity_resumed" and row.get("task_id") == task_id
    ]


def _claimed_payload(row) -> dict[str, Any]:
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    payload = json.loads(claimed.payload) if claimed.payload else {}
    payload.update(
        {
            "type": claimed.trigger_type,
            "trigger_id": claimed.id,
            "task_id": claimed.task_id,
            "source_channel": claimed.source_channel,
            "claim_generation": claimed.claim_generation,
        }
    )
    return payload


def _script(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> list[str]:
    queue = list(contents)
    seen: list[str] = []

    async def _fake_completion(**_kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        content = queue.pop(0)
        seen.append(content)
        return LLMResponse(content=content, model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen


@pytest.mark.asyncio
async def test_channel_reply_while_working_queues_the_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 20:48 case: "On it… executing now" in the thread must not orphan live work."""
    brad = db.create_agent("Brad", role="Reviewer", desk_x=2, desk_y=1, connection_id=model_connection("test/mock"))
    charles = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    channel = db.create_channel(
        name="Charles, Brad",
        member_agent_ids=[charles.id, brad.id],
        created_by=HUMAN_SENDER_ID,
    )
    task = _task(charles.id, channel_id=channel.id)
    activity_runtime.activate_work_activity(charles.id, task, task_status="active")
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Sounds good @Charles",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    spec = next(item for item in triggers if item["agent_id"] == charles.id)
    row = db.create_agent_trigger(
        agent_id=charles.id,
        trigger_type=spec["trigger_type"],
        source_channel=spec["source_channel"],
        payload=spec["payload"],
        task_id=spec.get("task_id"),
    )
    _script(
        monkeypatch,
        ['{"act":"reply","intent":"status","msg":"On it. Executing the 9 fixes now.","work_commit":true,"th":"ack"}'],
    )
    state = db.get_agent_state(charles.id)
    assert state is not None
    assert _resumes(charles.id, task.id) == []

    await TurnDispatcher()._run_trigger(charles, state, _claimed_payload(row))

    assert db.get_agent_trigger(row.id).status == "completed"
    assert len(_resumes(charles.id, task.id)) == 1


@pytest.mark.asyncio
async def test_status_reply_to_operator_queues_exactly_one_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    row = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "Status?", "from_name": "Human Operator"},
    )
    _script(monkeypatch, ['{"say":"Halfway through the fixes.","actions":[],"work_commit":false}'])
    state = db.get_agent_state(agent.id)
    assert state is not None

    await TurnDispatcher()._run_trigger(agent, state, _claimed_payload(row))

    assert len(_resumes(agent.id, task.id)) == 1


def test_no_resume_while_cli_approval_is_pending() -> None:
    agent, _task_row = _working_agent()
    db.create_cli_approval_request(agent_id=agent.id, command="pytest -q")
    assert db.agent_has_pending_operator_gate(agent.id) is True
    assert ensure_live_work_continuation(agent.id) is None


@pytest.mark.parametrize("card_kind", ["host_path", "workspace_preference", "shell_executor", "nest_git"])
def test_no_resume_while_a_consent_card_is_pending(card_kind: str) -> None:
    agent, task = _working_agent()
    db.create_consent_request(
        agent_id=agent.id,
        path="/home/operator/repo",
        grant_root="/home/operator/repo",
        reason="Needs access",
        task_id=task.id,
        card_kind=card_kind,
    )
    assert db.agent_has_pending_operator_gate(agent.id) is True
    assert ensure_live_work_continuation(agent.id) is None


def test_gate_is_clear_without_pending_rows() -> None:
    agent, _task_row = _working_agent()
    assert db.agent_has_pending_operator_gate(agent.id) is False
    spec = ensure_live_work_continuation(agent.id)
    assert spec is not None
    assert spec["trigger_type"] == "activity_resumed"


def test_no_resume_while_the_task_waits() -> None:
    agent, task = _working_agent()
    activity_runtime.pause_active_work(agent.id, "Waiting on Brad.", task_status="waiting")
    assert db.get_task(task.id).status == "waiting"
    assert ensure_live_work_continuation(agent.id) is None


def test_no_resume_while_the_task_is_blocked() -> None:
    agent, task = _working_agent()
    apply_no_progress_block(agent, {"type": "human_chat"})
    assert db.get_task(task.id).status == "blocked"
    assert ensure_live_work_continuation(agent.id) is None


def test_no_second_resume_when_one_is_already_open() -> None:
    agent, task = _working_agent()
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Resume."},
        task_id=task.id,
    )
    assert ensure_live_work_continuation(agent.id) is None


@pytest.mark.asyncio
async def test_skipped_turn_still_queues_the_resume() -> None:
    agent, task = _working_agent(model=None)
    row = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "Status?", "from_name": "Human Operator"},
    )
    state = db.get_agent_state(agent.id)
    assert state is not None

    await TurnDispatcher()._run_trigger(agent, state, _claimed_payload(row))

    assert db.get_agent_trigger(row.id).status == "completed"
    assert len(_resumes(agent.id, task.id)) == 1


@pytest.mark.asyncio
async def test_skipped_resume_is_not_requeued_forever() -> None:
    """With no model a resume only skips again, so the invariant does not re-queue it."""
    agent, task = _working_agent(model=None)
    row = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Resume."},
        task_id=task.id,
    )
    state = db.get_agent_state(agent.id)
    assert state is not None

    await TurnDispatcher()._run_trigger(agent, state, _claimed_payload(row))

    assert db.get_agent_trigger(row.id).status == "completed"
    assert _resumes(agent.id, task.id) == []


def _done_evidence(agent_id: str) -> None:
    db.create_bm_cli_event(
        agent_id=agent_id,
        command="cat /projects/review.md",
        content_present=False,
        executor="virtual",
        cwd_before="/",
        cwd_after="/",
        policy_tier="read",
        decision="allowed",
        exit_code=0,
        result_kind="read",
        stdout_preview="ok",
        stderr_preview=None,
        changed_paths=None,
        trigger_type="activity_resumed",
    )


def _set_created_at(task_id: str, when: str) -> None:
    db.execute("UPDATE tasks SET created_at = $1 WHERE id = $2", [when, task_id])


@pytest.mark.asyncio
async def test_done_resumes_paused_work_before_an_older_pending_task() -> None:
    from core.agent_loop.actions import execute_action
    from core.agent_loop.activity_scheduler import next_work_after_end

    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    queued = _task(agent.id, title="Older queued work")
    _set_created_at(queued.id, "2026-01-01 00:00:00")
    paused = _task(agent.id, title="Paused work")
    activity_runtime.activate_work_activity(agent.id, paused, task_status="active")
    current = _task(agent.id, title="Urgent fix")
    activity_runtime.activate_work_activity(agent.id, current, task_status="active")
    assert db.get_task(paused.id).status == "pending"
    assert db.get_resumable_work_activity(agent.id, paused.id) is not None
    # Still working: the habit does nothing.
    assert next_work_after_end(agent.id, ended_task_id=None) == []

    _done_evidence(agent.id)
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = await execute_action(
        {
            "action": "complete",
            "summary": "Fixed the build.",
            "doneClaim": {"type": "proof", "ev": "build log shows green"},
        },
        agent,
        state,
    )
    assert result["event"] == "status_changed"
    assert db.get_task(current.id).status == "complete"
    nxt = [
        (item.get("trigger_type"), item.get("task_id"))
        for item in result["trigger_requests"]
        if item.get("agent_id") == agent.id
    ]
    assert nxt == [("activity_resumed", paused.id)]
    live = activity_runtime.get_active_work_activity(agent.id)
    assert live is not None and live.task_id == paused.id
    assert db.get_task(queued.id).status == "pending"


def test_next_work_picks_the_oldest_queued_task_and_never_a_waiting_one() -> None:
    from core.agent_loop.activity_scheduler import next_work_after_end

    agent = db.create_agent("Harley", role="Planner", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    waiting = _task(agent.id, title="Waiting on Debra")
    activity_runtime.activate_work_activity(agent.id, waiting, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Waiting on Debra.", task_status="waiting")
    assert next_work_after_end(agent.id, ended_task_id=None) == []

    newer = _task(agent.id, title="Newer queued work")
    older = _task(agent.id, title="Older queued work")
    _set_created_at(older.id, "2026-01-01 00:00:00")
    _set_created_at(newer.id, "2026-01-02 00:00:00")
    wakes = next_work_after_end(agent.id, ended_task_id=None)
    assert [(item["trigger_type"], item["task_id"]) for item in wakes] == [("task_assigned", older.id)]
    # The ended task is never the next one, and an already-queued wake is not doubled.
    assert [
        item["task_id"] for item in next_work_after_end(agent.id, ended_task_id=older.id)
    ] == [newer.id]
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="task_assigned",
        source_channel="work",
        payload={},
        task_id=older.id,
    )
    assert next_work_after_end(agent.id, ended_task_id=None) == []
