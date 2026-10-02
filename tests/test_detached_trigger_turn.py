"""An ``extension_event`` runs detached from live work (plan W6).

Like checking email between steps of a task: the task's frozen transcript,
its state and the agent's activity are untouched; ``idle`` ends the turn; the
task-state actions are refused; and the dispatcher re-queues the live work
afterwards.
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
from core.agent_loop.dispatcher import NOT_RETRIED_NO_REPEAT_REASON, TurnDispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.policies import get_trigger_policy
from core.agent_loop.work_snapshot import freeze_work_turn
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task

_STATUS_STEP = '{"act":"cli","data":{"cmd":"status"},"th":"check what arrived"}'
_IDLE_STEP = '{"act":"idle","th":"handled the email"}'
_EVENT_PAYLOAD = {
    "extension_id": "ms365-mail",
    "extension_name": "Microsoft 365 Mailbox",
    "from_name": "Microsoft 365 Mailbox",
    "title": "New email in reports@contoso.com",
    "lines": ['[m3f9a21c] Alice Doe <alice@x.com> — Re: Daily report — "Numbers?"'],
    "content": 'New email in reports@contoso.com\n- [m3f9a21c] Alice Doe <alice@x.com> — Re: Daily report — "Numbers?"',
}


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


def _task(agent_id: str):
    return create_or_bind_task(
        title="Execute the 9 fixes",
        description="Apply the review fixes.",
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
    ).task


def _working_agent_with_snapshot():
    """An agent mid-task whose work activity already carries a frozen transcript."""
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    task = _task(agent.id)
    activity = _freeze_live_work(agent, task)
    return agent, task, activity


def _freeze_live_work(agent, task, *, task_status: str = "active"):
    """Activate ``task`` as the agent's live work and freeze a transcript onto it."""
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status=task_status)
    assert activity is not None
    freeze_work_turn(
        agent=agent,
        activity=activity,
        initial_len=0,
        context=[
            {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"my-board"},"th":"board"}'},
            {"role": "user", "content": "board result"},
        ],
        fingerprints=["my-board"],
        no_progress_checkpoints=0,
    )
    return activity


def _snapshot_bytes(activity_id: str) -> str:
    snapshot = db.get_work_snapshot(activity_id)
    assert snapshot is not None
    return json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)


def _script(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> list[list[dict[str, str]]]:
    queue = list(contents)
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append([dict(item) for item in kwargs.get("messages") or []])
        return LLMResponse(content=queue.pop(0), model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return prompts


def _event_trigger() -> dict[str, Any]:
    return {**_EVENT_PAYLOAD, "type": "extension_event", "source_channel": "system", "task_id": None}


def test_the_policy_is_detached_and_not_an_interrupt() -> None:
    from core.agent_loop.activity_scheduler import INTERRUPT_TRIGGER_TYPES

    policy = get_trigger_policy("extension_event")
    assert policy.detached_from_work is True and policy.end_turn_after_direct_reply is False
    assert "extension_event" not in INTERRUPT_TRIGGER_TYPES
    assert get_trigger_policy("activity_resumed").detached_from_work is False


@pytest.mark.asyncio
async def test_the_snapshot_is_untouched_and_idle_ends_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _snapshot_bytes(activity.id)
    prompts = _script(monkeypatch, [_STATUS_STEP, _IDLE_STEP])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "completed"
    assert outcome.result["detail"] == "Charles finished handling an extension event"
    assert len(prompts) == 2
    # No frozen step was replayed into the email turn.
    assert not any("board result" == item["content"] for item in prompts[0])
    # The trigger block reached the model.
    joined = "\n".join(item["content"] for item in prompts[0])
    assert "New email in reports@contoso.com" in joined and "This arrived through Microsoft 365 Mailbox" in joined
    assert _snapshot_bytes(activity.id) == before
    assert db.get_task(task.id).status == "active"
    current = db.get_activity(activity.id)
    assert current.status == "active" and current.kind == "work"


_DONE_STEP = '{"act":"done","data":{"sum":"finished"},"th":"done"}'


@pytest.mark.parametrize("step", [
    _DONE_STEP,
    '{"act":"wait","data":{"why":"waiting on Alice"},"th":"wait"}',
    '{"act":"block","data":{"why":"blocked on access"},"th":"block"}',
])
@pytest.mark.asyncio
async def test_a_refused_task_state_action_is_feedback_and_idle_completes_the_turn(
    monkeypatch: pytest.MonkeyPatch, step: str,
) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _snapshot_bytes(activity.id)
    prompts = _script(monkeypatch, [step, _IDLE_STEP])
    trigger = _claimed_trigger(agent.id, "extension_event", _EVENT_PAYLOAD)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    # The refusal reached the model in the same turn, as a refusal.
    assert len(prompts) == 2
    feedback = prompts[1][-1]["content"]
    assert feedback.startswith("Your previous action was rejected by the runtime: ")
    assert "your task is paused unchanged" in feedback
    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.status == "completed" and row.retry_count == 0
    assert [item["id"] for item in db.list_agent_triggers(agent.id) if item["trigger_type"] == "extension_event"] == [row.id]
    assert db.get_task(task.id).status == "active"
    assert db.get_activity(activity.id).status == "active"
    assert _snapshot_bytes(activity.id) == before


@pytest.mark.asyncio
async def test_refusals_past_the_repair_budget_fail_a_turn_that_ran_nothing_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _snapshot_bytes(activity.id)
    _script(monkeypatch, [_DONE_STEP] * 3)

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "failed"
    assert "your task is paused unchanged" in (outcome.diagnostic_error or "")
    # Nothing ran, so a retry replays nothing.
    assert outcome.retryable is True
    assert db.get_task(task.id).status == "active"
    assert _snapshot_bytes(activity.id) == before


@pytest.mark.asyncio
async def test_a_detached_turn_that_ran_an_unlisted_command_then_failed_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)
    _script(monkeypatch, [_STATUS_STEP, _DONE_STEP, _DONE_STEP, _DONE_STEP])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "failed"
    # `status` is not on the no-retry list, so replaying it is harmless.
    assert outcome.retryable is True
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_a_detached_turn_that_ran_a_listed_command_then_failed_is_exhausted_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.set_setting("cli_no_retry_commands", "mail send\nstatus", "cli_policy")
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)
    _script(monkeypatch, [_STATUS_STEP, _DONE_STEP, _DONE_STEP, _DONE_STEP])
    outcomes: list[Any] = []

    async def _recording_run_turn(*args: Any, **kwargs: Any):
        outcome = await run_turn(*args, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _recording_run_turn)
    trigger = _claimed_trigger(agent.id, "extension_event", _EVENT_PAYLOAD)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    [outcome] = outcomes
    assert outcome.trigger_status == "failed" and outcome.retryable is False
    # The retry limit is not spent: the trigger fails at once, with no retry.
    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.status == "failed" and row.retry_count == 0
    assert _live_state(task.id, activity.id) == before
    [notice] = _operator_dms(agent.id)
    assert notice.startswith("I hit repeated runtime failures while handling an extension event")
    assert "your task is paused unchanged" in notice


