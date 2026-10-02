"""One live AI connection per agent, and a thinking level per routed activation.

``routing.resolve_route`` reads the agent's connection on every call, so the
request uses exactly what the Connections screen shows — a later edit to the
connection reaches the agent's next turn. The agent API refuses a connection
that cannot be routed and a level the connection does not offer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.llm import client, routing
from core.llm.routing import RouteUnavailable

_BASE = "http://127.0.0.1:9/v1"
_LEVELS = {
    "off": {"thinking": {"type": "disabled"}},
    "high": {"thinking": {"type": "enabled"}},
    "low": {"stream": False},
}


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _connection(**overrides):
    fields = {
        "name": "ZAI-Flash", "api_base_url": _BASE, "api_key": "sk-route",
        "model": "glm-flash", "extra_body": '{"top_k": 5}', "thinking_levels": _LEVELS,
    }
    fields.update(overrides)
    return db.create_connection(**fields)


# ─── resolve_route ───


def test_social_and_work_send_different_bodies_on_one_connection() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id, thinking_social="off", thinking_work="high")

    social = routing.resolve_route(agent, "social")
    work = routing.resolve_route(agent, "work")

    assert social.model == work.model == "glm-flash"
    assert social.api_base == work.api_base == _BASE
    assert social.api_key == work.api_key == "sk-route"
    assert social.connection_id == conn.id
    assert json.loads(social.extra_body) == {"top_k": 5, "thinking": {"type": "disabled"}}
    assert json.loads(work.extra_body) == {"top_k": 5, "thinking": {"type": "enabled"}}


def test_default_sends_the_connection_extra_body_unchanged() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id)
    assert routing.resolve_route(agent, "work").extra_body == '{"top_k": 5}'


def test_a_level_that_sets_stream_changes_streaming_for_that_activation_only() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id, thinking_social="low")
    social = json.loads(routing.resolve_route(agent, "social").extra_body)
    work = json.loads(routing.resolve_route(agent, "work").extra_body)
    assert client._streaming_disabled(social) is True
    assert client._streaming_disabled(work) is False


def test_a_connection_edit_is_visible_on_the_next_resolve() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id, thinking_work="high")
    assert routing.resolve_route(agent, "work").model == "glm-flash"

    db.update_connection(
        conn.id, model="glm-next", api_base_url="http://127.0.0.1:10/v1",
        thinking_levels={"high": {"thinking": {"type": "max"}}},
    )

    route = routing.resolve_route(agent, "work")
    assert route.model == "glm-next"
    assert route.api_base == "http://127.0.0.1:10/v1"
    assert json.loads(route.extra_body) == {"top_k": 5, "thinking": {"type": "max"}}
    assert routing.agent_model(agent) == "glm-next"


def test_an_unlinked_agent_is_unavailable_with_a_reason() -> None:
    agent = db.create_agent("Ada")
    with pytest.raises(RouteUnavailable) as caught:
        routing.resolve_route(agent, "work")
    assert caught.value.reason == "no AI connection"
    assert routing.agent_model(agent) is None


def test_a_deleted_connection_is_unavailable_with_a_reason() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id)
    db.delete_connection(conn.id)
    with pytest.raises(RouteUnavailable) as caught:
        routing.resolve_route(agent, "social")
    assert caught.value.reason == f"AI connection {conn.id} no longer exists"
    assert routing.agent_model(agent) is None


def test_a_blank_model_is_unavailable_with_a_reason() -> None:
    conn = _connection(model="  ")
    agent = db.create_agent("Ada", connection_id=conn.id)
    with pytest.raises(RouteUnavailable) as caught:
        routing.resolve_route(agent, "work")
    assert caught.value.reason == "AI connection 'ZAI-Flash' has no model"
    assert routing.agent_model(agent) is None


def test_a_level_the_connection_stopped_offering_is_unavailable_with_a_reason() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id, thinking_work="high")
    db.update_connection(conn.id, thinking_levels={})
    with pytest.raises(RouteUnavailable) as caught:
        routing.resolve_route(agent, "work")
    assert "work thinking" in caught.value.reason
    assert "'high' is not offered" in caught.value.reason
    # The other activation, at default, still routes.
    assert routing.resolve_route(agent, "social").model == "glm-flash"


def test_an_extra_body_that_is_not_an_object_is_unavailable_for_a_level() -> None:
    conn = _connection(extra_body="not json")
    agent = db.create_agent("Ada", connection_id=conn.id, thinking_social="off")
    with pytest.raises(RouteUnavailable, match="extra body is not valid JSON"):
        routing.resolve_route(agent, "social")


# ─── The agent API ───


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def test_create_links_the_connection_and_the_levels() -> None:
    conn = _connection()
    response = _client().post("/api/agents", json={
        "name": "Ada", "connection_id": conn.id, "thinking_social": "off", "thinking_work": "high",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["connection_id"], body["thinking_social"], body["thinking_work"]) == (conn.id, "off", "high")
    for gone in ("model_work", "api_base_url", "api_key", "extra_body"):
        assert gone not in body, gone


def test_create_requires_a_connection() -> None:
    response = _client().post("/api/agents", json={"name": "Ada"})
    assert response.status_code == 422
    assert "connection_id" in response.text


def test_create_refuses_a_missing_connection() -> None:
    response = _client().post("/api/agents", json={"name": "Ada", "connection_id": "nope"})
    assert response.status_code == 400
    assert response.json()["detail"] == "AI connection not found"
    assert db.list_agents() == []


def test_create_refuses_a_connection_with_no_model() -> None:
    conn = _connection(model=None)
    response = _client().post("/api/agents", json={"name": "Ada", "connection_id": conn.id})
    assert response.status_code == 400
    assert "has no model" in response.json()["detail"]
    assert db.list_agents() == []


def test_create_refuses_a_level_the_connection_does_not_offer() -> None:
    conn = _connection()
    response = _client().post("/api/agents", json={
        "name": "Ada", "connection_id": conn.id, "thinking_work": "xhigh",
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Thinking level not offered by this AI connection: thinking_work: xhigh"


def test_an_unknown_level_word_is_a_validation_error() -> None:
    conn = _connection()
    response = _client().post("/api/agents", json={
        "name": "Ada", "connection_id": conn.id, "thinking_work": "none",
    })
    assert response.status_code == 422


def test_patch_checks_the_level_against_the_effective_connection() -> None:
    plain = _connection(name="Plain", thinking_levels=None)
    rich = _connection(name="Rich")
    agent = db.create_agent("Ada", connection_id=rich.id, thinking_work="high")
    api = _client()

    # Moving to a connection that does not offer the stored level is refused...
    moved = api.patch(f"/api/agents/{agent.id}", json={"connection_id": plain.id})
    assert moved.status_code == 400
    assert "thinking_work: high" in moved.json()["detail"]
    assert db.get_agent(agent.id).connection_id == rich.id
    # ...and allowed together with the level going back to default.
    moved = api.patch(f"/api/agents/{agent.id}", json={"connection_id": plain.id, "thinking_work": "default"})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["connection_id"], moved.json()["thinking_work"]) == (plain.id, "default")


def test_patch_back_to_default_is_written() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id, thinking_social="off")
    response = _client().patch(f"/api/agents/{agent.id}", json={"thinking_social": "default"})
    assert response.status_code == 200, response.text
    assert db.get_agent(agent.id).thinking_social == "default"


def test_patch_refuses_a_missing_connection() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id)
    response = _client().patch(f"/api/agents/{agent.id}", json={"connection_id": "nope"})
    assert response.status_code == 400
    assert db.get_agent(agent.id).connection_id == conn.id


def test_an_unlinked_agent_can_still_be_edited() -> None:
    agent = db.create_agent("Ada")
    response = _client().patch(f"/api/agents/{agent.id}", json={
        "name": "Ada II", "thinking_social": "default", "thinking_work": "default",
    })
    assert response.status_code == 200, response.text
    assert db.get_agent(agent.id).name == "Ada II"


def test_the_single_agent_read_names_the_connection_without_secrets() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id)
    unlinked = db.create_agent("Bo")
    api = _client()

    body = api.get(f"/api/agents/{agent.id}").json()
    assert body["connection"] == {"id": conn.id, "name": "ZAI-Flash", "model": "glm-flash"}
    assert "sk-route" not in json.dumps(body)
    assert api.get(f"/api/agents/{unlinked.id}").json()["connection"] is None


def test_the_agent_api_key_route_is_gone() -> None:
    conn = _connection()
    agent = db.create_agent("Ada", connection_id=conn.id)
    assert _client().get(f"/api/agents/{agent.id}/api-key").status_code == 404
