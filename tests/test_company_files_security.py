"""HA-SEC-P0-04 — Company files API is rooted at artifacts/projects."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.filesystem import (
    is_denied_company_file,
    normalize_company_relative_path,
    resolve_company_relative_path,
    resolve_relative_path,
)


BACKUP_NAME = "bossmod.sqlite3.20260101T000000Z.bak"
BACKUP_BYTES = b"SECRET-DB-BACKUP"
PROJECT_TEXT = "hello project"


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


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return app


def _client() -> TestClient:
    return TestClient(_app())


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _seed_artifact_tree(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    artifacts = tmp_path / "artifacts"
    projects = artifacts / "projects"
    backups = artifacts / "db_backups"
    agents = artifacts / "agents"
    project = projects / "alpha"
    project.mkdir(parents=True)
    backups.mkdir(parents=True)
    (agents / "agent_0001").mkdir(parents=True)

    notes = project / "notes.md"
    notes.write_text(PROJECT_TEXT, encoding="utf-8")
    backup = backups / BACKUP_NAME
    backup.write_bytes(BACKUP_BYTES)
    (agents / "agent_0001" / "secret.md").write_text("agent secret", encoding="utf-8")
    (project / "leak.bak").write_bytes(b"should not be served")

    monkeypatch.setattr("core.bm_cli.filesystem.company_files_root", lambda: projects)
    return {
        "artifacts": artifacts,
        "projects": projects,
        "backups": backups,
        "backup": backup,
        "notes": notes,
    }


def test_company_files_root_lists_projects_not_backups_or_agents(tmp_path, monkeypatch) -> None:
    _seed_artifact_tree(tmp_path, monkeypatch)
    client = _client()

    res = client.get("/api/company/files", params={"path": "/"}, headers=_auth_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "directory"
    names = [entry["name"] for entry in body["entries"]]
    assert "alpha" in names
    assert "db_backups" not in names
    assert "agents" not in names
    assert "projects" not in names


def test_company_files_raw_rejects_db_backup_path(tmp_path, monkeypatch) -> None:
    tree = _seed_artifact_tree(tmp_path, monkeypatch)
    client = _client()
    headers = _auth_headers()
    backup_path = f"/db_backups/{BACKUP_NAME}"

    res = client.get("/api/company/files/raw", params={"path": backup_path}, headers=headers)
    assert res.status_code in {400, 404}
    assert BACKUP_BYTES not in res.content

    traversal = client.get(
        "/api/company/files/raw",
        params={"path": f"/../db_backups/{BACKUP_NAME}"},
        headers=headers,
    )
    assert traversal.status_code in {400, 404}
    assert BACKUP_BYTES not in traversal.content
    assert tree["backup"].read_bytes() == BACKUP_BYTES


def test_company_files_cannot_delete_or_rename_backup(tmp_path, monkeypatch) -> None:
    tree = _seed_artifact_tree(tmp_path, monkeypatch)
    client = _client()
    headers = _auth_headers()
    backup_path = f"/db_backups/{BACKUP_NAME}"

    deleted = client.request(
        "DELETE",
        "/api/company/files",
        headers=headers,
        json={"path": backup_path},
    )
    assert deleted.status_code in {400, 404}
    assert tree["backup"].exists()
    assert tree["backup"].read_bytes() == BACKUP_BYTES

    renamed = client.patch(
        "/api/company/files/rename",
        headers=headers,
        json={"path": backup_path, "new_name": "stolen.md"},
    )
    assert renamed.status_code in {400, 404}
    assert tree["backup"].exists()
    assert not (tree["backups"] / "stolen.md").exists()


def test_company_files_can_open_and_edit_project_file(tmp_path, monkeypatch) -> None:
    tree = _seed_artifact_tree(tmp_path, monkeypatch)
    client = _client()
    headers = _auth_headers()

    opened = client.get("/api/company/files", params={"path": "/alpha/notes.md"}, headers=headers)
    assert opened.status_code == 200
    assert opened.json()["kind"] == "file"
    assert opened.json()["content"] == PROJECT_TEXT

    historical = client.get(
        "/api/company/files",
        params={"path": "/projects/alpha/notes.md"},
        headers=headers,
    )
    assert historical.status_code == 200
    assert historical.json()["content"] == PROJECT_TEXT

    saved = client.put(
        "/api/company/files",
        headers=headers,
        json={"path": "/alpha/notes.md", "content": "updated notes"},
    )
    assert saved.status_code == 200
    assert saved.json()["path"] == "/alpha/notes.md"
    assert tree["notes"].read_text(encoding="utf-8") == "updated notes"

    denied = client.get("/api/company/files", params={"path": "/alpha/leak.bak"}, headers=headers)
    assert denied.status_code in {400, 404}


def test_normalize_company_relative_path_strips_projects_prefix() -> None:
    assert normalize_company_relative_path("/") == "."
    assert normalize_company_relative_path("/projects") == "."
    assert normalize_company_relative_path("/projects/alpha/notes.md") == "alpha/notes.md"
    assert normalize_company_relative_path("alpha/notes.md") == "alpha/notes.md"


def test_resolve_company_relative_path_rejects_escape_and_backup_suffix(tmp_path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "alpha" / "notes.md").write_text("ok", encoding="utf-8")
    (tmp_path / "db_backups").mkdir()
    (tmp_path / "db_backups" / BACKUP_NAME).write_bytes(BACKUP_BYTES)

    resolved = resolve_company_relative_path(root, "/alpha/notes.md")
    assert resolved == (root / "alpha" / "notes.md").resolve()
    assert resolve_relative_path(root, "alpha/notes.md") == resolved

    with pytest.raises(ValueError):
        resolve_company_relative_path(root, f"/../db_backups/{BACKUP_NAME}")
    with pytest.raises(ValueError):
        resolve_company_relative_path(root, "/alpha/leak.bak")
    assert is_denied_company_file(Path(BACKUP_NAME))
    assert is_denied_company_file(Path("local.sqlite3"))
    assert is_denied_company_file(Path("cache.db"))
    assert not is_denied_company_file(Path("notes.md"))
