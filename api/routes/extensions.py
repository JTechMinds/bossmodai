"""Extensions: list, enable/disable, one-click setup, per-agent config and view.

The enabled set is the ``extensions_enabled`` setting; this is the only route
that writes it, because enabling needs the extension to be valid and set up.
The runtime worker reads it live, so nothing here touches runtime internals.

Per-agent config (manifest ``agent_config``) is stored by the host, wrapped
at rest, and verified through the extension before it is stored. Secret
fields never leave the backend: reads say only whether one is set. Calls
into an extension that may block on the network run in a worker thread.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

import db
from api.websocket import manager
from core.extensions.contract import (
    AgentConfigError,
    AgentViewError,
    Extension,
    LiveViewItem,
    SupportsAgentConfig,
    SupportsAgentView,
    SupportsLiveView,
)
from core.extensions.manifest import AgentConfigField, AgentConfigSpec, agent_config_value
from core.extensions.loader import ExtensionLoadError, contract_failure, load_extension
from core.extensions.paths import extension_data_dir
from core.extensions.registry import ExtensionEntry, enabled_ids, get_discovery, set_enabled
from core.extensions.setup_runner import SetupAlreadyRunning, entry_setup_status, start_setup
from core.llm.routing import select_model_with_source
from db.model_capabilities import supports_images

router = APIRouter()


class EnabledBody(BaseModel):
    """``PUT /extensions/{id}/enabled`` body."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SetupBody(BaseModel):
    """``POST /extensions/{id}/setup`` body."""

    model_config = ConfigDict(extra="forbid")

    enable_on_success: bool


