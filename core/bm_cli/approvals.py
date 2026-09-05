"""Shared CLI approval resume path for desktop API and Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import db
from core.models.cli_policy import CliApprovalRequest

ApprovalPrefixStatus = Literal["unique", "none", "ambiguous"]
_MIN_APPROVAL_DISPLAY_PREFIX = 8


@dataclass(frozen=True, slots=True)
class ApprovalPrefixMatch:
    """Result of matching a Telegram ``/approve`` id prefix."""

    status: ApprovalPrefixStatus
    request: Any | None = None
    match_count: int = 0


def resolve_approval_by_unique_prefix(
    prefix: str,
    requests: Sequence[Any],
) -> ApprovalPrefixMatch:
    """Match pending approvals by id prefix; refuse empty, missing, or ambiguous.

    Callbacks already use the full UUID. Typed ``/approve yes <prefix>`` must
    resolve to exactly one pending row — first-match is not allowed.
    """
    cleaned = (prefix or "").strip().lower()
    if not cleaned:
        return ApprovalPrefixMatch(status="none", match_count=0)
    matches = [
        req
        for req in requests
        if str(getattr(req, "id", "")).lower().startswith(cleaned)
    ]
    if len(matches) == 1:
        return ApprovalPrefixMatch(status="unique", request=matches[0], match_count=1)
    if not matches:
        return ApprovalPrefixMatch(status="none", match_count=0)
    return ApprovalPrefixMatch(status="ambiguous", match_count=len(matches))


def display_approval_prefix(
    request_id: str,
    sibling_ids: Sequence[str],
    *,
    min_len: int = _MIN_APPROVAL_DISPLAY_PREFIX,
) -> str:
    """Return a hex prefix of at least *min_len* that uniquely identifies *request_id*."""
    hex_id = (request_id or "").lower()
    if not hex_id:
        return request_id
    others = [oid.lower() for oid in sibling_ids if oid and oid.lower() != hex_id]
    length = max(1, min_len)
    while length < len(hex_id):
        candidate = hex_id[:length]
        if not any(other.startswith(candidate) for other in others):
            return request_id[:length]
        length += 1
    return request_id


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
