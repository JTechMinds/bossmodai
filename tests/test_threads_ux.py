"""Threads chrome: reuse the same active roster and archive instead of minting dupes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.models.message import HUMAN_SENDER_ID


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


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _three_agents() -> tuple[str, str, str]:
    debrah = db.create_agent("Debrah", role="PM")
    jim = db.create_agent("Jim", role="Eng")
    joey = db.create_agent("Joey", role="Writer")
    return debrah.id, jim.id, joey.id


def test_create_reuses_active_thread_with_same_roster() -> None:
    client = _api_client()
    agent_ids = list(_three_agents())
    first = client.post("/api/channels", headers=_headers(), json={"agent_ids": agent_ids})
    assert first.status_code == 201
    created = first.json()
    assert created["reused"] is False
    assert created["name"] == "Debrah, Jim, Joey"

    second = client.post("/api/channels", headers=_headers(), json={"agent_ids": list(reversed(agent_ids))})
    assert second.status_code == 200
    reused = second.json()
    assert reused["reused"] is True
    assert reused["id"] == created["id"]
    assert len(db.list_channels()) == 1


def test_archive_hides_thread_and_allows_a_fresh_roster() -> None:
    client = _api_client()
    agent_ids = list(_three_agents())
    created = client.post("/api/channels", headers=_headers(), json={"agent_ids": agent_ids}).json()
    archived = client.delete(f"/api/channels/{created['id']}", headers=_headers())
    assert archived.status_code == 200
    body = archived.json()
    assert body["status"] == "archived"
    assert body["archived_at"]
    assert db.list_channels() == []
    stored = db.get_channel(created["id"])
    assert stored is not None
    assert stored.status == "archived"

    again = client.post("/api/channels", headers=_headers(), json={"agent_ids": agent_ids})
    assert again.status_code == 201
    assert again.json()["id"] != created["id"]
    assert again.json()["reused"] is False
    assert len(db.list_channels()) == 1


def test_archive_route_matches_delete() -> None:
    client = _api_client()
    agent_ids = list(_three_agents())
    created = client.post("/api/channels", headers=_headers(), json={"agent_ids": agent_ids}).json()
    archived = client.post(f"/api/channels/{created['id']}/archive", headers=_headers())
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    listed = client.get("/api/channels", headers=_headers())
    assert listed.status_code == 200
    assert listed.json() == []


def test_find_active_channel_requires_exact_roster() -> None:
    debrah, jim, joey = _three_agents()
    full = db.create_channel(
        name="Debrah, Jim, Joey",
        member_agent_ids=[debrah, jim, joey],
        created_by=HUMAN_SENDER_ID,
    )
    assert db.find_active_channel_for_members([joey, debrah, jim]).id == full.id
    assert db.find_active_channel_for_members([debrah, jim]) is None
    pair = db.create_channel(
        name="Debrah, Jim",
        member_agent_ids=[debrah, jim],
        created_by=HUMAN_SENDER_ID,
    )
    assert db.find_active_channel_for_members([debrah, jim]).id == pair.id
    db.archive_channel(full.id)
    assert db.find_active_channel_for_members([debrah, jim, joey]) is None
