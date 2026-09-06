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
from core.agent_loop.loop import run_turn
from core.agent_loop.notifications import (
    emit_chat_notifications,
    persist_channel_notification,
    persist_chat_notification,
    project_chat_notifications,
)
from core.agent_loop.prompt_history import build_prompt_history_view
from core.agent_loop.runtime_core import format_runtime_core_block, preview_runtime_core
from core.bm_cli.consent_scope import ConsentScope, host_path_consent_scope
from core.bm_cli.host_path_consent import (
    is_verbal_host_access_ask,
    request_host_path_access,
    resume_host_path_consent,
)
from core.llm import context_builder
from core.llm.client import LLMResponse
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


async def test_consent_card_is_projected_once_and_attached_to_messages(
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
    unused = db.create_channel(
        name="Unused",
        member_agent_ids=[agent.id],
        created_by=agent.id,
    )
    await emit_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action=action,
        result=result,
    )
    assert db.list_channel_messages(unused.id) == []
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
    assert db.list_channel_messages(unused.id) == []


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
    assert notes[0].channel_id is None


async def test_channel_origin_consent_projects_interactive_card_in_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "channel-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("channel\n", encoding="utf-8")
    agent, state = _agent_and_state()
    peer = db.create_agent("Channel Peer")
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[agent.id, peer.id],
        created_by=agent.id,
    )
    parsed = parse_action(
        '{"act":"request_host_access","data":{"path":"%s","why":"Need the shared file"},"th":"ask"}'
        % fixture
    )
    result = await execute_action(parsed, agent, state)
    trigger = {
        "type": "channel_message",
        "source_channel": "channel",
        "channel_id": channel.id,
        "content": f"Please read {fixture}",
    }
    notes = project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    assert len(notes) == 1
    assert notes[0].kind == "host_path_consent"
    assert notes[0].channel_id == channel.id
    assert notes[0].consent_id

    await emit_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    )
    stored = db.list_notifications(agent_id=agent.id, chat_visible=True)
    assert not any(item.kind == "host_path_consent" for item in stored)
    messages = db.list_channel_messages(channel.id)
    cards = [item for item in messages if item.consent_id == notes[0].consent_id]
    assert len(cards) == 1
    assert cards[0].notification_kind == "host_path_consent"
    assert cards[0].author_type == "system"

    client = _api_client(monkeypatch)
    payload = client.get(f"/api/channels/{channel.id}", headers=_headers())
    assert payload.status_code == 200
    serialized = [
        item
        for item in payload.json()["messages"]
        if item.get("notification_kind") == "host_path_consent"
    ]
    assert len(serialized) == 1
    assert serialized[0]["host_path_consent"]["id"] == notes[0].consent_id
    assert serialized[0]["host_path_consent"]["path"]
    assert serialized[0]["host_path_consent"]["status"] == "pending"
    stored_request = db.get_consent_request(notes[0].consent_id)
    assert stored_request is not None
    assert stored_request.channel_id == channel.id
    focus = client.get(f"/api/agents/{agent.id}/messages", headers=_headers())
    assert focus.status_code == 200
    assert not [
        item
        for item in focus.json()
        if item.get("notification_kind") == "host_path_consent"
    ]
    assert db.has_consent_notification(notes[0].consent_id) is True
    assert project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=None,
        action=parsed,
        result=result,
    ) == []


