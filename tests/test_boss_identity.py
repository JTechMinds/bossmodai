"""The human user's one identity: "Boss", or "<Name> (the boss)" (core/boss.py).

Agents see the boss label on every human line, resolved at read time by
author type, never from the stored name. The name is a settings row,
validated at the API, and live on the next config refresh.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import db.settings as settings_db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import boss, config
from core.agent_loop.channel_rounds import router_transcript
from core.agent_repository import agent_repository
from core.default_prompts import load_default_prompt


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
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _connection_id() -> str:
    existing = db.list_connections()
    if existing:
        return existing[0].id
    return db.create_connection(name="Qwen", api_base_url="http://127.0.0.1:9/v1", model="qwen").id


def _set_name(value: str) -> None:
    db.set_setting("boss_name", value, "profile")
    config.refresh_if_changed()


# ── label, mention, mention names ─────────────────────────────────────────


def test_fresh_database_seeds_an_unset_name_and_an_unanswered_prompt() -> None:
    rows = {row.key: row for row in db.get_settings(category="profile")}
    assert rows["boss_name"].value == ""
    assert rows["boss_name_prompted"].value == "false"


def test_unset_name_reads_as_boss() -> None:
    assert boss.boss_name() is None
    assert boss.boss_label() == "Boss"
    assert boss.boss_mention() == "@Boss"
    assert boss.boss_mention_names() == ("Boss",)


def test_a_set_name_reads_as_name_the_boss() -> None:
    _set_name("  Jordan  ")
    assert boss.boss_name() == "Jordan"
    assert boss.boss_label() == "Jordan (the boss)"
    assert boss.boss_mention() == "@Jordan"
    assert boss.boss_mention_names() == ("Boss", "Jordan")


def test_a_missing_row_raises_instead_of_defaulting() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", ["boss_name"])
    config.reload()
    with pytest.raises(config.ConfigError):
        boss.boss_label()


def test_a_rename_is_live_on_the_next_refresh() -> None:
    assert boss.boss_label() == "Boss"
    db.set_setting("boss_name", "Jordan", "profile")
    assert config.refresh_if_changed() is True
    assert boss.boss_label() == "Jordan (the boss)"


# ── validate_boss_name ────────────────────────────────────────────────────


def test_validation_trims_and_allows_empty() -> None:
    assert boss.validate_boss_name("  Jordan ", agent_names=[]) == "Jordan"
    assert boss.validate_boss_name("   ", agent_names=[]) == ""


@pytest.mark.parametrize(
    "value",
    [
        "J" * (boss.BOSS_NAME_MAX_LENGTH + 1),
        "Jor\ndan",
        "Jor\rdan",
        "Jor\tdan",
        "Jor\x00dan",
        "@Jordan",
        "jor@dan",
        "Boss",
        "BOSS",
        "everyone",
        "All",
    ],
)
def test_validation_refuses(value: str) -> None:
    with pytest.raises(ValueError):
        boss.validate_boss_name(value, agent_names=[])


def test_validation_refuses_an_existing_agent_name_case_insensitively() -> None:
    with pytest.raises(ValueError, match="already named"):
        boss.validate_boss_name("ada", agent_names=["Ada", "Bob"])


def test_validation_allows_the_longest_name() -> None:
    name = "J" * boss.BOSS_NAME_MAX_LENGTH
    assert boss.validate_boss_name(name, agent_names=[]) == name


def test_settings_api_stores_the_trimmed_name() -> None:
    client = _client()
    response = client.put("/api/settings/boss_name", params={"value": "  Jordan  ", "category": "profile"})
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "Jordan"
    assert boss.boss_label() == "Jordan (the boss)"


def test_settings_api_clears_the_name_with_an_empty_value() -> None:
    _set_name("Jordan")
    client = _client()
    response = client.put("/api/settings/boss_name", params={"value": "", "category": "profile"})
    assert response.status_code == 200, response.text
    assert boss.boss_label() == "Boss"


def test_settings_api_refuses_an_agent_name_with_400() -> None:
    db.create_agent(name="Ada", connection_id=_connection_id())
    client = _client()
    response = client.put("/api/settings/boss_name", params={"value": "ADA", "category": "profile"})
    assert response.status_code == 400
    assert "already named" in response.json()["detail"]
    assert boss.boss_name() is None


def test_settings_api_refuses_a_non_boolean_prompted_flag() -> None:
    client = _client()
    response = client.put("/api/settings/boss_name_prompted", params={"value": "yes", "category": "profile"})
    assert response.status_code == 400
    ok = client.put("/api/settings/boss_name_prompted", params={"value": "true", "category": "profile"})
    assert ok.status_code == 200, ok.text


# ── read-time resolution of stored human rows ─────────────────────────────


def _thread_with_legacy_human_line() -> str:
    agent = db.create_agent(name="Ada", connection_id=_connection_id())
    channel = db.create_channel(name="Planning", member_agent_ids=[agent.id], created_by="__human__")
    db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Please draft the plan.",
        source_channel="channel",
    )
    db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_name="Ada",
        author_agent_id=agent.id,
        content="On it.",
        source_channel="channel",
    )
    return channel.id


def test_a_stored_human_name_renders_as_the_current_label_in_thread_history() -> None:
    channel_id = _thread_with_legacy_human_line()
    _set_name("Jordan")
    rows = db.get_formatted_channel_messages(channel_id, human_label=boss.boss_label())
    names = [row["from_name"] for row in rows]
    assert names == ["Jordan (the boss)", "Ada"]


def test_a_stored_human_name_renders_as_the_current_label_for_the_router() -> None:
    channel_id = _thread_with_legacy_human_line()
    assert [line.author for line in router_transcript(channel_id, exclude_message_id=None)] == ["Boss", "Ada"]
    _set_name("Jordan")
    assert [line.author for line in router_transcript(channel_id, exclude_message_id=None)] == [
        "Jordan (the boss)",
        "Ada",
    ]


# ── reconcile_boss_prompt_wording ─────────────────────────────────────────


def _rerun_reconcile() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", ["boss_prompt_wording_reconciled"])
    settings_db.reconcile_boss_prompt_wording()


def test_reconcile_overwrites_only_a_row_holding_the_prior_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    prior = "Prior shipped decision contract: ask the operator."
    edited = "An edited execution contract."
    monkeypatch.setattr(
        settings_db,
        "_BOSS_WORDING_PRIOR_DEFAULT_SHA256",
        {
            "runtime_contract_decision": hashlib.sha256(prior.encode("utf-8")).hexdigest(),
            "runtime_contract_execution": hashlib.sha256(b"something else").hexdigest(),
            "runtime_block_conversation_envelope": hashlib.sha256(b"another").hexdigest(),
        },
    )
    db.set_setting("runtime_contract_decision", prior, "advanced")
    db.set_setting("runtime_contract_execution", edited, "advanced")
    # runtime_block_conversation_envelope keeps its seeded (current) default.

    with caplog.at_level(logging.WARNING, logger="db.settings"):
        _rerun_reconcile()

    stored = {row.key: row.value for row in db.get_settings(category="advanced")}
    assert stored["runtime_contract_decision"] == load_default_prompt("runtime_contract_decision")
    assert stored["runtime_contract_execution"] == edited
    assert stored["runtime_block_conversation_envelope"] == load_default_prompt("runtime_block_conversation_envelope")
    warned = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("runtime_contract_execution" in message for message in warned)
    assert not any("runtime_contract_decision" in message for message in warned)
    assert not any("runtime_block_conversation_envelope" in message for message in warned)
    assert stored["boss_prompt_wording_reconciled"] == "true"


def test_reconcile_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    prior = "Prior text."
    monkeypatch.setattr(
        settings_db,
        "_BOSS_WORDING_PRIOR_DEFAULT_SHA256",
        {"runtime_contract_decision": hashlib.sha256(prior.encode("utf-8")).hexdigest()},
    )
    # init_db already recorded the marker; a later pass must not touch the row.
    db.set_setting("runtime_contract_decision", prior, "advanced")
    settings_db.reconcile_boss_prompt_wording()
    assert db.query_one(
        "SELECT value FROM settings WHERE key = $1", ["runtime_contract_decision"],
    )["value"] == prior


def test_new_defaults_differ_from_the_recorded_prior_defaults() -> None:
    for key, prior_sha in settings_db._BOSS_WORDING_PRIOR_DEFAULT_SHA256.items():
        current = hashlib.sha256(load_default_prompt(key).encode("utf-8")).hexdigest()
        assert current != prior_sha, key
        assert settings_db.get_seed_setting_default(key) is not None, key


# ── agent names may not collide with the boss ─────────────────────────────


@pytest.mark.parametrize("name", ["Boss", "boss", " BOSS "])
def test_hiring_boss_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="reserved for you"):
        agent_repository.create(name=name, connection_id=_connection_id())


def test_hiring_or_renaming_to_the_boss_name_is_refused() -> None:
    _set_name("Jordan")
    with pytest.raises(ValueError, match="reserved for you"):
        agent_repository.create(name="jordan", connection_id=_connection_id())
    agent = agent_repository.create(name="Ada", connection_id=_connection_id())
    client = _client()
    response = client.patch(f"/api/agents/{agent.id}", json={"name": "JORDAN"})
    assert response.status_code == 400
    assert "reserved for you" in response.json()["detail"]
    assert db.get_agent(agent.id).name == "Ada"


def test_the_boss_name_is_free_for_agents_while_unset() -> None:
    agent = agent_repository.create(name="Jordan", connection_id=_connection_id())
    assert agent.name == "Jordan"


def test_hire_api_refuses_boss_with_400() -> None:
    client = _client()
    response = client.post("/api/agents", json={"name": "Boss", "connection_id": _connection_id()})
    assert response.status_code == 400
    assert "reserved for you" in response.json()["detail"]


# ── what the agent's prompt reads ─────────────────────────────────────────


def test_the_prompt_preview_names_the_boss() -> None:
    from core.llm.context_preview import preview_prompt_bundle

    _set_name("Jordan")
    rendered = preview_prompt_bundle("decision", "human_chat")["rendered"]
    assert "You work for Jordan (the boss) — the boss. Tag them as @Jordan." in rendered
    assert "CURRENT REQUEST FROM [Jordan (the boss)]" in rendered
    assert "Human Operator" not in rendered
    assert "@Operator" not in rendered
