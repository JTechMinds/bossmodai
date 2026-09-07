"""Aggregated operator decision queue.

Read-only. Every query here already exists and is already indexed; this module
exists so the six UI surfaces that show "needs you" read one shape instead of
four. It grants no capability the operator did not already have through the
per-kind endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

import db

router = APIRouter()

MAX_LIMIT = 200
BLOCKED_STATUSES = ("blocked", "stalled")


def _agent_name(agent_id: str | None, cache: dict[str, str]) -> str:
    """Resolve an agent id to a display name, memoised per request."""
    if not agent_id:
        return "Unknown"
    if agent_id not in cache:
        agent = db.get_agent(agent_id)
        cache[agent_id] = agent.name if agent else "Unknown"
    return cache[agent_id]


def _consent_needs(cache: dict[str, str]) -> list[dict[str, Any]]:
    items = []
    for request in db.list_consent_requests(status="pending", limit=MAX_LIMIT):
        name = _agent_name(request.agent_id, cache)
        items.append({
            "id": request.id,
            "kind": "consent",
            "agent_id": request.agent_id,
            "agent_name": name,
            "title": f"{name} wants to read a folder",
            "sub": request.path,
            "created_at": request.created_at.isoformat(),
            # channel_id is a declared field on HostPathConsentRequest; when the
            # request did not originate in a thread it is None and the agent's
            # own conversation is the right place to show it.
            "conversation_id": request.channel_id or request.agent_id,
            "actions": [
                {"label": "Allow once", "method": "POST", "tone": "primary",
                 "href": f"/api/host-path-consent/{request.id}/allow-once"},
                {"label": "Always allow (for all agents)", "method": "POST", "tone": "default",
                 "href": f"/api/host-path-consent/{request.id}/always-allow"},
                {"label": "Deny", "method": "POST", "tone": "quiet",
                 "href": f"/api/host-path-consent/{request.id}/deny"},
            ],
        })
    return items


def _approval_needs(cache: dict[str, str]) -> list[dict[str, Any]]:
    items = []
    for request in db.list_cli_approval_requests(status="pending", limit=MAX_LIMIT):
        name = _agent_name(request.agent_id, cache)
        items.append({
            "id": request.id,
            "kind": "approval",
            "agent_id": request.agent_id,
            "agent_name": name,
            "title": f"{name} wants to run a command",
            "sub": request.command,
            "created_at": request.created_at.isoformat(),
            "conversation_id": request.agent_id,
            "actions": [
                {"label": "Approve", "method": "POST", "tone": "primary",
                 "href": f"/api/cli-policy/approvals/{request.id}/approve"},
                {"label": "Reject", "method": "POST", "tone": "quiet",
                 "href": f"/api/cli-policy/approvals/{request.id}/reject"},
            ],
        })
    return items


def _blocked_needs(cache: dict[str, str]) -> list[dict[str, Any]]:
    items = []
    for status in BLOCKED_STATUSES:
        for task in db.list_tasks(status=status):
            name = _agent_name(task.assigned_to, cache)
            items.append({
                "id": task.id,
                "kind": "blocked",
                "agent_id": task.assigned_to,
                "agent_name": name,
                "title": f"{name} is {status}",
                "sub": task.title,
                # Task has no `updated_at`. `last_activity` is a non-optional
                # datetime and is the field that moves when a task stalls.
                # (Verified against core/models/task.py — do not substitute.)
                "created_at": task.last_activity.isoformat(),
                "conversation_id": task.assigned_to,
                "actions": [
                    {"label": "Open task", "method": "GET", "tone": "primary",
                     "href": f"/api/tasks/{task.id}"},
                ],
            })
    return items


@router.get("/needs")
async def list_needs(limit: int = 100) -> list[dict[str, Any]]:
    """Everything currently waiting on an operator decision, newest first.

    Aggregates pending host-path consents, pending CLI approvals, and
    blocked/stalled tasks. Actions are server-described so the client never
    hardcodes a resolution URL per kind.

    :param limit: maximum items returned, clamped to 200.
    """
    cache: dict[str, str] = {}
    needs = _consent_needs(cache) + _approval_needs(cache) + _blocked_needs(cache)
    needs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return needs[:min(max(limit, 0), MAX_LIMIT)]
