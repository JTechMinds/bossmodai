"""Settings, AI connections, and connection test."""

import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.redaction import serialize_connection, serialize_setting, serialize_settings
from api.routes._shared import (
    _RUNTIME_CONTRACT_KEYS,
    _available_folder_opener_options,
    _validate_authored_prompt_template,
)
from api.websocket import manager
from core import boss, config
from core.agent_loop.standing_prefs import WARM_PREFIX_MAX_CHARS, WARM_SECTION_HEADER
from core.context_budget import (
    COMPACTION_KEEP_SETTING,
    COMPACTION_START_SETTING,
    CONTEXT_PRESETS,
    HISTORY_SETTING,
    NOTES_MAX_SETTING,
    PRESET_SETTINGS,
    SETTING_LABELS,
    SHARE_SETTINGS,
    WINDOW_SETTING,
    ContextPreset,
    active_preset,
    check_shares,
    compute_budget,
    find_preset,
    read_shares,
    reserve_of,
)
from core.bm_cli.approval_gate import GLOBAL_AUTO_APPROVE_SETTING
from core.llm import connection_health
from core.llm.call_budget import local_capacity_warning, slots_from_payload
from core.llm.connection_url import ConnectionUrlError, is_loopback_base, validate_connection_test_url
from core.llm.template_engine import TemplateError
from core.llm.system_completion import resolve_system_connection
from core.llm.thinking import unoffered
from core.models.thinking import THINKING_CHOICES, ThinkingLevels
from core.models import (
    AIConnection,
    AIConnectionCreate,
    AIConnectionUpdate,
)
from core.runtime import runtime_services
from core.runtime.services import request_dispatcher_wake
from integrations.telegram.auth import parse_allowed_user_ids
import db

router = APIRouter()

_IMAGE_FLAG_NEEDS_MODEL = "Set a model before marking it image-capable"


def _operator_surfaces_for_setting(key: str, category: str) -> list[str]:
    """Map one persisted setting to the Settings section ids the UI owns."""
    if key in {"system_ai_connection", "system_ai_thinking"}:
        return ["connections"]
    # Global auto-approve also changes every conversation's auto-approve
    # switch, so an open conversation refetches its header (`chat`).
    if key == GLOBAL_AUTO_APPROVE_SETTING:
        return ["advanced-system", "chat"]
    if category == "llm" and key.startswith("compaction_"):
        return ["system"]
    # Conversation memory changes what an open transcript marks as outside
    # the agent's context, so the open conversation refetches too.
    if key in CONVERSATION_COMPACTION_SETTINGS:
        return ["system", "chat"]
    if category in {"profile", "simulation", "social", "context", "desk"}:
        return ["system"]
    if category == "advanced":
        return ["advanced-system"]
    if key in _RUNTIME_CONTRACT_KEYS.values():
        return ["runtime-contracts"]
    if key == "system_prompt_template":
        return ["prompt-template"]
    if category == "telegram" or key.startswith("telegram_"):
        return ["telegram"]
    return ["system"]


async def _broadcast_operator_surfaces(surfaces: list[str]) -> None:
    await manager.broadcast_operator_invalidate(surfaces)


# ─── Settings ───

@router.get("/settings")
async def get_settings(category: str | None = None):
    return serialize_settings(db.get_settings(category))


@router.get("/settings/desktop-open-folder-options")
async def get_desktop_open_folder_options():
    return {
        "current": config.get("desktop_open_folder_handler"),
        "options": _available_folder_opener_options(),
    }


@router.post("/settings/reseed")
async def reseed_settings():
    """Force all seed settings back to their defaults."""
    db.force_reseed()
    config.reload()
    return {"status": "ok", "detail": "All seed settings reset to defaults"}


@router.post("/settings/reseed-application")
async def reseed_application():
    """Recreate the brand-new application database from the current schema.

    This clears DB state and agent desk workspaces (/me). Shared project files
    (/projects) and saved AI connections are preserved.
    """
    await runtime_services.reseed_application_data()
    await manager.broadcast_world_state()
    await manager.broadcast_activity(
        event="application_reseeded",
        detail="Application data reseeded from the current schema defaults (project files and AI connections preserved)",
    )
    return {"status": "ok", "detail": "Application database recreated from current schema defaults"}


