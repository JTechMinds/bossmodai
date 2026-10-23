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
from core.agent_loop.loop import run_turn
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.agent_loop.task_origin_mirrors import BOSS_CANCEL_REASON
from core.agent_loop.work_snapshot import freeze_work_turn, saved_chars, split_steps, transcript_chars
from core.agent_loop.work_summary import read_summary_limits
from core.bm_cli.runtime import execute_bm_cli
from core.llm.client import LLMError, LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import add_checklist_items, create_or_bind_task
from core.tasking.service import cancel_task_as_operator


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


# ─── Context across check-offs, and the budget boundary with the step summary ───

_STATUS_STEP = '{"act":"cli","data":{"cmd":"status"},"th":"check status"}'
_BOARD_STEP = '{"act":"cli","data":{"cmd":"my-board"},"th":"check board"}'
_CHECK_OFF_STEP = '{"act":"cli","data":{"cmd":"checklist done 1","body":"2 issues, in /projects/app/audit.md"},"th":"part 1 done"}'


def _script_prompts(
    monkeypatch: pytest.MonkeyPatch, contents: list[str | Exception], *, on_call=None,
) -> list[list[dict[str, Any]]]:
    """Like ``_script``, keeping the messages each call was sent.

    An ``Exception`` in ``contents`` is raised by that call; ``on_call`` gets
    the 1-based call number before the reply is returned.
    """
    queue = list(contents)
    prompts: list[list[dict[str, Any]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append([dict(item) for item in kwargs.get("messages") or []])
        if on_call is not None:
            on_call(len(prompts))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return prompts


def _queued_resume(agent_id: str, task_id: str) -> dict[str, Any]:
    """Claim the one queued resume for the task, as the dispatcher would."""
    (row,) = [
        item for item in db.list_queued_triggers()
        if item.agent_id == agent_id and item.trigger_type == "activity_resumed" and item.task_id == task_id
    ]
    return _claimed_payload(row)


def _start_resume(agent_id: str, task_id: str) -> dict[str, Any]:
    db.create_agent_trigger(
        agent_id=agent_id, trigger_type="activity_resumed", source_channel="work",
        payload={"content": "Resume work."}, task_id=task_id,
    )
    return _queued_resume(agent_id, task_id)


def _frozen_steps(agent, *steps: str) -> None:
    """Freeze earlier steps onto the agent's live work, each with a short result."""
    activity = activity_runtime.get_active_work_activity(agent.id)
    context: list[dict[str, Any]] = []
    for step in steps:
        context.extend([{"role": "assistant", "content": step}, {"role": "user", "content": f"result of {step}"}])
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=context,
        fingerprints=["status"], no_progress_checkpoints=1,
    )


def _queue_status_question(agent_id: str) -> None:
    """A waiting message: the running execution turn pauses after its current step."""
    db.create_agent_trigger(
        agent_id=agent_id, trigger_type="human_chat", source_channel="chat",
        payload={"content": "Status?", "from_name": "Human Operator"},
    )


_SUMMARY_SYSTEM_HEAD = "Condense an AI agent's earlier working steps"


@pytest.mark.asyncio
async def test_a_check_off_keeps_the_turn_and_its_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    add_checklist_items(task.id, ["Read the spec", "Write the tests"])
    state = db.get_agent_state(agent.id)
    assert execute_bm_cli(agent, state, "write /me/notes/spec.md", "SPEC: fn alpha(x: u32) -> Beta").ok
    assert execute_bm_cli(agent, state, "write /me/notes/types.md", "TYPES: struct Beta { id: u64 }").ok
    activity = activity_runtime.get_active_work_activity(agent.id)
    read_spec = '{"act":"cli","data":{"cmd":"rr /me/notes/spec.md 1:1"},"th":"read the spec"}'
    check_off = '{"act":"cli","data":{"cmd":"checklist done 1","body":"spec read"},"th":"part 1 done"}'
    read_types = '{"act":"cli","data":{"cmd":"rr /me/notes/types.md 1:1"},"th":"read the types"}'
    # A message arriving during the third call pauses the work after that step;
    # the fake refuses a fourth call.
    prompts = _script_prompts(
        monkeypatch, [read_spec, check_off, read_types],
        on_call=lambda call: _queue_status_question(agent.id) if call == 3 else None,
    )

    payload = _start_resume(agent.id, task.id)
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), payload)

    assert db.get_agent_trigger(payload["trigger_id"]).status == "completed"
    assert len(prompts) == 3, "the check-off did not end the turn"
    third = "\n".join(str(item["content"]) for item in prompts[2])
    assert "SPEC: fn alpha(x: u32) -> Beta" in third, "the first read is still in view after the check-off"
    snapshot = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in snapshot.transcript if item["role"] == "assistant"] == [
        read_spec, check_off, read_types,
    ]
    saved = "\n".join(str(item["content"]) for item in snapshot.transcript)
    assert "SPEC: fn alpha(x: u32) -> Beta" in saved and "TYPES: struct Beta { id: u64 }" in saved
    stored = db.get_task(task.id)
    assert [item.status for item in stored.checklist] == ["done", "open"]
    assert stored.last_progress_at is not None, "a check-off is still progress"


