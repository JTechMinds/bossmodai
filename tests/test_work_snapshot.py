"""Fix C — freeze the working transcript at every pause, view it, restore it.

The chat turn reads a code-rendered, read-only view of the frozen steps; an
execution resume replays them under a freshly built preamble. Saved work is
measured in tokens; tests that check the budget arithmetic count one token
per character (``_char_tokens``) so the sizes stay exact.
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
from core.boss import boss_label
from core.agent_loop import activity_runtime
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.turn_context import _get_current_activity, _get_current_task
from core.agent_loop.work_snapshot import (
    freeze_work_turn,
    interlude_tokens,
    render_paused_work_for_agent,
    render_paused_work_view,
    restore_work_turn,
    saved_tokens,
    transcript_over_budget,
    transcript_tokens,
)
from core.bm_cli.results import CLI_TOOL_RESULT_BEGIN
from core.llm import context_builder
from core.llm.client import LLMError, LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.llm.client import count_text_tokens
from core.tasking import create_or_bind_task
from tests._context_budget import set_context_budget

_STATUS_STEP = '{"act":"cli","data":{"cmd":"status"},"th":"check status"}'
_BOARD_STEP = '{"act":"cli","data":{"cmd":"my-board"},"th":"check board"}'
_VIEW_HEAD = "YOUR PAUSED WORK"


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


def _task(agent_id: str, title: str = "Execute the 9 fixes"):
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
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _char_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Count one token per character in work_snapshot, so budget arithmetic is exact."""
    monkeypatch.setattr("core.agent_loop.work_snapshot.count_text_tokens", len)


def _working_agent():
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    task = _task(agent.id)
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    assert activity is not None
    return agent, task, activity


def _queue_interrupt(agent_id: str, content: str = "Status?") -> None:
    db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": content, "from_name": "Human Operator"},
    )


def _script(monkeypatch: pytest.MonkeyPatch, contents: list[str], *, on_call=None) -> list[list[dict[str, str]]]:
    queue = list(contents)
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append([dict(item) for item in kwargs.get("messages") or []])
        if on_call is not None:
            on_call(len(prompts))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item, model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return prompts


def _resume_trigger(task_id: str, content: str = "Resume work.") -> dict[str, Any]:
    return {"type": "activity_resumed", "task_id": task_id, "content": content, "source_channel": "work"}


async def _interrupted_step(monkeypatch, agent, task, step: str, *, interrupt: str = "Status?"):
    state = db.get_agent_state(agent.id)
    prompts = _script(monkeypatch, [step], on_call=lambda call: _queue_interrupt(agent.id, interrupt))
    await run_turn(agent, state, _resume_trigger(task.id))
    return prompts


def _decision_context(agent, *, paused_work_view: str | None) -> list[dict[str, str]]:
    """A decision turn's prompt; the paused-work view is an input, rendered by the caller (turn_prompt)."""
    state = db.get_agent_state(agent.id)
    turn = context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger={"type": "human_chat", "content": "Status?", "from_name": "Human Operator"},
        conversation_history=[{"from_agent": HUMAN_SENDER_ID, "from_name": "Human Operator", "content": "Earlier ask"}],
        prompt_notifications=[],
        reference_materials=[],
        current_activity=_get_current_activity(agent.id),
        current_task=_get_current_task(agent.id),
        contract_kind="decision",
        paused_work_view=paused_work_view,
    )
    return context_builder.build_context(turn)


@pytest.mark.asyncio
async def test_interrupt_freezes_only_the_working_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent()
    prompts = await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    assert len(prompts) == 1, "freezing makes no extra model call"
    snapshot = db.get_work_snapshot(activity.id)
    assert snapshot is not None
    roles = [message["role"] for message in snapshot.transcript]
    assert "system" not in roles, "the preamble is never frozen"
    assert snapshot.transcript[0] == {"role": "assistant", "content": _STATUS_STEP}
    assert snapshot.transcript[1]["content"].startswith(CLI_TOOL_RESULT_BEGIN)
    assert snapshot.fingerprints == ["status"]


