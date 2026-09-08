"""Host-path consent requests and allow-once grants."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.host_path_consent import HostPathConsentRequest
from db.crud import execute, fetch_all, fetch_one, insert_returning, query, query_one

_ALL_COLUMNS = (
    "id, agent_id, path, grant_root, reason, command, content, cwd, task_id, "
    "channel_id, card_kind, is_git, clone_dest, status, decision_by, "
    "decision_note, decided_at, expires_at, created_at"
)
_HOST_PATH_KIND = "host_path"
_WORKSPACE_KIND = "workspace_preference"


def create_consent_request(
    *,
    agent_id: str,
    path: str,
    grant_root: str,
    reason: str,
    command: str | None = None,
    content: str | None = None,
    cwd: str | None = None,
    task_id: str | None = None,
    channel_id: str | None = None,
    expires_at: datetime | None = None,
    card_kind: str = _HOST_PATH_KIND,
    is_git: bool = False,
    clone_dest: str | None = None,
) -> HostPathConsentRequest:
    """Insert a pending host-path or workspace-preference consent request."""
    kind = (card_kind or _HOST_PATH_KIND).strip() or _HOST_PATH_KIND
    return insert_returning(
        f"""
        INSERT INTO host_path_consent_requests (
            agent_id, path, grant_root, reason, command, content, cwd,
            task_id, channel_id, card_kind, is_git, clone_dest, expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        RETURNING {_ALL_COLUMNS}
        """,
        [
            agent_id,
            path,
            grant_root,
            reason,
            command,
            content,
            cwd,
            task_id,
            channel_id,
            kind,
            bool(is_git),
            clone_dest,
            expires_at,
        ],
        HostPathConsentRequest,
    )


def get_consent_request(request_id: str) -> HostPathConsentRequest | None:
    """Fetch one consent request by id."""
    return fetch_one(
        f"SELECT {_ALL_COLUMNS} FROM host_path_consent_requests WHERE id = $1",
        [request_id],
        HostPathConsentRequest,
    )


def list_consent_requests(
    *,
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[HostPathConsentRequest]:
    """Return consent requests, newest first."""
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
        FROM host_path_consent_requests
        {where}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        params,
        HostPathConsentRequest,
    )


def bind_consent_channel(request_id: str, channel_id: str) -> HostPathConsentRequest | None:
    """Attach the originating shared channel to one consent request."""
    token = (channel_id or "").strip()
    if not token:
        return get_consent_request(request_id)
    return fetch_one(
        f"""
        UPDATE host_path_consent_requests
        SET channel_id = $1
        WHERE id = $2 AND (channel_id IS NULL OR channel_id = '')
        RETURNING {_ALL_COLUMNS}
        """,
        [token, request_id],
        HostPathConsentRequest,
    ) or get_consent_request(request_id)


def list_pending_consent_for_channel(channel_id: str) -> list[HostPathConsentRequest]:
    """Return pending host-path consent cards bound to one origin thread."""
    token = (channel_id or "").strip()
    if not token:
        return []
    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE status = 'pending' AND channel_id = $1
        ORDER BY created_at ASC
        """,
        [token],
        HostPathConsentRequest,
    )


def list_pending_for_grant_root(grant_root: str) -> list[HostPathConsentRequest]:
    """Return pending consent cards that share one company-wide grant root."""
    token = (grant_root or "").strip()
    if not token:
        return []
    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE status = 'pending' AND grant_root = $1
          AND COALESCE(card_kind, 'host_path') = 'host_path'
        ORDER BY created_at ASC
        """,
        [token],
        HostPathConsentRequest,
    )


def _clean_scope_channel_id(channel_id: str | None) -> str | None:
    """Return a non-empty channel id, or None for company/Focus scope."""
    token = (channel_id or "").strip()
    return token or None


def list_pending_for_grant_root_scope(
    grant_root: str,
    channel_id: str | None,
) -> list[HostPathConsentRequest]:
    """Return pending requests for one grant root in a channel or company scope."""
    token = (grant_root or "").strip()
    if not token:
        return []
    scoped = _clean_scope_channel_id(channel_id)
    if scoped:
        return fetch_all(
            f"""
            SELECT {_ALL_COLUMNS}
            FROM host_path_consent_requests
            WHERE status = 'pending' AND grant_root = $1 AND channel_id = $2
              AND COALESCE(card_kind, 'host_path') = 'host_path'
            ORDER BY created_at ASC
            """,
            [token, scoped],
            HostPathConsentRequest,
        )
    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE status = 'pending' AND grant_root = $1
          AND (channel_id IS NULL OR channel_id = '')
          AND COALESCE(card_kind, 'host_path') = 'host_path'
        ORDER BY created_at ASC
        """,
        [token],
        HostPathConsentRequest,
    )


def find_pending_for_grant_root_scope(
    grant_root: str,
    channel_id: str | None,
) -> HostPathConsentRequest | None:
    """Return the oldest pending request for this grant root in one scope."""
    pending = list_pending_for_grant_root_scope(grant_root, channel_id)
    return pending[0] if pending else None


def find_pending_for_path(agent_id: str, path: str) -> HostPathConsentRequest | None:
    """Return the pending request for this agent + canonical path, if any."""
    return fetch_one(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE agent_id = $1 AND path = $2 AND status = 'pending'
          AND COALESCE(card_kind, 'host_path') = 'host_path'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [agent_id, path],
        HostPathConsentRequest,
    )


