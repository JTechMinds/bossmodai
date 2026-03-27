"""BossMod AI — Settings CRUD and seed data."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.agent_loop.action_contract import default_action_contract_template
from core.agent_loop.decision_contract import default_decision_contract_template
from core.models import Setting
from db.crud import execute, fetch_all, query_one

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """# Role

You are {{agent_name}}, an employee at BossMod that works in a virtual office. You control your virtual character, which represents your physical location at BossMod.

Each turn you must respond with exactly one JSON object that conforms to the runtime contract provided separately for that specific turn.

## Personality
{{personality}}

# Context

## Live Runtime State
{{worldStatus}}

## Current Activity
{{activity}}

## Current Task
{{task}}

## Open Tasks
{{pending_tasks}}

## Recent Work History / Team Directory
{{references}}

---

# Operating Rules

- Treat `Live Runtime State` as authoritative for your current operational status.
- Treat `Current Activity` as the live runtime thread you are continuing right now.
- Treat `Current Task` as the only task you are actively working right now.
- Treat `Open Tasks` as pending or accepted work that is not complete yet.
- Treat `Recent Work History / Team Directory` as historical reference only, not proof that work is still active.
- For status questions, answer from `Live Runtime State` first. If `Current Task` is none, do not claim you are still actively working on a completed task; you may mention the most recent completed task as finished work.
- Use BossMod CLI when you need authoritative self/project facts instead of inferring them from old chat.
- Durable work output can only be produced from a workspace.
- Direct requests are decision turns: decide how to respond and what commitment to make.
- Resumed internal turns are execution turns: carry out the current commitment one step at a time.
- Durable work output can only be produced while a work commitment is active and you are in a workspace.
- Follow the runtime contract exactly. It is appended separately from this template.
- `thought` is a brief admin-visible operational note, not hidden scratch reasoning."""

RUNTIME_CONTRACT_DECISION_TEMPLATE = default_decision_contract_template()
RUNTIME_CONTRACT_EXECUTION_TEMPLATE = default_action_contract_template()
RUNTIME_CONTROL_STATE = "running"

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
    ("default_max_tokens", "8192", "llm"),

    # ── Context window ──
    ("context_recent_work_artifacts", "5", "context"),
    ("context_recent_completed_tasks", "3", "context"),

    # ── Desk ──
    ("desk_preview_max_chars", "50000", "desk"),

    # ── Diagnostics ──
    ("diagnostics_enabled", "false", "advanced"),
    ("diagnostics_retention_limit", "5000", "advanced"),
    ("desktop_open_folder_handler", "", "advanced"),

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

    # ── CLI safety ──
    ("cli_max_write_bytes", "262144", "advanced"),

    # ── CLI policy ──
    ("cli_shell_enabled", "false", "cli_policy"),
    ("cli_shell_timeout_seconds", "30", "cli_policy"),
    ("cli_shell_max_output_bytes", "65536", "cli_policy"),
    ("cli_approval_timeout_minutes", "60", "cli_policy"),
    ("cli_default_policy", "deny", "cli_policy"),

    # ── Agent defaults ──
    ("default_spawn_x", "14", "simulation"),
    ("default_spawn_y", "9", "simulation"),
    ("default_prompt_history_last_n", "30", "context"),
    ("default_prompt_history_max_tokens", "2000", "context"),

    # ── System prompt template (advanced) ──
    ("system_prompt_template", SYSTEM_PROMPT_TEMPLATE, "advanced"),
    ("runtime_contract_decision", RUNTIME_CONTRACT_DECISION_TEMPLATE, "advanced"),
    ("runtime_contract_execution", RUNTIME_CONTRACT_EXECUTION_TEMPLATE, "advanced"),
    ("runtime_control_state", RUNTIME_CONTROL_STATE, "advanced"),
]
_SEED_SETTING_DEFAULTS: dict[str, tuple[str, str]] = {
    key: (value, category) for key, value, category in _SEED_SETTINGS
}


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


def get_seed_setting_default(key: str) -> tuple[str, str] | None:
    """Return the seeded default value and category for one setting key."""
    return _SEED_SETTING_DEFAULTS.get(key)


def reset_setting_to_seed(key: str) -> Setting:
    """Reset one seeded setting key back to its default value."""
    seeded = get_seed_setting_default(key)
    if seeded is None:
        raise ValueError(f"Setting '{key}' has no seeded default")
    value, category = seeded
    return set_setting(key, value, category)


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
