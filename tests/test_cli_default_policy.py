"""cli_default_policy seed is approval_required; untouched factory deny moves; custom stays."""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from db.settings import (
    _CLI_DEFAULT_POLICY_FACTORY_RECONCILED,
    get_seed_setting_default,
    reconcile_factory_cli_default_policy,
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


def test_cli_default_policy_seed_is_approval_required() -> None:
    seeded, category = get_seed_setting_default("cli_default_policy")
    assert seeded == "approval_required"
    assert category == "cli_policy"
    assert config.get("cli_default_policy") == "approval_required"
    settings_js = Path("ui/static/js/settings/cli-policy/policy-settings.js").read_text(
        encoding="utf-8"
    )
    assert "cli_default_policy" in settings_js
    assert "Default Policy" in settings_js
    assert "Default approval_required" in settings_js
    assert "Deny is the default" not in settings_js
    assert "Default deny" not in settings_js
    assert "Default is Deny" not in settings_js
    policy_source = Path("core/bm_cli/policy_engine.py").read_text(encoding="utf-8")
    assert 'config.get("cli_default_policy") or "approval_required"' in policy_source
    assert 'or "deny"' not in policy_source
    help_source = Path("core/bm_cli/help_commands.py").read_text(encoding="utf-8")
    assert 'config.get("cli_default_policy") or "approval_required"' in help_source
    assert 'or "deny"' not in help_source
    simulator_js = Path("ui/static/js/settings/cli-policy/simulator.js").read_text(
        encoding="utf-8"
    )
    assert "simDefaultPolicy = 'approval_required'" in simulator_js
    assert "s.value || 'approval_required'" in simulator_js
    assert "|| 'deny'" not in simulator_js


def test_factory_cli_default_policy_bump_leaves_a_custom_value() -> None:
    assert config.get("cli_default_policy") == "approval_required"
    db.execute(
        "DELETE FROM settings WHERE key = $1",
        [_CLI_DEFAULT_POLICY_FACTORY_RECONCILED],
    )
    db.set_setting("cli_default_policy", "deny", "cli_policy")
    reconcile_factory_cli_default_policy()
    config.reload()
    assert config.get("cli_default_policy") == "approval_required"
    db.set_setting("cli_default_policy", "deny", "cli_policy")
    reconcile_factory_cli_default_policy()
    config.reload()
    assert config.get("cli_default_policy") == "deny"
    db.execute(
        "DELETE FROM settings WHERE key = $1",
        [_CLI_DEFAULT_POLICY_FACTORY_RECONCILED],
    )
    db.set_setting("cli_default_policy", "custom", "cli_policy")
    reconcile_factory_cli_default_policy()
    config.reload()
    assert config.get("cli_default_policy") == "custom"
