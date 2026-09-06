"""Decision-turn peek budget: free consent, soft 10, identical-triple stop."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.decision_peek import (
    IDENTICAL_PEEK_STEER,
    SOFT_PEEK_BUDGET,
    SOFT_PEEK_STEER,
    DecisionPeekBudget,
    normalize_peek_fingerprint,
)
from core.agent_loop.loop import run_turn
from core.bm_cli.types import BossModCliResult
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


def teardown_function() -> None:
    db.close_connection()


def _cli(cmd: str) -> str:
    return '{"act":"cli","data":{"cmd":"%s"},"th":"peek"}' % cmd


def _reply(msg: str = "Ready.") -> str:
    return '{"act":"reply","intent":"question","msg":"%s","th":"answer"}' % msg


def _host_access(path: str, why: str = "Need the file") -> str:
    return (
        '{"act":"request_host_access","data":{"path":"%s","why":"%s"},"th":"ask"}'
        % (path, why)
    )


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
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        content = queue.pop(0)
        seen.append(content)
        return _llm(content)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen


def _agent_and_state():
    agent = db.create_agent("Peek Clerk", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _human_chat_trigger() -> dict[str, Any]:
    return {
        "type": "human_chat",
        "content": "What is in the workspace?",
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "chat",
    }


# ---------------------------------------------------------------------------
# Fingerprint normalization
# ---------------------------------------------------------------------------


def test_peek_fingerprint_normalizes_path_tweaks() -> None:
    assert normalize_peek_fingerprint("ls a") == normalize_peek_fingerprint("ls a/")
    assert normalize_peek_fingerprint("ls a") == normalize_peek_fingerprint("ls ./a")
    assert normalize_peek_fingerprint("ls a") == normalize_peek_fingerprint("ls ./a/")
    assert normalize_peek_fingerprint("ls  a") == normalize_peek_fingerprint("ls a")
    assert normalize_peek_fingerprint("ls /me/notes") == normalize_peek_fingerprint("ls /me/notes/")
    assert normalize_peek_fingerprint("cat /me/foo.md") == normalize_peek_fingerprint("cat /me/foo.md/")
    assert normalize_peek_fingerprint("ls -l a/") == normalize_peek_fingerprint("ls -l a")
    assert normalize_peek_fingerprint("ls") == normalize_peek_fingerprint("ls .")
    assert normalize_peek_fingerprint("ls") == normalize_peek_fingerprint("ls ./")
    assert normalize_peek_fingerprint("ls a") != normalize_peek_fingerprint("ls b")
    assert normalize_peek_fingerprint("ls a") != normalize_peek_fingerprint("cat a")


def test_peek_budget_soft_ten_and_identical_triple() -> None:
    budget = DecisionPeekBudget()
    for index in range(SOFT_PEEK_BUDGET):
        verdict = budget.consider(f"ls /me/p{index}")
        assert verdict.allowed is True
        assert verdict.reason is None

    exhausted = budget.consider("ls /me/other")
    assert exhausted.allowed is False
    assert exhausted.reason == "soft_budget"
    assert exhausted.steer == SOFT_PEEK_STEER

    loop = DecisionPeekBudget()
    assert loop.consider("ls a").allowed is True
    assert loop.consider("ls a/").allowed is True
    third = loop.consider("ls ./a")
    assert third.allowed is False
    assert third.reason == "identical_loop"
    assert third.steer == IDENTICAL_PEEK_STEER


def test_peek_budget_counts_total_peeks_not_recyclable_identities() -> None:
    budget = DecisionPeekBudget()
    for index in range(SOFT_PEEK_BUDGET):
        command = "ls a" if index % 2 == 0 else "ls b"
        assert budget.consider(command).allowed is True
    assert budget.peek_count == SOFT_PEEK_BUDGET

    reused = budget.consider("ls a")
    assert reused.allowed is False
    assert reused.reason == "soft_budget"
    assert reused.steer == SOFT_PEEK_STEER

    other = DecisionPeekBudget()
    for index in range(SOFT_PEEK_BUDGET):
        assert other.consider(f"ls /me/p{index}").allowed is True
    assert other.consider("ls /me/p0").allowed is False
    assert other.consider("ls /me/p0").reason == "soft_budget"


# ---------------------------------------------------------------------------
# Decision-turn integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_four_peeks_then_decide_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    _script_completions(
        monkeypatch,
        [_cli(f"ls /me/p{index}") for index in range(4)] + [_reply("Four peeks in.")],
    )
    outcome = await run_turn(agent, state, _human_chat_trigger())
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("event") != "agent_error"
    assert "peek budget" not in (outcome.diagnostic_error or "")
    assert sum(1 for step in outcome.steps if step.get("action_name") == "bm_cli") == 4


@pytest.mark.asyncio
async def test_ten_total_peeks_then_decide_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    _script_completions(
        monkeypatch,
        [_cli(f"ls /me/p{index}") for index in range(SOFT_PEEK_BUDGET)] + [_reply("Budget used.")],
    )
    outcome = await run_turn(agent, state, _human_chat_trigger())
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("event") != "agent_error"
    assert sum(1 for step in outcome.steps if step.get("action_name") == "bm_cli") == SOFT_PEEK_BUDGET


@pytest.mark.asyncio
async def test_eleventh_peek_fails_soft_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    broadcasts: list[dict[str, Any]] = []

    async def _capture_activity(**kwargs: Any) -> None:
        broadcasts.append(kwargs)

    monkeypatch.setattr(
        "core.agent_loop.decision_turn.manager.broadcast_activity",
        _capture_activity,
    )
    _script_completions(
        monkeypatch,
        [_cli(f"ls /me/p{index}") for index in range(SOFT_PEEK_BUDGET + 1)],
    )
    outcome = await run_turn(agent, state, _human_chat_trigger())
    assert outcome.trigger_status == "failed"
    assert outcome.result.get("event") == "agent_error"
    assert outcome.result.get("peek_budget") == "soft_budget"
    assert SOFT_PEEK_STEER in (outcome.diagnostic_error or "")
    assert SOFT_PEEK_STEER in str(outcome.result.get("detail") or "")
    errors = [item for item in broadcasts if item.get("event") == "agent_error"]
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_alternating_known_peeks_still_hit_soft_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    broadcasts: list[dict[str, Any]] = []

    async def _capture_activity(**kwargs: Any) -> None:
        broadcasts.append(kwargs)

    monkeypatch.setattr(
        "core.agent_loop.decision_turn.manager.broadcast_activity",
        _capture_activity,
    )
    peeks = [_cli("ls a" if index % 2 == 0 else "ls b") for index in range(SOFT_PEEK_BUDGET + 1)]
    _script_completions(monkeypatch, peeks)
    outcome = await run_turn(agent, state, _human_chat_trigger())
    assert outcome.trigger_status == "failed"
    assert outcome.result.get("event") == "agent_error"
    assert outcome.result.get("peek_budget") == "soft_budget"
    assert SOFT_PEEK_STEER in (outcome.diagnostic_error or "")
    assert sum(1 for step in outcome.steps if step.get("action_name") == "bm_cli") == SOFT_PEEK_BUDGET
    errors = [item for item in broadcasts if item.get("event") == "agent_error"]
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_identical_triple_hard_stops_before_third_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    executed: list[str] = []
    real_execute = __import__("core.bm_cli.runtime", fromlist=["execute_bm_cli"]).execute_bm_cli

    def _count_execute(agent_obj, state_obj, command, content=None, **kwargs):
        executed.append(command)
        return real_execute(agent_obj, state_obj, command, content, **kwargs)

    monkeypatch.setattr("core.agent_loop.decision_turn.execute_bm_cli", _count_execute)
    broadcasts: list[dict[str, Any]] = []

    async def _capture_activity(**kwargs: Any) -> None:
        broadcasts.append(kwargs)

    monkeypatch.setattr(
        "core.agent_loop.decision_turn.manager.broadcast_activity",
        _capture_activity,
    )
    _script_completions(monkeypatch, [_cli("ls a"), _cli("ls a/"), _cli("ls ./a")])
    outcome = await run_turn(agent, state, _human_chat_trigger())
    assert outcome.trigger_status == "failed"
    assert outcome.result.get("peek_budget") == "identical_loop"
    assert IDENTICAL_PEEK_STEER in (outcome.diagnostic_error or "")
    assert IDENTICAL_PEEK_STEER in str(outcome.result.get("detail") or "")
    assert executed == ["ls a", "ls a/"]
    errors = [item for item in broadcasts if item.get("event") == "agent_error"]
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_request_host_access_does_not_burn_peek_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "allowed-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("ok\n", encoding="utf-8")
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()

    agent, state = _agent_and_state()
    peeks = [_cli(f"ls /me/p{index}") for index in range(SOFT_PEEK_BUDGET)]
    _script_completions(
        monkeypatch,
        [_host_access(str(fixture)), *peeks, _reply("Consent was free.")],
    )
    outcome = await run_turn(agent, state, _human_chat_trigger())
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("event") != "agent_error"
    assert sum(1 for step in outcome.steps if step.get("action_name") == "request_host_access") == 1
    assert sum(1 for step in outcome.steps if step.get("action_name") == "bm_cli") == SOFT_PEEK_BUDGET


@pytest.mark.asyncio
async def test_decision_cli_forwards_origin_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    peer = db.create_agent("Channel Peer")
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[agent.id, peer.id],
        created_by=agent.id,
    )
    captured: dict[str, Any] = {}

    def _fake_cli(agent_obj, state_obj, command, content=None, **kwargs):
        captured["channel_id"] = kwargs.get("channel_id")
        captured["trigger_type"] = kwargs.get("trigger_type")
        return BossModCliResult(
            command=command,
            ok=True,
            detail="listed",
            prompt_content="BOSSMOD CLI RESULT\nlisted",
        )

    monkeypatch.setattr("core.agent_loop.decision_turn.execute_bm_cli", _fake_cli)
    _script_completions(monkeypatch, [_cli("ls /me"), _reply("Saw the listing.")])
    trigger = {
        "type": "human_chat",
        "content": "What is in /me?",
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "channel",
        "channel_id": channel.id,
        "author_type": "human",
    }
    outcome = await run_turn(agent, state, trigger)
    assert outcome.trigger_status == "completed"
    assert captured.get("channel_id") == channel.id
    assert captured.get("trigger_type") == "human_chat"
