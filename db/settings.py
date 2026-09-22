"""BossMod AI — Settings CRUD and seed data."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from core.default_prompts import (
    RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE,
    RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE,
    RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE,
    RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE,
    RUNTIME_CONTRACT_DECISION_TEMPLATE,
    RUNTIME_CONTRACT_EXECUTION_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
)
from core.models import Setting
from db.crud import execute, fetch_all, query_one
from db.secret_store import decrypt_secret, decrypt_setting_value, encrypt_setting_value

logger = logging.getLogger(__name__)
RUNTIME_CONTROL_STATE = "running"
LOCAL_API_TOKEN_KEY = "local_api_token"

_OBSOLETE_SETTING_KEYS = {
    "action_contract_template",
    # Hidden second cap. Agent turns, System AI routes, and repairs share
    # max_concurrent_agent_turns. This seed used to allow 5 calls above that knob.
    "max_concurrent_llm_calls",
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
    # Output-token budget for one model completion. Prior factory default
    # was 8192; reconcile_factory_max_tokens raises an untouched 8192 only.
    ("default_max_tokens", "16384", "llm"),
    ("llm_request_timeout_seconds", "720", "llm"),
    # Idle silence with no streamed chunk. Not a wall-clock cap on a live
    # stream. Default 120 seconds. Each chunk resets the timer. A path that
    # cannot stream uses only llm_request_timeout_seconds.
    ("llm_stall_timeout_seconds", "120", "llm"),
    ("decision_repair_attempts", "6", "llm"),
    # One budget for inflight model calls: agent turns, System AI routes,
    # and repairs. Default 2. One agent still runs at most one turn.
    # Repair wakes use a lane and sort behind a live channel lead.
    # The operator-facing label is "Max concurrent model calls".
    ("max_concurrent_agent_turns", "2", "llm"),
    # Last-resort safety cap on channel rounds for one human message,
    # including round 1. Empty speak, Pause, demotion, and narrow dup-ack
    # stop a live thread. This number must not be that brake.
    ("channel_response_round_cap", "64", "llm"),
    # System AI + compaction pressure knobs. The System AI picker is the
    # first control under Settings → AI Connections. Compaction knobs stay
    # on Settings → System → AI Output.
    # Compaction never runs every turn, and it never blocks an agent turn.
    # When compactors exist they queue in the background. These keys only
    # store the choice and the knobs; no compaction runner ships with them.
    # system_ai_connection is one AI connection id, not a per-agent identity model
    # override. Empty means unset. A saved id that still names a
    # connection is kept on upgrade; this seed does not overwrite it.
    # Channel rounds use it for one short route. compaction_mode is off | pressure_only.
    ("system_ai_connection", "", "llm"),
    ("compaction_mode", "pressure_only", "llm"),
    ("compaction_task_budget_headroom_percent", "25", "llm"),
    ("compaction_chat_budget_headroom_percent", "35", "llm"),
    ("compaction_min_turns_between_runs", "8", "llm"),
    ("compaction_cooldown_minutes", "10", "llm"),
    ("managed_writer_max_batch_files", "8", "llm"),
    ("managed_writer_max_sections_per_file", "8", "llm"),

    # ── Context window ──
    ("context_recent_work_artifacts", "5", "context"),
    ("context_recent_completed_tasks", "3", "context"),

    # ── Desk ──
    ("desk_preview_max_chars", "50000", "desk"),

    # ── Diagnostics ──
    ("diagnostics_enabled", "false", "advanced"),
    ("diagnostics_retention_limit", "5000", "advanced"),
    ("desktop_open_folder_handler", "", "advanced"),

    # ── Simulation resilience ──
    ("sim_error_threshold", "10", "simulation"),
    ("sim_error_backoff_seconds", "30", "simulation"),

    # ── Watchdog ──
    ("watchdog_check_interval_seconds", "5", "simulation"),
    ("watchdog_soft_ping_minutes", "15", "simulation"),
    ("watchdog_escalation_minutes", "15", "simulation"),
    # HA-LOOP-P1-07: meeting watchdog keys (fallbacks in meeting_watchdog.py
    # must stay equal to these seed values).
    ("meeting_watchdog_check_interval_seconds", "5", "simulation"),
    ("meeting_invite_accept_timeout_seconds", "90", "simulation"),
    ("meeting_invite_arrival_timeout_seconds", "180", "simulation"),

    # ── WebSocket ──
    ("ws_send_timeout_seconds", "5", "advanced"),
    ("trigger_claim_timeout_seconds", "300", "advanced"),
    ("turn_failure_retry_limit", "2", "advanced"),

    # ── API limits ──
    ("api_message_limit_max", "200", "advanced"),
    ("api_diagnostics_limit_max", "200", "advanced"),

    # ── CLI safety ──
    ("cli_max_write_bytes", "262144", "advanced"),
    ("cli_max_read_lines", "200", "advanced"),

    # ── CLI policy ──
    # HA-SEC-P0-03: native shell stays off until an operator opts in.
    ("cli_shell_enabled", "false", "cli_policy"),
    ("cli_shell_timeout_seconds", "30", "cli_policy"),
    ("cli_shell_max_output_bytes", "65536", "cli_policy"),
    ("cli_approval_timeout_minutes", "60", "cli_policy"),
    # Unmatched commands. Prior factory default was deny;
    # reconcile_factory_cli_default_policy moves an untouched deny once.
    # seed_defaults inserts this row only when it is missing, so an
    # operator Deny pick is not overwritten by the insert.
    ("cli_default_policy", "approval_required", "cli_policy"),
    # Extra host directories a named absolute path may open/read/edit.
    # Empty = no extra host access (fail-closed). Not a full host mount.
    ("workspace_host_roots", "", "cli_policy"),

    # ── Nest git (self-host remotes) ──
    # Host Enable stays off until a Shell probe sees a credential helper
    # or SSH agent. PAT/SSH are secret settings (bm1 wrap).
    ("nest_git_host_enabled", "false", "nest_git"),
    ("nest_git_pat", "", "nest_git"),
    ("nest_git_ssh_key", "", "nest_git"),
    ("nest_git_credentials", '{"default_id":null,"items":[]}', "nest_git"),

    # ── Agent defaults ──
    ("default_spawn_x", "14", "simulation"),
    ("default_spawn_y", "9", "simulation"),
    ("default_prompt_history_last_n", "30", "context"),
    ("default_prompt_history_max_tokens", "2000", "context"),
    # How many agent snapshots Add agent's Recent keeps (db/agent_snapshots.py):
    # the newest by capture time, deleted agents included. No version history.
    ("recent_agents_limit", "20", "advanced"),

    # ── Telegram integration ──
    ("telegram_enabled", "false", "telegram"),
    ("telegram_bot_token", "", "telegram"),
    ("telegram_allowed_user_ids", "", "telegram"),

    # ── Agent packs (catalog browse + import; no store backend) ──
    ("agent_pack_catalog_repo", "JTechMinds/BossMod_AgentMP", "agent_packs"),
    ("agent_pack_catalog_path", "packs", "agent_packs"),
    ("agent_pack_catalog_pin", "8a0d68a", "agent_packs"),
    ("agent_pack_url_allowlist", "", "agent_packs"),

    # ── System prompt template (advanced) ──
    ("system_prompt_template", SYSTEM_PROMPT_TEMPLATE, "advanced"),
    ("runtime_contract_decision", RUNTIME_CONTRACT_DECISION_TEMPLATE, "advanced"),
    ("runtime_contract_execution", RUNTIME_CONTRACT_EXECUTION_TEMPLATE, "advanced"),
    ("runtime_block_conversation_envelope", RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE, "advanced"),
    ("runtime_block_file_deliverable_guidance", RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE, "advanced"),
    ("runtime_block_communication_snapshot", RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE, "advanced"),
    ("runtime_block_trigger_event", RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE, "advanced"),
    ("runtime_control_state", RUNTIME_CONTROL_STATE, "advanced"),
]
_SEED_SETTING_DEFAULTS: dict[str, tuple[str, str]] = {
    key: (value, category) for key, value, category in _SEED_SETTINGS
}


# Prior shipped defaults. Bumped on init when the stored pin is still one of
# these so Browse picks up the seven-pack catalog without overwriting a custom pin.
_PREVIOUS_DEFAULT_CATALOG_PINS = frozenset({"3c1e0a6", "dcc94ca"})


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
    ensure_local_api_token()
    reconcile_catalog_pin()
    reconcile_factory_round_cap()
    reconcile_factory_max_tokens()
    reconcile_factory_cli_default_policy()
    logger.info("Settings seeded (%d keys)", len(_SEED_SETTINGS))


# The shipped cap before Talk/Work/Paused. Only this factory value is raised.
# An operator who set a different cap keeps it.
_FACTORY_ROUND_CAP = "4"
_LAST_RESORT_ROUND_CAP = "64"

# Prior shipped default_max_tokens. Only this factory value is raised to 16384.
# An operator who set a different budget keeps it.
_FACTORY_MAX_TOKENS = "8192"
_DEFAULT_MAX_TOKENS = "16384"

# Prior shipped cli_default_policy. Only this factory value moves to
# approval_required, and only once. An operator who set a different policy
# keeps it. After the pass, a saved deny is an operator choice.
_FACTORY_CLI_DEFAULT_POLICY = "deny"
_DEFAULT_CLI_POLICY = "approval_required"
_CLI_DEFAULT_POLICY_FACTORY_RECONCILED = "cli_default_policy_factory_reconciled"


def reconcile_factory_round_cap() -> None:
    """Raise the untouched factory round cap so it is not the live-thread brake.

    Does not overwrite a value the operator changed away from ``4``.
    """
    row = query_one(
        "SELECT value FROM settings WHERE key = $1",
        ["channel_response_round_cap"],
    )
    if row is None or str(row.get("value") or "") != _FACTORY_ROUND_CAP:
        return
    set_setting("channel_response_round_cap", _LAST_RESORT_ROUND_CAP, "llm")


def reconcile_factory_max_tokens() -> None:
    """Raise the untouched factory max-tokens budget from 8k to 16k.

    Does not overwrite a value the operator changed away from ``8192``.
    """
    row = query_one(
        "SELECT value FROM settings WHERE key = $1",
        ["default_max_tokens"],
    )
    if row is None or str(row.get("value") or "") != _FACTORY_MAX_TOKENS:
        return
    set_setting("default_max_tokens", _DEFAULT_MAX_TOKENS, "llm")


def reconcile_factory_cli_default_policy() -> None:
    """Move an untouched factory CLI default from deny to approval_required once.

    A stored value other than the prior factory ``deny`` is left alone.
    After this pass, a saved ``deny`` is an operator choice and is not rewritten.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_CLI_DEFAULT_POLICY_FACTORY_RECONCILED],
    )
    if seen is not None:
        return
    row = query_one(
        "SELECT value FROM settings WHERE key = $1",
        ["cli_default_policy"],
    )
    if row is not None and str(row.get("value") or "") == _FACTORY_CLI_DEFAULT_POLICY:
        set_setting("cli_default_policy", _DEFAULT_CLI_POLICY, "cli_policy")
    set_setting(_CLI_DEFAULT_POLICY_FACTORY_RECONCILED, "true", "cli_policy")


