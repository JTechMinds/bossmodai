"""BossMod AI — Execution/runtime support for BossMod CLI calls."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from pathlib import Path
from typing import Any

import db
from core.bm_cli.filesystem import agent_artifact_dir, project_artifact_dir, resolve_relative_path
from core.models import Agent, AgentState
from core.world.tilemap import get_room_at


@dataclass(frozen=True, slots=True)
class BossModCliResult:
    """Turn-local result of a BossMod CLI command."""

    command: str
    ok: bool
    detail: str
    prompt_content: str
    kind: str = "generic"
    data: dict[str, Any] | None = None


def execute_bm_cli(agent: Agent, state: AgentState, command: str, content: str | None = None) -> BossModCliResult:
    """Execute a whitelisted BossMod CLI command for the given agent."""
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return _error_result(command, f"Command parse error: {exc}")

    if not tokens:
        return _error_result(command, "Command is empty.")

    try:
        if tokens[:3] == ["me", "get", "status"] and len(tokens) == 3:
            return _status_result(agent, state, command)
        if tokens[:3] == ["me", "get", "runtime"] and len(tokens) == 3:
            return _runtime_result(agent, state, command)
        if tokens[:3] == ["me", "get", "activity"] and len(tokens) == 3:
            return _activity_result(agent, state, command)
        if tokens[:3] == ["me", "get", "current-task"] and len(tokens) == 3:
            return _current_task_result(agent, state, command)
        if tokens[:3] == ["me", "get", "tasks"] and len(tokens) == 3:
            return _tasks_result(agent, state, command)
        if tokens[:3] == ["me", "get", "recent-work"] and len(tokens) == 3:
            return _recent_work_result(agent, state, command)
        if tokens[:3] == ["me", "get", "location"] and len(tokens) == 3:
            return _location_result(state, command)
        if tokens[:2] == ["me", "ls"] and len(tokens) == 2:
            return _ls_result(agent_artifact_dir(agent.name), command, label=f"{agent.name} personal notes")
        if tokens[:2] == ["me", "cat"] and len(tokens) == 3:
            return _cat_result(agent_artifact_dir(agent.name), tokens[2], command, label=f"{agent.name} personal notes")
        if tokens[:2] == ["me", "write"] and len(tokens) == 3:
            return _write_result(agent_artifact_dir(agent.name), tokens[2], command, label=f"{agent.name} personal notes", content=content)
        if tokens[:3] == ["me", "notes", "ls"] and len(tokens) == 3:
            return _ls_result(agent_artifact_dir(agent.name), command, label=f"{agent.name} personal notes")
        if tokens[:3] == ["me", "notes", "cat"] and len(tokens) == 4:
            return _cat_result(agent_artifact_dir(agent.name), tokens[3], command, label=f"{agent.name} personal notes")
        if tokens[:3] == ["me", "notes", "write"] and len(tokens) == 4:
            return _write_result(agent_artifact_dir(agent.name), tokens[3], command, label=f"{agent.name} personal notes", content=content)
        if len(tokens) >= 3 and tokens[0] == "project" and tokens[-1] == "ls" and len(tokens) == 3:
            return _project_ls_result(tokens[1], command)
        if len(tokens) >= 4 and tokens[0] == "project" and tokens[2] == "cat" and len(tokens) == 4:
            return _project_cat_result(tokens[1], tokens[3], command)
        if len(tokens) == 4 and tokens[0] == "project" and tokens[2] == "write":
            return _project_write_result(tokens[1], tokens[3], command, content=content)
        if len(tokens) == 4 and tokens[0] == "project" and tokens[2:] == ["notes", "ls"]:
            return _project_ls_result(tokens[1], command)
        if len(tokens) == 5 and tokens[0] == "project" and tokens[2] == "notes" and tokens[3] == "cat":
            return _project_cat_result(tokens[1], tokens[4], command)
        if len(tokens) == 5 and tokens[0] == "project" and tokens[2] == "notes" and tokens[3] == "write":
            return _project_write_result(tokens[1], tokens[4], command, content=content)
    except ValueError as exc:
        return _error_result(command, str(exc))

    return _error_result(command, f"Unsupported command: {command}")


def _status_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return a concise authoritative status snapshot."""
    snapshot = _build_status_snapshot(agent, state)
    return _success_result(
        command=command,
        detail=f"{agent.name} checked live status via BossMod CLI",
        kind="status",
        data=snapshot,
        sections=[
            ("RUNTIME STATUS", _runtime_lines(snapshot["runtime"])),
            ("OPEN TASKS", _task_table_lines(snapshot["open_tasks"], description_label="description")),
            ("RECENT COMPLETED TASKS", _completed_task_table_lines(snapshot["recent_completed_tasks"])),
            ("RECENT WORK ARTIFACTS", _artifact_table_lines(snapshot["recent_work_artifacts"])),
        ],
        authoritative_note="Use this snapshot as authoritative current state for this turn.",
    )


