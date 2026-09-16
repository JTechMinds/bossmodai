"""Shell Executor in-thread consent when Branch is locked and shell is off."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.blocked_origin import (
    SHELL_EXECUTOR_WHY,
    format_blocked_line,
    is_shell_executor_deny_result,
)
from core.agent_loop.notifications import persist_chat_notification, project_chat_notifications
from core.agent_loop.runtime_core import LOCKED_WORKSPACE_COPY_STEER, format_runtime_core_block
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.models.host_path_consent import (
    SHELL_EXECUTOR_CARD_COPY,
    SHELL_EXECUTOR_KIND,
    SHELL_EXECUTOR_TITLE,
)
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking.service import create_or_bind_task
from tests.test_workspace_preference import (
    _agent_and_state,
    _allow_host,
    _init_tiny_pytest_repo,
    _lock_workspace_copy,
)


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


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _persist_trigger(**kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _persist_trigger)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def test_shell_still_defaults_false() -> None:
    assert config.get("cli_shell_enabled") == "false"


def test_pytest_without_locked_copy_is_disabled_error_not_a_card() -> None:
    agent, state = _agent_and_state()
    result = execute_bm_cli(agent, state, "pytest -q")
    assert result.ok is False
    assert result.consent_required is False
    assert "shell execution is not enabled" in (result.data or {}).get("error", "")


def test_locked_clone_pytest_posts_shell_executor_card() -> None:
    agent, state = _agent_and_state()
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    paused = execute_bm_cli(agent, state, "pytest -q")
    assert paused.ok is False
    assert paused.consent_required is True
    assert paused.kind == "shell_executor_consent_required"
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == SHELL_EXECUTOR_KIND
    assert card["title"] == SHELL_EXECUTOR_TITLE
    assert card.get("channel_id") is None
    assert "shell execution is not enabled" not in (paused.data or {}).get("error", "")
    assert SHELL_EXECUTOR_CARD_COPY in (paused.detail or "")
    assert config.get("cli_shell_enabled") == "false"


def test_locked_clone_git_add_posts_shell_executor_card() -> None:
    agent, state = _agent_and_state()
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    paused = execute_bm_cli(agent, state, "git add tests/test_ok.py")
    assert paused.consent_required is True
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == SHELL_EXECUTOR_KIND
    assert card.get("command") == "git add tests/test_ok.py"


def test_bash_on_locked_clone_stays_never_allowed_without_a_card() -> None:
    agent, state = _agent_and_state()
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    blocked = execute_bm_cli(agent, state, "bash scripts/run-tests.sh")
    assert blocked.ok is False
    assert blocked.consent_required is False
    python_pytest = execute_bm_cli(agent, state, "python -m pytest -q")
    assert python_pytest.consent_required is False
    peek = policy_engine.evaluate("bash scripts/run-tests.sh", frozenset(), assume_shell=True)
    assert peek.tier == "never_allowed"


def test_shell_executor_card_stamps_channel_id() -> None:
    agent, state = _agent_and_state()
    channel = db.create_channel(
        name="Validate",
        member_agent_ids=[agent.id],
        created_by=HUMAN_SENDER_ID,
    )
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    paused = execute_bm_cli(
        agent, state, "pytest -q", channel_id=channel.id
    )
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card.get("channel_id") == channel.id
    stored = db.get_consent_request(paused.consent_request_id)
    assert stored is not None
    assert stored.channel_id == channel.id


def test_shell_executor_card_copy_is_enable_or_deny() -> None:
    agent, state = _agent_and_state()
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    paused = execute_bm_cli(agent, state, "pytest -q")
    notes = project_chat_notifications(
        agent=agent,
        trigger={"type": "human_chat", "source_channel": "chat"},
        active_activity=None,
        action={"action": "bm_cli"},
        result={
            "event": "shell_executor_consent_required",
            "consent_required": True,
            "consent_request_id": paused.consent_request_id,
            "consent_reused": False,
            "host_path_consent": (paused.data or {}).get("host_path_consent"),
        },
    )
    assert len(notes) == 1
    assert notes[0].content == f"{agent.name} {SHELL_EXECUTOR_CARD_COPY}"
    persist_chat_notification(agent, notes[0])
    retry = execute_bm_cli(agent, state, "pytest -q tests/test_ok.py")
    assert retry.consent_required is True
    assert retry.consent_request_id == paused.consent_request_id
    assert (retry.data or {}).get("consent_reused") is True
    second = project_chat_notifications(
        agent=agent,
        trigger={"type": "human_chat", "source_channel": "chat"},
        active_activity=None,
        action={"action": "bm_cli"},
        result={
            "event": "shell_executor_consent_required",
            "consent_required": True,
            "consent_request_id": retry.consent_request_id,
            "consent_reused": True,
            "host_path_consent": (retry.data or {}).get("host_path_consent"),
        },
    )
    assert second == []


def test_enable_turns_shell_on_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "llm_helper"
    host.mkdir()
    _init_tiny_pytest_repo(host)
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused_pref = execute_bm_cli(agent, state, f"write {host / 'note.txt'}", content="x\n")
    request_id = paused_pref.consent_request_id
    assert request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 200, branched.text
    dest = branched.json().get("clone_dest") or ""
    assert dest.startswith("/me/host-work/")
    assert config.get("cli_shell_enabled") == "false"

    cd = execute_bm_cli(agent, state, f"cd {dest}")
    assert cd.ok is True
    paused = execute_bm_cli(agent, state, "pytest -q tests/test_ok.py")
    assert paused.consent_required is True
    shell_id = paused.consent_request_id
    assert shell_id
    enabled = client.post(f"/api/shell-executor/{shell_id}/enable", headers=_headers())
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "enabled"
    assert config.get("cli_shell_enabled") == "true"

    triggers = db.list_agent_triggers(agent.id, status="queued")
    resume = []
    for row in triggers:
        raw = row.get("payload")
        if isinstance(raw, str):
            import json

            payload = json.loads(raw) if raw else {}
        else:
            payload = raw or {}
        if row.get("trigger_type") == "host_path_consent_resolved" and payload.get("status") == "enabled":
            resume.append(payload)
    assert resume, "Enable must wake the waiting agent"
    assert resume[0].get("command") == "pytest -q tests/test_ok.py"

    echo = execute_bm_cli(agent, state, "echo clone-shell-ok")
    assert echo.ok is True, echo.prompt_content
    assert echo.executor == "shell"
    assert "clone-shell-ok" in (echo.prompt_content or "")

    collected = execute_bm_cli(agent, state, "pytest -q tests/test_ok.py")
    assert collected.ok is True, collected.prompt_content
    assert collected.executor == "shell"
    payload = collected.prompt_content or ""
    assert "1 passed" in payload or "passed" in payload.lower()

    added = execute_bm_cli(agent, state, "git add tests/test_ok.py")
    assert added.ok is True, added.prompt_content
    bash = execute_bm_cli(agent, state, "bash scripts/run-tests.sh")
    assert bash.ok is False
    assert policy_engine.evaluate("bash scripts/run-tests.sh", frozenset()).tier == "never_allowed"
    push = execute_bm_cli(agent, state, "git push origin HEAD")
    assert push.approval_required is True


@pytest.mark.asyncio
async def test_deny_refuses_and_blocked_why_names_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state = _agent_and_state()
    peer = db.create_agent("Debra", role="Requirements Analyst")
    channel = db.create_channel(
        name="Jim, Debra",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = create_or_bind_task(
        title="Validate clone",
        description="Run pytest on the locked clone.",
        project=None,
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )
    activate_work_activity(agent.id, creation.task)
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
        task_id=creation.task.id,
    )
    client = _api_client(monkeypatch)
    paused = execute_bm_cli(
        agent, state, "pytest -q", channel_id=channel.id
    )
    shell_id = paused.consent_request_id
    assert shell_id
    denied = client.post(f"/api/shell-executor/{shell_id}/deny", headers=_headers())
    assert denied.status_code == 200, denied.text
    assert denied.json()["status"] == "denied"
    assert config.get("cli_shell_enabled") == "false"

    retry = execute_bm_cli(agent, state, "pytest -q")
    assert retry.ok is False
    assert retry.consent_required is False
    assert is_shell_executor_deny_result(retry)

    result = await execute_action(
        {"action": "bm_cli", "command": "pytest -q"},
        agent,
        state,
        trigger={"type": "channel_response", "channel_id": channel.id},
    )
    persist_result_triggers(result)
    expected = f"{agent.name} {format_blocked_line(SHELL_EXECUTOR_WHY, '@Debra')}"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert expected in contents
    assert not any("desk" in (item or "").lower() and "can't" in (item or "").lower() for item in contents)
    origin = result.get("origin_status_messages") or []
    assert any(SHELL_EXECUTOR_WHY in str(item.get("content") or "") for item in origin)


def test_runtime_core_steers_off_desk_cannot_shell() -> None:
    agent, _ = _agent_and_state()
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    block = format_runtime_core_block(agent)
    assert LOCKED_WORKSPACE_COPY_STEER in block
    assert "Do not invent that the desk cannot shell" in block
    assert "wait for the in-thread Enable/Deny card" in block
    assert "do not park @Operator as the shell enabler" in block
    assert "Do not park @Operator as the test runner or git pusher" in block


def test_blocked_line_names_shell_executor_gate() -> None:
    assert (
        format_blocked_line(SHELL_EXECUTOR_WHY, "@Debra")
        == "Blocked — Shell Executor off — needs enable. @Debra"
    )
    assert "Shell Executor" in format_blocked_line(SHELL_EXECUTOR_WHY)
