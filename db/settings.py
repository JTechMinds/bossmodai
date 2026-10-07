"""BossMod AI — Settings CRUD and seed data."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from core.default_prompts import (
    load_default_prompt,
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
    # Global per-mode model fallbacks. An agent now routes through its one AI
    # connection (core.llm.routing), so nothing reads these.
    "default_model_social",
    "default_model_work",
    "default_model_reasoning",
    "default_model_extraction",
    "default_model_self_queue",
}


# Settings that must exist for the application to function.
# Format: (key, value, category)
_SEED_SETTINGS: list[tuple[str, str, str]] = [
    # ── Profile (core/boss.py) ──
    # The name agents call the human: "<name> (the boss)", or "Boss" when
    # empty. Validated by the settings API (core.boss.validate_boss_name).
    ("boss_name", "", "profile"),
    # Whether the one-time "What should your team call you?" dialog was
    # answered or dismissed (ui/static/js/shell/boss-name-prompt.js).
    ("boss_name_prompted", "false", "profile"),

    # ── Simulation ──
    ("tick_interval", "0.25", "simulation"),
    ("steps_per_tick", "1", "simulation"),
    ("movement_tiles_per_second", "4", "simulation"),
    ("thought_bubble_duration_ms", "4000", "simulation"),
    ("office_chatter_page_size", "30", "simulation"),

    # ── Social triggers ──
    ("social_idle_threshold_minutes", "5", "social"),
    ("social_cooldown_minutes", "15", "social"),
    ("social_proximity_tiles", "8", "social"),

    # ── LLM defaults ──
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
    # Number of prior thread lines the System AI router reads before the
    # latest message. 0 means the router sees no transcript.
    ("channel_router_transcript_messages", "10", "llm"),
    # Thread idle check: whether the quiet-period owed-work check runs.
    ("channel_idle_check_enabled", "true", "llm"),
    # How long a thread must be silent before the idle check judges it.
    ("channel_idle_check_delay_seconds", "45", "llm"),
    # How often the worker scans threads for the idle check.
    ("channel_idle_check_interval_seconds", "5", "llm"),
    # Cap on members one idle check may wake.
    ("channel_idle_check_max_wakes", "2", "llm"),
    # A thread quiet longer than this is dormant; the idle check leaves it alone.
    ("channel_idle_check_max_age_minutes", "30", "llm"),
    # Failed judge attempts (no answer, or a malformed one) on one quiet period
    # before it is recorded as checked.
    ("channel_idle_check_max_attempts", "3", "llm"),
    # System AI + compaction pressure knobs. The System AI picker is the
    # first control under Settings → AI Connections. Compaction knobs stay
    # on Settings → System → AI Output.
    # Compaction never runs every turn, and it never blocks an agent turn.
    # Chat fade queues in the background on System AI when chat headroom
    # is tight. Sticky-slot fill queues in the background on System AI
    # when task headroom is tight. Slots are task-side and keyed by
    # existing task, owner, verdict, and blocker ids. These keys are the
    # only knob surface: mode, headroom, min turns, and cooldown.
    # system_ai_connection is one AI connection id, not a per-agent identity model
    # override. Empty means unset. A saved id that still names a
    # connection is kept on upgrade; this seed does not overwrite it.
    # Channel rounds use it for one short route. compaction_mode is off | pressure_only.
    ("system_ai_connection", "", "llm"),
    # Thinking choice for System AI calls, merged like an agent's
    # (core.llm.thinking). "default" sends the connection's extra_body as
    # stored; a level must be one the System AI connection offers.
    ("system_ai_thinking", "default", "llm"),
    # Default output cap for System AI completions. Reasoning tokens count
    # against it on reasoning models, so a small cap truncates the answer.
    # Every System AI completion uses it: channel routes, chat fade, sticky
    # slots, CLI auto-approve.
    ("system_ai_max_tokens", "6144", "llm"),
    # Wall-clock limit for one System AI completion (routing, idle check, fade,
    # sticky slots, auto-approve). Slow local models need headroom.
    ("system_ai_timeout_seconds", "180", "llm"),
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
    # Longest pref text an agent can save, and the most of one pref's text the
    # warm section shows. One knob for both, so any pref that saves shows whole.
    # The "- kind id — " prefix never counts against it.
    ("standing_prefs_line_max_chars", "400", "context"),
    # Most characters of the injected "Standing prefs" section, and the cap on
    # total pref text in one agent's store: storing more than can ever be
    # shown makes no sense. Lowering either never hides a stored pref.
    ("standing_prefs_section_max_chars", "4000", "context"),

    # ── Desk ──
    ("desk_preview_max_chars", "50000", "desk"),

    # ── Diagnostics ──
    ("diagnostics_enabled", "false", "advanced"),
    ("diagnostics_retention_limit", "5000", "advanced"),
    # History retention in days, pruned by the task watchdog every
    # history_prune_interval_minutes. The row limit above still applies on
    # every insert. Only finished (completed/failed) triggers are pruned.
    ("diagnostics_retention_days", "7", "advanced"),
    ("trigger_retention_days", "7", "advanced"),
    ("activity_log_retention_days", "30", "advanced"),
    # How often the task watchdog runs the history prune above.
    ("history_prune_interval_minutes", "60", "advanced"),
    ("desktop_open_folder_handler", "", "advanced"),

    # ── Simulation resilience ──
    ("sim_error_threshold", "10", "simulation"),
    ("sim_error_backoff_seconds", "30", "simulation"),

    # ── Watchdog ──
    # The stall thresholds below are minutes; a 30-second scan is plenty.
    # Prior factory default was 5; reconcile_factory_watchdog_interval moves
    # an untouched 5 once.
    ("watchdog_check_interval_seconds", "30", "simulation"),
    ("watchdog_soft_ping_minutes", "15", "simulation"),
    ("watchdog_escalation_minutes", "15", "simulation"),
    # Frozen work transcript (core/agent_loop/work_snapshot.py). Char
    # budgets, not tokens: count_tokens is 0 with no tokenizer configured.
    # The full transcript is restored on execution resumes; decision turns
    # see only the most recent steps that fit the smaller chat-view budget.
    ("work_snapshot_max_chars", "120000", "simulation"),
    ("work_snapshot_chat_view_max_chars", "24000", "simulation"),
    # Checkpoints (a "you are repeating steps" resume) before a no-progress
    # trip blocks the task.
    ("guardian_no_progress_checkpoints", "1", "simulation"),
    # HA-LOOP-P1-07: meeting watchdog keys (fallbacks in meeting_watchdog.py
    # must stay equal to these seed values).
    ("meeting_watchdog_check_interval_seconds", "5", "simulation"),
    ("meeting_invite_accept_timeout_seconds", "90", "simulation"),
    ("meeting_invite_arrival_timeout_seconds", "180", "simulation"),

    # ── Schedules (core/scheduling/watch.py, runtime worker) ──
    # Longest the schedule clock sleeps before re-reading the wall clock. The
    # loop's sleep stops while the machine is suspended, so this bounds how
    # late a run is noticed after a wake.
    ("schedule_max_sleep_seconds", "60", "simulation"),
    # How late a run may be handled and still fire; later is recorded as
    # missed. Must be greater than schedule_max_sleep_seconds.
    ("schedule_fire_grace_seconds", "120", "simulation"),

    # ── WebSocket ──
    ("ws_send_timeout_seconds", "5", "advanced"),
    # A burst of world-state broadcasts (agent steps, movement ticks, roster
    # edits) within this window is sent as one world_update read once
    # (api/websocket.py ConnectionManager.broadcast_world_state).
    ("world_state_coalesce_ms", "150", "advanced"),
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
    # Auto-approve review context (core.bm_cli.approval_gate.context):
    # recent conversation lines, and floor-wide operator Approve/Reject
    # precedents, sent to System AI with each reviewed command.
    ("cli_auto_approve_context_messages", "20", "cli_policy"),
    ("cli_auto_approve_precedent_limit", "15", "cli_policy"),
    # Global auto-approve (Settings → Advanced): when "true", the approval
    # gate runs for every thread and DM, whatever each one's own flag says.
    # Read live by core.bm_cli.approval_gate.gate.global_auto_approve_enabled.
    ("cli_auto_approve_global", "false", "advanced"),
    # Unmatched commands. Prior factory default was deny;
    # reconcile_factory_cli_default_policy moves an untouched deny once.
    # seed_defaults inserts this row only when it is missing, so an
    # operator Deny pick is not overwritten by the insert.
    ("cli_default_policy", "approval_required", "cli_policy"),
    # Extra host directories a named absolute path may open/read/edit.
    # Empty = no extra host access (fail-closed). Not a full host mount.
    ("workspace_host_roots", "", "cli_policy"),
    # Commands whose replay is harmful: a failed turn that ran one is not
    # retried (core.bm_cli.retry_policy). One command prefix per line.
    # Operator additions only: extensions declare their own in their
    # manifest (command.no_retry), so the seed is empty.
    ("cli_no_retry_commands", "", "cli_policy"),

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
    # Runtime events waiting for the Telegram bridge (core/runtime/services.py).
    # A full queue drops the newest event with a WARNING rather than stalling
    # the app's runtime-event reader behind a slow Telegram API.
    ("telegram_dispatch_queue_size", "200", "advanced"),

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
    # The app rings the runtime worker's stdin doorbell after filing a runtime
    # command; this is how long the worker waits for a ring before reading the
    # command queue anyway (a lost ring costs at most this much latency).
    ("runtime_command_fallback_poll_seconds", "5", "advanced"),
    # How often the runtime worker stamps its heartbeat. Readers treat a
    # heartbeat older than 3x this as a dead worker (db/runtime_control.py).
    ("runtime_heartbeat_seconds", "5", "advanced"),
    # Attachments (paste/attach Phase-1)
    ("bossmod.attach.max_size_mb", "10", "advanced"),
    ("bossmod.attach.max_per_message", "5", "advanced"),
    # Text files at or under this many characters go inline to the model;
    # longer ones are referenced by their /projects path instead.
    ("bossmod.attach.inline_text_max_chars", "20000", "advanced"),
    # Uploads never sent within this window are swept at app start.
    ("bossmod.attach.pending_ttl_hours", "24", "advanced"),

    # ── Extensions ──
    # JSON array of enabled extension ids. Written only by the extensions API,
    # which checks setup first; the generic settings PUT refuses it.
    ("extensions_enabled", "[]", "extensions"),
    # How often the runtime worker's wake service looks for (extension, agent)
    # pairs whose own poll interval (a per-agent setting) has come due.
    ("extension_wake_tick_seconds", "5", "extensions"),
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
    reconcile_factory_watchdog_interval()
    reconcile_work_commit_prompt_contract()
    reconcile_work_commit_resume_prompt()
    reconcile_extension_event_prompt()
    reconcile_specialty_gate_prompt_lines()
    reconcile_boss_prompt_wording()
    reconcile_memory_prompt_lines()
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


# Prior shipped watchdog_check_interval_seconds. Only this factory value moves
# to 30, and only once (marker row), so a 5 the operator sets later is kept.
_FACTORY_WATCHDOG_INTERVAL = "5"
_DEFAULT_WATCHDOG_INTERVAL = "30"
_WATCHDOG_INTERVAL_FACTORY_RECONCILED = "watchdog_check_interval_factory_reconciled"


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


def reconcile_factory_watchdog_interval() -> None:
    """Move an untouched factory watchdog interval from 5 to 30 seconds once.

    Same marker-guarded pattern as :func:`reconcile_factory_cli_default_policy`:
    a stored value other than the prior factory ``5`` is left alone, and after
    the first pass a saved ``5`` is an operator choice and is not rewritten.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_WATCHDOG_INTERVAL_FACTORY_RECONCILED],
    )
    if seen is not None:
        return
    row = query_one(
        "SELECT value FROM settings WHERE key = $1",
        ["watchdog_check_interval_seconds"],
    )
    if row is not None and str(row.get("value") or "") == _FACTORY_WATCHDOG_INTERVAL:
        set_setting("watchdog_check_interval_seconds", _DEFAULT_WATCHDOG_INTERVAL, "simulation")
    set_setting(_WATCHDOG_INTERVAL_FACTORY_RECONCILED, "true", "advanced")


