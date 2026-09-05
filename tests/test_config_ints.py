"""HA-CORR-P2-01 — config.get_int / get_float must not crash on garbage."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config


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


def test_get_int_returns_none_on_non_integer() -> None:
    db.set_setting("tick_interval", "nope", "simulation")
    config.reload()
    assert config.get_int("tick_interval") is None


def test_get_float_returns_none_on_non_float() -> None:
    db.set_setting("tick_interval", "nope", "simulation")
    config.reload()
    assert config.get_float("tick_interval") is None


def test_get_int_and_float_still_parse_valid_values() -> None:
    db.set_setting("tick_interval", "3.5", "simulation")
    db.set_setting("watchdog_soft_ping_minutes", "15", "watchdog")
    config.reload()
    assert config.get_float("tick_interval") == 3.5
    assert config.get_int("watchdog_soft_ping_minutes") == 15


def test_require_int_raises_config_error_not_value_error() -> None:
    db.set_setting("default_spawn_x", "nope", "simulation")
    config.reload()
    try:
        config.require_int("default_spawn_x")
    except config.ConfigError as exc:
        assert "not an integer" in str(exc)
    else:
        raise AssertionError("expected ConfigError")
