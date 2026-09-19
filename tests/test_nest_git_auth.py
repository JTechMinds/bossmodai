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
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.nest_git import (
    HostGitProbe,
    command_needs_nest_git_auth,
    enable_host_git,
    git_subcommand,
    host_git_is_enabled,
    nest_git_auth_ready,
    nest_git_pat,
    nest_git_shell_env,
    nest_git_status,
    shell_output_looks_like_git_auth_failure,
    write_nest_git_secret,
)
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.shell_executor import ShellExecutionResult, execute_shell_command
from core.models.nest_git import (
    NEST_GIT_ADD_LABEL,
    NEST_GIT_BAD_CREDS_HOWTO,
    NEST_GIT_BAD_CREDS_WHY,
    NEST_GIT_BODY,
    NEST_GIT_BOT_EMAIL,
    NEST_GIT_BOT_NAME,
    NEST_GIT_CARD_COPY,
    NEST_GIT_ENABLE_HINT,
    NEST_GIT_ENABLE_LABEL,
    NEST_GIT_EMPTY_CREDS,
    NEST_GIT_HOWTO,
    NEST_GIT_KIND,
    NEST_GIT_NO_CREDS_WHY,
    NEST_GIT_OPEN_SETTINGS_LABEL,
    NEST_GIT_PAT_KEY,
    NEST_GIT_SAVE_LABEL,
    NEST_GIT_SSH_LABEL,
    NEST_GIT_TOKEN_LABEL,
    nest_git_card_title,
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


def test_operator_copy_matches_beginner_lock() -> None:
    assert nest_git_card_title("Jim") == "Jim needs permission to push to GitHub"
    assert nest_git_card_title("") == "needs permission to push to GitHub"
    assert nest_git_card_title(None) == "needs permission to push to GitHub"
    assert NEST_GIT_BODY == (
        "Your computer’s GitHub login isn’t shared with agents. "
        "Paste a GitHub access token (a special password from GitHub → Settings → Developer settings), "
        "or an SSH key if you use those. Saved once under Settings → Nest git. "
        "Approving a command once doesn’t skip this."
    )
    assert NEST_GIT_TOKEN_LABEL == "GitHub access token"
    assert NEST_GIT_SSH_LABEL == "SSH key (optional)"
    assert NEST_GIT_SAVE_LABEL == "Save"
    assert NEST_GIT_OPEN_SETTINGS_LABEL == "Open Nest git settings"
    assert "Always-allow" not in NEST_GIT_BODY
    assert "Always-allow" not in NEST_GIT_ENABLE_HINT
    assert "PAT" not in NEST_GIT_TOKEN_LABEL
    assert "empty field" in NEST_GIT_EMPTY_CREDS
    # Fail-closed Blocked why + how-to stay on the gate path.
    assert NEST_GIT_NO_CREDS_WHY == "Nest git has no credentials"
    assert NEST_GIT_BAD_CREDS_WHY == "GitHub didn’t accept that access token or SSH key"
    assert "Nest git card" in NEST_GIT_BAD_CREDS_HOWTO
    assert "Enable host git" in NEST_GIT_HOWTO


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
    assert card["title"] == nest_git_card_title(agent.name)
    assert card["body"] == NEST_GIT_BODY
    assert card["enable_label"] == NEST_GIT_ENABLE_LABEL
    assert card["add_label"] == NEST_GIT_ADD_LABEL
    assert card["always_allow"] is False
    assert NEST_GIT_CARD_COPY in (paused.detail or "")
    assert f"{agent.name} {NEST_GIT_CARD_COPY}" in (paused.detail or "")
    assert "desktop GitHub" in (paused.prompt_content or "")
    assert "@Operator" in (paused.prompt_content or "")
    assert "Always-allow" in (paused.prompt_content or "")
    assert "Enable host git for nest?" not in json.dumps(card)
    assert "Add PAT/SSH" not in json.dumps(card)
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
    assert extra["BOSSMOD_NEST_GIT_USERNAME"] == "x-access-token"
    assert extra["GIT_ASKPASS"]
    assert extra["GIT_TERMINAL_PROMPT"] == "0"
    assert extra["GIT_CONFIG_VALUE_1"] == "x-access-token"
    assert extra["GIT_CONFIG_VALUE_0"] == ""
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
        NEST_GIT_ENABLE_LABEL, NEST_GIT_ADD_LABEL,
    }
    assert items[0]["title"] == nest_git_card_title(agent.name)
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
            str(root / "ui" / "static" / "js" / "core" / "consent-card-nest-git.js"),
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
        "cardShowsLockCopy": True,
        "cardShowsSaveAndSettings": True,
        "enableCollapsesSibling": True,
        "probeFailLeavesToggleOff": True,
        "patNotLeftInDom": True,
        "settingsShowsBeginnerCopy": True,
        "cardShowsAuthBounce": True,
    }


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _real_nest_cwd(agent) -> str:
    dest = "/me/host-work/sample_repo"
    real = agent_artifact_dir(agent.storage_key) / "host-work" / "sample_repo"
    real.mkdir(parents=True, exist_ok=True)
    (real / ".git").mkdir(exist_ok=True)
    set_cli_cwd(agent.id, dest)
    return dest


def _fake_ok_shell(captured: dict[str, Any]):
    def _run(command: str, **kwargs: Any) -> ShellExecutionResult:
        captured["command"] = command
        captured["extra_env"] = dict(kwargs.get("extra_env") or {})
        return ShellExecutionResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            duration_ms=1,
        )

    return _run


