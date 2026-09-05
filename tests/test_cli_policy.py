"""HA-SEC-P0-03 — Shell path jail and hardened CLI policy seeds."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.policy_engine import (
    argv0_basename_after_resolve,
    argv0_policy_names,
    policy_command_subjects,
    policy_engine,
    _match_prefix,
)
from core.bm_cli.runtime import execute_approved_command
from core.bm_cli.shell_executor import (
    PATH_JAIL_DENIED_EXIT_CODE,
    PathJailError,
    assert_argv_within_path_jail,
    execute_shell_command,
)
from db.cli_policy_rules import (
    HARDENED_NEVER_ALLOWED_PATTERNS,
    INTERPRETER_AND_XARGS_PATTERNS,
    POSIX_SHELL_PATTERNS,
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


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _seed_jail_tree(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "agent"
    projects = tmp_path / "projects"
    workspace.mkdir()
    projects.mkdir()
    (workspace / "notes.md").write_text("workspace-ok", encoding="utf-8")
    (projects / "readme.md").write_text("projects-ok", encoding="utf-8")
    return workspace, projects


# ---------------------------------------------------------------------------
# Path jail
# ---------------------------------------------------------------------------


def test_cat_etc_passwd_denied_by_path_jail_even_if_cat_is_allowed(tmp_path: Path) -> None:
    workspace, projects = _seed_jail_tree(tmp_path)

    result = execute_shell_command(
        "cat /etc/passwd",
        cwd=workspace,
        allowed_roots=[workspace, projects],
    )

    assert result.denied_by_path_jail is True
    assert result.exit_code == PATH_JAIL_DENIED_EXIT_CODE
    assert "Path jail" in result.stderr
    assert "/etc/passwd" in result.stderr
    assert "root:" not in result.stdout


def test_relative_escape_and_option_path_denied(tmp_path: Path) -> None:
    workspace, projects = _seed_jail_tree(tmp_path)
    roots = [workspace, projects]

    escaped = execute_shell_command(
        "cat ../../../../etc/passwd",
        cwd=workspace,
        allowed_roots=roots,
    )
    assert escaped.denied_by_path_jail is True

    option = execute_shell_command(
        "cat --show-ends=/etc/passwd",
        cwd=workspace,
        allowed_roots=roots,
    )
    assert option.denied_by_path_jail is True

    with pytest.raises(PathJailError):
        assert_argv_within_path_jail(
            ["head", "-n", "20", "/etc/shadow"],
            cwd=workspace,
            allowed_roots=roots,
        )


def test_workspace_and_projects_paths_allowed(tmp_path: Path) -> None:
    workspace, projects = _seed_jail_tree(tmp_path)
    roots = [workspace, projects]

    relative = execute_shell_command(
        "cat notes.md",
        cwd=workspace,
        allowed_roots=roots,
    )
    assert relative.denied_by_path_jail is False
    assert relative.exit_code == 0
    assert relative.stdout.strip() == "workspace-ok"

    absolute_project = execute_shell_command(
        f"cat {projects / 'readme.md'}",
        cwd=workspace,
        allowed_roots=roots,
    )
    assert absolute_project.denied_by_path_jail is False
    assert absolute_project.exit_code == 0
    assert absolute_project.stdout.strip() == "projects-ok"


def test_symlink_escape_denied(tmp_path: Path) -> None:
    workspace, projects = _seed_jail_tree(tmp_path)
    leak = workspace / "leak"
    leak.symlink_to("/etc/passwd")

    result = execute_shell_command(
        "cat leak",
        cwd=workspace,
        allowed_roots=[workspace, projects],
    )
    assert result.denied_by_path_jail is True
    assert "root:" not in result.stdout


def test_tilde_other_user_denied(tmp_path: Path) -> None:
    workspace, projects = _seed_jail_tree(tmp_path)
    with pytest.raises(PathJailError, match="~user"):
        assert_argv_within_path_jail(
            ["cat", "~root/.ssh/id_rsa"],
            cwd=workspace,
            allowed_roots=[workspace, projects],
        )


def test_execute_approved_command_still_applies_path_jail() -> None:
    agent = db.create_agent("Jail Tester")
    state = db.get_agent_state(agent.id)
    assert state is not None
    workspace = agent_artifact_dir(agent.storage_key)
    try:
        outside = db.create_cli_approval_request(
            agent_id=agent.id,
            command="cat /etc/passwd",
            cwd="/me",
        )
        denied = execute_approved_command(
            agent,
            state,
            "cat /etc/passwd",
            approval_request_id=outside.id,
        )
        assert denied.ok is False
        assert denied.executor == "shell"
        payload = f"{denied.detail} {denied.data}"
        assert "Path jail" in payload
        assert "root:" not in payload

        notes = workspace / "notes.md"
        notes.write_text("approved-ok", encoding="utf-8")
        inside = db.create_cli_approval_request(
            agent_id=agent.id,
            command="cat notes.md",
            cwd="/me",
        )
        allowed = execute_approved_command(
            agent,
            state,
            "cat notes.md",
            approval_request_id=inside.id,
        )
        assert allowed.ok is True
        assert "approved-ok" in allowed.prompt_content
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ---------------------------------------------------------------------------
# Seed / policy
# ---------------------------------------------------------------------------


def test_cli_shell_enabled_defaults_false() -> None:
    assert config.get("cli_shell_enabled") == "false"


def test_seed_rules_lock_interpreters_xargs_and_shells() -> None:
    rules = db.list_cli_policy_rules()
    always = {rule.pattern for rule in rules if rule.tier == "always_allowed"}
    never = {rule.pattern for rule in rules if rule.tier == "never_allowed"}

    assert not (INTERPRETER_AND_XARGS_PATTERNS & always)
    assert INTERPRETER_AND_XARGS_PATTERNS <= never
    assert POSIX_SHELL_PATTERNS <= never
    assert HARDENED_NEVER_ALLOWED_PATTERNS <= never
    assert "cat" in always


def test_reconcile_hardens_legacy_always_allowed_rows() -> None:
    db.execute("DELETE FROM cli_policy_rules")
    db.create_cli_policy_rule(
        tier="always_allowed",
        pattern="python",
        match_mode="prefix",
        description="legacy interpreter",
        category="development",
    )
    db.create_cli_policy_rule(
        tier="always_allowed",
        pattern="xargs",
        match_mode="prefix",
        description="legacy xargs",
        category="general",
    )
    db.create_cli_policy_rule(
        tier="approval_required",
        pattern="node",
        match_mode="prefix",
        description="legacy node",
        category="development",
    )

    changed = db.reconcile_hardened_cli_policy_rules()
    assert changed >= 3

    by_pattern = {rule.pattern: rule.tier for rule in db.list_cli_policy_rules()}
    for pattern in HARDENED_NEVER_ALLOWED_PATTERNS:
        assert by_pattern[pattern] == "never_allowed"


def test_policy_engine_denies_hardened_commands_when_shell_enabled() -> None:
    _enable_shell()

    cat_decision = policy_engine.evaluate("cat /etc/passwd", frozenset())
    assert cat_decision.allowed is True
    assert cat_decision.tier == "always_allowed"

    for command in (
        "bash -c id",
        "sh -c id",
        "zsh -c id",
        "dash -c id",
        "python -c 'print(1)'",
        "python3 -c 'print(1)'",
        "node -e 'console.log(1)'",
        "xargs rm",
    ):
        decision = policy_engine.evaluate(command, frozenset())
        assert decision.allowed is False, command
        assert decision.approval_required is False, command
        assert decision.tier == "never_allowed", command


# ---------------------------------------------------------------------------
# HA-SEC-P1-04 — argv[0] basename after resolve
# ---------------------------------------------------------------------------


def test_raw_prefix_does_not_see_bin_bash() -> None:
    """The remaining hole: prefix `bash` does not match `/bin/bash` as a raw string."""
    assert _match_prefix("bash -c id", "bash") is True
    assert _match_prefix("/bin/bash -c id", "bash") is False
    assert _match_prefix("/usr/bin/python3 -c 'print(1)'", "python3") is False


def test_argv0_basename_after_resolve_strips_path() -> None:
    assert argv0_basename_after_resolve("/bin/bash") == "bash"
    assert argv0_basename_after_resolve("/usr/bin/python3") == "python3"
    assert argv0_basename_after_resolve("./xargs") == "xargs"
    assert argv0_basename_after_resolve("bash") == "bash"
    # Do not follow the python3 → python3.12 symlink for the primary name.
    assert argv0_basename_after_resolve("/usr/bin/python3") != "python3.12"


def test_argv0_policy_names_include_version_strip() -> None:
    names = argv0_policy_names("/usr/bin/python3.12")
    assert "python3.12" in names
    assert "python3" in names


def test_policy_subjects_include_basename_rewrite() -> None:
    subjects = policy_command_subjects("/bin/bash -c id")
    assert "/bin/bash -c id" in subjects
    assert "bash -c id" in subjects


def test_policy_engine_denies_path_qualified_shells_and_xargs() -> None:
    _enable_shell()

    for command in (
        "/bin/bash -c id",
        "/usr/bin/bash -c id",
        "/bin/sh -c id",
        "/usr/bin/python3 -c 'print(1)'",
        "/usr/bin/python3.12 -c 'print(1)'",
        "/usr/bin/python -c 'print(1)'",
        "/usr/bin/node -e 'console.log(1)'",
        "/bin/xargs rm",
        "./bash -c id",
    ):
        decision = policy_engine.evaluate(command, frozenset())
        assert decision.allowed is False, command
        assert decision.approval_required is False, command
        assert decision.tier == "never_allowed", command


def test_path_qualified_always_allowed_still_matches_basename() -> None:
    _enable_shell()
    decision = policy_engine.evaluate("/bin/cat notes.md", frozenset())
    assert decision.allowed is True
    assert decision.tier == "always_allowed"
