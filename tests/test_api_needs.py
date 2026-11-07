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
VALID_KINDS = {"consent", "approval", "blocked", "error", "connection", "work_summary"}


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
    db.update_task(task.id, status="blocked", status_note="Waiting on the staging credentials")

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    needs = res.json()

    blocked = [n for n in needs if n["kind"] == "blocked"]
    assert len(blocked) == 1, f"expected one blocked need, got {needs}"
    item = blocked[0]
    assert REQUIRED_KEYS <= set(item), f"missing keys: {REQUIRED_KEYS - set(item)}"
    assert item["agent_name"] == "Jim", "agent id must be resolved to a name"
    assert item["kind"] in VALID_KINDS
    # The agent's stated reason rides on the sub line, after the title.
    assert item["sub"] == "Implement inline command suppression — Waiting on the staging credentials"
    assert isinstance(item["actions"], list) and item["actions"], "a need must be actionable"
    for action in item["actions"]:
        assert {"label", "href", "method"} <= set(action)
        assert action["method"] == "POST"
    assert item["actions"] == [{
        "label": "Nudge",
        "method": "POST",
        "tone": "primary",
        "href": f"/api/tasks/{task.id}/resume",
        "note": {"title": "Nudge Jim", "label": "Anything that might help? (optional)"},
    }]


def test_unassigned_blocked_task_has_no_action(client: TestClient) -> None:
    """Nudge needs an assignee to hand the task back to.

    The one need with no action: its "Show me" to Tasks, where it can be
    assigned, is the way forward, so "a need must be actionable" does not
    apply here.
    """
    requester = db.create_agent(name="Jim", role="Engineer")
    task = db.create_task(title="Write the recap", requester_id=requester.id)
    db.update_task(task.id, status="blocked")

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    blocked = [n for n in res.json() if n["kind"] == "blocked"]
    assert len(blocked) == 1, f"expected one blocked need, got {res.json()}"
    assert blocked[0]["agent_id"] is None
    assert blocked[0]["sub"] == "Write the recap"
    assert blocked[0]["actions"] == []


def _paused_connection(name: str = "Proxy key", *, kind: str = "rejected") -> str:
    """Create a connection and record enough provider failures to pause it."""
    from core.llm import connection_health

    connection_id = db.create_connection(
        name=name, api_base_url="http://127.0.0.1:9/v1", model="test-model",
    ).id
    for _ in range(config.require_int("llm_connection_failure_threshold")):
        connection_health.record_failure(
            connection_id, kind=kind, error="LlmProxy::Error: No active task for this API key's project",
        )
    return connection_id


def test_paused_connection_surfaces_first_with_retry_now(client: TestClient) -> None:
    """A paused AI connection is one need: what waits on it, the provider's words, Retry now."""
    connection_id = _paused_connection()
    db.create_agent(name="Jim", role="Engineer", connection_id=connection_id)
    agent = db.create_agent(name="Pam", role="Engineer")
    task = db.create_task(title="Ship it", assigned_to=agent.id, requester_id=agent.id)
    db.update_task(task.id, status="blocked")

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    connection_needs = [n for n in res.json() if n["kind"] == "connection"]
    assert len(connection_needs) == 1, f"expected one connection need, got {res.json()}"
    item = connection_needs[0]
    assert REQUIRED_KEYS <= set(item), f"missing keys: {REQUIRED_KEYS - set(item)}"
    assert item["kind"] in VALID_KINDS
    assert item["id"] == f"connection:{connection_id}"
    assert item["card_kind"] == "connection"
    assert item["agent_id"] is None and item["agent_name"] is None
    assert item["conversation_id"] is None
    assert item["title"] == "AI connection “Proxy key” is paused"
    # The only connection is also System AI's (an unset System AI uses the first).
    assert item["sub"] == (
        "Jim and System AI will wait until you fix it in Settings → AI Connections or retry. "
        "The provider said: LlmProxy::Error: No active task for this API key's project"
    )
    assert item["created_at"] == db.get_connection_health(connection_id).suspended_at.isoformat()
    assert item["actions"] == [{
        "label": "Retry now",
        "method": "POST",
        "tone": "primary",
        "href": f"/api/connections/{connection_id}/retry",
    }]


def test_transient_pause_says_it_tries_again_and_names_at_most_three(client: TestClient) -> None:
    db.create_connection(name="Another", api_base_url="http://127.0.0.1:9/v1", model="m")
    connection_id = _paused_connection("Zeta", kind="transient")
    for name in ("Ann", "Bob", "Cat", "Dan", "Eve"):
        db.create_agent(name=name, role="Engineer", connection_id=connection_id)

    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    item = next(n for n in res.json() if n["kind"] == "connection")
    assert item["sub"].startswith("Ann, Bob, Cat and 2 more will try again automatically. ")


