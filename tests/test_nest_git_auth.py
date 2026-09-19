"""Nest git auth: one Settings store, probe-gated Enable, always-allow ≠ skip."""

from __future__ import annotations

import json
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
from core.bm_cli.cli_always import write_nest_always_rule
from core.bm_cli.nest_git import (
    HostGitProbe,
    enable_host_git,
    host_git_is_enabled,
    nest_git_auth_ready,
    nest_git_pat,
    nest_git_shell_env,
    nest_git_status,
    write_nest_git_secret,
)
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from core.models.nest_git import (
    NEST_GIT_BOT_EMAIL,
    NEST_GIT_BOT_NAME,
    NEST_GIT_CARD_COPY,
    NEST_GIT_ENABLE_LABEL,
    NEST_GIT_HOWTO,
    NEST_GIT_KIND,
    NEST_GIT_NO_CREDS_WHY,
    NEST_GIT_PAT_KEY,
    NEST_GIT_TITLE,
)
from core.runtime import runtime_services
from db.secret_store import SECRET_PREFIX, is_encrypted
from tests.test_workspace_preference import _agent_and_state


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


def _nest_cwd(agent_id: str) -> str:
    dest = "/me/host-work/sample_repo"
    set_cli_cwd(agent_id, dest)
    return dest


def test_host_enable_defaults_off_and_auth_is_fail_closed() -> None:
    assert config.get("nest_git_host_enabled") == "false"
    assert host_git_is_enabled() is False
    assert nest_git_auth_ready() is False


def test_nest_push_without_creds_posts_card() -> None:
    agent, state = _agent_and_state()
    _nest_cwd(agent.id)
    paused = execute_bm_cli(agent, state, "git push origin main")
    assert paused.ok is False
    assert paused.consent_required is True
    assert paused.kind == "nest_git_consent_required"
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == NEST_GIT_KIND
    assert card["title"] == NEST_GIT_TITLE
    assert card["enable_label"] == NEST_GIT_ENABLE_LABEL
    assert card["always_allow"] is False
    assert NEST_GIT_CARD_COPY in (paused.detail or "")
    assert "desktop GitHub" in (paused.prompt_content or "")
    assert "@Operator" in (paused.prompt_content or "")
    assert "Always-allow" in (paused.prompt_content or "")
    dumped = json.dumps(card)
    assert "ghp_" not in dumped
    assert config.get("nest_git_host_enabled") == "false"


def test_always_allow_still_requires_nest_git_auth() -> None:
    agent, state = _agent_and_state()
    cwd = _nest_cwd(agent.id)
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    paused = execute_bm_cli(agent, state, "git push origin main")
    assert paused.consent_required is True
    assert paused.kind == "nest_git_consent_required"
    assert nest_git_auth_ready() is False


def test_approved_command_still_requires_nest_git_auth() -> None:
    agent, state = _agent_and_state()
    _nest_cwd(agent.id)
    result = execute_approved_command(
        agent, state, "git push origin main", approval_request_id="approved-1",
    )
    assert result.ok is False
    assert result.consent_required is True or result.kind == "nest_git_block"
    if result.consent_required:
        assert ((result.data or {}).get("host_path_consent") or {}).get("kind") == NEST_GIT_KIND
    else:
        assert NEST_GIT_NO_CREDS_WHY in ((result.data or {}).get("error") or "")


def test_enable_persists_on_only_after_probe_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.bm_cli.nest_git.probe_host_git_for_shell",
        lambda: HostGitProbe(ok=True, via="credential_helper"),
    )
    probe = enable_host_git()
    assert probe.ok is True
    assert host_git_is_enabled() is True
    assert config.get("nest_git_host_enabled") == "true"


def test_probe_fail_does_not_persist_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.bm_cli.nest_git.probe_host_git_for_shell",
        lambda: HostGitProbe(ok=False, why="Host git is not visible to Shell"),
    )
    probe = enable_host_git()
    assert probe.ok is False
    assert host_git_is_enabled() is False
    assert config.get("nest_git_host_enabled") == "false"
    assert NEST_GIT_HOWTO in probe.blocked_message()


def test_settings_put_enable_refuses_probe_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _api_client(monkeypatch)
    monkeypatch.setattr(
        "core.bm_cli.nest_git.probe_host_git_for_shell",
        lambda: HostGitProbe(ok=False, why="Host git is not visible to Shell"),
    )
    res = client.put(
        "/api/settings/nest_git_host_enabled?value=true&category=nest_git",
        headers=_headers(),
    )
    assert res.status_code == 400
    assert "Host git is not visible to Shell" in res.text
    assert config.get("nest_git_host_enabled") == "false"