@pytest.mark.asyncio
async def test_a_turn_that_ran_a_listed_command_then_raised_is_failed_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.set_setting("cli_no_retry_commands", "status", "cli_policy")
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)
    calls = 0

    async def _status_then_crash(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return LLMResponse(content=_STATUS_STEP, model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12)
        raise RuntimeError("provider crashed mid-turn")

    monkeypatch.setattr("core.llm.client.completion", _status_then_crash)
    trigger = _claimed_trigger(agent.id, "extension_event", _EVENT_PAYLOAD)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    assert calls == 2
    # The turn recorded the listed command on the row before it raised, so
    # the exception path fails it at once instead of replaying `status`.
    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.retry_blocked is True
    assert row.status == "failed" and row.retry_count == 0
    assert _live_state(task.id, activity.id) == before
    [notice] = _operator_dms(agent.id)
    assert notice.endswith("Last error: provider crashed mid-turn")


@pytest.mark.asyncio
async def test_a_turn_that_raised_without_a_listed_command_is_still_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _task_row, _activity = _working_agent_with_snapshot()

    async def _boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("Graph outage")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _boom)
    trigger = _claimed_trigger(agent.id, "extension_event", _EVENT_PAYLOAD)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.retry_blocked is False
    assert row.status == "queued" and row.retry_count == 1


@pytest.mark.asyncio
async def test_idle_does_not_complete_a_meeting(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Iris", role="Researcher", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    meeting = activity_runtime.begin_commitment_activity(
        agent.id, kind="meeting", title="Planning sync", reason="Joined the meeting.",
    )
    _script(monkeypatch, [_IDLE_STEP])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "completed"
    assert db.get_activity(meeting.id).status == "active"


@pytest.mark.asyncio
async def test_the_live_work_is_requeued_after_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _snapshot_bytes(activity.id)
    row = db.create_agent_trigger(
        agent_id=agent.id, trigger_type="extension_event", source_channel="system", payload=_EVENT_PAYLOAD,
    )
    claimed = db.claim_trigger(row.id)
    payload = {
        **json.loads(claimed.payload), "type": claimed.trigger_type, "trigger_id": claimed.id,
        "task_id": claimed.task_id, "source_channel": claimed.source_channel,
        "claim_generation": claimed.claim_generation,
    }
    _script(monkeypatch, [_IDLE_STEP])

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), payload)

    assert db.get_agent_trigger(row.id).status == "completed"
    resumes = [
        item for item in db.list_agent_triggers(agent.id, status="queued")
        if item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
    ]
    assert len(resumes) == 1
    assert _snapshot_bytes(activity.id) == before
    assert db.get_task(task.id).status == "active"


# ─── the trigger block, the runtime preview and the one-time prompt reconcile ───


def test_the_extension_event_branch_renders_content_and_from_name() -> None:
    from core.llm.context_builder import _format_trigger

    block = _format_trigger(_event_trigger(), "execution").strip()
    assert block.startswith("New email in reports@contoso.com\n- [m3f9a21c] Alice Doe <alice@x.com>")
    assert "This arrived through Microsoft 365 Mailbox; it is not a chat message." in block
    assert "Use idle when you are done." in block
    assert "You have been activated." not in block