@pytest.mark.asyncio
async def test_decision_turn_gets_the_plain_text_view_before_history(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, _activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    paused = render_paused_work_for_agent(agent.id)
    assert paused is not None and paused.startswith(_VIEW_HEAD)
    context = _decision_context(agent, paused_work_view=paused)
    views = [index for index, item in enumerate(context) if item["content"].startswith(_VIEW_HEAD)]
    assert len(views) == 1
    view_index = views[0]
    view = context[view_index]
    assert view["role"] == "system"
    assert "- you ran: status" in view["content"]
    assert '{"act":"cli"' not in view["content"], "execution JSON is rendered as a command line"
    assert "Execute the 9 fixes" in view["content"]
    history_index = next(i for i, item in enumerate(context) if "Earlier ask" in item["content"])
    assert view_index < history_index
    assert all(item["role"] == "system" for item in context[:view_index])
    assert view["content"] == paused, "build_context renders the view it is given, as given"


@pytest.mark.asyncio
async def test_a_decision_turn_without_a_view_renders_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_context no longer looks the paused work up itself: no view given, none shown."""
    agent, task, _activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    assert render_paused_work_for_agent(agent.id) is not None, "there is paused work to show"
    context = _decision_context(agent, paused_work_view=None)
    assert not any(item["content"].startswith(_VIEW_HEAD) for item in context)


@pytest.mark.asyncio
async def test_execution_turns_do_not_get_the_view(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, _activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    paused = render_paused_work_for_agent(agent.id)
    assert paused is not None
    state = db.get_agent_state(agent.id)
    turn = context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger=_resume_trigger(task.id),
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=[],
        current_activity=_get_current_activity(agent.id),
        current_task=_get_current_task(agent.id),
        contract_kind="execution",
        paused_work_view=paused,
    )
    assert not any(item["content"].startswith(_VIEW_HEAD) for item in context_builder.build_context(turn))


def test_view_shows_only_the_most_recent_steps_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    steps: list[dict[str, str]] = []
    for index in range(6):
        steps.append({"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f%d.md"},"th":"r"}' % index})
        steps.append({"role": "user", "content": f"contents of file {index} " + "x" * 200})
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0
    )
    assert set_context_budget(usable=2400).asked == 600
    view = render_paused_work_view(snapshot, task_title="Execute the 9 fixes")
    assert view is not None
    assert "cat /me/f5.md" in view
    assert "cat /me/f0.md" not in view
    assert "earlier steps are not shown" in view
    assert "Summary of your earlier steps" not in view, "no summary block without a summary"


def test_view_shows_the_summary_first_then_the_newest_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """When you ask: the step summary, whole, then the newest steps that fit the rest."""
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    steps = _steps(6, result_chars=200)
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    summary = "- read f0 to f9; the config sets the port\n" + "- " + "s" * 300
    snapshot = db.save_step_summary(activity.id, transcript=steps, step_summary=summary, summarized_steps=10)
    assert set_context_budget(usable=2400).asked == 600

    view = render_paused_work_view(snapshot, task_title="Execute the 9 fixes")

    assert view is not None
    head = "Summary of your earlier steps (10 steps, no longer shown word for word):\n" + summary
    assert head in view
    assert view.index(head) < view.index("Your newest steps:") < view.index("cat /me/f5.md")
    # 600 tokens less the ~350 the summary takes leaves room for the newest step only.
    assert "cat /me/f4.md" not in view
    assert "(5 earlier steps are not shown.)" in view


def test_the_newest_step_over_the_rest_keeps_its_leading_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    result = "\n".join(f"line {index} " + "z" * 40 for index in range(40))
    steps = [
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/big.md"},"th":"r"}'},
        {"role": "user", "content": result},
    ]
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    assert set_context_budget(usable=2400).asked == 600

    view = render_paused_work_view(snapshot, task_title="Execute the 9 fixes")

    assert view is not None
    assert "- you ran: cat /me/big.md" in view
    assert "line 0 " in view and "line 39 " not in view
    assert view.endswith("(output truncated for space)")


@pytest.mark.asyncio
async def test_interlude_is_recorded_with_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP, interrupt="How far along?")
    row = next(
        item for item in db.list_queued_triggers() if item.agent_id == agent.id and item.trigger_type == "human_chat"
    )
    claimed = db.claim_trigger(row.id)
    payload = json.loads(claimed.payload)
    payload.update({"type": claimed.trigger_type, "trigger_id": claimed.id, "task_id": claimed.task_id,
                    "source_channel": claimed.source_channel, "claim_generation": claimed.claim_generation})
    _script(monkeypatch, ['{"say":"Checked status; starting fix 1 next.","actions":[],"work_commit":true}'])
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), payload)
    snapshot = db.get_work_snapshot(activity.id)
    assert [item.model_dump() for item in snapshot.interludes] == [
        {"from_name": boss_label(), "content": "How far along?", "reply": "Checked status; starting fix 1 next."}
    ]
    assert any(
        item.trigger_type == "activity_resumed" and item.task_id == task.id
        for item in db.list_queued_triggers()
    ), "the invariant queues the resume after recording the interlude"


@pytest.mark.asyncio
async def test_resume_replays_frozen_steps_once_across_two_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    db.append_work_interlude(activity.id, {"from_name": "Human Operator", "content": "Also fix the css.", "reply": "Will do."})
    db.delete_queued_triggers(agent.id)

    prompts = await _interrupted_step(monkeypatch, agent, task, _BOARD_STEP)
    resumed = prompts[0]
    assert resumed[0]["role"] == "system", "the preamble is rebuilt fresh"
    assert sum(1 for item in resumed if item["content"] == _STATUS_STEP) == 1
    last = resumed[-1]
    assert last["role"] == "user"
    assert "[Human Operator]: Also fix the css." in last["content"]
    assert "You replied: Will do." in last["content"]
    assert "Continue from where you left off" in last["content"]
    frozen_index = next(i for i, item in enumerate(resumed) if item["content"] == _STATUS_STEP)
    assert frozen_index < len(resumed) - 1, "the frozen steps come before the resume message"

    snapshot = db.get_work_snapshot(activity.id)
    assistant_steps = [item["content"] for item in snapshot.transcript if item["role"] == "assistant"]
    assert assistant_steps == [_STATUS_STEP, _BOARD_STEP]
    assert snapshot.fingerprints == ["status", "my-board"]


@pytest.mark.asyncio
async def test_approval_result_lands_after_the_restored_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, _activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    db.delete_queued_triggers(agent.id)
    prompts = _script(monkeypatch, ['{"act":"cli","data":{"cmd":"my-board"},"th":"next"}'],
                      on_call=lambda call: _queue_interrupt(agent.id))
    await run_turn(
        agent,
        db.get_agent_state(agent.id),
        {
            "type": "cli_approval_resolved",
            "task_id": task.id,
            "status": "rejected",
            "command": "pytest -q",
            "decision_note": "Not now.",
            "source_channel": "work",
        },
    )
    messages = prompts[0]
    frozen = next(i for i, item in enumerate(messages) if item["content"] == _STATUS_STEP)
    rejected = next(i for i, item in enumerate(messages) if "rejected by the boss" in item["content"])
    assert frozen < rejected
    assert rejected == len(messages) - 2, "the approval result and its follow-up close the context"


def test_freeze_only_appends_even_over_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    assert set_context_budget(usable=1500, work_trim=88).work == 900
    context = [{"role": "system", "content": "preamble"}, *_steps(5, result_chars=250)]
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=1, context=context, fingerprints=[], no_progress_checkpoints=0
    )
    assert snapshot.transcript == context[1:], "nothing is dropped at a pause, even over the budget"
    assert saved_tokens(snapshot) > 900

    # A later freeze appends to the stored steps and keeps the step summary as stored.
    db.save_step_summary(
        activity.id, transcript=snapshot.transcript, step_summary="- read f0 to f4", summarized_steps=2,
    )
    more = _steps(1, result_chars=250, first=9)
    again = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=more, fingerprints=[], no_progress_checkpoints=0
    )
    assert again.transcript == [*context[1:], *more]
    assert (again.step_summary, again.summarized_steps) == ("- read f0 to f4", 2)


def test_cancel_deletes_the_snapshot() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "ok"}],
        fingerprints=["status"], no_progress_checkpoints=0,
    )
    apply_decision(
        {"decision": "cancel", "intentKind": "work_request", "reply": "Stopping."},
        agent,
        db.get_agent_state(agent.id),
        {"type": "human_chat", "content": "Stop that task.", "from_name": "Human Operator"},
    )
    assert db.get_work_snapshot(activity.id) is None


def test_accepting_new_work_keeps_the_paused_snapshot() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "ok"}],
        fingerprints=["status"], no_progress_checkpoints=0,
    )
    apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "Switching to the hotfix.",
            "commitmentKind": "work",
            "taskTitle": "Hotfix login",
            "taskDescription": "Fix the login bug.",
        },
        agent,
        db.get_agent_state(agent.id),
        {"type": "human_chat", "content": "Drop everything, hotfix login.", "from_name": "Human Operator"},
    )
    assert db.get_activity(activity.id).status == "paused"
    assert db.get_work_snapshot(activity.id) is not None


def test_completing_work_deletes_the_snapshot() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "ok"}],
        fingerprints=["status"], no_progress_checkpoints=0,
    )
    activity_runtime.complete_activity(activity.id, detail="Done.")
    assert db.get_work_snapshot(activity.id) is None


def test_deleting_the_agent_removes_its_snapshots() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "ok"}],
        fingerprints=["status"], no_progress_checkpoints=0,
    )
    assert db.delete_agent_rows(agent.id) is True
    assert db.get_work_snapshot(activity.id) is None


def _claim(agent_id: str, trigger_type: str) -> dict[str, Any]:
    row = next(
        item for item in db.list_queued_triggers() if item.agent_id == agent_id and item.trigger_type == trigger_type
    )
    claimed = db.claim_trigger(row.id)
    payload = json.loads(claimed.payload) if claimed.payload else {}
    payload.update({"type": claimed.trigger_type, "trigger_id": claimed.id, "task_id": claimed.task_id,
                    "source_channel": claimed.source_channel, "claim_generation": claimed.claim_generation})
    return payload


def _replayed_once(prompt: list[dict[str, str]], step: str) -> bool:
    return sum(1 for item in prompt if item["content"] == step) == 1


@pytest.mark.asyncio
async def test_llm_error_exit_freezes_and_the_retry_replays_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent()
    _script(monkeypatch, [_STATUS_STEP, LLMError("provider down")])
    outcome = await run_turn(agent, db.get_agent_state(agent.id), _resume_trigger(task.id))
    assert outcome.trigger_status == "failed"
    snapshot = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in snapshot.transcript if item["role"] == "assistant"] == [_STATUS_STEP]

    prompts = await _interrupted_step(monkeypatch, agent, task, _BOARD_STEP)
    assert _replayed_once(prompts[0], _STATUS_STEP)
    snapshot = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in snapshot.transcript if item["role"] == "assistant"] == [
        _STATUS_STEP,
        _BOARD_STEP,
    ]


@pytest.mark.asyncio
async def test_parse_failure_exit_freezes_and_the_retry_replays_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent()
    # Invented keys still fail-close an execution turn at once; prose now repairs.
    _script(monkeypatch, [_STATUS_STEP, '{"act":"cli","data":{"cmd":"status"},"th":"s","_needsApproval":true}'])
    outcome = await run_turn(agent, db.get_agent_state(agent.id), _resume_trigger(task.id))
    assert outcome.trigger_status == "failed"
    snapshot = db.get_work_snapshot(activity.id)
    assert [item["content"] for item in snapshot.transcript if item["role"] == "assistant"] == [_STATUS_STEP]

    prompts = await _interrupted_step(monkeypatch, agent, task, _BOARD_STEP)
    assert _replayed_once(prompts[0], _STATUS_STEP)


@pytest.mark.asyncio
async def test_a_failed_resume_renders_the_same_interludes_again(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    db.delete_queued_triggers(agent.id)
    db.append_work_interlude(activity.id, {"from_name": "Human Operator", "content": "Also fix the css.", "reply": "Will do."})
    db.create_agent_trigger(
        agent_id=agent.id, trigger_type="activity_resumed", source_channel="work",
        payload={"content": "Resume work."}, task_id=task.id,
    )

    prompts = _script(monkeypatch, [LLMError("provider down")])
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _claim(agent.id, "activity_resumed"))
    assert "Also fix the css." in prompts[0][-1]["content"]
    assert len(db.get_work_snapshot(activity.id).interludes) == 1, "a failed turn keeps its interludes"

    # The dispatcher re-queued the same trigger for retry; it renders them again.
    prompts = _script(monkeypatch, [_BOARD_STEP], on_call=lambda call: _queue_interrupt(agent.id))
    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _claim(agent.id, "activity_resumed"))
    assert "Also fix the css." in prompts[0][-1]["content"]
    assert _replayed_once(prompts[0], _STATUS_STEP)
    assert len(db.get_work_snapshot(activity.id).interludes) == 1, "kept for the life of the work"


@pytest.mark.asyncio
async def test_interludes_survive_a_completed_resume_and_show_on_the_next_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the operator said during the work stays in view until the work ends (D11)."""
    agent, task, activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    db.delete_queued_triggers(agent.id)
    db.append_work_interlude(activity.id, {"from_name": "Human Operator", "content": "Use port 8080.", "reply": "Noted."})
    db.append_work_interlude(activity.id, {"from_name": "Human Operator", "content": "Shorter summaries.", "reply": "Will do."})

    for step in (_BOARD_STEP, '{"act":"cli","data":{"cmd":"pwd"},"th":"where"}', _STATUS_STEP):
        db.create_agent_trigger(
            agent_id=agent.id, trigger_type="activity_resumed", source_channel="work",
            payload={"content": "Resume work."}, task_id=task.id,
        )
        prompts = _script(monkeypatch, [step], on_call=lambda call: _queue_interrupt(agent.id))
        await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), _claim(agent.id, "activity_resumed"))
        resume = prompts[0][-1]["content"]
        assert "Said to you during this work (oldest first;" in resume
        assert resume.index("Use port 8080.") < resume.index("Shorter summaries."), "oldest first"
        assert "You replied: Noted." in resume
        db.delete_queued_triggers(agent.id)
        assert len(db.get_work_snapshot(activity.id).interludes) == 2, "a completed resume keeps them"

    # They go with the work: ending it deletes the snapshot.
    activity_runtime.complete_activity(activity.id, detail="Done.")
    assert db.get_work_snapshot(activity.id) is None


