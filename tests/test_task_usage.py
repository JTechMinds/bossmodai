"""Task usage: what a task's work turns used, recorded per task and never pruned.

Only the turns that resume the task's work count (``loop.py``'s
``work_activity``), folds included; decision turns, detached turns and other
tasks' turns do not. The context size is one model call's prompt; the fold's
own trace and a trace with no prompt tokens are not model calls. The usage
row is written before the ``diagnostic`` broadcast the task detail refreshes
on. ``GET /api/tasks/{id}/usage`` serves the row with the current Context
window.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.loop import run_turn
from core.agent_loop.task_usage import WorkTurnUsage, work_turn_usage
from core.agent_loop.turn_helpers import _build_step_trace
from core.agent_loop.work_summary import SUMMARY_STEP_ACTION
from core.bm_cli.managed_writer.types import ManagedWriteOutcome
from core.bm_cli.results import success_result
from core.context_budget import read_context_budget
from core.llm.client import LLMError, LLMResponse
from core.llm.prompt_cache_usage import PromptCacheUsage
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskUsage
from core.runtime.events import NullRuntimeEventSink, runtime_events
from tests._connections import model_connection
from tests.test_live_work_continuation import (
    _BOARD_STEP,
    _STATUS_STEP,
    _SUMMARY_REPLY,
    _done_evidence,
    _frozen_steps,
    _over_budget_work,
    _queue_status_question,
    _start_resume,
    _working_agent,
)

AT = datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc)


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
    runtime_events.set_sink(NullRuntimeEventSink())
    db.close_connection()


@pytest.fixture(autouse=True)
def _diagnostics_stay_consistent(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Every turn here keeps its diagnostics row the sum of its steps: db.diagnostics logs no error."""
    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        yield
    errors = [
        record.getMessage() for record in caplog.records
        if record.name == "db.diagnostics" and record.levelno >= logging.ERROR
    ]
    assert errors == []


@pytest.fixture
def _char_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Saved work counts one token per character, so the fold's budgets are exact (as in the fold tests)."""
    monkeypatch.setattr("core.llm.client.count_text_tokens", len)
    monkeypatch.setattr("core.agent_loop.work_snapshot.count_text_tokens", len)


