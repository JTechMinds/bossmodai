"""Shared CLI approval resume path for desktop API and Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import db
from core.bm_cli.host_path_consent import _clean_channel_id
from core.models.cli_policy import CliApprovalRequest

ApprovalPrefixStatus = Literal["unique", "none", "ambiguous"]
_MIN_APPROVAL_DISPLAY_PREFIX = 8
ALWAYS_ALLOWED_NOTE = "Always allowed"


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
    always_allow: bool = False,
) -> CliApprovalRequest | None:
    """Persist an approve/reject/Always decision and wake the waiting agent.

    Returns the updated approval row, or ``None`` if the request is missing
    or already resolved. Callers must pass a runtime services object that
    implements ``enqueue_trigger`` (the real ``runtime_services`` or a test
    double) so the dispatcher is woken the same way as other inbound work.

    Always allow writes a real Settings CLI Always rule scoped to
    ``/me/host-work`` before approving. Missing/already-resolved ids return
    ``None`` and do not enqueue a wake.
    """
    if always_allow:
        existing = db.get_cli_approval_request(request_id)
        if existing is None or existing.status != "pending":
            return None
        from core.bm_cli.cli_always import write_nest_always_rule
        from core.bm_cli.policy_engine import policy_engine

        write_nest_always_rule(
            existing.command,
            existing.cwd,
            matched_rule_id=existing.matched_rule_id,
        )
        policy_engine.reload()
        approved = True
        note = note or ALWAYS_ALLOWED_NOTE

    if approved:
        approval = db.approve_cli_approval_request(
            request_id,
            decision_by=decision_by,
            decision_note=note if always_allow else None,
        )
    else:
        approval = db.reject_cli_approval_request(
            request_id,
            decision_by=decision_by,
            decision_note=note,
        )
    if approval is None:
        return None

    _collapse_duplicate_pending(approval, approved=approved, decision_by=decision_by, note=note)

    payload: dict[str, Any] = {
        "approval_request_id": approval.id,
        "command": approval.command,
        "status": "always_allowed" if always_allow else ("approved" if approved else "rejected"),
    }
    if approved:
        payload["content"] = approval.content
        payload["cwd"] = approval.cwd
        if always_allow:
            payload["decision_note"] = ALWAYS_ALLOWED_NOTE
    else:
        payload["decision_note"] = note

    channel_id = _clean_channel_id(getattr(approval, "channel_id", None))
    if channel_id:
        payload["channel_id"] = channel_id

    await services.enqueue_trigger(
        agent_id=approval.agent_id,
        trigger_type="cli_approval_resolved",
        source_channel="channel" if channel_id else "system",
        payload=payload,
    )
    return approval


def _collapse_duplicate_pending(
    approval: CliApprovalRequest,
    *,
    approved: bool,
    decision_by: str,
    note: str | None,
) -> None:
    """Resolve leftover pending rows for the same agent, command, and cwd.

    Does not enqueue extra wakes — one decision already resumed the agent.
    """
    command = str(approval.command or "")
    cwd = str(approval.cwd or "")
    for sibling in db.list_cli_approval_requests(status="pending", agent_id=approval.agent_id, limit=80):
        if sibling.id == approval.id:
            continue
        if str(sibling.command or "") != command:
            continue
        if str(sibling.cwd or "") != cwd:
            continue
        if approved:
            db.approve_cli_approval_request(sibling.id, decision_by=decision_by)
        else:
            db.reject_cli_approval_request(
                sibling.id,
                decision_by=decision_by,
                decision_note=note,
            )
