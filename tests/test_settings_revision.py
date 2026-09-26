"""Settings reach every process in real time through the settings_revision counter.

The database bumps ``settings_revision.rev`` on every write to ``settings``;
``config.refresh_if_changed`` reloads a process's cache when it moved. The
runtime worker calls it before each turn and each loop tick, the app at the
start of each HTTP request.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth, install_settings_refresh
from core import config
from core.agent_loop import standing_prefs
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.standing_prefs import StandingPref, render_warm_section


def _db_path() -> Path:
    return Path(os.environ["BOSSMOD_DB_PATH"])


def setup_function() -> None:
    db.close_connection()
    db_path = _db_path()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _rev() -> int:
    row = db.query_one("SELECT rev FROM settings_revision WHERE id = 1")
    assert row is not None
    return int(row["rev"])


def _write_on_a_separate_connection(sql: str, params: tuple[Any, ...]) -> None:
    # A raw connection of its own, as the other process would have.
    con = sqlite3.connect(_db_path())
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


# ── triggers ─────────────────────────────────────────────────────────────


def test_insert_update_and_delete_on_settings_each_bump_rev_by_one() -> None:
    start = _rev()
    db.execute(
        "INSERT INTO settings (key, value, category) VALUES ($1, $2, $3)",
        ["rev_probe", "a", "general"],
    )
    assert _rev() == start + 1
    db.execute("UPDATE settings SET value = $1 WHERE key = $2", ["b", "rev_probe"])
    assert _rev() == start + 2
    db.execute("DELETE FROM settings WHERE key = $1", ["rev_probe"])
    assert _rev() == start + 3


def test_set_setting_and_reset_setting_to_seed_bump_rev() -> None:
    # INSERT OR REPLACE: the REPLACE's implicit delete fires no trigger
    # (recursive_triggers is off), so each write is exactly one bump.
    start = _rev()
    db.set_setting("standing_prefs_line_max_chars", "120", "context")
    assert _rev() == start + 1
    db.reset_setting_to_seed("standing_prefs_line_max_chars")
    assert _rev() == start + 2


def test_init_db_on_an_existing_db_recreates_the_counter_and_triggers() -> None:
    # An existing DB from before this change has neither; the schema script
    # runs on every init_db, so it gains both without a migration.
    for trigger in (
        "settings_revision_after_insert",
        "settings_revision_after_update",
        "settings_revision_after_delete",
    ):
        db.execute(f"DROP TRIGGER {trigger}")
    db.execute("DROP TABLE settings_revision")
    db.init_db()
    names = {
        row["name"]
        for row in db.query("SELECT name FROM sqlite_master WHERE name LIKE 'settings_revision%'")
    }
    assert names == {
        "settings_revision",
        "settings_revision_after_insert",
        "settings_revision_after_update",
        "settings_revision_after_delete",
    }
    before = _rev()
    db.set_setting("tick_interval", "0.5", "simulation")
    assert _rev() > before


# ── refresh_if_changed ───────────────────────────────────────────────────


def test_refresh_is_false_without_a_change_and_true_after_a_write() -> None:
    assert config.refresh_if_changed() is False
    db.set_setting("tick_interval", "0.75", "simulation")
    assert config.refresh_if_changed() is True
    # No explicit reload(): the refresh loaded the new value.
    assert config.get("tick_interval") == "0.75"
    assert config.refresh_if_changed() is False


def test_a_write_on_a_separate_connection_is_picked_up() -> None:
    assert config.get("tick_interval") != "1.5"
    _write_on_a_separate_connection(
        "UPDATE settings SET value = ? WHERE key = ?",
        ("1.5", "tick_interval"),
    )
    assert config.get("tick_interval") != "1.5"  # the cache is stale until a refresh
    assert config.refresh_if_changed() is True
    assert config.get("tick_interval") == "1.5"


def test_a_missing_revision_row_raises() -> None:
    db.execute("DELETE FROM settings_revision WHERE id = 1")
    with pytest.raises(config.ConfigError, match="settings_revision row is missing"):
        config.refresh_if_changed()
    with pytest.raises(config.ConfigError, match="settings_revision row is missing"):
        config.reload()


# ── worker and app call sites ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_dispatcher_refreshes_settings_before_run_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    row = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "hello", "from_name": "Human"},
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    calls: list[str] = []

    def _refresh() -> bool:
        calls.append("refresh")
        return False

    async def _fake_turn(*_args: Any, **_kwargs: Any) -> TurnOutcome:
        calls.append("run_turn")
        return TurnOutcome(result={}, trigger_status="completed")

    monkeypatch.setattr(config, "refresh_if_changed", _refresh)
    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _fake_turn)
    payload = {
        "content": "hello",
        "from_name": "Human",
        "type": claimed.trigger_type,
        "trigger_id": claimed.id,
        "task_id": claimed.task_id,
        "source_channel": claimed.source_channel,
        "claim_generation": claimed.claim_generation,
    }
    state = db.get_agent_state(agent.id)
    assert state is not None
    await TurnDispatcher()._run_trigger(agent, state, payload)
    assert calls == ["refresh", "run_turn"]


def test_the_request_middleware_refreshes_once_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()

    @app.get("/api/ping")
    async def _ping() -> dict[str, str]:
        return {"ok": "yes"}

    install_local_api_auth(app)
    install_settings_refresh(app)
    calls: list[int] = []

    def _refresh() -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(config, "refresh_if_changed", _refresh)
    client = TestClient(app)
    headers = {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}
    assert client.get("/api/ping", headers=headers).status_code == 200
    assert len(calls) == 1
    assert client.get("/api/ping", headers=headers).status_code == 200
    assert len(calls) == 2


# ── end to end ───────────────────────────────────────────────────────────


def test_a_prefs_limit_written_on_a_second_connection_reaches_the_next_render() -> None:
    pref = StandingPref(id="tone", kind="style", text="t" * 200, sources=["operator"])
    before = render_warm_section([pref])
    assert before is not None
    assert before.splitlines()[1] == f"- style tone — {'t' * 200} sources: operator"

    # db.set_setting on another thread uses that thread's own connection.
    writer = threading.Thread(
        target=db.set_setting, args=("standing_prefs_line_max_chars", "100", "context")
    )
    writer.start()
    writer.join()

    assert config.refresh_if_changed() is True
    assert standing_prefs.line_max_chars() == 100
    after = render_warm_section([pref])
    assert after is not None
    assert after.splitlines()[1] == f"- style tone — {'t' * 97}..."
