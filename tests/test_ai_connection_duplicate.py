"""Duplicating an AI connection copies the secret without ever exposing it.

The operator's reason for duplicating is almost always ``extra_body`` — the
same provider, the same key, a different thinking mode — so the copy has to
carry the API key across. The key is never returned to the browser
(``serialize_connection`` emits ``has_api_key`` and the last four only), which
is why the copy is made server-side and why these tests check the *decrypted*
value and the *stored* column separately: a copy that loses the key and a copy
that stores it in plaintext both look identical from the API.

The DB is torn down and rebuilt per test rather than reset through
``reset_database()``: that call wipes ``artifacts/agents/`` and writes backups
into the work tree, and nothing here needs the reseed half of it.
"""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from db.ai_connections import _copy_name
from db.crud import query_one
from db.secret_store import is_encrypted

_API_KEY = "sk-duplicate-source-key-6789"
_BASE_URL = "https://api.example.test/v1"
_EXTRA_BODY = '{"thinking": {"type": "enabled"}}'


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


def _create_source(name: str = "Primary"):
    return db.create_connection(
        name=name,
        api_base_url=_BASE_URL,
        api_key=_API_KEY,
        model="gpt-test",
        extra_body=_EXTRA_BODY,
    )


def test_copy_carries_every_field_including_the_key() -> None:
    source = _create_source()

    copy = db.duplicate_connection(source.id)

    assert copy is not None
    # A copy, not the same row: a fresh id is what lets the operator edit one
    # without editing the other.
    assert copy.id != source.id
    assert copy.api_base_url == _BASE_URL
    assert copy.model == "gpt-test"
    assert copy.extra_body == _EXTRA_BODY
    # Usable, not merely present — the whole point of copying server-side.
    assert copy.api_key == _API_KEY


def test_copied_key_is_still_encrypted_at_rest() -> None:
    source = _create_source()

    copy = db.duplicate_connection(source.id)

    assert copy is not None
    row = query_one("SELECT api_key FROM ai_connections WHERE id = $1", [copy.id])
    assert row is not None
    stored = row["api_key"]
    # The duplicate reads plaintext out of get_connection_by_id and must hand it
    # back through create_connection's encrypt_secret, not straight into a
    # column. A second write path is how a copy silently downgrades the column.
    assert stored != _API_KEY
    assert is_encrypted(stored)


def test_duplicating_twice_increments_the_name() -> None:
    source = _create_source()

    first = db.duplicate_connection(source.id)
    second = db.duplicate_connection(source.id)

    assert first is not None and second is not None
    assert first.name == "Primary (copy)"
    assert second.name == "Primary (copy 2)"


def test_copy_name_derives_the_first_free_suffix() -> None:
    """The rule on its own, with no database — `taken` is a parameter."""
    assert _copy_name("X", set()) == "X (copy)"
    assert _copy_name("X", {"X (copy)"}) == "X (copy 2)"
    # A gap is filled rather than skipped past: deleting "(copy 2)" and
    # duplicating again reuses the number instead of climbing to "(copy 4)".
    assert _copy_name("X", {"X (copy)", "X (copy 3)"}) == "X (copy 2)"
    # An unrelated name in `taken` is not a collision.
    assert _copy_name("X", {"Y (copy)"}) == "X (copy)"


def test_unknown_id_returns_none_and_writes_nothing() -> None:
    _create_source()

    assert db.duplicate_connection("no-such-connection") is None
    # Matching get_connection_by_id/update_connection: a missing source is a
    # None, not an exception and not an orphan row named "None (copy)".
    assert [conn.name for conn in db.list_connections()] == ["Primary"]
