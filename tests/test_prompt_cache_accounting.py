"""Accounting: steps carry the cache figures, turns derive them, metrics share them.

The figures for a call travel beside its prompt tokens into the step that
counts them (managed-writer sub-calls and the step-summary fold included).
``db.create_diagnostic`` derives the turn's totals from its steps and logs
an error when the turn's prompt tokens are not the sum over its steps. The
failed fold is a step 0, so that rule holds on every finalize path.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.loop import run_turn
from core.agent_loop.turn_helpers import _build_step_trace
from core.bm_cli.managed_writer.types import ManagedWriteOutcome
from core.bm_cli.results import success_result
from core.llm.client import LLMError, LLMResponse
from core.llm.prompt_cache_usage import PromptCacheUsage
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from db.connection import _apply_migrations, get_connection
from tests._connections import model_connection
from tests.test_live_work_continuation import (
    _BOARD_STEP,
    _STATUS_STEP,
    _SUMMARY_REPLY,
    _frozen_steps,
    _over_budget_work,
    _queue_status_question,
    _start_resume,
    _working_agent,
)

_CACHE_KEYS = ("cached_prompt_tokens", "cache_reported_prompt_tokens")


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


@pytest.fixture
def _char_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Saved work counts one token per character, so the fold's budgets are exact (as in the fold tests)."""
    monkeypatch.setattr("core.llm.client.count_text_tokens", len)
    monkeypatch.setattr("core.agent_loop.work_snapshot.count_text_tokens", len)


def _llm(content: str, *, prompt: int, cached: int | None) -> LLMResponse:
    return LLMResponse(
        content=content, model="test/mock", prompt_tokens=prompt, completion_tokens=10,
        total_tokens=prompt + 10, cached_prompt_tokens=cached,
    )


def _script(monkeypatch: pytest.MonkeyPatch, replies: list[LLMResponse | Exception]) -> None:
    """Each model call takes the next reply; an exception is raised instead."""
    queue = list(replies)

    async def _fake_completion(**_kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)


def _human_chat() -> dict[str, Any]:
    return {
        "type": "human_chat", "content": "Write the notes.", "from_name": "Human",
        "from_id": HUMAN_SENDER_ID, "source_channel": "chat",
    }


def _cache(row: dict[str, Any]) -> tuple[int, int, int]:
    return row["cached_prompt_tokens"], row["cache_reported_prompt_tokens"], row["prompt_tokens"]


def _last_turn(agent_id: str) -> dict[str, Any]:
    return db.get_diagnostic(db.get_diagnostics(agent_id, limit=1)[0]["id"])


def _consistency_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records
        if record.name == "db.diagnostics" and record.levelno >= logging.ERROR
    ]


def _turn_equals_its_steps(turn: dict[str, Any]) -> None:
    steps = turn["steps"]
    for key in ("prompt_tokens", *_CACHE_KEYS):
        assert turn[key] == sum(step[key] for step in steps), key


_REPLY = '{"act":"reply","work_commit":false,"intent":"status","msg":"Done.","th":"answer"}'
_WRITE = '{"act":"cli","data":{"cmd":"write /me/notes.md"},"th":"draft the notes"}'


