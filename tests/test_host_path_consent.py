"""Shared runtime core injection and in-chat host-path consent."""

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
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.decision_contract import parse_direct_turn_response
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.notifications import persist_chat_notification, project_chat_notifications
from core.agent_loop.runtime_core import format_runtime_core_block, preview_runtime_core
from core.bm_cli.consent_scope import ConsentScope, host_path_consent_scope
from core.bm_cli.host_path_consent import is_verbal_host_access_ask, request_host_path_access
from core.bm_cli.host_roots import configured_host_roots, extra_host_roots
from core.bm_cli.runtime import execute_bm_cli
from core.default_prompts import load_default_prompt
from core.llm import context_preview
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking import create_or_bind_task


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


def _agent_and_state():
    agent = db.create_agent("Path Clerk", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _bind_task(agent_id: str):
    return create_or_bind_task(
        title="Read host file",
        description="Need a host path",
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
    )


def test_runtime_core_is_compact_and_skips_description() -> None:
    agent = db.create_agent(
        "Core Writer",
        role="Writer",
        description="Never put this quality bar in the shared core.",
        desk_x=1,
        desk_y=2,
    )
    block = format_runtime_core_block(agent)
    assert block.startswith("# Runtime core")
    assert "You are Core Writer (Writer)." in block
    assert "Desk: assigned at (1,2)." in block
    assert "Tools you may use:" in block
    assert "request_host_access" in block
    assert "do not ask the operator for verbal yes/no" in block
    assert "stop and ask in chat" not in block
    assert "Empty done is rejected" in block
    assert "Never put this quality bar" not in block
    assert "DRY" not in block
    preview = preview_runtime_core(name="Pat", role="Auditor")
    assert "You are Pat (Auditor)." in preview


def test_preview_and_api_inject_runtime_core(monkeypatch: pytest.MonkeyPatch) -> None:
    preview = context_preview.preview_prompt_bundle("execution", "activity_resumed")
    contents = "\n".join(str(message.get("content") or "") for message in preview["messages"])
    assert "# Runtime core" in contents
    assert "request_host_access" in contents
    assert "do not ask the operator for verbal yes/no" in contents
    assert "stop and ask in chat" not in contents
    client = _api_client(monkeypatch)
    response = client.get(
        "/api/runtime/core",
        headers=_headers(),
        params={"name": "Sam", "role": "Engineer"},
    )
    assert response.status_code == 200
    assert "You are Sam (Engineer)." in response.json()["runtime_core"]


def test_allow_once_is_turn_scoped(tmp_path: Path) -> None:
    host = tmp_path / "once-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("hello\n", encoding="utf-8")
    agent, state = _agent_and_state()

    first = execute_bm_cli(agent, state, f"cat {fixture}")
    assert first.ok is False
    assert first.consent_required is True
    request_id = first.consent_request_id
    assert request_id
    pending = db.get_consent_request(request_id)
    assert pending is not None
    assert pending.status == "pending"

    db.resolve_consent_request(request_id, status="allowed_once")
    db.create_once_grant(
        agent_id=agent.id,
        root=pending.grant_root,
        consent_id=request_id,
        task_id=None,
    )
    allowed = execute_bm_cli(agent, state, f"cat {fixture}")
    assert allowed.ok is True
    assert "hello" in allowed.prompt_content

    db.consume_turn_once_grants(agent.id)
    again = execute_bm_cli(agent, state, f"cat {fixture}")
    assert again.ok is False
    assert again.consent_required is True
    assert again.consent_request_id != request_id


def test_allow_once_lasts_for_task_then_clears(tmp_path: Path) -> None:
    host = tmp_path / "task-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("task\n", encoding="utf-8")
    agent, state = _agent_and_state()
    task = _bind_task(agent.id).task
    activate_work_activity(agent.id, task)

    first = execute_bm_cli(agent, state, f"cat {fixture}")
    assert first.consent_required is True
    request = db.get_consent_request(first.consent_request_id or "")
    assert request is not None
    assert request.task_id == task.id
    db.resolve_consent_request(request.id, status="allowed_once")
    db.create_once_grant(
        agent_id=agent.id,
        root=request.grant_root,
        consent_id=request.id,
        task_id=task.id,
    )
    assert execute_bm_cli(agent, state, f"cat {fixture}").ok is True
    db.consume_turn_once_grants(agent.id)
    assert execute_bm_cli(agent, state, f"cat {fixture}").ok is True

    db.update_task(task.id, status="complete")
    denied = execute_bm_cli(agent, state, f"cat {fixture}")
    assert denied.ok is False
    assert denied.consent_required is True


def test_always_allow_persists_real_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "always-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("always\n", encoding="utf-8")
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    first = execute_bm_cli(agent, state, f"cat {fixture}")
    assert first.consent_required is True
    request_id = first.consent_request_id
    assert request_id

    response = client.post(f"/api/host-path-consent/{request_id}/always-allow", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "always_allowed"

    setting = next(item for item in db.get_settings() if item.key == "workspace_host_roots")
    assert str(host.resolve()) in setting.value.splitlines()
    config._cache["workspace_host_roots"] = ""
    roots = configured_host_roots()
    assert host.resolve() in roots
    assert execute_bm_cli(agent, state, f"cat {fixture}").ok is True


def test_deny_is_fail_closed_and_blocks_tool_spam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "deny-root"
    host.mkdir()
    fixture = host / "secret.txt"
    fixture.write_text("nope\n", encoding="utf-8")
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    first = execute_bm_cli(agent, state, f"cat {fixture}")
    second = execute_bm_cli(agent, state, f"cat {fixture}")
    assert first.consent_required is True
    assert second.consent_required is True
    assert first.consent_request_id == second.consent_request_id
    assert (second.data or {}).get("consent_reused") is True
    assert len(db.list_consent_requests(agent_id=agent.id)) == 1

    request_id = first.consent_request_id
    denied = client.post(f"/api/host-path-consent/{request_id}/deny", headers=_headers())
    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"

    third = execute_bm_cli(agent, state, f"cat {fixture}")
    assert third.ok is False
    assert third.consent_required is False
    assert "denied" in (third.detail or "").lower()
    assert len(db.list_consent_requests(agent_id=agent.id, status="pending")) == 0
    assert fixture.read_text(encoding="utf-8") == "nope\n"


def test_allow_once_api_and_no_company_files_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "once-api"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("scoped\n", encoding="utf-8")
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    first = execute_bm_cli(agent, state, f"cat {fixture}")
    request_id = first.consent_request_id
    allowed = client.post(f"/api/host-path-consent/{request_id}/allow-once", headers=_headers())
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "allowed_once"
    assert execute_bm_cli(agent, state, f"cat {fixture}").ok is True

    company = client.get(
        "/api/company/files",
        headers=_headers(),
        params={"path": str(fixture)},
    )
    assert company.status_code == 400
    assert "outside the allowed workspace roots" in company.json()["detail"]


def test_etc_stays_hard_denied_without_a_card() -> None:
    agent, state = _agent_and_state()
    denied = execute_bm_cli(agent, state, "cat /etc/passwd")
    assert denied.ok is False
    assert denied.consent_required is False
    assert db.list_consent_requests(agent_id=agent.id) == []
    payload = (denied.detail or "") + denied.prompt_content
    assert "outside the allowed workspace roots" in payload
    assert "root:" not in payload


def test_consent_card_is_projected_once_and_attached_to_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "card-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("card\n", encoding="utf-8")
    agent, state = _agent_and_state()
    first = execute_bm_cli(agent, state, f"cat {fixture}")
    result = {
        "event": "host_path_consent_required",
        "consent_required": True,
        "consent_request_id": first.consent_request_id,
        "consent_reused": False,
        "host_path_consent": (first.data or {}).get("host_path_consent"),
    }
    trigger = {"type": "activity_resumed", "source_channel": "work"}
    action = {"action": "bm_cli", "command": f"cat {fixture}"}
    notes = project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action=action,
        result=result,
    )
    assert len(notes) == 1
    assert notes[0].kind == "host_path_consent"
    persist_chat_notification(agent, notes[0])
    reused = dict(result)
    reused["consent_reused"] = True
    assert project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action=action,
        result=reused,
    ) == []

    client = _api_client(monkeypatch)
    messages = client.get(f"/api/agents/{agent.id}/messages", headers=_headers())
    assert messages.status_code == 200
    cards = [item for item in messages.json() if item.get("notification_kind") == "host_path_consent"]
    assert len(cards) == 1
    assert cards[0]["host_path_consent"]["id"] == first.consent_request_id
    assert cards[0]["host_path_consent"]["path"]


