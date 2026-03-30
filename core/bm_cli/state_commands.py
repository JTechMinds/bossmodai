"""BossMod AI — Runtime/state-oriented virtual BossMod CLI commands."""

from __future__ import annotations

from typing import Any

import db
from core.bm_cli.results import success_result, trim
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.default_prompts import load_default_prompt
from core.tasking.board import build_task_board, serialize_task_board
from core.world.tilemap import get_room_at


_AUTHORITATIVE_NOTES = {
    "status": load_default_prompt("internal_cli_authoritative_status"),
    "runtime": load_default_prompt("internal_cli_authoritative_runtime"),
    "activity": load_default_prompt("internal_cli_authoritative_activity"),
    "current_task": load_default_prompt("internal_cli_authoritative_current_task"),
    "tasks": load_default_prompt("internal_cli_authoritative_tasks"),
    "recent_work": load_default_prompt("internal_cli_authoritative_recent_work"),
    "location": load_default_prompt("internal_cli_authoritative_location"),
    "my_board": load_default_prompt("internal_cli_authoritative_tasks"),
    "owned_tasks": load_default_prompt("internal_cli_authoritative_tasks"),
    "delegated_tasks": load_default_prompt("internal_cli_authoritative_tasks"),
    "waiting_on_me": load_default_prompt("internal_cli_authoritative_tasks"),
    "task_detail": load_default_prompt("internal_cli_authoritative_current_task"),
}


def handle_status(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return a concise authoritative status snapshot."""
    snapshot = _build_status_snapshot(context)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked live status via BossMod CLI",
        kind="status",
        data=snapshot,
        sections=[
            ("RUNTIME STATUS", _runtime_lines(snapshot["runtime"])),
            ("OPEN TASKS", _task_table_lines(snapshot["open_tasks"], description_label="description")),
            ("RECENT COMPLETED TASKS", _completed_task_table_lines(snapshot["recent_completed_tasks"])),
            ("RECENT WORK ARTIFACTS", _artifact_table_lines(snapshot["recent_work_artifacts"])),
        ],
        authoritative_note=_AUTHORITATIVE_NOTES["status"],
        cwd=context.cwd,
    )


def handle_runtime(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return only the live runtime state block."""
    snapshot = _build_status_snapshot(context)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked live runtime via BossMod CLI",
        kind="runtime",
        data={"runtime": snapshot["runtime"]},
        sections=[("RUNTIME STATUS", _runtime_lines(snapshot["runtime"]))],
        authoritative_note=_AUTHORITATIVE_NOTES["runtime"],
        cwd=context.cwd,
    )


def handle_activity(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return the active runtime activity details."""
    snapshot = _build_status_snapshot(context)
    current_activity = snapshot["current_activity"]
    lines = ["none"] if current_activity is None else _activity_lines(current_activity)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked current activity via BossMod CLI",
        kind="activity",
        data={"current_activity": current_activity},
        sections=[("CURRENT ACTIVITY", lines)],
        authoritative_note=_AUTHORITATIVE_NOTES["activity"],
        cwd=context.cwd,
    )


def handle_current_task(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return the current bound task details, if any."""
    snapshot = _build_status_snapshot(context)
    current_task = snapshot["current_task"]
    lines = ["none"] if current_task is None else _current_task_lines(current_task)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked current task via BossMod CLI",
        kind="current_task",
        data={"current_task": current_task},
        sections=[("CURRENT TASK", lines)],
        authoritative_note=_AUTHORITATIVE_NOTES["current_task"],
        cwd=context.cwd,
    )


def handle_tasks(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return open tasks plus recent completed tasks."""
    snapshot = _build_status_snapshot(context)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked task lists via BossMod CLI",
        kind="tasks",
        data={
            "open_tasks": snapshot["open_tasks"],
            "recent_completed_tasks": snapshot["recent_completed_tasks"],
        },
        sections=[
            ("OPEN TASKS", _task_table_lines(snapshot["open_tasks"], description_label="description")),
            ("RECENT COMPLETED TASKS", _completed_task_table_lines(snapshot["recent_completed_tasks"])),
        ],
        authoritative_note=_AUTHORITATIVE_NOTES["tasks"],
        cwd=context.cwd,
    )


def handle_recent_work(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return recent completed work and recent artifacts."""
    snapshot = _build_status_snapshot(context)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked recent work via BossMod CLI",
        kind="recent_work",
        data={
            "recent_completed_tasks": snapshot["recent_completed_tasks"],
            "recent_work_artifacts": snapshot["recent_work_artifacts"],
        },
        sections=[
            ("RECENT COMPLETED TASKS", _completed_task_table_lines(snapshot["recent_completed_tasks"])),
            ("RECENT WORK ARTIFACTS", _artifact_table_lines(snapshot["recent_work_artifacts"])),
        ],
        authoritative_note=_AUTHORITATIVE_NOTES["recent_work"],
        cwd=context.cwd,
    )