# The required-``work_commit`` / TURN MODEL contract lives in these two
# prompt rows. Stored copies predate it, and seeding never overwrites, so
# they are moved to the shipped defaults once per database. After the pass,
# any stored text is an operator choice and is not rewritten.
_WORK_COMMIT_PROMPT_KEYS = ("system_prompt_template", "runtime_contract_decision")
_WORK_COMMIT_PROMPTS_RECONCILED = "work_commit_prompt_contract_reconciled"


def reconcile_work_commit_prompt_contract() -> None:
    """Overwrite the system prompt and decision contract rows with the shipped defaults once.

    Guarded by a marker row, like ``reconcile_factory_cli_default_policy``:
    the first pass on a database writes the current file-backed defaults,
    records the marker, and every later pass is a no-op, so later operator
    edits are never touched.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_WORK_COMMIT_PROMPTS_RECONCILED],
    )
    if seen is not None:
        return
    for key in _WORK_COMMIT_PROMPT_KEYS:
        seeded = get_seed_setting_default(key)
        if seeded is None:
            raise RuntimeError(f"Prompt setting '{key}' has no seeded default")
        set_setting(key, load_default_prompt(key), seeded[1])
    set_setting(_WORK_COMMIT_PROMPTS_RECONCILED, "true", "advanced")
    logger.info(
        "Reconciled prompt settings to the work_commit contract: %s",
        ", ".join(_WORK_COMMIT_PROMPT_KEYS),
    )


# The decision contract's resume-open-work ``work_commit`` rule, ``data.task.id``,
# the new-work-while-busy guidance, and the thread-wake copy without the
# next-owner nudge or the one-line limit live in these two prompt rows.
# Seeding never overwrites them, so both move to the shipped defaults once per
# database.
_WORK_COMMIT_RESUME_PROMPT_KEYS = ("runtime_contract_decision", "runtime_block_trigger_event")
_WORK_COMMIT_RESUME_RECONCILED = "work_commit_resume_prompt_reconciled"


def reconcile_work_commit_resume_prompt() -> None:
    """Overwrite the decision contract and trigger-event rows with the shipped defaults once.

    Same marker-guarded pattern as :func:`reconcile_work_commit_prompt_contract`:
    the first pass on a database writes the current file-backed default for
    each key and records the marker; every later pass is a no-op, so operator
    edits made after it are never touched. Edits made before it are replaced.

    Raises:
        RuntimeError: A prompt key has no seeded default.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_WORK_COMMIT_RESUME_RECONCILED],
    )
    if seen is not None:
        return
    for key in _WORK_COMMIT_RESUME_PROMPT_KEYS:
        seeded = get_seed_setting_default(key)
        if seeded is None:
            raise RuntimeError(f"Prompt setting '{key}' has no seeded default")
        set_setting(key, load_default_prompt(key), seeded[1])
    set_setting(_WORK_COMMIT_RESUME_RECONCILED, "true", "advanced")
    logger.info(
        "Reconciled prompt settings to the work_commit resume and thread-wake contract: %s",
        ", ".join(_WORK_COMMIT_RESUME_PROMPT_KEYS),
    )


