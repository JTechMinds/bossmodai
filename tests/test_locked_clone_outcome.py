"""Locked-clone shared shell outcome: allow / rewrite / approval / Blocked."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.locked_clone_outcome import (
    DEFAULT_APPROVAL_MESSAGE,
    LOCKED_CLONE_DEFAULT_STEER,
    PATH_JAIL_BLOCKED_WHY,
    PATH_JAIL_STEER,
    decide_locked_clone_shell_outcome,
    is_virtual_cli_path,
    path_jail_blocked_result,
    prepare_locked_clone_approved,
    rewrite_virtual_shell_paths,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import VIRTUAL_COMMANDS, execute_bm_cli, preview_bm_cli
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from tests.test_project_env import _lock_and_cd_clone
from tests.test_workspace_preference import (
    _agent_and_state,
    _allow_host,
    _enable_shell,
    _init_tiny_pytest_repo,
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


def test_never_allowed_operator_note_wording() -> None:
    from core.agent_loop.notifications import never_allowed_operator_note

    note = never_allowed_operator_note("Jim", "bash scripts/run-tests.sh")
    assert note == (
        "Jim tried `bash scripts/run-tests.sh` — auto-denied (never allowed). "
        "To enable, update CLI Policy in Settings."
    )


def test_virtual_cli_path_detects_me_and_projects() -> None:
    assert is_virtual_cli_path("/me/host-work/llm_helper/tests/x.py") is True
    assert is_virtual_cli_path("/projects/demo") is True
    assert is_virtual_cli_path("/home/operator/Desktop/x.py") is False


def test_nest_sed_posts_approval_chrome_not_silent_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_consent_origin import _channel_for

    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    target = real / "tests" / "test_ok.py"
    before = target.read_text(encoding="utf-8")
    channel = _channel_for(agent.id)

    paused = execute_bm_cli(
        agent,
        state,
        "sed -i s/True/False/ tests/test_ok.py",
        channel_id=channel.id,
    )
    assert paused.ok is False
    assert paused.approval_required is True, paused.detail
    assert paused.approval_request_id
    assert paused.kind == "approval_required"
    assert "request id" in (paused.prompt_content or "")
    stored = db.get_cli_approval_request(paused.approval_request_id)
    assert stored is not None
    assert stored.channel_id == channel.id
    cards = [
        item
        for item in db.list_channel_messages(channel.id)
        if item.approval_id == stored.id
    ]
    assert len(cards) == 1
    assert target.read_text(encoding="utf-8") == before
    assert dest.startswith("/me/host-work/")


def test_nest_virtual_path_grep_is_not_a_quiet_jail_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    script = real / "scripts" / "run-tests.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("pytest -q\n# requirements-dev\n", encoding="utf-8")
    virtual = f"{dest}/scripts/run-tests.sh"

    result = execute_bm_cli(agent, state, f"grep -n requirements-dev {virtual}")
    assert result.ok is True, result.prompt_content
    assert result.approval_required is False
    assert "requirements-dev" in (result.prompt_content or "")
    assert "path jail" not in (result.detail or "").lower()
    assert "Path jail" not in (result.prompt_content or "")


def test_rewrite_virtual_nest_path_to_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    virtual = f"{dest}/tests/test_ok.py"
    rewritten = rewrite_virtual_shell_paths(
        agent, parse_cli_command(f"grep -n ok {virtual}"), dest
    )
    assert str(real / "tests" / "test_ok.py") in rewritten.raw
    assert "/me/host-work" not in rewritten.raw


def test_never_allowed_names_the_gate_and_steers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    blocked = execute_bm_cli(agent, state, "python -m pytest -q")
    assert blocked.ok is False
    assert blocked.approval_required is False
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert "Blocked" in blob
    assert "never_allowed" in blob.lower() or "python" in blob.lower()
    assert "uv run pytest" in blob or ".venv/bin/pytest" in blob
    assert "desk" not in blob.lower() or "do not invent a desk" in blob.lower()

    bash = execute_bm_cli(agent, state, "bash scripts/run-tests.sh")
    assert bash.ok is False
    assert bash.approval_required is False
    assert bash.approval_request_id is None
    bash_blob = f"{bash.detail} {bash.prompt_content}"
    assert "Blocked" in bash_blob
    assert "uv run pytest" in bash_blob or ".venv/bin/pytest" in bash_blob
    assert dest.startswith("/me/host-work/")


def test_bash_run_tests_posts_operator_note_not_an_approve_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.agent_loop.notifications import never_allowed_operator_note
    from tests.test_consent_origin import _channel_for

    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    channel = _channel_for(agent.id)
    command = "bash scripts/run-tests.sh"
    blocked = execute_bm_cli(agent, state, command, channel_id=channel.id)
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.consent_required is False
    assert blocked.approval_request_id is None
    assert db.list_cli_approval_requests(status="pending") == []
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert "Blocked" in blob
    assert "uv run pytest" in blob or ".venv/bin/pytest" in blob
    assert "request id" not in (blocked.prompt_content or "").lower()
    expected = never_allowed_operator_note(agent.name, command)
    assert "auto-denied (never allowed)" in expected
    assert "CLI Policy" in expected
    assert "Settings" in expected
    notes = [item.content for item in db.list_channel_messages(channel.id)]
    assert expected in notes
    assert not any(item.approval_id for item in db.list_channel_messages(channel.id))
    assert not any(
        "Approve" in (item.content or "") for item in db.list_channel_messages(channel.id)
    )
    retry = execute_bm_cli(agent, state, command, channel_id=channel.id)
    assert retry.approval_request_id is None
    assert [
        item.content for item in db.list_channel_messages(channel.id) if item.content == expected
    ] == [expected]


def test_bash_run_tests_never_allowed_note_on_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.agent_loop.notifications import never_allowed_operator_note

    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    command = "bash scripts/run-tests.sh"
    blocked = execute_bm_cli(agent, state, command)
    assert blocked.ok is False
    assert blocked.approval_request_id is None
    expected = never_allowed_operator_note(agent.name, command)
    notes = [
        item.content
        for item in db.list_notifications(agent_id=agent.id, chat_visible=True)
    ]
    assert expected in notes
    assert "Settings" in expected
    assert "CLI Policy" in expected
    assert db.list_cli_approval_requests(status="pending") == []


def test_chrome_fail_is_fail_closed_on_nest_sed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_consent_origin import _channel_for

    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    channel = _channel_for(agent.id)
    monkeypatch.setattr(
        "core.agent_loop.notifications.persist_channel_notification",
        lambda *args, **kwargs: {},
    )
    paused = execute_bm_cli(
        agent,
        state,
        "sed -i s/a/b/ tests/test_ok.py",
        channel_id=channel.id,
    )
    assert paused.approval_required is False
    assert "could not be posted" in (paused.detail or "").lower()
    assert db.list_cli_approval_requests(status="pending") == []
    assert db.list_channel_messages(channel.id) == []


def test_outside_clone_mutate_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    host_file = desktop / "secret.txt"
    host_file.write_text("keep\n", encoding="utf-8")

    blocked = execute_bm_cli(agent, state, f"sed -i s/keep/gone/ {host_file}")
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.kind == "host_deny"
    assert "Blocked" in (blocked.detail or "")
    assert "request_host_access" in (blocked.detail or "")
    assert host_file.read_text(encoding="utf-8") == "keep\n"
    assert db.list_cli_approval_requests(status="pending") == []


def test_allowlisted_host_path_passes_from_a_self_made_repo_in_me(tmp_path: Path) -> None:
    _enable_shell()
    agent, state = _agent_and_state()
    repo = agent_artifact_dir(agent.storage_key) / "host-work" / "repo"
    repo.mkdir(parents=True)
    _init_tiny_pytest_repo(repo)
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "data.txt").write_text("granted data\n", encoding="utf-8")
    _allow_host(ext)
    cd = execute_bm_cli(agent, state, "cd /me/host-work/repo")
    assert cd.ok is True, cd.prompt_content

    # head, not the virtual cat: only a shell command reaches the locked-clone gate.
    read = execute_bm_cli(agent, state, f"head {ext / 'data.txt'}")
    assert read.ok is True, read.prompt_content
    assert read.executor == "shell"
    assert "granted data" in (read.prompt_content or "")

    outcome = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command(f"cp tests/test_ok.py {ext / 'copy.py'}"),
        "/me/host-work/repo",
        virtual_commands=VIRTUAL_COMMANDS,
    )
    assert outcome is not None
    assert outcome.kind != "never_allowed", outcome.message

    from tests.test_consent_origin import _channel_for

    # The reported command shape: cp and chmod are approval_required, so it pauses for approval.
    target = ext / "copy.py"
    reported = execute_bm_cli(
        agent,
        state,
        f"cp tests/test_ok.py {target} && chmod 0644 {target} && sha256sum {target}",
        channel_id=_channel_for(agent.id).id,
    )
    assert reported.approval_required is True, reported.detail
    assert reported.kind != "host_deny"
    assert "locked clone" not in (reported.detail or "")
    assert "locked clone" not in (reported.prompt_content or "")
    assert not target.exists()


def test_allowlisted_host_path_passes_beside_a_locked_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "data.txt").write_text("granted data\n", encoding="utf-8")
    roots = [str((tmp_path / "llm_helper").resolve()), str(ext.resolve())]
    db.set_setting("workspace_host_roots", "\n".join(roots), "cli_policy")
    config.reload()

    read = execute_bm_cli(agent, state, f"head {ext / 'data.txt'}")
    assert read.ok is True, read.prompt_content
    assert read.executor == "shell"
    assert "granted data" in (read.prompt_content or "")


def test_locked_host_tree_stays_blocked_even_when_allowlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    host = tmp_path / "llm_helper"

    blocked = execute_bm_cli(agent, state, f"head {host / 'pytest.ini'}")
    assert blocked.ok is False
    assert blocked.approval_required is False
    assert blocked.kind == "host_deny"
    assert dest in (blocked.detail or "")
    assert db.list_cli_approval_requests(status="pending") == []
    assert db.list_consent_requests(status="pending") == []


def test_approved_command_gets_the_same_host_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    host = tmp_path / "llm_helper"
    ext = tmp_path / "ext"
    ext.mkdir()
    roots = [str(host.resolve()), str(ext.resolve())]
    db.set_setting("workspace_host_roots", "\n".join(roots), "cli_policy")
    config.reload()

    allowed = prepare_locked_clone_approved(
        agent, parse_cli_command(f"cp tests/test_ok.py {ext / 'copy.py'}"), dest
    )
    assert isinstance(allowed, ParsedCliCommand)

    refused = prepare_locked_clone_approved(
        agent, parse_cli_command(f"cp tests/test_ok.py {host / 'copy.py'}"), dest
    )
    assert isinstance(refused, BossModCliResult)
    assert refused.ok is False
    assert refused.kind == "host_deny"
    assert dest in (refused.detail or "")
    assert not (host / "copy.py").exists()


def test_never_allowed_without_a_locked_copy_has_no_clone_steer() -> None:
    _enable_shell()
    agent, state = _agent_and_state()

    blocked = execute_bm_cli(agent, state, "bash scripts/run-tests.sh")
    assert blocked.ok is False
    assert blocked.approval_required is False
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert "Blocked" in blob
    assert LOCKED_CLONE_DEFAULT_STEER not in blob


def test_removing_the_copy_releases_the_shell_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    host = tmp_path / "llm_helper"
    shutil.rmtree(real)
    cd = execute_bm_cli(agent, state, "cd /me")
    assert cd.ok is True, cd.prompt_content

    read = execute_bm_cli(agent, state, f"head {host / 'pytest.ini'}")
    assert read.ok is True, read.prompt_content
    assert read.executor == "shell"
    assert "testpaths" in (read.prompt_content or "")


def test_decide_outcome_is_table_driven_not_sed_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    sed = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command("sed -i s/a/b/ tests/test_ok.py"),
        dest,
        virtual_commands=VIRTUAL_COMMANDS,
    )
    assert sed is not None
    assert sed.kind == "approval_required"
    assert DEFAULT_APPROVAL_MESSAGE in (sed.message or "")

    awk = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command("awk '{print}' tests/test_ok.py"),
        dest,
        virtual_commands=VIRTUAL_COMMANDS,
    )
    assert awk is not None
    assert awk.kind == "approval_required"

    echo = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command("echo nest-ok"),
        dest,
        virtual_commands=VIRTUAL_COMMANDS,
    )
    assert echo is not None
    assert echo.kind == "allow"


def test_preview_nest_sed_is_approval_not_silent_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    preview = preview_bm_cli(agent, state, "sed -i s/a/b/ tests/test_ok.py")
    assert preview.approval_required is True
    assert preview.ok is False


def test_path_jail_blocked_why_steers_projects_rewrite() -> None:
    assert PATH_JAIL_BLOCKED_WHY.startswith("Blocked")
    assert "/projects" in PATH_JAIL_BLOCKED_WHY
    assert "rewrite" in PATH_JAIL_BLOCKED_WHY
    assert "enablement" in PATH_JAIL_BLOCKED_WHY


def test_path_jail_blocked_result_leads_with_the_offending_path() -> None:
    message = "Path jail: 'x' resolves outside the allowed workspace roots"

    result = path_jail_blocked_result("cat x", "/me", message)

    why = (result.data or {})["error"]
    assert why.startswith("Blocked — Path jail: 'x'")
    assert PATH_JAIL_STEER in why
    assert why.count("Blocked —") == 1
    assert result.detail == f"BossMod CLI error: {why}"
    assert result.kind == "host_deny"

    bare = path_jail_blocked_result("cat x", "/me")

    assert (bare.data or {})["error"] == PATH_JAIL_BLOCKED_WHY
    assert bare.kind == "host_deny"
