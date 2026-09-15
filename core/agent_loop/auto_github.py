"""Auto GitHub issue opener for blocked work with no next owner.

Last-resort escalation. The opener is :func:`open_auto_github_issue`.
Blocked-origin writes ``result["auto_github_issue"]``. The persist path
:func:`persist_auto_github_from_result` is what **reads** that flag.
When it is not True — origin line or ``@NextOwner`` already names the
handoff — the opener does not run. Opening then is a #44-style duplicate.
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

    The persist path must read ``result["auto_github_issue"]`` first via
    :func:`persist_auto_github_from_result`. Direct calls skip that flag.
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
    """Skip the opener when an origin line or next owner is already named."""
    if not should_open_auto_github_issue(origin_line=origin_line, next_owner=next_owner):
        return {"opened": False, "reason": "next_owner_or_origin_line"}
    issue = open_auto_github_issue(title=title, body=body, task_id=task_id)
    return {"opened": True, "issue": issue}


def persist_auto_github_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Honor ``result["auto_github_issue"]``. Open only when that flag is True.

    A False flag — named ``@NextOwner`` or a posted origin line — stops
    the opener. No #44-style duplicate.
    """
    if result.get("auto_github_issue") is not True:
        return None
    spec = result.get("auto_github") if isinstance(result.get("auto_github"), dict) else {}
    title = str(spec.get("title") or result.get("detail") or "Blocked")
    body = str(spec.get("body") or title)
    decision = maybe_open_auto_github_issue(
        origin_line=spec.get("origin_line"),
        next_owner=spec.get("next_owner"),
        title=title,
        body=body,
        task_id=spec.get("task_id"),
    )
    result["auto_github"] = {**spec, **decision}
    result["auto_github_issue"] = bool(decision.get("opened"))
    return decision