# The ``extension_event`` branch (an extension woke the agent, e.g. new mail
# from a mailbox extension) lives in the trigger-event row. Seeding never
# overwrites it, so it moves to the shipped default once per database.
_EXTENSION_EVENT_PROMPT_KEY = "runtime_block_trigger_event"
_EXTENSION_EVENT_PROMPT_RECONCILED = "extension_event_prompt_reconciled"


def reconcile_extension_event_prompt() -> None:
    """Overwrite the trigger-event row with the shipped default once.

    Same marker-guarded pattern as :func:`reconcile_work_commit_resume_prompt`:
    the first pass on a database writes the file-backed default and records
    the marker; every later pass is a no-op, so operator edits made after it
    are never touched. Edits made before it are replaced.

    Raises:
        RuntimeError: The prompt key has no seeded default.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_EXTENSION_EVENT_PROMPT_RECONCILED],
    )
    if seen is not None:
        return
    seeded = get_seed_setting_default(_EXTENSION_EVENT_PROMPT_KEY)
    if seeded is None:
        raise RuntimeError(f"Prompt setting '{_EXTENSION_EVENT_PROMPT_KEY}' has no seeded default")
    set_setting(_EXTENSION_EVENT_PROMPT_KEY, load_default_prompt(_EXTENSION_EVENT_PROMPT_KEY), seeded[1])
    set_setting(_EXTENSION_EVENT_PROMPT_RECONCILED, "true", "advanced")
    logger.info("Reconciled prompt setting to the extension_event contract: %s", _EXTENSION_EVENT_PROMPT_KEY)


# The specialty-mismatch assign gate was removed, and with it the one line in
# each contract row that described it (it told agents to send ``data.confirm``,
# a key the parser now rejects). Copied verbatim from the shipped files before
# the lines were deleted there.
_SPECIALTY_GATE_PROMPT_LINES: dict[str, str] = {
    "runtime_contract_execution": "  - assign: prefer a teammate whose specialty matches the work; if it is a clear mismatch, pick a better teammate or set data.confirm=true only after stating why\n",
    "runtime_contract_decision": "- Prefer a teammate whose specialty matches the child work. A clear mismatch (writer vs review/audit) is rejected unless a matching teammate is chosen.\n",
}
_SPECIALTY_GATE_PROMPT_LINES_RECONCILED = "specialty_gate_prompt_lines_reconciled"


def reconcile_specialty_gate_prompt_lines() -> None:
    """Remove the retired specialty-gate line from the two contract rows once.

    Unlike the whole-row reconcilers above, this edits only the exact line:
    stored prompt rows drift from the shipped defaults (older seeds, operator
    edits), so overwriting a row would silently discard text this pass has no
    business touching. Each row loses the first whole line (newline included)
    that equals its retired line; every other byte stays as stored, and the
    row keeps its category. A row without the line is left untouched. It is
    logged as a warning, so the operator can review that prompt by hand, only
    when it differs from the shipped default (``load_default_prompt``) or is
    missing: a row equal to the shipped default (every fresh or reset
    database) never had the line and needs no review.

    Guarded by a marker row like :func:`reconcile_extension_event_prompt`:
    the first pass records the marker and every later pass is a no-op.
    ``system_prompt_template`` is never read or written here.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_SPECIALTY_GATE_PROMPT_LINES_RECONCILED],
    )
    if seen is not None:
        return
    for key, retired_line in _SPECIALTY_GATE_PROMPT_LINES.items():
        row = query_one("SELECT value, category FROM settings WHERE key = $1", [key])
        lines = str(row.get("value") or "").splitlines(keepends=True) if row is not None else []
        if row is None or retired_line not in lines:
            if row is not None and str(row.get("value") or "") == load_default_prompt(key):
                continue
            logger.warning(
                "Prompt setting '%s': the retired specialty-gate assign line was not found; "
                "review that prompt for specialty-mismatch / data.confirm guidance",
                key,
            )
            continue
        lines.remove(retired_line)
        set_setting(key, "".join(lines), str(row["category"]))
        logger.info("Removed the retired specialty-gate assign line from prompt setting: %s", key)
    set_setting(_SPECIALTY_GATE_PROMPT_LINES_RECONCILED, "true", "advanced")