def _percent_of(part: int, whole: int) -> int:
    """``part`` as a whole percent of ``whole``, rounded half up."""
    return (part * 100 + whole // 2) // whole


def _context_budget_payload() -> dict:
    """The Context Window tab's numbers: every budget, the reserve and whose it is, the worst cases and the presets.

    The budget and ``reserved_by`` come from one read of the largest stored
    instruction measurement, so the agent the tab names is always the one
    whose instructions the numbers were computed with.

    Stored settings that break a rule are not a server error: the tab is
    where the operator fixes them, so they come back as ``problems`` with
    what the tab needs to offer a fix (the presets and the reserve). The
    save routes refuse such values, so this is a damaged settings row, or
    an agent's measured instructions have outgrown the window since it was
    saved (the problem names the agent).

    Returns:
        Always ``problems`` (``list[str]``), ``presets``,
        ``reserved``, ``reserved_by`` and ``active_preset``. When
        ``problems`` is empty, also every budget, the worst cases and
        ``values`` (see :func:`get_context_budget`). Otherwise those are
        left out, because no budget can be computed, and ``active_preset``
        is ``None``. ``problems`` is one message when a setting is missing
        or not an integer, else every rule ``check_shares`` reports.

    Raises:
        Anything unexpected, such as the database failing, propagates: only
        a settings problem is reported instead of raised.
    """
    largest = db.largest_instruction_size()
    reserved, reserved_name = reserve_of(largest)
    reserved_by = None
    if largest is not None:
        reserved_by = {
            "agent_id": largest.agent_id,
            "agent_name": largest.agent_name,
            "measured_at": largest.measured_at.isoformat(),
        }
    # What the tab needs to offer a fix, whether or not a budget can be computed.
    fixable = {
        "reserved": reserved,
        "reserved_by": reserved_by,
        "active_preset": None,
        "presets": [{"id": preset.id, "label": preset.label} for preset in CONTEXT_PRESETS],
    }
    try:
        shares = read_shares()
    except config.ConfigError as exc:
        return {"problems": [str(exc)], **fixable}
    # check_shares, not checked_budget: each problem stays its own message.
    problems = check_shares(shares, reserved=reserved, reserved_by=reserved_name)
    if problems:
        return {"problems": problems, **fixable}
    try:
        values = {key: config.require_int(key) for key in PRESET_SETTINGS}
    except config.ConfigError as exc:
        return {"problems": [str(exc)], **fixable}
    budget = compute_budget(shares, reserved=reserved)
    return {
        "problems": [],
        "window": budget.window,
        "reserved": budget.reserved,
        "reserved_by": reserved_by,
        "usable": budget.usable,
        "reply": budget.reply,
        "result": budget.result,
        "history": budget.history,
        "history_cap": budget.history_cap,
        "asked": budget.asked,
        "task_log": budget.task_log,
        "work": budget.work,
        "worst_chat_turn": budget.worst_chat_turn,
        "worst_work_turn": budget.worst_work_turn,
        "worst_chat_percent": _percent_of(budget.worst_chat_turn, budget.window),
        "worst_work_percent": _percent_of(budget.worst_work_turn, budget.window),
        # The stored percents behind the numbers, for the tab's summaries.
        "values": values,
        "active_preset": active_preset(values),
        "presets": fixable["presets"],
    }


@router.get("/settings/context-budget")
async def get_context_budget():
    """Return the context window budget for Settings → System Settings → Context Window.

    Answers 200 even when the stored settings break a rule: the tab is where
    they are fixed, so it gets the problems and what it needs to offer a
    fix rather than an error (see :func:`_context_budget_payload`).

    Returns:
        One of two shapes, told apart by ``problems`` (``list[str]``).

        Always: ``problems``; ``presets`` as ``[{"id", "label"}]``;
        ``reserved`` and ``reserved_by`` as below; ``active_preset``.

        ``problems`` empty, the budget: ``window``, ``reserved``,
        ``usable``, ``reply``, ``result``,
        ``history``, ``history_cap``, ``asked``, ``task_log`` and ``work``
        in tokens (``core.context_budget.ContextBudget``; ``reserved`` is
        the largest measured instructions of any agent, 0 when none is
        measured yet); ``reserved_by``, the agent those instructions are,
        as ``{"agent_id", "agent_name", "measured_at"}`` (ISO 8601, UTC),
        or ``None`` before any measurement;
        ``worst_chat_turn`` and ``worst_work_turn`` in tokens with
        ``worst_chat_percent`` and ``worst_work_percent`` as whole percents
        of the window; ``values``, the stored setting behind each number;
        and ``active_preset`` (``"small"``, ``"medium"``, ``"large"``, or
        ``None`` when any value differs from every preset).

        ``problems`` not empty, the settings can't be used: a setting is
        missing or not an integer (one message naming it), or the values
        break the context rules, the measured instructions outgrowing the
        window included (one message per rule, naming the agent). No
        budget, worst case or ``values`` is sent, and ``active_preset`` is
        ``None``; saving a fixed value or applying a preset clears them.

    Raises:
        Anything unexpected, such as the database failing, propagates as a
        server error; a settings problem never does.
    """
    return _context_budget_payload()


def _preset_problems(preset: ContextPreset, *, reserved: int, reserved_by: str | None) -> list[str]:
    """Every rule a preset would break: ``check_shares`` at this reserve, and the conversation-memory ranges and order."""
    shares = read_shares({key: value for key, value in preset.values.items() if key in SHARE_SETTINGS})
    problems = check_shares(shares, reserved=reserved, reserved_by=reserved_by)
    for key in (COMPACTION_START_SETTING, COMPACTION_KEEP_SETTING, NOTES_MAX_SETTING):
        label, low, high = _PERCENT_SETTINGS[key]
        if not low <= preset.values[key] <= high:
            problems.append(f"{label} must be a whole number from {low} to {high}.")
    order = _compaction_order_problem(
        start=preset.values[COMPACTION_START_SETTING],
        keep=preset.values[COMPACTION_KEEP_SETTING],
        notes=preset.values[NOTES_MAX_SETTING],
    )
    if order is not None:
        problems.append(order)
    return problems


@router.post("/settings/context-presets/{preset_id}")
async def apply_context_preset(preset_id: str):
    """Apply a Context Window starting point: write every value of one preset in one save.

    Every value is written inside one transaction, so the stored settings
    are never half one preset and half another; each row keeps its stored
    category (the two work percents live under ``simulation``). Afterwards
    every value is an ordinary setting the operator can edit.

    Args:
        preset_id: ``small``, ``medium`` or ``large``.

    Returns:
        The Context Window payload (:func:`get_context_budget`) after the
        save, with this preset active.

    Raises:
        HTTPException: 404 for an unknown preset; 500 when the preset itself
            breaks a context rule with nothing reserved (a shipped value is
            wrong), naming every problem; 400 when it cannot hold the
            measured instructions (its window is at or below them), naming
            the agent. Both before anything is written.
    """
    try:
        preset = find_preset(preset_id)
    except KeyError as exc:
        raise HTTPException(404, f"There is no context preset named {preset_id!r}.") from exc
    problems = _preset_problems(preset, reserved=0, reserved_by=None)
    if problems:
        raise HTTPException(500, f"The {preset.label} preset breaks the context rules: {' '.join(problems)}")
    reserved, reserved_by = reserve_of(db.largest_instruction_size())
    unfit = _preset_problems(preset, reserved=reserved, reserved_by=reserved_by)
    if unfit:
        raise HTTPException(400, " ".join(unfit))
    categories = {row.key: row.category for row in db.get_settings()}
    with db.transaction():
        for key, value in preset.values.items():
            category = categories.get(key)
            if category is None:
                seeded = db.get_seed_setting_default(key)
                if seeded is None:
                    raise HTTPException(500, f"Setting '{key}' has no row and no seeded default.")
                category = seeded[1]
            db.set_setting(key, str(value), category)
    config.reload()
    await _broadcast_operator_surfaces(["system", "chat"])
    return _context_budget_payload()


@router.post("/settings/{key}/reset")
async def reset_setting_to_default(key: str):
    """Reset one seeded setting back to its default value.

    The seed value is checked like a PUT first: a default is not valid on
    its own for a paired limit (the prefs section default can sit below the
    minimum when the line limit was raised). A key with no seed skips the
    check; ``reset_setting_to_seed`` then refuses it with its own 400.
    """
    seeded = db.get_seed_setting_default(key)
    if seeded is not None:
        _validate_positive_int_setting(key, seeded[0])
        _validate_percent_setting(key, seeded[0])
        _validate_context_setting(key, seeded[0])
    try:
        result = db.reset_setting_to_seed(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config.reload()
    return serialize_setting(result)


def _validate_nest_git_settings(key: str, value: str) -> None:
    """Host Enable only persists On after the Shell probe passes."""
    from core.models.nest_git import NEST_GIT_HOST_ENABLED_KEY

    if key != NEST_GIT_HOST_ENABLED_KEY or value != "true":
        return
    from core.bm_cli.nest_git import probe_host_git_for_shell

    probe = probe_host_git_for_shell()
    if not probe.ok:
        raise HTTPException(400, probe.blocked_message())


def _validate_telegram_settings(key: str, value: str) -> None:
    """Reject Telegram enablement without a usable allowlist (SEC-P0-01)."""
    if key == "telegram_enabled" and value == "true":
        if not parse_allowed_user_ids(config.get("telegram_allowed_user_ids")):
            raise HTTPException(
                400,
                "Add at least one Telegram user ID before enabling the bot. "
                "An empty allowlist is deny-all and the bot will not start.",
            )
    if key == "telegram_allowed_user_ids":
        if not parse_allowed_user_ids(value) and config.get("telegram_enabled") == "true":
            raise HTTPException(
                400,
                "Cannot clear the Telegram allowlist while the bot is enabled. "
                "Disable Telegram first, or keep at least one user ID.",
            )


# Settings that must be a whole number of at least 1. Key → the label the
# 400 names, matching the Settings UI.
POSITIVE_INT_SETTINGS = frozenset({
    "system_ai_max_tokens",
    "system_ai_timeout_seconds",
    "standing_prefs_line_max_chars",
    "standing_prefs_section_max_chars",
    "channel_response_round_cap",
    "channel_idle_check_delay_seconds",
    "channel_idle_check_interval_seconds",
    "channel_idle_check_max_age_minutes",
    "channel_idle_check_max_wakes",
    "channel_idle_check_max_attempts",
    "schedule_max_sleep_seconds",
    "schedule_fire_grace_seconds",
    # Read with require_positive_int at every background launch and Keep.
    "background_process_checkin_minutes",
    # Read with require_positive_int at every history prune.
    "agent_process_retention_days",
    # Read with require_positive_int on every counted provider failure
    # (core/llm/connection_health.py).
    "llm_connection_failure_threshold",
    "llm_connection_backoff_seconds",
    "llm_connection_backoff_max_seconds",
})
_POSITIVE_INT_SETTING_LABELS = {
    "system_ai_max_tokens": "System AI max output tokens",
    "system_ai_timeout_seconds": "System AI timeout",
    "standing_prefs_line_max_chars": "Memory Line Limit",
    "standing_prefs_section_max_chars": "Memory Section Limit",
    "channel_response_round_cap": "Round cap per message",
    "channel_idle_check_delay_seconds": "Idle check delay",
    "channel_idle_check_interval_seconds": "Idle check scan interval",
    "channel_idle_check_max_age_minutes": "Idle check max age",
    "channel_idle_check_max_wakes": "Idle check max wakes",
    "channel_idle_check_max_attempts": "Idle check attempts",
    "schedule_max_sleep_seconds": "Schedule clock re-check",
    "schedule_fire_grace_seconds": "Schedule on-time window",
    "background_process_checkin_minutes": "Background process check-in (minutes)",
    "agent_process_retention_days": "Background process history (days)",
    "llm_connection_failure_threshold": "Failures before pausing a connection",
    "llm_connection_backoff_seconds": "Paused connection first retry wait",
    "llm_connection_backoff_max_seconds": "Paused connection longest retry wait",
}
_NON_NEGATIVE_INT_SETTING_LABELS = {
    "channel_router_transcript_messages": "Router transcript lines",
}
_BOOLEAN_SETTING_LABELS = {
    "channel_idle_check_enabled": "Idle check",
    # The approval gate refuses to guess at any other value.
    GLOBAL_AUTO_APPROVE_SETTING: "Global auto-approve",
    # Written by the one-time "What should your team call you?" dialog.
    "boss_name_prompted": "Name prompt answered",
    "conversation_compaction_enabled": "Conversation memory",
}

# Conversation-memory percents: key → (label the 400 names, lowest, highest).
_PERCENT_SETTINGS = {
    "conversation_compaction_start_percent": ("Start summarizing at", 1, 100),
    "conversation_compaction_keep_percent": ("Keep recent messages", 1, 100),
    "conversation_notes_max_percent": ("Notes size", 1, 100),
    "conversation_history_ceiling_percent": ("Hard limit", 100, 300),
}
# Settings that change what an open transcript marks as outside the agent's
# context: the conversation-memory knobs and the numbers that size History.
CONVERSATION_COMPACTION_SETTINGS = frozenset({
    "conversation_compaction_enabled",
    WINDOW_SETTING,
    HISTORY_SETTING,
    *_PERCENT_SETTINGS,
})


def _validate_positive_int_setting(key: str, value: str) -> None:
    """Reject a value for a ``POSITIVE_INT_SETTINGS`` key that is not a whole number ≥ 1.

    ``system_ai_max_tokens``, ``system_ai_timeout_seconds`` and the two
    agent memory limits are read with
    ``config.require_int``: System AI completions, and every memory
    save and warm render. A bad value (``6k``, ``0``) would fail all of them,
    so it is rejected here, at the write boundary, instead. The thread keys
    (``channel_response_round_cap`` and the ``channel_idle_check_*``
    numbers) are read with ``config.get_int`` / ``get_float`` plus a
    fallback; they are rejected here so the operator's value is never
    silently replaced by that fallback. The two ``schedule_*`` keys are read
    with ``config.require_int`` by the runtime worker's schedule watch,
    which refuses to run on a bad value.

    For the memory limits it also requires section ≥ line +
    ``WARM_PREFIX_MAX_CHARS`` + the warm header and its newline, reading the
    other limit from ``config``. The line limit counts memory text only, but
    the section counts whole rendered lines, so this is the least room that
    always holds one full-limit memory with the longest number.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 when the stripped value is not a base-10 integer
            of at least 1, naming the setting's label; or 400 naming both
            memory limits and the required minimum when the section limit
            could not hold one full memory line.
    """
    if key not in POSITIVE_INT_SETTINGS:
        return
    stripped = value.strip()
    # isascii + isdigit: base-10 digits only; int() alone would take "+5" or "1_000".
    if not (stripped.isascii() and stripped.isdigit()) or int(stripped, 10) < 1:
        raise HTTPException(400, f"{_POSITIVE_INT_SETTING_LABELS[key]} must be a whole number of at least 1.")
    written = int(stripped, 10)
    if key == "standing_prefs_line_max_chars":
        line, section = written, config.require_int("standing_prefs_section_max_chars")
    elif key == "standing_prefs_section_max_chars":
        line, section = config.require_int("standing_prefs_line_max_chars"), written
    else:
        return
    minimum = line + WARM_PREFIX_MAX_CHARS + len(WARM_SECTION_HEADER) + 1
    if section < minimum:
        raise HTTPException(
            400,
            f"Memory Section Limit ({section}) must be at least {minimum}: "
            f"the line limit plus room for one memory's number (Memory Line Limit is {line}).",
        )


def _validate_non_negative_int_setting(key: str, value: str) -> None:
    """Reject a value for a non-negative integer key that is not a whole number ≥ 0.

    0 means the router sees no transcript.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 naming the setting's label when the stripped
            value is not a base-10 integer of 0 or more.
    """
    label = _NON_NEGATIVE_INT_SETTING_LABELS.get(key)
    if label is None:
        return
    stripped = value.strip()
    # isascii + isdigit: base-10 digits only, so "-1" and "+5" are refused.
    if not (stripped.isascii() and stripped.isdigit()):
        raise HTTPException(400, f"{label} must be a whole number of 0 or more.")


def _validate_boolean_setting(key: str, value: str) -> None:
    """Reject a value for a boolean key that is not exactly ``true`` or ``false``.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 naming the setting's label for any other value.
    """
    label = _BOOLEAN_SETTING_LABELS.get(key)
    if label is None:
        return
    if value not in {"true", "false"}:
        raise HTTPException(400, f"{label} must be true or false.")


def _validate_percent_setting(key: str, value: str) -> None:
    """Reject a conversation-memory percent that is out of range or breaks the pair rules.

    Each percent is a whole number in its range ([1, 100], and [100, 300] for
    the hard limit). Across keys, ``keep < start <= 100`` and ``notes < keep``
    must hold with the value being written and the others read from
    ``config``, so the stored settings can never stop
    ``core.agent_loop.conversation_notes.read_settings`` from loading.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 naming the setting's label and the range, or 400
            naming the pair rule that the value would break.
    """
    spec = _PERCENT_SETTINGS.get(key)
    if spec is None:
        return
    label, low, high = spec
    stripped = value.strip()
    # isascii + isdigit: base-10 digits only, so "-1", "+5" and "1_0" are refused.
    if not (stripped.isascii() and stripped.isdigit()) or not low <= int(stripped, 10) <= high:
        raise HTTPException(400, f"{label} must be a whole number from {low} to {high}.")
    written = int(stripped, 10)
    problem = _compaction_order_problem(
        start=written if key == COMPACTION_START_SETTING else config.require_int(COMPACTION_START_SETTING),
        keep=written if key == COMPACTION_KEEP_SETTING else config.require_int(COMPACTION_KEEP_SETTING),
        notes=written if key == NOTES_MAX_SETTING else config.require_int(NOTES_MAX_SETTING),
    )
    if problem is not None:
        raise HTTPException(400, problem)


def _compaction_order_problem(*, start: int, keep: int, notes: int) -> str | None:
    """The conversation-memory order rule a set of percents breaks, if any.

    ``keep < start`` and ``notes < keep``; shared by the single-setting save
    (:func:`_validate_percent_setting`) and the presets.

    Returns:
        The message naming the Settings labels, or ``None`` when the order holds.
    """
    if keep >= start:
        return (
            f"Keep recent messages ({keep}%) must be lower than Start summarizing at ({start}%), "
            "or there would be nothing to summarize."
        )
    if notes >= keep:
        return f"Notes size ({notes}%) must be lower than Keep recent messages ({keep}%)."
    return None


def _validate_context_setting(key: str, value: str) -> None:
    """Refuse a Context Window value that would let any turn outgrow the window.

    For every key ``core.context_budget.check_shares`` governs (the window,
    the six shares of the usable window, Hard limit, Work kept after
    summarizing and Work summary size): the value must be a base-10 whole
    number, and the stored settings with this one value replaced must pass
    ``check_shares`` at the current reserve (the largest measured
    instructions, ``db.largest_instruction_size``), the single source of the
    rule that a chat turn and a work turn both fit. So no saved combination
    can stop a turn with ``ConfigError``; a window at or below the reserve
    is refused naming the agent whose instructions it is.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 when the value is not a whole number, or with the
            first problem ``check_shares`` reports (in Settings labels).
    """
    if key not in SHARE_SETTINGS:
        return
    stripped = value.strip()
    # isascii + isdigit: base-10 digits only, so "-1", "+5" and "1_0" are refused.
    if not (stripped.isascii() and stripped.isdigit()):
        raise HTTPException(400, f"{SETTING_LABELS[key]} must be a whole number.")
    reserved, reserved_by = reserve_of(db.largest_instruction_size())
    problems = check_shares(read_shares({key: int(stripped, 10)}), reserved=reserved, reserved_by=reserved_by)
    if problems:
        raise HTTPException(400, problems[0])


def _validate_boss_name_setting(key: str, value: str) -> str:
    """Validate ``boss_name`` and return the value to store.

    Args:
        key: Setting key being written. Other keys pass through unchanged.
        value: Raw value from the request.

    Returns:
        The trimmed name for ``boss_name`` (``""`` means unset), else ``value``.

    Raises:
        HTTPException: 400 with the reason from
            :func:`core.boss.validate_boss_name` (too long, multi-line, ``@``,
            reserved, or an existing agent's name).
    """
    if key != "boss_name":
        return value
    try:
        return boss.validate_boss_name(value, agent_names=[agent.name for agent in db.list_agents()])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _validate_system_ai_thinking(key: str, value: str) -> None:
    """Reject a ``system_ai_thinking`` value the System AI connection cannot apply.

    ``complete_text`` merges the choice like an agent's, so a level the
    resolved System AI connection does not offer would only fail later, at
    call time. It is refused here instead.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 when the value is not a thinking choice; when it
            is a level and there is no usable System AI connection; or when
            the System AI connection does not offer that level.
    """
    if key != "system_ai_thinking":
        return
    if value not in THINKING_CHOICES:
        raise HTTPException(400, f"System AI thinking must be one of: {', '.join(THINKING_CHOICES)}.")
    if value == "default":
        return
    connection = resolve_system_connection()
    if connection is None:
        raise HTTPException(
            400,
            "There is no usable System AI connection, so only Server default can be chosen for System AI thinking.",
        )
    if unoffered(connection.thinking_levels, {"system_ai_thinking": value}):
        raise HTTPException(
            400,
            f"System AI connection '{connection.name}' does not offer thinking level '{value}'. "
            "Add it to the connection's thinking levels, or choose Server default.",
        )


def _system_connection_after(connection_id: str) -> AIConnection | None:
    """Return the connection System AI would use with ``connection_id`` saved.

    The same rule as ``resolve_system_connection``, applied to the value
    being written rather than the stored one: an id that names a connection
    is used, otherwise the first connection, otherwise None.
    """
    if connection_id:
        connection = db.get_connection_by_id(connection_id)
        if connection is not None:
            return connection
    connections = db.list_connections()
    return connections[0] if connections else None


def _refuse_orphaning_system_ai_thinking(key: str, value: str) -> None:
    """Raise 409 when a new System AI connection would not offer the stored level.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: The System AI connection id being saved.

    Raises:
        HTTPException: 409 naming the stored level and the connection.
    """
    if key != "system_ai_connection":
        return
    stored = config.get("system_ai_thinking")
    if stored is None:
        return
    connection = _system_connection_after(value.strip())
    levels = connection.thinking_levels if connection is not None else None
    if unoffered(levels, {"system_ai_thinking": stored}):
        named = f"connection '{connection.name}'" if connection is not None else "no connection"
        raise HTTPException(
            409,
            f"System AI thinking is '{stored}', which {named} does not offer. "
            "Set System AI thinking to Server default or an offered level first.",
        )


# Settings with their own route, which validates what the generic PUT cannot
# (enabling an extension needs its setup to be ready).
_OWN_ROUTE_SETTINGS = {
    "extensions_enabled": "Extensions are turned on and off in Add → Extensions (PUT /api/extensions/{id}/enabled).",
}


@router.put("/settings/{key}")
async def set_setting(key: str, value: str, category: str = "general"):
    if key in _OWN_ROUTE_SETTINGS:
        raise HTTPException(400, _OWN_ROUTE_SETTINGS[key])
    if key == "system_prompt_template" or key in _RUNTIME_CONTRACT_KEYS.values():
        try:
            _validate_authored_prompt_template(value)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    _validate_telegram_settings(key, value)
    _validate_nest_git_settings(key, value)
    _validate_positive_int_setting(key, value)
    _validate_non_negative_int_setting(key, value)
    _validate_boolean_setting(key, value)
    _validate_percent_setting(key, value)
    _validate_context_setting(key, value)
    value = _validate_boss_name_setting(key, value)
    _validate_system_ai_thinking(key, value)
    _refuse_orphaning_system_ai_thinking(key, value)
    if key == "workspace_host_roots":
        from core.bm_cli.host_roots import SETTING_CATEGORY, normalize_host_root_setting

        try:
            value = normalize_host_root_setting(value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        category = SETTING_CATEGORY
    try:
        result = db.set_setting(key, value, category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config.reload()  # Invalidate cache so changes take effect immediately
    await _broadcast_operator_surfaces(_operator_surfaces_for_setting(key, category))
    return serialize_setting(result)


# ─── AI Connections CRUD ───

def _require_unmanaged(connection_id=None, endpoint=None):
    """One write policy for every connection owned by an extension."""
    from core.extensions.model_provider import ProviderError, provider_id
    stored = db.get_connection_by_id(connection_id) if connection_id else None
    try:
        managed = provider_id(endpoint) or (provider_id(stored.api_base_url) if stored else None)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from None
    if managed:
        raise HTTPException(409, "Manage this connection in Extensions. Its extension owns authentication and model settings.")


@router.get("/connections")
async def list_connections():
    return [serialize_connection(conn) for conn in db.list_connections()]


@router.get("/connections/{connection_id}")
async def get_connection(connection_id: str):
    conn = db.get_connection_by_id(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return serialize_connection(conn)


@router.post("/connections", status_code=201)
async def create_connection(body: AIConnectionCreate):
    """Create an AI connection and, when sent, record its model's image flag.

    The flag is keyed by model name and shared with every other connection
    naming that model, so an omitted flag writes nothing.

    Raises:
        HTTPException: 400 when ``supports_images`` is set without a model,
            since the flag is keyed by model name.
    """
    _require_unmanaged(endpoint=body.api_base_url)
    has_model = bool(body.model and body.model.strip())
    if body.supports_images and not has_model:
        raise HTTPException(400, _IMAGE_FLAG_NEEDS_MODEL)
    created = db.create_connection(
        name=body.name,
        api_base_url=body.api_base_url,
        api_key=body.api_key,
        model=body.model,
        extra_body=body.extra_body,
        thinking_levels=body.thinking_levels,
    )
    if has_model and body.supports_images is not None:
        db.set_supports_images(body.model, body.supports_images)
    conn = serialize_connection(created)
    await _broadcast_operator_surfaces(["connections"])
    return conn


@router.patch("/connections/{connection_id}")
async def update_connection(connection_id: str, body: AIConnectionUpdate):
    """Patch an AI connection and, when sent, its model's image flag.

    The flag is written for the effective model (the patched ``model`` if
    present, else the stored one) after the connection update succeeds.

    Raises:
        HTTPException: 400 when nothing is sent, or ``supports_images`` is
            true with no effective model; 404 for an unknown connection;
            409 when the new ``thinking_levels`` drops a level agents on
            this connection, or System AI on it, still pick (the detail
            names them).
    """
    _require_unmanaged(connection_id, body.api_base_url)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    if fields.get("api_key") == "":
        fields.pop("api_key", None)
    if "thinking_levels" in fields:
        _refuse_removing_used_levels(connection_id, fields["thinking_levels"])
    image_flag = fields.pop("supports_images", None)
    effective_model: str | None = None
    if image_flag is not None:
        existing = db.get_connection_by_id(connection_id)
        if existing is None:
            raise HTTPException(404, "Connection not found")
        effective_model = fields["model"] if "model" in fields else existing.model
        if image_flag and not (effective_model and effective_model.strip()):
            raise HTTPException(400, _IMAGE_FLAG_NEEDS_MODEL)
    conn = db.update_connection(connection_id, **fields)
    if not conn:
        raise HTTPException(404, "Connection not found")
    if image_flag is not None and effective_model and effective_model.strip():
        db.set_supports_images(effective_model, image_flag)
    await _broadcast_operator_surfaces(["connections"])
    return serialize_connection(conn)


def _refuse_removing_used_levels(connection_id: str, new_levels: ThinkingLevels | None) -> None:
    """Raise 409 when ``new_levels`` lacks a level the connection's users pick.

    Its users are the agents linked to it and, when it is the resolved
    System AI connection, System AI with its ``system_ai_thinking`` choice.

    Raises:
        HTTPException: 409 naming each agent, and "System AI", with the
            levels it would lose.
    """
    in_use = []
    for agent in db.list_agents_by_connection(connection_id):
        missing = unoffered(
            new_levels or None,
            {"thinking_social": agent.thinking_social, "thinking_work": agent.thinking_work},
        )
        if missing:
            in_use.append(f"{agent.name} ({', '.join(missing)})")
    system = resolve_system_connection()
    stored = config.get("system_ai_thinking")
    if system is not None and system.id == connection_id and stored is not None:
        missing = unoffered(new_levels or None, {"system_ai_thinking": stored})
        if missing:
            in_use.append(f"System AI ({', '.join(missing)})")
    if in_use:
        raise HTTPException(
            409,
            f"Still picked by: {'; '.join(in_use)}. Change their thinking level first.",
        )


@router.post("/connections/{connection_id}/duplicate", status_code=201)
async def duplicate_connection(connection_id: str):
    """Copy a connection server-side and return the copy, redacted as usual.

    There is no request body because there is nothing the caller could send:
    the API key is the one field a copy must carry and the one field the
    browser has never had — responses only ever expose ``has_api_key`` and the
    last four. A client-side duplicate would have to re-ask for the key or
    write a keyless row, so the copy is made where the key already is.
    """
    _require_unmanaged(connection_id)
    conn = db.duplicate_connection(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    await _broadcast_operator_surfaces(["connections"])
    return serialize_connection(conn)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str):
    """Delete an AI connection no agent uses.

    Raises:
        HTTPException: 409 naming the agents linked to it; 404 when unknown.
    """
    _require_unmanaged(connection_id)
    users = db.list_agents_by_connection(connection_id)
    if users:
        names = ", ".join(agent.name for agent in users)
        raise HTTPException(409, f"Used by: {names}. Move them to another connection first.")
    if not db.delete_connection(connection_id):
        raise HTTPException(404, "Connection not found")
    await _broadcast_operator_surfaces(["connections"])


@router.post("/connections/{connection_id}/retry")
async def retry_connection(connection_id: str):
    """Let a paused AI connection make one test call now: the Needs card's "Retry now".

    The next model call on the connection becomes its test call; the
    dispatcher is woken so held work is tried now rather than on its next
    poll. A success resumes the connection; a failure keeps it paused.
    Not limited to unmanaged connections: retrying edits nothing, and an
    extension-managed connection (after a usage limit resets) needs it too.

    Returns:
        ``{"ok": True}`` when the test call is now due.

    Raises:
        HTTPException: 404 when the connection is unknown; 409 when it is
            not paused.
    """
    if db.get_connection_by_id(connection_id) is None:
        raise HTTPException(404, "Connection not found")
    if not connection_health.retry_now(connection_id):
        raise HTTPException(409, "This connection is not paused.")
    request_dispatcher_wake()
    return {"ok": True}


class TestConnectionBody(BaseModel):
    api_base_url: str
    api_key: str | None = None
    model: str | None = None
    connection_id: str | None = None


@router.post("/connections/test")
async def test_connection(body: TestConnectionBody):
    """Test an AI connection by hitting GET {base_url}/models.

    Verifies the host is reachable, auth works, and the response
    is OpenAI-compatible. Optionally checks the model exists.
    """
    from core.extensions.model_provider import ProviderError, provider_id, resolve_provider
    try:
        ext_id = provider_id(body.api_base_url)
        if ext_id is not None:
            status = await asyncio.to_thread(resolve_provider(ext_id).provider_status)
            return {"ok": status.connected, "error": None if status.connected else status.message}
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        base = validate_connection_test_url(body.api_base_url).rstrip("/")
    except ConnectionUrlError as exc:
        return {"ok": False, "error": str(exc)}
    if base.endswith("/chat/completions") or base.endswith("/completions"):
        return {
            "ok": False,
            "error": "Use the API base URL, not a completions endpoint. Example: https://host/v1",
        }

    api_key = body.api_key
    if not api_key and body.connection_id:
        stored = db.get_connection_by_id(body.connection_id)
        if stored is not None:
            api_key = stored.api_key

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(base + "/models", headers=headers)
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection failed — check the URL"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Connection timed out after 10s"}
    except Exception as exc:
        return {"ok": False, "error": f"Request failed: {exc}"}

    if resp.status_code == 401:
        return {"ok": False, "error": "Authentication failed — check your API key"}
    if resp.status_code == 403:
        return {"ok": False, "error": "Access denied — API key lacks permissions"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"Server returned {resp.status_code}"}

    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "error": "Response is not valid JSON"}

    models_list = data.get("data")
    if not isinstance(models_list, list):
        return {"ok": False, "error": "Response missing 'data' array — may not be OpenAI-compatible"}

    model_ids = [m.get("id", "") for m in models_list]
    capacity_note = await _local_capacity_note(base, headers)

    if body.model and body.model not in model_ids:
        warning = f"Connected, but model '{body.model}' not found in {len(model_ids)} available models"
        if capacity_note:
            warning = f"{warning} {capacity_note}"
        return {
            "ok": True,
            "warning": warning,
            "models": model_ids[:20],
        }

    payload = {
        "ok": True,
        "models_count": len(model_ids),
        "models": model_ids[:20],
    }
    if capacity_note:
        payload["warning"] = capacity_note
    return payload


async def _local_capacity_note(base: str, headers: dict[str, str]) -> str | None:
    """Warn when a local server reports fewer parallel slots than the knob.

    A missing or unreadable slot list is not a capacity. The probe is not a setting.
    """
    if not is_loopback_base(base):
        return None
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(base + "/slots", headers=headers)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    return local_capacity_warning(slots_from_payload(payload))