@pytest.mark.asyncio
async def test_a_check_off_in_a_decision_turn_clears_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    add_checklist_items(task.id, ["Audit core/tasking", "Audit the plugins"])
    _frozen_steps(agent, _STATUS_STEP, _BOARD_STEP)
    activity = activity_runtime.get_active_work_activity(agent.id)
    before = db.get_work_snapshot(activity.id)
    row = db.create_agent_trigger(
        agent_id=agent.id, trigger_type="human_chat", source_channel="chat",
        payload={"content": "Skip the plugins part.", "from_name": "Human Operator"},
    )
    _script(monkeypatch, [
        '{"act":"cli","data":{"cmd":"checklist skip 2","body":"The boss dropped the plugins."},"th":"skip it"}',
        '{"act":"reply","intent":"status","msg":"Done, the plugins are off the list.","work_commit":true,"th":"ack"}',
    ])

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _claimed_payload(row))

    assert db.get_agent_trigger(row.id).status == "completed"
    assert [item.status for item in db.get_task(task.id).checklist] == ["open", "skipped"]
    after = db.get_work_snapshot(activity.id)
    assert after.transcript == before.transcript
    assert after.fingerprints == before.fingerprints
    assert after.no_progress_checkpoints == before.no_progress_checkpoints


def _over_budget_work(agent) -> list[str]:
    """Budget 1500 with a 66% trim; freeze four 357-character steps (1428, under it)."""
    activity = activity_runtime.get_active_work_activity(agent.id)
    db.set_setting("work_snapshot_max_chars", "1500", "simulation")
    db.set_setting("work_snapshot_trim_percent", "66", "simulation")
    config.reload()
    earlier = [
        '{"act":"cli","data":{"cmd":"cat /me/f%d.md"},"th":"read"}' % index for index in range(4)
    ]
    context: list[dict[str, Any]] = []
    for step in earlier:
        context.extend([{"role": "assistant", "content": step}, {"role": "user", "content": "y" * 300}])
    freeze_work_turn(agent=agent, activity=activity, initial_len=0, context=context, fingerprints=[],
                     no_progress_checkpoints=0)
    return earlier


_SUMMARY_REPLY = "- read /me/f0.md to /me/f3.md: 300 y each\n- ran status"


