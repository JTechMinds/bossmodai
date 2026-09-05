"""HA-SEC-NEW-01 — connection-test URL allowlist."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.llm.connection_url import ConnectionUrlError, validate_connection_test_url


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
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://169.254.1.1/",
        "http://10.0.0.5/v1",
        "http://example.com/v1",
        "https://169.254.169.254/",
        "http://metadata.google.internal/",
        "ftp://127.0.0.1/v1",
    ],
)
def test_validate_rejects_ssrf_and_plain_http(url: str) -> None:
    with pytest.raises(ConnectionUrlError):
        validate_connection_test_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234/v1",
        "http://[::1]:11434/v1",
        "https://127.0.0.1:8443/v1",
    ],
)
def test_validate_allows_https_and_loopback(url: str) -> None:
    assert validate_connection_test_url(url) == url


def test_api_rejects_metadata_ip_before_fetch() -> None:
    client = _client()
    res = client.post(
        "/api/connections/test",
        headers=_auth_headers(),
        json={"api_base_url": "http://169.254.169.254/latest"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "not allowed" in body["error"].lower() or "link-local" in body["error"].lower()


def test_api_rejects_loopback_discard_port_without_hanging() -> None:
    """http://127.0.0.1:9 is loopback-allowed, then fails closed on connect."""
    client = _client()
    res = client.post(
        "/api/connections/test",
        headers=_auth_headers(),
        json={"api_base_url": "http://127.0.0.1:9"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body.get("error")
