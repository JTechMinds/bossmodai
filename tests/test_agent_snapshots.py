"""Agent snapshots — the one current, secret-free copy of each agent's setup.

A delete is a hard delete, so before snapshots nothing could bring a tuned
agent back. These pin the capture points (create, save, prompt-history policy
change, and delete — before the rows go), the secret boundary, the trim to
``recent_agents_limit``, and the read Add agent's Recent scope makes.
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
from core.models import AgentSnapshot

SECRET_KEY = "sk-live-DO-NOT-STORE-7f3a"
SECRET_URL = "https://tenant-secret.example.com/v1"
SECRET_BODY = '{"api_token": "body-secret-91c2"}'
SECRET_COLUMNS = {"api_key", "api_base_url", "extra_body"}


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
    # config caches settings per process, and a later module may create
    # agents without reloading it. A limit a test lowered — or deleted — must
    # not follow it there, so the seed is put back and re-read.
    db.reset_setting_to_seed("recent_agents_limit")
    config.reload()
    db.close_connection()


def _only(agent_id: str) -> AgentSnapshot:
    matches = [snap for snap in db.list_agent_snapshots() if snap.agent_id == agent_id]
    assert len(matches) == 1, matches
    return matches[0]


def _raw_rows() -> list[dict]:
    return db.query("SELECT * FROM agent_snapshots")


def _set_limit(value: str) -> None:
    db.set_setting("recent_agents_limit", value, "advanced")
    config.reload()


def test_create_captures_the_setup_and_its_policy() -> None:
    agent = db.create_agent(
        "Ada",
        role="Code Auditor",
        description="Reads a diff and reports what is not true.",
        done_fail_bar="A checkable allow/deny exists.",
        communication={"tone": "direct", "density": "compact", "jargon": "light",
                       "audience": "operator"},
        prompt_template="You are terse.",
        color="#1d4ed8",
        model_work="qwen3.8-27b",
        model_reasoning="gpt-4o-mini",
        desk_x=3,
        desk_y=4,
    )
    snap = _only(agent.id)
    assert snap.name == "Ada"
    assert snap.role == "Code Auditor"
    assert snap.description == "Reads a diff and reports what is not true."
    assert snap.done_fail_bar == "A checkable allow/deny exists."
    assert snap.communication == agent.communication == {
        "tone": "direct", "density": "compact", "jargon": "light", "audience": "operator",
    }
    assert snap.prompt_template == "You are terse."
    assert snap.color == "#1d4ed8"
    assert (snap.model_work, snap.model_reasoning, snap.model_social) == (
        "qwen3.8-27b", "gpt-4o-mini", None,
    )
    assert (snap.desk_x, snap.desk_y) == (3, 4)
    assert snap.deleted_at is None
    # create_agent writes the default policy row itself, so the create-time
    # capture already holds it: the four fields the form edits, and no more.
    assert snap.prompt_history_policy == {
        "last_n_histories": config.get_int("default_prompt_history_last_n"),
        "max_allowed_history_tokens": config.get_int("default_prompt_history_max_tokens"),
        "earliest_ts_allowed": None,
        "include_notifications": True,
    }


def test_a_save_recaptures_in_place() -> None:
    agent = db.create_agent("Ada", role="Writer", model_work="llama3.1:8b")
    first = _only(agent.id)
    db.update_agent(agent.id, role="Editor", description="Tightens drafts.",
                    model_work="gpt-4o-mini")
    second = _only(agent.id)
    assert second.id == first.id, "one row per agent, overwritten — no history"
    assert second.role == "Editor"
    assert second.description == "Tightens drafts."
    assert second.model_work == "gpt-4o-mini"
    assert second.captured_at > first.captured_at
    assert len(_raw_rows()) == 1


def test_an_update_that_writes_nothing_captures_nothing() -> None:
    agent = db.create_agent("Ada", role="Writer")
    first = _only(agent.id)
    db.update_agent(agent.id, not_a_column="ignored")
    assert _only(agent.id).captured_at == first.captured_at


def test_a_policy_change_recaptures() -> None:
    """The create form saves the policy AFTER creating the agent."""
    agent = db.create_agent("Ada", role="Writer")
    db.update_agent_prompt_history_policy(
        agent.id, last_n_histories=7, max_allowed_history_tokens=900,
        include_notifications=False,
    )
    snap = _only(agent.id)
    assert snap.prompt_history_policy == {
        "last_n_histories": 7,
        "max_allowed_history_tokens": 900,
        "earliest_ts_allowed": None,
        "include_notifications": False,
    }


def test_delete_stamps_the_snapshot_and_it_outlives_the_agent() -> None:
    agent = db.create_agent("Ada", role="Writer", description="Drafts.")
    db.update_agent_prompt_history_policy(agent.id, last_n_histories=12)
    assert db.delete_agent(agent.id) is True
    assert db.get_agent(agent.id) is None
    assert db.get_agent_prompt_history_policy(agent.id) is None
    snap = _only(agent.id)
    assert snap.deleted_at is not None
    assert snap.deleted_at == snap.captured_at
    assert snap.name == "Ada" and snap.description == "Drafts."
    # Captured BEFORE the policy row went, so it still holds what was set.
    assert snap.prompt_history_policy["last_n_histories"] == 12
    # Deleting what is not there captures nothing.
    assert db.delete_agent("no-such-agent") is False
    assert len(_raw_rows()) == 1


def test_the_table_has_no_column_that_could_hold_a_secret() -> None:
    columns = {row["name"] for row in db.query("PRAGMA table_info(agent_snapshots)")}
    assert columns, "agent_snapshots does not exist"
    assert not columns & SECRET_COLUMNS
    assert not set(AgentSnapshot.model_fields) & SECRET_COLUMNS


def test_an_agent_with_credentials_leaves_no_trace_of_them() -> None:
    agent = db.create_agent(
        "Keyed", role="Writer", model_work="gpt-4o-mini",
        api_base_url=SECRET_URL, api_key=SECRET_KEY, extra_body=SECRET_BODY,
    )
    # The agent really does hold them, so their absence below means something.
    assert db.get_agent(agent.id).api_key == SECRET_KEY
    db.update_agent(agent.id, role="Editor", api_key=SECRET_KEY)
    db.update_agent_prompt_history_policy(agent.id, last_n_histories=3)
    db.delete_agent(agent.id)
    rows = _raw_rows()
    assert len(rows) == 1
    stored = json.dumps(rows, default=str)
    for secret in (SECRET_KEY, SECRET_URL, "body-secret-91c2"):
        assert secret not in stored, secret
    # Nor through the model the route serializes.
    served = json.dumps([snap.model_dump(mode="json") for snap in db.list_agent_snapshots()])
    for secret in (SECRET_KEY, SECRET_URL, "body-secret-91c2"):
        assert secret not in served, secret


def test_captures_are_trimmed_to_the_setting() -> None:
    _set_limit("2")
    first = db.create_agent("First")
    second = db.create_agent("Second")
    third = db.create_agent("Third")
    kept = [snap.agent_id for snap in db.list_agent_snapshots()]
    assert kept == [third.id, second.id]
    # A save moves an agent back to the top and something else falls off.
    db.update_agent(second.id, role="Editor")
    fourth = db.create_agent("Fourth")
    assert [snap.agent_id for snap in db.list_agent_snapshots()] == [fourth.id, second.id]
    assert first.id not in kept


def test_prune_keeps_the_newest_and_counts_what_it_removed() -> None:
    agents = [db.create_agent(name) for name in ("A", "B", "C")]
    assert db.prune_agent_snapshots(1) == 2
    assert [snap.agent_id for snap in db.list_agent_snapshots()] == [agents[-1].id]
    assert db.prune_agent_snapshots(1) == 0
    assert db.prune_agent_snapshots(0) == 1
    assert db.list_agent_snapshots() == []
    with pytest.raises(ValueError):
        db.prune_agent_snapshots(-1)


def test_a_missing_limit_is_an_error_and_the_create_rolls_back() -> None:
    """No `or 20`: a missing seed is a bug, and it is said, not guessed."""
    db.execute("DELETE FROM settings WHERE key = $1", ["recent_agents_limit"])
    config.reload()
    with pytest.raises(config.ConfigError):
        db.create_agent("Orphan")
    # The snapshot is written inside the create's transaction, so an agent
    # whose snapshot could not be written was not created either.
    assert db.list_agents() == []
    assert _raw_rows() == []


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def test_the_route_lists_newest_first() -> None:
    older = db.create_agent("Older", role="Writer")
    newer = db.create_agent("Newer", role="Editor")
    gone = db.create_agent("Gone", role="Auditor", api_key=SECRET_KEY)
    db.delete_agent(gone.id)
    # A save puts an agent at the top again.
    db.update_agent(older.id, description="Now first.")
    response = _client().get(
        "/api/agent-snapshots",
        headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["agent_id"] for row in body] == [older.id, gone.id, newer.id]
    assert body[0]["description"] == "Now first."
    assert body[1]["deleted_at"] is not None
    assert body[0]["deleted_at"] is None
    assert SECRET_KEY not in response.text
    for row in body:
        assert not set(row) & SECRET_COLUMNS
