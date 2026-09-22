"""BossMod AI — CLI approval request CRUD operations.

Provides create, read, update, and lifecycle management for CLI approval
requests that gate agent command execution behind human review.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.cli_policy import CliApprovalRequest
from db.crud import execute, fetch_all, fetch_one, insert_returning, query_one

_ALL_COLUMNS = (
    "id, agent_id, trigger_id, command, content, cwd, matched_rule_id, "
    "channel_id, status, decision_by, decision_note, review_note, "
    "decided_at, expires_at, created_at"
)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_approval_request(
    *,
    agent_id: str,
    command: str,
    content: str | None = None,
    cwd: str | None = None,
    matched_rule_id: str | None = None,
    trigger_id: str | None = None,
    expires_at: datetime | None = None,
    channel_id: str | None = None,
    review_note: str | None = None,
) -> CliApprovalRequest:
    """Insert a new approval request, or reuse a pending row for this command."""
    origin = (channel_id or "").strip() or None
    note = (review_note or "").strip() or None
    existing = get_pending_for_command(agent_id, command, cwd=cwd)
    if existing is not None:
        if note and not (existing.review_note or "").strip():
            updated = _set_review_note(existing.id, note)
            if updated is not None:
                return updated
        return existing
    return insert_returning(
        f"""
        INSERT INTO cli_approval_requests (
            agent_id, command, content, cwd,
            matched_rule_id, trigger_id, expires_at, channel_id, review_note
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING {_ALL_COLUMNS}
        """,
        [agent_id, command, content, cwd, matched_rule_id, trigger_id, expires_at, origin, note],
        CliApprovalRequest,
    )


def _set_review_note(request_id: str, review_note: str) -> CliApprovalRequest | None:
    """Stamp a pending card with the reason it was not auto-approved."""
    return fetch_one(
        f"""
        UPDATE cli_approval_requests
        SET review_note = $1
        WHERE id = $2 AND status = 'pending'
        RETURNING {_ALL_COLUMNS}
        """,
        [review_note, request_id],
        CliApprovalRequest,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def bind_approval_channel(request_id: str, channel_id: str) -> CliApprovalRequest | None:
    """Stamp ``channel_id`` onto a pending approval that does not have one yet."""
    token = (channel_id or "").strip()
    if not token:
        return None
    return fetch_one(
        f"""
        UPDATE cli_approval_requests
        SET channel_id = $1
        WHERE id = $2 AND (channel_id IS NULL OR channel_id = '')
        RETURNING {_ALL_COLUMNS}
        """,
        [token, request_id],
        CliApprovalRequest,
    )


def get_pending_for_command(
    agent_id: str,
    command: str,
    cwd: str | None = None,
) -> CliApprovalRequest | None:
    """Return the newest pending approval for this agent, command, and cwd."""
    scope = (cwd or "").strip()
    return fetch_one(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM cli_approval_requests
        WHERE agent_id = $1 AND command = $2 AND status = 'pending'
          AND COALESCE(cwd, '') = $3
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        [agent_id, command, scope],
        CliApprovalRequest,
    )


def get_approval_request(request_id: str) -> CliApprovalRequest | None:
    """Fetch a single approval request by ID, or ``None`` if not found."""
    return fetch_one(
        f"SELECT {_ALL_COLUMNS} FROM cli_approval_requests WHERE id = $1",
        [request_id],
        CliApprovalRequest,
    )


def list_approval_requests(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    decision_by: str | None = None,
    limit: int = 50,
) -> list[CliApprovalRequest]:
    """Return approval requests, newest first, with optional filters."""
    conditions: list[str] = []
    params: list[object] = []

    if status is not None:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    if agent_id is not None:
        params.append(agent_id)
        conditions.append(f"agent_id = ${len(params)}")
    if decision_by is not None:
        params.append(decision_by)
        conditions.append(f"decision_by = ${len(params)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM cli_approval_requests
        {where}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        params,
        CliApprovalRequest,
    )


def list_thread_manual_approvals(
    channel_id: str,
    *,
    excluded_note: str,
    limit: int = 40,
) -> list[CliApprovalRequest]:
    """Return recent human Approves in one thread, newest first.

    System rows are not manual. ``excluded_note`` drops Always-allow, which
    is a policy rule rather than thread advice.
    """
    token = (channel_id or "").strip()
    if not token:
        return []
    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM cli_approval_requests
        WHERE channel_id = $1
          AND status = 'approved'
          AND decision_by IS NOT NULL
          AND decision_by != 'system'
          AND (decision_note IS NULL OR decision_note != $2)
        ORDER BY decided_at DESC, id DESC
        LIMIT $3
        """,
        [token, excluded_note, limit],
        CliApprovalRequest,
    )


def count_pending_requests(agent_id: str | None = None) -> int:
    """Return the number of pending approval requests, optionally for one agent."""
    if agent_id is not None:
        row = query_one(
            "SELECT COUNT(*) AS cnt FROM cli_approval_requests WHERE status = 'pending' AND agent_id = $1",
            [agent_id],
        )
    else:
        row = query_one(
            "SELECT COUNT(*) AS cnt FROM cli_approval_requests WHERE status = 'pending'",
        )
    return int(row["cnt"]) if row else 0


# ---------------------------------------------------------------------------
# Update — decisions
# ---------------------------------------------------------------------------

def approve_request(
    request_id: str,
    *,
    decision_by: str = "human",
    decision_note: str | None = None,
) -> CliApprovalRequest | None:
    """Mark a request as approved and return the updated row."""
    return fetch_one(
        f"""
        UPDATE cli_approval_requests
        SET status = 'approved', decision_by = $1, decision_note = $2, decided_at = $3
        WHERE id = $4 AND status = 'pending'
        RETURNING {_ALL_COLUMNS}
        """,
        [decision_by, decision_note, datetime.now(timezone.utc), request_id],
        CliApprovalRequest,
    )


def reject_request(
    request_id: str,
    *,
    decision_by: str = "human",
    decision_note: str | None = None,
) -> CliApprovalRequest | None:
    """Mark a request as rejected and return the updated row."""
    return fetch_one(
        f"""
        UPDATE cli_approval_requests
        SET status = 'rejected', decision_by = $1, decision_note = $2, decided_at = $3
        WHERE id = $4 AND status = 'pending'
        RETURNING {_ALL_COLUMNS}
        """,
        [decision_by, decision_note, datetime.now(timezone.utc), request_id],
        CliApprovalRequest,
    )


# ---------------------------------------------------------------------------
# Lifecycle — expiration
# ---------------------------------------------------------------------------

def expire_stale_requests() -> int:
    """Expire all pending requests whose expiry has passed. Return the count."""
    now = datetime.now(timezone.utc)
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM cli_approval_requests WHERE status = 'pending' AND expires_at < $1",
        [now],
    )
    count = int(row["cnt"]) if row else 0
    if count > 0:
        execute(
            "UPDATE cli_approval_requests SET status = 'expired' WHERE status = 'pending' AND expires_at < $1",
            [now],
        )
    return count
