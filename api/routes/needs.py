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
    from core.bm_cli.host_roots import offers_always_allow_grant
    from core.models.host_path_consent import (
        SHELL_EXECUTOR_CARD_COPY,
        SHELL_EXECUTOR_DENY_LABEL,
        SHELL_EXECUTOR_ENABLE_LABEL,
        SHELL_EXECUTOR_KIND,
        WORKSPACE_PREFERENCE_KIND,
    )
    from core.models.nest_git import (
        NEST_GIT_CARD_COPY,
        NEST_GIT_KIND,
    )

    groups: dict[tuple[str, str, str], list[Any]] = {}
    order: list[tuple[str, str, str]] = []
    for request in db.list_consent_requests(status="pending", limit=MAX_LIMIT):
        kind = (request.card_kind or "host_path").strip() or "host_path"
        conversation = request.channel_id or request.agent_id
        if kind == SHELL_EXECUTOR_KIND or kind == NEST_GIT_KIND:
            key = (kind, conversation, "")
        else:
            key = (kind, conversation, request.grant_root or request.path)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(request)

    items = []
    for key in order:
        rows = groups[key]
        request = rows[0]
        grouped_ids = [row.id for row in rows]
        name = _agent_name(request.agent_id, cache)
        kind = (request.card_kind or "host_path").strip() or "host_path"
        if kind == SHELL_EXECUTOR_KIND:
            actions = [
                {"label": SHELL_EXECUTOR_ENABLE_LABEL, "method": "POST", "tone": "primary",
                 "href": f"/api/shell-executor/{request.id}/enable"},
                {"label": SHELL_EXECUTOR_DENY_LABEL, "method": "POST", "tone": "quiet",
                 "href": f"/api/shell-executor/{request.id}/deny"},
            ]
            title = f"{name} {SHELL_EXECUTOR_CARD_COPY}"
            sub = request.command or request.path
        elif kind == NEST_GIT_KIND:
            actions = _nest_git_need_actions(request)
            title = f"{name} {NEST_GIT_CARD_COPY}"
            sub = request.command or request.path
        elif kind == WORKSPACE_PREFERENCE_KIND:
            actions = [
                {"label": "Clone into workspace", "method": "POST", "tone": "primary",
                 "href": f"/api/workspace-preference/{request.id}/clone"},
                {"label": "Make a branch", "method": "POST", "tone": "default",
                 "href": f"/api/workspace-preference/{request.id}/branch"},
                {"label": "Edit host directly (not advised)", "method": "POST", "tone": "quiet",
                 "href": f"/api/workspace-preference/{request.id}/edit-host"},
                {"label": "Cancel", "method": "POST", "tone": "quiet",
                 "href": f"/api/workspace-preference/{request.id}/cancel"},
            ]
            title = f"{name} needs a workspace preference"
            sub = request.path
        else:
            actions = [
                {"label": "Allow once", "method": "POST", "tone": "primary",
                 "href": f"/api/host-path-consent/{request.id}/allow-once"},
            ]
            if offers_always_allow_grant(request.grant_root):
                actions.append(
                    {"label": "Always allow (for all agents)", "method": "POST", "tone": "default",
                     "href": f"/api/host-path-consent/{request.id}/always-allow"},
                )
            actions.append(
                {"label": "Deny", "method": "POST", "tone": "quiet",
                 "href": f"/api/host-path-consent/{request.id}/deny"},
            )
            title = f"{name} wants to read a folder"
            sub = request.path
        items.append({
            "id": request.id,
            "kind": "consent",
            "card_kind": kind,
            "agent_id": request.agent_id,
            "agent_name": name,
            "title": title,
            "sub": sub,
            "created_at": request.created_at.isoformat(),
            "conversation_id": request.channel_id or request.agent_id,
            "grouped_ids": grouped_ids,
            "actions": actions,
        })
    return items