def _runtime_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return only the live runtime state block."""
    snapshot = _build_status_snapshot(agent, state)
    return _success_result(
        command=command,
        detail=f"{agent.name} checked live runtime via BossMod CLI",
        kind="runtime",
        data={"runtime": snapshot["runtime"]},
        sections=[("RUNTIME STATUS", _runtime_lines(snapshot["runtime"]))],
        authoritative_note="Use this runtime snapshot as authoritative current state for this turn.",
    )


def _activity_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return the active runtime activity details."""
    snapshot = _build_status_snapshot(agent, state)
    current_activity = snapshot["current_activity"]
    lines = ["none"] if current_activity is None else _activity_lines(current_activity)
    return _success_result(
        command=command,
        detail=f"{agent.name} checked current activity via BossMod CLI",
        kind="activity",
        data={"current_activity": current_activity},
        sections=[("CURRENT ACTIVITY", lines)],
        authoritative_note="Use this activity snapshot as authoritative for the current live thread.",
    )


def _current_task_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return the current bound task details, if any."""
    snapshot = _build_status_snapshot(agent, state)
    current_task = snapshot["current_task"]
    lines = ["none"] if current_task is None else _current_task_lines(current_task)
    return _success_result(
        command=command,
        detail=f"{agent.name} checked current task via BossMod CLI",
        kind="current_task",
        data={"current_task": current_task},
        sections=[("CURRENT TASK", lines)],
        authoritative_note="Use this task snapshot as authoritative for currently active work.",
    )


def _tasks_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return open tasks plus recent completed tasks."""
    snapshot = _build_status_snapshot(agent, state)
    return _success_result(
        command=command,
        detail=f"{agent.name} checked task lists via BossMod CLI",
        kind="tasks",
        data={
            "open_tasks": snapshot["open_tasks"],
            "recent_completed_tasks": snapshot["recent_completed_tasks"],
        },
        sections=[
            ("OPEN TASKS", _task_table_lines(snapshot["open_tasks"], description_label="description")),
            ("RECENT COMPLETED TASKS", _completed_task_table_lines(snapshot["recent_completed_tasks"])),
        ],
        authoritative_note="Use open tasks as current backlog and recent completed tasks as historical reference.",
    )


def _recent_work_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return recent completed work and recent artifacts."""
    snapshot = _build_status_snapshot(agent, state)
    return _success_result(
        command=command,
        detail=f"{agent.name} checked recent work via BossMod CLI",
        kind="recent_work",
        data={
            "recent_completed_tasks": snapshot["recent_completed_tasks"],
            "recent_work_artifacts": snapshot["recent_work_artifacts"],
        },
        sections=[
            ("RECENT COMPLETED TASKS", _completed_task_table_lines(snapshot["recent_completed_tasks"])),
            ("RECENT WORK ARTIFACTS", _artifact_table_lines(snapshot["recent_work_artifacts"])),
        ],
        authoritative_note="Treat this as recent historical work, not proof that work is still active now.",
    )


def _location_result(state: AgentState, command: str) -> BossModCliResult:
    """Return the agent's current physical location snapshot."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    return _success_result(
        command=command,
        detail="BossMod CLI returned current location",
        kind="location",
        data={"room": room_name, "x": state.x, "y": state.y, "runtime_status": state.status},
        sections=[("LOCATION", [f"room: {room_name}", f"x: {state.x}", f"y: {state.y}", f"runtime_status: {state.status}"])],
        authoritative_note="Use this location snapshot as authoritative current state for this turn.",
    )


def _project_ls_result(project_name: str, command: str) -> BossModCliResult:
    """List the files available under a project artifact directory."""
    return _ls_result(project_artifact_dir(project_name), command, label=f'project "{project_name}"')


def _project_cat_result(project_name: str, relative_path: str, command: str) -> BossModCliResult:
    """Read a file from a project artifact directory."""
    return _cat_result(project_artifact_dir(project_name), relative_path, command, label=f'project "{project_name}"')


def _project_write_result(project_name: str, relative_path: str, command: str, *, content: str | None) -> BossModCliResult:
    """Write a file into a project artifact directory."""
    return _write_result(project_artifact_dir(project_name), relative_path, command, label=f'project "{project_name}"', content=content)


def _ls_result(root: Path, command: str, *, label: str) -> BossModCliResult:
    """Render a stable directory listing for a personal or project artifact root."""
    if not root.exists():
        return _success_result(
            command=command,
            detail=f"BossMod CLI listed {label}",
            kind="listing",
            data={"label": label, "entries": []},
            sections=[(f"{label.upper()} FILES", ["none"])],
        )

    entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
    lines: list[str] = []
    if not entries:
        lines.append("none")
    else:
        for entry in entries:
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"- {entry.name}{suffix}")

    return _success_result(
        command=command,
        detail=f"BossMod CLI listed {label}",
        kind="listing",
        data={"label": label, "entries": [entry.name + ("/" if entry.is_dir() else "") for entry in entries]},
        sections=[(f"{label.upper()} FILES", lines)],
    )


