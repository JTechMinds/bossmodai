"""A say that commits to work must not ghost after a bad envelope."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.loop import run_turn
from core.agent_loop.promise_lock import (
    BAD_JSON_WHY,
    EMPTY_ACTIONS_WHY,
    REPAIR_EXHAUSTED_WHY,
    promise_fail_why,
    promise_gap_note,
    say_commits_to_work,
)
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.results import BossModCliResult
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _limit_decision_repairs(attempts: int) -> None:
    db.set_setting("decision_repair_attempts", str(attempts), "llm")
    config.reload()


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
    )


def _script_completions(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> list[str]:
    queue = list(contents)
    seen: list[str] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        del kwargs
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        content = queue.pop(0)
        seen.append(content)
        return _llm(content)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen


def _agent_and_work():
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Land G0", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    return agent, state, task


def _why_counts(agent_id: str, task_id: str, why: str) -> tuple[int, int]:
    events = sum(
        1
        for item in db.list_task_events(task_id)
        if why in (item.content or "") and "re-queued" in (item.content or "")
    )
    notes = sum(
        1
        for item in db.list_notifications(agent_id=agent_id, limit=12)
        if why in (item.content or "") and "re-queued" in (item.content or "")
    )
    return events, notes


def _work_wakes(result: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in result.get("trigger_requests") or []
        if item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task_id
    ]


def _human_turn(agent, state, _content: str):
    return run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )


@pytest.mark.parametrize(
    ("text", "commits"),
    [
        ("landing G0 now", True),
        ("writing the notes now", True),
        ("running pytest now", True),
        ("Still reading the brief.", False),
        ("Committed and pushed.\n\n- Next: pytest on the clone.", False),
        ("Done. Tests passed. Waiting on review.", False),
        ("I'll write it tomorrow.", False),
    ],
)
def test_say_commits_to_work_matches_only_immediate_promises(text: str, commits: bool) -> None:
    assert say_commits_to_work(text) is commits


def test_promise_fail_why_names_the_terminal_gap() -> None:
    assert promise_fail_why(repair_attempts=0, kind="invalid_json") == BAD_JSON_WHY
    assert promise_fail_why(repair_attempts=2, kind="invalid_json") == REPAIR_EXHAUSTED_WHY
    assert "re-queued" in promise_gap_note(EMPTY_ACTIONS_WHY)
    assert EMPTY_ACTIONS_WHY in promise_gap_note(EMPTY_ACTIONS_WHY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "say",
    ["landing G0 now", "writing the notes now", "running pytest now"],
)
async def test_empty_actions_after_committed_say_notes_and_requeues(
    monkeypatch: pytest.MonkeyPatch,
    say: str,
) -> None:
    agent, state, task = _agent_and_work()
    seen = _script_completions(
        monkeypatch,
        ['{"say":"%s","actions":[]}' % say],
    )
    outcome = await _human_turn(agent, state, say)
    assert len(seen) == 1
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, EMPTY_ACTIONS_WHY)
    assert events == 1
    assert notes == 1
    assert len(_work_wakes(outcome.result, task.id)) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    thread = db.get_human_chat_thread(agent.id)
    assert any(say in (item.content or "") for item in thread)
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


@pytest.mark.asyncio
async def test_bad_json_after_committed_say_notes_and_requeues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(0)
    agent, state, task = _agent_and_work()
    seen = _script_completions(
        monkeypatch,
        ['{"say":"landing G0 now","actions":'],
    )
    outcome = await _human_turn(agent, state, "bad")
    assert len(seen) == 1
    assert outcome.result.get("parse_steer") is True
    events, notes = _why_counts(agent.id, task.id, BAD_JSON_WHY)
    assert events == 1
    assert notes == 1
    assert "Needs —" in str(outcome.result.get("chat_message", {}).get("content") or "")
    assert len(_work_wakes(outcome.result, task.id)) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


@pytest.mark.asyncio
async def test_repair_exhausted_after_committed_say_notes_once_and_requeues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(1)
    agent, state, task = _agent_and_work()
    broken = '{"say":"running pytest now","actions":'
    seen = _script_completions(monkeypatch, [broken, broken])
    outcome = await _human_turn(agent, state, "repair")
    assert len(seen) == 2
    events, notes = _why_counts(agent.id, task.id, REPAIR_EXHAUSTED_WHY)
    assert events == 1
    assert notes == 1
    assert len(_work_wakes(outcome.result, task.id)) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None


@pytest.mark.asyncio
async def test_committed_say_does_not_leave_a_silent_soft_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, task = _agent_and_work()
    apply_no_progress_block(agent, {"type": "human_chat"})
    assert db.get_task(task.id).status == "blocked"
    _script_completions(
        monkeypatch,
        ['{"say":"landing G0 now","actions":[]}'],
    )
    outcome = await _human_turn(agent, state, "blocked")
    events, notes = _why_counts(agent.id, task.id, EMPTY_ACTIONS_WHY)
    assert events == 1
    assert notes == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert len(_work_wakes(outcome.result, task.id)) == 1
    assert db.get_agent(agent.id) is not None
    assert db.get_agent_state(agent.id) is not None


@pytest.mark.asyncio
async def test_ambient_say_without_work_commitment_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, task = _agent_and_work()
    say = "Still reading the brief."
    _script_completions(monkeypatch, ['{"say":"%s","actions":[]}' % say])
    outcome = await _human_turn(agent, state, say)
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    assert refreshed.status != "blocked"
    thread = db.get_human_chat_thread(agent.id)
    assert any(say in (item.content or "") for item in thread)


@pytest.mark.asyncio
async def test_committed_say_with_actions_does_not_invent_the_gap_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, task = _agent_and_work()

    def _fake_cli(agent_obj, state_obj, command, content=None, **kwargs):
        del agent_obj, state_obj, content, kwargs
        return BossModCliResult(
            command=command,
            ok=True,
            detail="listed",
            prompt_content="BOSSMOD CLI RESULT\nlisted",
        )

    monkeypatch.setattr("core.agent_loop.decision_turn.execute_bm_cli", _fake_cli)
    _script_completions(
        monkeypatch,
        [
            '{"say":"landing G0 now","actions":[{"act":"cli","data":{"cmd":"ls /me"},"th":"list"}]}',
            '{"say":"Listed /me.","actions":[]}',
        ],
    )
    outcome = await _human_turn(agent, state, "cli")
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
