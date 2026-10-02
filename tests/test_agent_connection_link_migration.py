"""The upgrade that links each agent to one AI connection.

Before it, an agent carried five per-mode model names and a COPY of one
connection's base URL, key and extra body. The migration links it to the
connection it was really using and drops the copies:

1. exact — one connection on the agent's endpoint with its model (an exact
   ``extra_body`` match breaks a tie);
2. endpoint — otherwise the single connection on that endpoint, logged at INFO
   with the old and new model, because the agent now sends the connection's;
3. unlinked — anything else, logged at WARNING.

Built from a real database: a fresh schema is turned back into the old shape
(new columns dropped, old columns added and filled), then ``init_db`` runs the
migration sequence the way a boot does.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

import db
from core import config

_OLD_AGENT_COLUMNS = {
    "model_social": "VARCHAR", "model_work": "VARCHAR", "model_reasoning": "VARCHAR",
    "model_extraction": "VARCHAR", "model_self_queue": "VARCHAR",
    "api_base_url": "VARCHAR", "api_key": "VARCHAR", "extra_body": "TEXT",
}
_OLD_SNAPSHOT_COLUMNS = (
    "model_social", "model_work", "model_reasoning", "model_extraction", "model_self_queue",
)
_NEW_COLUMNS = ("connection_id", "thinking_social", "thinking_work")


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


def _columns(table: str) -> set[str]:
    return {row["name"] for row in db.query(f"PRAGMA table_info({table})")}


def _to_old_schema() -> None:
    """Put agents, snapshots and connections back in their pre-link shape."""
    for table in ("agents", "agent_snapshots"):
        for column in _NEW_COLUMNS:
            db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    db.execute("ALTER TABLE ai_connections DROP COLUMN thinking_levels")
    for column, kind in _OLD_AGENT_COLUMNS.items():
        db.execute(f"ALTER TABLE agents ADD COLUMN {column} {kind}")
    for column in _OLD_SNAPSHOT_COLUMNS:
        db.execute(f"ALTER TABLE agent_snapshots ADD COLUMN {column} VARCHAR")


def _old_agent(agent_id: str, *, base: str | None, work: str | None = None,
               social: str | None = None, extra_body: str | None = None) -> None:
    db.execute(
        "UPDATE agents SET api_base_url = $1, model_work = $2, model_social = $3, "
        "extra_body = $4, api_key = $5 WHERE id = $6",
        [base, work, social, extra_body, "sk-copied", agent_id],
    )


def _link_of(agent_id: str) -> str | None:
    return db.query("SELECT connection_id FROM agents WHERE id = $1", [agent_id])[0]["connection_id"]


@pytest.fixture
def upgraded(caplog: pytest.LogCaptureFixture) -> dict[str, str]:
    """An old database with one agent per linking case, migrated once."""
    exact = db.create_connection(name="Exact", api_base_url="http://exact/v1/", model="m1").id
    high = db.create_connection(
        name="ZAI-Flash", api_base_url="http://zai/v1", model="glm", extra_body='{"t": "high"}',
    ).id
    low = db.create_connection(
        name="ZAI-Flash-Low", api_base_url="http://zai/v1", model="glm", extra_body='{"t": "low"}',
    ).id
    qwen = db.create_connection(name="QWEN-Local", api_base_url="http://qwen/v1", model="Qwen-Flash").id
    agents = {name: db.create_agent(name).id for name in ("Ada", "Zed", "Quinn", "Nobody", "Crowd")}

    _to_old_schema()
    _old_agent(agents["Ada"], base="http://exact/v1", work="m1")
    _old_agent(agents["Zed"], base="http://zai/v1/", work="glm", extra_body='{"t": "low"}')
    _old_agent(agents["Quinn"], base="http://qwen/v1", social="qwen3.8-27b")
    _old_agent(agents["Nobody"], base="http://gone/v1", work="m1")
    _old_agent(agents["Crowd"], base="http://zai/v1", work="other")
    db.execute(
        "UPDATE agent_snapshots SET model_work = $1 WHERE agent_id = $2", ["m1", agents["Ada"]],
    )
    db.execute(
        "UPDATE agent_snapshots SET model_social = $1 WHERE agent_id = $2", ["glm", agents["Zed"]],
    )

    with caplog.at_level(logging.INFO, logger="db.connection"):
        db.init_db()
    return {**agents, "exact": exact, "high": high, "low": low, "qwen": qwen}


def test_an_exact_match_links_the_agent(upgraded: dict[str, str]) -> None:
    # Trailing "/" on either side is the same endpoint.
    assert _link_of(upgraded["Ada"]) == upgraded["exact"]


def test_extra_body_breaks_a_tie_between_exact_matches(upgraded: dict[str, str]) -> None:
    assert _link_of(upgraded["Zed"]) == upgraded["low"]


def test_the_single_connection_on_an_endpoint_links_and_logs_the_model_change(
    upgraded: dict[str, str], caplog: pytest.LogCaptureFixture,
) -> None:
    assert _link_of(upgraded["Quinn"]) == upgraded["qwen"]
    # The migration ran in the fixture, so its records are the setup phase's.
    records = caplog.get_records("setup")
    infos = [r.getMessage() for r in records if r.levelno == logging.INFO and "Quinn" in r.getMessage()]
    assert infos == [
        "Migration: agent Quinn (was model qwen3.8-27b) linked to AI connection QWEN-Local "
        "on the same endpoint; it now sends model Qwen-Flash"
    ]
    # An exact link changes nothing the agent sends, so it is not logged as one.
    assert not [r for r in records if "Ada" in r.getMessage()]


def test_no_connection_or_several_on_the_endpoint_leaves_the_agent_unlinked(
    upgraded: dict[str, str], caplog: pytest.LogCaptureFixture,
) -> None:
    assert _link_of(upgraded["Nobody"]) is None
    assert _link_of(upgraded["Crowd"]) is None
    warnings = sorted(r.getMessage() for r in caplog.get_records("setup") if r.levelno == logging.WARNING
                      and "left without an AI connection" in r.getMessage())
    assert warnings == [
        "Migration: agent Crowd left without an AI connection (model other, base URL http://zai/v1) "
        "— choose one in its settings",
        "Migration: agent Nobody left without an AI connection (model m1, base URL http://gone/v1) "
        "— choose one in its settings",
    ]


def test_snapshots_link_only_when_one_connection_has_their_model(upgraded: dict[str, str]) -> None:
    rows = {row["agent_id"]: row["connection_id"] for row in db.query("SELECT agent_id, connection_id FROM agent_snapshots")}
    assert rows[upgraded["Ada"]] == upgraded["exact"]
    # Two connections answer to "glm".
    assert rows[upgraded["Zed"]] is None


def test_the_old_columns_are_dropped_and_the_new_ones_hold(upgraded: dict[str, str]) -> None:
    agents = _columns("agents")
    snapshots = _columns("agent_snapshots")
    assert not agents & set(_OLD_AGENT_COLUMNS)
    assert not snapshots & set(_OLD_SNAPSHOT_COLUMNS)
    assert set(_NEW_COLUMNS) <= agents
    assert set(_NEW_COLUMNS) <= snapshots
    assert "thinking_levels" in _columns("ai_connections")
    ada = db.get_agent(upgraded["Ada"])
    assert (ada.connection_id, ada.thinking_social, ada.thinking_work) == (upgraded["exact"], "default", "default")


def test_a_second_boot_finds_nothing_to_link_or_drop(
    upgraded: dict[str, str], caplog: pytest.LogCaptureFixture,
) -> None:
    before = {row["id"]: row["connection_id"] for row in db.query("SELECT id, connection_id FROM agents")}
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="db.connection"):
        db.init_db()
    after = {row["id"]: row["connection_id"] for row in db.query("SELECT id, connection_id FROM agents")}
    assert after == before
    assert not [r for r in caplog.records if r.getMessage().startswith("Migration:")]
