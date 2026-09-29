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
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.dispatcher import TurnDispatcher
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
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, model_work="test/mock")
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


@pytest.mark.parametrize("step", [
    '{"act":"done","data":{"sum":"finished"},"th":"done"}',
    '{"act":"wait","data":{"why":"waiting on Alice"},"th":"wait"}',
    '{"act":"block","data":{"why":"blocked on access"},"th":"block"}',
])
@pytest.mark.asyncio
async def test_task_state_actions_are_refused(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    agent, task, activity = _working_agent_with_snapshot()
    before = _snapshot_bytes(activity.id)
    _script(monkeypatch, [step])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

    assert outcome.trigger_status == "failed"
    assert "your task is paused unchanged" in (outcome.diagnostic_error or "")
    assert db.get_task(task.id).status == "active"
    assert db.get_activity(activity.id).status == "active"
    assert _snapshot_bytes(activity.id) == before


@pytest.mark.asyncio
async def test_idle_does_not_complete_a_meeting(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Iris", role="Researcher", desk_x=1, desk_y=1, model_work="test/mock")
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

    agent = db.create_agent("Gerry", role="Engineer", model_work="test/mock")
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

    agent = db.create_agent("Charles", role="Build Engineer", model_work="test/mock")
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
    _script(monkeypatch, [step])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _event_trigger())

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