def _nest_git_need_actions(request: Any) -> list[dict[str, Any]]:
    """Describe Enable / Add / Use with the JSON body multi-cred routes expect."""
    from core.bm_cli.nest_git_store import load_credentials
    from core.models.nest_git import (
        NEST_GIT_ADD_LABEL,
        NEST_GIT_ENABLE_LABEL,
        NEST_GIT_PICK_PREFIX,
    )

    actions = [
        {"label": NEST_GIT_ENABLE_LABEL, "method": "POST", "tone": "primary",
         "href": f"/api/nest-git/{request.id}/enable", "body": {}},
        {"label": NEST_GIT_ADD_LABEL, "method": "POST", "tone": "default",
         "href": f"/api/nest-git/{request.id}/credentials", "body": {}},
    ]
    for cred in load_credentials():
        if not cred or not cred.id:
            continue
        name = (cred.label or "").strip() or "saved credential"
        actions.append({
            "label": f"{NEST_GIT_PICK_PREFIX} {name}",
            "method": "POST",
            "tone": "default",
            "href": f"/api/nest-git/{request.id}/use",
            "body": {"credential_id": cred.id},
        })
    return actions


def _approval_needs(cache: dict[str, str]) -> list[dict[str, Any]]:
    from core.bm_cli.cli_always import offers_always_allow_cli

    groups: dict[tuple[str, str, str], list[Any]] = {}
    order: list[tuple[str, str, str]] = []
    for request in db.list_cli_approval_requests(status="pending", limit=MAX_LIMIT):
        key = (request.agent_id, request.command, request.cwd or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(request)
    items = []
    for key in order:
        rows = groups[key]
        request = rows[0]
        name = _agent_name(request.agent_id, cache)
        actions = [
            {"label": "Approve", "method": "POST", "tone": "primary",
             "href": f"/api/cli-policy/approvals/{request.id}/approve"},
        ]
        if offers_always_allow_cli(request.cwd):
            actions.append(
                {"label": "Always allow", "method": "POST", "tone": "default",
                 "href": f"/api/cli-policy/approvals/{request.id}/always-allow"},
            )
        actions.append(
            {"label": "Reject", "method": "POST", "tone": "quiet",
             "href": f"/api/cli-policy/approvals/{request.id}/reject"},
        )
        items.append({
            "id": request.id,
            "kind": "approval",
            "card_kind": "cli_approval",
            "agent_id": request.agent_id,
            "agent_name": name,
            "title": f"{name} wants to run a command",
            "sub": request.command,
            "cwd": request.cwd,
            "created_at": request.created_at.isoformat(),
            "conversation_id": request.channel_id or request.agent_id,
            "grouped_ids": [row.id for row in rows],
            "actions": actions,
        })
    return items


def _auto_approve_needs(cache: dict[str, str]) -> list[dict[str, Any]]:
    """Recent System AI approvals, so the audit line is visible in Needs."""
    from core.bm_cli.cli_auto_approve import AUDIT_PREFIX

    items = []
    for request in db.list_cli_approval_requests(
        status="approved",
        decision_by="system",
        limit=8,
    ):
        note = (request.decision_note or "").strip()
        if AUDIT_PREFIX not in note:
            continue
        name = _agent_name(request.agent_id, cache)
        when = request.decided_at or request.created_at
        items.append({
            "id": request.id,
            "kind": "audit",
            "card_kind": "cli_auto_approve",
            "agent_id": request.agent_id,
            "agent_name": name,
            "title": f"{name} auto-approved a command",
            "sub": f"{note} — {request.command}",
            "cwd": request.cwd,
            "created_at": when.isoformat(),
            "conversation_id": request.channel_id or request.agent_id,
            "grouped_ids": [request.id],
            "actions": [
                {"label": "Open log", "method": "GET", "tone": "quiet",
                 "href": "/api/diagnostics"},
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
    needs = (
        _consent_needs(cache)
        + _approval_needs(cache)
        + _auto_approve_needs(cache)
        + _blocked_needs(cache)
    )
    needs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return needs[:min(max(limit, 0), MAX_LIMIT)]
