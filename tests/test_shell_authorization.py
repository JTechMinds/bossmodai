"""authorize_shell_command decides without side effects; materialize acts like the runtime.

Each scenario runs twice on a fresh database: once as ``authorize`` then
``materialize``, once through ``execute_bm_cli``. The two must leave the
same result and the same rows behind, and ``authorize`` alone must leave
the database byte-for-byte unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import pytest

import db
from core import config
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.shell_authorization import ShellAuthorization, authorize_shell_command
from core.bm_cli.types import BossModCliResult
from core.models.host_path_consent import SHELL_EXECUTOR_KIND
from core.models.nest_git import NEST_GIT_KIND
from db.crud import query
from tests.test_project_env import _lock_and_cd_clone
from tests.test_workspace_preference import _agent_and_state, _lock_workspace_copy

_RESUME = "host_path_consent_resolved"


def _fresh_db() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def setup_function() -> None:
    _fresh_db()


def teardown_function() -> None:
    db.close_connection()


def _enable_shell() -> None:
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def _snapshot() -> dict[str, list[dict[str, Any]]]:
    """Every row of every table, so an update counts as a write too."""
    tables = [
        row["name"]
        for row in query("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    ]
    return {name: query(f'SELECT * FROM "{name}"') for name in tables}


def _row_counts(snapshot: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {name: len(rows) for name, rows in snapshot.items()}


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {name: after[name] - before.get(name, 0) for name in after if after[name] != before.get(name, 0)}


def _projection(result: BossModCliResult) -> tuple[Any, ...]:
    """The result minus per-run ids."""
    return (
        result.ok,
        result.kind,
        result.detail,
        result.consent_required,
        result.approval_required,
        result.executor,
        result.exit_code,
        result.cwd,
        result.matched_rule_id,
        bool(result.consent_request_id),
        bool(result.approval_request_id),
        sorted((result.data or {}).keys()),
    )


def _audit_rows() -> list[tuple[Any, ...]]:
    return [
        (row["command"], row["executor"], row["policy_tier"], row["decision"], row["result_kind"], row["trigger_type"])
        for row in query("SELECT * FROM bm_cli_events ORDER BY rowid")
    ]


Setup = Callable[[], tuple[Any, Any]]


def _authorize_without_writes(
    setup: Setup, command: str, *, trigger_type: str | None = None,
) -> tuple[ShellAuthorization, Any]:
    agent, _state = setup()
    before = _snapshot()
    auth = authorize_shell_command(
        agent, parse_cli_command(command), get_cli_cwd(agent.id), trigger_type=trigger_type,
    )
    assert _snapshot() == before, "authorize_shell_command wrote to the database"
    return auth, agent


def _materialized_matches_runtime(
    setup: Setup, command: str, *, trigger_type: str | None = None,
) -> tuple[ShellAuthorization, BossModCliResult]:
    """Run *command* both ways on fresh databases; assert the same outcome and rows."""
    auth, _agent = _authorize_without_writes(setup, command, trigger_type=trigger_type)
    assert auth.materialize is not None
    before = _row_counts(_snapshot())
    materialized = auth.materialize(content=None, channel_id=None)
    via_auth = (_projection(materialized), _delta(before, _row_counts(_snapshot())), _audit_rows())

    _fresh_db()
    agent, state = setup()
    before = _row_counts(_snapshot())
    executed = execute_bm_cli(agent, state, command, trigger_type=trigger_type)
    via_runtime = (_projection(executed), _delta(before, _row_counts(_snapshot())), _audit_rows())

    assert via_auth == via_runtime
    return auth, materialized


def _plain() -> tuple[Any, Any]:
    return _agent_and_state()


def _shell_on() -> tuple[Any, Any]:
    _enable_shell()
    return _agent_and_state()


def _locked_copy() -> tuple[Any, Any]:
    agent, state = _agent_and_state()
    _lock_workspace_copy(
        agent.id,
        path="/home/operator/Projects/sample_repo",
        dest="/me/host-work/sample_repo",
    )
    return agent, state


def test_virtual_command_runs_without_materialize() -> None:
    auth, _agent = _authorize_without_writes(_plain, "pwd")

    assert auth.kind == "run"
    assert auth.materialize is None
    assert auth.policy is not None and auth.policy.tier == "virtual"
    assert auth.parsed.raw == "pwd"


def test_shell_off_command_is_a_block_that_records_like_the_runtime() -> None:
    auth, result = _materialized_matches_runtime(_plain, "pytest -q")

    assert auth.kind == "block"
    assert auth.policy is not None and auth.policy.tier == "disabled"
    assert "shell execution is not enabled" in (auth.message or "")
    assert result.ok is False and result.consent_required is False
    assert _audit_rows() == [("pytest -q", "shell", "disabled", "denied", "error", None)]


def test_never_allowed_command_is_a_block_with_operator_chrome() -> None:
    auth, result = _materialized_matches_runtime(_shell_on, "bash -c true")

    assert auth.kind == "block"
    assert auth.policy is not None and auth.policy.tier == "never_allowed"
    assert result.ok is False
    assert result.data is not None and result.data.get("policy_tier") == "never_allowed"
    assert _audit_rows() == [("bash -c true", "shell", "never_allowed", "denied", "error", None)]


def test_unmatched_shell_command_needs_approval_and_gets_one_card() -> None:
    auth, result = _materialized_matches_runtime(_shell_on, "frobnicate --now")

    assert auth.kind == "approval"
    assert auth.policy is not None and auth.policy.approval_required is True
    assert auth.message == auth.policy.message
    assert result.approval_required is True
    approvals = query("SELECT * FROM cli_approval_requests")
    assert len(approvals) == 1 and approvals[0]["command"] == "frobnicate --now"


def test_locked_copy_with_shell_off_is_shell_executor_consent() -> None:
    auth, result = _materialized_matches_runtime(_locked_copy, "pytest -q")

    assert auth.kind == "consent"
    assert auth.policy is None
    assert result.consent_required is True
    card = (result.data or {}).get("host_path_consent") or {}
    assert card["kind"] == SHELL_EXECUTOR_KIND
    # The consent result names the virtual executor; the runtime recorded that too.
    assert _audit_rows() == [("pytest -q", "virtual", "disabled", "approval_required", result.kind, None)]


def test_consent_resume_skips_the_shell_executor_card() -> None:
    auth, result = _materialized_matches_runtime(_locked_copy, "pytest -q", trigger_type=_RESUME)

    assert auth.kind == "block"
    assert auth.policy is not None and auth.policy.tier == "disabled"
    assert result.consent_required is False
    assert query("SELECT * FROM host_path_consent_requests WHERE card_kind = $1", [SHELL_EXECUTOR_KIND]) == []


def test_gh_without_nest_git_auth_is_nest_git_consent() -> None:
    auth, result = _materialized_matches_runtime(_shell_on, "gh pr list")

    assert auth.kind == "consent"
    assert result.consent_required is True
    card = (result.data or {}).get("host_path_consent") or {}
    assert card["kind"] == NEST_GIT_KIND


def test_gh_on_consent_resume_is_a_block_not_a_second_card() -> None:
    auth, result = _materialized_matches_runtime(_shell_on, "gh pr list", trigger_type=_RESUME)

    assert auth.kind == "block"
    assert result.ok is False and result.consent_required is False
    assert result.kind == "nest_git_block"


def test_locked_clone_virtual_path_is_rewritten_before_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _state, dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    script = real / "scripts" / "run-tests.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("pytest -q\n", encoding="utf-8")
    before = _snapshot()

    auth = authorize_shell_command(
        agent, parse_cli_command(f"grep -n pytest {dest}/scripts/run-tests.sh"), get_cli_cwd(agent.id),
    )

    assert _snapshot() == before
    assert auth.kind == "run"
    assert auth.policy is not None and auth.policy.executor == "shell"
    assert str(script) in auth.parsed.raw
    assert "/me/host-work" not in auth.parsed.raw


def test_locked_clone_unmatched_write_is_an_approval_on_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    command = "sed -i s/True/False/ tests/test_ok.py"
    before = _snapshot()

    auth = authorize_shell_command(agent, parse_cli_command(command), get_cli_cwd(agent.id))

    assert _snapshot() == before
    assert auth.kind == "approval"
    assert auth.materialize is not None
    materialized = auth.materialize(content=None, channel_id=None)
    # A still-pending identical card would be reused (``reused_pending``),
    # so decide it first and compare two fresh cards.
    assert materialized.approval_request_id is not None
    assert db.reject_cli_approval_request(materialized.approval_request_id) is not None
    executed = execute_bm_cli(agent, state, command)
    assert _projection(materialized) == _projection(executed)
    assert materialized.approval_required is True
    assert (real / "tests" / "test_ok.py").read_text(encoding="utf-8").count("True") > 0


def test_authorization_rejects_a_run_that_materializes() -> None:
    with pytest.raises(ValueError, match="must materialize unless it runs"):
        ShellAuthorization("run", parse_cli_command("pwd"), None, None, lambda **_: _never())
    with pytest.raises(ValueError, match="needs a policy decision"):
        ShellAuthorization("run", parse_cli_command("pwd"), None, None, None)


def _never() -> BossModCliResult:
    raise AssertionError("not called")