class AgentConfigBody(BaseModel):
    """``PUT /extensions/{id}/agents/{agent_id}/config`` body."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, str]


# An address shape check, not RFC validation: the service is the authority.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"error": code, "message": message})


def _invalid(code: str, message: str) -> HTTPException:
    return HTTPException(422, {"error": code, "message": message})


def _entry(ext_id: str) -> ExtensionEntry:
    entry = get_discovery().get(ext_id)
    if entry is None:
        raise HTTPException(404, f"No extension {ext_id!r} is installed")
    return entry


def _excluded_agents() -> list[dict[str, Any]]:
    """Agents whose work-mode model is not flagged image-capable.

    ``work`` is the mode agents run CLI commands in (routing picks it for
    every trigger except ``social``), so it is the model ``bv`` checks.
    """
    excluded = []
    for agent in db.list_agents():
        model, _source = select_model_with_source(agent, "work")
        if model is None or not supports_images(model):
            excluded.append({"id": agent.id, "name": agent.name, "model": model})
    return excluded


def _item(entry: ExtensionEntry, enabled: frozenset[str]) -> dict[str, Any]:
    """One extension as the Extensions dialog renders it."""
    manifest = entry.manifest
    if manifest is None:
        return {
            "id": entry.id,
            "name": entry.id,
            "version": None,
            "description": None,
            "command": None,
            "enabled": False,
            "valid": False,
            "invalid_reason": entry.invalid_reason,
            "requires_image_model": False,
            "live_view": False,
            "agent_config": None,
            "agent_view": None,
            "setup": None,
            "setup_label": None,
            "excluded_agents": [],
        }
    requires_image = manifest.requires.image_model
    # A contract broken at load (see loader.contract_failure) makes it invalid.
    broken = contract_failure(entry.id)
    valid = entry.valid and broken is None
    return {
        "id": entry.id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "command": {"name": manifest.command.name, "summary": manifest.command.summary},
        "enabled": valid and entry.id in enabled,
        "valid": valid,
        "invalid_reason": entry.invalid_reason or broken,
        "requires_image_model": requires_image,
        "live_view": manifest.live_view,
        "agent_config": {"label": manifest.agent_config.label} if manifest.agent_config else None,
        "agent_view": {
            "label": manifest.agent_view.label,
            "views": [{"key": view.key, "label": view.label} for view in manifest.agent_view.views],
        } if manifest.agent_view else None,
        "setup": entry_setup_status(entry).model_dump(),
        "setup_label": manifest.setup.label,
        "excluded_agents": _excluded_agents() if requires_image and valid else [],
    }


@router.get("/extensions")
async def list_extensions() -> list[dict[str, Any]]:
    """List every extension folder found at start, valid or not."""
    enabled = enabled_ids()
    return [_item(entry, enabled) for entry in get_discovery().entries]


@router.put("/extensions/{ext_id}/enabled")
async def set_extension_enabled(ext_id: str, body: EnabledBody) -> dict[str, Any]:
    """Turn one extension on or off.

    Raises:
        HTTPException: 404 unknown id; 409 ``INVALID_EXTENSION``; 409
            ``SETUP_REQUIRED`` when enabling before setup is ready.
    """
    entry = _entry(ext_id)
    if not entry.valid:
        raise _conflict("INVALID_EXTENSION", entry.invalid_reason or "This extension is invalid.")
    if body.enabled:
        state = entry_setup_status(entry).state
        if state not in {"ready", "not_required"}:
            raise _conflict("SETUP_REQUIRED", f"Set up {entry.manifest.name} before turning it on (setup is {state}).")
    set_enabled(ext_id, body.enabled)
    # Turning a live-view extension off empties its live view and on again
    # may bring one back; open chat headers re-read /live on this nudge.
    await manager.broadcast_extension_live(ext_id, None)
    return _item(entry, enabled_ids())


@router.post("/extensions/{ext_id}/setup", status_code=202)
async def start_extension_setup(ext_id: str, body: SetupBody) -> dict[str, Any]:
    """Start the extension's one-click setup in the background.

    Raises:
        HTTPException: 404 unknown id; 409 ``INVALID_EXTENSION``; 409
            ``NO_SETUP`` when it has none; 409 ``SETUP_RUNNING`` when a setup
            is already in progress; 500 when the extension cannot load.
    """
    entry = _entry(ext_id)
    if not entry.valid:
        raise _conflict("INVALID_EXTENSION", entry.invalid_reason or "This extension is invalid.")
    if not entry.manifest.setup.required:
        raise _conflict("NO_SETUP", f"{entry.manifest.name} has no setup step.")
    try:
        start_setup(entry, enable_on_success=body.enable_on_success)
    except SetupAlreadyRunning as exc:
        raise _conflict("SETUP_RUNNING", str(exc)) from exc
    except ExtensionLoadError as exc:
        raise HTTPException(500, str(exc)) from exc
    return _item(entry, enabled_ids())


def _live_items(ext_id: str) -> list[LiveViewItem]:
    """Load an enabled live-view extension and return its items.

    Loading happens only here, for an enabled extension (D10); constructing
    Browser Vision does not start its browser.

    Raises:
        HTTPException: 404 unknown; 409 ``LIVE_VIEW_UNSUPPORTED``; 409
            ``EXTENSION_DISABLED``; 409 ``INVALID_EXTENSION`` when it cannot
            load or breaks its declared contract.
    """
    entry = _entry(ext_id)
    if not entry.valid:
        raise _conflict("INVALID_EXTENSION", entry.invalid_reason or "This extension is invalid.")
    if not entry.manifest.live_view:
        raise _conflict("LIVE_VIEW_UNSUPPORTED", f"{entry.manifest.name} has no live view.")
    if ext_id not in enabled_ids():
        raise _conflict("EXTENSION_DISABLED", f"{entry.manifest.name} is off.")
    try:
        instance = load_extension(entry)
    except ExtensionLoadError as exc:
        raise _conflict("INVALID_EXTENSION", str(exc)) from exc
    if not isinstance(instance, SupportsLiveView):
        raise _conflict("INVALID_EXTENSION", "the extension has no live_view() method")
    return instance.live_view()


@router.get("/extensions/{ext_id}/live")
async def extension_live_view(ext_id: str) -> dict[str, Any]:
    """Each agent's latest output for the operator's read-only live view, newest first.

    An agent that no longer exists is listed under its id with
    ``agent_missing: true`` rather than dropped.
    """
    items = []
    for item in _live_items(ext_id):
        agent = db.get_agent(item.agent_id)
        stamp = int(item.taken_at.timestamp() * 1000)
        row: dict[str, Any] = {
            "agent_id": item.agent_id,
            "agent_name": agent.name if agent is not None else item.agent_id,
            "taken_at": item.taken_at.isoformat(),
            "command": item.command,
            "url": item.url,
            "title": item.title,
            "caption_lines": item.caption_lines,
            "image_url": f"/api/extensions/{ext_id}/live/{item.agent_id}/image?t={stamp}",
        }
        if agent is None:
            row["agent_missing"] = True
        items.append(row)
    return {"items": items}


@router.get("/extensions/{ext_id}/live/{agent_id}/image")
async def extension_live_image(ext_id: str, agent_id: str) -> FileResponse:
    """Serve one agent's latest live-view image, never cached.

    Raises:
        HTTPException: 404 when the agent has no screenshot, is not one the
            extension lists, or the file resolves outside the extension's
            data dir; the ``_live_items`` conflicts otherwise.
    """
    match = next((item for item in _live_items(ext_id) if item.agent_id == agent_id), None)
    if match is None:
        raise HTTPException(404, "No screenshot for that agent")
    root = extension_data_dir(ext_id).resolve()
    path = Path(match.image_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "No screenshot for that agent")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


# ─── per-agent config and view ───


def _usable(entry: ExtensionEntry) -> bool:
    """Valid, not contract-broken, and enabled."""
    return entry.valid and contract_failure(entry.id) is None and entry.id in enabled_ids()


def _agent_or_404(agent_id: str) -> None:
    if db.get_agent(agent_id) is None:
        raise HTTPException(404, "Agent not found")


def _enabled_entry(ext_id: str) -> ExtensionEntry:
    """The entry for a per-agent route: known, valid, enabled.

    Raises:
        HTTPException: 404 unknown; 409 ``INVALID_EXTENSION``; 409
            ``EXTENSION_DISABLED``.
    """
    entry = _entry(ext_id)
    if not entry.valid:
        raise _conflict("INVALID_EXTENSION", entry.invalid_reason or "This extension is invalid.")
    broken = contract_failure(entry.id)
    if broken is not None:
        raise _conflict("INVALID_EXTENSION", broken)
    if ext_id not in enabled_ids():
        raise _conflict("EXTENSION_DISABLED", f"{entry.manifest.name} is off.")
    return entry


def _config_spec(entry: ExtensionEntry) -> AgentConfigSpec:
    spec = entry.manifest.agent_config
    if spec is None:
        raise _conflict("NO_AGENT_CONFIG", f"{entry.manifest.name} has no per-agent settings.")
    return spec


def _load(entry: ExtensionEntry) -> Extension:
    try:
        return load_extension(entry)
    except ExtensionLoadError as exc:
        raise _conflict("INVALID_EXTENSION", str(exc)) from exc


def _summary(spec: AgentConfigSpec, stored: dict[str, str] | None) -> str | None:
    if stored is None:
        return None
    field = next((item for item in spec.fields if item.summary), None)
    return None if field is None else stored.get(field.key)


def _config_payload(entry: ExtensionEntry, spec: AgentConfigSpec, agent_id: str) -> dict[str, Any]:
    """The config as the UI sees it: secrets only say whether they are set."""
    stored = db.get_extension_agent_config(entry.id, agent_id)
    updated = db.extension_agent_config_updated_at(entry.id, agent_id)
    fields = []
    for item in spec.fields:
        field: dict[str, Any] = {"key": item.key, "label": item.label, "kind": item.kind, "required": item.required}
        if item.kind == "secret":
            field["set"] = bool(stored and stored.get(item.key))
        elif item.kind == "number":
            field.update({"min": item.min, "max": item.max, "default": item.default})
            # A config saved before the field existed reads as its default.
            field["value"] = agent_config_value(spec, stored, item.key) if stored else item.default
        else:
            field["value"] = stored.get(item.key, "") if stored else ""
        fields.append(field)
    return {
        "label": spec.label,
        "help": spec.help,
        "configured": stored is not None,
        "updated_at": updated.isoformat() if updated is not None else None,
        "fields": fields,
    }


def _resolve_values(
    spec: AgentConfigSpec,
    submitted: dict[str, str],
    stored: dict[str, str] | None,
) -> dict[str, str]:
    """Validate a submitted config and fill blank secrets from the stored one.

    A blank secret while a config is stored means "keep the stored secret"
    (the dialog says so); with nothing stored it is a missing value.

    Raises:
        HTTPException: 422 ``CONFIG_INVALID`` naming the first problem.
    """
    declared = {item.key for item in spec.fields}
    unknown = sorted(set(submitted) - declared)
    if unknown:
        raise _invalid("CONFIG_INVALID", f"Unknown field: {', '.join(unknown)}")
    resolved: dict[str, str] = {}
    for item in spec.fields:
        value = submitted.get(item.key, "").strip()
        if item.kind == "secret" and not value and stored is not None and stored.get(item.key):
            value = stored[item.key]
        if item.required and not value:
            raise _invalid("CONFIG_INVALID", f"{item.label} is required.")
        if item.kind == "email" and value and not _EMAIL_RE.match(value):
            raise _invalid("CONFIG_INVALID", f"{item.label} is not an email address.")
        if item.kind == "number":
            value = _number_value(item, value)
        resolved[item.key] = value
    return resolved


def _number_value(item: AgentConfigField, value: str) -> str:
    """A number field's value to store: blank is its default, else a whole number in bounds.

    Raises:
        HTTPException: 422 ``CONFIG_INVALID`` naming the allowed range.
    """
    if not value:
        return item.default
    try:
        number = int(value)
    except ValueError:
        number = None
    if number is None or (item.min is not None and number < item.min) or (item.max is not None and number > item.max):
        raise _invalid("CONFIG_INVALID", f"{item.label} must be a whole number{_range_text(item)}.")
    return str(number)


def _range_text(item: AgentConfigField) -> str:
    if item.min is not None and item.max is not None:
        return f" from {item.min} to {item.max}"
    if item.min is not None:
        return f" of at least {item.min}"
    if item.max is not None:
        return f" of at most {item.max}"
    return ""


@router.get("/agents/{agent_id}/extensions")
async def agent_extensions(agent_id: str) -> list[dict[str, Any]]:
    """The desk's one read: every enabled, valid extension with per-agent settings.

    Returns:
        ``[{id, name, config_label, view_label, configured, summary, wakes,
        wake}]`` where ``summary`` is the ``summary: true`` field's stored
        value, else null; ``wakes`` says whether the extension wakes agents
        (so the desk can tell "no check yet" from "never checks"); and
        ``wake`` is the last wake check ``{checked_at, ok, error, last_new_at,
        last_new_count}`` (UTC ISO times), null for a non-wake extension or
        before the first check.

    Raises:
        HTTPException: 404 unknown agent.
    """
    _agent_or_404(agent_id)
    items = []
    for entry in get_discovery().valid_entries():
        spec = entry.manifest.agent_config
        if spec is None or not _usable(entry):
            continue
        stored = db.get_extension_agent_config(entry.id, agent_id)
        view = entry.manifest.agent_view
        items.append({
            "id": entry.id,
            "name": entry.manifest.name,
            "config_label": spec.label,
            "view_label": view.label if view is not None else None,
            "configured": stored is not None,
            "summary": _summary(spec, stored),
            "wakes": entry.manifest.wake is not None,
            "wake": _wake_status(entry, agent_id) if entry.manifest.wake is not None else None,
        })
    return items


def _wake_status(entry: ExtensionEntry, agent_id: str) -> dict[str, Any] | None:
    """The agent's last wake check for the desk, or ``None`` before the first."""
    row = db.get_wake_status(entry.id, agent_id)
    if row is None:
        return None
    return {
        "checked_at": _iso_utc(row["checked_at"]),
        "ok": bool(row["ok"]),
        "error": row["error"],
        "last_new_at": _iso_utc(row["last_new_at"]) if row["last_new_at"] is not None else None,
        "last_new_count": row["last_new_count"],
    }


