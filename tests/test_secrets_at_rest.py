"""HA-SEC-P1-05 — secret columns are wrapped at rest."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.redaction import serialize_connection, serialize_setting
from api.routes import router
from core import config
from core.models import Setting
from db.secret_store import SECRET_PREFIX, data_key_path, is_encrypted, migrate_plaintext_secrets
from fastapi import FastAPI


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


def _raw_value(sql: str, params: list[object]) -> str | None:
    row = db.query_one(sql, params)
    if row is None:
        return None
    value = next(iter(row.values()))
    return None if value is None else str(value)


def test_connection_api_key_not_plaintext_in_sqlite() -> None:
    secret = "sk-test-plaintext-connection-key"
    created = db.create_connection(
        name="Local",
        api_base_url="http://127.0.0.1:9/v1",
        api_key=secret,
        model="test",
    )
    assert created.api_key == secret

    stored = _raw_value("SELECT api_key FROM ai_connections WHERE id = $1", [created.id])
    assert stored is not None
    assert secret not in stored
    assert is_encrypted(stored)
    assert stored.startswith(SECRET_PREFIX)

    loaded = db.get_connection_by_id(created.id)
    assert loaded is not None
    assert loaded.api_key == secret


def test_agent_api_key_not_plaintext_in_sqlite() -> None:
    secret = "sk-test-plaintext-agent-key"
    agent = db.create_agent("Ada", role="Eng", api_key=secret, desk_x=1, desk_y=1)
    assert agent.api_key == secret

    stored = _raw_value("SELECT api_key FROM agents WHERE id = $1", [agent.id])
    assert stored is not None
    assert secret not in stored
    assert is_encrypted(stored)

    loaded = db.get_agent(agent.id)
    assert loaded is not None
    assert loaded.api_key == secret


def test_secret_settings_not_plaintext_in_sqlite() -> None:
    token = "123456:TELEGRAM-BOT-TOKEN-VALUE"
    db.set_setting("telegram_bot_token", token, "telegram")
    stored = _raw_value("SELECT value FROM settings WHERE key = $1", ["telegram_bot_token"])
    assert stored is not None
    assert token not in stored
    assert is_encrypted(stored)

    settings = {row.key: row.value for row in db.get_settings()}
    assert settings["telegram_bot_token"] == token

    api_token = db.ensure_local_api_token()
    assert api_token
    raw_api = _raw_value("SELECT value FROM settings WHERE key = $1", ["local_api_token"])
    assert raw_api is not None
    assert api_token not in raw_api
    assert is_encrypted(raw_api)


def test_migrate_rewrites_legacy_plaintext_rows() -> None:
    secret = "sk-legacy-plaintext"
    created = db.create_connection(
        name="Legacy",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="placeholder",
        model="test",
    )
    db.execute(
        "UPDATE ai_connections SET api_key = $1 WHERE id = $2",
        [secret, created.id],
    )
    assert _raw_value("SELECT api_key FROM ai_connections WHERE id = $1", [created.id]) == secret

    rewritten = migrate_plaintext_secrets()
    assert rewritten >= 1
    stored = _raw_value("SELECT api_key FROM ai_connections WHERE id = $1", [created.id])
    assert stored is not None
    assert secret not in stored
    assert is_encrypted(stored)
    loaded = db.get_connection_by_id(created.id)
    assert loaded is not None
    assert loaded.api_key == secret


def test_data_key_is_created_private() -> None:
    db.set_setting("telegram_bot_token", "x" * 20, "telegram")
    key_path = data_key_path()
    assert key_path.exists()
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_get_serializers_still_redact() -> None:
    secret = "sk-visible-only-as-last4"
    connection = db.create_connection(
        name="Redact",
        api_base_url="http://127.0.0.1:9/v1",
        api_key=secret,
        model="test",
    )
    payload = serialize_connection(connection)
    assert "api_key" not in payload
    assert payload["has_api_key"] is True
    assert payload["api_key_last4"] == secret[-4:]

    setting = Setting(
        key="telegram_bot_token",
        value=secret,
        category="telegram",
        updated_at=connection.created_at,
    )
    redacted = serialize_setting(setting)
    assert redacted["value"] == ""
    assert redacted["has_value"] is True
    assert redacted["value_last4"] == secret[-4:]


def test_connections_http_get_does_not_return_plaintext() -> None:
    secret = "sk-http-must-not-leak"
    db.create_connection(
        name="HTTP",
        api_base_url="http://127.0.0.1:9/v1",
        api_key=secret,
        model="test",
    )
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    token = db.ensure_local_api_token()
    client = TestClient(app)
    response = client.get("/api/connections", headers={LOCAL_API_TOKEN_HEADER: token})
    assert response.status_code == 200
    body = response.json()
    dumped = str(body)
    assert secret not in dumped
    assert body[0]["has_api_key"] is True
    assert body[0]["api_key_last4"] == secret[-4:]
