"""BossMod AI — Unified activity feed query.

Merges ``activity_log``, ``activities``, and ``notifications`` into a single
chronological feed for the Activity panel. All category classification and
entry normalization lives here (single source of truth).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from db.crud import query
from db.notification_links import list_notification_links


# ---------------------------------------------------------------------------
# Category classification — single source of truth
# ---------------------------------------------------------------------------

_ACTIVITY_LOG_AGENT_EVENTS = frozenset({
    "agent_created", "agent_updated", "agent_deleted", "agent_moved",
    "agent_prompt_history_policy_updated", "agent_runtime_reset",
    "chat_history_cleared",
})
_ACTIVITY_LOG_TASK_EVENTS = frozenset({
    "task_created", "task_updated", "task_stalled",
})
_ACTIVITY_LOG_ERROR_PATTERNS = ("error", "invalid")


def classify_category(source: str, event: str) -> str:
    """Derive a UI category from the entry's source table and event/kind.

    Returns one of ``"agent"``, ``"task"``, ``"error"``, ``"system"``.
    """
    if source == "activity_log":
        if event in _ACTIVITY_LOG_AGENT_EVENTS:
            return "agent"
        if event in _ACTIVITY_LOG_TASK_EVENTS:
            return "task"
        if any(p in event for p in _ACTIVITY_LOG_ERROR_PATTERNS):
            return "error"
        return "system"

    if source == "activity":
        if event in ("work", "assignment"):
            return "task"
        if event in ("meeting", "conversation", "social"):
            return "agent"
        return "system"  # movement, break

    if source == "notification":
        if event in ("completion", "handoff"):
            return "task"
        if event in ("blocked", "abandoned"):
            return "error"
        return "agent"  # receipt

    return "system"


# ---------------------------------------------------------------------------
# Per-source normalizers (reused by both bulk query and WS broadcasts)
# ---------------------------------------------------------------------------

def _iso(val: Any) -> str | None:
    """Convert a datetime-ish value to an ISO string, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def normalize_activity_log_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one ``activity_log`` row to the unified feed shape."""
    event = row["event"]
    return {
        "id": row["id"],
        "source": "activity_log",
        "category": classify_category("activity_log", event),
        "event": event,
        "title": row["detail"],
        "detail": None,
        "agent_name": row.get("agent_name"),
        "task_id": None,
        "metadata": {"event": event},
        "timestamp": _iso(row.get("created_at")),
        "is_active": False,
    }


def normalize_activity_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one ``activities`` row to the unified feed shape."""
    kind = row["kind"]
    status = row.get("status", "")
    title = row.get("title") or f"{kind} ({status})"
    raw_meta = row.get("metadata")
    parsed_meta: dict[str, Any] | None = None
    if raw_meta:
        try:
            parsed_meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except (json.JSONDecodeError, TypeError):
            parsed_meta = None

    return {
        "id": row["id"],
        "source": "activity",
        "category": classify_category("activity", kind),
        "event": kind,
        "title": title,
        "detail": row.get("detail"),
        "agent_name": row.get("agent_name"),
        "task_id": row.get("task_id"),
        "metadata": {
            "kind": kind,
            "status": status,
            "destination": row.get("destination"),
            "parent_activity_id": row.get("parent_activity_id"),
            "ended_at": _iso(row.get("ended_at")),
            **({"extra": parsed_meta} if parsed_meta else {}),
        },
        "timestamp": _iso(row.get("updated_at") or row.get("created_at")),
        "is_active": status in ("active", "paused"),
    }


def normalize_notification_entry(
    row: dict[str, Any],
    target_path: str | None = None,
) -> dict[str, Any]:
    """Normalize one ``notifications`` row to the unified feed shape."""
    kind = row["kind"]
    return {
        "id": row["id"],
        "source": "notification",
        "category": classify_category("notification", kind),
        "event": kind,
        "title": row["content"],
        "detail": None,
        "agent_name": row.get("agent_name"),
        "task_id": row.get("task_id"),
        "metadata": {
            "kind": kind,
            "source_channel": row.get("source_channel"),
            "policy": row.get("policy"),
            "chat_visible": row.get("chat_visible"),
            "prompt_visibility": row.get("prompt_visibility"),
            **({"target_path": target_path} if target_path else {}),
        },
        "timestamp": _iso(row.get("created_at")),
        "is_active": False,
    }


