"""Shared /projects deliverables rewrite into the artifact root.

Read/list/find must not quiet-drop in the path jail. Remaining escapes
are Blocked {why} with a rewrite steer. Host Desktop stays locked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.filesystem import projects_artifact_root
from core.bm_cli.locked_clone_outcome import (
    PATH_JAIL_BLOCKED_WHY,
    rewrite_virtual_shell_paths,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli


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


def _reviewer():
    agent = db.create_agent("Reviewer", role="Code Auditor")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _seed_shared_deliverable() -> tuple[Path, str]:
    root = projects_artifact_root() / "llm-helper-review" / "a1-impl"
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    target = tests_dir / "test_ok.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (root / "README.md").write_text("shared deliverable\n", encoding="utf-8")
    return root, "/projects/llm-helper-review/a1-impl"


def test_rewrite_projects_find_into_artifact_root() -> None:
    agent, _state = _reviewer()
    real, virtual = _seed_shared_deliverable()
    rewritten = rewrite_virtual_shell_paths(
        agent, parse_cli_command(f"find {virtual}"), "/me"
    )
    assert str(real) in rewritten.raw
    assert "/projects/" not in rewritten.raw
    assert rewritten.raw.startswith("find ")


def test_find_projects_allows_via_rewrite_not_quiet_jail() -> None:
    _enable_shell()
    agent, state = _reviewer()
    _real, virtual = _seed_shared_deliverable()

    listed = execute_bm_cli(agent, state, f"find {virtual}")
    assert listed.ok is True, listed.prompt_content
    blob = f"{listed.detail} {listed.prompt_content}"
    assert "test_ok.py" in blob
    assert "path jail" not in blob.lower()
    assert listed.approval_required is False

    tests = execute_bm_cli(agent, state, f"find {virtual}/tests")
    assert tests.ok is True, tests.prompt_content
    assert "test_ok.py" in (tests.prompt_content or "")
    assert "path jail" not in (tests.detail or "").lower()


def test_read_projects_via_virtual_and_shell() -> None:
    _enable_shell()
    agent, state = _reviewer()
    _real, virtual = _seed_shared_deliverable()
    readme = f"{virtual}/README.md"

    listed = execute_bm_cli(agent, state, f"ls {virtual}")
    assert listed.ok is True, listed.prompt_content
    assert "README.md" in (listed.prompt_content or "")
    assert "tests/" in (listed.prompt_content or "")

    read = execute_bm_cli(agent, state, f"cat {readme}")
    assert read.ok is True, read.prompt_content
    assert "shared deliverable" in (read.prompt_content or "")

    headed = execute_bm_cli(agent, state, f"head -n 1 {readme}")
    assert headed.ok is True, headed.prompt_content
    assert "shared deliverable" in (headed.prompt_content or "")
    assert "path jail" not in (headed.detail or "").lower()


def test_outside_jail_is_blocked_with_why_and_steer() -> None:
    _enable_shell()
    agent, state = _reviewer()

    denied = execute_bm_cli(agent, state, "find /etc/passwd")
    assert denied.ok is False
    assert denied.approval_required is False
    blob = f"{denied.detail} {denied.prompt_content}"
    assert "Blocked" in blob
    assert "path jail" in blob.lower()
    assert "/projects" in blob
    assert "rewrite" in blob.lower()
    assert "root:" not in blob
    assert PATH_JAIL_BLOCKED_WHY in (denied.data or {}).get("error", "")


def test_desktop_mutate_stays_denied(tmp_path: Path) -> None:
    _enable_shell()
    agent, state = _reviewer()
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    secret = desktop / "secret.txt"
    secret.write_text("keep\n", encoding="utf-8")

    blocked = execute_bm_cli(agent, state, f"sort -o {secret} {secret}")
    assert blocked.ok is False
    assert blocked.approval_required is False
    blob = f"{blocked.detail} {blocked.prompt_content}"
    assert "Blocked" in blob
    assert "path jail" in blob.lower() or "outside" in blob.lower()
    assert secret.read_text(encoding="utf-8") == "keep\n"
    assert db.list_cli_approval_requests(status="pending") == []
