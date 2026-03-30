"""BossMod AI — Shared communication snapshots and deterministic enrichment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import db
from core.bm_cli.filesystem import slugify_name
from core.models import Agent, AgentState, Task

_OPEN_TASK_STATUSES = ("pending", "accepted", "active", "blocked", "stalled")


@dataclass(frozen=True, slots=True)
class CommunicationProfile:
    """Shared communication behavior for one trigger family."""

    name: str
    label: str


COMMUNICATION_PROFILES: dict[str, CommunicationProfile] = {
    "human_chat": CommunicationProfile(
        name="direct_message",
        label="direct human message",
    ),
    "peer_message": CommunicationProfile(
        name="peer_chat",
        label="direct coworker message",
    ),
    "session_message": CommunicationProfile(
        name="meeting_thread",
        label="meeting thread message",
    ),
    "session_response": CommunicationProfile(
        name="meeting_thread",
        label="meeting thread response turn",
    ),
    "channel_message": CommunicationProfile(
        name="channel_thread",
        label="channel thread message",
    ),
    "channel_response": CommunicationProfile(
        name="channel_thread",
        label="channel thread response turn",
    ),
    "watchdog_status_ping": CommunicationProfile(
        name="watchdog_status",
        label="system watchdog status ping",
    ),
}


def communication_profile_for_trigger(trigger_type: str | None) -> CommunicationProfile | None:
    """Return the communication profile for one conversational trigger."""
    return COMMUNICATION_PROFILES.get(str(trigger_type or ""))


def build_communication_snapshot(
    *,
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any],
) -> dict[str, Any]:
    """Build the bounded authoritative snapshot for one communication turn."""
    profile = communication_profile_for_trigger(trigger.get("type"))
    active = db.get_active_activity(agent.id)
    current_task = db.get_task(active.task_id) if active and active.task_id else None
    assigned_open_tasks = _list_tasks_by_statuses(assigned_to=agent.id, statuses=_OPEN_TASK_STATUSES)
    owned_open_tasks = _list_tasks_by_statuses(owner_id=agent.id, statuses=_OPEN_TASK_STATUSES)
    recent_completed_rows = db.get_recent_completed_tasks(agent.id, limit=3)
    recent_artifacts = db.get_recent_artifact_refs(agent.id, limit=2)
    recent_notifications = db.list_notifications(agent_id=agent.id, limit=5)
    recent_meeting_rows = db.get_recent_meeting_summaries_for_agent(agent.id, limit_sessions=2, messages_per_session=4)
    cli_state = db.get_agent_cli_state(agent.id)

    current_task_id = current_task.id if current_task is not None else None
    assigned_rows = [
        _task_row(task)
        for task in assigned_open_tasks
        if task.id != current_task_id
    ][:3]
    owned_rows = [
        _task_row(task)
        for task in owned_open_tasks
        if task.id != current_task_id and task.assigned_to != agent.id
    ][:3]
    project_rollups = _project_rollups_for_agent(agent.id, current_task_id=current_task_id)
    recent_completed = [
        {
            "task_id": item.get("id"),
            "title": item.get("title"),
            "project": item.get("project"),
            "status": item.get("status"),
            "summary": item.get("completion_summary") or item.get("status_note") or "-",
            "created_at": item.get("last_activity") or item.get("created_at"),
        }
        for item in recent_completed_rows
    ]
    recent_meetings = [
        {
            "session_id": item["session_id"],
            "title": item["title"],
            "status": item["status"],
            "room_id": item["room_id"],
            "messages": [
                {
                    "author_name": message["author_name"],
                    "content": _summarize_text(str(message["content"]), limit=180),
                    "created_at": message["created_at"],
                }
                for message in item["messages"]
            ],
        }
        for item in recent_meeting_rows
    ]

    snapshot = {
        "communication": {
            "profile": profile.name if profile else "unknown",
            "trigger_type": trigger.get("type"),
            "source_channel": trigger.get("source_channel"),
            "speaker": trigger.get("from_name") or trigger.get("author_name") or "Unknown",
            "author_type": trigger.get("author_type") or ("human" if trigger.get("type") == "human_chat" else "agent"),
        },
        "runtime": {
            "status": state.status,
            "location": _room_name(state),
            "cwd": cli_state.cwd if cli_state is not None else "/me",
            "current_activity": active.kind if active else "none",
            "current_task": current_task.title if current_task else "none",
            "open_assigned_task_count": len(assigned_rows) + (1 if current_task is not None else 0),
            "open_owned_task_count": len(owned_rows),
        },
        "current_activity": _activity_row(active),
        "current_task": _task_row(current_task),
        "assigned_open_tasks": assigned_rows,
        "owned_open_tasks": owned_rows,
        "project_rollups": project_rollups,
        "recent_task_updates": [
            {
                "kind": item.kind,
                "task_id": item.task_id,
                "content": item.content,
                "created_at": item.created_at,
            }
            for item in recent_notifications
            if item.task_id
        ],
        "recent_completed_tasks": recent_completed,
        "recent_work_artifacts": [
            {
                "task_id": item["task_id"],
                "task_title": item["task_title"],
                "path": item["path"],
                "title": item["title"],
                "type": item["type"],
                "created_at": item["created_at"],
            }
            for item in recent_artifacts
        ],
        "recent_meetings": recent_meetings,
        "referenced_records": _resolve_referenced_records(
            message_content=str(trigger.get("content") or ""),
            current_task=current_task,
            assigned_rows=assigned_rows,
            owned_rows=owned_rows,
            recent_completed=recent_completed,
            project_rollups=project_rollups,
            recent_meetings=recent_meetings,
        ),
    }
    return snapshot


def communication_snapshot_json(snapshot: dict[str, Any]) -> str:
    """Serialize one communication snapshot for prompt use."""
    return json.dumps(snapshot, default=_json_default, indent=2)


def _room_name(state: AgentState) -> str:
    from core.world.tilemap import get_room_at

    room = get_room_at(state.x, state.y)
    return room["name"] if room else "unknown"


def _list_tasks_by_statuses(
    *,
    assigned_to: str | None = None,
    owner_id: str | None = None,
    statuses: tuple[str, ...],
) -> list[Task]:
    tasks: list[Task] = []
    seen: set[str] = set()
    for status in statuses:
        for task in db.list_tasks(assigned_to=assigned_to, owner_id=owner_id, status=status):
            if task.id in seen:
                continue
            seen.add(task.id)
            tasks.append(task)
    tasks.sort(key=lambda item: (item.last_activity, item.created_at), reverse=True)
    return tasks


def _activity_row(activity: Any | None) -> dict[str, Any] | None:
    if activity is None:
        return None
    return {
        "kind": activity.kind,
        "status": activity.status,
        "title": activity.title or activity.kind,
        "detail": activity.detail or "",
        "destination": activity.destination,
        "metadata": activity.metadata or {},
    }


def _task_row(task: Task | None) -> dict[str, Any] | None:
    if task is None:
        return None
    assignee_name = None
    if task.assigned_to:
        assignee = db.get_agent(task.assigned_to)
        assignee_name = assignee.name if assignee is not None else None
    return {
        "id": task.id,
        "title": task.title,
        "project": task.project,
        "status": task.status,
        "assigned_to": task.assigned_to,
        "assignee_name": assignee_name,
        "description": task.description,
        "status_note": task.status_note,
        "completion_summary": task.completion_summary,
        "last_activity": task.last_activity,
        "created_at": task.created_at,
    }


def _project_rollups_for_agent(agent_id: str, *, current_task_id: str | None) -> list[dict[str, Any]]:
    relevant = _list_tasks_by_statuses(assigned_to=agent_id, statuses=_OPEN_TASK_STATUSES)
    for status in ("complete", "blocked", "delegated", "abandoned"):
        for task in db.list_tasks(owner_id=agent_id, status=status):
            if all(existing.id != task.id for existing in relevant):
                relevant.append(task)
    for task in _list_tasks_by_statuses(owner_id=agent_id, statuses=_OPEN_TASK_STATUSES):
        if all(existing.id != task.id for existing in relevant):
            relevant.append(task)

    grouped: dict[str, dict[str, Any]] = {}
    for task in relevant:
        project = str(task.project or "").strip()
        if not project:
            continue
        bucket = grouped.setdefault(
            project,
            {
                "project": project,
                "path": f"/projects/{slugify_name(project)}",
                "counts": {},
                "latest_tasks": [],
                "sort_ts": task.last_activity,
            },
        )
        bucket["counts"][task.status] = bucket["counts"].get(task.status, 0) + 1
        bucket["sort_ts"] = max(bucket["sort_ts"], task.last_activity)
        if len(bucket["latest_tasks"]) < 3 and task.id != current_task_id:
            bucket["latest_tasks"].append(
                {
                    "title": task.title,
                    "status": task.status,
                    "assigned_to": task.assigned_to,
                    "assignee_name": _task_row(task).get("assignee_name"),
                }
            )
    ordered = sorted(grouped.values(), key=lambda item: item["sort_ts"], reverse=True)
    return [
        {
            "project": item["project"],
            "path": item["path"],
            "counts": item["counts"],
            "latest_tasks": item["latest_tasks"],
        }
        for item in ordered[:3]
    ]


def _resolve_referenced_records(
    *,
    message_content: str,
    current_task: Task | None,
    assigned_rows: list[dict[str, Any]],
    owned_rows: list[dict[str, Any]],
    recent_completed: list[dict[str, Any]],
    project_rollups: list[dict[str, Any]],
    recent_meetings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    query = _normalize_search_text(message_content)
    if not query:
        return {}

    candidate_tasks: list[dict[str, Any]] = []
    if current_task is not None:
        current_row = _task_row(current_task)
        if current_row is not None:
            candidate_tasks.append(current_row)
    candidate_tasks.extend(assigned_rows)
    candidate_tasks.extend(owned_rows)
    candidate_tasks.extend(recent_completed)

    task_matches = _top_matches(
        query=query,
        rows=candidate_tasks,
        text_parts=lambda row: [
            row.get("title"),
            row.get("project"),
            row.get("description"),
            row.get("summary"),
        ],
        limit=2,
    )
    project_matches = _top_matches(
        query=query,
        rows=project_rollups,
        text_parts=lambda row: [row.get("project")],
        limit=2,
    )
    meeting_matches = _top_matches(
        query=query,
        rows=recent_meetings,
        text_parts=lambda row: [row.get("title"), row.get("room_id")],
        limit=1,
    )

    matches: dict[str, list[dict[str, Any]]] = {}
    if task_matches:
        matches["tasks"] = task_matches
    if project_matches:
        matches["projects"] = project_matches
    if meeting_matches:
        matches["meetings"] = meeting_matches
    return matches


def _top_matches(
    *,
    query: str,
    rows: list[dict[str, Any]],
    text_parts: Any,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        identity = json.dumps(row, default=str, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        score = _match_score(query, " ".join(str(part or "") for part in text_parts(row)))
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _match_score(query: str, target: str) -> int:
    normalized_target = _normalize_search_text(target)
    if not normalized_target:
        return 0
    if query in normalized_target:
        return 4
    query_terms = set(query.split())
    target_terms = set(normalized_target.split())
    if not query_terms or not target_terms:
        return 0
    overlap = len(query_terms & target_terms)
    if overlap >= min(2, len(query_terms)):
        return 3
    if overlap == 1:
        return 1
    return 0


def _normalize_search_text(value: str) -> str:
    lowered = str(value or "").lower()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    return " ".join(cleaned.split())


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _summarize_text(value: str, *, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