def _iso_utc(moment: datetime) -> str:
    """ISO 8601 with an explicit UTC offset, so the desk can show local time."""
    return (moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)).isoformat()


@router.get("/extensions/{ext_id}/agents/{agent_id}/config")
async def get_agent_config(ext_id: str, agent_id: str) -> dict[str, Any]:
    """One agent's settings for one extension, secrets masked as ``{"set": bool}``.

    Raises:
        HTTPException: 404 unknown extension or agent; 409
            ``INVALID_EXTENSION`` / ``EXTENSION_DISABLED`` / ``NO_AGENT_CONFIG``.
    """
    entry = _enabled_entry(ext_id)
    spec = _config_spec(entry)
    _agent_or_404(agent_id)
    return _config_payload(entry, spec, agent_id)


@router.put("/extensions/{ext_id}/agents/{agent_id}/config")
async def put_agent_config(ext_id: str, agent_id: str, body: AgentConfigBody) -> dict[str, Any]:
    """Verify and store one agent's settings for one extension.

    Every declared field is taken from ``body.values`` (trimmed). A blank
    secret while a config is stored keeps the stored secret; this is resolved
    BEFORE verification, so the extension verifies the values that will be
    stored. Nothing is stored unless verification passes.

    Returns:
        The GET shape plus ``verified``: the extension's success detail.

    Raises:
        HTTPException: 404 unknown extension or agent; 409
            ``INVALID_EXTENSION`` / ``EXTENSION_DISABLED`` / ``NO_AGENT_CONFIG``;
            422 ``CONFIG_INVALID`` (unknown or missing keys, a bad email);
            422 ``CONFIG_VERIFY_FAILED`` with the extension's message.
    """
    entry = _enabled_entry(ext_id)
    spec = _config_spec(entry)
    _agent_or_404(agent_id)
    stored = db.get_extension_agent_config(entry.id, agent_id)
    values = _resolve_values(spec, body.values, stored)
    instance = _load(entry)
    if not isinstance(instance, SupportsAgentConfig):
        raise _conflict("INVALID_EXTENSION", "the extension has no verify_agent_config() method")
    try:
        verified = await asyncio.to_thread(instance.verify_agent_config, values)
    except AgentConfigError as exc:
        raise _invalid("CONFIG_VERIFY_FAILED", str(exc)) from exc
    db.set_extension_agent_config(entry.id, agent_id, values)
    return {**_config_payload(entry, spec, agent_id), "verified": verified}


