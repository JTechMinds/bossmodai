"""The serve loop paints Needs and channels while agent work runs elsewhere."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.decision_parse_fail import PARSE_FAIL_NOTE
from core.agent_loop.dispatcher import TurnDispatcher, _runtime_is_paused
from core.agent_loop.loop import run_turn
from core.bm_cli.shell_executor import (
    ShellExecutionResult,
    ShellOnRequestLoopError,
    execute_shell_command,
)
from core.llm.client import LLMResponse, close_provider_sessions, completion
from core.llm.system_completion import complete_text
from core.loop_breathing import (
    off_request_loop,
    run_shell_off_request_loop,
    shell_is_long,
    shell_uses_worker_process,
)
from core.messaging import route_human_channel_message
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


class _SilentBroadcast:
    async def broadcast_channel_message(self, **_kwargs: Any) -> None:
        return None


class _Services:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
    )


def test_stall_shell_returns_from_a_child_process(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db.set_setting("llm_stall_timeout_seconds", "1", "llm")
    config.reload()
    with caplog.at_level("WARNING", logger="core.bm_cli.shell_executor"):
        result = execute_shell_command(
            "echo hi-from-process",
            cwd=tmp_path,
            timeout_seconds=2,
        )
    assert "waiting on the worker thread" not in caplog.text
    assert result.exit_code == 0
    assert result.timed_out is False
    assert "hi-from-process" in result.stdout


def test_shell_wait_classes() -> None:
    assert shell_is_long(30) is True
    assert shell_is_long(1) is False
    assert shell_uses_worker_process(30) is False
    assert shell_uses_worker_process(120) is True
    assert shell_uses_worker_process(720) is True


@pytest.mark.asyncio
async def test_long_shell_refuses_the_request_loop(tmp_path: Path) -> None:
    with pytest.raises(ShellOnRequestLoopError):
        execute_shell_command("echo hi", cwd=tmp_path, timeout_seconds=30)


def test_stall_or_backstop_shell_uses_a_worker_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.set_setting("llm_stall_timeout_seconds", "1", "llm")
    config.reload()
    seen: dict[str, Any] = {}

    def _fake(command: str, **kwargs: Any) -> ShellExecutionResult:
        seen["command"] = command
        seen["timeout"] = kwargs.get("timeout_seconds")
        return ShellExecutionResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            duration_ms=1,
        )

    monkeypatch.setattr("core.bm_cli.shell_executor._execute_shell_in_process", _fake)
    result = execute_shell_command("echo hi", cwd=tmp_path, timeout_seconds=2)
    assert seen["command"] == "echo hi"
    assert seen["timeout"] == 2
    assert result.stdout == "ok"
    assert shell_uses_worker_process(2) is True


@pytest.mark.asyncio
async def test_needs_paint_runs_while_shell_blocks_a_worker(tmp_path: Path) -> None:
    """A multi-hundred-millisecond shell must not hold the serve loop."""
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(0.4)\nprint('done')\n", encoding="utf-8")
    painted: list[str] = []

    async def needs() -> None:
        await asyncio.sleep(0.05)
        painted.append("needs")

    started = time.monotonic()
    shell_task = asyncio.create_task(
        run_shell_off_request_loop(
            execute_shell_command,
            "python3 hang.py",
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )
    await needs()
    assert painted == ["needs"]
    assert time.monotonic() - started < 0.3
    result = await shell_task
    assert result.timed_out is False
    assert result.exit_code == 0
    assert "done" in result.stdout


@pytest.mark.asyncio
async def test_channel_route_system_ai_leaves_the_serve_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    db.set_setting("system_ai_connection", connection.id, "llm")
    config.reload()
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[jim.id, laura.id],
        created_by=HUMAN_SENDER_ID,
    )
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def _route(_messages: list[dict[str, str]], **_kwargs: Any) -> str:
        seen.append(threading.get_ident())
        ids = [jim.id, laura.id]
        return (
            '{"speak": ["'
            + ids[0]
            + '"], "stay_out": ["'
            + ids[1]
            + '"]}'
        )

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    services = _Services()
    result = await route_human_channel_message(
        channel_id=channel.id,
        channel_name=channel.name,
        content="Who has the next step?",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    assert result["round_id"]
    assert seen
    assert seen[0] != loop_thread
    assert services.calls


def test_system_ai_bad_request_is_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    config.reload()
    calls: list[dict[str, Any]] = []

    def _boom(**kwargs: Any) -> None:
        calls.append(kwargs)
        raise RuntimeError("BadRequestError")

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _boom)
    assert complete_text([{"role": "user", "content": "route"}]) is None
    assert len(calls) == 1
    assert calls[0]["num_retries"] == 0
    assert calls[0]["max_retries"] == 0


@pytest.mark.asyncio
async def test_decision_repair_yields_then_fail_closes_and_requeues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.set_setting("decision_repair_attempts", "1", "llm")
    config.reload()
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    yields: list[str] = []

    async def _breathe() -> None:
        yields.append("yield")
        await asyncio.sleep(0)

    monkeypatch.setattr("core.agent_loop.decision_turn.breathe", _breathe)
    queue = ["not json", "still prose"]

    async def _fake_completion(**_kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("repair continued after the budget")
        return _llm(queue.pop(0))

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    outcome = await run_turn(
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
    assert yields == ["yield"]
    assert queue == []
    assert outcome.trigger_status == "completed"
    assert outcome.result.get("parse_steer") is True
    notes = [
        item
        for item in db.list_task_events(task.id)
        if (item.content or "").strip() == PARSE_FAIL_NOTE
    ]
    assert len(notes) == 1
    wakes = [
        item
        for item in outcome.result.get("trigger_requests") or []
        if item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
    ]
    assert len(wakes) == 1
    assert db.get_task(task.id) is not None
    assert db.get_task(task.id).status != "complete"
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


def test_pause_freezes_new_wakes_immediately() -> None:
    agent = db.create_agent("Jim", role="Eng", desk_x=1, desk_y=1)
    db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "go", "from_name": "Human"},
    )
    db.set_setting("runtime_control_state", "paused", "advanced")
    assert _runtime_is_paused() is True
    dispatcher = TurnDispatcher()
    assert dispatcher._claim_available_trigger() is None
    queued = db.list_agent_triggers(agent.id, status="queued")
    assert len(queued) == 1
    db.set_setting("runtime_control_state", "running", "advanced")
    claimed = dispatcher._claim_available_trigger()
    assert claimed is not None
    assert claimed.agent_id == agent.id


@pytest.mark.asyncio
async def test_provider_sessions_close_on_call_end(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = asyncio.Event()

    async def _close() -> None:
        closed.set()

    monkeypatch.setattr("core.llm.client.litellm.close_litellm_async_clients", _close)

    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class _Done:
        choices = [_Choice()]
        model = "test/mock"
        usage = _Usage()

    async def _fake_acompletion(**_kwargs: Any) -> _Done:
        return _Done()

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    response = await completion(
        model="test/mock",
        messages=[{"role": "user", "content": "hi"}],
        extra_body='{"stream": false}',
    )
    assert response.content == "ok"
    assert closed.is_set()


@pytest.mark.asyncio
async def test_provider_sessions_stay_open_while_a_sibling_call_holds_a_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.llm.call_budget import budget

    lane = budget.try_acquire(kind="turn", owner="sibling")
    assert lane is not None
    called = False

    async def _close() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("core.llm.client.litellm.close_litellm_async_clients", _close)
    await close_provider_sessions(allow_inflight=0)
    assert called is False
    budget.release(lane)
    await close_provider_sessions(allow_inflight=0)
    assert called is True


@pytest.mark.asyncio
async def test_off_request_loop_helper_is_not_the_serve_thread() -> None:
    loop_thread = threading.get_ident()
    seen = await off_request_loop(threading.get_ident)
    assert seen != loop_thread