@pytest.mark.asyncio
async def test_a_decision_step_carries_its_managed_writes_cache_beside_its_tokens(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    agent = db.create_agent("Ada", role="Engineer", connection_id=model_connection("test/mock"))

    async def _managed_write(**_kwargs: Any) -> ManagedWriteOutcome:
        return ManagedWriteOutcome(
            cli_result=success_result(
                command="write /me/notes.md", detail="Wrote /me/notes.md", kind="write", data={}, sections=[],
            ),
            prompt_tokens=500, completion_tokens=20, total_tokens=520,
            cache=PromptCacheUsage(cached_tokens=300, reported_prompt_tokens=500), chunks=1,
        )

    monkeypatch.setattr("core.agent_loop.decision_turn.run_managed_write", _managed_write)
    _script(monkeypatch, [_llm(_WRITE, prompt=1000, cached=400), _llm(_REPLY, prompt=1000, cached=None)])

    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        outcome = await run_turn(agent, db.get_agent_state(agent.id), _human_chat())

    assert outcome.trigger_status == "completed"
    turn = _last_turn(agent.id)
    write_step, reply_step = turn["steps"]
    assert write_step["action_name"] == "bm_cli"
    # The action's call (400 of 1,000) plus the write's sub-calls (300 of 500).
    assert _cache(write_step) == (700, 1500, 1500)
    # The reply's provider reported nothing: its prompt tokens are not "reported".
    assert _cache(reply_step) == (0, 0, 1000)
    assert _cache(turn) == (700, 1500, 2500)
    _turn_equals_its_steps(turn)
    assert _consistency_errors(caplog) == []


@pytest.mark.asyncio
async def test_mixed_reporting_counts_only_the_reported_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    agent = db.create_agent("Bea", role="Engineer", connection_id=model_connection("test/mock"))
    # Broken JSON first (a repair step), then a valid reply whose provider said nothing.
    _script(monkeypatch, [_llm("not json at all", prompt=800, cached=250), _llm(_REPLY, prompt=900, cached=None)])

    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        await run_turn(agent, db.get_agent_state(agent.id), _human_chat())

    turn = _last_turn(agent.id)
    assert [_cache(step) for step in turn["steps"]] == [(250, 800, 800), (0, 0, 900)]
    assert _cache(turn) == (250, 800, 1700)
    _turn_equals_its_steps(turn)
    assert _consistency_errors(caplog) == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_char_tokens")
async def test_a_successful_fold_is_step_0_with_its_cache(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    agent, task = _working_agent()
    _over_budget_work(agent)
    _frozen_steps(agent, _STATUS_STEP)
    _queue_status_question(agent.id)  # pauses the turn after its first step
    _script(monkeypatch, [_llm(_SUMMARY_REPLY, prompt=900, cached=600), _llm(_BOARD_STEP, prompt=1200, cached=0)])

    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    turn = _last_turn(agent.id)
    fold, board = turn["steps"]
    assert (fold["step_index"], fold["action_name"]) == (0, "summarize_steps")
    assert _cache(fold) == (600, 900, 900)
    # A reported 0 is reported: 0 of 1,200 cached, not "not reported".
    assert _cache(board) == (0, 1200, 1200)
    assert _cache(turn) == (600, 2100, 2100)
    _turn_equals_its_steps(turn)
    assert _consistency_errors(caplog) == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_char_tokens")
async def test_a_failed_fold_is_step_0_with_its_tokens_cache_and_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    agent, task = _working_agent()
    _over_budget_work(agent)  # the summary limit is 150 tokens
    _frozen_steps(agent, _STATUS_STEP)
    # An over-limit summary, then the shorter request fails.
    _script(monkeypatch, [_llm("x" * 200, prompt=700, cached=350), LLMError("stalled")])

    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        outcome = await run_turn(agent, db.get_agent_state(agent.id), _start_resume(agent.id, task.id))

    assert outcome.trigger_status == "failed" and outcome.retryable is True
    assert (outcome.prompt_tokens, outcome.completion_tokens, outcome.total_tokens) == (700, 10, 710)
    (fold,) = outcome.steps
    assert (fold["step_index"], fold["action_name"]) == (0, "summarize_steps")
    assert (fold["prompt_tokens"], fold["completion_tokens"], fold["total_tokens"]) == (700, 10, 710)
    assert _cache(fold) == (350, 700, 700)
    assert fold["error"] == "Step summary failed: The step summary call failed: stalled"
    assert isinstance(fold["duration_ms"], int) and fold["duration_ms"] >= 0

    turn = _last_turn(agent.id)
    assert _cache(turn) == (350, 700, 700)
    _turn_equals_its_steps(turn)
    assert _consistency_errors(caplog) == []


_PEEK = '{"act":"cli","data":{"cmd":"%s"},"th":"peek"}'


@pytest.mark.asyncio
async def test_a_peek_budget_hard_stop_records_the_refused_call_as_its_last_step(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    agent = db.create_agent("Cleo", role="Engineer", connection_id=model_connection("test/mock"))
    # Two looks at one folder run, the third is steered, the fourth stops the turn.
    _script(monkeypatch, [
        _llm(_PEEK % "ls a", prompt=600, cached=None),
        _llm(_PEEK % "ls a/", prompt=700, cached=100),
        _llm(_PEEK % "ls ./a", prompt=800, cached=200),
        _llm(_PEEK % "ls a", prompt=900, cached=300),
    ])

    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        outcome = await run_turn(agent, db.get_agent_state(agent.id), _human_chat())

    assert outcome.trigger_status == "failed"
    assert outcome.result["peek_budget"] == "identical_loop"
    turn = _last_turn(agent.id)
    refused = turn["steps"][-1]
    assert (refused["step_index"], refused["action_name"]) == (4, "bm_cli")
    assert json.loads(refused["parsed_action"])["command"] == "ls a"
    assert refused["error"] == outcome.diagnostic_error
    assert (refused["prompt_tokens"], refused["completion_tokens"], refused["total_tokens"]) == (900, 10, 910)
    assert _cache(refused) == (300, 900, 900)
    assert _cache(turn) == (600, 2400, 3000)
    _turn_equals_its_steps(turn)
    assert _consistency_errors(caplog) == []


# ─── create_diagnostic's consistency check ───


def _step(prompt: int, cache: PromptCacheUsage) -> dict[str, Any]:
    return _build_step_trace(
        step_index=1, context_snapshot=None, raw_response="r", action={"action": "bm_cli"}, result=None,
        prompt_tokens=prompt, context_prompt_tokens=prompt, completion_tokens=1, total_tokens=prompt + 1,
        cache=cache, duration_ms=5,
    )


def test_a_turn_whose_steps_do_not_add_up_is_logged_and_still_written(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        row = db.create_diagnostic(
            agent_id="a", agent_name="Ada", trigger_type="t", trigger_data="{}",
            prompt_tokens=100, steps=[_step(60, PromptCacheUsage(20, 60))],
        )

    assert _consistency_errors(caplog) == [
        "Diagnostics for Ada: the turn has 100 prompt tokens but its steps sum to 60",
    ]
    stored = db.get_diagnostic(row["id"])
    assert _cache(stored) == (20, 60, 100)
    assert _cache(stored["steps"][0]) == (20, 60, 60)


def test_prompt_tokens_with_no_steps_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        row = db.create_diagnostic(agent_id="a", agent_name="Ada", trigger_type="t", trigger_data="{}", prompt_tokens=50)

    assert _consistency_errors(caplog) == [
        "Diagnostics for Ada: the turn has 50 prompt tokens but no steps to account for them",
    ]
    assert _cache(db.get_diagnostic(row["id"])) == (0, 0, 50)


def test_a_turn_with_no_steps_and_no_tokens_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="db.diagnostics"):
        row = db.create_diagnostic(agent_id="a", agent_name="Ada", trigger_type="t", trigger_data="{}")

    assert _consistency_errors(caplog) == []
    assert _cache(db.get_diagnostic(row["id"])) == (0, 0, 0)


def test_a_step_record_without_the_cache_keys_is_a_bug() -> None:
    with pytest.raises(KeyError):
        db.create_diagnostic(
            agent_id="a", agent_name="Ada", trigger_type="t", trigger_data="{}",
            steps=[{"step_index": 0, "prompt_tokens": 0}],
        )


# ─── Migration ───


def test_a_database_from_before_the_change_reads_not_reported() -> None:
    con = get_connection()
    for table in ("diagnostic_steps", "diagnostics"):
        for column in _CACHE_KEYS:
            con.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    diagnostic_id = con.execute(
        "INSERT INTO diagnostics (agent_id, agent_name, trigger_type, trigger_data, prompt_tokens) "
        "VALUES ('a', 'Ada', 't', '{}', 120) RETURNING id"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO diagnostic_steps (diagnostic_id, step_index, prompt_tokens) VALUES (?, 0, 120)",
        [diagnostic_id],
    )

    _apply_migrations(con)

    for table in ("diagnostics", "diagnostic_steps"):
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        assert set(_CACHE_KEYS) <= columns, table
    stored = db.get_diagnostic(diagnostic_id)
    assert _cache(stored) == (0, 0, 120)
    assert _cache(stored["steps"][0]) == (0, 0, 120)


# ─── Metrics ───

START = datetime(2001, 3, 10, 5, tzinfo=timezone.utc)
END = START + timedelta(days=1)
_INSIDE = "2001-03-10 12:00:00"
_OUTSIDE = "2001-03-12 12:00:00"


def _turn(agent_id: str, created_at: str, *, prompt: int, cached: int, reported: int) -> None:
    db.execute(
        """
        INSERT INTO diagnostics (agent_id, agent_name, trigger_type, trigger_data, prompt_tokens,
                                 completion_tokens, cached_prompt_tokens, cache_reported_prompt_tokens,
                                 created_at)
        VALUES (?, ?, 'test', '{}', ?, 5, ?, ?, ?)
        """,
        [agent_id, agent_id.upper(), prompt, cached, reported, created_at],
    )


def _block(prompt: int, cached: int, reported: int, cached_share: float | None, unreported_share: float | None):
    return {
        "prompt_tokens": prompt, "cached_tokens": cached, "reported_prompt_tokens": reported,
        "cached_share": cached_share, "unreported_share": unreported_share,
    }


def _seed_turns() -> None:
    _turn("a1", _INSIDE, prompt=1000, cached=400, reported=1000)
    _turn("a1", _INSIDE, prompt=500, cached=0, reported=0)  # its provider said nothing
    _turn("a2", _INSIDE, prompt=0, cached=0, reported=0)  # a turn with no model call
    _turn("a3", _OUTSIDE, prompt=2000, cached=2000, reported=2000)


def test_the_period_block_shares_cached_over_all_prompt_tokens() -> None:
    _seed_turns()

    tokens = db.get_dashboard_metrics(START, END)["tokens"]

    assert tokens["prompt_cache"] == _block(1500, 400, 1000, 400 / 1500, 500 / 1500)
    by_agent = {row["agent_id"]: row["prompt_cache"] for row in tokens["by_agent"]}
    assert by_agent == {
        "a1": _block(1500, 400, 1000, 400 / 1500, 500 / 1500),
        # No prompt tokens is "no data", never "0% cached".
        "a2": _block(0, 0, 0, None, None),
    }


def test_a_quiet_period_has_no_shares() -> None:
    _seed_turns()

    tokens = db.get_dashboard_metrics(START - timedelta(days=30), START - timedelta(days=29))["tokens"]

    assert tokens["prompt_cache"] == _block(0, 0, 0, None, None)
    assert tokens["by_agent"] == []


def test_agent_stats_carry_an_all_time_block() -> None:
    _seed_turns()

    stats = db.get_agent_stats_batch()

    assert stats["a1"]["prompt_cache"] == _block(1500, 400, 1000, 400 / 1500, 500 / 1500)
    assert stats["a3"]["prompt_cache"] == _block(2000, 2000, 2000, 1.0, 0.0)
    assert db.prompt_cache_block(0, 0, 0) == _block(0, 0, 0, None, None)


def test_the_company_roster_gives_an_agent_with_no_turns_a_zero_block() -> None:
    worked = db.create_agent("Worked", role="Eng")
    idle = db.create_agent("Idle", role="Eng")
    _turn(worked.id, _INSIDE, prompt=1000, cached=410, reported=1000)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)

    response = TestClient(app).get(
        "/api/company/agents?include=stats", headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
    )

    assert response.status_code == 200
    blocks = {row["id"]: row["prompt_cache"] for row in response.json()}
    assert blocks[worked.id] == _block(1000, 410, 1000, 0.41, 0.0)
    assert blocks[idle.id] == _block(0, 0, 0, None, None)
