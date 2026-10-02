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


# ─── "Supports images": an operator flag keyed by model name, not connection ───


def _settings_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
    from api.routes import router

    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def test_model_capabilities_round_trip_and_default_false() -> None:
    assert db.supports_images("gpt-test") is False  # no row: text-only
    db.set_supports_images("gpt-test", True)
    assert db.supports_images("gpt-test") is True
    db.set_supports_images("gpt-test", False)
    assert db.supports_images("gpt-test") is False
    # Exact, raw-name matching: a prefixed name is a different key.
    db.set_supports_images("gpt-test", True)
    assert db.supports_images("openai/gpt-test") is False


def test_create_connection_writes_the_flag_for_its_model() -> None:
    client = _settings_client()
    r = client.post("/api/connections", json={
        "name": "Vision", "api_base_url": _BASE_URL, "model": "llava", "supports_images": True,
    })
    assert r.status_code == 201, r.text
    assert r.json()["supports_images"] is True
    assert db.supports_images("llava") is True


def test_create_without_the_flag_leaves_the_models_row_alone() -> None:
    """The flag is shared by model name: an omitted one must not overwrite it."""
    client = _settings_client()
    db.set_supports_images("llava", True)
    r = client.post("/api/connections", json={
        "name": "Second", "api_base_url": _BASE_URL, "model": "llava",
    })
    assert r.status_code == 201, r.text
    assert db.supports_images("llava") is True
    assert r.json()["supports_images"] is True


def test_create_with_an_explicit_false_writes_false() -> None:
    client = _settings_client()
    db.set_supports_images("llava", True)
    r = client.post("/api/connections", json={
        "name": "Text only", "api_base_url": _BASE_URL, "model": "llava", "supports_images": False,
    })
    assert r.status_code == 201, r.text
    assert db.supports_images("llava") is False


def test_supports_images_without_a_model_is_refused() -> None:
    client = _settings_client()
    r = client.post("/api/connections", json={
        "name": "No model", "api_base_url": _BASE_URL, "supports_images": True,
    })
    assert r.status_code == 400
    assert db.list_connections() == []

    source = db.create_connection(name="Blank", api_base_url=_BASE_URL, model=None)
    r = client.patch(f"/api/connections/{source.id}", json={"supports_images": True})
    assert r.status_code == 400


def test_patch_writes_the_flag_for_the_effective_model() -> None:
    client = _settings_client()
    source = _create_source()  # model "gpt-test"

    r = client.patch(f"/api/connections/{source.id}", json={"supports_images": True})
    assert r.status_code == 200, r.text
    assert r.json()["supports_images"] is True
    assert db.supports_images("gpt-test") is True

    r = client.patch(f"/api/connections/{source.id}", json={"model": "gpt-next", "supports_images": True})
    assert r.status_code == 200
    assert db.supports_images("gpt-next") is True
    assert r.json()["model"] == "gpt-next"


def test_serialize_connection_reports_the_model_flag() -> None:
    from api.redaction import serialize_connection

    source = _create_source()
    assert serialize_connection(source)["supports_images"] is False
    db.set_supports_images("gpt-test", True)
    assert serialize_connection(source)["supports_images"] is True
    blank = db.create_connection(name="Blank", api_base_url=_BASE_URL, model=None)
    assert serialize_connection(blank)["supports_images"] is False


# ─── Thinking levels: carried on a copy, guarded where agents use them ───

_LEVELS = {"off": {"thinking": {"type": "disabled"}}, "high": {"thinking": {"type": "enabled"}}}


def test_copy_carries_the_thinking_levels() -> None:
    source = db.create_connection(
        name="Leveled", api_base_url=_BASE_URL, api_key=_API_KEY, model="gpt-test",
        thinking_levels=_LEVELS,
    )
    copy = db.duplicate_connection(source.id)
    assert copy is not None
    assert copy.thinking_levels == _LEVELS


def test_levels_round_trip_through_the_api_and_an_empty_map_clears_them() -> None:
    client = _settings_client()
    created = client.post("/api/connections", json={
        "name": "Leveled", "api_base_url": _BASE_URL, "model": "gpt-test", "thinking_levels": _LEVELS,
    })
    assert created.status_code == 201, created.text
    assert created.json()["thinking_levels"] == _LEVELS
    conn_id = created.json()["id"]

    patched = client.patch(f"/api/connections/{conn_id}", json={"thinking_levels": {"low": {"a": 1}}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["thinking_levels"] == {"low": {"a": 1}}

    cleared = client.patch(f"/api/connections/{conn_id}", json={"thinking_levels": {}})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["thinking_levels"] is None
    assert query_one("SELECT thinking_levels FROM ai_connections WHERE id = $1", [conn_id])["thinking_levels"] is None


def test_a_malformed_level_map_is_refused() -> None:
    client = _settings_client()
    bad = client.post("/api/connections", json={
        "name": "Bad", "api_base_url": _BASE_URL, "thinking_levels": {"turbo": {"a": 1}},
    })
    assert bad.status_code == 422
    assert db.list_connections() == []
    source = _create_source()
    bad = client.patch(f"/api/connections/{source.id}", json={"thinking_levels": {"high": "yes"}})
    assert bad.status_code == 422


def test_removing_a_level_agents_pick_is_refused_and_names_them() -> None:
    client = _settings_client()
    source = db.create_connection(
        name="Leveled", api_base_url=_BASE_URL, model="gpt-test", thinking_levels=_LEVELS,
    )
    db.create_agent("Ada", connection_id=source.id, thinking_work="high")
    db.create_agent("Bo", connection_id=source.id)

    refused = client.patch(f"/api/connections/{source.id}", json={"thinking_levels": {"off": _LEVELS["off"]}})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Still picked by: Ada (thinking_work: high). Change their thinking level first."
    )
    assert db.get_connection_by_id(source.id).thinking_levels == _LEVELS
    # Clearing every level is the same removal.
    assert client.patch(f"/api/connections/{source.id}", json={"thinking_levels": {}}).status_code == 409
    # Removing a level nobody picks is fine.
    kept = client.patch(f"/api/connections/{source.id}", json={"thinking_levels": {"high": _LEVELS["high"]}})
    assert kept.status_code == 200, kept.text


def test_deleting_a_connection_agents_use_is_refused_and_names_them() -> None:
    client = _settings_client()
    source = _create_source()
    db.create_agent("Ada", connection_id=source.id)
    db.create_agent("Bo", connection_id=source.id)

    refused = client.delete(f"/api/connections/{source.id}")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Used by: Ada, Bo. Move them to another connection first."
    assert db.get_connection_by_id(source.id) is not None

    unused = _create_source(name="Unused")
    assert client.delete(f"/api/connections/{unused.id}").status_code == 204
