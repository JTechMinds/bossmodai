"""The desk's memory routes: GET /agents/{id}/memory and DELETE /agents/{id}/memory/{n}.

The store is system-owned and outside every agent path, so these are the
operator's only way to see and prune what an agent is shown every turn.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.standing_prefs import add_memory, list_memories, standing_prefs_file
from core.bm_cli import filesystem


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    monkeypatch.setattr(filesystem, "_SYSTEM_ROOT", tmp_path / "system")
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
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _agent():
    return db.create_agent("Ada", role="Writer")


# ─── GET ───


def test_get_lists_the_memories_in_store_order() -> None:
    agent = _agent()
    add_memory(agent.storage_key, "The boss wants plain English.")
    add_memory(agent.storage_key, "Acme's contact is Dana Lee.")
    response = _client().get(f"/api/agents/{agent.id}/memory")
    assert response.status_code == 200
    assert response.json() == {
        "memories": [
            {"id": 1, "text": "The boss wants plain English."},
            {"id": 2, "text": "Acme's contact is Dana Lee."},
        ]
    }


def test_get_is_an_empty_list_when_nothing_was_saved() -> None:
    agent = _agent()
    response = _client().get(f"/api/agents/{agent.id}/memory")
    assert response.status_code == 200
    assert response.json() == {"memories": []}


def test_get_of_an_unknown_agent_is_404() -> None:
    response = _client().get("/api/agents/nope/memory")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"


def test_get_of_a_corrupt_store_is_500_with_the_store_sentence(caplog: pytest.LogCaptureFixture) -> None:
    agent = _agent()
    standing_prefs_file(agent.storage_key).write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="api.routes.agents"):
        response = _client().get(f"/api/agents/{agent.id}/memory")
    assert response.status_code == 500
    assert response.json()["detail"].startswith("memory store is unreadable: ")
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and agent.id in errors[0]


# ─── DELETE ───


def test_delete_removes_the_memory_and_its_number_is_not_reused(caplog: pytest.LogCaptureFixture) -> None:
    agent = _agent()
    add_memory(agent.storage_key, "one")
    add_memory(agent.storage_key, "a private sentence")
    with caplog.at_level(logging.INFO, logger="api.routes.agents"):
        response = _client().delete(f"/api/agents/{agent.id}/memory/2")
    assert response.status_code == 200
    assert response.json() == {"removed": 2}
    assert [(item.id, item.text) for item in list_memories(agent.storage_key)] == [(1, "one")]
    assert add_memory(agent.storage_key, "three").id == 3
    infos = [r.getMessage() for r in caplog.records if r.name == "api.routes.agents"]
    assert len(infos) == 1
    assert agent.id in infos[0] and "#2" in infos[0]
    # The text is the agent's memory, never the log's.
    assert "a private sentence" not in infos[0]


def test_delete_of_an_unknown_agent_is_404() -> None:
    response = _client().delete("/api/agents/nope/memory/1")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"


def test_delete_of_a_missing_memory_is_404_with_the_store_sentence() -> None:
    agent = _agent()
    add_memory(agent.storage_key, "one")
    response = _client().delete(f"/api/agents/{agent.id}/memory/7")
    assert response.status_code == 404
    assert response.json()["detail"] == "no memory #7"
    assert [item.id for item in list_memories(agent.storage_key)] == [1]


def test_delete_of_a_corrupt_store_is_500_and_leaves_it() -> None:
    agent = _agent()
    path = standing_prefs_file(agent.storage_key)
    path.write_text("{not json", encoding="utf-8")
    response = _client().delete(f"/api/agents/{agent.id}/memory/1")
    assert response.status_code == 500
    assert response.json()["detail"] == "memory store is unreadable; refusing to overwrite"
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("memory_id", ["0", "-1", "two"])
def test_delete_refuses_a_number_that_is_not_positive(memory_id: str) -> None:
    agent = _agent()
    add_memory(agent.storage_key, "one")
    response = _client().delete(f"/api/agents/{agent.id}/memory/{memory_id}")
    assert response.status_code == 422
    assert [item.id for item in list_memories(agent.storage_key)] == [1]
