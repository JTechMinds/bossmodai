"""Hard-deny GH_TOKEN env dumps; Nest git PAT injects into the gh subprocess."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.notifications import never_allowed_operator_note
from core.bm_cli.cli_always import write_nest_always_rule
from core.bm_cli.nest_git import (
    command_needs_gh_auth,
    github_compare_url,
    is_gh_cli,
    nest_git_can_inject_gh,
    nest_git_shell_env,
    write_nest_git_secret,
)
from core.bm_cli.nest_git_consent import gh_auth_operator_note
from core.bm_cli.nest_git_store import add_credential
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import (
    _shell_extra_env,
    execute_approved_command,
    execute_bm_cli,
    preview_bm_cli,
)
from core.bm_cli.secret_env import command_dumps_secret_token_env, redact_secret_env_values
from core.bm_cli.shell_executor import ShellExecutionResult, execute_shell_command
from core.models.nest_git import (
    GH_CLI_NO_AUTH_WHY,
    NEST_GIT_BOT_NAME,
    NEST_GIT_KIND,
    NEST_GIT_NO_MATCH_WHY,
    NEST_GIT_PAT_KEY,
    NEST_GIT_SSH_KEY,
    nest_git_card_title,
)
from tests.test_consent_origin import _channel_for
from tests.test_project_env import _lock_and_cd_clone
from tests.test_workspace_preference import _agent_and_state, _lock_workspace_copy


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


def _set_origin_and_branch(repo: Path, url: str, branch: str = "feature-tip") -> None:
    git = repo / ".git"
    git.mkdir(parents=True, exist_ok=True)
    config_path = git / "config"
    existing = ""
    if config_path.is_file():
        existing = config_path.read_text(encoding="utf-8")
    lines: list[str] = []
    skipping_origin = False
    for line in existing.splitlines():
        stripped = line.strip()
        if stripped == '[remote "origin"]':
            skipping_origin = True
            continue
        if skipping_origin:
            if stripped.startswith("[") and stripped != '[remote "origin"]':
                skipping_origin = False
            else:
                continue
        if not skipping_origin:
            lines.append(line)
    lines.append('[remote "origin"]')
    lines.append(f"\turl = {url}")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (git / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")


def test_detector_matches_printenv_and_named_env_dumps() -> None:
    assert command_dumps_secret_token_env("printenv GH_TOKEN GITHUB_TOKEN") is True
    assert command_dumps_secret_token_env("printenv") is True
    assert command_dumps_secret_token_env("env GH_TOKEN") is True
    assert command_dumps_secret_token_env("env") is False
    assert command_dumps_secret_token_env("echo hello") is False
    assert command_dumps_secret_token_env("gh auth token") is True
    assert command_dumps_secret_token_env("gh pr create") is False
    assert is_gh_cli(parse_cli_command("gh pr create")) is True
    assert command_needs_gh_auth(parse_cli_command("gh pr create")) is True
    assert command_needs_gh_auth(parse_cli_command("gh auth status")) is True
    assert command_needs_gh_auth(parse_cli_command("gh auth login")) is True
    assert command_needs_gh_auth(parse_cli_command("gh help")) is False
    assert command_needs_gh_auth(parse_cli_command("gh --version")) is False


def test_printenv_token_dump_never_opens_approve_on_locked_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    channel = _channel_for(agent.id)
    command = "printenv GH_TOKEN GITHUB_TOKEN"
    blocked = execute_bm_cli(agent, state, command, channel_id=channel.id)
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.consent_required is False
    assert blocked.approval_request_id is None
    assert db.list_cli_approval_requests(status="pending") == []
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert "Blocked" in blob
    assert "printenv" in blob.lower() or "GH_TOKEN" in blob
    assert "compare URL" in blob or "Nest git" in blob
    expected = never_allowed_operator_note(agent.name, command)
    assert "auto-denied (never allowed)" in expected
    notes = [item.content for item in db.list_channel_messages(channel.id)]
    assert expected in notes
    assert not any(item.approval_id for item in db.list_channel_messages(channel.id))
    assert not any("Approve" in (item.content or "") for item in db.list_channel_messages(channel.id))
    assert "ghp_" not in f"{blocked.detail} {blocked.prompt_content}"

    retry = execute_bm_cli(agent, state, command, channel_id=channel.id)
    assert retry.approval_request_id is None
    assert db.list_cli_approval_requests(status="pending") == []
    assert [
        item.content for item in db.list_channel_messages(channel.id) if item.content == expected
    ] == [expected]


def test_printenv_on_locked_clone_with_shell_off_skips_enable_card() -> None:
    agent, state = _agent_and_state()
    channel = _channel_for(agent.id)
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    command = "printenv GH_TOKEN"
    blocked = execute_bm_cli(agent, state, command, channel_id=channel.id)
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.consent_required is False
    assert blocked.approval_request_id is None
    assert db.list_cli_approval_requests(status="pending") == []
    assert db.list_consent_requests(status="pending") == []
    expected = never_allowed_operator_note(agent.name, command)
    assert expected in [item.content for item in db.list_channel_messages(channel.id)]
    peek = policy_engine.evaluate(command, frozenset(), assume_shell=True)
    assert peek.tier == "never_allowed"


def test_approved_printenv_still_never_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    result = execute_approved_command(
        agent,
        state,
        "printenv GH_TOKEN GITHUB_TOKEN",
        approval_request_id="approved-printenv",
        cwd=dest,
    )
    assert result.ok is False
    assert result.approval_required is False
    assert "Blocked" in f"{result.detail} {result.prompt_content}"
    assert db.list_cli_approval_requests(status="pending") == []


def test_gh_without_nest_git_reuses_one_nest_git_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    channel = _channel_for(agent.id)
    paused = execute_bm_cli(agent, state, "gh pr create", channel_id=channel.id)
    assert paused.ok is False
    assert paused.approval_required is False
    assert paused.consent_required is True
    assert paused.kind == "nest_git_consent_required"
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == NEST_GIT_KIND
    assert card["title"] == nest_git_card_title(agent.name)
    assert db.list_cli_approval_requests(status="pending") == []
    nest_cards = [
        item for item in db.list_channel_messages(channel.id) if item.consent_id
    ]
    assert len(nest_cards) == 1
    assert not any(item.approval_id for item in db.list_channel_messages(channel.id))

    retry = execute_bm_cli(agent, state, "gh auth status", channel_id=channel.id)
    assert retry.consent_required is True
    assert retry.consent_request_id == paused.consent_request_id
    assert db.list_cli_approval_requests(status="pending") == []
    nest_cards = [
        item for item in db.list_channel_messages(channel.id) if item.consent_id
    ]
    assert len(nest_cards) == 1
    assert dest.startswith("/me/host-work/")


def test_gh_when_ssh_only_is_one_blocked_compare_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake-ssh-key-not-a-pat\n-----END OPENSSH PRIVATE KEY-----\n"
    write_nest_git_secret(NEST_GIT_SSH_KEY, ssh_key)
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git", "feature-tip")
    channel = _channel_for(agent.id)
    compare = github_compare_url(agent, parse_cli_command("gh pr create"), dest)
    assert compare == "https://github.com/acme/app/compare/feature-tip"

    blocked = execute_bm_cli(agent, state, "gh pr create", channel_id=channel.id)
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.consent_required is False
    assert blocked.kind == "nest_git_block"
    assert blocked.approval_request_id is None
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert GH_CLI_NO_AUTH_WHY in blob
    assert compare in blob
    assert "printenv" in blob
    assert ssh_key not in blob
    assert "fake-ssh-key-not-a-pat" not in blob
    assert ssh_key not in json.dumps(blocked.data or {})
    extra = _shell_extra_env(agent, parse_cli_command("gh pr create"), dest)
    assert "GH_TOKEN" not in extra
    assert "GITHUB_TOKEN" not in extra
    assert db.list_cli_approval_requests(status="pending") == []
    assert db.list_consent_requests(status="pending") == []

    expected = gh_auth_operator_note(
        agent.name,
        "gh pr create",
        (blocked.data or {}).get("error") or blob,
    )
    notes = [item.content for item in db.list_channel_messages(channel.id)]
    assert any(compare in (item or "") for item in notes)
    assert any(GH_CLI_NO_AUTH_WHY in (item or "") for item in notes)
    assert not any(item.approval_id for item in db.list_channel_messages(channel.id))
    assert not any("Approve" in (item.content or "") for item in db.list_channel_messages(channel.id))
    assert expected in notes

    retry = execute_bm_cli(agent, state, "gh pr create", channel_id=channel.id)
    assert retry.approval_request_id is None
    assert retry.consent_required is False
    matching = [
        item.content
        for item in db.list_channel_messages(channel.id)
        if item.content == expected
    ]
    assert matching == [expected]


def _fake_ok_shell(captured: dict[str, dict[str, str] | str]):
    def _run(command: str, **kwargs: object) -> ShellExecutionResult:
        captured["command"] = command
        extra = kwargs.get("extra_env") or {}
        captured["extra_env"] = dict(extra) if isinstance(extra, dict) else {}
        return ShellExecutionResult(
            exit_code=0,
            stdout="github.com\n  ✓ Logged in to github.com account bossmod-bot (GH_TOKEN)",
            stderr="",
            timed_out=False,
            duration_ms=1,
        )

    return _run


def test_nest_git_pat_injects_into_gh_subprocess_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_inject-gh-subprocess-HHHH"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    captured: dict[str, dict[str, str] | str] = {}
    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _fake_ok_shell(captured))
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    write_nest_always_rule("gh pr create", dest)
    write_nest_always_rule("gh auth status", dest)
    policy_engine.reload()
    parsed = parse_cli_command("gh pr create")
    assert nest_git_can_inject_gh(agent, parsed, dest) is True

    extra = nest_git_shell_env(agent, parsed, dest)
    assert extra.get("GH_TOKEN") == token
    assert extra.get("GITHUB_TOKEN") == token
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") == token
    assert extra.get("GIT_ASKPASS")
    assert extra.get("GH_PROMPT_DISABLED") == "1"
    assert extra.get("GIT_AUTHOR_NAME") == NEST_GIT_BOT_NAME

    git_extra = nest_git_shell_env(agent, parse_cli_command("git push origin main"), dest)
    assert "GH_TOKEN" not in git_extra
    assert git_extra.get("BOSSMOD_NEST_GIT_PASSWORD") == token

    result = execute_bm_cli(agent, state, "gh pr create")
    assert result.ok is True, result.detail
    extra_env = captured.get("extra_env") or {}
    assert isinstance(extra_env, dict)
    assert extra_env.get("GH_TOKEN") == token
    assert extra_env.get("GITHUB_TOKEN") == token
    assert extra_env.get("BOSSMOD_NEST_GIT_PASSWORD") == token
    assert captured.get("command") == "gh pr create"
    assert token not in (result.prompt_content or "")
    assert token not in (result.detail or "")
    assert token not in json.dumps(result.data or {})
    assert "ghp_" not in (result.prompt_content or "")

    captured.clear()
    status = execute_bm_cli(agent, state, "gh auth status")
    assert status.ok is True, status.detail
    extra_env = captured.get("extra_env") or {}
    assert isinstance(extra_env, dict)
    assert extra_env.get("GH_TOKEN") == token
    assert token not in (status.prompt_content or "")
    assert token not in json.dumps(status.data or {})


def test_printenv_still_denied_when_nest_git_pat_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_printenv-still-denied-IIII"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    agent, state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    write_nest_always_rule("printenv", dest)
    policy_engine.reload()
    blocked = execute_bm_cli(agent, state, "printenv GH_TOKEN")
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert "Blocked" in f"{blocked.detail} {blocked.prompt_content}"
    assert token not in f"{blocked.detail} {blocked.prompt_content}"
    print_extra = _shell_extra_env(agent, parse_cli_command("printenv GH_TOKEN"), dest)
    assert "GH_TOKEN" not in print_extra
    assert print_extra.get("BOSSMOD_NEST_GIT_PASSWORD") != token


def test_gh_auth_token_never_allowed_even_with_pat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_auth-token-dump-JJJJ"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    write_nest_always_rule("gh auth token", dest)
    policy_engine.reload()
    blocked = execute_bm_cli(agent, state, "gh auth token")
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.consent_required is False
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert "Blocked" in blob
    assert token not in blob
    extra = _shell_extra_env(agent, parse_cli_command("gh auth token"), dest)
    assert extra.get("GH_TOKEN") != token
    assert "GH_TOKEN" not in extra
    peek = policy_engine.evaluate("gh auth token", frozenset(), assume_shell=True)
    assert peek.tier == "never_allowed"


def test_gh_auth_login_stays_blocked_when_pat_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_login-still-blocked-KKKK"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git", "feature-tip")
    blocked = execute_bm_cli(agent, state, "gh auth login")
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.kind == "nest_git_block"
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert GH_CLI_NO_AUTH_WHY in blob
    assert token not in blob
    extra = _shell_extra_env(agent, parse_cli_command("gh auth login"), dest)
    assert extra.get("GH_TOKEN") != token
    assert "GH_TOKEN" not in extra


def test_always_allow_unmatched_remote_still_hits_nest_git_for_gh() -> None:
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_always-allow-unmatched-gh-LLLL",
        is_default=False,
    )
    from core.bm_cli.filesystem import agent_artifact_dir
    from core.bm_cli.session import set_cli_cwd
    from tests.test_workspace_preference import _agent_and_state

    agent, state = _agent_and_state()
    dest = "/me/host-work/sample_repo"
    real = agent_artifact_dir(agent.storage_key) / "host-work" / "sample_repo"
    real.mkdir(parents=True, exist_ok=True)
    _set_origin_and_branch(real, "https://github.com/Widgets/app.git")
    set_cli_cwd(agent.id, dest)
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    write_nest_always_rule("gh pr create", dest)
    policy_engine.reload()
    paused = execute_bm_cli(agent, state, "gh pr create")
    assert paused.ok is False
    assert paused.consent_required is True
    assert paused.kind == "nest_git_consent_required"
    error = str(((paused.data or {}).get("host_path_consent") or {}).get("error") or "")
    assert NEST_GIT_NO_MATCH_WHY in error
    extra = _shell_extra_env(agent, parse_cli_command("gh pr create"), dest)
    assert extra.get("GH_TOKEN") != "ghp_always-allow-unmatched-gh-LLLL"
    assert "GH_TOKEN" not in extra
    assert "ghp_always-allow-unmatched-gh-LLLL" not in json.dumps(paused.data or {})


def test_named_credential_match_injects_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    add_credential(
        label="Acme",
        match="github.com/Acme/*",
        pat="ghp_inject-gh-acme-MMMM",
        is_default=False,
    )
    add_credential(
        label="Widgets",
        match="github.com/Widgets/app",
        pat="ghp_inject-gh-widgets-NNNN",
        is_default=False,
    )
    from core.bm_cli.filesystem import agent_artifact_dir
    from core.bm_cli.session import set_cli_cwd
    from tests.test_workspace_preference import _agent_and_state

    captured: dict[str, dict[str, str] | str] = {}
    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _fake_ok_shell(captured))
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    agent, state = _agent_and_state()
    dest = "/me/host-work/sample_repo"
    real = agent_artifact_dir(agent.storage_key) / "host-work" / "sample_repo"
    real.mkdir(parents=True, exist_ok=True)
    _set_origin_and_branch(real, "https://github.com/Acme/tools.git")
    set_cli_cwd(agent.id, dest)
    write_nest_always_rule("gh pr create", dest)
    policy_engine.reload()
    execute_bm_cli(agent, state, "gh pr create")
    extra = captured.get("extra_env") or {}
    assert isinstance(extra, dict)
    assert extra.get("GH_TOKEN") == "ghp_inject-gh-acme-MMMM"
    assert extra.get("GITHUB_TOKEN") == "ghp_inject-gh-acme-MMMM"

    captured.clear()
    _set_origin_and_branch(real, "https://github.com/Widgets/app.git")
    execute_bm_cli(agent, state, "gh pr create")
    extra = captured.get("extra_env") or {}
    assert isinstance(extra, dict)
    assert extra.get("GH_TOKEN") == "ghp_inject-gh-widgets-NNNN"


def test_host_gh_token_is_stripped_subprocess_inject_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_token = "ghp_host-must-not-leak-OOOO"
    monkeypatch.setenv("GH_TOKEN", host_token)
    monkeypatch.setenv("GITHUB_TOKEN", host_token)
    dumped = execute_shell_command("printenv GH_TOKEN", cwd=tmp_path)
    assert host_token not in (dumped.stdout or "")
    assert host_token not in (dumped.stderr or "")

    injected = "ghp_subprocess-only-PPPP"
    echoed = execute_shell_command(
        "printenv GH_TOKEN",
        cwd=tmp_path,
        extra_env={"GH_TOKEN": injected},
    )
    assert injected not in (echoed.stdout or "")
    assert injected not in (echoed.stderr or "")
    assert redact_secret_env_values(injected, {"GH_TOKEN": injected}) == "***"


def test_self_host_remote_sets_gh_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "ghp_enterprise-inject-QQQQ"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    agent, _state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://git.example.internal/acme/app.git")
    extra = nest_git_shell_env(agent, parse_cli_command("gh pr create"), dest)
    assert extra.get("GH_TOKEN") == token
    assert extra.get("GH_HOST") == "git.example.internal"
    assert extra.get("GH_ENTERPRISE_TOKEN") == token


def test_preview_gh_without_pat_is_blocked_not_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    preview = preview_bm_cli(agent, state, "gh auth status")
    assert preview.ok is False
    assert preview.approval_required is False
    assert GH_CLI_NO_AUTH_WHY in f"{preview.detail} {preview.prompt_content}"
    assert db.list_cli_approval_requests(status="pending") == []
    assert dest.startswith("/me/host-work/")


def test_preview_gh_with_pat_is_not_an_auth_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_nest_git_secret(NEST_GIT_PAT_KEY, "ghp_preview-inject-RRRR")
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    preview = preview_bm_cli(agent, state, "gh auth status")
    blob = f"{preview.detail} {preview.prompt_content}"
    assert GH_CLI_NO_AUTH_WHY not in blob
    assert "ghp_preview-inject-RRRR" not in blob
    assert dest.startswith("/me/host-work/")