def _cat_result(root: Path, relative_path: str, command: str, *, label: str) -> BossModCliResult:
    """Read and return a single file from a bounded artifact root."""
    if not root.exists():
        return _error_result(command, f'{label} does not exist yet.')

    path = resolve_relative_path(root, relative_path)
    if not path.exists():
        return _error_result(command, f'File not found: {relative_path}')
    if path.is_dir():
        return _error_result(command, f'Cannot read directory: {relative_path}')

    content = path.read_text(encoding="utf-8", errors="ignore")
    return _success_result(
        command=command,
        detail=f"BossMod CLI read {relative_path}",
        kind="file",
        data={"path": relative_path, "content": content},
        sections=[(f"FILE: {relative_path}", [_trim(content, limit=4000)])],
    )


def _write_result(root: Path, relative_path: str, command: str, *, label: str, content: str | None) -> BossModCliResult:
    """Write a bounded text file into a personal or project artifact root."""
    if content is None or not content.strip():
        return _error_result(command, 'Write commands require a non-empty "content" field.')

    root.mkdir(parents=True, exist_ok=True)
    path = resolve_relative_path(root, relative_path)
    if path.exists() and path.is_dir():
        return _error_result(command, f'Cannot write directory: {relative_path}')

    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.rstrip() + "\n"
    path.write_text(normalized, encoding="utf-8")

    return _success_result(
        command=command,
        detail=f"BossMod CLI wrote {relative_path}",
        kind="write",
        data={
            "label": label,
            "path": relative_path,
            "chars": len(normalized),
        },
        sections=[
            ("WRITE RESULT", [
                f"target: {relative_path}",
                f"chars: {len(normalized)}",
                "preview:",
                _trim(normalized, limit=600),
            ])
        ],
        authoritative_note="The file write succeeded inside the bounded BossMod artifact area.",
    )


def _error_result(command: str, message: str) -> BossModCliResult:
    """Render a turn-local CLI error result so the model can recover."""
    return BossModCliResult(
        command=command,
        ok=False,
        detail=f"BossMod CLI error: {message}",
        prompt_content=_render_sections(
            command,
            [("ERROR", [message, "Pick a supported command or continue without BossMod CLI."])],
        ),
        kind="error",
        data={"error": message},
    )


def _build_status_snapshot(agent: Agent, state: AgentState) -> dict[str, Any]:
    """Build a structured snapshot for read-only BossMod CLI queries."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    active = db.get_active_activity(agent.id)
    current_task = db.get_task(active.task_id) if active and active.task_id else None
    open_tasks = db.list_tasks(assigned_to=agent.id, status="pending") + db.list_tasks(assigned_to=agent.id, status="accepted")
    recent_completed = db.get_recent_completed_tasks(agent.id, limit=3)
    recent_artifacts = db.get_recent_work_artifacts(agent.id, limit=3)

    runtime = {
        "status": state.status,
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
                "created_at": task.created_at,
                "status": task.status,
                "title": task.title,
                "description": task.description or task.status_note or "-",
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
                "created_at": artifact.created_at,
                "type": artifact.message_type,
                "summary": (artifact.content or "").replace("\n", " "),
            }
            for artifact in recent_artifacts
        ],
    }


def _success_result(
    *,
    command: str,
    detail: str,
    kind: str,
    data: dict[str, Any],
    sections: list[tuple[str, list[str]]],
    authoritative_note: str | None = None,
) -> BossModCliResult:
    """Build a successful CLI result with both structured and prompt-ready forms."""
    return BossModCliResult(
        command=command,
        ok=True,
        detail=detail,
        prompt_content=_render_sections(command, sections, authoritative_note=authoritative_note),
        kind=kind,
        data=data,
    )


def _render_sections(
    command: str,
    sections: list[tuple[str, list[str]]],
    *,
    authoritative_note: str | None = None,
) -> str:
    """Render prompt-friendly CLI output from named sections."""
    lines = ["BOSSMOD CLI RESULT", f"command: {command}"]
    for title, content_lines in sections:
        lines.extend(["", f"{title}:"])
        lines.extend(content_lines or ["none"])
    if authoritative_note:
        lines.extend(["", authoritative_note])
    return "\n".join(lines)


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
                    _trim(str(task.get("description") or "-")),
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
                    _trim(str(task["summary"])),
                ]
            )
        )
    return lines


def _artifact_table_lines(artifacts: list[dict[str, Any]]) -> list[str]:
    """Render recent work artifacts as a compact table."""
    lines = ["datetime | type | summary"]
    if not artifacts:
        lines.append("none")
        return lines
    for artifact in artifacts:
        lines.append(
            " | ".join(
                [
                    _fmt_time(artifact["created_at"]),
                    str(artifact["type"]),
                    _trim(str(artifact["summary"])),
                ]
            )
        )
    return lines


def _fmt_time(value: object) -> str:
    """Format timestamps consistently for prompt results."""
    if value is None:
        return "unknown"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _trim(value: str, *, limit: int = 160) -> str:
    """Trim large values to keep tool results compact."""
    text = (value or "").strip()
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
