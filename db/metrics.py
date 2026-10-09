"""BossMod AI — Dashboard metrics aggregation queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db.crud import query, query_one


def get_dashboard_metrics(start: datetime, end: datetime) -> dict[str, Any]:
    """Run all aggregation queries and return the dashboard payload for a period.

    Event-timestamped figures (tasks completed, tokens, errors, messages,
    meetings, tool calls) count only rows inside the half-open period
    ``[start, end)``: a row stamped exactly at ``start`` is in, one stamped
    exactly at ``end`` is out, so back-to-back periods never count a row twice.
    Current-state figures (agents, task status, active channels, uptime)
    describe now whatever the period, and the tasks and tokens blocks carry an
    all-time companion (``completed_all_time``, ``all_time``).

    Args:
        start: The period's first instant. Must be timezone-aware.
        end: The instant just after the period. Must be timezone-aware.

    Returns:
        The dashboard payload: ``tasks``, ``agents``, ``tokens``, ``errors``,
        ``communication``, ``tool_calls`` and ``uptime`` blocks.

    Raises:
        ValueError: When either bound is naive (its instant would depend on the
            host's timezone) or ``start`` is not before ``end``.
    """
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be a timezone-aware datetime")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be a timezone-aware datetime")
    if start >= end:
        raise ValueError("start must be before end")
    lo, hi = _sql_bound(start), _sql_bound(end)
    return {
        "tasks": _task_metrics(lo, hi),
        "agents": _agent_metrics(),
        "tokens": _token_metrics(lo, hi),
        "errors": _error_metrics(lo, hi),
        "communication": _communication_metrics(lo, hi),
        "tool_calls": _tool_call_metrics(lo, hi),
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

def _sql_bound(moment: datetime) -> str:
    """A period bound as stored timestamps compare against it, as text.

    Timestamps are stored as UTC text in two shapes — the adapter's
    ``YYYY-MM-DD HH:MM:SS.ffffff+00:00`` and SQL ``current_timestamp``'s
    ``YYYY-MM-DD HH:MM:SS`` — and both order correctly against this one, so
    the raw column is compared and its index stays usable.

    A whole-second bound drops the fraction: ``05:00:00`` sorts BEFORE
    ``05:00:00.000000``, so a ``current_timestamp`` row stamped on the bound's
    second would otherwise fall out of the period it starts and into the one
    it ends.
    """
    utc = moment.astimezone(timezone.utc)
    if utc.microsecond == 0:
        return utc.strftime("%Y-%m-%d %H:%M:%S")
    return utc.strftime("%Y-%m-%d %H:%M:%S.%f")


def _task_metrics(lo: str, hi: str) -> dict[str, Any]:
    """Aggregate task status counts; ``completed`` counts closures in the period."""
    status_rows = query("SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status")
    by_status: dict[str, int] = {row["status"]: row["cnt"] for row in status_rows}

    # closed_at, not last_activity: completion is stamped once, on entry to a
    # terminal status, while last_activity moves with every later touch.
    completed_row = query_one(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE status = 'complete' AND closed_at >= ? AND closed_at < ?",
        [lo, hi],
    )
    completed = int(completed_row["cnt"]) if completed_row else 0

    return {
        "completed": completed,
        "completed_all_time": by_status.get("complete", 0),
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


def _token_metrics(lo: str, hi: str) -> dict[str, Any]:
    """Aggregate token usage from diagnostics over the period, plus the all-time total."""
    all_time_row = query_one(
        "SELECT SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total FROM diagnostics",
    )
    all_time_tokens = int(all_time_row["total"] or 0) if all_time_row else 0

    total_row = query_one(
        """
        SELECT SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total
        FROM diagnostics
        WHERE created_at >= ? AND created_at < ?
        """,
        [lo, hi],
    )
    total_tokens = int(total_row["total"] or 0) if total_row else 0

    by_agent_rows = query(
        """
        SELECT agent_id, agent_name,
               SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total_tokens,
               COUNT(*) AS api_calls
        FROM diagnostics
        WHERE created_at >= ? AND created_at < ?
        GROUP BY agent_id, agent_name
        ORDER BY total_tokens DESC
        """,
        [lo, hi],
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
        "all_time": all_time_tokens,
        "by_agent": by_agent,
    }


def _error_metrics(lo: str, hi: str) -> dict[str, Any]:
    """Aggregate error stats from diagnostics over the period."""
    totals_row = query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
        FROM diagnostics
        WHERE created_at >= ? AND created_at < ?
        """,
        [lo, hi],
    )
    total = int(totals_row["total"] or 0) if totals_row else 0
    errors = int(totals_row["errors"] or 0) if totals_row else 0
    rate = (errors / total) if total > 0 else 0.0

    invalid_row = query_one(
        """
        SELECT COUNT(*) AS cnt FROM diagnostics
        WHERE status = 'error' AND action_name IS NOT NULL
          AND created_at >= ? AND created_at < ?
        """,
        [lo, hi],
    )
    invalid_decisions = int(invalid_row["cnt"]) if invalid_row else 0

    return {
        "total": errors,
        "rate": round(rate, 4),
        "invalid_decisions": invalid_decisions,
    }


def _communication_metrics(lo: str, hi: str) -> dict[str, Any]:
    """Aggregate communication stats over the period; active channels are current state."""
    msg_row = query_one(
        "SELECT COUNT(*) AS cnt FROM messages WHERE created_at >= ? AND created_at < ?",
        [lo, hi],
    )
    messages_sent = int(msg_row["cnt"]) if msg_row else 0

    conversations_row = query_one(
        """
        SELECT COUNT(DISTINCT from_agent || '-' || to_agent) AS cnt
        FROM messages
        WHERE from_agent IS NOT NULL AND to_agent IS NOT NULL
          AND created_at >= ? AND created_at < ?
        """,
        [lo, hi],
    )
    agent_conversations = int(conversations_row["cnt"]) if conversations_row else 0

    channels_row = query_one("SELECT COUNT(*) AS cnt FROM channels WHERE status = 'active'")
    active_channels = int(channels_row["cnt"]) if channels_row else 0

    meetings_row = query_one(
        "SELECT COUNT(*) AS cnt FROM meeting_sessions WHERE created_at >= ? AND created_at < ?",
        [lo, hi],
    )
    meetings_held = int(meetings_row["cnt"]) if meetings_row else 0

    return {
        "messages_sent": messages_sent,
        "agent_conversations": agent_conversations,
        "active_channels": active_channels,
        "meetings_held": meetings_held,
    }


def _tool_call_metrics(lo: str, hi: str) -> dict[str, Any]:
    """Count tool calls from bm_cli_events over the period."""
    row = query_one(
        "SELECT COUNT(*) AS cnt FROM bm_cli_events WHERE created_at >= ? AND created_at < ?",
        [lo, hi],
    )
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