def test_interludes_count_toward_the_saved_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    steps = [{"role": "assistant", "content": "a" * 30}, {"role": "user", "content": "b" * 30}]
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    snapshot = db.append_work_interlude(
        activity.id, {"from_name": "Human Operator", "content": "Use port 8080.", "reply": "Noted."},
    )
    rendered = "[Human Operator]: Use port 8080.\nYou replied: Noted."
    assert interlude_tokens(snapshot) == len(rendered)
    assert saved_tokens(snapshot) == 60 + len(rendered)


@pytest.mark.asyncio
async def test_wait_feedback_keeps_the_turn_alive_until_the_wait_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    task = create_or_bind_task(
        title="Execute the 9 fixes",
        description="Apply the review fixes.",
        project=None,
        assigned_to=agent.id,
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
    activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    bare_wait = '{"act":"wait","data":{"why":"Waiting on Brad."},"th":"wait"}'
    good_wait = '{"act":"wait","data":{"why":"Waiting on Brad.","msg":"Waiting on Brad\'s review."},"th":"wait"}'
    prompts = _script(monkeypatch, [bare_wait, good_wait])
    outcome = await run_turn(agent, db.get_agent_state(agent.id), _resume_trigger(task.id))
    assert len(prompts) == 2, "the feedback came back in the same turn"
    assert "data.msg" in prompts[1][-1]["content"]
    assert outcome.trigger_status == "completed"
    assert db.get_task(task.id).status == "waiting"


def _thread_wake_behind_a_live_speaker(agent_id: str, *, live: bool) -> Any:
    """Queue a thread wake for ``agent_id``; ``live`` puts a peer mid-draft there first.

    Returns the peer's claimed trigger, or ``None`` when ``live`` is false.
    """
    peer = db.create_agent("Debra", role="PM", desk_x=2, desk_y=1, connection_id=model_connection("test/mock"))
    room = db.create_channel(name="Room", member_agent_ids=[agent_id, peer.id])
    claimed = None
    if live:
        drafting = db.create_agent_trigger(
            agent_id=peer.id,
            trigger_type="channel_message",
            source_channel="channel",
            payload={"content": "Status?", "channel_id": room.id},
        )
        claimed = db.claim_trigger(drafting.id)
        assert claimed is not None
        assert db.channel_peer_snapshot_is_drafting(room.id)
    db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="channel_message",
        source_channel="channel",
        payload={"content": "Charles, status?", "channel_id": room.id},
    )
    return claimed


