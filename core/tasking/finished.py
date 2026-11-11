"""BossMod AI — How finished work is listed for agents: one row shape, one table.

A summary alone does not lead back to a result: the prompt cuts it short and
the work may have moved on. So every place that lists an agent's finished
tasks (the system prompt's recent completed tasks, the ``status``, ``tasks``
and ``recent-work`` CLI tables, the chat snapshot's rows) names each task by
id, so ``task <id>`` reads it whole, and lists its file deliverables, so
``cat`` reads the result itself. Document contents are never inlined: their
size is unbounded.

Rows come from ``db.get_recent_completed_tasks`` (each carries its stored
``work_contract`` JSON text). Pure: no database or transport code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.models.work_contract import WorkContract

FINISHED_TASKS_HEADING = (
    "RECENT COMPLETED TASKS (summaries are short: read a task's files with cat, or the whole task with task <id>):"
)
_TABLE_HEADER = "task id | finished | status | task name | files | summary"


def finished_task_files(row: Mapping[str, Any]) -> list[str]:
    """The paths of a finished task's ``file`` deliverables, in contract order.

    Args:
        row: A ``db.get_recent_completed_tasks`` row; ``row["work_contract"]``
            is the stored contract JSON text, or ``None`` when the task has
            none.

    Returns:
        The paths; ``[]`` when the task has no contract or no file
        deliverable.

    Raises:
        KeyError: The row carries no ``work_contract`` key (it was not read
            by ``db.get_recent_completed_tasks``).
        pydantic.ValidationError: The stored contract is malformed.
    """
    raw = row["work_contract"]
    if raw is None:
        return []
    contract = WorkContract.model_validate_json(raw)
    return [item.path for item in contract.deliverables if item.type == "file"]


def finished_task_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One finished task, in the shape every listing renders.

    Args:
        row: A ``db.get_recent_completed_tasks`` row.

    Returns:
        ``{"id", "finished_at", "status", "title", "files", "summary"}``:
        ``finished_at`` is the last activity (else the creation time),
        ``files`` is :func:`finished_task_files`, and ``summary`` the
        completion summary, else the status note, else ``""``.

    Raises:
        KeyError: A column the listing needs is missing from the row.
        pydantic.ValidationError: The stored contract is malformed.
    """
    return {
        "id": row["id"],
        "finished_at": row["last_activity"] or row["created_at"],
        "status": row["status"],
        "title": row["title"],
        "files": finished_task_files(row),
        "summary": row["completion_summary"] or row["status_note"] or "",
    }


def _cut(text: str, limit: int) -> str:
    """``text`` stripped and cut to ``limit`` characters with ``...``; ``-`` when empty."""
    value = text.strip()
    if not value:
        return "-"
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def format_finished_task_lines(rows: Sequence[Mapping[str, Any]], *, summary_chars: int) -> list[str]:
    """The finished-task table: a header, then one line per task, or ``none``.

    The one formatter the system prompt and the CLI share::

        task id | finished | status | task name | files | summary
        3f2c… | 2026-11-06T20:42:00+00:00 | complete | arXiv survey | /projects/jtech/reports/research-survey.md | …

    Args:
        rows: :func:`finished_task_row` rows, newest first.
        summary_chars: Where the summary is cut (``...`` marks a cut). The
            prompt cuts at 160, the CLI at 240.

    Returns:
        The lines; the header and ``none`` when there are no rows. Files
        are joined with ``, `` (``-`` when there are none); an empty summary
        is ``-``.

    Raises:
        AttributeError: A row's ``finished_at`` is not a datetime.
    """
    lines = [_TABLE_HEADER]
    if not rows:
        lines.append("none")
        return lines
    for row in rows:
        lines.append(" | ".join([
            str(row["id"]),
            row["finished_at"].isoformat(),
            str(row["status"]),
            str(row["title"]),
            ", ".join(row["files"]) or "-",
            _cut(str(row["summary"]), summary_chars),
        ]))
    return lines