def test_once_grants_require_consent_scope(tmp_path: Path) -> None:
    host = tmp_path / "scope-root"
    host.mkdir()
    agent, _state = _agent_and_state()
    db.create_once_grant(
        agent_id=agent.id,
        root=str(host.resolve()),
        consent_id=db.create_consent_request(
            agent_id=agent.id,
            path=str(host.resolve()),
            grant_root=str(host.resolve()),
            reason="test",
        ).id,
        task_id=None,
    )
    assert host.resolve() not in extra_host_roots()
    token = host_path_consent_scope.set(ConsentScope(agent_id=agent.id, task_id=None))
    try:
        assert host.resolve() in extra_host_roots()
    finally:
        host_path_consent_scope.reset(token)


async def test_request_host_access_opens_card_without_cli(tmp_path: Path) -> None:
    host = tmp_path / "request-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("hello\n", encoding="utf-8")
    agent, state = _agent_and_state()

    parsed = parse_action(
        '{"act":"request_host_access","data":{"path":"%s","why":"Need to review the file"},"th":"ask"}'
        % fixture
    )
    assert parsed["action"] == "request_host_access"
    assert parsed["path"] == str(fixture)
    assert parsed["reason"] == "Need to review the file"

    result = await execute_action(parsed, agent, state)
    assert result["event"] == "host_path_consent_required"
    assert result["consent_required"] is True
    assert result["host_path_consent"]["path"] == str(fixture.resolve())
    assert result["host_path_consent"]["reason"] == "Need to review the file"
    pending = db.list_consent_requests(agent_id=agent.id, status="pending")
    assert len(pending) == 1
    assert pending[0].command is None
    notes = project_chat_notifications(
        agent=agent,
        trigger={"type": "human_chat", "source_channel": "chat"},
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert len(notes) == 1
    assert notes[0].kind == "host_path_consent"


def test_request_host_access_etc_is_hard_denied_without_a_card() -> None:
    agent, state = _agent_and_state()
    denied = request_host_path_access(
        agent=agent,
        raw_path="/etc/passwd",
        reason="Need the password file",
    )
    assert denied.ok is False
    assert denied.consent_required is False
    assert db.list_consent_requests(agent_id=agent.id) == []
    payload = (denied.detail or "") + denied.prompt_content
    assert "outside the allowed workspace roots" in payload
    assert "root:" not in payload


def test_request_host_access_already_allowed_skips_card(tmp_path: Path) -> None:
    host = tmp_path / "allowed-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("ok\n", encoding="utf-8")
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()
    agent, _state = _agent_and_state()
    result = request_host_path_access(
        agent=agent,
        raw_path=str(fixture),
        reason="Already on the allowlist",
    )
    assert result.ok is True
    assert result.consent_required is False
    assert db.list_consent_requests(agent_id=agent.id) == []


def test_request_host_access_always_allow_writes_workspace_host_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "always-request"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("always\n", encoding="utf-8")
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    first = request_host_path_access(
        agent=agent,
        raw_path=str(fixture),
        reason="Need durable access",
        cwd="/me",
    )
    assert first.consent_required is True
    request_id = first.consent_request_id
    assert request_id

    response = client.post(f"/api/host-path-consent/{request_id}/always-allow", headers=_headers())
    assert response.status_code == 200
    setting = next(item for item in db.get_settings() if item.key == "workspace_host_roots")
    assert str(host.resolve()) in setting.value.splitlines()
    assert execute_bm_cli(agent, state, f"cat {fixture}").ok is True


async def test_verbal_socialmsg_does_not_open_a_card(tmp_path: Path) -> None:
    host = tmp_path / "verbal-root"
    host.mkdir()
    fixture = host / "secret.txt"
    fixture.write_text("nope\n", encoding="utf-8")
    agent, state = _agent_and_state()

    parsed = parse_action(
        '{"act":"socialmsg","data":{"to":"human","msg":"Please confirm I can read %s"},"th":"ask"}'
        % fixture
    )
    result = await execute_action(parsed, agent, state)
    assert result["event"] == "world_feedback"
    assert "not negotiated in chat" in result["detail"]
    assert result["expected_action"] == "request_host_access"
    assert db.list_consent_requests(agent_id=agent.id) == []
    assert all("Please confirm" not in (item.content or "") for item in db.get_human_chat_thread(agent.id))


def test_verbal_decision_reply_does_not_open_a_card(tmp_path: Path) -> None:
    host = tmp_path / "reply-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("ask\n", encoding="utf-8")
    agent, state = _agent_and_state()
    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "work_request",
            "commitmentKind": "none",
            "reply": f"Please confirm I can access {fixture} before I continue.",
        },
        agent,
        state,
        {"type": "human_chat", "content": f"Read {fixture}", "from_name": "Human"},
    )
    assert result["event"] == "world_feedback"
    assert "not negotiated in chat" in result["detail"]
    assert db.list_consent_requests(agent_id=agent.id) == []
    assert all("Please confirm" not in (item.content or "") for item in db.get_human_chat_thread(agent.id))


