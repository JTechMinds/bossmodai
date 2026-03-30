"""BossMod AI — Dashboard metrics aggregation queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db.crud import query, query_one


def get_dashboard_metrics() -> dict[str, Any]:
    """Run all aggregation queries and return the full dashboard metrics payload."""
    return {
        "tasks": _task_metrics(),
        "agents": _agent_metrics(),
        "tokens": _token_metrics(),
        "errors": _error_metrics(),
        "communication": _communication_metrics(),
        "tool_calls": _tool_call_metrics(),
        "uptime": _uptime_metrics(),
    }


def get_agent_stats_batch() -> dict[str, dict[str, Any]]:
    """Return per-agent stats for the Org Chart tab.

    Returns ``{agent_id: {"tasks_completed": int, "tokens_used": int, "current_task": str|None}}``.
    """
    completed_rows = query(
        "SELECT assigned_to, COUNT(*) AS cnt FROM tasks WHERE status = 'complete' GROUP BY assigned_to",
    )
    completed_map: dict[str, int] = {
        row["assigned_to"]: row["cnt"] for row in completed_rows if row["assigned_to"]
    }

    token_rows = query(
        """
        SELECT agent_id,
               SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total_tokens
        FROM diagnostics
        GROUP BY agent_id
        """,
    )
    token_map: dict[str, int] = {
        row["agent_id"]: int(row["total_tokens"] or 0) for row in token_rows
    }

    active_task_rows = query(
        "SELECT assigned_to, title FROM tasks WHERE status = 'active'",
    )
    active_task_map: dict[str, str] = {}
    for row in active_task_rows:
        if row["assigned_to"] and row["assigned_to"] not in active_task_map:
            active_task_map[row["assigned_to"]] = row["title"]

    all_agent_ids: set[str] = set(completed_map) | set(token_map) | set(active_task_map)
    result: dict[str, dict[str, Any]] = {}
    for agent_id in all_agent_ids:
        result[agent_id] = {
            "tasks_completed": completed_map.get(agent_id, 0),
            "tokens_used": token_map.get(agent_id, 0),
            "current_task": active_task_map.get(agent_id),
        }
    return result


# ---------------------------------------------------------------------------
# Private aggregation helpers
# ---------------------------------------------------------------------------

def _task_metrics() -> dict[str, Any]:
    """Aggregate task status counts."""
    status_rows = query("SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status")
    by_status: dict[str, int] = {row["status"]: row["cnt"] for row in status_rows}

    completed_today_row = query_one(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE status = 'complete' AND DATE(last_activity) = DATE('now')",
    )
    completed_today = int(completed_today_row["cnt"]) if completed_today_row else 0

    return {
        "completed": by_status.get("complete", 0),
        "completed_today": completed_today,
        "active": by_status.get("active", 0),
        "pending": by_status.get("pending", 0) + by_status.get("accepted", 0),
        "stalled": by_status.get("stalled", 0),
        "blocked": by_status.get("blocked", 0),
        "by_status": by_status,
    }


def _agent_metrics() -> dict[str, Any]:
    """Aggregate agent status counts from agent_state."""
    rows = query("SELECT status, COUNT(*) AS cnt FROM agent_state GROUP BY status")
    by_status: dict[str, int] = {row["status"]: row["cnt"] for row in rows}
    total = sum(by_status.values())
    active = total - by_status.get("idle", 0)
    return {
        "total": total,
        "active": active,
        "idle": by_status.get("idle", 0),
    }


def _token_metrics() -> dict[str, Any]:
    """Aggregate token usage from diagnostics."""
    total_row = query_one(
        "SELECT SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total FROM diagnostics",
    )
    total_tokens = int(total_row["total"] or 0) if total_row else 0

    today_row = query_one(
        """
        SELECT SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total
        FROM diagnostics
        WHERE DATE(created_at) = DATE('now')
        """,
    )
    today_tokens = int(today_row["total"] or 0) if today_row else 0

    by_agent_rows = query(
        """
        SELECT agent_id, agent_name,
               SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total_tokens,
               COUNT(*) AS api_calls
        FROM diagnostics
        GROUP BY agent_id, agent_name
        ORDER BY total_tokens DESC
        """,
    )
    by_agent = [
        {
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "total_tokens": int(row["total_tokens"] or 0),
            "api_calls": row["api_calls"],
        }
        for row in by_agent_rows
    ]

    return {
        "total": total_tokens,
        "today": today_tokens,
        "by_agent": by_agent,
    }


def _error_metrics() -> dict[str, Any]:
    """Aggregate error stats from diagnostics."""
    totals_row = query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
        FROM diagnostics
        """,
    )
    total = int(totals_row["total"] or 0) if totals_row else 0
    errors = int(totals_row["errors"] or 0) if totals_row else 0
    rate = (errors / total) if total > 0 else 0.0

    today_row = query_one(
        "SELECT COUNT(*) AS cnt FROM diagnostics WHERE status = 'error' AND DATE(created_at) = DATE('now')",
    )
    today_errors = int(today_row["cnt"]) if today_row else 0

    invalid_row = query_one(
        """
        SELECT COUNT(*) AS cnt FROM diagnostics
        WHERE status = 'error' AND action_name IS NOT NULL
        """,
    )
    invalid_decisions = int(invalid_row["cnt"]) if invalid_row else 0

    return {
        "total": errors,
        "today": today_errors,
        "rate": round(rate, 4),
        "invalid_decisions": invalid_decisions,
    }


def _communication_metrics() -> dict[str, Any]:
    """Aggregate communication stats from messages, channels, and meetings."""
    msg_row = query_one("SELECT COUNT(*) AS cnt FROM messages")
    messages_sent = int(msg_row["cnt"]) if msg_row else 0

    conversations_row = query_one(
        """
        SELECT COUNT(DISTINCT from_agent || '-' || to_agent) AS cnt
        FROM messages
        WHERE from_agent IS NOT NULL AND to_agent IS NOT NULL
        """,
    )
    agent_conversations = int(conversations_row["cnt"]) if conversations_row else 0

    channels_row = query_one("SELECT COUNT(*) AS cnt FROM channels WHERE status = 'active'")
    active_channels = int(channels_row["cnt"]) if channels_row else 0

    meetings_row = query_one("SELECT COUNT(*) AS cnt FROM meeting_sessions")
    meetings_held = int(meetings_row["cnt"]) if meetings_row else 0

    return {
        "messages_sent": messages_sent,
        "agent_conversations": agent_conversations,
        "active_channels": active_channels,
        "meetings_held": meetings_held,
    }


def _tool_call_metrics() -> dict[str, Any]:
    """Aggregate tool call stats from bm_cli_events."""
    row = query_one("SELECT COUNT(*) AS cnt FROM bm_cli_events")
    return {"total": int(row["cnt"]) if row else 0}


def _uptime_metrics() -> dict[str, Any]:
    """Compute runtime uptime from runtime_worker_state."""
    row = query_one(
        """
        SELECT started_at
        FROM runtime_worker_state
        WHERE lifecycle_state = 'running'
        ORDER BY started_at DESC
        LIMIT 1
        """,
    )
    if not row or row["started_at"] is None:
        return {"started_at": None, "seconds": None}

    started_at = row["started_at"]
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    return {
        "started_at": started_at.isoformat(),
        "seconds": round(max(elapsed, 0.0), 1),
    }
