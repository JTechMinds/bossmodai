"""Per-agent extension settings: wrapped at rest, upserted, deleted with the agent."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config

SECRET = "super-secret-client-value"


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


def _raw(extension_id: str, agent_id: str) -> str:
    row = db.query_one(
        "SELECT config FROM extension_agent_configs WHERE extension_id = $1 AND agent_id = $2",
        [extension_id, agent_id],
    )
    return row["config"]


def test_round_trip_and_updated_at() -> None:
    agent = db.create_agent("Iris", role="Researcher")
    assert db.get_extension_agent_config("ms365-mail", agent.id) is None
    assert db.extension_agent_config_updated_at("ms365-mail", agent.id) is None
    values = {"mailbox": "reports@contoso.com", "client_secret": SECRET}
    db.set_extension_agent_config("ms365-mail", agent.id, values)
    assert db.get_extension_agent_config("ms365-mail", agent.id) == values
    assert db.extension_agent_config_updated_at("ms365-mail", agent.id) is not None
    assert db.configured_agent_ids("ms365-mail") == frozenset({agent.id})
    assert db.configured_agent_ids("other-ext") == frozenset()


def test_the_stored_value_is_wrapped_and_hides_the_secret() -> None:
    agent = db.create_agent("Iris", role="Researcher")
    db.set_extension_agent_config("ms365-mail", agent.id, {"client_secret": SECRET})
    raw = _raw("ms365-mail", agent.id)
    assert raw.startswith("bm1:")
    assert SECRET not in raw and "client_secret" not in raw


def test_an_upsert_replaces_the_whole_object() -> None:
    agent = db.create_agent("Iris", role="Researcher")
    db.set_extension_agent_config("ms365-mail", agent.id, {"a": "1", "b": "2"})
    db.set_extension_agent_config("ms365-mail", agent.id, {"a": "3"})
    assert db.get_extension_agent_config("ms365-mail", agent.id) == {"a": "3"}
    assert db.query_one("SELECT COUNT(*) AS n FROM extension_agent_configs", [])["n"] == 1


def test_delete_reports_whether_a_row_went() -> None:
    agent = db.create_agent("Iris", role="Researcher")
    db.set_extension_agent_config("ms365-mail", agent.id, {"a": "1"})
    assert db.delete_extension_agent_config("ms365-mail", agent.id) is True
    assert db.delete_extension_agent_config("ms365-mail", agent.id) is False
    assert db.get_extension_agent_config("ms365-mail", agent.id) is None


@pytest.mark.parametrize("stored", ['["not", "an", "object"]', '{"a": 1}', "not json"])
def test_a_corrupt_row_raises(stored: str) -> None:
    from db.secret_store import encrypt_secret

    agent = db.create_agent("Iris", role="Researcher")
    db.execute(
        "INSERT INTO extension_agent_configs (extension_id, agent_id, config) VALUES ($1, $2, $3)",
        ["ms365-mail", agent.id, encrypt_secret(stored)],
    )
    with pytest.raises(ValueError):
        db.get_extension_agent_config("ms365-mail", agent.id)


def test_deleting_the_agent_removes_its_rows() -> None:
    agent = db.create_agent("Iris", role="Researcher")
    other = db.create_agent("Vera", role="Writer")
    db.set_extension_agent_config("ms365-mail", agent.id, {"a": "1"})
    db.set_extension_agent_config("ms365-mail", other.id, {"a": "2"})
    assert db.delete_agent_rows(agent.id) is True
    assert db.get_extension_agent_config("ms365-mail", agent.id) is None
    assert db.get_extension_agent_config("ms365-mail", other.id) == {"a": "2"}
