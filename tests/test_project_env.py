"""Locked-clone installs stay in uv/venv — never host pip."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.project_env import (
    HOST_PIP_DENY_MESSAGE,
    detect_project_env,
    is_project_local_pip,
    pip_mutation_args,
    rewrite_for_project_env,
)
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.types import ParsedCliCommand
from tests.test_workspace_preference import (
    _agent_and_state,
    _allow_host,
    _api_client,
    _enable_shell,
    _headers,
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


def _hide_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.bm_cli.project_env.shutil.which",
        lambda name: None,
    )


def _fake_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.bm_cli.project_env.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )


def _write_venv_tool(root: Path, name: str, script: str) -> Path:
    bin_dir = root / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _lock_and_cd_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, object, str, Path]:
    host = tmp_path / "llm_helper"
    host.mkdir()
    _init_tiny_pytest_repo(host)
    _allow_host(host)
    _enable_shell()
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)
    paused = execute_bm_cli(agent, state, f"write {host / 'note.txt'}", content="changed\n")
    request_id = paused.consent_request_id
    assert request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 200, branched.text
    dest = branched.json().get("clone_dest") or ""
    assert dest.startswith("/me/host-work/")
    cd = execute_bm_cli(agent, state, f"cd {dest}")
    assert cd.ok is True
    real = agent_artifact_dir(agent.storage_key) / "host-work" / Path(dest).name
    assert real.is_dir()
    return agent, state, dest, real


# ---------------------------------------------------------------------------
# Pure rewrite
# ---------------------------------------------------------------------------


def test_pip_mutation_args_detects_install_and_skips_list() -> None:
    assert pip_mutation_args(parse_cli_command("pip install pytest")) == ("install", "pytest")
    assert pip_mutation_args(parse_cli_command('pip install -e ".[dev]"')) == (
        "install",
        "-e",
        ".[dev]",
    )
    assert pip_mutation_args(parse_cli_command("python -m pip install pytest")) == (
        "install",
        "pytest",
    )
    assert pip_mutation_args(parse_cli_command("pip list")) is None
    assert pip_mutation_args(parse_cli_command("uv pip install pytest")) is None
    assert is_project_local_pip(parse_cli_command(".venv/bin/pip install pytest")) is True
    assert is_project_local_pip(parse_cli_command("uv pip install pytest")) is True
    assert is_project_local_pip(parse_cli_command("pip install pytest")) is False


def test_rewrite_bare_pip_install_to_venv_pip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_uv(monkeypatch)
    _write_venv_tool(tmp_path, "pip", "exit 0")
    env = detect_project_env(tmp_path)
    outcome = rewrite_for_project_env(
        parse_cli_command("pip install pytest"), env, cwd=tmp_path
    )
    assert isinstance(outcome, ParsedCliCommand)
    assert outcome.raw == ".venv/bin/pip install pytest"


def test_rewrite_editable_and_python_dash_m_pip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_uv(monkeypatch)
    _write_venv_tool(tmp_path, "pip", "exit 0")
    env = detect_project_env(tmp_path)
    editable = rewrite_for_project_env(
        parse_cli_command('pip install -e ".[dev]"'), env, cwd=tmp_path
    )
    assert isinstance(editable, ParsedCliCommand)
    assert editable.name == ".venv/bin/pip"
    assert "install" in editable.args
    assert ".[dev]" in editable.args

    python_pip = rewrite_for_project_env(
        parse_cli_command("python -m pip install pytest"), env, cwd=tmp_path
    )
    assert isinstance(python_pip, ParsedCliCommand)
    assert python_pip.raw == ".venv/bin/pip install pytest"


def test_rewrite_pytest_prefers_uv_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_uv(monkeypatch)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    _write_venv_tool(tmp_path, "pytest", "exit 0")
    env = detect_project_env(tmp_path)
    assert env.has_uv is True
    outcome = rewrite_for_project_env(parse_cli_command("pytest -q"), env, cwd=tmp_path)
    assert isinstance(outcome, ParsedCliCommand)
    assert outcome.raw == "uv run pytest -q"


def test_rewrite_pytest_uses_venv_when_no_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_uv(monkeypatch)
    _write_venv_tool(tmp_path, "pytest", "exit 0")
    env = detect_project_env(tmp_path)
    outcome = rewrite_for_project_env(parse_cli_command("pytest -q"), env, cwd=tmp_path)
    assert isinstance(outcome, ParsedCliCommand)
    assert outcome.raw == ".venv/bin/pytest -q"


def test_rewrite_host_pip_denies_without_uv_or_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_uv(monkeypatch)
    env = detect_project_env(tmp_path)
    outcome = rewrite_for_project_env(
        parse_cli_command("pip install pytest"), env, cwd=tmp_path
    )
    assert outcome == HOST_PIP_DENY_MESSAGE


# ---------------------------------------------------------------------------
# Locked clone runtime
# ---------------------------------------------------------------------------


def test_locked_clone_rewrites_pip_install_to_venv_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_uv(monkeypatch)
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    marker = real / ".venv" / "pip-argv.txt"
    _write_venv_tool(
        real,
        "pip",
        'printf "%s\\n" "$0" "$@" > .venv/pip-argv.txt\necho project-local-pip-ok',
    )

    result = execute_bm_cli(agent, state, "pip install pytest")
    assert result.ok is True, result.prompt_content
    assert result.approval_required is False
    assert result.executor == "shell"
    assert "project-local-pip-ok" in (result.prompt_content or "")
    assert marker.exists()
    recorded = marker.read_text(encoding="utf-8")
    assert "install" in recorded
    assert "pytest" in recorded
    assert db.list_cli_approval_requests(status="pending") == []


def test_locked_clone_rewrites_editable_pip_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_uv(monkeypatch)
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _write_venv_tool(
        real,
        "pip",
        'printf "%s\\n" "$0" "$@" > .venv/pip-argv.txt\necho project-local-editable-ok',
    )

    result = execute_bm_cli(agent, state, 'pip install -e ".[dev]"')
    assert result.ok is True, result.prompt_content
    assert result.approval_required is False
    recorded = (real / ".venv" / "pip-argv.txt").read_text(encoding="utf-8")
    assert "install" in recorded
    assert "-e" in recorded
    assert ".[dev]" in recorded


def test_locked_clone_venv_pytest_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_uv(monkeypatch)
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _write_venv_tool(real, "pytest", "echo venv-pytest-ok")

    rewritten = execute_bm_cli(agent, state, "pytest -q")
    assert rewritten.ok is True, rewritten.prompt_content
    assert "venv-pytest-ok" in (rewritten.prompt_content or "")

    direct = execute_bm_cli(agent, state, ".venv/bin/pytest -q")
    assert direct.ok is True, direct.prompt_content
    assert "venv-pytest-ok" in (direct.prompt_content or "")


def test_uv_run_pytest_is_allowed_on_locked_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    captured: dict[str, str] = {}

    def _fake_shell(command: str, **kwargs: object) -> object:
        from core.bm_cli.shell_executor import ShellExecutionResult

        captured["command"] = command
        return ShellExecutionResult(
            exit_code=0,
            stdout="uv-run-pytest-ok\n",
            stderr="",
            timed_out=False,
            duration_ms=1,
        )

    monkeypatch.setattr("core.bm_cli.runtime.execute_shell_command", _fake_shell)
    result = execute_bm_cli(agent, state, "uv run pytest -q")
    assert result.ok is True, result.prompt_content
    assert result.approval_required is False
    assert captured.get("command") == "uv run pytest -q"
    assert "uv-run-pytest-ok" in (result.prompt_content or "")


def test_locked_clone_denies_host_pip_without_project_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_uv(monkeypatch)
    agent, state, _dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)

    result = execute_bm_cli(agent, state, "pip install pytest")
    assert result.ok is False
    assert result.approval_required is False
    assert "Host pip is blocked" in (result.detail or "")
    assert db.list_cli_approval_requests(status="pending") == []

    approved = execute_approved_command(
        agent,
        state,
        "pip install pytest",
        approval_request_id="approved-host-pip",
    )
    assert approved.ok is False
    assert approved.approval_required is False
    assert "Host pip is blocked" in (approved.detail or "")


def test_locked_clone_denies_uv_pip_system_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, state, _dest, real = _lock_and_cd_clone(tmp_path, monkeypatch)
    _write_venv_tool(real, "pip", "echo should-not-run")
    result = execute_bm_cli(agent, state, "uv pip install --system pytest")
    assert result.ok is False
    assert result.approval_required is False
    assert "Host pip is blocked" in (result.detail or "")


def test_desk_pip_install_still_requires_approval_without_lock() -> None:
    from core.bm_cli.session import get_cli_cwd
    from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo

    _enable_shell()
    agent, state = _agent_and_state()
    # Artifacts live inside this checkout's .git; /me is still not a locked clone.
    assert cwd_is_nested_clone_repo(agent, get_cli_cwd(agent.id)) is False
    paused = execute_bm_cli(agent, state, "pip install pytest")
    assert paused.ok is False
    assert paused.approval_required is True
    assert paused.kind == "approval_required"
    assert paused.command == "pip install pytest"