def test_git_subcommand_skips_global_flags() -> None:
    assert git_subcommand(("push", "origin", "main")) == "push"
    assert git_subcommand(("-C", "/me/host-work/sample_repo", "--no-pager", "push", "origin")) == "push"
    assert git_subcommand(("-c", "credential.helper=", "fetch")) == "fetch"
    agent, _state = _agent_and_state()
    parsed = parse_cli_command("git -C /me/host-work/sample_repo --no-pager push origin main")
    assert command_needs_nest_git_auth(agent, parsed, "/me/host-work/sample_repo") is True


def test_saved_pat_reaches_shell_push_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _fake_ok_shell(captured))
    token = "ghp_inject-reaches-push-CCCC"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    _enable_shell()
    agent, state = _agent_and_state()
    cwd = _real_nest_cwd(agent)
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    result = execute_bm_cli(agent, state, "git push origin main")
    extra = captured.get("extra_env") or {}
    assert extra.get("GIT_ASKPASS")
    assert extra.get("GIT_TERMINAL_PROMPT") == "0"
    assert extra.get("BOSSMOD_NEST_GIT_USERNAME") == "x-access-token"
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") == token
    assert extra.get("GIT_CONFIG_VALUE_1") == "x-access-token"
    assert token not in (captured.get("command") or "")
    assert token not in (result.prompt_content or "")
    assert token not in json.dumps(result.data or {})


def test_saved_pat_reaches_flagged_push_and_approved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _fake_ok_shell(captured))
    token = "ghp_approved-push-inject-DDDD"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    _enable_shell()
    agent, state = _agent_and_state()
    cwd = _real_nest_cwd(agent)
    flagged = "git -C /me/host-work/sample_repo --no-pager push origin main"
    write_nest_always_rule(flagged, cwd)
    policy_engine.reload()
    execute_bm_cli(agent, state, flagged)
    extra = captured.get("extra_env") or {}
    assert extra.get("GIT_ASKPASS")
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") == token

    captured.clear()
    approval = db.create_cli_approval_request(
        agent_id=agent.id,
        command="git push origin HEAD",
        cwd=cwd,
    )
    approved = execute_approved_command(
        agent, state, "git push origin HEAD", approval_request_id=approval.id,
    )
    extra = captured.get("extra_env") or {}
    assert extra.get("GIT_ASKPASS")
    assert extra.get("BOSSMOD_NEST_GIT_USERNAME") == "x-access-token"
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") == token
    assert token not in (approved.prompt_content or "")


def test_interactive_username_prompt_fail_closed_not_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_dir = tmp_path / "bin"
    git_dir.mkdir()
    git = git_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        "echo \"Username for 'https://github.com':\" >&2\n"
        "if [ \"${GIT_TERMINAL_PROMPT:-1}\" = \"0\" ]; then\n"
        "  echo \"fatal: could not read Username for 'https://github.com': "
        "terminal prompts disabled\" >&2\n"
        "  exit 128\n"
        "fi\n"
        "read dummy || true\n"
        "exit 1\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{git_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    hung = execute_shell_command("git push origin main", cwd=tmp_path, timeout_seconds=2)
    assert hung.timed_out is False
    assert hung.exit_code != 0
    assert "terminal prompts disabled" in (hung.stderr or "")

    token = "ghp_prompt-fail-closed-EEEE"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    _enable_shell()
    agent, state = _agent_and_state()
    cwd = _real_nest_cwd(agent)
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    result = execute_bm_cli(agent, state, "git push origin main")
    assert result.ok is False
    assert result.kind == "nest_git_block"
    error = (result.data or {}).get("error") or ""
    assert NEST_GIT_BAD_CREDS_WHY in error
    assert token not in error
    assert token not in (result.prompt_content or "")


def test_bad_pat_bounces_nest_git_card(monkeypatch: pytest.MonkeyPatch) -> None:
    def _auth_fail(command: str, **kwargs: Any) -> ShellExecutionResult:
        extra = kwargs.get("extra_env") or {}
        assert extra.get("GIT_ASKPASS")
        assert extra.get("BOSSMOD_NEST_GIT_USERNAME") == "x-access-token"
        return ShellExecutionResult(
            exit_code=128,
            stdout="",
            stderr="fatal: Authentication failed for 'https://github.com/org/repo.git/'",
            timed_out=False,
            duration_ms=5,
        )

    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _auth_fail)
    token = "ghp_bad-credentials-bounce-FFFF"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    _enable_shell()
    agent, state = _agent_and_state()
    cwd = _real_nest_cwd(agent)
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    result = execute_bm_cli(agent, state, "git push origin main")
    assert result.ok is False
    assert result.kind == "nest_git_block"
    error = (result.data or {}).get("error") or ""
    assert NEST_GIT_BAD_CREDS_WHY in error
    assert NEST_GIT_BAD_CREDS_HOWTO in error
    assert token not in error
    assert nest_git_pat() == token
    card = (result.data or {}).get("host_path_consent") or {}
    assert card.get("kind") == NEST_GIT_KIND
    assert card.get("status") == "pending"
    assert card.get("error")
    assert NEST_GIT_BAD_CREDS_WHY in str(card.get("error"))
    dumped = json.dumps(result.data or {})
    assert token not in dumped
    pending = [
        row for row in db.list_consent_requests(agent_id=agent.id, status="pending", limit=20)
        if (row.card_kind or "") == NEST_GIT_KIND
    ]
    assert pending
    assert shell_output_looks_like_git_auth_failure(
        "", "fatal: Authentication failed for 'https://github.com/org/repo.git/'"
    )
