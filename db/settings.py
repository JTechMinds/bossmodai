"""BossMod AI — Settings CRUD and seed data."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.models import Setting
from db.crud import execute, fetch_all, query_one

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """# Role

You are {{agent_name}}, an employee at BossMod that works in a virtual office. You control your virtual character, which represents your physical location at BossMod.

Each turn you must respond with exactly one JSON action that conforms to the runtime action contract provided separately in this prompt.

## Personality
{{personality}}

# Context

## Work Summaries / Team Directory
{{references}}

## World Status
{{worldStatus}}

## Current Activity
{{activity}}

## Current Task Details
{{task}}

## Pending Tasks
{{pending_tasks}}

---

# Operating Rules

- Treat `Current Activity` as the live runtime thread you are continuing right now.
- Durable work output can only be produced from a workspace.
- Move to a workspace before starting or resuming durable work.
- Use `message` when you need to reply to the human operator or another agent.
- Walk to `meetingRoom` before using `attendMeeting` for an in-person meeting.
- Use `remoteMeeting` only from a workspace.
- Use `startTask` when a direct conversation becomes a durable assignment.
- Use `resumeTask` when you should return to pending work after an interruption.
- Follow the runtime action contract exactly. It is code-owned and appended separately from this template.
- `thought` is a brief admin-visible operational note, not hidden scratch reasoning."""

_OBSOLETE_SETTING_KEYS = {
    "action_contract_template",
}


# Settings that must exist for the application to function.
# Format: (key, value, category)
_SEED_SETTINGS: list[tuple[str, str, str]] = [
    # ── Simulation ──
    ("tick_interval", "0.25", "simulation"),
    ("steps_per_tick", "1", "simulation"),
    ("movement_tiles_per_second", "4", "simulation"),
    ("thought_bubble_duration_ms", "4000", "simulation"),

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
    ("trigger_claim_timeout_seconds", "300", "advanced"),

    # ── API limits ──
    ("api_message_limit_max", "200", "advanced"),
    ("api_diagnostics_limit_max", "200", "advanced"),

    # ── System prompt template (advanced) ──
    ("system_prompt_template", SYSTEM_PROMPT_TEMPLATE, "advanced"),
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


def prune_obsolete_settings() -> None:
    """Delete settings keys that are no longer part of the runtime contract."""
    for key in _OBSOLETE_SETTING_KEYS:
        execute("DELETE FROM settings WHERE key = $1", [key])


def force_reseed() -> None:
    """Overwrite ALL seed settings back to their defaults."""
    now = datetime.now(timezone.utc)
    for key, value, category in _SEED_SETTINGS:
        execute(
            "INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
            "VALUES ($1, $2, $3, $4)",
            [key, value, category, now],
        )
    prune_obsolete_settings()
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
    if key in _OBSOLETE_SETTING_KEYS:
        raise ValueError(f"Setting '{key}' is obsolete and cannot be modified")
    now = datetime.now(timezone.utc)
    execute(
        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
        "VALUES ($1, $2, $3, $4)",
        [key, value, category, now],
    )
    return Setting(key=key, value=value, category=category, updated_at=now)
