"""CLI approve resume must execute the flattened trigger, not fake-reject it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.loop import run_turn
from core.bm_cli.approvals import resume_cli_approval
from core.llm.client import LLMResponse

COMMAND = "curl https://example.invalid/probe"
_REJECT_MARK = "rejected by the operator"


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


class _RecordingServices:
    async def enqueue_trigger(self, **kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )


def _agent():
    return db.create_agent("Ada", role="Eng", model_work="test/mock", desk_x=1, desk_y=1)


def _dispatcher_trigger(trigger_id: str) -> dict[str, Any]:
    """Match TurnDispatcher._drain_queue: stored JSON becomes top-level fields."""
    claimed = db.claim_trigger(trigger_id)
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
    assert "payload" not in payload
    return payload


def _install_turn_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reask_on_reject: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    executed: list[dict[str, Any]] = []
    prompts: list[str] = []

    def _execute(agent, state, command, content=None, **kwargs):
        del agent, state
        executed.append(
            {
                "command": command,
                "content": content,
                "approval_request_id": kwargs.get("approval_request_id"),
                "cwd": kwargs.get("cwd"),
            }
        )

        class _Result:
            prompt_content = f"BOSSMOD CLI RESULT\ncommand: {command}\n\nexit 0"

        return _Result()

    async def _completion(**kwargs: Any) -> LLMResponse:
        joined = "\n".join(
            str(message.get("content") or "") for message in (kwargs.get("messages") or [])
        )
        prompts.append(joined)
        if reask_on_reject and _REJECT_MARK in joined:
            content = json.dumps({"act": "cli", "data": {"cmd": COMMAND}, "th": "retry"})
        else:
            content = '{"act":"idle","data":{},"th":"ran"}'
        return LLMResponse(
            content=content,
            model="test/mock",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    monkeypatch.setattr("core.bm_cli.runtime.execute_approved_command", _execute)
    monkeypatch.setattr("core.llm.client.completion", _completion)
    return executed, prompts


@pytest.mark.asyncio
async def test_approve_wake_executes_flattened_trigger_without_reask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    request = db.create_cli_approval_request(
        agent_id=agent.id,
        command=COMMAND,
        cwd="/me",
    )
    updated = await resume_cli_approval(request.id, approved=True, services=_RecordingServices())
    assert updated is not None
    assert updated.status == "approved"

    rows = db.list_agent_triggers(agent.id)
    queued = next(row for row in rows if row["trigger_type"] == "cli_approval_resolved")
    trigger = _dispatcher_trigger(queued["id"])
    assert trigger["status"] == "approved"
    assert trigger["command"] == COMMAND

    executed, prompts = _install_turn_doubles(monkeypatch, reask_on_reject=True)
    await run_turn(agent, state, trigger)

    assert executed == [
        {
            "command": COMMAND,
            "content": None,
            "approval_request_id": request.id,
            "cwd": "/me",
        }
    ]
    assert prompts
    assert _REJECT_MARK not in prompts[0]
    assert "No reason given." not in prompts[0]
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
    assert db.get_cli_approval_request(request.id).status == "approved"


@pytest.mark.asyncio
async def test_flat_always_allowed_executes_on_the_same_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    executed, prompts = _install_turn_doubles(monkeypatch)
    await run_turn(
        agent,
        state,
        {
            "type": "cli_approval_resolved",
            "status": "always_allowed",
            "command": COMMAND,
            "cwd": "/me",
            "approval_request_id": "always-1",
            "decision_note": "Always allowed",
            "trigger_id": "flat-always",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert executed[0]["command"] == COMMAND
    assert executed[0]["approval_request_id"] == "always-1"
    assert _REJECT_MARK not in prompts[0]


@pytest.mark.asyncio
async def test_missing_status_stays_rejected_and_nested_approved_still_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    executed, prompts = _install_turn_doubles(monkeypatch)

    await run_turn(
        agent,
        state,
        {
            "type": "cli_approval_resolved",
            "command": COMMAND,
            "trigger_id": "flat-missing",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert executed == []
    assert "No reason given." in prompts[0]
    assert "command: unknown" in prompts[0]

    await run_turn(
        agent,
        state,
        {
            "type": "cli_approval_resolved",
            "payload": {
                "status": "approved",
                "command": COMMAND,
                "cwd": "/tmp",
                "approval_request_id": "nested-1",
            },
            "trigger_id": "nested-approved",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert executed[-1]["command"] == COMMAND
    assert executed[-1]["approval_request_id"] == "nested-1"
    assert executed[-1]["cwd"] == "/tmp"
    assert _REJECT_MARK not in prompts[-1]


@pytest.mark.asyncio
async def test_flat_approved_wins_over_nested_reject_and_real_reject_keeps_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    executed, prompts = _install_turn_doubles(monkeypatch)

    await run_turn(
        agent,
        state,
        {
            "type": "cli_approval_resolved",
            "status": "approved",
            "command": COMMAND,
            "cwd": "/me",
            "approval_request_id": "flat-wins",
            "payload": {"status": "rejected", "decision_note": "nested noise", "command": "other"},
            "trigger_id": "flat-wins",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert executed[-1]["command"] == COMMAND
    assert executed[-1]["approval_request_id"] == "flat-wins"
    assert _REJECT_MARK not in prompts[-1]

    await run_turn(
        agent,
        state,
        {
            "type": "cli_approval_resolved",
            "status": "rejected",
            "command": COMMAND,
            "decision_note": "too dangerous",
            "trigger_id": "flat-reject",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert len(executed) == 1
    assert "too dangerous" in prompts[-1]
    assert "No reason given." not in prompts[-1]


@pytest.mark.asyncio
async def test_host_path_flat_always_allowed_grants_and_missing_status_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    _executed, prompts = _install_turn_doubles(monkeypatch)

    await run_turn(
        agent,
        state,
        {
            "type": "host_path_consent_resolved",
            "status": "always_allowed",
            "command": "request_host_access",
            "path": "/tmp/example",
            "trigger_id": "host-grant",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert "Host-path access granted for /tmp/example." in prompts[-1]
    assert "Host-path access denied." not in prompts[-1]

    await run_turn(
        agent,
        state,
        {
            "type": "host_path_consent_resolved",
            "command": "request_host_access",
            "path": "/tmp/example",
            "trigger_id": "host-missing",
            "source_channel": "system",
            "claim_generation": 1,
        },
    )
    assert "Host-path access denied." in prompts[-1]
