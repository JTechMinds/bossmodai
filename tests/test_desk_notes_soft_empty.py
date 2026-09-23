"""Desk GET /me/notes lists empty when never created; named missing files 404."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from api.routes._desk import _build_agent_desk_payload
from core import config
from core.bm_cli import filesystem
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.virtual_fs import (
    NOTES_FOLDER_PATH,
    is_notes_folder_path,
    is_soft_empty_virtual_directory,
    resolve_cli_path,
)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep personal storage out of the work tree so absence is actually absent."""
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(tmp_path / "company"))
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    db.close_connection()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return app


def _client() -> TestClient:
    return TestClient(_app())


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _notes_dir(agent) -> Path:
    return agent_artifact_dir(agent.storage_key) / "notes"


@pytest.mark.parametrize(
    "raw_path",
    ("/me/notes", "/me/notes/", "/me/./notes", "me/notes"),
)
def test_is_notes_folder_path_normalizes_equivalents(raw_path: str) -> None:
    assert is_notes_folder_path(raw_path) is True
    assert is_notes_folder_path("/me/notes/todo.md") is False
    assert is_notes_folder_path("/me/missing.md") is False


def test_soft_empty_is_only_the_notes_folder_when_absent() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    notes = resolve_cli_path(agent.storage_key, "/", "/me/notes")
    missing_file = resolve_cli_path(agent.storage_key, "/", "/me/missing.md")
    nested_note = resolve_cli_path(agent.storage_key, "/", "/me/notes/todo.md")

    assert notes.exists is False
    assert is_soft_empty_virtual_directory(notes) is True
    assert is_soft_empty_virtual_directory(missing_file) is False
    assert is_soft_empty_virtual_directory(nested_note) is False

    _notes_dir(agent).mkdir(parents=True)
    present = resolve_cli_path(agent.storage_key, "/", "/me/notes")
    assert present.exists is True
    assert is_soft_empty_virtual_directory(present) is False


def test_desk_get_notes_is_empty_directory_when_never_created() -> None:
    client = _client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    notes_dir = _notes_dir(agent)
    assert notes_dir.exists() is False

    res = client.get(
        f"/api/agents/{agent.id}/desk",
        params={"path": "/me/notes"},
        headers=_auth_headers(),
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["kind"] == "directory"
    assert payload["path"] == NOTES_FOLDER_PATH
    assert payload["entries"] == []
    assert notes_dir.exists() is False, "a probe must not create Notes"


@pytest.mark.parametrize(
    "path",
    ("/me/notes/", "/me/./notes", "me/notes"),
)
def test_desk_get_notes_normalized_equivalents_soft_empty(path: str) -> None:
    client = _client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    res = client.get(
        f"/api/agents/{agent.id}/desk",
        params={"path": path},
        headers=_auth_headers(),
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["kind"] == "directory"
    assert payload["path"] == NOTES_FOLDER_PATH
    assert payload["entries"] == []


def test_desk_get_named_missing_file_stays_404() -> None:
    client = _client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    res = client.get(
        f"/api/agents/{agent.id}/desk",
        params={"path": "/me/never-created.md"},
        headers=_auth_headers(),
    )
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "Path not found"


def test_desk_get_named_missing_note_file_stays_404() -> None:
    client = _client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    assert _notes_dir(agent).exists() is False

    res = client.get(
        f"/api/agents/{agent.id}/desk",
        params={"path": "/me/notes/todo.md"},
        headers=_auth_headers(),
    )
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "Path not found"


def test_desk_payload_builder_soft_empties_notes_without_creating() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    notes_dir = _notes_dir(agent)
    assert notes_dir.exists() is False

    payload = _build_agent_desk_payload(agent, "/me/notes")
    assert payload["kind"] == "directory"
    assert payload["path"] == NOTES_FOLDER_PATH
    assert payload["entries"] == []
    assert notes_dir.exists() is False

    with pytest.raises(HTTPException) as missing:
        _build_agent_desk_payload(agent, "/me/scratch-missing.md")
    assert missing.value.status_code == 404


def test_desk_get_notes_lists_existing_files() -> None:
    client = _client()
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    notes_dir = _notes_dir(agent)
    notes_dir.mkdir(parents=True)
    (notes_dir / "todo.md").write_text("ship it\n", encoding="utf-8")

    res = client.get(
        f"/api/agents/{agent.id}/desk",
        params={"path": "/me/notes"},
        headers=_auth_headers(),
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["kind"] == "directory"
    names = [entry["name"] for entry in payload["entries"]]
    assert names == ["todo.md"]
    assert payload["entries"][0]["path"] == "/me/notes/todo.md"
    assert payload["entries"][0]["category"] == "note"