def test_paused_connection_with_nothing_waiting_says_so(client: TestClient) -> None:
    db.create_connection(name="Another", api_base_url="http://127.0.0.1:9/v1", model="m")
    _paused_connection("Zeta")

    res = client.get("/api/needs", headers=_auth())
    item = next(n for n in res.json() if n["kind"] == "connection")
    assert item["sub"].startswith("Nothing is waiting on it. The provider said: ")


def test_retry_route_404_for_an_unknown_connection(client: TestClient) -> None:
    res = client.post("/api/connections/no-such-connection/retry", headers=_auth())
    assert res.status_code == 404, res.text


def test_retry_route_409_when_the_connection_is_not_paused(client: TestClient) -> None:
    connection_id = db.create_connection(
        name="Healthy", api_base_url="http://127.0.0.1:9/v1", model="m",
    ).id
    res = client.post(f"/api/connections/{connection_id}/retry", headers=_auth())
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "This connection is not paused."


def test_retry_route_makes_the_test_call_due(client: TestClient) -> None:
    from datetime import datetime, timezone

    from core.llm import connection_health

    connection_id = _paused_connection()
    assert db.get_connection_health(connection_id).retry_at is None
    assert not connection_health.admit(connection_id, lease_seconds=60).allowed

    res = client.post(f"/api/connections/{connection_id}/retry", headers=_auth())
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}
    health = db.get_connection_health(connection_id)
    assert health.is_suspended
    assert health.retry_at is not None and health.retry_at <= datetime.now(timezone.utc)
    admission = connection_health.admit(connection_id, lease_seconds=60)
    assert admission.allowed and admission.probe


def _work_context(*, budget: int = 350_000, trim_percent: int = 50, summary_percent: int = 10) -> None:
    db.set_setting("work_snapshot_max_chars", str(budget), "simulation")
    db.set_setting("work_snapshot_trim_percent", str(trim_percent), "simulation")
    db.set_setting("work_snapshot_summary_percent", str(summary_percent), "simulation")
    config.reload()


def _overflowing_agent(summary_chars: int = 49_587) -> str:
    """Eng-A, whose gpt-6.1-sol summary did not fit a 35,000 limit at a 350,000 budget."""
    agent = db.create_agent(name="Eng-A", role="Engineer")
    db.record_work_summary_overflow(
        agent.id, model="gpt-6.1-sol", summary_chars=summary_chars, limit_chars=35_000, budget_chars=350_000,
    )
    return agent.id


def _work_summary_needs(client: TestClient) -> list[dict]:
    res = client.get("/api/needs", headers=_auth())
    assert res.status_code == 200, res.text
    return [n for n in res.json() if n["kind"] == "work_summary"]


def test_a_summary_too_long_for_the_settings_says_which_setting_to_raise(client: TestClient) -> None:
    _work_context()
    agent_id = _overflowing_agent()

    (item,) = _work_summary_needs(client)

    assert REQUIRED_KEYS <= set(item), f"missing keys: {REQUIRED_KEYS - set(item)}"
    assert item["id"] == f"work_summary:{agent_id}"
    assert item["kind"] == "work_summary"
    assert (item["agent_id"], item["agent_name"]) == (agent_id, "Eng-A")
    assert item["title"] == "Eng-A's work summary is too long for your settings"
    assert item["sub"] == (
        "Its model (gpt-6.1-sol) wrote a 49,587-character summary of its earlier steps; the limit was 35,000. "
        "Set Work summary size to 15% or more (it is 10%). Summaries can then use up to 52,500 characters "
        "(about 13K tokens), and recent steps kept word for word go from 140,000 to 122,500 characters "
        "(about 35K → 31K tokens). Total context stays the same."
    )
    assert item["setting_key"] == "work_snapshot_summary_percent"
    assert item["created_at"] == db.list_work_summary_overflows()[0].created_at.isoformat()
    assert item["conversation_id"] is None
    assert item["actions"] == [], "no action: its button opens the setting"


def test_the_work_summary_need_hides_once_the_settings_fit(client: TestClient) -> None:
    _work_context()
    _overflowing_agent()
    assert len(_work_summary_needs(client)) == 1

    _work_context(summary_percent=15)

    assert _work_summary_needs(client) == []


def test_a_summary_no_single_size_fixes_points_at_work_kept_after_summarizing(client: TestClient) -> None:
    _work_context()
    _overflowing_agent(summary_chars=180_000)  # needs 52%, not below the 50% trim share

    (item,) = _work_summary_needs(client)

    assert item["setting_key"] == "work_snapshot_trim_percent"


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