def handle_location(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return the current physical location snapshot."""
    room = get_room_at(context.state.x, context.state.y)
    room_name = room["name"] if room else "unknown"
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked current location via BossMod CLI",
        kind="location",
        data={
            "room": room_name,
            "x": context.state.x,
            "y": context.state.y,
            "runtime_status": context.state.status,
        },
        sections=[
            ("LOCATION", [
                f"room: {room_name}",
                f"x: {context.state.x}",
                f"y: {context.state.y}",
                f"runtime_status: {context.state.status}",
            ])
        ],
        authoritative_note=_AUTHORITATIVE_NOTES["location"],
        cwd=context.cwd,
    )


def handle_my_board(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return the agent's self board."""
    board = serialize_task_board(build_task_board(context.agent.id, scope="self"))
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked the personal task board via BossMod CLI",
        kind="my_board",
        data=board,
        sections=_board_sections(board),
        authoritative_note=_AUTHORITATIVE_NOTES["my_board"],
        cwd=context.cwd,
    )


def handle_owned_tasks(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return the manager-owned board."""
    board = serialize_task_board(build_task_board(context.agent.id, scope="owned"))
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked owned tasks via BossMod CLI",
        kind="owned_tasks",
        data=board,
        sections=_board_sections(board),
        authoritative_note=_AUTHORITATIVE_NOTES["owned_tasks"],
        cwd=context.cwd,
    )


def handle_delegated_tasks(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return delegated child tasks."""
    board = serialize_task_board(build_task_board(context.agent.id, scope="delegated"))
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked delegated tasks via BossMod CLI",
        kind="delegated_tasks",
        data=board,
        sections=_board_sections(board),
        authoritative_note=_AUTHORITATIVE_NOTES["delegated_tasks"],
        cwd=context.cwd,
    )


def handle_waiting_on_me(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return tasks currently waiting on the selected agent."""
    board = serialize_task_board(build_task_board(context.agent.id, scope="owned"))
    waiting = board["sections"].get("tasks_waiting_on_me", [])
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked tasks waiting on them via BossMod CLI",
        kind="waiting_on_me",
        data={"tasks_waiting_on_me": waiting},
        sections=[("TASKS WAITING ON ME", _task_table_lines(waiting, description_label="description"))],
        authoritative_note=_AUTHORITATIVE_NOTES["waiting_on_me"],
        cwd=context.cwd,
    )


def handle_task_detail(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return one task plus its recent task-thread events."""
    task_id = parsed.args[0].strip() if parsed.args else ""
    if not task_id:
        return success_result(
            command=parsed.raw,
            detail="Task id required",
            kind="task_detail",
            data={"task": None, "events": []},
            sections=[("TASK", ["Provide a task id. Example: task <id>"])],
            authoritative_note=_AUTHORITATIVE_NOTES["task_detail"],
            cwd=context.cwd,
        )
    task = db.get_task(task_id)
    events = db.list_task_events(task_id, limit=10) if task is not None else []
    lines = ["not found"] if task is None else _current_task_lines(
        {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "description": task.description or "-",
            "completion_summary": task.completion_summary,
            "status_note": task.status_note,
        }
    )
    sections = [("TASK", lines)]
    sections.append(("TASK THREAD", _task_event_lines(events)))
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked task detail via BossMod CLI",
        kind="task_detail",
        data={
            "task": task.model_dump(mode="json") if task is not None else None,
            "events": [event.model_dump(mode="json") for event in events],
        },
        sections=sections,
        authoritative_note=_AUTHORITATIVE_NOTES["task_detail"],
        cwd=context.cwd,
    )


def _build_status_snapshot(context: CliExecutionContext) -> dict[str, Any]:
    """Build a structured snapshot for read-only BossMod CLI queries."""
    room = get_room_at(context.state.x, context.state.y)
    room_name = room["name"] if room else "unknown"
    active = db.get_active_activity(context.agent.id)
    board = serialize_task_board(build_task_board(context.agent.id, scope="self"))
    current_task = db.get_task(active.task_id) if active and active.task_id else None
    open_tasks = [
        task for task in board["sections"].get("my_open_tasks", [])
        if not current_task or task.get("id") != current_task.id
    ]
    recent_completed = db.get_recent_completed_tasks(context.agent.id, limit=3)
    recent_artifacts = db.get_recent_artifact_refs(context.agent.id, limit=3)

    runtime = {
        "status": context.state.status,
        "location": room_name,
        "current_activity": active.kind if active else "none",
        "current_task": current_task.title if current_task else "none",
    }
    activity = None
    if active is not None:
        activity = {
            "kind": active.kind,
            "status": active.status,
            "title": active.title or active.kind,
            "detail": active.detail or "-",
            "destination": active.destination,
            "metadata": active.metadata or {},
        }
    task_data = None
    if current_task is not None:
        task_data = {
            "id": current_task.id,
            "title": current_task.title,
            "status": current_task.status,
            "description": current_task.description or "-",
            "completion_summary": current_task.completion_summary,
            "status_note": current_task.status_note,
        }

    return {
        "runtime": runtime,
        "current_activity": activity,
        "current_task": task_data,
        "open_tasks": [
            {
                "created_at": task["created_at"],
                "status": task["status"],
                "title": task["title"],
                "description": task.get("description") or task.get("status_note") or "-",
            }
            for task in open_tasks[-5:]
        ],
        "recent_completed_tasks": [
            {
                "created_at": task.get("last_activity") or task.get("created_at"),
                "status": str(task.get("status") or "unknown"),
                "title": str(task.get("title") or "Untitled"),
                "summary": task.get("completion_summary") or task.get("status_note") or "-",
            }
            for task in recent_completed
        ],
        "recent_work_artifacts": [
            {
                "created_at": artifact["created_at"],
                "task_id": artifact["task_id"],
                "task_title": artifact["task_title"],
                "path": artifact["path"],
                "title": artifact["title"],
                "type": artifact["type"],
            }
            for artifact in recent_artifacts
        ],
    }


