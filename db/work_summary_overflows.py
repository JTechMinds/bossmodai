"""BossMod AI — Over-limit work step summary CRUD (``work_summary_overflows``).

One row per agent whose latest work step summary came out over its limit
(``core/agent_loop/turn_helpers.py`` writes it, and deletes it when a later
summary fits). ``api/routes/needs.py`` reads the rows to build the Needs card
that says which work-context setting to raise. Keyed by agent rather than by
activity so the row outlives a stall, which deletes the work snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.work_snapshot import WorkSummaryOverflow
from db.crud import fetch_all, insert_returning, query

_ALL_COLUMNS = "agent_id, model, summary_chars, limit_chars, budget_chars, created_at"


def record_work_summary_overflow(
    agent_id: str,
    *,
    model: str,
    summary_chars: int,
    limit_chars: int,
    budget_chars: int,
) -> WorkSummaryOverflow:
    """Insert or replace an agent's over-limit summary measurement.

    Every column is replaced, ``created_at`` included, so the row (and the
    card built from it) always describes the latest failure.

    Args:
        agent_id: The agent whose summary did not fit.
        model: The model that wrote the summary.
        summary_chars: Length of the first summary the model wrote.
        limit_chars: The summary limit at the time.
        budget_chars: ``work_snapshot_max_chars`` at the time.

    Returns:
        The stored row.
    """
    return insert_returning(
        f"""
        INSERT INTO work_summary_overflows (
            agent_id, model, summary_chars, limit_chars, budget_chars, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT(agent_id) DO UPDATE SET
            model = excluded.model,
            summary_chars = excluded.summary_chars,
            limit_chars = excluded.limit_chars,
            budget_chars = excluded.budget_chars,
            created_at = excluded.created_at
        RETURNING {_ALL_COLUMNS}
        """,
        [
            agent_id,
            model,
            int(summary_chars),
            int(limit_chars),
            int(budget_chars),
            datetime.now(timezone.utc),
        ],
        WorkSummaryOverflow,
    )


def delete_work_summary_overflow(agent_id: str) -> bool:
    """Delete an agent's over-limit summary row, once a summary fits again.

    Args:
        agent_id: The agent whose row goes.

    Returns:
        Whether a row was deleted; ``False`` when the agent had none.
    """
    deleted = query("DELETE FROM work_summary_overflows WHERE agent_id = $1 RETURNING agent_id", [agent_id])
    return bool(deleted)


def list_work_summary_overflows() -> list[WorkSummaryOverflow]:
    """Return every over-limit summary row, oldest failure first."""
    return fetch_all(
        f"SELECT {_ALL_COLUMNS} FROM work_summary_overflows ORDER BY created_at ASC",
        [],
        WorkSummaryOverflow,
    )
