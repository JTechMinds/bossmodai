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
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.loop import run_turn
from core.bm_cli.nest_git import (
    HostGitProbe,
    classify_git_auth_failure,
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
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.shell_executor import ShellExecutionResult, execute_shell_command
from core.models.nest_git import (
    NEST_GIT_ADD_LABEL,
    NEST_GIT_AMBIGUOUS_CREDS_WHY,
    NEST_GIT_BAD_CREDS_HOWTO,
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
    NEST_GIT_TOKEN_NO_REPO_HINT,
    NEST_GIT_TOKEN_NO_REPO_HOWTO,
    NEST_GIT_TOKEN_NO_REPO_WHY,
    NEST_GIT_TOKEN_REJECTED_HOWTO,
    NEST_GIT_TOKEN_REJECTED_WHY,
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
    assert NEST_GIT_TOKEN_REJECTED_WHY == "GitHub rejected this token"
    assert NEST_GIT_TOKEN_NO_REPO_WHY == "this token may not have access to this repo"
    assert NEST_GIT_TOKEN_NO_REPO_HINT == "grant it under the token’s repository access."
    assert NEST_GIT_TOKEN_NO_REPO_HOWTO == (
        "this token may not have access to this repo — grant it under the token’s repository access."
    )
    assert NEST_GIT_AMBIGUOUS_CREDS_WHY == (
        "GitHub rejected this token, or it may not have access to this repo"
    )
    assert NEST_GIT_TOKEN_REJECTED_WHY in NEST_GIT_BAD_CREDS_HOWTO
    assert NEST_GIT_TOKEN_NO_REPO_HOWTO in NEST_GIT_BAD_CREDS_HOWTO
    assert "Nest git card" in NEST_GIT_TOKEN_REJECTED_HOWTO
    assert "Enable host git" in NEST_GIT_HOWTO
    assert "PAT" not in NEST_GIT_TOKEN_REJECTED_WHY
    assert "PAT" not in NEST_GIT_TOKEN_NO_REPO_HOWTO
    assert "401" not in NEST_GIT_BAD_CREDS_HOWTO
    assert "403" not in NEST_GIT_BAD_CREDS_HOWTO


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
    assert NEST_GIT_TOKEN_REJECTED_WHY in error
    assert NEST_GIT_TOKEN_REJECTED_HOWTO in error
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
    assert NEST_GIT_TOKEN_REJECTED_WHY in error
    assert NEST_GIT_TOKEN_REJECTED_HOWTO in error
    assert NEST_GIT_TOKEN_NO_REPO_HOWTO not in error
    assert token not in error
    assert nest_git_pat() == token
    card = (result.data or {}).get("host_path_consent") or {}
    assert card.get("kind") == NEST_GIT_KIND
    assert card.get("status") == "pending"
    assert card.get("error")
    assert NEST_GIT_TOKEN_REJECTED_WHY in str(card.get("error"))
    dumped = json.dumps(result.data or {})
    assert token not in dumped
    pending = [
        row for row in db.list_consent_requests(agent_id=agent.id, status="pending", limit=20)
        if (row.card_kind or "") == NEST_GIT_KIND
    ]
    assert pending
    focus_notes = [
        item.content or ""
        for item in db.list_notifications(agent_id=agent.id, chat_visible=True)
    ]
    assert any(item.kind == "host_path_consent" for item in db.list_notifications(agent_id=agent.id, chat_visible=True))
    assert any("Blocked — GitHub rejected this token" in text for text in focus_notes)
    assert shell_output_looks_like_git_auth_failure(
        "", "fatal: Authentication failed for 'https://github.com/org/repo.git/'"
    )


def test_auth_failure_copy_splits_401_vs_403() -> None:
    assert classify_git_auth_failure(
        "", "fatal: Authentication failed for 'https://github.com/org/repo.git/'"
    ) == "token_rejected"
    assert classify_git_auth_failure(
        "", "The requested URL returned error: 401"
    ) == "token_rejected"
    assert classify_git_auth_failure(
        "",
        "remote: Write access to repository not granted.\n"
        "fatal: unable to access 'https://github.com/org/repo.git/': "
        "The requested URL returned error: 403",
    ) == "repo_access"
    assert classify_git_auth_failure(
        "", "remote: Resource not accessible by personal access token"
    ) == "repo_access"
    assert classify_git_auth_failure("", "Username for 'https://github.com':") == "ambiguous"


def _auth_fail_shell(stderr: str):
    def _run(command: str, **kwargs: Any) -> ShellExecutionResult:
        del command
        extra = kwargs.get("extra_env") or {}
        assert extra.get("GIT_ASKPASS")
        return ShellExecutionResult(
            exit_code=128,
            stdout="",
            stderr=stderr,
            timed_out=False,
            duration_ms=5,
        )

    return _run


def _ready_push_agent(monkeypatch: pytest.MonkeyPatch, stderr: str):
    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _auth_fail_shell(stderr))
    token = "ghp_visible-auth-reject-GGGG"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    _enable_shell()
    agent = db.create_agent("Path Clerk", role="Writer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    cwd = _real_nest_cwd(agent)
    write_nest_always_rule("git push origin main", cwd)
    policy_engine.reload()
    return agent, state, token


def test_403_repo_access_copy_on_card(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state, token = _ready_push_agent(
        monkeypatch,
        "remote: Write access to repository not granted.\n"
        "The requested URL returned error: 403",
    )
    result = execute_bm_cli(agent, state, "git push origin main")
    error = (result.data or {}).get("error") or ""
    card = (result.data or {}).get("host_path_consent") or {}
    assert NEST_GIT_TOKEN_NO_REPO_WHY in error
    assert NEST_GIT_TOKEN_NO_REPO_HOWTO in error
    assert NEST_GIT_TOKEN_NO_REPO_HINT in error
    assert NEST_GIT_TOKEN_REJECTED_WHY not in error
    assert NEST_GIT_TOKEN_NO_REPO_WHY in str(card.get("error"))
    assert token not in error
    assert token not in json.dumps(result.data or {})


def test_ambiguous_auth_fail_shows_both_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state, token = _ready_push_agent(
        monkeypatch,
        "Username for 'https://github.com':\nPassword for 'https://github.com':",
    )
    result = execute_bm_cli(agent, state, "git push origin main")
    error = (result.data or {}).get("error") or ""
    card = (result.data or {}).get("host_path_consent") or {}
    assert NEST_GIT_AMBIGUOUS_CREDS_WHY in error
    assert NEST_GIT_TOKEN_REJECTED_WHY in error
    assert NEST_GIT_TOKEN_NO_REPO_HOWTO in str(card.get("error"))
    assert token not in error


@pytest.mark.asyncio
async def test_auth_reject_posts_card_and_in_thread_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, token = _ready_push_agent(
        monkeypatch,
        "fatal: Authentication failed for 'https://github.com/org/repo.git/'",
    )
    peer = db.create_agent("Debra", role="Analyst")
    channel = db.create_channel(
        name="Push thread",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = create_or_bind_task(
        title="Push the nest",
        description="Push origin.",
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
    trigger = {"type": "channel_response", "channel_id": channel.id}
    result = await execute_action(
        {"action": "bm_cli", "command": "git push origin main"},
        agent,
        state,
        trigger,
    )
    assert result.get("consent_required") is True
    assert result.get("event") == "nest_git_consent_required"
    card = result.get("host_path_consent") or {}
    assert card.get("kind") == NEST_GIT_KIND
    assert NEST_GIT_TOKEN_REJECTED_WHY in str(card.get("error"))
    messages = db.list_channel_messages(channel.id)
    contents = [item.content or "" for item in messages]
    assert any(item.consent_id for item in messages)
    assert any("Blocked — GitHub rejected this token" in text for text in contents)
    assert token not in json.dumps(result)
    assert token not in "".join(contents)


@pytest.mark.asyncio
async def test_auth_reject_pauses_before_prose_or_th2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, token = _ready_push_agent(
        monkeypatch,
        "fatal: Authentication failed for 'https://github.com/org/repo.git/'",
    )
    queue = [
        '{"act":"cli","data":{"cmd":"git push origin main"},"th":"push"}',
        '{"act":"reply","intent":"status","msg":"Still pushing.","th2":"waiting"}',
        "In progress — GitHub said no. Prose only.",
    ]
    seen: list[str] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        del kwargs
        if not queue:
            raise AssertionError("turn continued after nest git auth reject")
        content = queue.pop(0)
        seen.append(content)
        return LLMResponse(
            content=content,
            model="test/mock",
            prompt_tokens=8,
            completion_tokens=4,
            total_tokens=12,
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    peer = db.create_agent("Debra", role="Analyst")
    channel = db.create_channel(
        name="Push thread",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = create_or_bind_task(
        title="Push the nest",
        description="Push origin.",
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
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "task_id": creation.task.id,
            "channel_id": channel.id,
            "source_channel": "channel",
        },
    )
    assert len(seen) == 1
    assert queue[0].startswith('{"act":"reply"')
    assert outcome.result.get("event") == "nest_git_consent_required"
    assert outcome.result.get("consent_required") is True
    assert outcome.result.get("parse_steer") is not True
    contents = [item.content or "" for item in db.list_channel_messages(channel.id)]
    assert any("Blocked — GitHub rejected this token" in text for text in contents)
    assert any(item.consent_id for item in db.list_channel_messages(channel.id))
    assert token not in "".join(contents)
