"""Shared CLI approval resume path for desktop API and Telegram."""

from __future__ import annotations

from typing import Any

import db
from core.models.cli_policy import CliApprovalRequest


async def resume_cli_approval(
    request_id: str,
    *,
    approved: bool,
    note: str | None = None,
    decision_by: str = "human",
    services: Any,
) -> CliApprovalRequest | None:
    """Persist an approve/reject decision and wake the waiting agent.

    Returns the updated approval row, or ``None`` if the request is missing
    or already resolved. Callers must pass a runtime services object that
    implements ``enqueue_trigger`` (the real ``runtime_services`` or a test
    double) so the dispatcher is woken the same way as other inbound work.
    """
    if approved:
        approval = db.approve_cli_approval_request(request_id, decision_by=decision_by)
    else:
        approval = db.reject_cli_approval_request(
            request_id,
            decision_by=decision_by,
            decision_note=note,
        )
    if approval is None:
        return None

    payload: dict[str, Any] = {
        "approval_request_id": approval.id,
        "command": approval.command,
        "status": "approved" if approved else "rejected",
    }
    if approved:
        payload["content"] = approval.content
        payload["cwd"] = approval.cwd
    else:
        payload["decision_note"] = note

    await services.enqueue_trigger(
        agent_id=approval.agent_id,
        trigger_type="cli_approval_resolved",
        source_channel="system",
        payload=payload,
    )
    return approval
