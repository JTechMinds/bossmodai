"""BossMod AI — Settings CRUD and seed data."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.agent_loop.action_contract import render_action_contract
from core.models import Setting
from db.crud import execute, fetch_all, query_one

logger = logging.getLogger(__name__)

# Settings that must exist for the application to function.
# Format: (key, value, category)
_SEED_SETTINGS: list[tuple[str, str, str]] = [
    # ── Simulation ──
    ("tick_interval", "3", "simulation"),
    ("steps_per_tick", "1", "simulation"),

    # ── Social triggers ──
    ("social_idle_threshold_minutes", "5", "social"),
    ("social_cooldown_minutes", "15", "social"),
    ("social_proximity_tiles", "8", "social"),

    # ── LLM defaults (empty = user must configure) ──
    ("default_model_social", "", "llm"),
    ("default_model_work", "", "llm"),
    ("default_model_reasoning", "", "llm"),
    ("default_model_extraction", "", "llm"),
    ("default_model_self_queue", "", "llm"),
    ("default_temperature", "0.7", "llm"),
    ("default_max_tokens", "2048", "llm"),

    # ── Context window ──
    ("context_window_messages", "30", "context"),
    ("context_recent_work_artifacts", "5", "context"),
    ("context_recent_completed_tasks", "3", "context"),

    # ── Diagnostics ──
    ("diagnostics_enabled", "false", "advanced"),
    ("diagnostics_retention_limit", "5000", "advanced"),

    # ── Concurrency ──
    ("max_concurrent_llm_calls", "5", "llm"),

    # ── Simulation resilience ──
    ("sim_error_threshold", "10", "simulation"),
    ("sim_error_backoff_seconds", "30", "simulation"),

    # ── Watchdog ──
    ("watchdog_check_interval_seconds", "5", "simulation"),
    ("watchdog_soft_ping_minutes", "15", "simulation"),
    ("watchdog_escalation_minutes", "15", "simulation"),

    # ── WebSocket ──
    ("ws_send_timeout_seconds", "5", "advanced"),

    # ── API limits ──
    ("api_message_limit_max", "200", "advanced"),
    ("api_diagnostics_limit_max", "200", "advanced"),

    # ── System prompt template (advanced) ──
    ("system_prompt_template", """{{personality}}

{{memory}}

{{worldStatus}}

{{task}}

{{references}}

---

{{action_contract}}""", "advanced"),
    ("action_contract_template", render_action_contract(), "advanced"),
]


def seed_defaults() -> None:
    """Populate settings that don't yet exist. Never overwrites user values."""
    for key, value, category in _SEED_SETTINGS:
        existing = query_one("SELECT key FROM settings WHERE key = $1", [key])
        if existing is None:
            now = datetime.now(timezone.utc)
            execute(
                "INSERT INTO settings (key, value, category, updated_at) "
                "VALUES ($1, $2, $3, $4)",
                [key, value, category, now],
            )
    logger.info("Settings seeded (%d keys)", len(_SEED_SETTINGS))


def force_reseed() -> None:
    """Overwrite ALL seed settings back to their defaults."""
    now = datetime.now(timezone.utc)
    for key, value, category in _SEED_SETTINGS:
        execute(
            "INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
            "VALUES ($1, $2, $3, $4)",
            [key, value, category, now],
        )
    logger.info("Settings force-reseeded (%d keys)", len(_SEED_SETTINGS))


def get_settings(category: str | None = None) -> list[Setting]:
    """Return settings, optionally filtered by category."""
    if category is not None:
        return fetch_all(
            "SELECT key, value, category, updated_at FROM settings "
            "WHERE category = $1 ORDER BY key",
            [category],
            Setting,
        )
    return fetch_all(
        "SELECT key, value, category, updated_at FROM settings ORDER BY key",
        model_cls=Setting,
    )


def set_setting(key: str, value: str, category: str = "general") -> Setting:
    """Insert or update a setting."""
    now = datetime.now(timezone.utc)
    execute(
        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
        "VALUES ($1, $2, $3, $4)",
        [key, value, category, now],
    )
    return Setting(key=key, value=value, category=category, updated_at=now)
