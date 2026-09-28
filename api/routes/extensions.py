"""Extensions: list, enable/disable, and one-click setup.

The enabled set is the ``extensions_enabled`` setting; this is the only route
that writes it, because enabling needs the extension to be valid and set up.
The runtime worker reads it live, so nothing here touches runtime internals.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

import db
from core.extensions.loader import ExtensionLoadError
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
            "setup": None,
            "setup_label": None,
            "excluded_agents": [],
        }
    requires_image = manifest.requires.image_model
    return {
        "id": entry.id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "command": {"name": manifest.command.name, "summary": manifest.command.summary},
        "enabled": entry.valid and entry.id in enabled,
        "valid": entry.valid,
        "invalid_reason": entry.invalid_reason,
        "requires_image_model": requires_image,
        "setup": entry_setup_status(entry).model_dump(),
        "setup_label": manifest.setup.label,
        "excluded_agents": _excluded_agents() if requires_image and entry.valid else [],
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