def _runtime_lines(runtime: dict[str, Any]) -> list[str]:
    """Render runtime state lines from a structured snapshot."""
    return [
        f"status: {runtime['status']}",
        f"location: {runtime['location']}",
        f"current_activity: {runtime['current_activity']}",
        f"current_task: {runtime['current_task']}",
    ]


def _activity_lines(activity: dict[str, Any]) -> list[str]:
    """Render current activity detail lines."""
    lines = [
        f"kind: {activity['kind']}",
        f"status: {activity['status']}",
        f"title: {activity['title']}",
        f"detail: {activity['detail']}",
    ]
    if activity.get("destination"):
        lines.append(f"destination: {activity['destination']}")
    for key, value in (activity.get("metadata") or {}).items():
        lines.append(f"metadata.{key}: {value}")
    return lines


def _current_task_lines(task: dict[str, Any]) -> list[str]:
    """Render current task detail lines."""
    lines = [
        f"id: {task['id']}",
        f"title: {task['title']}",
        f"status: {task['status']}",
        f"description: {task['description']}",
    ]
    if task.get("completion_summary"):
        lines.append(f"completion_summary: {task['completion_summary']}")
    if task.get("status_note"):
        lines.append(f"status_note: {task['status_note']}")
    return lines


def _task_table_lines(tasks: list[dict[str, Any]], *, description_label: str) -> list[str]:
    """Render a compact task table."""
    lines = [f"datetime | status | task name | {description_label}"]
    if not tasks:
        lines.append("none")
        return lines
    for task in tasks:
        lines.append(
            " | ".join(
                [
                    _fmt_time(task["created_at"]),
                    str(task["status"]),
                    str(task["title"]),
                    trim(str(task.get("description") or "-")),
                ]
            )
        )
    return lines


def _completed_task_table_lines(tasks: list[dict[str, Any]]) -> list[str]:
    """Render recent completed tasks as a compact table."""
    lines = ["datetime | status | task name | summary"]
    if not tasks:
        lines.append("none")
        return lines
    for task in tasks:
        lines.append(
            " | ".join(
                [
                    _fmt_time(task["created_at"]),
                    str(task["status"]),
                    str(task["title"]),
                    trim(str(task["summary"])),
                ]
            )
        )
    return lines


def _artifact_table_lines(artifacts: list[dict[str, Any]]) -> list[str]:
    """Render recent work artifacts as a compact table."""
    lines = ["datetime | task | path | title"]
    if not artifacts:
        lines.append("none")
        return lines
    for artifact in artifacts:
        lines.append(
            " | ".join(
                [
                    _fmt_time(artifact["created_at"]),
                    trim(str(artifact.get("task_title") or "-")),
                    trim(str(artifact["path"])),
                    trim(str(artifact["title"])),
                ]
            )
        )
    return lines


def _fmt_time(value: object) -> str:
    """Format timestamps consistently for prompt results."""
    if value is None:
        return "unknown"
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _board_sections(board: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Render generic board sections with a consistent compact format."""
    sections: list[tuple[str, list[str]]] = []
    current_task = board.get("current_task")
    sections.append(("CURRENT TASK", ["none"] if current_task is None else _current_task_lines(current_task)))
    for name, rows in (board.get("sections") or {}).items():
        title = name.replace("_", " ").upper()
        sections.append((title, _task_table_lines(rows, description_label="description")))
    if board.get("assignee_rollup"):
        sections.append(("ASSIGNEE ROLLUP", _assignee_rollup_lines(board["assignee_rollup"])))
    return sections


def _assignee_rollup_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["assignee | counts"]
    if not rows:
        lines.append("none")
        return lines
    for row in rows:
        counts = ", ".join(f"{key}={value}" for key, value in sorted((row.get("counts") or {}).items()))
        lines.append(f"{row.get('agent_name') or row.get('agent_id')} | {counts or '-'}")
    return lines


def _task_event_lines(events: list[Any]) -> list[str]:
    lines = ["datetime | event | author | content"]
    if not events:
        lines.append("none")
        return lines
    for event in events:
        lines.append(
            " | ".join(
                [
                    _fmt_time(event.created_at),
                    event.event_type,
                    event.author_name,
                    trim(event.content),
                ]
            )
        )
    return lines
