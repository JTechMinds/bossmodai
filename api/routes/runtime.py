"""Map, world, metrics, activity, diagnostics, and runtime-control routes."""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.routes._shared import _RUNTIME_CONTRACT_KEYS, _validate_authored_prompt_template
from api.websocket import manager
from core import config
from core.llm import context_builder
from core.llm.template_engine import TemplateError
from core.prompting.runtime_prompt_lint import lint_runtime_prompts
from core.runtime import runtime_services
from core.world.tilemap import get_map_data
import db

router = APIRouter()


class RuntimeContractsBody(BaseModel):
    decision: str
    execution: str
    trigger_event: str
    conversation_envelope: str
    file_deliverable_guidance: str
    communication_snapshot: str


class RuntimeContractTemplateOverridesBody(BaseModel):
    decision: str | None = None
    execution: str | None = None
    trigger_event: str | None = None
    conversation_envelope: str | None = None
    file_deliverable_guidance: str | None = None
    communication_snapshot: str | None = None


class RuntimeContractPreviewBody(BaseModel):
    contract_kind: Literal["decision", "execution"]
    trigger_type: str = "human_chat"
    scope: Literal["contract", "bundle"] = "bundle"
    templates: RuntimeContractTemplateOverridesBody | None = None


class RuntimeControlBody(BaseModel):
    paused: bool


_RUNTIME_PREVIEW_TRIGGERS = [
    "human_chat",
    "peer_message",
    "task_follow_up",
    "task_assigned",
    "session_message",
    "session_response",
    "channel_message",
    "channel_response",
    "activity_resumed",
    "watchdog_status_ping",
    "social",
]


def _runtime_contracts_payload() -> dict[str, object]:
    """Return the current runtime contract settings and template metadata."""
    return {
        "decision": config.require(_RUNTIME_CONTRACT_KEYS["decision"]),
        "execution": config.require(_RUNTIME_CONTRACT_KEYS["execution"]),
        "trigger_event": config.require(_RUNTIME_CONTRACT_KEYS["trigger_event"]),
        "conversation_envelope": config.require(_RUNTIME_CONTRACT_KEYS["conversation_envelope"]),
        "file_deliverable_guidance": config.require(_RUNTIME_CONTRACT_KEYS["file_deliverable_guidance"]),
        "communication_snapshot": config.require(_RUNTIME_CONTRACT_KEYS["communication_snapshot"]),
        "allowed_variables": context_builder.template_variable_metadata(),
        "template_syntax": context_builder.template_syntax_examples(),
        "preview_triggers": list(_RUNTIME_PREVIEW_TRIGGERS),
        "prompt_health": lint_runtime_prompts().to_payload(),
    }


def _runtime_contract_template_overrides(
    body: RuntimeContractsBody | RuntimeContractTemplateOverridesBody | None,
) -> dict[str, str]:
    """Map runtime-contract request fields to prompt-setting keys."""
    if body is None:
        return {}

    overrides: dict[str, str] = {}
    for field_name, setting_key in _RUNTIME_CONTRACT_KEYS.items():
        value = getattr(body, field_name, None)
        if value is not None:
            overrides[setting_key] = value
    return overrides


# ─── Map ───

@router.get("/map")
async def get_map():
    """Return the office tilemap data for the Canvas renderer."""
    return get_map_data()


# ─── World state ───

@router.get("/world")
async def get_world_state():
    """Return all agents with their positions/status for Canvas rendering."""
    return db.get_world_state()


@router.get("/metrics/dashboard")
async def get_metrics_dashboard() -> dict[str, object]:
    """Return aggregated dashboard metrics for the Company overview."""
    return await asyncio.to_thread(db.get_dashboard_metrics)


# ─── Unified Activity Feed ───


@router.get("/activity/feed")
async def get_activity_feed(
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    category: str | None = None,
    agent_name: str | None = None,
):
    """Return a paginated, filterable unified feed from all activity sources."""
    limit = min(max(limit, 1), 200)
    valid_categories = {"agent", "task", "error", "system"}
    if category and category not in valid_categories:
        raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(sorted(valid_categories))}")
    return db.get_unified_feed(
        limit=limit,
        offset=offset,
        search=search or None,
        category=category or None,
        agent_name=agent_name or None,
    )


# ─── Diagnostics ───

