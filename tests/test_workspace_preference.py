"""Workspace preference consent: clone / branch / edit-host / cancel."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.filesystem import agent_artifact_dir
from core.models.host_path_consent import (
    WORKSPACE_PREFERENCE_BODY,
    WORKSPACE_PREFERENCE_KIND,
    WORKSPACE_PREFERENCE_TITLE,
)
from core.runtime import runtime_services


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


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


def _agent_and_state():
    agent = db.create_agent("Path Clerk", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _allow_host(host: Path) -> None:
    db.set_setting("workspace_host_roots", str(host.resolve()), "cli_policy")
    config.reload()


def test_card_fires_on_named_host_path_write(tmp_path: Path) -> None:
    host = tmp_path / "named-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("original\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="changed\n")
    assert paused.ok is False
    assert paused.consent_required is True
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card["kind"] == WORKSPACE_PREFERENCE_KIND
    assert card["title"] == WORKSPACE_PREFERENCE_TITLE
    assert card.get("body") == WORKSPACE_PREFERENCE_BODY
    assert card.get("git") is False
    assert fixture.read_text(encoding="utf-8") == "original\n"

    read = execute_bm_cli(agent, state, f"cat {fixture}")
    assert read.ok is True
    assert "original" in read.prompt_content


def test_cancel_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "cancel-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("keep\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="nope\n")
    request_id = paused.consent_request_id
    assert request_id
    cancelled = client.post(f"/api/workspace-preference/{request_id}/cancel", headers=_headers())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "denied"
    assert fixture.read_text(encoding="utf-8") == "keep\n"

    again = execute_bm_cli(agent, state, f"write {fixture}", content="still nope\n")
    assert again.ok is False
    assert again.consent_required is False
    assert "blocked" in (again.detail or "").lower() or "cancelled" in (again.detail or "").lower()
    assert fixture.read_text(encoding="utf-8") == "keep\n"


def test_edit_host_requires_explicit_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "edit-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("before\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="after\n")
    assert paused.consent_required is True
    assert fixture.read_text(encoding="utf-8") == "before\n"

    request_id = paused.consent_request_id
    edited = client.post(f"/api/workspace-preference/{request_id}/edit-host", headers=_headers())
    assert edited.status_code == 200
    assert edited.json()["status"] == "edit_host"

    allowed = execute_bm_cli(agent, state, f"write {fixture}", content="after\n")
    assert allowed.ok is True
    assert fixture.read_text(encoding="utf-8") == "after\n"


def test_non_git_hides_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "plain-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("x\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="y\n")
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card.get("git") is False
    request_id = paused.consent_request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 409
    assert fixture.read_text(encoding="utf-8") == "x\n"
    pending = db.get_consent_request(request_id or "")
    assert pending is not None
    assert pending.status == "pending"


def test_git_repo_offers_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "git-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("git\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=host, check=True, capture_output=True)
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="changed\n")
    card = (paused.data or {}).get("host_path_consent") or {}
    assert card.get("git") is True
    request_id = paused.consent_request_id
    branched = client.post(f"/api/workspace-preference/{request_id}/branch", headers=_headers())
    assert branched.status_code == 200, branched.text
    body = branched.json()
    assert body["status"] == "branched"
    assert body.get("clone_dest", "").startswith("/me/")
    assert fixture.read_text(encoding="utf-8") == "git\n"

    blocked = execute_bm_cli(agent, state, f"write {fixture}", content="host write\n")
    assert blocked.ok is False
    assert fixture.read_text(encoding="utf-8") == "git\n"


def test_clone_into_workspace_does_not_write_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "clone-root"
    host.mkdir()
    fixture = host / "note.txt"
    fixture.write_text("keep host\n", encoding="utf-8")
    _allow_host(host)
    agent, state = _agent_and_state()
    client = _api_client(monkeypatch)

    paused = execute_bm_cli(agent, state, f"write {fixture}", content="changed\n")
    request_id = paused.consent_request_id
    cloned = client.post(f"/api/workspace-preference/{request_id}/clone", headers=_headers())
    assert cloned.status_code == 200, cloned.text
    body = cloned.json()
    assert body["status"] == "cloned"
    dest = body.get("clone_dest") or ""
    assert dest.startswith("/me/host-work/")
    assert fixture.read_text(encoding="utf-8") == "keep host\n"

    copy = agent_artifact_dir(agent.storage_key) / "host-work" / Path(dest).name
    assert copy.exists()
    copied = copy.read_text(encoding="utf-8") if copy.is_file() else (copy / "note.txt").read_text(encoding="utf-8")
    assert copied == "keep host\n"

    blocked = execute_bm_cli(agent, state, f"write {fixture}", content="host write\n")
    assert blocked.ok is False
    assert fixture.read_text(encoding="utf-8") == "keep host\n"


def test_desk_me_write_does_not_open_workspace_card() -> None:
    agent, state = _agent_and_state()
    result = execute_bm_cli(agent, state, "write /me/note.txt", content="desk\n")
    assert result.consent_required is False
    assert result.ok is True
