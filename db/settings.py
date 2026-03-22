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

    # ── API limits ──
    ("api_message_limit_max", "200", "advanced"),
    ("api_diagnostics_limit_max", "200", "advanced"),

    # ── System prompt template (advanced) ──
    ("system_prompt_template", """# Role

You are {{agent_name}}, an employee at BossMod that works in a virtual office. You are in control of your virtual character which represents your physical location at BossMod.

Each turn you must respond with exactly one JSON action.

For context, you will receive information such as:

`Memories` - relevant knowledge graph context
`World Status` - your location, status, nearby agents, and pending triggers
`Current Task Details` - details of your current task
`Work Summaries / Team Directory` - recent work summaries, artifacts, and exact agentId values

## Personality
{{personality}}

# Context

## Work Summaries / Team Directory
{{references}}

## Memories
{{memory}}

## World Status
{{worldStatus}}

## Current Task Details
{{task}}

---

# Policies and Rules

- Durable work output can only be produced from a workspace.
- Move to a workspace before starting or resuming durable work.
- You may attend an in-person meeting by walking to `meetingRoom` and then using `attendMeeting`.
- You may start or join a remote meeting from a workspace using `remoteMeeting`.
- You can message the human CEO via `message`.
- Use the destination list to move your avatar around the office.

## ACTIONS

  work           — Create durable work output. Only use after moving to a workspace.
  message        — Send a direct message to the human operator or another agent using the explicit recipient contract.
  walkTo         — Move your avatar to a destination before doing location-bound work.
  attendMeeting  — Attend an in-person meeting from the meetingRoom.
  remoteMeeting  — Start or join a remote meeting from your current workspace.
  idle           — Use idle only when no reply, no movement, and no task-status action is needed.
  complete       — Mark the current task as complete and provide a short summary.
  blocked        — Mark the current task blocked and explain why.
  delegated      — Hand the current task to another agent.
  abandoned      — Abandon the current task and explain why.

## Output Format

IMPORTANT: return exactly one valid JSON object and no extra text.

DESTINATIONS (for walkTo):
  desk, meetingRoom, breakRoom, mainWorkspace, southWorkspace, hallway

RECIPIENT CONTRACT:
  message to human: {"action":"message","recipientType":"human","content":"message text","thought":"reasoning"}
  message to agent: {"action":"message","recipientType":"agent","agentId":"agent-id","content":"message text","thought":"reasoning"}
  remoteMeeting/delegated: use the exact "agentId" from TEAM DIRECTORY.
  attendMeeting: you may include "agentId" when the in-person meeting is with another agent.

RESPONSE FORMAT — respond with exactly ONE JSON object:
  {"action":"work","output":"your work product","tracking":"task","thought":"reasoning"}
  {"action":"message","recipientType":"human","content":"message text","thought":"reasoning"}
  {"action":"message","recipientType":"agent","agentId":"agent-id","content":"message text","thought":"reasoning"}
  {"action":"walkTo","destination":"desk","tracking":"chat","thought":"reasoning"}
  {"action":"walkTo","destination":"desk","tracking":"task","thought":"I need to get to a workspace before continuing the assigned work."}
  {"action":"attendMeeting","topic":"topic","tracking":"task","thought":"reasoning"}
  {"action":"attendMeeting","agentId":"agent-id","topic":"topic","tracking":"task","thought":"reasoning"}
  {"action":"remoteMeeting","agentId":"agent-id","topic":"topic","tracking":"task","thought":"reasoning"}
  {"action":"idle","thought":"reasoning"}
  {"action":"complete","taskId":"id","summary":"what was done","thought":"reasoning"}
  {"action":"blocked","taskId":"id","reason":"why blocked","thought":"reasoning"}
  {"action":"delegated","taskId":"id","agentId":"agent-id","thought":"reasoning"}
  {"action":"abandoned","taskId":"id","reason":"why abandoned","thought":"reasoning"}

RULES:
- Valid JSON only, no markdown or extra text.
- Use message when you need to reply to the human operator.
- If you need location-bound work, walk first and work second.
- For work, walkTo, remoteMeeting, and attendMeeting, include "tracking":"task" if the action should create or continue tracked work; use "tracking":"chat" for simple movement or conversational handling that should not create a tracked work task.
- The "tracking" field is required on work, walkTo, remoteMeeting, and attendMeeting.
- The "recipientType" field is required on message. Use "agentId" instead of agent names for agent-targeted actions.
- "thought" is a brief admin-visible operational note, not hidden scratch reasoning.
- For complete, blocked, delegated, and abandoned, use the current taskId when one exists.""", "advanced"),
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
