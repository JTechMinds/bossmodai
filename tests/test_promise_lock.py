"""A say that commits to work must not ghost after a bad envelope."""

from __future__ import annotations

import json
import os
import re
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
    response_commits_to_work,
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


# The retired #145 gate. Production must not use it. "Committing now to land …"
# does not match, which is the gap this lock closes.
_RETIRED_PHRASE_GATE = re.compile(
    r"(?is)\b(?:landing|writing|running\s+pytest)\b.{0,80}?\bnow\b"
)
COMMIT_NOW = "Committing now to land the patch"


def test_commit_now_phrasing_misses_the_retired_phrase_gate() -> None:
    assert _RETIRED_PHRASE_GATE.search(COMMIT_NOW) is None
    assert _RETIRED_PHRASE_GATE.search("landing G0 now") is not None


def test_work_commit_flag_is_the_decision_and_skips_system_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("System AI must not run when work_commit is set")

    monkeypatch.setattr("core.agent_loop.promise_lock.complete_text", _boom)
    assert say_commits_to_work(COMMIT_NOW, work_commit=True) is True
    assert say_commits_to_work("Still reading the brief.", work_commit=True) is True
    assert say_commits_to_work("landing G0 now", work_commit=False) is False
    broken = '{"say":"%s","work_commit":true,"actions":' % COMMIT_NOW
    assert response_commits_to_work(broken) is True
    quiet = '{"say":"landing G0 now","work_commit":false,"actions":'
    assert response_commits_to_work(quiet) is False


@pytest.mark.parametrize(
    ("say", "payload", "commits"),
    [
        (COMMIT_NOW, {"commits_to_work": True}, True),
        ("Still reading the brief.", {"commits_to_work": False}, False),
        ("landing G0 now", {"commits_to_work": False}, False),
        ("Committed and pushed.\n\n- Next: pytest on the clone.", {"commits_to_work": False}, False),
        ("I'll write it tomorrow.", {"commits_to_work": False}, False),
        (COMMIT_NOW, {"commits_to_work": True, "why": "phrase"}, False),
        (COMMIT_NOW, "not-json", False),
    ],
)
def test_omitted_flag_uses_system_ai_intent(
    monkeypatch: pytest.MonkeyPatch,
    say: str,
    payload: Any,
    commits: bool,
) -> None:
    seen: list[str] = []

    def _fake(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        seen.append(messages[-1]["content"])
        if isinstance(payload, str):
            return payload
        return json.dumps(payload)

    monkeypatch.setattr("core.agent_loop.promise_lock.complete_text", _fake)
    assert say_commits_to_work(say) is commits
    assert seen == [" ".join(say.split())]


def test_intent_prompt_does_not_use_a_phrase_list() -> None:
    from core.agent_loop.promise_lock import _INTENT_SYSTEM

    assert "phrase list" in _INTENT_SYSTEM
    assert "mention pills" in _INTENT_SYSTEM
    assert "landing" not in _INTENT_SYSTEM
    assert "pytest" not in _INTENT_SYSTEM


def test_omitted_flag_stays_quiet_when_system_ai_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.agent_loop.promise_lock.complete_text", lambda *_a, **_k: None)
    assert say_commits_to_work("landing G0 now") is False
    assert say_commits_to_work(COMMIT_NOW) is False
    assert say_commits_to_work("Still reading the brief.") is False
    assert response_commits_to_work('{"say":"landing G0 now","actions":') is False


def test_blank_say_does_not_ask_system_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("System AI must not run for a blank say")

    monkeypatch.setattr("core.agent_loop.promise_lock.complete_text", _boom)
    assert say_commits_to_work(None) is False
    assert say_commits_to_work("  ") is False


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
        ['{"say":"%s","actions":[],"work_commit":true}' % say],
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
        ['{"say":"landing G0 now","work_commit":true,"actions":'],
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
    broken = '{"say":"running pytest now","work_commit":true,"actions":'
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
        ['{"say":"landing G0 now","actions":[],"work_commit":true}'],
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
    asked = _stub_intent(monkeypatch, False)
    _script_completions(monkeypatch, ['{"say":"%s","actions":[]}' % say])
    outcome = await _human_turn(agent, state, say)
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    assert asked == [say]
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
    asked = _stub_intent(monkeypatch, True)
    _script_completions(
        monkeypatch,
        [
            '{"say":"landing G0 now","work_commit":true,"actions":'
            '[{"act":"cli","data":{"cmd":"ls /me"},"th":"list"}]}',
            '{"say":"Listed /me.","actions":[],"work_commit":false}',
        ],
    )
    outcome = await _human_turn(agent, state, "cli")
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    assert asked == []
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


def _stub_intent(monkeypatch: pytest.MonkeyPatch, commits: bool) -> list[str]:
    seen: list[str] = []

    def _fake(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        seen.append(messages[-1]["content"])
        return json.dumps({"commits_to_work": commits})

    monkeypatch.setattr("core.agent_loop.promise_lock.complete_text", _fake)
    return seen


def _forbid_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("System AI must not run when work_commit is set")

    monkeypatch.setattr("core.agent_loop.promise_lock.complete_text", _boom)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "bad_json", "repair"])
