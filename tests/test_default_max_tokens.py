"""default_max_tokens seed is 16k; untouched factory 8k is raised; custom stays."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from db.settings import get_seed_setting_default, reconcile_factory_max_tokens


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


def test_default_max_tokens_seed_is_16384() -> None:
    seeded, category = get_seed_setting_default("default_max_tokens")
    assert seeded == "16384"
    assert category == "llm"
    assert config.get("default_max_tokens") == "16384"
    settings_js = Path("ui/static/js/settings/settings-system.js").read_text(encoding="utf-8")
    assert "default_max_tokens" in settings_js
    assert "Default Max Completion Tokens" in settings_js
    assert "Default 16384" in settings_js
    client_source = Path("core/llm/client.py").read_text(encoding="utf-8")
    assert 'config.get_int("default_max_tokens") or 16384' in client_source
    assert "or 8192" not in client_source


def test_factory_max_tokens_bump_leaves_a_custom_value() -> None:
    assert config.get("default_max_tokens") == "16384"
    db.set_setting("default_max_tokens", "8192", "llm")
    reconcile_factory_max_tokens()
    config.reload()
    assert config.get("default_max_tokens") == "16384"
    db.set_setting("default_max_tokens", "4096", "llm")
    reconcile_factory_max_tokens()
    config.reload()
    assert config.get("default_max_tokens") == "4096"