def test_the_runtime_preview_accepts_extension_event() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
    from api.routes import router

    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    client = TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})
    assert "extension_event" in client.get("/api/runtime/contracts").json()["preview_triggers"]
    response = client.post("/api/runtime/contracts/preview", json={
        "contract_kind": "execution", "trigger_type": "extension_event", "scope": "bundle",
    })
    assert response.status_code == 200, response.text
    rendered = response.json()["rendered"]
    assert "New email in reports@contoso.com" in rendered
    assert "This arrived through Microsoft 365 Mailbox" in rendered


def test_the_extension_event_prompt_reconcile_runs_once() -> None:
    from core.default_prompts import load_default_prompt
    from db.settings import seed_defaults

    key, marker = "runtime_block_trigger_event", "extension_event_prompt_reconciled"

    def stored(name: str) -> str | None:
        row = db.query_one("SELECT value FROM settings WHERE key = $1", [name])
        return None if row is None else str(row["value"])

    assert stored(marker) == "true"
    assert "trigger.type = 'extension_event'" in stored(key)
    db.execute("DELETE FROM settings WHERE key = $1", [marker])
    db.execute("UPDATE settings SET value = $1 WHERE key = $2", ["an older operator edit", key])
    seed_defaults()
    assert stored(key) == load_default_prompt(key) and stored(marker) == "true"
    db.set_setting(key, "operator's own text", "advanced")
    seed_defaults()
    assert stored(key) == "operator's own text"


# ─── Revision 2: the remaining detached-turn leaks (L1–L5) ───


def _live_state(task_id: str, activity_id: str) -> tuple[Any, ...]:
    """Everything a detached turn must leave untouched on the live task and its activity."""
    task = db.get_task(task_id)
    activity = db.get_activity(activity_id)
    assert task is not None and activity is not None
    return (task.status, task.status_note, _snapshot_bytes(activity_id), activity.status, activity.detail)


def _channel_worker():
    """An agent on live work whose task was assigned from a shared thread."""
    from tests.test_consent_origin import _channel_for, _channel_task

    agent = db.create_agent("Gerry", role="Engineer", connection_id=model_connection("test/mock"))
    peer = db.create_agent("Jim", role="Engineer")
    channel = _channel_for(agent.id, peer.id)
    task = _channel_task(assignee_id=agent.id, channel_id=channel.id).task
    activity = _freeze_live_work(agent, task)
    return agent, peer, channel, task, activity


@pytest.mark.asyncio
async def test_l1_no_progress_in_a_detached_turn_fails_the_turn_without_touching_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    db.execute("UPDATE agents SET guardian_no_progress_threshold = 2 WHERE id = $1", [agent.id])
    agent = db.get_agent(agent.id)
    before = _live_state(task.id, activity.id)
    _script(monkeypatch, [_STATUS_STEP] * 5)

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "failed"
    # Only `status` ran, and it is not on the no-retry list.
    assert outcome.retryable is True
    assert (outcome.diagnostic_error or "").startswith("Guardian [no_progress]: ")
    assert outcome.result["event"] == "guardian_violation"
    assert outcome.steps[-1]["error"].startswith("Guardian [no_progress]: ")
    assert _live_state(task.id, activity.id) == before
    # Neither a checkpoint resume nor a block was queued for the paused task.
    assert not any(
        item.get("task_id") == task.id for item in db.list_agent_triggers(agent.id, status="queued")
    )


@pytest.mark.asyncio
async def test_l2_a_cli_approval_card_goes_to_focus_not_the_task_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.bm_cli.policy_engine import policy_engine
    from core.models.cli_policy import CLI_APPROVAL_KIND

    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()
    agent, _peer, channel, task, activity = _channel_worker()
    before = _live_state(task.id, activity.id)
    _script(monkeypatch, ['{"act":"cli","data":{"cmd":"pip install pytest"},"th":"install"}'])
    trigger = _event_trigger()

    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.result.get("event") == "cli_approval_required"
    stored = db.get_cli_approval_request(outcome.result.get("approval_request_id"))
    assert stored is not None and stored.channel_id is None
    assert trigger.get("channel_id") is None
    assert not any(item.approval_id == stored.id for item in db.list_channel_messages(channel.id))
    assert any(
        item.kind == CLI_APPROVAL_KIND for item in db.list_notifications(agent_id=agent.id, chat_visible=True)
    )
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_l3_a_managed_write_reports_progress_but_leaves_the_activity_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.bm_cli.managed_writer.types import ManagedWriteOutcome, ManagedWriteProgress
    from core.bm_cli.results import success_result

    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)
    broadcasts: list[dict[str, Any]] = []

    class _RecordingSink(NullRuntimeEventSink):
        async def broadcast_activity(self, event: str, detail: str, agent_name: str | None = None,
                                     extra: dict[str, Any] | None = None) -> None:
            broadcasts.append({"event": event, "detail": detail})

    runtime_events.set_sink(_RecordingSink())

    async def _fake_managed_write(**kwargs: Any) -> ManagedWriteOutcome:
        await kwargs["progress_callback"](ManagedWriteProgress(
            stage="file_started", detail="Writing /me/reply.md", path="/me/reply.md", counts_as_progress=True,
        ))
        return ManagedWriteOutcome(
            cli_result=success_result(
                command="write /me/reply.md", detail="Wrote /me/reply.md", kind="write", data={}, sections=[],
            ),
            prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=1,
        )

    monkeypatch.setattr("core.agent_loop.execution_turn.run_managed_write", _fake_managed_write)
    _script(monkeypatch, ['{"act":"cli","data":{"cmd":"write /me/reply.md"},"th":"draft the reply"}', _IDLE_STEP])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "completed"
    assert any(
        item["event"] == "managed_writer_progress" and "Writing /me/reply.md" in item["detail"]
        for item in broadcasts
    )
    assert _live_state(task.id, activity.id) == before


