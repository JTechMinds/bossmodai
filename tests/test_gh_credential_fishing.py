"""Hard-deny GH_TOKEN env dumps; one Nest git / compare-URL card for gh."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.notifications import never_allowed_operator_note
from core.bm_cli.nest_git import (
    command_needs_gh_auth,
    github_compare_url,
    is_gh_cli,
    write_nest_git_secret,
)
from core.bm_cli.nest_git_consent import gh_auth_operator_note
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import (
    _shell_extra_env,
    execute_approved_command,
    execute_bm_cli,
    preview_bm_cli,
)
from core.bm_cli.secret_env import command_dumps_secret_token_env
from core.models.nest_git import (
    GH_CLI_NO_AUTH_WHY,
    NEST_GIT_KIND,
    NEST_GIT_PAT_KEY,
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
    if '[remote "origin"]' not in existing:
        existing += f'\n[remote "origin"]\n\turl = {url}\n'
        config_path.write_text(existing, encoding="utf-8")
    (git / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")


def test_detector_matches_printenv_and_named_env_dumps() -> None:
    assert command_dumps_secret_token_env("printenv GH_TOKEN GITHUB_TOKEN") is True
    assert command_dumps_secret_token_env("printenv") is True
    assert command_dumps_secret_token_env("env GH_TOKEN") is True
    assert command_dumps_secret_token_env("env") is False
    assert command_dumps_secret_token_env("echo hello") is False
    assert is_gh_cli(parse_cli_command("gh pr create")) is True
    assert command_needs_gh_auth(parse_cli_command("gh pr create")) is True
    assert command_needs_gh_auth(parse_cli_command("gh auth status")) is True
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
    assert "GH_TOKEN" in blob
    assert "never allowed" in blob.lower() or "never_allowed" in blob.lower()
    assert "printenv" in blob.lower()
    expected = never_allowed_operator_note(agent.name, command)
    notes = [item.content for item in db.list_channel_messages(channel.id)]
    assert expected in notes
    assert not any(item.approval_id for item in db.list_channel_messages(channel.id))
    assert not any("Approve" in (item.content or "") for item in db.list_channel_messages(channel.id))
    dumped = json.dumps(blocked.data or {})
    assert "ghp_" not in dumped

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


def test_gh_when_nest_git_covers_push_is_one_blocked_compare_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_already-pushed-nest-EEEE"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
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
    assert "gh auth login" in blob
    assert "printenv" in blob
    assert token not in blob
    assert token not in json.dumps(blocked.data or {})
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
    assert token not in "".join(notes)
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


def test_gh_does_not_inject_pat_as_gh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "ghp_parked-gh-inject-FFFF"
    write_nest_git_secret(NEST_GIT_PAT_KEY, token)
    agent, _state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    extra = _shell_extra_env(agent, parse_cli_command("gh pr create"), dest)
    assert "GH_TOKEN" not in extra
    assert "GITHUB_TOKEN" not in extra
    assert extra.get("BOSSMOD_NEST_GIT_PASSWORD") != token
    assert token not in json.dumps(extra)


def test_preview_gh_is_blocked_not_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_nest_git_secret(NEST_GIT_PAT_KEY, "ghp_preview-block-GGGG")
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _set_origin_and_branch(real, "https://github.com/acme/app.git")
    preview = preview_bm_cli(agent, state, "gh auth status")
    assert preview.ok is False
    assert preview.approval_required is False
    assert GH_CLI_NO_AUTH_WHY in f"{preview.detail} {preview.prompt_content}"
    assert db.list_cli_approval_requests(status="pending") == []
    assert dest.startswith("/me/host-work/")