def test_parse_direct_turn_request_host_access() -> None:
    parsed = parse_direct_turn_response(
        '{"act":"request_host_access","data":{"path":"/tmp/app/main.py","why":"Need the source"},"th":"ask"}'
    )
    assert parsed.get("action") == "request_host_access"
    assert parsed.get("path") == "/tmp/app/main.py"
    assert parsed.get("reason") == "Need the source"


def test_verbal_host_access_detector() -> None:
    assert is_verbal_host_access_ask("Please confirm I can read /tmp/app/main.py")
    assert is_verbal_host_access_ask("May I access the host path before I continue?")
    assert is_verbal_host_access_ask("Do you allow /home/you/notes.txt — yes/no?")
    assert not is_verbal_host_access_ask("Got it. I'll take a look and report back soon.")
    assert not is_verbal_host_access_ask("Please confirm the meeting time.")
    assert not is_verbal_host_access_ask("I read /tmp/app/main.py and the tests passed.")


def test_contracts_forbid_verbal_host_access_asks() -> None:
    core = format_runtime_core_block(db.create_agent("Lint Clerk", role="Writer"))
    execution = load_default_prompt("runtime_contract_execution")
    decision = load_default_prompt("runtime_contract_decision")
    for text in (core, execution, decision):
        assert "stop and ask in chat" not in text
        assert "request_host_access" in text
        assert "verbal yes/no" in text
