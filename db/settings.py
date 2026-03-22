"""BossMod AI — Settings CRUD and seed data."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

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

    # ── Diagnostics ──
    ("diagnostics_enabled", "false", "advanced"),
    ("diagnostics_retention_limit", "5000", "advanced"),

    # ── Concurrency ──
    ("max_concurrent_llm_calls", "5", "llm"),

    # ── Simulation resilience ──
    ("sim_error_threshold", "10", "simulation"),
    ("sim_error_backoff_seconds", "30", "simulation"),

    # ── WebSocket ──
    ("ws_send_timeout_seconds", "5", "advanced"),

    # ── API limits ──
    ("api_message_limit_max", "200", "advanced"),
    ("api_diagnostics_limit_max", "200", "advanced"),

    # ── Available actions schema (advanced) ──
    ("available_actions_schema", """You are an AI agent in a virtual office. You control an avatar that represents your physical presence. You are free to think, reason, and produce any work you need — your capabilities are not limited to the actions below.

Each turn you receive a WORLD STATUS and must respond with a single JSON action.

ACTIONS:
  work           — Produce work output. System moves your avatar to your desk.
  message        — Send a message to another agent or the human operator.
  walkTo         — Move your avatar to a destination.
  remoteMeeting  — Start a remote meeting with another agent from your desk.
  idle           — Nothing to do. Return to idle state.
  complete       — Mark current task as done.
  blocked        — Mark current task as blocked.
  delegated      — Hand current task to another agent.
  abandoned      — Abandon current task.

DESTINATIONS (for walkTo):
  desk, meetingRoom, breakRoom, mainWorkspace, southWorkspace, hallway

RESPONSE FORMAT — respond with exactly ONE JSON object:
  {"action":"work","output":"your work product","thought":"reasoning"}
  {"action":"message","to":"agentName","content":"message text","thought":"reasoning"}
  {"action":"walkTo","destination":"breakRoom","thought":"reasoning"}
  {"action":"remoteMeeting","with":"agentName","topic":"topic","thought":"reasoning"}
  {"action":"idle","thought":"reasoning"}
  {"action":"complete","taskId":"id","summary":"what was done","thought":"reasoning"}
  {"action":"blocked","taskId":"id","reason":"why blocked","thought":"reasoning"}
  {"action":"delegated","taskId":"id","to":"agentName","thought":"reasoning"}
  {"action":"abandoned","taskId":"id","reason":"why abandoned","thought":"reasoning"}

RULES:
- Valid JSON only, no markdown or extra text.
- Finish work before changing state.
- If a meeting is called, walk to the meeting room.
- "thought" is your internal reasoning (visible to admins).""", "advanced"),

    # ── System prompt template (advanced) ──
    ("system_prompt_template", """{{personality}}

{{memory}}

{{worldStatus}}

{{task}}

---

{{available_actions}}""", "advanced"),
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
