"""Fix C — freeze the working transcript at every pause, view it, restore it.

The chat turn reads a code-rendered, read-only view of the frozen steps; an
execution resume replays them under a freshly built preamble.
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
    finish_restored_turn,
    freeze_work_turn,
    render_paused_work_view,
    reset_work_turn,
    transcript_over_budget,
)
from core.bm_cli.results import CLI_TOOL_RESULT_BEGIN
from core.llm import context_builder
from core.llm.client import LLMError, LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task

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


def _decision_context(agent) -> list[dict[str, str]]:
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
    context = _decision_context(agent)
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


@pytest.mark.asyncio
async def test_execution_turns_do_not_get_the_view(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, _activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
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
    )
    assert not any(item["content"].startswith(_VIEW_HEAD) for item in context_builder.build_context(turn))


def test_view_shows_only_the_most_recent_steps_within_budget() -> None:
    agent, _task_row, activity = _working_agent()
    steps: list[dict[str, str]] = []
    for index in range(6):
        steps.append({"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f%d.md"},"th":"r"}' % index})
        steps.append({"role": "user", "content": f"contents of file {index} " + "x" * 200})
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0
    )
    db.set_setting("work_snapshot_chat_view_max_chars", "600", "simulation")
    config.reload()
    view = render_paused_work_view(snapshot, task_title="Execute the 9 fixes")
    assert view is not None
    assert "cat /me/f5.md" in view
    assert "cat /me/f0.md" not in view
    assert "earlier steps are not shown" in view


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


def test_oldest_steps_compact_behind_a_marker() -> None:
    agent, _task_row, activity = _working_agent()
    db.set_setting("work_snapshot_max_chars", "900", "simulation")
    db.set_setting("work_snapshot_trim_percent", "88", "simulation")
    config.reload()
    context = [{"role": "system", "content": "preamble"}]
    for index in range(5):
        context.append({"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f%d.md"},"th":"r"}' % index})
        context.append({"role": "user", "content": "y" * 250})
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=1, context=context, fingerprints=[], no_progress_checkpoints=0
    )
    marker = snapshot.transcript[0]
    assert marker["role"] == "user"
    assert "earlier steps were dropped for space." in marker["content"]
    assert "- cat /me/f0.md" in marker["content"]
    assistant = [item for item in snapshot.transcript if item["role"] == "assistant"]
    assert assistant[-1]["content"].find("f4.md") > 0, "the newest step is kept"
    assert all("f0.md" not in item["content"] for item in assistant), "a dropped step is dropped whole"
    for index, item in enumerate(snapshot.transcript[1:], start=1):
        if item["role"] == "user":
            assert snapshot.transcript[index - 1]["role"] == "assistant", "no half pair survives"
    # Trimmed to the target (88% of 900), not just under the 900 trigger.
    assert sum(len(item["content"]) for item in snapshot.transcript) <= 792

    # A later freeze folds the old marker into the new one.
    more = [{"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f9.md"},"th":"r"}'},
            {"role": "user", "content": "z" * 250}]
    again = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=more, fingerprints=[], no_progress_checkpoints=0
    )
    head = again.transcript[0]["content"]
    assert "- cat /me/f0.md" in head
    assert sum(1 for item in again.transcript if "dropped for space" in item["content"]) == 1


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
    assert db.get_work_snapshot(activity.id).interludes == [], "cleared once the resumed turn completed"


def test_finish_restored_turn_ignores_unrestored_triggers() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "ok"}],
        fingerprints=["status"], no_progress_checkpoints=0,
    )
    db.append_work_interlude(activity.id, {"from_name": "Human Operator", "content": "Hi", "reply": "Hello"})
    finish_restored_turn({"type": "human_chat"})
    assert len(db.get_work_snapshot(activity.id).interludes) == 1


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


# ─── Context per checklist item (the item and budget boundaries) ───

_HANDOFF = "You checked off item 1 of your checklist. Continue with the next open item."


def test_reset_replaces_the_steps_fingerprints_and_checkpoints_but_keeps_interludes() -> None:
    agent, _task_row, activity = _working_agent()
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "ok"}],
        fingerprints=["status"], no_progress_checkpoints=2,
    )
    db.append_work_interlude(activity.id, {"from_name": "Human Operator", "content": "Hi", "reply": "Hello"})

    reset = reset_work_turn(agent=agent, activity=activity, handoff=_HANDOFF)

    assert reset.transcript == [{"role": "user", "content": _HANDOFF}]
    assert reset.fingerprints == []
    assert reset.no_progress_checkpoints == 0
    stored = db.get_work_snapshot(activity.id)
    assert stored.transcript == [{"role": "user", "content": _HANDOFF}]
    assert [item.model_dump() for item in stored.interludes] == [
        {"from_name": "Human Operator", "content": "Hi", "reply": "Hello"}
    ]
    # Only the hand-off is left: no steps, so a decision turn gets no paused-work view.
    assert render_paused_work_view(stored, task_title="Execute the 9 fixes") is None


def test_the_budget_is_crossed_one_character_past_it() -> None:
    agent, _task_row, activity = _working_agent()
    db.set_setting("work_snapshot_max_chars", "100", "simulation")
    config.reload()
    prior = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0,
        context=[{"role": "assistant", "content": "a" * 30}, {"role": "user", "content": "b" * 30}],
        fingerprints=[], no_progress_checkpoints=0,
    )
    assert transcript_over_budget(prior, [{"role": "user", "content": "c" * 40}]) is False
    assert transcript_over_budget(prior, [{"role": "user", "content": "c" * 41}]) is True
    assert transcript_over_budget(None, [{"role": "user", "content": "c" * 100}]) is False
    assert transcript_over_budget(None, [{"role": "user", "content": "c" * 101}]) is True


@pytest.mark.asyncio
async def test_a_restore_after_a_reset_is_the_preamble_the_handoff_then_the_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task, activity = _working_agent()
    await _interrupted_step(monkeypatch, agent, task, _STATUS_STEP)
    db.delete_queued_triggers(agent.id)
    reset_work_turn(agent=agent, activity=activity, handoff=_HANDOFF)

    prompts = await _interrupted_step(monkeypatch, agent, task, _BOARD_STEP)
    resumed = prompts[0]
    handoff_index = next(i for i, item in enumerate(resumed) if item["content"] == _HANDOFF)
    assert resumed[0]["role"] == "system", "the preamble is rebuilt fresh"
    assert all(item["role"] != "assistant" for item in resumed[:handoff_index]), "no cleared step precedes it"
    assert resumed[handoff_index]["role"] == "user"
    assert handoff_index == len(resumed) - 2, "the hand-off, then the resume message, close the context"
    assert "Continue from where you left off" in resumed[-1]["content"]
    assert not any(item["content"] == _STATUS_STEP for item in resumed), "the cleared step is gone"


# ─── The trim target: trim below the trigger so the next trim is a run of steps away ───


def _steps(count: int, *, result_chars: int, first: int = 0) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(first, first + count):
        messages.append({"role": "assistant", "content": '{"act":"cli","data":{"cmd":"cat /me/f%d.md"},"th":"r"}' % index})
        messages.append({"role": "user", "content": "y" * result_chars})
    return messages


def _limits(max_chars: int, trim_percent: int) -> None:
    db.set_setting("work_snapshot_max_chars", str(max_chars), "simulation")
    db.set_setting("work_snapshot_trim_percent", str(trim_percent), "simulation")
    config.reload()


def test_the_trim_share_is_seeded_at_half_the_budget() -> None:
    assert config.require_int("work_snapshot_trim_percent") == 50


def test_a_transcript_at_the_budget_is_kept_as_is() -> None:
    agent, _task_row, activity = _working_agent()
    steps = _steps(4, result_chars=197)  # 4 × (53 + 197) = exactly 1000 characters
    assert sum(len(item["content"]) for item in steps) == 1000
    _limits(1000, 40)
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0
    )
    assert snapshot.transcript == steps


def test_over_the_budget_the_trim_goes_down_to_the_target() -> None:
    agent, _task_row, activity = _working_agent()
    steps = _steps(4, result_chars=198)  # 1004 characters: just over the 1000 trigger
    _limits(1000, 40)
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0
    )
    total = sum(len(item["content"]) for item in snapshot.transcript)
    assert total <= 400, "the marker and the kept steps fit 40% of the budget"
    marker = snapshot.transcript[0]["content"]
    assert "earlier steps were dropped for space." in marker
    assert "- cat /me/f0.md" in marker and "- cat /me/f2.md" in marker
    assistant = [item["content"] for item in snapshot.transcript if item["role"] == "assistant"]
    assert assistant == [steps[-2]["content"]], "only the newest step fits, and it is kept whole"


def test_the_newest_step_is_kept_even_when_it_alone_is_over_the_target() -> None:
    agent, _task_row, activity = _working_agent()
    steps = [*_steps(1, result_chars=100), *_steps(1, result_chars=900, first=1)]
    _limits(1000, 40)
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0
    )
    assert [item["content"] for item in snapshot.transcript[1:]] == [item["content"] for item in steps[2:]]
    assert "- cat /me/f0.md" in snapshot.transcript[0]["content"]


@pytest.mark.parametrize("trim_percent", [0, 100, -5])
def test_a_trim_share_outside_1_to_99_is_refused(trim_percent: int) -> None:
    agent, _task_row, activity = _working_agent()
    _limits(1000, trim_percent)
    with pytest.raises(config.ConfigError) as excinfo:
        freeze_work_turn(
            agent=agent, activity=activity, initial_len=0, context=_steps(1, result_chars=10),
            fingerprints=[], no_progress_checkpoints=0,
        )
    message = str(excinfo.value)
    assert "work_snapshot_trim_percent" in message and str(trim_percent) in message
    assert db.get_work_snapshot(activity.id) is None, "nothing is saved under a broken setting"


def test_a_lowered_budget_works_with_the_seeded_share() -> None:
    """A budget lowered below the old 60000 trim count needs no second edit."""
    agent, _task_row, activity = _working_agent()
    db.set_setting("work_snapshot_max_chars", "1000", "simulation")
    config.reload()
    assert config.require_int("work_snapshot_trim_percent") == 50
    steps = _steps(6, result_chars=147)  # 6 × 200 = 1200 characters, over the 1000 budget
    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0
    )
    assert sum(len(item["content"]) for item in snapshot.transcript) <= 500
    assert "earlier steps were dropped for space." in snapshot.transcript[0]["content"]
    assert snapshot.transcript[-2]["content"] == steps[-2]["content"], "the newest step is kept"