def test_settings_put_enable_after_probe_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _api_client(monkeypatch)
    monkeypatch.setattr(
        "core.bm_cli.nest_git.probe_host_git_for_shell",
        lambda: HostGitProbe(ok=True, via="ssh_agent"),
    )
    res = client.put(
        "/api/settings/nest_git_host_enabled?value=true&category=nest_git",
        headers=_headers(),
    )
    assert res.status_code == 200, res.text
    assert config.get("nest_git_host_enabled") == "true"


def test_pat_uses_bm1_wrap_and_bot_identity() -> None:
    token = "ghp_nest-git-test-token-AAAA"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    raw = db.query_one("SELECT value FROM settings WHERE key = $1", [NEST_GIT_PAT_KEY])
    stored = "" if raw is None else str(raw.get("value") or "")
    assert token not in stored
    assert stored.startswith(SECRET_PREFIX)
    assert is_encrypted(stored)
    assert nest_git_pat() == token
    agent, _state = _agent_and_state()
    extra = nest_git_shell_env(agent)
    assert extra["GIT_AUTHOR_NAME"] == NEST_GIT_BOT_NAME
    assert extra["GIT_AUTHOR_EMAIL"] == NEST_GIT_BOT_EMAIL
    assert extra["BOSSMOD_NEST_GIT_PASSWORD"] == token
    assert extra["GIT_ASKPASS"]
    status = nest_git_status()
    assert status["has_pat"] is True
    assert status["pat_last4"] == token[-4:]
    dumped = json.dumps(status)
    assert token not in dumped


def test_card_enable_and_credentials_write_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent_and_state()
    _nest_cwd(agent.id)
    paused = execute_bm_cli(agent, state, "git push origin main")
    request_id = paused.consent_request_id
    assert request_id
    client = _api_client(monkeypatch)
    monkeypatch.setattr(
        "core.bm_cli.nest_git.probe_host_git_for_shell",
        lambda: HostGitProbe(ok=False, why="Host git is not visible to Shell"),
    )
    fail = client.post(f"/api/nest-git/{request_id}/enable", headers=_headers())
    assert fail.status_code == 400
    assert config.get("nest_git_host_enabled") == "false"
    pending = db.get_consent_request(request_id)
    assert pending is not None
    assert pending.status == "pending"

    token = "ghp_card-path-token-BBBB"
    ok = client.post(
        f"/api/nest-git/{request_id}/credentials",
        headers=_headers(),
        json={"pat": token},
    )
    assert ok.status_code == 200, ok.text
    card = ok.json()
    assert token not in json.dumps(card)
    assert nest_git_pat() == token
    assert nest_git_auth_ready() is True
    resolved = db.get_consent_request(request_id)
    assert resolved is not None
    assert resolved.status == "enabled"


def test_resume_without_creds_is_blocked_not_quiet() -> None:
    agent, state = _agent_and_state()
    _nest_cwd(agent.id)
    blocked = execute_bm_cli(
        agent, state, "git push origin main",
        trigger_type="host_path_consent_resolved",
    )
    assert blocked.ok is False
    assert blocked.consent_required is False
    assert blocked.kind == "nest_git_block"
    error = (blocked.data or {}).get("error") or ""
    assert NEST_GIT_NO_CREDS_WHY in error
    assert NEST_GIT_HOWTO in error


def test_needs_and_status_redact_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent_and_state()
    _nest_cwd(agent.id)
    execute_bm_cli(agent, state, "git fetch")
    client = _api_client(monkeypatch)
    needs = client.get("/api/needs", headers=_headers())
    assert needs.status_code == 200
    items = [item for item in needs.json() if item.get("card_kind") == NEST_GIT_KIND]
    assert len(items) == 1
    assert {action["label"] for action in items[0]["actions"]} == {
        "Enable host git for nest", "Add PAT/SSH",
    }
    status = client.get("/api/nest-git/status", headers=_headers())
    assert status.status_code == 200
    body = status.json()
    assert body["host_enabled"] is False
    assert body["has_pat"] is False
    assert "how_to" in body


def test_card_and_settings_harness() -> None:
    root = Path(__file__).resolve().parent.parent
    import subprocess

    result = subprocess.run(
        [
            "node",
            str(Path(__file__).resolve().parent / "js_nest_git_harness.cjs"),
            str(root / "ui" / "static" / "js" / "core" / "dom.js"),
            str(root / "ui" / "static" / "js" / "core" / "consent-card.js"),
            str(root / "ui" / "static" / "js" / "settings" / "settings-nest-git.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "cardShowsEnableAndAdd": True,
        "enableCollapsesSibling": True,
        "probeFailLeavesToggleOff": True,
        "patNotLeftInDom": True,
    }
