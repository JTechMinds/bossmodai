"""Locked-clone shared shell outcome: allow / rewrite / approval / Blocked."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.locked_clone_outcome import (
    DEFAULT_APPROVAL_MESSAGE,
    HOST_OUTSIDE_NEST_WHY,
    decide_locked_clone_shell_outcome,
    is_virtual_cli_path,
    rewrite_virtual_shell_paths,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import VIRTUAL_COMMANDS, execute_bm_cli, preview_bm_cli
from tests.test_project_env import _lock_and_cd_clone


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
    assert "Blocked" in (bash.detail or "")
    assert dest.startswith("/me/host-work/")


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
    assert "Blocked" in (blocked.detail or "")
    assert "outside the locked clone" in (blocked.detail or "")
    assert host_file.read_text(encoding="utf-8") == "keep\n"
    assert db.list_cli_approval_requests(status="pending") == []


def test_decide_outcome_is_table_driven_not_sed_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _state, dest, _real = _lock_and_cd_clone(tmp_path, monkeypatch)
    from core.agent_loop.activity_runtime import get_active_task_id

    task_id = get_active_task_id(agent.id)
    sed = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command("sed -i s/a/b/ tests/test_ok.py"),
        dest,
        task_id=task_id,
        virtual_commands=VIRTUAL_COMMANDS,
    )
    assert sed is not None
    assert sed.kind == "approval_required"
    assert DEFAULT_APPROVAL_MESSAGE in (sed.message or "")

    awk = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command("awk '{print}' tests/test_ok.py"),
        dest,
        task_id=task_id,
        virtual_commands=VIRTUAL_COMMANDS,
    )
    assert awk is not None
    assert awk.kind == "approval_required"

    echo = decide_locked_clone_shell_outcome(
        agent,
        parse_cli_command("echo nest-ok"),
        dest,
        task_id=task_id,
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


def test_host_outside_nest_why_constant() -> None:
    assert HOST_OUTSIDE_NEST_WHY.startswith("Blocked")
    assert "enablement" in HOST_OUTSIDE_NEST_WHY