def _script(
    monkeypatch: pytest.MonkeyPatch, replies: list[tuple[str, int] | Exception], *, on_call=None,
) -> list[int]:
    """Each model call takes the next ``(content, prompt_tokens)`` reply, 10 completion tokens each.

    An ``Exception`` is raised by that call instead. ``on_call`` gets the
    1-based call number before the reply. Returns the call numbers made.
    """
    queue = list(replies)
    calls: list[int] = []

    async def _fake_completion(**_kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        calls.append(len(calls) + 1)
        if on_call is not None:
            on_call(len(calls))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        content, prompt = item
        return LLMResponse(
            content=content, model="test/mock", prompt_tokens=prompt, completion_tokens=10, total_tokens=prompt + 10,
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return calls


def _pause_after(agent_id: str, call: int):
    """An ``on_call`` that queues a waiting message on ``call``, so the work turn pauses after that step."""
    return lambda number: _queue_status_question(agent_id) if number == call else None


def _usage(task_id: str, **fields: Any) -> TaskUsage:
    """A ``TaskUsage`` with every count 0 and every time ``None``, but ``fields``."""
    base: dict[str, Any] = {
        "task_id": task_id, "work_turns": 0, "model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "latest_prompt_tokens": None, "latest_call_at": None, "peak_prompt_tokens": None,
        "summaries": 0, "summarized_steps": 0, "last_summary_at": None,
        "failed_summaries": 0, "last_failed_summary_at": None,
    }
    return TaskUsage(**{**base, **fields})


def _stored(task_id: str) -> TaskUsage:
    usage = db.get_task_usage(task_id)
    assert usage is not None
    return usage


def _times(usage: TaskUsage) -> TaskUsage:
    """The row with its times blanked, after checking each set one is recent."""
    now = datetime.now(timezone.utc)
    blanked: dict[str, Any] = {}
    for key in ("latest_call_at", "last_summary_at", "last_failed_summary_at"):
        value = getattr(usage, key)
        if value is not None:
            assert now - timedelta(minutes=1) <= value <= now, key
            blanked[key] = None
    return usage.model_copy(update=blanked)


# ─── work_turn_usage ───


def _trace(action: str, context: int, *, prompt: int | None = None) -> dict[str, Any]:
    """A step trace whose own agent call sent ``context``; ``prompt`` (default the same) is every call it made."""
    spent = context if prompt is None else prompt
    return _build_step_trace(
        step_index=0, context_snapshot=None, raw_response="", action={"action": action}, result=None,
        prompt_tokens=spent, context_prompt_tokens=context, completion_tokens=1, total_tokens=spent + 1,
        cache=PromptCacheUsage.ZERO, duration_ms=1,
    )


def test_the_fold_and_traces_with_no_agent_call_are_not_model_calls() -> None:
    steps = [_trace(SUMMARY_STEP_ACTION, 9000), _trace("bm_cli", 1200), _trace("", 0), _trace("bm_cli", 800)]

    assert work_turn_usage(steps) == WorkTurnUsage(model_calls=2, latest_prompt_tokens=800, peak_prompt_tokens=1200)


def test_the_context_is_the_agents_own_call_not_the_steps_sub_calls() -> None:
    # A managed write: the agent's call sent 1,000; its writer's sub-calls 5,000 more.
    steps = [_trace("bm_cli", 1000, prompt=6000)]

    assert work_turn_usage(steps) == WorkTurnUsage(model_calls=1, latest_prompt_tokens=1000, peak_prompt_tokens=1000)


def test_latest_is_the_last_call_and_peak_the_largest() -> None:
    steps = [_trace("bm_cli", 500), _trace("bm_cli", 3000), _trace("complete", 2500)]

    assert work_turn_usage(steps) == WorkTurnUsage(model_calls=3, latest_prompt_tokens=2500, peak_prompt_tokens=3000)


def test_with_no_call_there_is_no_context_size() -> None:
    assert work_turn_usage([]) == WorkTurnUsage(model_calls=0, latest_prompt_tokens=None, peak_prompt_tokens=None)
    assert work_turn_usage([_trace(SUMMARY_STEP_ACTION, 0, prompt=700), _trace("", 0)]) == WorkTurnUsage(
        model_calls=0, latest_prompt_tokens=None, peak_prompt_tokens=None,
    )


def test_the_summary_step_action_is_the_literal_the_diagnostics_show() -> None:
    assert SUMMARY_STEP_ACTION == "summarize_steps"


# ─── the db writes ───


def _bare_task(title: str = "Compress the model", *, parent_task_id: str | None = None):
    return db.create_task(title=title, created_by=HUMAN_SENDER_ID, parent_task_id=parent_task_id)


def _turn(task_id: str, *, calls: int, prompt: int, latest: int | None, peak: int | None, at: datetime = AT) -> None:
    db.add_task_work_turn(
        task_id, model_calls=calls, prompt_tokens=prompt, completion_tokens=calls * 10,
        latest_prompt_tokens=latest, peak_prompt_tokens=peak, at=at,
    )


def test_a_task_without_work_has_no_usage() -> None:
    assert db.get_task_usage(_bare_task().id) is None


def test_the_first_turn_creates_the_row_and_the_next_adds_to_it() -> None:
    task = _bare_task()

    _turn(task.id, calls=2, prompt=3000, latest=1800, peak=1800)
    assert db.get_task_usage(task.id) == _usage(
        task.id, work_turns=1, model_calls=2, prompt_tokens=3000, completion_tokens=20,
        latest_prompt_tokens=1800, latest_call_at=AT, peak_prompt_tokens=1800,
    )

    later = AT + timedelta(minutes=5)
    _turn(task.id, calls=3, prompt=6000, latest=2400, peak=2600, at=later)
    assert db.get_task_usage(task.id) == _usage(
        task.id, work_turns=2, model_calls=5, prompt_tokens=9000, completion_tokens=50,
        latest_prompt_tokens=2400, latest_call_at=later, peak_prompt_tokens=2600,
    )


def test_a_turn_without_a_call_keeps_the_latest_and_still_counts() -> None:
    task = _bare_task()
    _turn(task.id, calls=1, prompt=1800, latest=1800, peak=1800)

    _turn(task.id, calls=0, prompt=700, latest=None, peak=None, at=AT + timedelta(minutes=5))

    assert db.get_task_usage(task.id) == _usage(
        task.id, work_turns=2, model_calls=1, prompt_tokens=2500, completion_tokens=10,
        latest_prompt_tokens=1800, latest_call_at=AT, peak_prompt_tokens=1800,
    )


def test_a_first_turn_without_a_call_has_no_context_size_yet() -> None:
    task = _bare_task()

    _turn(task.id, calls=0, prompt=0, latest=None, peak=None)

    assert db.get_task_usage(task.id) == _usage(task.id, work_turns=1)


def test_the_peak_only_grows() -> None:
    task = _bare_task()
    _turn(task.id, calls=1, prompt=5000, latest=5000, peak=5000)

    _turn(task.id, calls=1, prompt=1200, latest=1200, peak=1200)

    stored = _stored(task.id)
    assert (stored.latest_prompt_tokens, stored.peak_prompt_tokens) == (1200, 5000)


def test_folds_and_failures_count_with_their_times() -> None:
    task = _bare_task()
    first, second, failed = AT, AT + timedelta(hours=1), AT + timedelta(hours=2)

    db.add_task_work_summary(task.id, summarized_steps=12, at=first)
    db.add_task_work_summary_failure(task.id, at=failed)
    db.add_task_work_summary(task.id, summarized_steps=7, at=second)

    # A fold before any turn ends creates the row; the turns are added later.
    assert db.get_task_usage(task.id) == _usage(
        task.id, summaries=2, summarized_steps=19, last_summary_at=second,
        failed_summaries=1, last_failed_summary_at=failed,
    )


def test_a_task_that_does_not_exist_is_refused() -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _turn("no-such-task", calls=1, prompt=10, latest=10, peak=10)


def test_a_malformed_row_raises_instead_of_reading_as_none() -> None:
    task = _bare_task()
    _turn(task.id, calls=1, prompt=10, latest=10, peak=10)
    db.execute("UPDATE task_usage SET model_calls = -1 WHERE task_id = $1", [task.id])

    with pytest.raises(ValidationError):
        db.get_task_usage(task.id)


# ─── scripted work turns ───


@pytest.mark.asyncio
async def test_a_work_turn_adds_its_calls_and_context_sizes_to_its_task(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    # A waiting message arrives during the second call: the turn pauses after that step.
    calls = _script(monkeypatch, [(_STATUS_STEP, 1500), (_BOARD_STEP, 1000)], on_call=_pause_after(agent.id, 2))

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert calls == [1, 2] and outcome.trigger_status == "completed"
    assert (outcome.prompt_tokens, outcome.completion_tokens) == (2500, 20)
    assert _times(_stored(task.id)) == _usage(
        task.id, work_turns=1, model_calls=2, prompt_tokens=2500, completion_tokens=20,
        latest_prompt_tokens=1000, peak_prompt_tokens=1500,
    )
    assert _stored(task.id).latest_call_at is not None


_WRITE_STEP = '{"act":"cli","data":{"cmd":"write /me/notes.md"},"th":"draft the notes"}'


@pytest.mark.asyncio
async def test_a_managed_writes_sub_calls_count_in_the_tokens_but_not_in_the_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task = _working_agent()

    async def _managed_write(**_kwargs: Any) -> ManagedWriteOutcome:
        return ManagedWriteOutcome(
            cli_result=success_result(
                command="write /me/notes.md", detail="Wrote /me/notes.md", kind="write", data={}, sections=[],
            ),
            prompt_tokens=5000, completion_tokens=300, total_tokens=5300, cache=PromptCacheUsage.ZERO, chunks=1,
        )

    monkeypatch.setattr("core.agent_loop.execution_turn.run_managed_write", _managed_write)
    # The agent's own call sends 1,000; a waiting message pauses the turn after this step.
    calls = _script(monkeypatch, [(_WRITE_STEP, 1000)], on_call=_pause_after(agent.id, 1))

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert calls == [1] and outcome.trigger_status == "completed"
    (step,) = outcome.steps
    assert (step["action_name"], step["prompt_tokens"], step["context_prompt_tokens"]) == ("bm_cli", 6000, 1000)
    assert _times(_stored(task.id)) == _usage(
        task.id, work_turns=1, model_calls=1, prompt_tokens=6000, completion_tokens=310,
        latest_prompt_tokens=1000, peak_prompt_tokens=1000,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_char_tokens")
async def test_a_fold_is_counted_as_a_fold_and_its_tokens_but_not_as_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    activity = activity_runtime.get_active_work_activity(agent.id)
    _over_budget_work(agent)
    _frozen_steps(agent, _STATUS_STEP)
    _queue_status_question(agent.id)  # pauses the turn after its first step
    calls = _script(monkeypatch, [(_SUMMARY_REPLY, 900), (_BOARD_STEP, 1200)])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert calls == [1, 2] and outcome.trigger_status == "completed"
    folded = db.get_work_snapshot(activity.id).summarized_steps
    assert folded > 0
    assert _times(_stored(task.id)) == _usage(
        task.id, work_turns=1, model_calls=1, prompt_tokens=2100, completion_tokens=20,
        latest_prompt_tokens=1200, peak_prompt_tokens=1200, summaries=1, summarized_steps=folded,
    )
    assert _stored(task.id).last_summary_at is not None


@pytest.mark.asyncio
@pytest.mark.usefixtures("_char_tokens")
async def test_a_failed_fold_is_counted_as_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    _over_budget_work(agent)  # the summary limit is 150 tokens
    _frozen_steps(agent, _STATUS_STEP)
    # An over-limit summary, then the shorter request fails: no work step runs.
    _script(monkeypatch, [("x" * 200, 700), LLMError("stalled")])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert outcome.trigger_status == "failed"
    # The fold spent tokens, but made no call of the work: no context size yet.
    assert _times(_stored(task.id)) == _usage(
        task.id, work_turns=1, prompt_tokens=700, completion_tokens=10, failed_summaries=1,
    )
    assert _stored(task.id).last_failed_summary_at is not None


# ─── attribution ───


_REPLY = '{"act":"reply","work_commit":false,"intent":"status","msg":"Still on it.","th":"answer"}'


@pytest.mark.asyncio
async def test_a_chat_turn_about_the_task_writes_no_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    _script(monkeypatch, [(_REPLY, 900)])
    trigger = {
        "type": "human_chat", "content": "How is it going?", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "chat",
    }

    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed" and outcome.prompt_tokens == 900
    assert db.get_task_usage(task.id) is None


@pytest.mark.asyncio
async def test_a_task_follow_up_decision_writes_no_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    _script(monkeypatch, [(_REPLY, 900)])
    trigger = {
        "type": "task_follow_up", "task_id": task.id, "task_status": "active", "task_party": "assignee",
        "content": "Any update?", "from_name": "Human", "source_channel": "chat",
    }

    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed" and outcome.prompt_tokens == 900
    assert db.get_task_usage(task.id) is None


@pytest.mark.asyncio
async def test_a_detached_resume_writes_no_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    _frozen_steps(agent, _STATUS_STEP)
    _script(monkeypatch, [('{"act":"idle","th":"handled it"}', 900)])
    # A resume stamped detached_origin runs detached even though its type resumes work.
    trigger = {
        "type": "cli_approval_resolved", "source_channel": "system", "task_id": None, "detached_origin": True,
        "approval_request_id": "approval-1", "command": "pip install pytest", "status": "rejected",
        "decision_note": "Not needed for a reply.",
    }

    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed" and outcome.prompt_tokens == 900
    assert db.get_task_usage(task.id) is None


_DONE_STEP = (
    '{"act":"done","data":{"sum":"Applied the fixes.","claim":{"type":"proof","ev":"review shows green"}},'
    '"th":"done"}'
)


@pytest.mark.asyncio
async def test_a_turn_that_finishes_the_task_is_credited_to_the_task_it_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, task = _working_agent()
    _done_evidence(agent.id)
    _script(monkeypatch, [(_DONE_STEP, 1300)])

    outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert outcome.trigger_status == "completed"
    # The done ended the binding before the turn was finalized.
    assert db.get_task(task.id).status == "complete"
    assert activity_runtime.get_active_work_activity(agent.id) is None
    assert _times(_stored(task.id)) == _usage(
        task.id, work_turns=1, model_calls=1, prompt_tokens=1300, completion_tokens=10,
        latest_prompt_tokens=1300, peak_prompt_tokens=1300,
    )


@pytest.mark.asyncio
async def test_a_child_tasks_work_is_credited_to_the_child_only(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1,
                            connection_id=model_connection("test/mock"))
    parent = db.create_task(title="Compress the model", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    child = db.create_task(title="Profile Q4", assigned_to=agent.id, created_by=HUMAN_SENDER_ID,
                           parent_task_id=parent.id)
    activity_runtime.activate_work_activity(agent.id, child, task_status="active")
    _script(monkeypatch, [(_STATUS_STEP, 1100)], on_call=_pause_after(agent.id, 1))

    await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, child.id))

    assert _stored(child.id).work_turns == 1 and _stored(child.id).prompt_tokens == 1100
    assert db.get_task_usage(parent.id) is None


# ─── ordering ───


class _DiagnosticSpy(NullRuntimeEventSink):
    """Reads the task's usage at the moment each diagnostic is broadcast."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.seen: list[TaskUsage | None] = []

    async def broadcast_diagnostic(self, summary: dict[str, Any]) -> None:
        self.seen.append(db.get_task_usage(self.task_id))


@pytest.mark.asyncio
async def test_the_usage_is_written_before_the_diagnostic_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task = _working_agent()
    spy = _DiagnosticSpy(task.id)
    runtime_events.set_sink(spy)
    _script(monkeypatch, [(_STATUS_STEP, 1500)], on_call=_pause_after(agent.id, 1))

    await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    (seen,) = spy.seen
    assert seen is not None and (seen.work_turns, seen.model_calls, seen.latest_prompt_tokens) == (1, 1, 1500)


# ─── the API ───


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def test_the_usage_endpoint_answers_unknown_none_and_a_row_with_the_window() -> None:
    client, headers = _client()
    task = _bare_task()
    url = "/api/tasks/{}/usage"

    unknown = client.get(url.format("no-such-task"), headers=headers)
    assert unknown.status_code == 404 and unknown.json()["detail"] == "Task not found"
    none = client.get(url.format(task.id), headers=headers)
    assert none.status_code == 200 and none.json() == {"usage": None}

    _turn(task.id, calls=2, prompt=3000, latest=1800, peak=1900)
    db.add_task_work_summary(task.id, summarized_steps=12, at=AT + timedelta(minutes=1))
    row = client.get(url.format(task.id), headers=headers)
    assert row.status_code == 200
    assert row.json() == {"usage": {
        **_stored(task.id).model_dump(mode="json"),
        "window_tokens": read_context_budget().window,
    }}
    usage = row.json()["usage"]
    assert (usage["latest_prompt_tokens"], usage["peak_prompt_tokens"], usage["summaries"]) == (1800, 1900, 1)
    assert datetime.fromisoformat(usage["latest_call_at"]) == AT
    assert usage["last_failed_summary_at"] is None


def test_the_usage_endpoint_says_where_to_fix_invalid_context_settings(caplog: pytest.LogCaptureFixture) -> None:
    client, headers = _client()
    task = _bare_task()
    _turn(task.id, calls=1, prompt=10, latest=10, peak=10)
    db.set_setting("context_reply_percent", "0", "context")
    config.reload()

    with caplog.at_level(logging.ERROR, logger="api.routes.tasks"):
        res = client.get(f"/api/tasks/{task.id}/usage", headers=headers)

    assert res.status_code == 500
    assert res.json()["detail"] == "The Context Window settings are invalid. Fix them in Settings → Context Window."
    assert any(record.name == "api.routes.tasks" and record.levelno == logging.ERROR for record in caplog.records)