def test_channel_consent_deny_keeps_workspace_host_roots_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "channel-deny"
    host.mkdir()
    fixture = host / "secret.txt"
    fixture.write_text("nope\n", encoding="utf-8")
    agent, _state = _agent_and_state()
    channel = db.create_channel(
        name="Deny Thread",
        member_agent_ids=[agent.id],
        created_by=agent.id,
    )
    first = request_host_path_access(
        agent=agent,
        raw_path=str(fixture),
        reason="Need the secret",
        cwd="/me",
    )
    assert first.consent_required is True
    request_id = first.consent_request_id
    note = persist_channel_notification(
        agent,
        project_chat_notifications(
            agent=agent,
            trigger={"type": "channel_message", "source_channel": "channel", "channel_id": channel.id},
            active_activity=None,
            action={"action": "request_host_access"},
            result={
                "event": "host_path_consent_required",
                "consent_required": True,
                "consent_request_id": request_id,
                "consent_reused": False,
                "host_path_consent": (first.data or {}).get("host_path_consent"),
            },
        )[0],
    )
    assert note["host_path_consent"]["id"] == request_id

    before = next((item.value for item in db.get_settings() if item.key == "workspace_host_roots"), "")
    client = _api_client(monkeypatch)
    denied = client.post(f"/api/host-path-consent/{request_id}/deny", headers=_headers())
    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"
    after = next((item.value for item in db.get_settings() if item.key == "workspace_host_roots"), "")
    assert after == before
    assert str(host.resolve()) not in (after or "").splitlines()

    refreshed = client.get(f"/api/channels/{channel.id}", headers=_headers())
    cards = [
        item
        for item in refreshed.json()["messages"]
        if item.get("host_path_consent")
    ]
    assert cards[0]["host_path_consent"]["status"] == "denied"


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
    assert is_verbal_host_access_ask(
        "confirm in chat that I may access /home/jordan/Desktop/Projects/Jtech-CLI"
    )
    assert is_verbal_host_access_ask(
        "Happy to review it, but that path is outside my allowed roots (/me and /projects). "
        "Could you either copy the code into /projects (e.g. /projects/jtech-cli) or "
        "confirm in chat that I may access /home/jordan/Desktop/Projects/Jtech-CLI?"
    )
    assert not is_verbal_host_access_ask("Got it. I'll take a look and report back soon.")
    assert not is_verbal_host_access_ask("Please confirm the meeting time.")
    assert not is_verbal_host_access_ask("I read /tmp/app/main.py and the tests passed.")


@pytest.mark.asyncio
async def test_verbal_confirm_in_chat_socialmsg_is_steered() -> None:
    agent, state = _agent_and_state()
    parsed = parse_action(
        '{"act":"socialmsg","data":{"to":"human","msg":'
        '"Could you confirm in chat that I may access /home/jordan/Desktop/Projects/Jtech-CLI?"},'
        '"th":"ask"}'
    )
    result = await execute_action(parsed, agent, state)
    assert result["event"] == "world_feedback"
    assert "not negotiated in chat" in result["detail"]
    assert result["expected_action"] == "request_host_access"
    assert db.list_consent_requests(agent_id=agent.id) == []


def test_consent_and_approval_resume_load_human_chat_history() -> None:
    agent, state = _agent_and_state()
    ask = "Hey jimothy can you review the code at /home/jordan/Desktop/Projects/Jtech-CLI/"
    db.create_message(HUMAN_SENDER_ID, agent.id, ask, message_type="human")
    db.create_message(agent.id, HUMAN_SENDER_ID, "Access worked this time.", message_type="work")

    for trigger_type in ("host_path_consent_resolved", "cli_approval_resolved"):
        trigger = {"type": trigger_type, "payload": {"status": "always_allowed"}}
        history = build_prompt_history_view(agent, trigger)
        contents = [str(item.get("content") or "") for item in history.conversation_history]
        assert any(ask in content for content in contents), trigger_type
        context = context_builder.build_context(
            context_builder.TurnContext(
                agent=agent,
                state=state,
                trigger=trigger,
                conversation_history=history.conversation_history,
                prompt_notifications=[],
                reference_materials=[],
                contract_kind="execution",
            )
        )
        joined = "\n".join(str(message.get("content") or "") for message in context)
        assert ask in joined, trigger_type