def test_l4_claiming_a_detached_trigger_keeps_the_soft_block() -> None:
    from core.agent_loop.activity_scheduler import prepare_trigger_context
    from core.tasking.transitions import transition_task

    agent, task, activity = _working_agent_with_snapshot()
    transition_task(task.id, "blocked", reason="Blocked — no progress.", actor="BossMod",
                    status_note="Blocked — no progress.")
    before = _live_state(task.id, activity.id)

    prepare_trigger_context(agent.id, _event_trigger())

    assert _live_state(task.id, activity.id) == before
    # Unchanged for every other trigger: a claim still demotes the Soft-block.
    prepare_trigger_context(agent.id, {"type": "human_chat", "source_channel": "chat", "task_id": None})
    assert db.get_task(task.id).status == "active"


@pytest.mark.asyncio
async def test_l5_request_host_access_is_not_bound_to_the_paused_task(tmp_path: Path) -> None:
    from core.agent_loop.actions import execute_action, parse_action
    from tests.test_consent_origin import _host_file

    fixture = _host_file(tmp_path)
    agent, _peer, channel, task, activity = _channel_worker()
    before = _live_state(task.id, activity.id)
    parsed = parse_action(
        '{"act":"request_host_access","data":{"path":"%s","why":"Attach the report"},"th":"ask"}' % fixture
    )

    result = await execute_action(parsed, agent, db.get_agent_state(agent.id), _event_trigger())

    assert result["event"] == "host_path_consent_required"
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None and stored.task_id is None and stored.channel_id is None
    assert not any(item.consent_id == stored.id for item in db.list_channel_messages(channel.id))
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_l5_work_output_is_not_written_against_the_paused_task() -> None:
    from core.agent_loop.actions import execute_action
    from core.world.tilemap import DEFAULT_DESKS, get_room_at

    agent = db.create_agent("Charles", role="Build Engineer", connection_id=model_connection("test/mock"))
    task = _task(agent.id)
    # "accepted" is the state _handle_work would move to "active".
    activity = _freeze_live_work(agent, task, task_status="accepted")
    chair_x, chair_y = DEFAULT_DESKS[0]["chair_xy"]
    state = db.update_agent_state(agent.id, x=chair_x, y=chair_y)
    assert get_room_at(state.x, state.y)["room_type"] == "workspace"
    before = _live_state(task.id, activity.id)
    assert before[0] == "accepted"

    result = await execute_action({"action": "work", "output": "Replied to Alice."}, agent, state, _event_trigger())

    assert result == {"event": "agent_error", "detail": "No active work activity is bound", "agent_name": agent.name}
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_l5_a_delegation_is_not_filed_under_the_paused_task() -> None:
    from core.agent_loop.actions import execute_action

    agent, peer, _channel, task, activity = _channel_worker()
    before = _live_state(task.id, activity.id)

    result = await execute_action(
        {
            "action": "delegateTask",
            "agentId": peer.id,
            "taskTitle": "Pull the daily numbers",
            "taskDescription": "Alice asked for today's numbers by email.",
            "confirmSpecialtyMismatch": True,
        },
        agent,
        db.get_agent_state(agent.id),
        _event_trigger(),
    )

    assert result["event"] == "status_changed", result
    assert db.list_tasks(parent_task_id=task.id) == []
    delegated = db.list_tasks(assigned_to=peer.id)
    assert len(delegated) == 1 and delegated[0].parent_task_id is None
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_l5_peer_chat_is_not_steered_by_the_paused_work_lane() -> None:
    from core.agent_loop.actions import execute_action

    agent, peer, _channel, task, activity = _channel_worker()
    before = _live_state(task.id, activity.id)

    result = await execute_action(
        {"action": "message", "recipientType": "agent", "agentId": peer.id, "content": "Alice is asking for the numbers."},
        agent,
        db.get_agent_state(agent.id),
        _event_trigger(),
    )

    assert result["event"] == "message_sent", result
    assert _live_state(task.id, activity.id) == before


# ─── Revision 3: one work-binding seam replaces the per-site gates ───


