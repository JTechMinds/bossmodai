"""Extensions: list, enable/disable, and one-click setup.

The enabled set is the ``extensions_enabled`` setting; this is the only route
that writes it, because enabling needs the extension to be valid and set up.
The runtime worker reads it live, so nothing here touches runtime internals.
"""

from __future__ import annotations

from typing import Any

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

import db
from core.extensions.contract import LiveViewItem, SupportsLiveView
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


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"error": code, "message": message})


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
