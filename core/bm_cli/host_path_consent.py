"""In-chat host-path consent: request, reuse, and resume onto the allowlist."""

from __future__ import annotations

from typing import Any, Literal

import db
from core.bm_cli.host_roots import (
    SETTING_CATEGORY,
    SETTING_KEY,
    grantable_host_root,
    normalize_host_root_setting,
)
from core.bm_cli.results import consent_required_result, error_result
from core.bm_cli.types import BossModCliResult
from core.models import Agent
from core.models.host_path_consent import HostPathConsentRequest

ConsentDecision = Literal["allow_once", "always_allow", "deny"]


def canonical_host_path(raw_path: str) -> str:
    """Return a stable absolute path string for pending/denied matching."""
    from pathlib import Path

    token = (raw_path or "").strip()
    if not token:
        return token
    path = Path(token).expanduser()
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def handle_named_path_consent(
    *,
    agent: Agent,
    raw_path: str,
    command: str,
    content: str | None,
    cwd: str,
    task_id: str | None,
) -> BossModCliResult:
    """Pause for in-chat consent, reuse a pending card, or fail closed.

    Unsanctionable paths (denied system trees, filesystem root, missing
    grantable directory) stay hard-denied with no card.
    """
    path = canonical_host_path(raw_path)
    grant_root = grantable_host_root(raw_path)
    if grant_root is None:
        from core.bm_cli.host_roots import denial_message

        return error_result(command, denial_message(raw_path), cwd=cwd, executor="virtual")

    denied = db.find_denied_for_scope(agent.id, path, task_id=task_id)
    if denied is not None:
        return error_result(
            command,
            (
                f"Host-path access denied for {path!r}. "
                "The operator refused this path for the current task."
            ),
            cwd=cwd,
            executor="virtual",
        )

    pending = db.find_pending_for_path(agent.id, path)
    if pending is not None:
        return consent_required_result(
            command,
            _consent_message(pending),
            cwd=cwd,
            consent_request=pending,
            reused=True,
        )

    reason = f"Required for command: {command}"
    request = db.create_consent_request(
        agent_id=agent.id,
        path=path,
        grant_root=str(grant_root),
        reason=reason,
        command=command,
        content=content,
        cwd=cwd,
        task_id=task_id,
    )
    return consent_required_result(
        command,
        _consent_message(request),
        cwd=cwd,
        consent_request=request,
        reused=False,
    )


async def resume_host_path_consent(
    request_id: str,
    *,
    decision: ConsentDecision,
    services: Any,
    decision_by: str = "human",
    note: str | None = None,
) -> HostPathConsentRequest | None:
    """Apply Allow once / Always allow / Deny and wake the waiting agent."""
    existing = db.get_consent_request(request_id)
    if existing is None or existing.status != "pending":
        return None

    if decision == "deny":
        updated = db.resolve_consent_request(
            request_id,
            status="denied",
            decision_by=decision_by,
            decision_note=note,
        )
        if updated is None:
            return None
        await _enqueue_resume(updated, status="denied", services=services)
        return updated

    if decision == "allow_once":
        updated = db.resolve_consent_request(
            request_id,
            status="allowed_once",
            decision_by=decision_by,
            decision_note=note,
        )
        if updated is None:
            return None
        db.create_once_grant(
            agent_id=updated.agent_id,
            root=updated.grant_root,
            consent_id=updated.id,
            task_id=updated.task_id,
        )
        await _enqueue_resume(updated, status="allowed_once", services=services)
        return updated

    if decision != "always_allow":
        raise ValueError(f"Unsupported consent decision: {decision}")

    from core.bm_cli.host_roots import validate_host_root
    from core import config

    validate_host_root(existing.grant_root)
    current = _current_host_root_setting()
    merged = normalize_host_root_setting("\n".join([current, existing.grant_root]))
    db.set_setting(SETTING_KEY, merged, SETTING_CATEGORY)
    config.reload()
    updated = db.resolve_consent_request(
        request_id,
        status="always_allowed",
        decision_by=decision_by,
        decision_note=note,
    )
    if updated is None:
        return None
    await _enqueue_resume(updated, status="always_allowed", services=services)
    return updated


def _current_host_root_setting() -> str:
    """Read the live allowlist value from the settings table."""
    from db.crud import query_one

    row = query_one("SELECT value FROM settings WHERE key = $1", [SETTING_KEY])
    if row and row.get("value") is not None:
        return str(row["value"])
    return ""


def _consent_message(request: HostPathConsentRequest) -> str:
    return (
        f"Host-path access needs operator consent in chat for {request.path!r} "
        f"(grant root {request.grant_root!r}). {request.reason}"
    )


async def _enqueue_resume(
    request: HostPathConsentRequest,
    *,
    status: str,
    services: Any,
) -> None:
    payload: dict[str, Any] = {
        "consent_request_id": request.id,
        "command": request.command or "",
        "status": status,
        "path": request.path,
        "task_id": request.task_id,
    }
    if status != "denied":
        payload["content"] = request.content
        payload["cwd"] = request.cwd
    await services.enqueue_trigger(
        agent_id=request.agent_id,
        trigger_type="host_path_consent_resolved",
        source_channel="system",
        payload=payload,
        task_id=request.task_id,
    )