@pytest.mark.asyncio
async def test_r3_a_plain_cli_consent_is_not_bound_to_the_paused_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import core.bm_cli.runtime as cli_runtime
    from core.agent_loop.actions import execute_action
    from core.bm_cli.consent_scope import current_consent_scope
    from tests.test_consent_origin import _host_file

    fixture = _host_file(tmp_path)
    agent, _peer, channel, task, activity = _channel_worker()
    before = _live_state(task.id, activity.id)
    scopes: list[Any] = []
    inner = cli_runtime._execute_bm_cli_inner

    def _recording_inner(*args: Any, **kwargs: Any):
        scopes.append(current_consent_scope())
        return inner(*args, **kwargs)

    monkeypatch.setattr(cli_runtime, "_execute_bm_cli_inner", _recording_inner)

    result = await execute_action(
        {"action": "bm_cli", "command": f"cat {fixture}"}, agent, db.get_agent_state(agent.id), _event_trigger(),
    )

    assert result["event"] == "host_path_consent_required", result
    # The CLI ran on a worker thread and still saw the detached binding.
    assert len(scopes) == 1 and scopes[0].agent_id == agent.id and scopes[0].task_id is None
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None and stored.task_id is None and stored.channel_id is None
    assert not any(item.consent_id == stored.id for item in db.list_channel_messages(channel.id))
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_r3_a_gate_deny_posts_no_blocked_line_on_the_task_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_loop.actions import execute_action
    from core.bm_cli.results import error_result
    from tests.test_blocked_origin import _jim_debra_channel, _thread_task

    jim, debra, channel = _jim_debra_channel()
    task = _thread_task(assignee_id=jim.id, channel_id=channel.id, requester_id=debra.id).task
    activity = _freeze_live_work(jim, task)
    before = _live_state(task.id, activity.id)
    events_before = [event.id for event in db.list_task_events(task.id)]

    def _host_deny(*_args: Any, **_kwargs: Any):
        return error_result(
            "write /host/report.md",
            "Host writes stay blocked. Work in the agent workspace copy at /me.",
            kind="host_deny",
        )

    monkeypatch.setattr("core.agent_loop.actions_cli.execute_bm_cli", _host_deny)

    result = await execute_action(
        {"action": "bm_cli", "command": "write /host/report.md", "content": "x\n"},
        jim,
        db.get_agent_state(jim.id),
        _event_trigger(),
    )

    # The deny still surfaces, named for the operator, off the task thread.
    assert "Blocked — host deny. @Human Operator" in str(result.get("origin_status_messages")), result
    assert result["auto_github"]["task_id"] is None
    assert not any("Blocked —" in (item.content or "") for item in db.list_channel_messages(channel.id))
    assert [event.id for event in db.list_task_events(task.id)] == events_before
    assert not any(item.get("agent_id") == debra.id for item in result.get("trigger_requests", []))
    assert not [
        row for row in db.list_agent_triggers(debra.id, status="queued")
        if row.get("trigger_type") in {"channel_message", "task_follow_up", "peer_message"}
    ]
    assert _live_state(task.id, activity.id) == before


@pytest.mark.parametrize("step", [
    '{"act":"walk","data":{"dst":"break"},"th":"stretch"}',
    '{"act":"mtg","data":{"mode":"room","aids":["x"],"topic":"email"},"th":"meet"}',
    '{"act":"mtg","data":{"mode":"remote","aids":["x"],"topic":"email"},"th":"call"}',
])
@pytest.mark.asyncio
async def test_r3_walk_and_meeting_actions_are_refused(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)
    # Each refusal is fed back within the repair budget; the third ends the turn.
    prompts = _script(monkeypatch, [step] * 3)

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert len(prompts) == 3
    assert "is not available while handling an extension event." in prompts[1][-1]["content"]
    assert outcome.trigger_status == "failed"
    assert "is not available while handling an extension event." in (outcome.diagnostic_error or "")
    assert db.get_activity(activity.id).status == "active"
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_r3_a_resume_stamped_detached_origin_runs_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)
    prompts = _script(monkeypatch, [_IDLE_STEP])
    trigger = {
        "type": "cli_approval_resolved",
        "source_channel": "system",
        "task_id": None,
        "detached_origin": True,
        "approval_request_id": "approval-1",
        "command": "pip install pytest",
        "status": "rejected",
        "decision_note": "Not needed for a reply.",
    }

    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed"
    assert outcome.result["detail"] == "Charles finished handling an extension event"
    # The paused task's frozen steps were not restored into the resume turn...
    assert not any("board result" == item["content"] for item in prompts[0])
    # ...and the resume turn's transcript was not frozen onto the paused task.
    assert _live_state(task.id, activity.id) == before


def _soft_blocked_worker():
    from core.tasking.transitions import transition_task

    agent, task, activity = _working_agent_with_snapshot()
    transition_task(task.id, "blocked", reason="Blocked — no progress.", actor="BossMod",
                    status_note="Blocked — no progress.")
    assert db.get_activity(activity.id).status == "active"
    return agent, task, activity


@pytest.mark.asyncio
async def test_r3_finalize_keeps_the_soft_block_for_detached_and_clears_it_for_attached() -> None:
    import time

    from core.agent_loop.outcomes import TurnOutcome
    from core.agent_loop.turn_helpers import _finalize_turn

    agent, task, _activity = _soft_blocked_worker()

    async def _finalize(trigger: dict[str, Any]) -> None:
        await _finalize_turn(
            agent=agent, trigger=trigger, trigger_type=trigger["type"], mode="work", model=None,
            model_source="test", initial_context_json=None,
            outcome=TurnOutcome.skipped(result={"event": "agent_updated", "detail": "", "agent_name": agent.name},
                                        error="skipped", steps=[]),
            start=time.monotonic(),
        )

    await _finalize(_event_trigger())
    assert db.get_task(task.id).status == "blocked"
    await _finalize({"type": "human_chat", "source_channel": "chat", "task_id": None})
    assert db.get_task(task.id).status == "active"


