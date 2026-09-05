"""HA-LOOP-P1-07 — Seed meeting watchdog settings and read them via config."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop.meeting_watchdog import (
    MEETING_INVITE_ACCEPT_TIMEOUT_FALLBACK,
    MEETING_INVITE_ARRIVAL_TIMEOUT_FALLBACK,
    MEETING_WATCHDOG_CHECK_INTERVAL_FALLBACK,
    read_meeting_watchdog_settings,
)
from db.settings import get_seed_setting_default


MEETING_WATCHDOG_KEYS = (
    "meeting_watchdog_check_interval_seconds",
    "meeting_invite_accept_timeout_seconds",
    "meeting_invite_arrival_timeout_seconds",
)


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


def test_fresh_db_contains_meeting_watchdog_seed_keys() -> None:
    settings = {row.key: row for row in db.get_settings()}
    for key in MEETING_WATCHDOG_KEYS:
        seeded = get_seed_setting_default(key)
        assert seeded is not None
        assert key in settings
        assert settings[key].value == seeded[0]
        assert settings[key].category == "simulation"


def test_meeting_watchdog_fallbacks_match_seed_defaults() -> None:
    interval_seed, _ = get_seed_setting_default("meeting_watchdog_check_interval_seconds")
    accept_seed, _ = get_seed_setting_default("meeting_invite_accept_timeout_seconds")
    arrival_seed, _ = get_seed_setting_default("meeting_invite_arrival_timeout_seconds")
    assert float(interval_seed) == MEETING_WATCHDOG_CHECK_INTERVAL_FALLBACK
    assert int(accept_seed) == MEETING_INVITE_ACCEPT_TIMEOUT_FALLBACK
    assert int(arrival_seed) == MEETING_INVITE_ARRIVAL_TIMEOUT_FALLBACK


def test_watchdog_reads_updated_settings_via_config() -> None:
    db.set_setting("meeting_watchdog_check_interval_seconds", "7.5", "simulation")
    db.set_setting("meeting_invite_accept_timeout_seconds", "45", "simulation")
    db.set_setting("meeting_invite_arrival_timeout_seconds", "120", "simulation")
    config.reload()

    interval, accept_timeout, arrival_timeout = read_meeting_watchdog_settings()
    assert interval == 7.5
    assert accept_timeout == 45
    assert arrival_timeout == 120


def test_watchdog_fallbacks_apply_when_keys_missing() -> None:
    for key in MEETING_WATCHDOG_KEYS:
        db.execute("DELETE FROM settings WHERE key = $1", [key])
    config.reload()

    interval, accept_timeout, arrival_timeout = read_meeting_watchdog_settings()
    assert interval == MEETING_WATCHDOG_CHECK_INTERVAL_FALLBACK
    assert accept_timeout == MEETING_INVITE_ACCEPT_TIMEOUT_FALLBACK
    assert arrival_timeout == MEETING_INVITE_ARRIVAL_TIMEOUT_FALLBACK


def test_settings_ui_exposes_meeting_watchdog_rows() -> None:
    settings_js = (Path(__file__).resolve().parents[1] / "ui/static/js/settings-view.js").read_text(
        encoding="utf-8"
    )
    for key in MEETING_WATCHDOG_KEYS:
        assert f"{key}:" in settings_js
