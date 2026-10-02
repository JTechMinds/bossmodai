"""Identical pending CLI approvals reuse one card; shell results echo the asked form.

A re-ask of a command the operator has not decided yet pauses on the
existing card: no second row, no second card, no second System AI review.
Shell results name the command in the agent's /me and /projects world,
while the audit row keeps the real path that ran.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.work_binding import bind_turn
from core.bm_cli.floor_roots import project_dir
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.runtime import execute_approved_command, execute_bm_cli
from core.bm_cli.session import set_cli_cwd
from db.crud import execute
from db.floors import LOBBY_ID

_COMPLETE = "core.bm_cli.approval_gate.review.complete_text"
_CHROME = "core.agent_loop.notifications.ensure_cli_approval_chrome"
_COMMAND = "rm notes.txt"
_DETACHED = {"type": "extension_event", "source_channel": "system", "task_id": None}


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
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _agent_and_state():
    agent = db.create_agent("Dedupe Clerk", role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _thread(agent_id: str, *, auto_approve: bool = False):
    channel = db.create_channel(name="Ops", member_agent_ids=[agent_id])
    if auto_approve:
        channel = db.update_channel(channel.id, cli_auto_approve=True)
    assert channel is not None
    return channel


def _project_file(name: str = "demo") -> Path:
    # New agents live in Lobby, so their /projects is Lobby's folder.
    root = project_dir(LOBBY_ID, name)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "notes.txt"
    path.write_text("keep", encoding="utf-8")
    return path


def _count_chrome(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every card post while still posting it for real."""
    from core.agent_loop import notifications

    real = notifications.ensure_cli_approval_chrome
    posted: list[str] = []

    def _recording(agent, approval, **kwargs):
        posted.append(approval.id)
        return real(agent, approval, **kwargs)

    monkeypatch.setattr(_CHROME, _recording)
    return posted


