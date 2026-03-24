"""BossMod AI — Execution/runtime support for BossMod CLI calls."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from pathlib import Path

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


def execute_bm_cli(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
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
        if tokens[:3] == ["me", "get", "location"] and len(tokens) == 3:
            return _location_result(state, command)
        if tokens[:2] == ["me", "ls"] and len(tokens) == 2:
            return _ls_result(agent_artifact_dir(agent.name), command, label=f"{agent.name} personal notes")
        if tokens[:2] == ["me", "cat"] and len(tokens) == 3:
            return _cat_result(agent_artifact_dir(agent.name), tokens[2], command, label=f"{agent.name} personal notes")
        if len(tokens) >= 3 and tokens[0] == "project" and tokens[-1] == "ls" and len(tokens) == 3:
            return _project_ls_result(tokens[1], command)
        if len(tokens) >= 4 and tokens[0] == "project" and tokens[2] == "cat" and len(tokens) == 4:
            return _project_cat_result(tokens[1], tokens[3], command)
    except ValueError as exc:
        return _error_result(command, str(exc))

    return _error_result(command, f"Unsupported command: {command}")


def _status_result(agent: Agent, state: AgentState, command: str) -> BossModCliResult:
    """Return a concise authoritative status snapshot."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    active = db.get_active_activity(agent.id)
    current_task = db.get_task(active.task_id) if active and active.task_id else None
    open_tasks = db.list_tasks(assigned_to=agent.id, status="pending") + db.list_tasks(assigned_to=agent.id, status="accepted")
    recent_completed = db.get_recent_completed_tasks(agent.id, limit=3)
    recent_artifacts = db.get_recent_work_artifacts(agent.id, limit=2)

    lines = [
        "BOSSMOD CLI RESULT",
        f"command: {command}",
        "",
        "RUNTIME STATUS:",
        f"status: {state.status}",
        f"location: {room_name}",
        f"current_activity: {active.kind if active else 'none'}",
        f"current_task: {current_task.title if current_task else 'none'}",
        "",
        "OPEN TASKS:",
        "datetime | status | task name | description",
    ]
    if open_tasks:
        for task in open_tasks[-5:]:
            lines.append(
                " | ".join(
                    [
                        _fmt_time(task.created_at),
                        task.status,
                        task.title,
                        _trim(task.description or task.status_note or "-"),
                    ]
                )
            )
    else:
        lines.append("none")

    lines.extend(["", "RECENT COMPLETED TASKS:", "datetime | status | task name | summary"])
    if recent_completed:
        for task in recent_completed:
            lines.append(
                " | ".join(
                    [
                        _fmt_time(task.get("last_activity") or task.get("created_at")),
                        str(task.get("status") or "unknown"),
                        str(task.get("title") or "Untitled"),
                        _trim(task.get("completion_summary") or task.get("status_note") or "-"),
                    ]
                )
            )
    else:
        lines.append("none")

    lines.extend(["", "RECENT WORK ARTIFACTS:", "datetime | type | summary"])
    if recent_artifacts:
        for artifact in recent_artifacts:
            lines.append(
                " | ".join(
                    [
                        _fmt_time(artifact.created_at),
                        artifact.message_type,
                        _trim((artifact.content or "").replace("\n", " ")),
                    ]
                )
            )
    else:
        lines.append("none")

    lines.extend(
        [
            "",
            "Use this snapshot as authoritative current state for this turn.",
        ]
    )
    return BossModCliResult(
        command=command,
        ok=True,
        detail=f"{agent.name} checked live status via BossMod CLI",
        prompt_content="\n".join(lines),
    )


def _location_result(state: AgentState, command: str) -> BossModCliResult:
    """Return the agent's current physical location snapshot."""
    room = get_room_at(state.x, state.y)
    room_name = room["name"] if room else "unknown"
    lines = [
        "BOSSMOD CLI RESULT",
        f"command: {command}",
        "",
        "LOCATION:",
        f"room: {room_name}",
        f"x: {state.x}",
        f"y: {state.y}",
        f"runtime_status: {state.status}",
        "",
        "Use this location snapshot as authoritative current state for this turn.",
    ]
    return BossModCliResult(
        command=command,
        ok=True,
        detail="BossMod CLI returned current location",
        prompt_content="\n".join(lines),
    )


def _project_ls_result(project_name: str, command: str) -> BossModCliResult:
    """List the files available under a project artifact directory."""
    return _ls_result(project_artifact_dir(project_name), command, label=f'project "{project_name}"')


def _project_cat_result(project_name: str, relative_path: str, command: str) -> BossModCliResult:
    """Read a file from a project artifact directory."""
    return _cat_result(project_artifact_dir(project_name), relative_path, command, label=f'project "{project_name}"')


def _ls_result(root: Path, command: str, *, label: str) -> BossModCliResult:
    """Render a stable directory listing for a personal or project artifact root."""
    if not root.exists():
        lines = [
            "BOSSMOD CLI RESULT",
            f"command: {command}",
            "",
            f"{label.upper()} FILES:",
            "none",
        ]
        return BossModCliResult(command=command, ok=True, detail=f"BossMod CLI listed {label}", prompt_content="\n".join(lines))

    entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
    lines = [
        "BOSSMOD CLI RESULT",
        f"command: {command}",
        "",
        f"{label.upper()} FILES:",
    ]
    if not entries:
        lines.append("none")
    else:
        for entry in entries:
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"- {entry.name}{suffix}")

    return BossModCliResult(
        command=command,
        ok=True,
        detail=f"BossMod CLI listed {label}",
        prompt_content="\n".join(lines),
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
    lines = [
        "BOSSMOD CLI RESULT",
        f"command: {command}",
        "",
        f"FILE: {relative_path}",
        _trim(content, limit=4000),
    ]
    return BossModCliResult(
        command=command,
        ok=True,
        detail=f"BossMod CLI read {relative_path}",
        prompt_content="\n".join(lines),
    )


def _error_result(command: str, message: str) -> BossModCliResult:
    """Render a turn-local CLI error result so the model can recover."""
    lines = [
        "BOSSMOD CLI RESULT",
        f"command: {command}",
        "",
        f"ERROR: {message}",
        "Pick a supported command or continue without BossMod CLI.",
    ]
    return BossModCliResult(
        command=command,
        ok=False,
        detail=f"BossMod CLI error: {message}",
        prompt_content="\n".join(lines),
    )


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