@pytest.mark.asyncio
async def test_r3_launching_a_detached_trigger_keeps_the_soft_block(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _soft_blocked_worker()
    before = _live_state(task.id, activity.id)
    row = db.create_agent_trigger(
        agent_id=agent.id, trigger_type="extension_event", source_channel="system", payload=_EVENT_PAYLOAD,
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    _script(monkeypatch, [_IDLE_STEP])
    dispatcher = TurnDispatcher()

    assert await dispatcher._launch_claimed_trigger(claimed) is True
    await dispatcher._active_turns[agent.id]

    assert db.get_agent_trigger(row.id).status == "completed"
    assert _live_state(task.id, activity.id) == before
    assert db.get_task(task.id).status == "blocked"


# ─── Revision 4: failure paths and resume provenance ───


class _RecordingServices:
    """Runtime services double: persists each resume the way the real enqueue does."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )


def _claimed_trigger(agent_id: str, trigger_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Queue and claim one trigger, shaped as the dispatcher hands it to a turn."""
    row = db.create_agent_trigger(
        agent_id=agent_id, trigger_type=trigger_type, source_channel="system", payload=payload,
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    return {
        **payload,
        "type": claimed.trigger_type,
        "trigger_id": claimed.id,
        "task_id": claimed.task_id,
        "source_channel": claimed.source_channel,
        "claim_generation": claimed.claim_generation,
    }


def _no_retries() -> None:
    db.set_setting("turn_failure_retry_limit", "0", "advanced")
    config.reload()


def _operator_dms(agent_id: str) -> list[str]:
    return [item.content for item in db.get_human_chat_thread(agent_id) if item.from_agent == agent_id]


@pytest.mark.asyncio
async def test_r4_detached_retry_exhaustion_leaves_the_live_work_and_tells_the_operator() -> None:
    agent, task, activity = _soft_blocked_worker()
    before = _live_state(task.id, activity.id)
    trigger = _claimed_trigger(agent.id, "extension_event", _EVENT_PAYLOAD)

    await TurnDispatcher()._supervise_failed_turn(
        agent=agent, trigger=trigger, failure_detail="Graph outage",
        not_retried_reason=NOT_RETRIED_NO_REPEAT_REASON,
    )

    assert db.get_agent_trigger(trigger["trigger_id"]).status == "failed"
    assert _live_state(task.id, activity.id) == before
    assert db.get_task(task.id).status == "blocked"
    assert db.get_activity(activity.id).status == "active"
    assert _operator_dms(agent.id) == [
        "I hit repeated runtime failures while handling an extension event and could not recover. "
        "Last error: Graph outage"
    ]


@pytest.mark.asyncio
async def test_r4_attached_retry_exhaustion_still_stalls_the_live_task() -> None:
    agent, task, activity = _working_agent_with_snapshot()
    trigger = _claimed_trigger(agent.id, "activity_resumed", {})

    await TurnDispatcher()._supervise_failed_turn(
        agent=agent, trigger=trigger, failure_detail="model error",
        not_retried_reason=NOT_RETRIED_NO_REPEAT_REASON,
    )

    assert db.get_task(task.id).status == "stalled"
    assert db.get_activity(activity.id).status == "cancelled"
    assert db.get_work_snapshot(activity.id) is None


@pytest.mark.asyncio
async def test_a_non_retryable_stall_says_not_retried() -> None:
    agent, task, _activity = _working_agent_with_snapshot()
    trigger = _claimed_trigger(agent.id, "activity_resumed", {})

    await TurnDispatcher()._supervise_failed_turn(
        agent=agent, trigger=trigger, failure_detail="model error",
        not_retried_reason=NOT_RETRIED_NO_REPEAT_REASON,
    )

    stalled = db.get_task(task.id)
    assert stalled.status == "stalled"
    assert stalled.status_note == "Not retried (a command that must not repeat had already run): model error"


@pytest.mark.asyncio
async def test_retry_exhaustion_keeps_the_exhausted_wording() -> None:
    agent, task, _activity = _working_agent_with_snapshot()
    trigger = _claimed_trigger(agent.id, "activity_resumed", {})
    _no_retries()

    await TurnDispatcher()._supervise_failed_turn(
        agent=agent, trigger=trigger, failure_detail="model error", not_retried_reason=None,
    )

    stalled = db.get_task(task.id)
    assert stalled.status == "stalled"
    assert stalled.status_note == "Runtime exhausted automatic retries: model error"


@pytest.mark.asyncio
async def test_a_missing_attachment_stall_names_the_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm.attachment_parts import AttachmentUnavailableError

    agent, task, _activity = _working_agent_with_snapshot()

    async def _gone(*_args: Any, **_kwargs: Any):
        raise AttachmentUnavailableError("Attachment gone no longer exists")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _gone)
    trigger = _claimed_trigger(agent.id, "activity_resumed", {})

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.status == "failed" and row.retry_count == 0
    stalled = db.get_task(task.id)
    assert stalled.status == "stalled"
    assert stalled.status_note == (
        "Not retried (an attached file is no longer available): Attachment gone no longer exists"
    )


@pytest.mark.asyncio
async def test_r4_a_detached_turn_raising_keeps_the_soft_block(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _soft_blocked_worker()
    before = _live_state(task.id, activity.id)
    _no_retries()

    async def _boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("Graph outage")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _boom)
    trigger = _claimed_trigger(agent.id, "extension_event", _EVENT_PAYLOAD)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    assert db.get_agent_trigger(trigger["trigger_id"]).status == "failed"
    assert _live_state(task.id, activity.id) == before
    assert db.get_task(task.id).status == "blocked"


@pytest.mark.asyncio
async def test_r4_an_attached_turn_raising_still_clears_the_soft_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm.client import LLMTimeoutError

    agent, task, _activity = _soft_blocked_worker()

    async def _timeout(*_args: Any, **_kwargs: Any):
        raise LLMTimeoutError(30.0)

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _timeout)
    trigger = _claimed_trigger(agent.id, "human_chat", {"content": "status?"})

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    assert db.get_task(task.id).status == "active"


def _enable_shell() -> None:
    from core.bm_cli.policy_engine import policy_engine

    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


_ATTACHED_TRIGGER = {"type": "human_chat", "source_channel": "chat", "task_id": None}


async def _open_approval(agent, trigger: dict[str, Any]):
    from core.agent_loop.actions import execute_action

    result = await execute_action(
        {"action": "bm_cli", "command": "pip install pytest"}, agent, db.get_agent_state(agent.id), trigger,
    )
    assert result["event"] == "cli_approval_required", result
    stored = db.get_cli_approval_request(result["approval_request_id"])
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_r4_an_approval_opened_detached_resumes_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.bm_cli.approvals import resume_cli_approval

    _enable_shell()
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)

    stored = await _open_approval(agent, _event_trigger())

    assert stored.detached_origin is True
    services = _RecordingServices()
    await resume_cli_approval(stored.id, approved=False, note="Not needed for a reply.", services=services)
    assert services.calls[0]["payload"]["detached_origin"] is True
    queued = [row for row in db.list_agent_triggers(agent.id, status="queued")
              if row.get("trigger_type") == "cli_approval_resolved"]
    assert len(queued) == 1
    claimed = db.claim_trigger(queued[0]["id"])
    assert claimed is not None
    prompts = _script(monkeypatch, [_IDLE_STEP])
    dispatcher = TurnDispatcher()

    assert await dispatcher._launch_claimed_trigger(claimed) is True
    await dispatcher._active_turns[agent.id]

    assert db.get_agent_trigger(claimed.id).status == "completed"
    assert not any("board result" == item["content"] for item in prompts[0])
    assert _live_state(task.id, activity.id) == before