def _ask_once(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """The gate answers "ask" once; a second review fails the test."""
    seen: list[object] = []

    def _complete(messages):
        if seen:
            raise AssertionError("System AI must not review a pending twin again")
        seen.append(messages)
        return json.dumps({"decision": "ask", "basis": "unsure", "why": "operator should look"})

    monkeypatch.setattr(_COMPLETE, _complete)
    return seen


def _pending(agent_id: str) -> list:
    return db.list_cli_approval_requests(status="pending", agent_id=agent_id)


def test_pending_twin_reuses_the_card_without_review(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent_and_state()
    channel = _thread(agent.id, auto_approve=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    reviews = _ask_once(monkeypatch)
    posted = _count_chrome(monkeypatch)

    first = execute_bm_cli(agent, state, _COMMAND, channel_id=channel.id)
    second = execute_bm_cli(agent, state, _COMMAND, channel_id=channel.id)

    assert first.approval_required is True
    assert second.approval_required is True
    assert second.approval_request_id == first.approval_request_id
    assert len(reviews) == 1
    assert posted == [first.approval_request_id]
    assert [row.id for row in _pending(agent.id)] == [first.approval_request_id]
    assert (second.data or {}).get("reused_pending") is True
    assert "reused_pending" not in (first.data or {})
    assert notes.read_text(encoding="utf-8") == "keep"
    asks = [
        row for row in db.list_bm_cli_events(agent_id=agent.id)
        if row["decision"] == "approval_required"
    ]
    assert [row["approval_request_id"] for row in asks] == [first.approval_request_id] * 2


def test_twin_in_another_thread_gets_its_own_card(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent_and_state()
    one = _thread(agent.id)
    two = _thread(agent.id)
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    posted = _count_chrome(monkeypatch)

    first = execute_bm_cli(agent, state, _COMMAND, channel_id=one.id)
    second = execute_bm_cli(agent, state, _COMMAND, channel_id=two.id)

    assert first.approval_request_id != second.approval_request_id
    assert posted == [first.approval_request_id, second.approval_request_id]
    assert len(_pending(agent.id)) == 2
    stored = db.get_cli_approval_request(second.approval_request_id)
    assert stored is not None
    assert stored.channel_id == two.id


def test_twin_from_another_cwd_gets_its_own_card() -> None:
    agent, state = _agent_and_state()
    _project_file("demo")
    _project_file("other")

    set_cli_cwd(agent.id, "/projects/demo")
    first = execute_bm_cli(agent, state, _COMMAND)
    set_cli_cwd(agent.id, "/projects/other")
    second = execute_bm_cli(agent, state, _COMMAND)

    assert first.approval_required is True
    assert second.approval_required is True
    assert first.approval_request_id != second.approval_request_id
    assert len(_pending(agent.id)) == 2


def test_expired_twin_is_not_reused() -> None:
    agent, state = _agent_and_state()
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")

    first = execute_bm_cli(agent, state, _COMMAND)
    assert first.approval_request_id is not None
    # Past its expiry but not yet swept by expire_stale_requests.
    execute(
        "UPDATE cli_approval_requests SET expires_at = $1 WHERE id = $2",
        [datetime.now(timezone.utc) - timedelta(minutes=1), first.approval_request_id],
    )
    second = execute_bm_cli(agent, state, _COMMAND)

    assert second.approval_required is True
    assert second.approval_request_id != first.approval_request_id
    assert "reused_pending" not in (second.data or {})


def test_decided_twin_is_not_reused() -> None:
    agent, state = _agent_and_state()
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")

    first = execute_bm_cli(agent, state, _COMMAND)
    assert first.approval_request_id is not None
    assert db.reject_cli_approval_request(first.approval_request_id) is not None
    second = execute_bm_cli(agent, state, _COMMAND)

    assert second.approval_required is True
    assert second.approval_request_id != first.approval_request_id
    assert [row.id for row in _pending(agent.id)] == [second.approval_request_id]


def test_detached_and_attached_twins_get_their_own_cards() -> None:
    agent, state = _agent_and_state()
    _project_file()
    set_cli_cwd(agent.id, "/projects/demo")

    attached = execute_bm_cli(agent, state, _COMMAND)
    with bind_turn(agent.id, _DETACHED):
        detached = execute_bm_cli(agent, state, _COMMAND)
        detached_again = execute_bm_cli(agent, state, _COMMAND)

    assert attached.approval_request_id != detached.approval_request_id
    assert detached_again.approval_request_id == detached.approval_request_id
    stored = db.get_cli_approval_request(detached.approval_request_id)
    assert stored is not None
    assert stored.detached_origin is True
    assert len(_pending(agent.id)) == 2


def test_compound_script_twin_is_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, state = _agent_and_state()
    channel = _thread(agent.id, auto_approve=True)
    notes = _project_file()
    set_cli_cwd(agent.id, "/projects/demo")
    reviews = _ask_once(monkeypatch)
    posted = _count_chrome(monkeypatch)
    script = "rm notes.txt && echo removed"

    first = execute_bm_cli(agent, state, script, channel_id=channel.id)
    second = execute_bm_cli(agent, state, script, channel_id=channel.id)

    assert first.approval_required is True
    assert second.approval_request_id == first.approval_request_id
    assert (second.data or {}).get("reused_pending") is True
    assert len(reviews) == 1
    assert posted == [first.approval_request_id]
    assert [row.command for row in _pending(agent.id)] == [script]
    assert notes.read_text(encoding="utf-8") == "keep"


def test_approved_result_echoes_the_submitted_projects_form() -> None:
    agent, state = _agent_and_state()
    notes = _project_file()
    real = str(notes)
    command = "head -n 1 /projects/demo/notes.txt"
    request = db.create_cli_approval_request(agent_id=agent.id, command=command, cwd="/me")
    assert db.approve_cli_approval_request(request.id) is not None

    result = execute_approved_command(
        agent, state, command, approval_request_id=request.id, cwd="/me",
    )

    assert result.ok is True, result.prompt_content
    assert result.kind == "shell"
    assert result.command == command
    assert f"command: {command}" in result.prompt_content
    assert "keep" in result.prompt_content
    assert real not in result.prompt_content
    audit = db.list_bm_cli_events(agent_id=agent.id)[0]
    assert audit["approval_request_id"] == request.id
    assert audit["command"] == f"head -n 1 {real}"


def test_always_allowed_shell_result_echoes_the_submitted_projects_form() -> None:
    agent, state = _agent_and_state()
    notes = _project_file()
    real = str(notes)
    set_cli_cwd(agent.id, "/projects/demo")
    command = "nl -ba /projects/demo/notes.txt"

    result = execute_bm_cli(agent, state, command)

    assert result.ok is True, result.prompt_content
    assert result.kind == "shell"
    assert result.command == command
    assert f"command: {command}" in result.prompt_content
    assert "keep" in result.prompt_content
    assert real not in result.prompt_content
    audit = db.list_bm_cli_events(agent_id=agent.id)[0]
    assert audit["decision"] == "allowed"
    assert audit["command"] == f"nl -ba {real}"