@pytest.mark.asyncio
async def test_consent_resolved_resume_continues_with_chat_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask = "Hey jimothy can you review the code at /home/jordan/Desktop/Projects/Jtech-CLI/"
    agent = db.create_agent("Jimothy", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    db.create_message(HUMAN_SENDER_ID, agent.id, ask, message_type="human")

    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        captured["messages"] = list(kwargs.get("messages") or [])
        return LLMResponse(
            content='{"act":"idle","data":{},"th":"history is present so the review can continue"}',
            model="test/mock",
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "host_path_consent_resolved",
            "payload": {
                "status": "always_allowed",
                "command": "request_host_access",
                "path": "/home/jordan/Desktop/Projects/Jtech-CLI",
            },
        },
    )
    joined = "\n".join(
        str(message.get("content") or "") for message in captured.get("messages") or []
    )
    assert ask in joined
    assert outcome.result.get("event") != "agent_error"
    assert captured.get("messages"), "consent resume must call the model with history"


class _ResumeServices:
    def __init__(self) -> None:
        self.triggers: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.triggers.append(kwargs)


def test_consent_resolved_loads_origin_channel_thread(tmp_path: Path) -> None:
    host = tmp_path / "resume-channel"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("resume\n", encoding="utf-8")
    agent, _state = _agent_and_state()
    peer = db.create_agent("Laura")
    channel = db.create_channel(
        name="Review",
        member_agent_ids=[agent.id, peer.id],
        created_by=agent.id,
    )
    ask = f"Please read {fixture} and report back here."
    db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content=ask,
        source_channel="channel",
    )
    db.create_message(HUMAN_SENDER_ID, agent.id, "Unrelated Focus chat", message_type="human")
    first = request_host_path_access(
        agent=agent,
        raw_path=str(fixture),
        reason="Need the shared file",
        channel_id=channel.id,
    )
    assert first.consent_required is True
    stored = db.get_consent_request(first.consent_request_id)
    assert stored is not None
    assert stored.channel_id == channel.id

    history = build_prompt_history_view(
        agent,
        {
            "type": "host_path_consent_resolved",
            "channel_id": channel.id,
            "payload": {"status": "always_allowed"},
        },
    )
    contents = [str(item.get("content") or "") for item in history.conversation_history]
    assert any(ask in content for content in contents)
    assert all("Unrelated Focus chat" not in content for content in contents)


def test_consent_resolved_prefers_bound_task_thread(tmp_path: Path) -> None:
    host = tmp_path / "resume-task"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("task\n", encoding="utf-8")
    agent, _state = _agent_and_state()
    channel = db.create_channel(
        name="Task Channel",
        member_agent_ids=[agent.id],
        created_by=agent.id,
    )
    db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Channel-only ask should lose to the task thread.",
        source_channel="channel",
    )
    task = _bind_task(agent.id).task
    assert task is not None
    db.create_task_event(
        task_id=task.id,
        author_type="human",
        author_name="Human Operator",
        event_type="comment",
        content=f"Task ask: read {fixture}",
    )
    history = build_prompt_history_view(
        agent,
        {
            "type": "host_path_consent_resolved",
            "task_id": task.id,
            "channel_id": channel.id,
            "payload": {"status": "always_allowed"},
        },
    )
    contents = [str(item.get("content") or "") for item in history.conversation_history]
    assert any("Task ask:" in content for content in contents)
    assert all("Channel-only ask" not in content for content in contents)


