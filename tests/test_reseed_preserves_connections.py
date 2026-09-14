"""Recreate DB carries AI connections across the reseed — and nothing else.

A base URL and an API key are the one thing an operator cannot regenerate from
inside the app, so the reseed restores them. Everything else — agents, tasks,
chat history, personalities — is what the reseed is for, so these tests pin the
destruction as hard as they pin the survival.

The round trip runs a *real* ``reset_database()``: a double would not catch a
capture placed on the wrong side of the file deletion, which is the whole bug
this behaviour exists to prevent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
import db.connection as db_connection
from core.bm_cli import filesystem
from core.runtime.services import RuntimeServices
from db.crud import query_one
from db.secret_store import is_encrypted

_API_KEY = "sk-reseed-survivor-1234567890"
_BASE_URL = "https://api.example.test/v1"
_EXTRA_BODY = '{"temperature": 0.2}'


@pytest.fixture(autouse=True)
def _sandboxed_reset(tmp_path, monkeypatch) -> None:
    """Give each test an empty database and keep the reset out of the work tree.

    ``reset_database()`` wipes ``artifacts/agents/`` and writes its backup under
    ``artifacts/db_backups/`` — both resolved from module-level roots, both
    holding a running installation's own files. Redirecting the roots leaves the
    database half of the reset (the half under test) completely real while the
    test stops short of deleting the developer's agent workspaces.

    The fresh database is what lets "what came back" be read as an exact list
    rather than a search through whatever earlier tests left behind.
    """
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    monkeypatch.setattr(filesystem, "_PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(db_connection, "_PROJECT_ROOT", tmp_path)

    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()


def _create_connection():
    return db.create_connection(
        name="Primary",
        api_base_url=_BASE_URL,
        api_key=_API_KEY,
        model="gpt-test",
        extra_body=_EXTRA_BODY,
    )


def test_reset_and_restore_round_trips_the_connection() -> None:
    created = _create_connection()

    preserved = db.list_connections()
    db.reset_database()
    restored = db.restore_connections(preserved)

    assert restored == 1
    # Found by its original id at all: the row is the same connection, not a copy.
    after = db.get_connection_by_id(created.id)
    assert after is not None
    assert after.name == created.name
    assert after.api_base_url == _BASE_URL
    assert after.model == "gpt-test"
    assert after.extra_body == _EXTRA_BODY
    assert after.created_at == created.created_at
    # The operator's key must come back usable, not merely present.
    assert after.api_key == _API_KEY


def test_restored_key_is_still_encrypted_at_rest() -> None:
    created = _create_connection()

    preserved = db.list_connections()
    db.reset_database()
    db.restore_connections(preserved)

    row = query_one("SELECT api_key FROM ai_connections WHERE id = $1", [created.id])
    assert row is not None
    stored = row["api_key"]
    # Restoring through the plaintext the capture hands back must not downgrade
    # the column: the same encrypt_secret() wrapping create_connection uses.
    assert stored != _API_KEY
    assert is_encrypted(stored)


def test_reseed_still_destroys_everything_else() -> None:
    personality = db.create_personality(
        name="Reseed Victim",
        prompt_template="You do not survive the reseed.",
    )
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)

    preserved = db.list_connections()
    db.reset_database()
    db.restore_connections(preserved)

    assert db.get_personality(personality.id) is None
    assert db.get_agent(agent.id) is None


def test_restoring_nothing_is_a_no_op() -> None:
    db.reset_database()

    assert db.restore_connections([]) == 0
    assert db.list_connections() == []


async def test_service_captures_connections_before_the_reset(monkeypatch) -> None:
    """The ordering test: a capture after the reset would return an empty list."""
    services = RuntimeServices()

    async def _noop() -> None:
        return None

    monkeypatch.setattr(services, "_start_unlocked", _noop)
    monkeypatch.setattr(services, "_stop_unlocked", _noop)

    created = _create_connection()
    await services.reseed_application_data()

    survivor = db.get_connection_by_id(created.id)
    assert survivor is not None
    assert survivor.api_key == _API_KEY