# ---------------------------------------------------------------------------
# Unified feed query
# ---------------------------------------------------------------------------

_UNION_SQL = """
WITH unified AS (
    SELECT id, 'activity_log' AS source, event, detail AS title,
           NULL AS detail_text, agent_name, NULL AS task_id,
           NULL AS metadata, created_at AS ts, FALSE AS is_active
    FROM activity_log

    UNION ALL

    SELECT a.id, 'activity' AS source, a.kind AS event,
           COALESCE(a.title, a.kind || ' (' || a.status || ')') AS title,
           a.detail AS detail_text, ag.name AS agent_name, a.task_id,
           a.metadata, COALESCE(a.updated_at, a.created_at) AS ts,
           (a.status IN ('active', 'paused')) AS is_active
    FROM activities a
    LEFT JOIN agents ag ON ag.id = a.agent_id

    UNION ALL

    SELECT n.id, 'notification' AS source, n.kind AS event,
           n.content AS title, NULL AS detail_text,
           ag.name AS agent_name, n.task_id,
           NULL AS metadata, n.created_at AS ts, FALSE AS is_active
    FROM notifications n
    LEFT JOIN agents ag ON ag.id = n.agent_id
)
SELECT * FROM unified
WHERE ($1 IS NULL OR LOWER(title) LIKE '%' || LOWER($1) || '%')
  AND ($2 IS NULL OR agent_name = $2)
ORDER BY is_active DESC, ts DESC
LIMIT $3 OFFSET $4
"""


def get_unified_feed(
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    category: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Return a unified feed page from all three activity sources.

    Returns ``{"entries": [...], "has_more": bool}``.
    Category filtering is applied in Python (the mapping is non-trivial).
    Uses the ``limit + 1`` trick to determine ``has_more`` without a count query.
    """
    fetch_limit = limit + 1
    rows = query(_UNION_SQL, [search, agent_name, fetch_limit, offset])

    # The UNION query returns pre-aliased columns (title, detail_text, ts,
    # is_active, source, event, agent_name, task_id, metadata).  Build
    # unified entries directly from these — the per-source normalize_*
    # functions are for raw table rows used in WS broadcasts.
    entries: list[dict[str, Any]] = []
    notification_ids: list[str] = []

    for row in rows:
        source = row["source"]
        event = row["event"]
        cat = classify_category(source, event)

        # Parse metadata JSON for activities
        raw_meta = row.get("metadata")
        parsed_meta: dict[str, Any] | None = None
        if raw_meta:
            try:
                parsed_meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
            except (json.JSONDecodeError, TypeError):
                parsed_meta = None

        entry: dict[str, Any] = {
            "id": row["id"],
            "source": source,
            "category": cat,
            "event": event,
            "title": row.get("title") or "",
            "detail": row.get("detail_text"),
            "agent_name": row.get("agent_name"),
            "task_id": row.get("task_id"),
            "metadata": {"event": event, **(parsed_meta or {})},
            "timestamp": _iso(row.get("ts")),
            "is_active": bool(row.get("is_active")),
        }
        entries.append(entry)

        if source == "notification":
            notification_ids.append(row["id"])

    # Batch-load notification links and merge target_path into metadata
    if notification_ids:
        links = list_notification_links(notification_ids)
        for entry in entries:
            if entry["source"] == "notification" and entry["id"] in links:
                link = links[entry["id"]]
                entry["metadata"]["target_path"] = link.target_path

    # Apply category filter in Python
    if category:
        entries = [e for e in entries if e["category"] == category]

    has_more = len(entries) > limit
    return {"entries": entries[:limit], "has_more": has_more}