@pytest.mark.asyncio
async def test_r4_an_approval_opened_attached_is_unchanged() -> None:
    from core.bm_cli.approvals import resume_cli_approval

    _enable_shell()
    agent, _task_row, _activity = _working_agent_with_snapshot()

    stored = await _open_approval(agent, dict(_ATTACHED_TRIGGER))

    assert stored.detached_origin is False
    services = _RecordingServices()
    await resume_cli_approval(stored.id, approved=False, note="No.", services=services)
    assert "detached_origin" not in services.calls[0]["payload"]


@pytest.mark.asyncio
async def test_r4_detached_and_attached_approvals_never_share_a_row() -> None:
    from core.bm_cli.approvals import resume_cli_approval

    _enable_shell()
    agent, _task_row, _activity = _working_agent_with_snapshot()

    attached = await _open_approval(agent, dict(_ATTACHED_TRIGGER))
    detached = await _open_approval(agent, _event_trigger())
    again = await _open_approval(agent, _event_trigger())

    assert attached.id != detached.id and again.id == detached.id
    assert (attached.command, attached.cwd) == (detached.command, detached.cwd)
    # Each origin has its own card, and one decision resumes only its own turn.
    assert db.has_approval_notification(attached.id) and db.has_approval_notification(detached.id)
    services = _RecordingServices()
    await resume_cli_approval(attached.id, approved=True, services=services)
    assert db.get_cli_approval_request(detached.id).status == "pending"
    assert len(services.calls) == 1


async def _open_consent(agent, trigger: dict[str, Any], path: Path):
    from core.agent_loop.actions import execute_action, parse_action

    parsed = parse_action(
        '{"act":"request_host_access","data":{"path":"%s","why":"Attach the report"},"th":"ask"}' % path
    )
    result = await execute_action(parsed, agent, db.get_agent_state(agent.id), trigger)
    assert result["event"] == "host_path_consent_required", result
    stored = db.get_consent_request(result["consent_request_id"])
    assert stored is not None
    return stored