@pytest.mark.asyncio
async def test_held_thread_wake_does_not_end_the_work_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, _activity = _working_agent()
    claimed = _thread_wake_behind_a_live_speaker(agent.id, live=True)

    def _free_the_room(call: int) -> None:
        # The live speaker posts during step 2; the held wake can run after it.
        if call == 2:
            db.complete_agent_trigger(claimed.id, claim_generation=claimed.claim_generation)

    prompts = _script(monkeypatch, [_STATUS_STEP, _BOARD_STEP], on_call=_free_the_room)
    state = db.get_agent_state(agent.id)
    await run_turn(agent, state, _resume_trigger(task.id))
    # Step 1 kept going while the wake was held; step 2 yielded once it could run.
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_thread_wake_with_no_live_speaker_ends_the_work_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, _activity = _working_agent()
    _thread_wake_behind_a_live_speaker(agent.id, live=False)
    prompts = _script(monkeypatch, [_STATUS_STEP])
    state = db.get_agent_state(agent.id)
    await run_turn(agent, state, _resume_trigger(task.id))
    assert len(prompts) == 1


# ─── The budget boundary and the step summary ───


def test_the_budget_is_crossed_one_token_past_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    assert set_context_budget(usable=167).work == 100
    prior = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": "a" * 30}, {"role": "user", "content": "b" * 30}],
        fingerprints=[], no_progress_checkpoints=0,
    )
    assert transcript_over_budget(prior, [{"role": "user", "content": "c" * 40}]) is False
    assert transcript_over_budget(prior, [{"role": "user", "content": "c" * 41}]) is True
    assert transcript_over_budget(None, [{"role": "user", "content": "c" * 100}]) is False
    assert transcript_over_budget(None, [{"role": "user", "content": "c" * 101}]) is True


