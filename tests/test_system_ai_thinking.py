"""System AI thinking level: the setting, its merge, and its API guards."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.config import ConfigError
from core.llm.system_completion import complete_text
from db.crud import execute
from db.settings import get_seed_setting_default

LEVELS = {
    "low": {"reasoning_effort": "low"},
    "high": {"reasoning_effort": "high", "chat_template_kwargs": {"enable_thinking": True}},
}


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


class _Message:
    content = "ok"


class _Choice:
    message = _Message()
    finish_reason = "stop"


class _Response:
    choices = [_Choice()]


def _connection(name: str, *, levels: dict | None = None, extra_body: str | None = None) -> str:
    return db.create_connection(
        name=name,
        api_base_url="http://127.0.0.1:9/v1",
        model="mock-small",
        extra_body=extra_body,
        thinking_levels=levels,
    ).id


def _set(key: str, value: str) -> None:
    db.set_setting(key, value, "llm")
    config.reload()


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def _fake(**kwargs: object) -> _Response:
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake)
    return calls


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _put(key: str, value: str):
    return _client().put(f"/api/settings/{key}", params={"value": value, "category": "llm"})


def _stored(key: str) -> str:
    return next(row.value for row in db.get_settings() if row.key == key)


# ─── Seed and complete_text ───


def test_system_ai_thinking_is_seeded_as_server_default() -> None:
    assert get_seed_setting_default("system_ai_thinking") == ("default", "llm")
    assert config.get("system_ai_thinking") == "default"


def test_default_sends_the_stored_extra_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _connection("Local", levels=LEVELS, extra_body='{"top_k": 20}')
    calls = _record_calls(monkeypatch)
    assert complete_text([{"role": "user", "content": "route"}]) == "ok"
    assert calls[0]["extra_body"] == {"top_k": 20}


def test_a_level_merges_its_fragment_over_the_extra_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _connection("Local", levels=LEVELS, extra_body='{"top_k": 20, "chat_template_kwargs": {"x": 1}}')
    _set("system_ai_thinking", "high")
    calls = _record_calls(monkeypatch)
    assert complete_text([{"role": "user", "content": "route"}]) == "ok"
    assert calls[0]["extra_body"] == {
        "top_k": 20,
        "reasoning_effort": "high",
        "chat_template_kwargs": {"x": 1, "enable_thinking": True},
    }


def test_an_unoffered_level_warns_and_skips_the_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _connection("Bare Local")
    _set("system_ai_thinking", "low")
    calls = _record_calls(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="core.llm.system_completion"):
        assert complete_text([{"role": "user", "content": "route"}]) is None
    assert calls == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Bare Local" in line and "'low'" in line for line in warnings), warnings


def test_a_missing_system_ai_thinking_setting_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _connection("Local")
    execute("DELETE FROM settings WHERE key = $1", ["system_ai_thinking"])
    config.reload()
    calls = _record_calls(monkeypatch)
    with pytest.raises(ConfigError, match="system_ai_thinking"):
        complete_text([{"role": "user", "content": "route"}])
    assert calls == []


# ─── Settings API ───


@pytest.mark.parametrize("value", ["", "turbo", "Low"])
def test_an_invalid_system_ai_thinking_is_a_400(value: str) -> None:
    _connection("Local", levels=LEVELS)
    response = _put("system_ai_thinking", value)
    assert response.status_code == 400, response.text
    assert "System AI thinking must be one of" in response.json()["detail"]
    assert _stored("system_ai_thinking") == "default"


def test_an_unoffered_system_ai_thinking_is_a_400() -> None:
    _connection("Local", levels=LEVELS)
    response = _put("system_ai_thinking", "medium")
    assert response.status_code == 400, response.text
    assert "'Local' does not offer thinking level 'medium'" in response.json()["detail"]
    assert _stored("system_ai_thinking") == "default"


def test_a_level_without_a_usable_system_ai_is_a_400_but_default_is_not() -> None:
    response = _put("system_ai_thinking", "low")
    assert response.status_code == 400, response.text
    assert "no usable System AI connection" in response.json()["detail"]
    assert _put("system_ai_thinking", "default").status_code == 200


def test_system_ai_thinking_round_trips() -> None:
    first = _connection("Alpha", levels=LEVELS)
    _connection("Zed")
    # Unset System AI uses the first connection by name, which offers "high".
    response = _put("system_ai_thinking", "high")
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "high"
    assert _stored("system_ai_thinking") == "high"
    assert config.get("system_ai_thinking") == "high"
    listed = {row["key"]: row["value"] for row in _client().get("/api/settings").json()}
    assert listed["system_ai_thinking"] == "high"
    assert _put("system_ai_connection", first).status_code == 200


def test_switching_to_a_connection_without_the_level_is_a_409() -> None:
    offered = _connection("Alpha", levels=LEVELS)
    bare = _connection("Zed")
    _set("system_ai_connection", offered)
    assert _put("system_ai_thinking", "low").status_code == 200
    response = _put("system_ai_connection", bare)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "'low'" in detail and "'Zed'" in detail
    assert "Server default or an offered level first" in detail
    assert _stored("system_ai_connection") == offered
    assert _put("system_ai_thinking", "default").status_code == 200
    assert _put("system_ai_connection", bare).status_code == 200


def test_patching_away_the_system_ai_level_is_a_409() -> None:
    offered = _connection("Alpha", levels=LEVELS)
    _set("system_ai_connection", offered)
    assert _put("system_ai_thinking", "high").status_code == 200
    response = _client().patch(f"/api/connections/{offered}", json={"thinking_levels": {"low": LEVELS["low"]}})
    assert response.status_code == 409, response.text
    assert "System AI (system_ai_thinking: high)" in response.json()["detail"]
    kept = db.get_connection_by_id(offered)
    assert kept is not None and kept.thinking_levels is not None and "high" in kept.thinking_levels
    # Dropping a level System AI does not pick is allowed.
    allowed = _client().patch(f"/api/connections/{offered}", json={"thinking_levels": {"high": LEVELS["high"]}})
    assert allowed.status_code == 200, allowed.text


def test_patching_another_connection_ignores_the_system_ai_level() -> None:
    offered = _connection("Alpha", levels=LEVELS)
    other = _connection("Zed", levels=LEVELS)
    _set("system_ai_connection", offered)
    assert _put("system_ai_thinking", "high").status_code == 200
    response = _client().patch(f"/api/connections/{other}", json={"thinking_levels": {}})
    assert response.status_code == 200, response.text


def test_system_ai_thinking_repaints_the_connections_surface() -> None:
    from api.routes.settings import _operator_surfaces_for_setting

    assert _operator_surfaces_for_setting("system_ai_thinking", "llm") == ["connections"]