@pytest.mark.asyncio
async def test_a_turn_past_the_budget_ends_saved_untrimmed_and_the_next_turn_summarizes_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    earlier = _over_budget_work(agent)
    # One reply only: the saved work starts under the budget, so there is no
    # summary call, and the status step crosses the budget, so the turn ends.
    prompts = _script_prompts(monkeypatch, [_STATUS_STEP])

    payload = _start_resume(agent.id, task.id)
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), payload)

    assert len(prompts) == 1
    assert db.get_agent_trigger(payload["trigger_id"]).status == "completed"
    untrimmed = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in untrimmed.transcript if item["role"] == "assistant"] == [
        *earlier, _STATUS_STEP,
    ], "the freeze appends; nothing is dropped"
    assert (untrimmed.step_summary, untrimmed.summarized_steps) == ("", 0)
    assert saved_chars(untrimmed) > 1500
    assert len(_resumes(agent.id, task.id)) == 1

    # The next resumed turn: its first model call writes the step summary.
    # A waiting message pauses it after its first step.
    _queue_status_question(agent.id)
    prompts = _script_prompts(monkeypatch, [_SUMMARY_REPLY, _BOARD_STEP])
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _queued_resume(agent.id, task.id))

    assert len(prompts) == 2
    summary_call, first_step = prompts
    assert summary_call[0]["role"] == "system" and summary_call[0]["content"].startswith(_SUMMARY_SYSTEM_HEAD)
    assert earlier[0] in summary_call[1]["content"], "the oldest step is folded verbatim"
    summarized = db.get_work_snapshot(activity.id)
    assert summarized.step_summary == _SUMMARY_REPLY
    folded = summarized.summarized_steps
    # The status step alone fits the target with the summary limit; one more
    # 357-character step would not, so every earlier step is folded.
    assert folded == len(earlier)
    kept = [message for group in split_steps(untrimmed.transcript)[folded:] for message in group]
    limits = read_summary_limits()
    assert limits.cap + transcript_chars(kept) <= limits.target

    # Its first execution call: preamble, then the summary, then the kept steps, then the resume.
    heads = [
        index for index, item in enumerate(first_step)
        if str(item["content"]).startswith(f"Summary of your {folded} earlier steps on this work.")
    ]
    assert len(heads) == 1
    head = heads[0]
    assert first_step[head]["role"] == "user"
    assert first_step[head]["content"].endswith(_SUMMARY_REPLY)
    assert all(item["role"] != "assistant" for item in first_step[:head]), "only the preamble precedes it"
    assert [item["content"] for item in first_step[head + 1:-1]] == [item["content"] for item in kept]
    assert "Continue from where you left off" in first_step[-1]["content"]
    assert [item["content"] for item in summarized.transcript[:len(kept)]] == [item["content"] for item in kept]

    # The diagnostics row counts the summary call, as its first step.
    detail = db.get_diagnostic(db.get_diagnostics(agent.id, limit=1)[0]["id"])
    steps = detail["steps"]
    assert [step["action_name"] for step in steps] == ["summarize_steps", "bm_cli"]
    assert steps[0]["step_index"] == 0 and steps[0]["raw_response"] == _SUMMARY_REPLY
    assert detail["total_tokens"] == sum(step["total_tokens"] for step in steps) == 24
    assert detail["prompt_tokens"] == 16 and detail["completion_tokens"] == 8