def test_the_step_summary_counts_toward_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _char_tokens(monkeypatch)
    agent, _task_row, activity = _working_agent()
    assert set_context_budget(usable=167).work == 100
    steps = [{"role": "assistant", "content": "a" * 30}, {"role": "user", "content": "b" * 30}]
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    prior = db.save_step_summary(activity.id, transcript=steps, step_summary="s" * 20, summarized_steps=1)
    assert saved_tokens(prior) == 80
    assert transcript_over_budget(prior, [{"role": "user", "content": "c" * 20}]) is False
    assert transcript_over_budget(prior, [{"role": "user", "content": "c" * 21}]) is True


_PREAMBLE = [{"role": "system", "content": "preamble"}]
_TRIGGER = {"role": "user", "content": "Resume work."}


def test_restore_places_the_step_summary_between_the_preamble_and_the_steps() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=_steps(3, result_chars=20),
        fingerprints=[], no_progress_checkpoints=0,
    )
    kept = _steps(1, result_chars=20, first=2)
    snapshot = db.save_step_summary(
        activity.id, transcript=kept, step_summary="- read /me/f0.md 1:20: the config", summarized_steps=2,
    )

    restored = restore_work_turn(activity=activity, context=[*_PREAMBLE, _TRIGGER])

    assert restored[0] == _PREAMBLE[0]
    summary = restored[1]
    assert summary["role"] == "user"
    assert summary["content"] == (
        "Summary of your 2 earlier steps on this work. Those steps are no longer shown. "
        "Read a file again only when you need text this summary does not have.\n"
        "- read /me/f0.md 1:20: the config"
    )
    assert restored[2:-1] == kept
    assert restored[-1]["role"] == "user" and "Continue from where you left off" in restored[-1]["content"]
    # A decision turn's paused-work view still renders the kept steps.
    view = render_paused_work_view(snapshot, task_title="Execute the 9 fixes")
    assert view is not None and "cat /me/f2.md" in view


