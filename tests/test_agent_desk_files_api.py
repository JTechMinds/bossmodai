"""Desk Save and raw bytes: PUT /agents/{id}/desk and GET /agents/{id}/desk/raw.

The shared file viewer reads a desk file through the agent's namespace, so its
Save and image preview must go back through that namespace too. These routes
resolve agent-virtual paths (``/me``, ``/projects``) with the CLI's own jail.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli import filesystem
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.floor_roots import floor_root

# The smallest valid PNG header bytes are enough: the route serves bytes, it
# does not decode them.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep personal storage and the company root inside this test's tmp dir."""
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


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _me_dir(agent) -> Path:
    path = agent_artifact_dir(agent.storage_key)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─── Save ───


def test_desk_save_writes_me_file_and_commits() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    target = _me_dir(agent) / "task_instructions.md"
    target.write_text("old\n", encoding="utf-8")

    res = _client().put(
        f"/api/agents/{agent.id}/desk",
        json={"path": "/me/task_instructions.md", "content": "new text"},
        headers=_auth_headers(),
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["path"] == "/me/task_instructions.md"
    assert body["commit_sha"]
    # write_virtual_text normalizes to exactly one trailing newline.
    assert target.read_text(encoding="utf-8") == "new text\n"


def test_desk_save_writes_projects_file_under_floor_folder() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    project_dir = floor_root(agent.floor_id) / "game"
    project_dir.mkdir(parents=True)
    target = project_dir / "design.md"
    target.write_text("plan\n", encoding="utf-8")

    res = _client().put(
        f"/api/agents/{agent.id}/desk",
        json={"path": "/projects/game/design.md", "content": "revised plan\n"},
        headers=_auth_headers(),
    )

    assert res.status_code == 200, res.text
    assert res.json()["path"] == "/projects/game/design.md"
    assert target.read_text(encoding="utf-8") == "revised plan\n"


def test_desk_save_missing_file_is_404_and_creates_nothing() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    target = _me_dir(agent) / "never-created.md"

    res = _client().put(
        f"/api/agents/{agent.id}/desk",
        json={"path": "/me/never-created.md", "content": "x"},
        headers=_auth_headers(),
    )

    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "File not found"
    assert target.exists() is False


def test_desk_save_outside_roots_is_400() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    res = _client().put(
        f"/api/agents/{agent.id}/desk",
        json={"path": "/etc/passwd", "content": "x"},
        headers=_auth_headers(),
    )

    assert res.status_code == 400, res.text


def test_desk_save_unknown_agent_is_404() -> None:
    res = _client().put(
        "/api/agents/no-such-agent/desk",
        json={"path": "/me/x.md", "content": "x"},
        headers=_auth_headers(),
    )

    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "Agent not found"


def test_desk_save_over_cli_write_cap_is_400() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    target = _me_dir(agent) / "big.md"
    target.write_text("small\n", encoding="utf-8")
    limit = config.get_int("cli_max_write_bytes")
    assert limit is not None and limit > 0

    res = _client().put(
        f"/api/agents/{agent.id}/desk",
        json={"path": "/me/big.md", "content": "x" * (limit + 1)},
        headers=_auth_headers(),
    )

    assert res.status_code == 400, res.text
    assert "maximum write size" in res.json()["detail"]
    assert target.read_text(encoding="utf-8") == "small\n"


# ─── Raw ───


def test_desk_raw_returns_image_bytes_with_mime() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    (_me_dir(agent) / "pic.png").write_bytes(PNG_BYTES)

    res = _client().get(
        f"/api/agents/{agent.id}/desk/raw",
        params={"path": "/me/pic.png"},
        headers=_auth_headers(),
    )

    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "image/png"
    assert res.content == PNG_BYTES


def test_desk_raw_directory_is_400() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    (_me_dir(agent) / "reports").mkdir()

    res = _client().get(
        f"/api/agents/{agent.id}/desk/raw",
        params={"path": "/me/reports"},
        headers=_auth_headers(),
    )

    assert res.status_code == 400, res.text
    assert res.json()["detail"] == "Path is not a file"


def test_desk_raw_missing_file_is_404() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    res = _client().get(
        f"/api/agents/{agent.id}/desk/raw",
        params={"path": "/me/missing.png"},
        headers=_auth_headers(),
    )

    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "File not found"


def test_desk_raw_outside_roots_is_400() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    res = _client().get(
        f"/api/agents/{agent.id}/desk/raw",
        params={"path": "/etc/passwd"},
        headers=_auth_headers(),
    )

    assert res.status_code == 400, res.text