@pytest.mark.asyncio
async def test_r4_host_path_consent_carries_its_origin_into_the_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from core.bm_cli.host_path_consent import resume_host_path_consent
    from tests.test_consent_origin import _host_file

    fixture = _host_file(tmp_path)
    agent, task, activity = _working_agent_with_snapshot()
    before = _live_state(task.id, activity.id)

    attached = await _open_consent(agent, dict(_ATTACHED_TRIGGER), fixture)
    detached = await _open_consent(agent, _event_trigger(), fixture)
    again = await _open_consent(agent, _event_trigger(), fixture)

    assert attached.detached_origin is False and detached.detached_origin is True
    assert attached.id != detached.id and again.id == detached.id
    services = _RecordingServices()
    await resume_host_path_consent(detached.id, decision="deny", services=services, note="Not for email.")
    # The deny also resolves the attached waiter in the same scope, each with its own origin.
    by_id = {call["payload"]["consent_request_id"]: call["payload"] for call in services.calls}
    assert by_id[detached.id]["detached_origin"] is True
    assert "detached_origin" not in by_id[attached.id]
    queued = [row for row in db.list_agent_triggers(agent.id, status="queued")
              if row.get("trigger_type") == "host_path_consent_resolved"
              and json.loads(row["payload"])["consent_request_id"] == detached.id]
    assert len(queued) == 1
    claimed = db.claim_trigger(queued[0]["id"])
    assert claimed is not None
    prompts = _script(monkeypatch, [_IDLE_STEP])
    dispatcher = TurnDispatcher()

    assert await dispatcher._launch_claimed_trigger(claimed) is True
    await dispatcher._active_turns[agent.id]

    assert db.get_agent_trigger(claimed.id).status == "completed"
    assert not any("board result" == item["content"] for item in prompts[0])
    assert _live_state(task.id, activity.id) == before


def test_r4_the_migration_adds_detached_origin_to_existing_tables() -> None:
    from db.connection import _apply_migrations, get_connection

    agent = db.create_agent("Charles", role="Build Engineer")
    approval = db.create_cli_approval_request(agent_id=agent.id, command="pip install pytest", cwd="/me")
    consent = db.create_consent_request(agent_id=agent.id, path="/srv/a", grant_root="/srv", reason="why")
    con = get_connection()
    for table in ("cli_approval_requests", "host_path_consent_requests"):
        con.execute(f"ALTER TABLE {table} DROP COLUMN detached_origin")

    _apply_migrations(con)

    for table in ("cli_approval_requests", "host_path_consent_requests"):
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "detached_origin" in columns
    # Rows from before the column read as attached, which is what they were.
    assert db.get_cli_approval_request(approval.id).detached_origin is False
    assert db.get_consent_request(consent.id).detached_origin is False


@pytest.mark.parametrize("kind", ["shell_executor", "nest_git", "workspace_preference"])
def test_r4_other_consent_cards_never_share_a_row_across_origins(kind: str, tmp_path: Path) -> None:
    from core.agent_loop.work_binding import bind_turn
    from core.bm_cli.nest_git_consent import request_nest_git_consent
    from core.bm_cli.parser import parse_cli_command
    from core.bm_cli.shell_executor_consent import request_shell_executor_consent
    from core.bm_cli.workspace_preference import request_workspace_preference
    from tests.test_consent_origin import _host_file

    fixture = _host_file(tmp_path)
    agent = db.create_agent("Charles", role="Build Engineer", connection_id=model_connection("test/mock"))

    def _open(trigger: dict[str, Any]):
        with bind_turn(agent.id, trigger):
            if kind == "workspace_preference":
                result = request_workspace_preference(
                    agent=agent, raw_path=str(fixture), command=f"write {fixture}", content="x\n",
                    cwd="/me", task_id=None,
                )
            else:
                opener = request_shell_executor_consent if kind == "shell_executor" else request_nest_git_consent
                result = opener(
                    agent=agent, parsed=parse_cli_command("git push"), content=None, cwd="/me",
                    task_id=None, channel_id=None,
                )
        assert result.consent_request_id, result
        stored = db.get_consent_request(result.consent_request_id)
        assert stored is not None and stored.card_kind == kind
        return stored

    attached = _open(dict(_ATTACHED_TRIGGER))
    detached = _open(_event_trigger())

    assert attached.detached_origin is False and detached.detached_origin is True
    assert attached.id != detached.id
    assert _open(_event_trigger()).id == detached.id
    assert _open(dict(_ATTACHED_TRIGGER)).id == attached.id
    # Each origin has its own card to resolve.
    assert db.has_consent_notification(attached.id) and db.has_consent_notification(detached.id)


def test_r4_needs_lists_each_origin_as_its_own_item() -> None:
    from api.routes.needs import _approval_needs, _consent_needs

    agent = db.create_agent("Charles", role="Build Engineer")
    rows = [
        db.create_cli_approval_request(
            agent_id=agent.id, command="pip install pytest", cwd="/me", detached_origin=detached,
        )
        for detached in (False, True)
    ]
    consents = [
        db.create_consent_request(
            agent_id=agent.id, path="/srv/a", grant_root="/srv", reason="why", detached_origin=detached,
        )
        for detached in (False, True)
    ]

    assert sorted(item["grouped_ids"] for item in _approval_needs({})) == sorted([row.id] for row in rows)
    assert sorted(item["grouped_ids"] for item in _consent_needs({})) == sorted([row.id] for row in consents)
