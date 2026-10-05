"""The ``agent_updated`` activity names its agent by id, and the desk matches on it.

World updates are coalesced (Phase 3), so the activity for an edit now lands
before the roster that carries the edit. A rename therefore cannot be
recognised by name: the desk's roster still holds the old one. The desk's
pack line matches the activity by ``agent_id`` instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config

DESK_PANEL = Path(__file__).resolve().parents[1] / "ui" / "static" / "js" / "context" / "desk-panel.js"


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


def test_a_rename_broadcasts_agent_updated_with_the_agent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    sent: list[dict[str, Any]] = []

    async def record(event: str, detail: str, agent_name: str | None = None, extra: dict | None = None) -> None:
        sent.append({"event": event, "agent_name": agent_name, **(extra or {})})

    monkeypatch.setattr("api.routes.agents.manager.broadcast_activity", record)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    res = TestClient(app).patch(
        f"/api/agents/{agent.id}",
        headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
        json={"name": "Ada Renamed"},
    )

    assert res.status_code == 200, res.text
    updated = [item for item in sent if item["event"] == "agent_updated"]
    assert updated == [{"event": "agent_updated", "agent_name": "Ada Renamed", "agent_id": agent.id}]


def test_the_desk_pack_line_matches_agent_updated_by_id_not_name() -> None:
    source = DESK_PANEL.read_text(encoding="utf-8")
    handler = source.split("bus.subscribe('activity', (entry) => {", 1)[1].split("}));", 1)[0]
    assert "entry.agent_id === agentId" in handler
    assert "agent_name" not in handler
