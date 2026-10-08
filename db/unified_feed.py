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
    "chat_history_cleared", "chat_rewound",
})
_ACTIVITY_LOG_TASK_EVENTS = frozenset({
    "task_created", "task_updated", "task_stalled", "task_cancelled", "world_feedback",
})
_ACTIVITY_LOG_ERROR_PATTERNS = ("error", "invalid")
_ACTIVITY_TASK_KINDS = frozenset({"work", "assignment"})
_ACTIVITY_AGENT_KINDS = frozenset({"meeting", "conversation", "social"})
_NOTIFICATION_TASK_KINDS = frozenset({"completion", "handoff"})
_NOTIFICATION_ERROR_KINDS = frozenset({"blocked", "abandoned"})
_NOTIFICATION_SYSTEM_KINDS = frozenset({"host_path_consent", "cli_approval"})


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
        if event in _ACTIVITY_TASK_KINDS:
            return "task"
        if event in _ACTIVITY_AGENT_KINDS:
            return "agent"
        return "system"  # movement, break

    if source == "notification":
        if event in _NOTIFICATION_TASK_KINDS:
            return "task"
        if event in _NOTIFICATION_ERROR_KINDS:
            return "error"
        if event in _NOTIFICATION_SYSTEM_KINDS:
            return "system"
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

def _sql_in(values: frozenset[str]) -> str:
    """A SQL ``IN`` list of these module constants (literals, never user input)."""
    return ", ".join(f"'{value}'" for value in sorted(values))


def _category_sql(column: str, source: str) -> str:
    """:func:`classify_category` as a SQL CASE over ``column``, for one source.

    Built from the same constants, so the SQL filter and the Python label
    cannot drift; ``instr`` is case-sensitive like Python's ``in``.
    """
    if source == "activity_log":
        errors = " OR ".join(f"instr({column}, '{pattern}') > 0" for pattern in _ACTIVITY_LOG_ERROR_PATTERNS)
        return (
            f"CASE WHEN {column} IN ({_sql_in(_ACTIVITY_LOG_AGENT_EVENTS)}) THEN 'agent' "
            f"WHEN {column} IN ({_sql_in(_ACTIVITY_LOG_TASK_EVENTS)}) THEN 'task' "
            f"WHEN {errors} THEN 'error' ELSE 'system' END"
        )
    if source == "activity":
        return (
            f"CASE WHEN {column} IN ({_sql_in(_ACTIVITY_TASK_KINDS)}) THEN 'task' "
            f"WHEN {column} IN ({_sql_in(_ACTIVITY_AGENT_KINDS)}) THEN 'agent' ELSE 'system' END"
        )
    if source == "notification":
        return (
            f"CASE WHEN {column} IN ({_sql_in(_NOTIFICATION_TASK_KINDS)}) THEN 'task' "
            f"WHEN {column} IN ({_sql_in(_NOTIFICATION_ERROR_KINDS)}) THEN 'error' "
            f"WHEN {column} IN ({_sql_in(_NOTIFICATION_SYSTEM_KINDS)}) THEN 'system' ELSE 'agent' END"
        )
    raise ValueError(f"Unknown feed source: {source}")


# One source's rows in the feed's common columns, filtered ($1 search on the
# title, $2 agent name, $6 category) and capped at its newest $5 in the page's
# sort order. The category is filtered here, before the cap, so a category
# page is a true page of that category: paging it neither repeats nor skips.
# A page is the first ``limit + 1`` merged rows after ``offset``, so no source
# can contribute more than ``offset + limit + 1`` of them: capping each source
# there leaves every page unchanged, while each source reads only its newest
# rows (by its own timestamp column) instead of the whole table. In the two
# sources whose is_active is constantly FALSE the sort key is just the
# timestamp column itself, which a (created_at) index can serve.
#
# Timestamps tie (activity_log and notifications store whole seconds), so the
# order is made total: after the timestamp, the source name, then the row's
# rowid (``seq``), newest insert first. Each branch sorts by the same key
# restricted to itself (its source is constant), so its capped rows are
# exactly its rows of the merged page, and paging never repeats or skips a
# row. rowid, not the uuid id: a (created_at) index already ends in rowid,
# so it serves the whole branch order, and same-second rows read newest first.
_TITLE_FILTER = "($1 IS NULL OR LOWER({title}) LIKE '%' || LOWER($1) || '%')"
_AGENT_FILTER = "($2 IS NULL OR {agent_name} = $2)"
_CATEGORY_FILTER = "($6 IS NULL OR {category} = $6)"

