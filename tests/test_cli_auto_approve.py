"""Per-thread CLI auto-approve. Off by default. Hard blocks still win."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions_cli import _cli_action_result
from core.bm_cli.cli_auto_approve import AUDIT_PREFIX, parse_review_payload
from core.bm_cli.filesystem import agent_artifact_dir, project_artifact_dir
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import set_cli_cwd

ROOT = Path(__file__).resolve().parent.parent


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


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _agent_and_state():
    agent = db.create_agent("Ops Clerk", role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _thread(agent_id: str, *, enabled: bool):
    channel = db.create_channel(name="Ops", member_agent_ids=[agent_id])
    if enabled:
        channel = db.update_channel(channel.id, cli_auto_approve=True)
    assert channel is not None
    return channel


def _project_file(name: str = "demo") -> Path:
    root = project_artifact_dir(name)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "notes.txt"
    path.write_text("keep", encoding="utf-8")
    return path


def _allow(why: str = "deletes one project file"):
    def _complete(_messages):
        return json.dumps({"allow": True, "why": why})

    return _complete


def test_review_payload_is_fail_closed() -> None:
    assert parse_review_payload("not json") is None
    assert parse_review_payload('{"allow": true}') is None
    assert parse_review_payload('{"allow": "true", "why": "yes"}') is None
    assert parse_review_payload('{"allow": true, "why": ""}') is None
    assert parse_review_payload('{"allow": false, "why": "unsure", "extra": 1}') is None
    assert parse_review_payload('{"allow": false, "why": "unsure"}') == (False, "unsure")
    assert parse_review_payload('```json\n{"allow": true, "why": "in project"}\n```') == (
        True,
        "in project",
    )


def test_toggle_off_keeps_the_approval_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default off. System AI is not asked, and the file stays."""
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")

    def _boom(_messages):
        raise AssertionError("System AI must not run while the thread flag is off")

    monkeypatch.setattr("core.bm_cli.cli_auto_approve.complete_text", _boom)
    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert result.ok is False
    assert notes.read_text(encoding="utf-8") == "keep"
    pending = db.list_cli_approval_requests(status="pending", agent_id=agent.id)
    assert len(pending) == 1
    assert pending[0].decision_by is None
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


def test_toggle_on_auto_approves_a_project_delete(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    assert channel.cli_auto_approve is True
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    why = "deletes one project file"
    monkeypatch.setattr("core.bm_cli.cli_auto_approve.complete_text", _allow(why))

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.ok is True, result.detail
    assert result.approval_required is False
    assert not notes.exists()
    line = f"{AUDIT_PREFIX} {why}"
    assert result.data is not None
    assert result.data.get("audit") == line
    assert result.data.get("approved_by") == "system"
    assert line in (result.prompt_content or "")

    approved = db.list_cli_approval_requests(status="approved", decision_by="system")
    assert len(approved) == 1
    assert approved[0].decision_by == "system"
    assert approved[0].decision_note == line

    mapped = _cli_action_result(agent, result, command="rm notes.txt")
    assert mapped["audit"] == line
    assert mapped["approved_by"] == "system"

    feed = db.get_recent_activity_log_entries(limit=10)
    assert any(
        row.get("event") == "cli_auto_approved" and line in str(row.get("detail") or "")
        for row in feed
    )
    events = db.list_bm_cli_events(agent_id=agent.id, limit=5)
    assert any(line in str(row.get("stdout_preview") or "") for row in events)

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    audits = [item for item in res.json() if item["kind"] == "audit"]
    assert len(audits) == 1
    assert line in audits[0]["sub"]
    assert "rm notes.txt" in audits[0]["sub"]


def test_bad_json_stays_on_the_approval_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    monkeypatch.setattr(
        "core.bm_cli.cli_auto_approve.complete_text",
        lambda _messages: "sure, go ahead",
    )

    result = execute_bm_cli(agent, state, "rm notes.txt", channel_id=channel.id)

    assert result.approval_required is True
    assert notes.read_text(encoding="utf-8") == "keep"
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


def test_never_allow_fence_and_jail_stay_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    calls: list[object] = []

    def _allow_everything(messages):
        calls.append(messages)
        return '{"allow": true, "why": "looks fine"}'

    monkeypatch.setattr("core.bm_cli.cli_auto_approve.complete_text", _allow_everything)

    blocked = execute_bm_cli(agent, state, "bash -c 'echo hi'", channel_id=channel.id)
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert "Blocked" in blocked.detail
    assert calls == []

    made = execute_bm_cli(agent, state, "mkdir /projects/bound-poc", channel_id=channel.id)
    assert made.ok is True, made.detail
    entered = execute_bm_cli(agent, state, "cd /projects/bound-poc", channel_id=channel.id)
    assert entered.ok is True, entered.detail
    parent = execute_bm_cli(agent, state, "cd ..", channel_id=channel.id)
    assert parent.ok is True, parent.detail
    fenced = execute_bm_cli(agent, state, "git checkout -b escaped", channel_id=channel.id)
    assert fenced.ok is False
    assert fenced.kind == "project_git_fence"
    assert fenced.approval_required is False
    assert calls == []

    set_cli_cwd(agent.id, "/projects/demo")
    jailed = execute_bm_cli(agent, state, "rm /etc/passwd", channel_id=channel.id)
    assert jailed.ok is False
    assert jailed.approval_required is False
    assert "Path jail" in jailed.detail
    assert calls == []
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []


def test_write_outside_the_bound_project_stays_a_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    _project_file("demo")
    other = project_artifact_dir("other")
    other.mkdir(parents=True, exist_ok=True)
    secret = other / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    set_cli_cwd(agent.id, "/projects/demo")

    def _boom(_messages):
        raise AssertionError("a write outside the bound project is not sent to System AI")

    monkeypatch.setattr("core.bm_cli.cli_auto_approve.complete_text", _boom)
    result = execute_bm_cli(
        agent, state, "rm /projects/other/secret.txt", channel_id=channel.id,
    )

    assert result.approval_required is True
    assert secret.read_text(encoding="utf-8") == "nope"


def test_me_delete_can_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=True)
    scratch = agent_artifact_dir(agent.storage_key) / "scratch.txt"
    scratch.write_text("temp", encoding="utf-8")
    set_cli_cwd(agent.id, "/me")
    monkeypatch.setattr(
        "core.bm_cli.cli_auto_approve.complete_text",
        _allow("deletes one file in /me"),
    )

    result = execute_bm_cli(agent, state, "rm scratch.txt", channel_id=channel.id)

    assert result.ok is True, result.detail
    assert not scratch.exists()
    assert result.data is not None
    assert str(result.data.get("audit") or "").startswith(AUDIT_PREFIX)