@router.delete("/extensions/{ext_id}/agents/{agent_id}/config", status_code=204)
async def delete_agent_config(ext_id: str, agent_id: str) -> Response:
    """Remove one agent's settings for one extension.

    Raises:
        HTTPException: 404 unknown extension or agent, or no stored config;
            409 ``INVALID_EXTENSION`` / ``EXTENSION_DISABLED`` / ``NO_AGENT_CONFIG``.
    """
    entry = _enabled_entry(ext_id)
    _config_spec(entry)
    _agent_or_404(agent_id)
    if not db.delete_extension_agent_config(entry.id, agent_id):
        raise HTTPException(404, "No settings are stored for that agent")
    return Response(status_code=204)


def _view_instance(ext_id: str, agent_id: str, view_key: str) -> SupportsAgentView:
    """Load an enabled agent-view extension for an agent that may use it.

    Raises:
        HTTPException: 404 unknown extension, agent or view; 409
            ``INVALID_EXTENSION`` / ``EXTENSION_DISABLED`` / ``NO_AGENT_VIEW``;
            409 ``NOT_CONFIGURED`` when the view needs a config the agent lacks.
    """
    entry = _enabled_entry(ext_id)
    view = entry.manifest.agent_view
    if view is None:
        raise _conflict("NO_AGENT_VIEW", f"{entry.manifest.name} has no per-agent view.")
    if view_key not in {item.key for item in view.views}:
        raise HTTPException(404, f"{entry.manifest.name} has no view {view_key!r}")
    _agent_or_404(agent_id)
    if view.requires_config and db.get_extension_agent_config(entry.id, agent_id) is None:
        raise _conflict("NOT_CONFIGURED", f"{entry.manifest.name} is not set up for this agent.")
    instance = _load(entry)
    if not isinstance(instance, SupportsAgentView):
        raise _conflict("INVALID_EXTENSION", "the extension has no agent_view() method")
    return instance


