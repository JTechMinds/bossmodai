"""Fix C — "no progress" means repeated or failed steps, checkpoint before block.

A first read is investigation (neutral). A re-read of something this work
activity already read, across pauses, or a failed command is stale. The
first trip spends a checkpoint resume; only a later trip blocks the task.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.liveness import classify_step, next_stale_streak, step_fingerprint
from core.agent_loop.loop import run_turn
from core.agent_loop.soft_blocks import NO_PROGRESS_CHECKPOINT_CODE, NO_PROGRESS_CODE
from core.agent_loop.work_snapshot import freeze_work_turn
from core.default_prompts import load_default_prompt
from core.llm.client import LLMResponse
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task
from db.connection import _apply_migrations, get_connection

_STATUS_STEP = '{"act":"cli","data":{"cmd":"status"},"th":"check status"}'


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


def _read(command: str) -> dict[str, Any]:
    return {"action": "bm_cli", "command": command}


def test_write_is_progress() -> None:
    kind = classify_step(_read("write /projects/app/main.py"), {"event": "bm_cli_result"}, set())
    assert kind == "progress"
    assert next_stale_streak(7, kind) == 0


def test_first_read_is_novel_and_leaves_the_streak() -> None:
    kind = classify_step(_read("cat /projects/app/README.md"), {"event": "bm_cli_result"}, set())
    assert kind == "novel"
    assert next_stale_streak(3, kind) == 3


def test_repeat_read_is_stale_even_with_a_path_tweak() -> None:
    seen = {step_fingerprint(_read("cat ./docs/verdict.md"))}
    kind = classify_step(_read("cat docs/verdict.md/"), {"event": "bm_cli_result"}, seen)
    assert kind == "stale"
    assert next_stale_streak(3, kind) == 4


def test_failed_command_is_stale_even_when_new() -> None:
    kind = classify_step(_read("pytest -q"), {"event": "bm_cli_error"}, set())
    assert kind == "stale"


def test_threshold_default_is_100() -> None:
    assert Agent.model_fields["guardian_no_progress_threshold"].default == 100
    agent = db.create_agent("Charles", role="Build Engineer")
    assert db.get_agent(agent.id).guardian_no_progress_threshold == 100


def test_migration_moves_the_old_default_only() -> None:
    old = db.create_agent("Charles", role="Build Engineer")
    tuned = db.create_agent("Jim", role="Engineer")
    db.execute("UPDATE agents SET guardian_no_progress_threshold = 30 WHERE id = $1", [old.id])
    db.execute("UPDATE agents SET guardian_no_progress_threshold = 45 WHERE id = $1", [tuned.id])
    _apply_migrations(get_connection())
    _apply_migrations(get_connection())
    assert db.get_agent(old.id).guardian_no_progress_threshold == 100
    assert db.get_agent(tuned.id).guardian_no_progress_threshold == 45


def _working_agent(threshold: int):
    agent = db.create_agent("Charles", role="Build Engineer", desk_x=1, desk_y=1, model_work="test/mock")
    db.update_agent(agent.id, guardian_no_progress_threshold=threshold)
    agent = db.get_agent(agent.id)
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
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    assert activity is not None
    return agent, task, activity


def _script(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> list[str]:
    queue = list(contents)
    seen: list[str] = []

    async def _fake_completion(**_kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        seen.append(queue[0])
        return LLMResponse(
            content=queue.pop(0), model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen


def _resume(task_id: str) -> dict[str, Any]:
    return {"type": "activity_resumed", "task_id": task_id, "content": "Resume.", "source_channel": "work"}


@pytest.mark.asyncio
async def test_cross_turn_reread_is_stale_then_checkpoint_then_block(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent(threshold=1)
    # An earlier pause already ran `status` on this work activity.
    freeze_work_turn(
        agent=agent,
        activity=activity,
        initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "status output"}],
        fingerprints=["status"],
        no_progress_checkpoints=0,
    )

    calls = _script(monkeypatch, [_STATUS_STEP])
    outcome = await run_turn(agent, db.get_agent_state(agent.id), _resume(task.id))
    assert len(calls) == 1, "one repeated read trips a threshold of 1"
    assert outcome.result["feedback_code"] == NO_PROGRESS_CHECKPOINT_CODE
    assert db.get_task(task.id).status == "active"
    resumes = [item for item in outcome.result["trigger_requests"] if item["trigger_type"] == "activity_resumed"]
    assert len(resumes) == 1
    assert resumes[0]["payload"]["content"] == load_default_prompt("internal_loop_execution_no_progress_checkpoint")
    assert db.get_work_snapshot(activity.id).no_progress_checkpoints == 1

    _script(monkeypatch, [_STATUS_STEP])
    outcome = await run_turn(agent, db.get_agent_state(agent.id), _resume(task.id))
    assert outcome.result["feedback_code"] == NO_PROGRESS_CODE
    assert db.get_task(task.id).status == "blocked"
    assert db.get_work_snapshot(activity.id) is not None, "a blocked task keeps its frozen work"


@pytest.mark.asyncio
async def test_progress_resets_the_checkpoint_count(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, task, activity = _working_agent(threshold=100)
    freeze_work_turn(
        agent=agent,
        activity=activity,
        initial_len=0,
        context=[{"role": "assistant", "content": _STATUS_STEP}, {"role": "user", "content": "status output"}],
        fingerprints=["status"],
        no_progress_checkpoints=1,
    )
    write = '{"act":"cli","data":{"cmd":"write /me/notes.md","body":"fix 1 done"},"th":"write"}'
    _script(monkeypatch, [write])
    # A queued interrupt ends the turn right after the write step.
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "Status?", "from_name": "Human Operator"},
    )
    await run_turn(agent, db.get_agent_state(agent.id), _resume(task.id))
    assert db.get_work_snapshot(activity.id).no_progress_checkpoints == 0
