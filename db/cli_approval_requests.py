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
    "status, decision_by, decision_note, decided_at, expires_at, created_at"
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
) -> CliApprovalRequest:
    """Insert a new approval request and return the created row."""
    return insert_returning(
        f"""
        INSERT INTO cli_approval_requests (
            agent_id, command, content, cwd,
            matched_rule_id, trigger_id, expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_ALL_COLUMNS}
        """,
        [agent_id, command, content, cwd, matched_rule_id, trigger_id, expires_at],
        CliApprovalRequest,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

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
) -> CliApprovalRequest | None:
    """Mark a request as approved and return the updated row."""
    return fetch_one(
        f"""
        UPDATE cli_approval_requests
        SET status = 'approved', decision_by = $1, decided_at = $2
        WHERE id = $3 AND status = 'pending'
        RETURNING {_ALL_COLUMNS}
        """,
        [decision_by, datetime.now(timezone.utc), request_id],
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
