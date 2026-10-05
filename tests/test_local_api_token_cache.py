"""Phase 3 (refresh efficiency): the API token is checked against the settings cache.

``tokens_match`` used to read and decrypt the token from the database on
every ``/api`` request. It now reads the process settings cache, which
``SettingsRefreshMiddleware`` (HTTP) and ``websocket_authorized`` (WebSocket)
refresh from the ``settings_revision`` counter first, so a token written by
any process still applies to the very next request.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import db
from api.auth import (
    LOCAL_API_TOKEN_ENV,
    LOCAL_API_TOKEN_HEADER,
    LOCAL_API_TOKEN_KEY,
    current_local_api_token,
    install_local_api_auth,
    install_settings_refresh,
)
from api.routes import router
from core import config
from db.connection import SQLiteCompatConnection
from db.crud import execute


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


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    install_settings_refresh(app)
    return TestClient(app)


def _rotate(token: str) -> None:
    """Write a new token the way another process would: no reload here."""
    db.set_setting(LOCAL_API_TOKEN_KEY, token, "security")


def test_a_request_reads_no_token_row(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    token = db.ensure_local_api_token()
    sent: list[str] = []
    original = SQLiteCompatConnection.execute

    def counting(self, sql, params=None):
        sent.append(" ".join(str(sql).split()))
        return original(self, sql, params)

    monkeypatch.setattr(SQLiteCompatConnection, "execute", counting)
    res = client.get("/api/floors", headers={LOCAL_API_TOKEN_HEADER: token})

    assert res.status_code == 200, res.text
    assert not any("FROM settings WHERE key" in sql for sql in sent), sent


def test_a_rotated_token_applies_to_the_next_request() -> None:
    client = _client()
    old = db.ensure_local_api_token()
    assert client.get("/api/floors", headers={LOCAL_API_TOKEN_HEADER: old}).status_code == 200

    _rotate("rotated-token-value")

    assert client.get("/api/floors", headers={LOCAL_API_TOKEN_HEADER: old}).status_code == 401
    assert client.get("/api/floors", headers={LOCAL_API_TOKEN_HEADER: "rotated-token-value"}).status_code == 200


def test_a_rotated_token_applies_to_the_next_websocket() -> None:
    client = _client()
    old = db.ensure_local_api_token()
    with client.websocket_connect(f"/api/ws?token={old}") as ws:
        assert ws.receive_json()["type"] == "world_update"

    _rotate("rotated-token-value")

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"/api/ws?token={old}") as ws:
            ws.receive_json()
    assert refused.value.code == 4401
    with client.websocket_connect("/api/ws?token=rotated-token-value") as ws:
        assert ws.receive_json()["type"] == "world_update"


def test_a_wrong_or_missing_token_is_refused() -> None:
    client = _client()
    assert client.get("/api/floors").status_code == 401
    assert client.get("/api/floors", headers={LOCAL_API_TOKEN_HEADER: "nope"}).status_code == 401


def test_the_environment_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOCAL_API_TOKEN_ENV, "from-the-environment")
    assert current_local_api_token() == "from-the-environment"


def test_a_missing_token_setting_is_an_error_not_an_empty_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOCAL_API_TOKEN_ENV, raising=False)
    execute("DELETE FROM settings WHERE key = $1", [LOCAL_API_TOKEN_KEY])
    config.reload()
    with pytest.raises(config.ConfigError):
        current_local_api_token()
