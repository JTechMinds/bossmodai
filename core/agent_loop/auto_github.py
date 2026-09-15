"""Auto GitHub issue opener for blocked work with no next owner.

Last-resort escalation. The opener is :func:`open_auto_github_issue`.
:func:`maybe_open_auto_github_issue` reads the quieter-GH gate first and
skips when an origin line or ``@NextOwner`` already names the handoff.
Opening then is a #44-style duplicate.
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.blocked_origin import should_open_auto_github_issue

AUTO_GH_EVENT_PREFIX = "Auto GH #"

_opened: list[dict[str, Any]] = []


def list_opened_auto_github_issues() -> list[dict[str, Any]]:
    """Return Auto GH issues opened in this process."""
    return list(_opened)


def reset_opened_auto_github_issues() -> None:
    """Clear the in-process Auto GH ledger. Tests use this between cases."""
    _opened.clear()


def open_auto_github_issue(
    *,
    title: str,
    body: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Open one Auto GH issue. Does not consult the quieter-GH gate.

    Callers that must honor next-owner / origin-line skip go through
    :func:`maybe_open_auto_github_issue`.
    """
    number = len(_opened) + 1
    issue = {
        "number": number,
        "title": title,
        "body": body,
        "task_id": task_id,
    }
    _opened.append(issue)
    if task_id:
        from core.tasking.service import append_task_event

        append_task_event(
            task_id=task_id,
            author_type="system",
            author_name="BossMod",
            event_type="system",
            content=f"{AUTO_GH_EVENT_PREFIX}{number}: {title}",
        )
    return issue


def maybe_open_auto_github_issue(
    *,
    origin_line: str | None,
    next_owner: str | None,
    title: str,
    body: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Read the quieter-GH gate, then open or skip."""
    if not should_open_auto_github_issue(origin_line=origin_line, next_owner=next_owner):
        return {"opened": False, "reason": "next_owner_or_origin_line"}
    issue = open_auto_github_issue(title=title, body=body, task_id=task_id)
    return {"opened": True, "issue": issue}