def test_deny_pick_and_soft_block_stay(client: TestClient) -> None:
    """The thread flag does not rewrite Default Policy, Deny rows, or Soft-block."""
    _enable_shell()
    db.set_setting("cli_default_policy", "deny", "cli_policy")
    config.reload()
    policy_engine.reload()
    agent, state = _agent_and_state()
    channel = _thread(agent.id, enabled=False)
    task = db.create_task(
        title="Stuck",
        assigned_to=agent.id,
        requester_id=agent.id,
    )
    db.update_task(task.id, status="blocked")
    never = {rule.pattern for rule in db.list_cli_policy_rules(tier="never_allowed")}
    assert "bash" in never

    res = client.patch(
        f"/api/channels/{channel.id}/cli-auto-approve",
        headers=_auth(),
        json={"enabled": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cli_auto_approve"] is True
    assert config.get_live("cli_default_policy") == "deny"
    assert db.get_task(task.id).status == "blocked"
    still_never = {rule.pattern for rule in db.list_cli_policy_rules(tier="never_allowed")}
    assert still_never == never

    fresh = db.get_agent_state(agent.id)
    assert fresh is not None
    denied = execute_bm_cli(agent, fresh, "hostname", channel_id=channel.id)
    assert denied.approval_required is False
    assert denied.ok is False
    assert "denied by default policy" in denied.detail
    assert db.list_cli_approval_requests(status="approved", decision_by="system") == []

    off = client.patch(
        f"/api/channels/{channel.id}/cli-auto-approve",
        headers=_auth(),
        json={"enabled": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["cli_auto_approve"] is False
    assert config.get_live("cli_default_policy") == "deny"
    assert db.get_task(task.id).status == "blocked"


def test_thread_toggle_is_off_in_the_menu_until_the_flag_is_set() -> None:
    thread = (ROOT / "ui/static/js/conversation/sources/thread-source.js").read_text(encoding="utf-8")
    assert "id: 'channel-cli-auto-approve'" in thread
    assert "label: 'Auto-approve safe commands'" in thread
    assert "pressed: !!(channel && channel.cli_auto_approve)" in thread
    assert "/cli-auto-approve" in thread
    popover = (ROOT / "ui/static/js/needs/needs-popover.js").read_text(encoding="utf-8")
    assert "audit: 'Auto-approved'" in popover
    shape = (ROOT / "ui/static/js/needs/need-shape.js").read_text(encoding="utf-8")
    assert "'cli_auto_approved'" in shape
