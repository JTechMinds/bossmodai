"""SEC-P0-02 — Secret redaction and local API token gate."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config


TELEGRAM_TOKEN = "123456:TELEGRAM-SECRET-wxyz"
CONNECTION_KEY = "sk-test-SECRETVALUE-abcd"


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


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return app


def _client() -> TestClient:
    return TestClient(_app())


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def test_settings_and_connections_redact_secrets() -> None:
    db.set_setting("telegram_bot_token", TELEGRAM_TOKEN, "telegram")
    conn = db.create_connection(
        name="Test Provider",
        api_base_url="https://api.example.test/v1",
        api_key=CONNECTION_KEY,
        model="demo-model",
    )
    config.reload()

    client = _client()
    headers = _auth_headers()

    settings_res = client.get("/api/settings", headers=headers)
    assert settings_res.status_code == 200
    settings_body = settings_res.text
    assert TELEGRAM_TOKEN not in settings_body
    assert "TELEGRAM-SECRET" not in settings_body
    token_row = next(item for item in settings_res.json() if item["key"] == "telegram_bot_token")
    assert token_row["value"] == ""
    assert token_row["has_value"] is True
    assert token_row["value_last4"] == "wxyz"
    assert all(item["key"] != "local_api_token" for item in settings_res.json())

    connections_res = client.get("/api/connections", headers=headers)
    assert connections_res.status_code == 200
    assert CONNECTION_KEY not in connections_res.text
    assert "SECRETVALUE" not in connections_res.text
    listed = connections_res.json()
    assert len(listed) == 1
    assert listed[0]["has_api_key"] is True
    assert listed[0]["api_key_last4"] == "abcd"
    assert "api_key" not in listed[0]

    one = client.get(f"/api/connections/{conn.id}", headers=headers)
    assert one.status_code == 200
    assert CONNECTION_KEY not in one.text
    assert one.json()["has_api_key"] is True
    assert one.json()["api_key_last4"] == "abcd"

    created = client.post(
        "/api/connections",
        headers=headers,
        json={
            "name": "After Write",
            "api_base_url": "https://api.example.test/v1",
            "api_key": "sk-after-write-SECRET-zz99",
            "model": "demo-model",
        },
    )
    assert created.status_code == 201
    assert "sk-after-write-SECRET-zz99" not in created.text
    assert created.json()["has_api_key"] is True
    assert created.json()["api_key_last4"] == "zz99"


def test_unauthenticated_destructive_route_is_rejected() -> None:
    client = _client()
    res = client.post("/api/settings/reseed")
    assert res.status_code == 401
    assert "token" in res.json()["detail"].lower()


def test_authenticated_destructive_route_is_allowed() -> None:
    client = _client()
    res = client.post("/api/settings/reseed", headers=_auth_headers())
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