def test_restore_adds_no_summary_message_when_the_summary_is_empty() -> None:
    agent, _task_row, activity = _working_agent()
    steps = _steps(2, result_chars=20)
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )

    restored = restore_work_turn(activity=activity, context=[*_PREAMBLE, _TRIGGER])

    assert restored[:-1] == [*_PREAMBLE, *steps]
    assert not any(str(item["content"]).startswith("Summary of your") for item in restored)


def test_an_old_work_snapshots_table_gets_the_summary_columns_at_init_db() -> None:
    from db.connection import get_connection

    agent, _task_row, activity = _working_agent()
    steps = _steps(1, result_chars=10)
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=["cat"], no_progress_checkpoints=0,
    )
    con = get_connection()
    for column in ("step_summary", "summarized_steps"):
        con.execute(f"ALTER TABLE work_snapshots DROP COLUMN {column}")
    db.close_connection()

    db.init_db()

    columns = {row[1] for row in get_connection().execute("PRAGMA table_info(work_snapshots)").fetchall()}
    assert {"step_summary", "summarized_steps"} <= columns
    stored = db.get_work_snapshot(activity.id)
    assert (stored.step_summary, stored.summarized_steps) == ("", 0), "rows from before read as unsummarized"
    assert stored.transcript == steps and stored.fingerprints == ["cat"]


# ─── Step helpers and the seeded trim share ───


def _steps(count: int, *, result_chars: int, first: int = 0) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(first, first + count):
        messages.append({"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f%d.md"},"th":"r"}' % index})
        messages.append({"role": "user", "content": "y" * result_chars})
    return messages


def test_the_work_shares_are_seeded_at_the_medium_preset() -> None:
    assert config.require_int("work_snapshot_trim_percent") == 60
    assert config.require_int("work_snapshot_summary_percent") == 20


def test_saved_work_is_counted_with_the_shared_tokenizer() -> None:
    """Without the test counter: transcript_tokens is the real count of each message's content."""
    messages = [
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f0.md"},"th":"r"}'},
        {"role": "user", "content": "def main():\n    return 42\n"},
        {"role": "user", "content": ""},
    ]
    assert transcript_tokens(messages) == count_text_tokens(messages[0]["content"]) + count_text_tokens(
        messages[1]["content"]
    )