def _view_failed(exc: AgentViewError) -> HTTPException:
    return HTTPException(502, {"error": exc.code, "message": exc.message})


@router.get("/extensions/{ext_id}/agents/{agent_id}/view")
async def agent_view(
    ext_id: str,
    agent_id: str,
    view: str = Query(..., min_length=1),
    skip: int = Query(0, ge=0),
    top: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """One page of one of an agent's record lists (e.g. its inbox), read-only.

    ``view`` is one of the manifest's ``agent_view.views`` keys (required).

    Raises:
        HTTPException: the ``_view_instance`` refusals; 502 ``{error,
            message}`` when the extension's read fails.
    """
    instance = _view_instance(ext_id, agent_id, view)
    try:
        page = await asyncio.to_thread(instance.agent_view, agent_id, view=view, skip=skip, top=top)
    except AgentViewError as exc:
        raise _view_failed(exc) from exc
    return page.model_dump()


@router.get("/extensions/{ext_id}/agents/{agent_id}/view/{item_id}")
async def agent_view_item(
    ext_id: str,
    agent_id: str,
    item_id: str,
    view: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """One record of one list in full (e.g. a message), read-only.

    Raises:
        HTTPException: the ``_view_instance`` refusals; 502 ``{error,
            message}`` when the extension's read fails.
    """
    instance = _view_instance(ext_id, agent_id, view)
    try:
        item = await asyncio.to_thread(instance.agent_view_item, agent_id, item_id, view=view)
    except AgentViewError as exc:
        raise _view_failed(exc) from exc
    return item.model_dump()