@router.get("/diagnostics")
async def list_diagnostics(agent_id: str | None = None, limit: int = 50):
    """Return recent diagnostic summaries (no context/response blobs)."""
    max_limit = config.get_int("api_diagnostics_limit_max") or 200
    limit = min(limit, max_limit)
    return db.get_diagnostics(agent_id=agent_id, limit=limit)


@router.get("/diagnostics/{diagnostic_id}")
async def get_diagnostic_detail(diagnostic_id: str):
    """Return a single diagnostic entry with full detail."""
    entry = db.get_diagnostic(diagnostic_id)
    if not entry:
        raise HTTPException(404, "Diagnostic entry not found")
    return entry


@router.get("/runtime/core")
async def get_runtime_core(
    name: str = "",
    role: str = "",
    desk_x: int | None = None,
    desk_y: int | None = None,
):
    """Return the shared runtime core preview for hire Advanced."""
    from core.agent_loop.runtime_core import preview_runtime_core

    return {"runtime_core": preview_runtime_core(name=name, role=role, desk_x=desk_x, desk_y=desk_y)}


@router.get("/runtime/contracts")
async def get_runtime_contracts():
    return _runtime_contracts_payload()


@router.get("/runtime/state")
async def get_runtime_state():
    return runtime_services.status_payload()


@router.put("/runtime/state")
async def set_runtime_state(body: RuntimeControlBody):
    was_paused = runtime_services.is_paused()
    if body.paused:
        payload = await runtime_services.pause()
        if not was_paused:
            await manager.broadcast_activity(
                event="runtime_paused",
                detail="Emergency pause engaged. AI runtime services stopped.",
            )
    else:
        payload = await runtime_services.resume()
        if was_paused:
            await manager.broadcast_activity(
                event="runtime_resumed",
                detail="AI runtime services resumed.",
            )
    await manager.broadcast_runtime_state(payload)
    return payload


@router.put("/runtime/contracts")
async def set_runtime_contracts(body: RuntimeContractsBody):
    try:
        _validate_authored_prompt_template(body.decision)
        _validate_authored_prompt_template(body.execution)
        _validate_authored_prompt_template(body.trigger_event)
        _validate_authored_prompt_template(body.conversation_envelope)
        _validate_authored_prompt_template(body.file_deliverable_guidance)
        _validate_authored_prompt_template(body.communication_snapshot)
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc

    with db.transaction():
        db.set_setting(_RUNTIME_CONTRACT_KEYS["decision"], body.decision, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["execution"], body.execution, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["trigger_event"], body.trigger_event, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["conversation_envelope"], body.conversation_envelope, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["file_deliverable_guidance"], body.file_deliverable_guidance, "advanced")
        db.set_setting(_RUNTIME_CONTRACT_KEYS["communication_snapshot"], body.communication_snapshot, "advanced")
    config.reload()
    return _runtime_contracts_payload()


@router.post("/runtime/contracts/reset")
async def reset_runtime_contracts():
    """Reset runtime contracts and runtime-owned prompt blocks back to seeded defaults."""
    with db.transaction():
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["decision"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["execution"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["trigger_event"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["conversation_envelope"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["file_deliverable_guidance"])
        db.reset_setting_to_seed(_RUNTIME_CONTRACT_KEYS["communication_snapshot"])
    config.reload()
    return _runtime_contracts_payload()


@router.post("/runtime/contracts/preview")
async def preview_runtime_contract(body: RuntimeContractPreviewBody):
    if body.trigger_type not in _RUNTIME_PREVIEW_TRIGGERS:
        raise HTTPException(400, f"Unsupported preview trigger: {body.trigger_type}")
    template_overrides = _runtime_contract_template_overrides(body.templates)
    try:
        for template in template_overrides.values():
            _validate_authored_prompt_template(template)
        prompt_health = lint_runtime_prompts(template_overrides).to_payload()
        if body.scope == "contract":
            rendered = context_builder.preview_runtime_contract(
                contract_kind=body.contract_kind,
                trigger_type=body.trigger_type,
                template_overrides=template_overrides,
            )
            messages: list[dict[str, str]] = []
        else:
            preview = context_builder.preview_prompt_bundle(
                contract_kind=body.contract_kind,
                trigger_type=body.trigger_type,
                template_overrides=template_overrides,
            )
            rendered = str(preview["rendered"])
            messages = list(preview["messages"])
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "contract_kind": body.contract_kind,
        "trigger_type": body.trigger_type,
        "scope": body.scope,
        "rendered": rendered,
        "messages": messages,
        "prompt_health": prompt_health,
    }
