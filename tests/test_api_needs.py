"""GET /api/needs aggregates every operator decision into one shape."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, ensure_local_api_token, install_local_api_auth
from api.routes import router
from core import config

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_KEYS = {
    "id", "kind", "agent_id", "agent_name", "title", "sub",
    "created_at", "conversation_id", "actions",
}
VALID_KINDS = {"consent", "approval", "blocked", "error"}


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


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: ensure_local_api_token()}


def test_needs_is_empty_list_when_nothing_pending(client: TestClient) -> None:
    """Nothing pending is an empty list, not an error and not null."""
    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    assert res.json() == []


def test_needs_requires_auth(client: TestClient) -> None:
    res = client.get("/api/needs")
    assert res.status_code == 401


def test_blocked_task_surfaces_as_a_need(client: TestClient) -> None:
    agent = db.create_agent(name="Jim", role="Senior software engineer")
    task = db.create_task(
        title="Implement inline command suppression",
        assigned_to=agent.id,
        requester_id=agent.id,
    )
    db.update_task(task.id, status="blocked")

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    needs = res.json()

    blocked = [n for n in needs if n["kind"] == "blocked"]
    assert len(blocked) == 1, f"expected one blocked need, got {needs}"
    item = blocked[0]
    assert REQUIRED_KEYS <= set(item), f"missing keys: {REQUIRED_KEYS - set(item)}"
    assert item["agent_name"] == "Jim", "agent id must be resolved to a name"
    assert item["kind"] in VALID_KINDS
    assert isinstance(item["actions"], list) and item["actions"], "a need must be actionable"
    for action in item["actions"]:
        assert {"label", "href", "method"} <= set(action)
        assert action["method"] in {"GET", "POST"}


def test_needs_respects_limit(client: TestClient) -> None:
    agent = db.create_agent(name="Jim", role="Engineer")
    for i in range(5):
        task = db.create_task(title=f"t{i}", assigned_to=agent.id, requester_id=agent.id)
        db.update_task(task.id, status="blocked")

    res = client.get("/api/needs?limit=2", headers=_auth())
    assert res.status_code == 200, res.text
    assert len(res.json()) == 2


def test_route_is_registered_in_the_public_table() -> None:
    """The route table snapshot must know about this endpoint."""
    from tests.test_route_split import EXPECTED_ROUTES
    assert (("GET",), "/api/needs", "list_needs") in EXPECTED_ROUTES


def test_activity_trigger_names_still_exist_in_the_engine() -> None:
    """A renamed engine event must fail a test, not silently stale the queue.

    Spec 5.4 and 13. The constant lives in needs/need-shape.js, the pure half
    of the needs store; the client refreshes on these names and on nothing
    else, so a rename in core/ would leave a newly raised consent invisible
    until the next reconnect.
    """
    import re
    js = (ROOT / "ui" / "static" / "js" / "needs" / "need-shape.js").read_text(encoding="utf-8")
    block = js.split("ACTIVITY_TRIGGERS = Object.freeze([", 1)[1].split("]);", 1)[0]
    names = re.findall(r"'([a-z_]+)'", block)
    assert names, "the trigger constant must not be empty"
    haystack = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("core", "api")
        for path in (ROOT / folder).rglob("*.py")
    )
    missing = [n for n in names if f'"{n}"' not in haystack and f"'{n}'" not in haystack]
    assert not missing, f"needs triggers no engine event emits: {missing}"