# The contract rows called the human "the operator". They now say
# "the boss" / "@Boss". sha256 of each row's shipped default before that
# rewrite (prompts/<file> at 06e428ad, as load_default_prompt returns it:
# trailing newlines stripped), so only untouched rows are overwritten.
_BOSS_WORDING_PRIOR_DEFAULT_SHA256: dict[str, str] = {
    "runtime_contract_decision": "508a52e6b57f77a2e6859df4b5d248d015032b6765eeee01e1342ab64037b83d",
    "runtime_contract_execution": "fcfced67cfbb52f664c48c43322740e408eaebebebb8194f786c242413ecca8b",
    "runtime_block_conversation_envelope": "4d442677ec470d41bc78765163d2e923044a78372651b51f34954a8e527f032e",
}
_BOSS_PROMPT_WORDING_RECONCILED = "boss_prompt_wording_reconciled"


def reconcile_boss_prompt_wording() -> None:
    """Move untouched contract rows from "operator" to "boss" wording once.

    Unlike the whole-row reconcilers above, this overwrites a row only when
    it provably holds the previous shipped default: its sha256 equals the
    entry in ``_BOSS_WORDING_PRIOR_DEFAULT_SHA256``. Such a row is replaced by
    the current file-backed default (``load_default_prompt``) and keeps its
    category. A row already equal to the current default is skipped. Any
    other row (operator edits, older seeds, or a missing row) is left as
    stored and logged as a warning naming the key, so the operator can move
    its wording by hand; edits are never discarded.

    Guarded by a marker row like :func:`reconcile_specialty_gate_prompt_lines`:
    the first pass records the marker and every later pass is a no-op.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_BOSS_PROMPT_WORDING_RECONCILED],
    )
    if seen is not None:
        return
    for key, prior_sha256 in _BOSS_WORDING_PRIOR_DEFAULT_SHA256.items():
        row = query_one("SELECT value, category FROM settings WHERE key = $1", [key])
        stored = str(row.get("value") or "") if row is not None else None
        if stored is not None and stored == load_default_prompt(key):
            continue
        if stored is not None and hashlib.sha256(stored.encode("utf-8")).hexdigest() == prior_sha256:
            set_setting(key, load_default_prompt(key), str(row["category"]))
            logger.info("Reconciled prompt setting to the boss wording: %s", key)
            continue
        logger.warning(
            "Prompt setting '%s' differs from its previous shipped default; it was left as "
            "stored. Review it by hand: the human is now \"the boss\" (@Boss), not \"the operator\"",
            key,
        )
    set_setting(_BOSS_PROMPT_WORDING_RECONCILED, "true", "advanced")


# Agent memory: three shipped lines told the model CLI is for lookups only and
# that durable output needs a work commitment, which steered it away from
# saving what it was told. (old line, new line), each without its newline,
# copied verbatim from the shipped files before and after the edit.
_MEMORY_PROMPT_LINE_EDITS: dict[str, tuple[tuple[str, str], ...]] = {
    "runtime_contract_decision": (
        (
            "- If the snapshot already answers the question, reply directly instead of using CLI.",
            "- If the snapshot already answers the question, reply directly instead of looking it up "
            "with CLI. Saving to memory is not a lookup.",
        ),
        (
            "Use CLI only when the snapshot and surrounding turn context still lack an internal fact "
            "you genuinely need before making the final conversation decision.",
            "Use CLI lookups only when the snapshot and surrounding turn context still lack an internal "
            "fact you genuinely need before making the final conversation decision. Saving what you were "
            "just told (`memory add`, or adding to a project's project_knowledge.md) is not a lookup: do "
            "it in this turn with `cli`, then give your final conversation decision.",
        ),
    ),
    "system_prompt_template": (
        (
            "- Durable work output can only be produced while a work commitment is active and you are "
            "in a workspace.",
            "- Durable work output (task deliverables) can only be produced while a work commitment is "
            "active and you are in a workspace. Saving to memory or adding to a project's "
            "project_knowledge.md is not work output; do it in any turn.",
        ),
    ),
}
_MEMORY_PROMPT_LINES_RECONCILED = "memory_prompt_lines_reconciled"


def reconcile_memory_prompt_lines() -> None:
    """Swap the three memory-blocking prompt lines for their new wording, once.

    Line-level, like :func:`reconcile_specialty_gate_prompt_lines`: stored
    prompt rows drift from the shipped defaults (older seeds, operator
    edits), so overwriting a row would discard text this pass has no
    business touching. For each ``(old, new)`` pair in
    ``_MEMORY_PROMPT_LINE_EDITS``, the first whole line equal to ``old`` is
    replaced by ``new`` (its line ending kept); every other byte stays as
    stored, and the row keeps its category. A row already equal to the
    shipped default (``load_default_prompt``) is skipped. A row without one
    of the old lines is left alone for that line, and a warning names the key
    and the line, so the operator can review that prompt by hand; operator
    edits are never discarded.

    Guarded by a marker row: the first pass records the marker and every
    later pass is a no-op.
    """
    seen = query_one(
        "SELECT key FROM settings WHERE key = $1",
        [_MEMORY_PROMPT_LINES_RECONCILED],
    )
    if seen is not None:
        return
    for key, pairs in _MEMORY_PROMPT_LINE_EDITS.items():
        row = query_one("SELECT value, category FROM settings WHERE key = $1", [key])
        if row is None:
            logger.warning("Prompt setting '%s' is missing; the memory prompt lines were not applied", key)
            continue
        stored = str(row.get("value") or "")
        if stored == load_default_prompt(key):
            continue
        lines = stored.splitlines(keepends=True)
        changed = False
        for old_line, new_line in pairs:
            index = next(
                (i for i, line in enumerate(lines) if line.rstrip("\r\n") == old_line),
                None,
            )
            if index is None:
                logger.warning(
                    "Prompt setting '%s': the line %r was not found; review that prompt by hand "
                    "for the agent-memory wording",
                    key,
                    old_line,
                )
                continue
            ending = lines[index][len(old_line):]
            lines[index] = new_line + ending
            changed = True
        if changed:
            set_setting(key, "".join(lines), str(row["category"]))
            logger.info("Applied the agent-memory prompt lines to prompt setting: %s", key)
    set_setting(_MEMORY_PROMPT_LINES_RECONCILED, "true", "advanced")


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