_ACTIVITY_LOG_BRANCH = f"""
    SELECT * FROM (
        SELECT id, 'activity_log' AS source, event, detail AS title,
               NULL AS detail_text, agent_name, NULL AS task_id,
               NULL AS metadata, created_at AS ts, FALSE AS is_active,
               rowid AS seq
        FROM activity_log
        WHERE {_TITLE_FILTER.format(title="detail")}
          AND {_AGENT_FILTER.format(agent_name="agent_name")}
          AND {_CATEGORY_FILTER.format(category=_category_sql("event", "activity_log"))}
        ORDER BY created_at DESC, rowid DESC
        LIMIT $5
    )"""

_ACTIVITY_BRANCH = f"""
    SELECT * FROM (
        SELECT a.id, 'activity' AS source, a.kind AS event,
               COALESCE(a.title, a.kind || ' (' || a.status || ')') AS title,
               a.detail AS detail_text, ag.name AS agent_name, a.task_id,
               a.metadata, COALESCE(a.updated_at, a.created_at) AS ts,
               (a.status IN ('active', 'paused')) AS is_active,
               a.rowid AS seq
        FROM activities a
        LEFT JOIN agents ag ON ag.id = a.agent_id
        WHERE {_TITLE_FILTER.format(title="COALESCE(a.title, a.kind || ' (' || a.status || ')')")}
          AND {_AGENT_FILTER.format(agent_name="ag.name")}
          AND {_CATEGORY_FILTER.format(category=_category_sql("a.kind", "activity"))}
        ORDER BY is_active DESC, ts DESC, a.rowid DESC
        LIMIT $5
    )"""

_NOTIFICATION_BRANCH = f"""
    SELECT * FROM (
        SELECT n.id, 'notification' AS source, n.kind AS event,
               n.content AS title, NULL AS detail_text,
               ag.name AS agent_name, n.task_id,
               NULL AS metadata, n.created_at AS ts, FALSE AS is_active,
               n.rowid AS seq
        FROM notifications n
        LEFT JOIN agents ag ON ag.id = n.agent_id
        WHERE {_TITLE_FILTER.format(title="n.content")}
          AND {_AGENT_FILTER.format(agent_name="ag.name")}
          AND {_CATEGORY_FILTER.format(category=_category_sql("n.kind", "notification"))}
        ORDER BY n.created_at DESC, n.rowid DESC
        LIMIT $5
    )"""

_UNION_SQL = f"""
SELECT * FROM (
    {_ACTIVITY_LOG_BRANCH}
    UNION ALL
    {_ACTIVITY_BRANCH}
    UNION ALL
    {_NOTIFICATION_BRANCH}
)
ORDER BY is_active DESC, ts DESC, source, seq DESC
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
    Every filter, the category included, is applied in SQL before the page
    is cut, so ``offset`` counts rows of the filtered feed.
    Uses the ``limit + 1`` trick to determine ``has_more`` without a count query.
    """
    fetch_limit = limit + 1
    # SQLite reads a negative OFFSET as 0, so the per-source cap does too.
    per_source_limit = fetch_limit + max(offset, 0)
    rows = query(_UNION_SQL, [search, agent_name, fetch_limit, offset, per_source_limit, category])

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

    has_more = len(entries) > limit
    return {"entries": entries[:limit], "has_more": has_more}