def ensure_local_api_token() -> str:
    """Return the persisted local API token, generating one if missing.

    Not part of ``_SEED_SETTINGS`` so Settings reseed does not rotate it.
    Application-level DB reset recreates it via ``seed_defaults``.
    """
    existing = query_one(
        "SELECT value FROM settings WHERE key = $1",
        [LOCAL_API_TOKEN_KEY],
    )
    value = ""
    if existing is not None and existing.get("value") is not None:
        value = decrypt_secret(str(existing["value"])) or ""
        value = value.strip()
    if value:
        return value
    token = secrets.token_urlsafe(32)
    set_setting(LOCAL_API_TOKEN_KEY, token, "security")
    logger.info("Generated local API token")
    return token


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


def reconcile_catalog_pin() -> None:
    """Move the shipped catalog pin off a previous default. Custom pins stay."""
    seeded = get_seed_setting_default("agent_pack_catalog_pin")
    if seeded is None:
        return
    new_pin, category = seeded
    row = query_one("SELECT value FROM settings WHERE key = $1", ["agent_pack_catalog_pin"])
    current = str((row or {}).get("value") or "").strip()
    if current in _PREVIOUS_DEFAULT_CATALOG_PINS and current != new_pin:
        set_setting("agent_pack_catalog_pin", new_pin, category)


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
        rows = fetch_all(
            "SELECT key, value, category, updated_at FROM settings "
            "WHERE category = $1 ORDER BY key",
            [category],
            Setting,
        )
    else:
        rows = fetch_all(
            "SELECT key, value, category, updated_at FROM settings ORDER BY key",
            model_cls=Setting,
        )
    return [
        row.model_copy(update={"value": decrypt_setting_value(row.key, row.value)})
        for row in rows
    ]


def set_setting(key: str, value: str, category: str = "general") -> Setting:
    """Insert or update a setting."""
    if key in _OBSOLETE_SETTING_KEYS:
        raise ValueError(f"Setting '{key}' is obsolete and cannot be modified")
    now = datetime.now(timezone.utc)
    stored = encrypt_setting_value(key, value)
    execute(
        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
        "VALUES ($1, $2, $3, $4)",
        [key, stored, category, now],
    )
    return Setting(key=key, value=value, category=category, updated_at=now)