@pytest.mark.parametrize("signal", ["flag", "system"])
async def test_commit_intent_phrasing_notes_and_requeues(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    signal: str,
) -> None:
    """Phrasing the retired phrase list misses still Needs and re-queues."""
    if mode == "repair":
        _limit_decision_repairs(1)
    elif mode == "bad_json":
        _limit_decision_repairs(0)
    agent, state, task = _agent_and_work()
    if signal == "flag":
        _forbid_intent(monkeypatch)
        asked: list[str] = []
        body = '{"say":"%s","actions":[],"work_commit":true}' % COMMIT_NOW
        broken = '{"say":"%s","work_commit":true,"actions":' % COMMIT_NOW
    else:
        asked = _stub_intent(monkeypatch, True)
        body = '{"say":"%s","actions":[]}' % COMMIT_NOW
        broken = '{"say":"%s","actions":' % COMMIT_NOW
    if mode == "empty":
        contents = [body]
        why = EMPTY_ACTIONS_WHY
    elif mode == "bad_json":
        contents = [broken]
        why = BAD_JSON_WHY
    else:
        contents = [broken, broken]
        why = REPAIR_EXHAUSTED_WHY
    seen = _script_completions(monkeypatch, contents)
    outcome = await _human_turn(agent, state, mode)
    assert len(seen) == (2 if mode == "repair" else 1)
    if mode == "empty":
        assert outcome.result.get("event") == "decision_applied"
        thread = db.get_human_chat_thread(agent.id)
        assert any(COMMIT_NOW in (item.content or "") for item in thread)
    else:
        assert outcome.result.get("parse_steer") is True
    events, notes = _why_counts(agent.id, task.id, why)
    assert events == 1
    assert notes == 1
    assert len(_work_wakes(outcome.result, task.id)) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
    if signal == "system":
        assert asked == [COMMIT_NOW]
    else:
        assert asked == []


@pytest.mark.asyncio
async def test_old_phrase_without_commit_intent_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, task = _agent_and_work()
    asked = _stub_intent(monkeypatch, False)
    say = "landing G0 now"
    _script_completions(monkeypatch, ['{"say":"%s","actions":[]}' % say])
    outcome = await _human_turn(agent, state, say)
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    assert asked == [say]
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    assert refreshed.status != "blocked"
    thread = db.get_human_chat_thread(agent.id)
    assert any(say in (item.content or "") for item in thread)
    assert db.get_agent(agent.id) is not None


@pytest.mark.asyncio
async def test_explicit_non_commit_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, task = _agent_and_work()
    _forbid_intent(monkeypatch)
    say = "landing G0 now"
    _script_completions(
        monkeypatch,
        ['{"say":"%s","actions":[],"work_commit":false}' % say],
    )
    outcome = await _human_turn(agent, state, say)
    assert outcome.result.get("event") == "decision_applied"
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None


@pytest.mark.asyncio
async def test_bad_json_old_phrase_without_intent_does_not_post_the_promise_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(0)
    agent, state, task = _agent_and_work()
    asked = _stub_intent(monkeypatch, False)
    _script_completions(monkeypatch, ['{"say":"landing G0 now","actions":'])
    outcome = await _human_turn(agent, state, "bad-phrase")
    assert outcome.result.get("parse_steer") is True
    events, notes = _why_counts(agent.id, task.id, "Needs —")
    assert events == 0
    assert notes == 0
    assert asked == ["landing G0 now"]
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