async def test_always_follow_through_posts_to_origin_channel(
    tmp_path: Path,
) -> None:
    host = tmp_path / "follow-through"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("go\n", encoding="utf-8")
    asker, _state = _agent_and_state()
    peer = db.create_agent("Laura")
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[asker.id, peer.id],
        created_by=asker.id,
    )
    first = request_host_path_access(
        agent=asker,
        raw_path=str(fixture),
        reason="Need the shared file",
        channel_id=channel.id,
    )
    services = _ResumeServices()
    updated = await resume_host_path_consent(
        first.consent_request_id,
        decision="always_allow",
        services=services,
    )
    assert updated is not None
    assert updated.status == "always_allowed"
    assert updated.channel_id == channel.id

    resumes = [
        item
        for item in services.triggers
        if item.get("trigger_type") == "host_path_consent_resolved"
    ]
    assert resumes
    assert resumes[0]["payload"]["channel_id"] == channel.id
    assert resumes[0]["source_channel"] == "channel"

    messages = db.list_channel_messages(channel.id)
    follow = [
        item
        for item in messages
        if item.author_type == "agent" and "Always allow (for all agents)" in item.content
    ]
    assert follow
    assert str(fixture.resolve()) in follow[0].content or first.data["host_path_consent"]["path"] in follow[0].content
    peer_wakes = [
        item
        for item in services.triggers
        if item.get("trigger_type") == "channel_message" and item.get("agent_id") == peer.id
    ]
    assert peer_wakes


async def test_allow_once_follow_through_posts_to_origin_channel(
    tmp_path: Path,
) -> None:
    host = tmp_path / "once-follow"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("once\n", encoding="utf-8")
    asker, _state = _agent_and_state()
    peer = db.create_agent("Once Peer")
    channel = db.create_channel(
        name="Once Ops",
        member_agent_ids=[asker.id, peer.id],
        created_by=asker.id,
    )
    first = request_host_path_access(
        agent=asker,
        raw_path=str(fixture),
        reason="Need the shared file once",
        channel_id=channel.id,
    )
    services = _ResumeServices()
    updated = await resume_host_path_consent(
        first.consent_request_id,
        decision="allow_once",
        services=services,
    )
    assert updated is not None
    assert updated.status == "allowed_once"
    messages = db.list_channel_messages(channel.id)
    follow = [
        item
        for item in messages
        if item.author_type == "agent" and "Allow once" in item.content
    ]
    assert follow
    assert "Always allow" not in follow[0].content


async def test_always_resolves_sibling_pending_without_second_follow_through(
    tmp_path: Path,
) -> None:
    host = tmp_path / "sibling-always"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("share\n", encoding="utf-8")
    asker, _state = _agent_and_state()
    sibling = db.create_agent("Sibling Clerk")
    channel = db.create_channel(
        name="Shared Ask",
        member_agent_ids=[asker.id, sibling.id],
        created_by=asker.id,
    )
    first = request_host_path_access(
        agent=asker,
        raw_path=str(fixture),
        reason="Need the shared file",
        channel_id=channel.id,
    )
    second = request_host_path_access(
        agent=sibling,
        raw_path=str(fixture),
        reason="Need the same file",
        channel_id=channel.id,
    )
    assert first.consent_request_id != second.consent_request_id
    services = _ResumeServices()
    updated = await resume_host_path_consent(
        first.consent_request_id,
        decision="always_allow",
        services=services,
    )
    assert updated is not None
    sibling_row = db.get_consent_request(second.consent_request_id)
    assert sibling_row is not None
    assert sibling_row.status == "always_allowed"
    resumes = [
        item
        for item in services.triggers
        if item.get("trigger_type") == "host_path_consent_resolved"
    ]
    assert {item["agent_id"] for item in resumes} == {asker.id, sibling.id}
    follow = [
        item
        for item in db.list_channel_messages(channel.id)
        if item.author_type == "agent" and "Always allow (for all agents)" in item.content
    ]
    assert len(follow) == 1


def test_contracts_forbid_verbal_host_access_asks() -> None:
    core = format_runtime_core_block(db.create_agent("Lint Clerk", role="Writer"))
    execution = load_default_prompt("runtime_contract_execution")
    decision = load_default_prompt("runtime_contract_decision")
    for text in (core, execution, decision):
        assert "stop and ask in chat" not in text
        assert "request_host_access" in text
        assert "verbal yes/no" in text