def find_denied_for_scope(
    agent_id: str,
    path: str,
    *,
    task_id: str | None,
) -> HostPathConsentRequest | None:
    """Return a sticky deny for this agent + path in the current task/turn scope."""
    if task_id:
        return fetch_one(
            f"""
            SELECT {_ALL_COLUMNS}
            FROM host_path_consent_requests
            WHERE agent_id = $1 AND path = $2 AND status = 'denied'
              AND task_id = $3
              AND COALESCE(card_kind, 'host_path') = 'host_path'
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            [agent_id, path, task_id],
            HostPathConsentRequest,
        )
    return fetch_one(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE agent_id = $1 AND path = $2 AND status = 'denied'
          AND task_id IS NULL
          AND COALESCE(card_kind, 'host_path') = 'host_path'
        ORDER BY decided_at DESC
        LIMIT 1
        """,
        [agent_id, path],
        HostPathConsentRequest,
    )


def resolve_consent_request(
    request_id: str,
    *,
    status: str,
    decision_by: str = "human",
    decision_note: str | None = None,
    clone_dest: str | None = None,
) -> HostPathConsentRequest | None:
    """Mark a pending request as resolved and return the updated row."""
    if status not in {
        "allowed_once",
        "always_allowed",
        "denied",
        "cloned",
        "branched",
        "edit_host",
    }:
        raise ValueError(f"Unsupported consent status: {status}")
    return fetch_one(
        f"""
        UPDATE host_path_consent_requests
        SET status = $1, decision_by = $2, decision_note = $3, decided_at = $4,
            clone_dest = COALESCE($5, clone_dest)
        WHERE id = $6 AND status = 'pending'
        RETURNING {_ALL_COLUMNS}
        """,
        [status, decision_by, decision_note, datetime.now(timezone.utc), clone_dest, request_id],
        HostPathConsentRequest,
    )


def create_once_grant(
    *,
    agent_id: str,
    root: str,
    consent_id: str,
    task_id: str | None = None,
) -> None:
    """Persist an allow-once grant for this agent + task (or turn)."""
    execute(
        """
        INSERT INTO host_path_once_grants (agent_id, root, consent_id, task_id)
        VALUES ($1, $2, $3, $4)
        """,
        [agent_id, root, consent_id, task_id],
    )


def list_once_grant_roots(agent_id: str, task_id: str | None = None) -> list[str]:
    """Return active allow-once roots for this agent in the current scope."""
    if task_id:
        rows = query(
            """
            SELECT root FROM host_path_once_grants
            WHERE agent_id = $1 AND (task_id = $2 OR task_id IS NULL)
            """,
            [agent_id, task_id],
        )
    else:
        rows = query(
            """
            SELECT root FROM host_path_once_grants
            WHERE agent_id = $1 AND task_id IS NULL
            """,
            [agent_id],
        )
    return [str(row["root"]) for row in rows]


def consume_turn_once_grants(agent_id: str) -> int:
    """Drop turn-scoped (no task) allow-once grants after the resume command."""
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM host_path_once_grants WHERE agent_id = $1 AND task_id IS NULL",
        [agent_id],
    )
    count = int(row["cnt"]) if row else 0
    if count:
        execute(
            "DELETE FROM host_path_once_grants WHERE agent_id = $1 AND task_id IS NULL",
            [agent_id],
        )
    return count


def clear_once_grants_for_task(task_id: str) -> int:
    """Drop allow-once grants bound to a finished task."""
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM host_path_once_grants WHERE task_id = $1",
        [task_id],
    )
    count = int(row["cnt"]) if row else 0
    if count:
        execute("DELETE FROM host_path_once_grants WHERE task_id = $1", [task_id])
    return count


def find_pending_workspace_preference(
    agent_id: str,
    path: str,
) -> HostPathConsentRequest | None:
    """Return the pending workspace-preference card for this agent + path."""
    return fetch_one(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE agent_id = $1 AND path = $2 AND status = 'pending'
          AND card_kind = '{_WORKSPACE_KIND}'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [agent_id, path],
        HostPathConsentRequest,
    )


def find_workspace_preference_for_scope(
    agent_id: str,
    path: str,
    *,
    task_id: str | None,
) -> HostPathConsentRequest | None:
    """Return the latest resolved workspace preference for this write scope."""
    if task_id:
        return fetch_one(
            f"""
            SELECT {_ALL_COLUMNS}
            FROM host_path_consent_requests
            WHERE agent_id = $1 AND path = $2
              AND card_kind = '{_WORKSPACE_KIND}'
              AND status IN ('edit_host', 'cloned', 'branched', 'denied')
              AND task_id = $3
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            [agent_id, path, task_id],
            HostPathConsentRequest,
        )
    return fetch_one(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM host_path_consent_requests
        WHERE agent_id = $1 AND path = $2
          AND card_kind = '{_WORKSPACE_KIND}'
          AND status IN ('edit_host', 'cloned', 'branched', 'denied')
          AND task_id IS NULL
        ORDER BY decided_at DESC
        LIMIT 1
        """,
        [agent_id, path],
        HostPathConsentRequest,
    )


def delete_agent_consent(agent_id: str) -> None:
    """Remove consent rows for a deleted agent."""
    execute("DELETE FROM host_path_once_grants WHERE agent_id = $1", [agent_id])
    execute("DELETE FROM host_path_consent_requests WHERE agent_id = $1", [agent_id])
