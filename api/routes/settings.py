"""Settings, AI connections, connection test, and personalities."""

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
from core import config
from core.agent_loop.standing_prefs import WARM_PREFIX_MAX_CHARS, WARM_SECTION_HEADER
from core.bm_cli.approval_gate import GLOBAL_AUTO_APPROVE_SETTING
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
    AIPersonality,
    AIPersonalityCreate,
    AIPersonalityUpdate,
)
from core.runtime import runtime_services
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
    if category in {"simulation", "social", "context", "desk"}:
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
})
_POSITIVE_INT_SETTING_LABELS = {
    "system_ai_max_tokens": "System AI max output tokens",
    "system_ai_timeout_seconds": "System AI timeout",
    "standing_prefs_line_max_chars": "Standing Pref Line Limit",
    "standing_prefs_section_max_chars": "Standing Prefs Section Limit",
    "channel_response_round_cap": "Round cap per message",
    "channel_idle_check_delay_seconds": "Idle check delay",
    "channel_idle_check_interval_seconds": "Idle check scan interval",
    "channel_idle_check_max_age_minutes": "Idle check max age",
    "channel_idle_check_max_wakes": "Idle check max wakes",
    "channel_idle_check_max_attempts": "Idle check attempts",
    "schedule_max_sleep_seconds": "Schedule clock re-check",
    "schedule_fire_grace_seconds": "Schedule on-time window",
}
_NON_NEGATIVE_INT_SETTING_LABELS = {
    "channel_router_transcript_messages": "Router transcript lines",
}
_BOOLEAN_SETTING_LABELS = {
    "channel_idle_check_enabled": "Idle check",
    # The approval gate refuses to guess at any other value.
    GLOBAL_AUTO_APPROVE_SETTING: "Global auto-approve",
}


def _validate_positive_int_setting(key: str, value: str) -> None:
    """Reject a value for a ``POSITIVE_INT_SETTINGS`` key that is not a whole number ≥ 1.

    ``system_ai_max_tokens``, ``system_ai_timeout_seconds`` and the two
    standing-prefs limits are read with
    ``config.require_int``: System AI completions, and every standing-prefs
    save and warm render. A bad value (``6k``, ``0``) would fail all of them,
    so it is rejected here, at the write boundary, instead. The thread keys
    (``channel_response_round_cap`` and the ``channel_idle_check_*``
    numbers) are read with ``config.get_int`` / ``get_float`` plus a
    fallback; they are rejected here so the operator's value is never
    silently replaced by that fallback. The two ``schedule_*`` keys are read
    with ``config.require_int`` by the runtime worker's schedule watch,
    which refuses to run on a bad value.

    For the prefs limits it also requires section ≥ line +
    ``WARM_PREFIX_MAX_CHARS`` + the warm header and its newline, reading the
    other limit from ``config``. The line limit counts pref text only, but
    the section counts whole rendered lines, so this is the least room that
    always holds one full-limit pref with the longest kind and id.

    Args:
        key: Setting key being written. Other keys are not checked.
        value: Raw value from the request.

    Raises:
        HTTPException: 400 when the stripped value is not a base-10 integer
            of at least 1, naming the setting's label; or 400 naming both
            prefs limits and the required minimum when the section limit
            could not hold one full pref line.
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
            f"Standing Prefs Section Limit ({section}) must be at least {minimum}: "
            f"the line limit plus room for one pref's label (Standing Pref Line Limit is {line}).",
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
    users = db.list_agents_by_connection(connection_id)
    if users:
        names = ", ".join(agent.name for agent in users)
        raise HTTPException(409, f"Used by: {names}. Move them to another connection first.")
    if not db.delete_connection(connection_id):
        raise HTTPException(404, "Connection not found")
    await _broadcast_operator_surfaces(["connections"])


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


# ─── AI Personalities CRUD ───

@router.get("/personalities")
async def list_personalities() -> list[AIPersonality]:
    return db.list_personalities()


@router.get("/personalities/{personality_id}")
async def get_personality(personality_id: str) -> AIPersonality:
    p = db.get_personality(personality_id)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.post("/personalities", status_code=201)
async def create_personality(body: AIPersonalityCreate) -> AIPersonality:
    try:
        _validate_authored_prompt_template(body.prompt_template)
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return db.create_personality(
        name=body.name,
        prompt_template=body.prompt_template,
    )


@router.patch("/personalities/{personality_id}")
async def update_personality(personality_id: str, body: AIPersonalityUpdate) -> AIPersonality:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "No fields to update")
    prompt_template = fields.get("prompt_template")
    if isinstance(prompt_template, str):
        try:
            _validate_authored_prompt_template(prompt_template)
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    p = db.update_personality(personality_id, **fields)
    if not p:
        raise HTTPException(404, "Personality not found")
    return p


@router.delete("/personalities/{personality_id}", status_code=204)
async def delete_personality(personality_id: str):
    if not db.delete_personality(personality_id):
        raise HTTPException(404, "Personality not found")
