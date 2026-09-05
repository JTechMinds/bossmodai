"""HA-SEC-P1-06 — CLI simulator defaults to dry-run."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.filesystem import agent_artifact_dir


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


def _probe_path(agent) -> Path:
    return agent_artifact_dir(agent.storage_key) / "sim-dry-run-probe.md"


def test_simulator_default_post_does_not_create_files() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    probe = _probe_path(agent)
    if probe.exists():
        probe.unlink()

    client = _client()
    res = client.post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={
            "command": "write /me/sim-dry-run-probe.md",
            "agent_id": agent.id,
            "content": "should not land",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is True
    assert body["ok"] is True
    assert body["kind"] == "dry_run"
    assert not probe.exists()


def test_simulator_explicit_execute_writes_file() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    probe = _probe_path(agent)
    if probe.exists():
        probe.unlink()

    client = _client()
    res = client.post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={
            "command": "write /me/sim-dry-run-probe.md",
            "agent_id": agent.id,
            "content": "wrote for real",
            "execute": True,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is False
    assert body["ok"] is True
    assert body["kind"] == "write"
    assert probe.exists()
    assert "wrote for real" in probe.read_text(encoding="utf-8")
    probe.unlink(missing_ok=True)


def test_simulator_dry_run_false_is_explicit_execute() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    probe = _probe_path(agent)
    if probe.exists():
        probe.unlink()

    client = _client()
    res = client.post(
        "/api/cli-policy/simulator/execute",
        headers=_auth_headers(),
        json={
            "command": "write /me/sim-dry-run-probe.md",
            "agent_id": agent.id,
            "content": "via dry_run false",
            "dry_run": False,
        },
    )

    assert res.status_code == 200
    assert res.json()["dry_run"] is False
    assert probe.exists()
    probe.unlink(missing_ok=True)