@pytest.mark.asyncio
async def test_a_failed_summary_call_fails_the_turn_before_any_step(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    _over_budget_work(agent)
    _frozen_steps(agent, _STATUS_STEP)
    before = db.get_work_snapshot(activity.id)
    assert saved_chars(before) > 1500
    # One call only: the fake refuses a second, so no execution step can run.
    prompts = _script_prompts(monkeypatch, [LLMError("provider unreachable")])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert outcome.trigger_status == "failed"
    assert outcome.retryable is True
    assert outcome.diagnostic_error == "Step summary failed: The step summary call failed: provider unreachable"
    assert outcome.steps == []
    assert (outcome.prompt_tokens, outcome.completion_tokens, outcome.total_tokens) == (0, 0, 0)
    assert len(prompts) == 1 and prompts[0][0]["content"].startswith(_SUMMARY_SYSTEM_HEAD)
    assert db.get_work_snapshot(activity.id).model_dump() == before.model_dump(), "the snapshot is untouched"


@pytest.mark.asyncio
async def test_a_failed_shorter_request_records_the_first_call_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    _over_budget_work(agent)  # the summary limit is 150 characters
    _frozen_steps(agent, _STATUS_STEP)
    before = db.get_work_snapshot(activity.id)
    # An over-limit reply, then the shorter request fails: two calls, no execution step.
    prompts = _script_prompts(monkeypatch, ["x" * 200, LLMError("stalled")])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert outcome.trigger_status == "failed" and outcome.retryable is True
    assert outcome.diagnostic_error == "Step summary failed: The step summary call failed: stalled"
    assert (outcome.prompt_tokens, outcome.completion_tokens, outcome.total_tokens) == (8, 4, 12)
    assert len(prompts) == 2
    assert db.get_work_snapshot(activity.id).model_dump() == before.model_dump()


class _ActivityRecorder(NullRuntimeEventSink):
    """Keeps every broadcast activity event."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def broadcast_activity(
        self, event: str, detail: str, agent_name: str | None = None, extra: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_work_cancelled_during_the_summary_skips_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    _over_budget_work(agent)
    _frozen_steps(agent, _STATUS_STEP)
    recorder = _ActivityRecorder()
    runtime_events.set_sink(recorder)
    # The operator cancels the task while the summary call is out; the fake
    # refuses a second call, so no execution step can run.
    prompts = _script_prompts(
        monkeypatch, ["- read /me/f0.md to /me/f3.md"],
        on_call=lambda _call: cancel_task_as_operator(task.id, reason=BOSS_CANCEL_REASON),
    )

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert outcome.trigger_status == "skipped"
    assert outcome.diagnostic_error == "the work ended while its earlier steps were being summarized"
    assert outcome.steps == []
    assert len(prompts) == 1 and prompts[0][0]["content"].startswith(_SUMMARY_SYSTEM_HEAD)
    assert "agent_error" not in recorder.events
    assert "agent_updated" in recorder.events
    assert db.get_task(task.id).status == "cancelled"
    assert db.get_work_snapshot(activity.id) is None


@pytest.mark.asyncio
async def test_a_single_step_over_the_budget_still_moves_on(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    # Smaller than any one CLI result: every turn runs exactly one step.
    db.set_setting("work_snapshot_max_chars", "50", "simulation")
    config.reload()

    prompts = _script_prompts(monkeypatch, [_STATUS_STEP])
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))
    assert len(prompts) == 1
    first = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in first.transcript if item["role"] == "assistant"] == [_STATUS_STEP]

    prompts = _script_prompts(monkeypatch, [_BOARD_STEP])
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _queued_resume(agent.id, task.id))
    assert len(prompts) == 1
    second = db.get_work_snapshot(activity.id)
    # One step alone over the budget cannot be folded (no summary call): the
    # freeze appends, and the next turn's summary folds the older step.
    assert [item["content"] for item in second.transcript if item["role"] == "assistant"] == [
        _STATUS_STEP, _BOARD_STEP,
    ]
    assert second.step_summary == ""
    assert len(_resumes(agent.id, task.id)) == 1



def _cat_step(index: int) -> str:
    return '{"act":"cli","data":{"cmd":"cat /me/notes/f%d.md"},"th":"read part %d"}' % (index, index)


@pytest.mark.asyncio
async def test_after_a_budget_boundary_the_next_turn_summarizes_then_runs_a_run_of_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression for folding only to the budget: every later turn then ran one step.

    Each step reads a same-size file, so every step is the same size. The
    budget is 4.5 steps, the fold keeps 33% of it (about 1.5 steps) for the
    summary limit (10%) plus the kept steps, so after one budget boundary the
    next turn summarizes first and then has room for several steps before it
    reaches the budget again.
    """
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    state = db.get_agent_state(agent.id)
    for index in range(1, 20):
        assert execute_bm_cli(agent, state, f"write /me/notes/f{index:d}.md", "x" * 400).ok

    # Measure one step: a turn paused by a waiting message after its first step.
    _script_prompts(monkeypatch, [_cat_step(1)])
    _queue_status_question(agent.id)
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))
    db.delete_queued_triggers(agent.id)
    step = sum(len(item["content"]) for item in db.get_work_snapshot(activity.id).transcript)
    budget = step * 9 // 2
    db.set_setting("work_snapshot_max_chars", str(budget), "simulation")
    db.set_setting("work_snapshot_trim_percent", "33", "simulation")
    config.reload()

    # The first boundary: the saved step plus four more passes 4.5 steps. The
    # saved work started under the budget, so there was no summary call.
    first = _script_prompts(monkeypatch, [_cat_step(index) for index in range(2, 10)])
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))
    assert len(first) == 4
    untrimmed = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in untrimmed.transcript if item["role"] == "assistant"] == [
        _cat_step(index) for index in range(1, 6)
    ]
    assert untrimmed.step_summary == ""

    # The next turn folds first, then has room again: more than one step before the next boundary.
    second = _script_prompts(
        monkeypatch, ["- read /me/notes/f1.md to f4.md: 400 x each", *[_cat_step(index) for index in range(6, 14)]],
    )
    payload = _queued_resume(agent.id, task.id)
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), payload)
    assert second[0][0]["content"].startswith(_SUMMARY_SYSTEM_HEAD), "the summary is the turn's first call"
    steps_run = len(second) - 1
    assert steps_run >= 2, "a fold to just under the budget would end this turn after one step"
    assert steps_run < 8, "the turn still ended at the budget"
    assert db.get_agent_trigger(payload["trigger_id"]).status == "completed"
    again = db.get_work_snapshot(activity.id)
    assert again.summarized_steps == 4 and again.step_summary.startswith("- read /me/notes/f1.md")
    groups = split_steps(again.transcript)
    kept = [message for group in groups[: len(groups) - steps_run] for message in group]
    limits = read_summary_limits()
    assert limits.cap + transcript_chars(kept) <= limits.target
    assert len(_resumes(agent.id, task.id)) == 1
