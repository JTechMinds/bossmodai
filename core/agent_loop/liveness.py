"""BossMod AI — Task liveness bookkeeping.

Successful project writes and other mutating CLI outcomes reset the
no-progress streak. Reads, failed writes, and consent pauses do not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.bm_cli.command_registry import resolve_virtual_command_name
from core.bm_cli.parser import parse_cli_command

_PROGRESS_ACTIONS = {
    "work",
    "waiting",
    "complete",
    "blocked",
    "delegated",
    "abandoned",
    "attendMeeting",
    "remoteMeeting",
}

# Virtual CLI kinds whose success means a file or tree actually changed.
_PROGRESS_KINDS = frozenset(
    {
        "write",
        "append",
        "mkdir",
        "replace-section",
        "rewrite-section",
        "batch-write",
        "git_restore",
    }
)
_FILE_MUTATIONS = frozenset({"write", "append", "bwrite", "mkdir", "repsect", "rewsect"})
_READ_ONLY_SHELL = frozenset(
    {
        "ls",
        "cat",
        "pwd",
        "cd",
        "ol",
        "head",
        "tail",
        "find",
        "rg",
        "grep",
        "echo",
        "printf",
        "wc",
        "file",
        "stat",
        "which",
        "whoami",
        "env",
        "printenv",
        "true",
        "test",
        "diff",
        "cmp",
        "less",
        "more",
    }
)
_READ_ONLY_GIT = frozenset({"status", "log", "diff", "show", "rev-parse", "branch", "remote"})


def record_task_heartbeat(task_id: str | None, *, at: datetime | None = None) -> None:
    """Record that a task-bound turn is still alive."""
    if not task_id:
        return
    timestamp = at or datetime.now(timezone.utc)
    db.update_task(
        task_id,
        last_heartbeat_at=timestamp,
        last_activity=timestamp,
        watchdog_pinged_at=None,
    )


def record_task_progress(task_id: str | None, *, at: datetime | None = None) -> None:
    """Record that tangible progress happened on a task."""
    if not task_id:
        return
    timestamp = at or datetime.now(timezone.utc)
    db.update_task(
        task_id,
        last_progress_at=timestamp,
        last_heartbeat_at=timestamp,
        last_activity=timestamp,
        watchdog_pinged_at=None,
    )


def next_actions_since_progress(streak: int, *, progressed: bool) -> int:
    """Return the no-progress streak after one action.

    A landed write or other real outcome resets it to zero. The guardian
    compares the returned streak with ``guardian_no_progress_threshold``.
    """
    if progressed:
        return 0
    return max(streak, 0) + 1


def outcome_resets_no_progress(action: dict[str, Any], result: dict[str, Any]) -> bool:
    """Return True when this action is real work and the idle streak should reset."""
    if result.get("consent_required") or result.get("approval_required"):
        return False
    action_name = action.get("action")
    event = result.get("event")
    if action_name == "work" and event == "agent_updated":
        return True
    if action_name in {"attendMeeting", "remoteMeeting"} and event == "meeting_started":
        return True
    if action_name in _PROGRESS_ACTIONS - {"work", "attendMeeting", "remoteMeeting"}:
        return True
    if event != "bm_cli_result":
        return False
    if "counts_as_progress" in result:
        return bool(result.get("counts_as_progress"))
    managed = result.get("managed_writer")
    if isinstance(managed, dict) and managed.get("completed"):
        return True
    batch = result.get("batch_writer")
    if isinstance(batch, dict) and batch.get("completed"):
        return True
    return command_mutates_project(str(action.get("command") or ""))


def cli_result_counts_as_progress(cli_result: Any) -> bool:
    """Return True when a CLI result landed a project mutation."""
    if not getattr(cli_result, "ok", False):
        return False
    if getattr(cli_result, "consent_required", False) or getattr(cli_result, "approval_required", False):
        return False
    data = getattr(cli_result, "data", None) or {}
    if (
        data.get("managed_writer_completed")
        or data.get("managed_writer_used")
        or data.get("batch_writer_completed")
        or data.get("batch_writer_used")
    ):
        return True
    return command_mutates_project(
        str(getattr(cli_result, "command", "") or ""),
        kind=str(getattr(cli_result, "kind", "") or ""),
        executor=str(getattr(cli_result, "executor", "") or ""),
    )


def command_mutates_project(command: str, *, kind: str = "", executor: str = "") -> bool:
    """Return True for a write or other CLI that changes the project."""
    if kind in _PROGRESS_KINDS:
        return True
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        return False
    if kind == "shell" or executor == "shell" or resolve_virtual_command_name(parsed.name) is None:
        return _shell_mutates(parsed.name, parsed.args)
    return _virtual_mutates(parsed.name, parsed.args)


def _virtual_mutates(name: str, args: tuple[str, ...]) -> bool:
    token = (name or "").strip().lower()
    if token in _FILE_MUTATIONS:
        return True
    if token == "git" and args:
        return args[0].strip().lower() == "restore"
    return False


def _shell_mutates(name: str, args: tuple[str, ...]) -> bool:
    token = (name or "").strip().lower()
    if token == "git":
        sub = args[0].strip().lower() if args else ""
        return bool(sub) and sub not in _READ_ONLY_GIT
    return token not in _READ_ONLY_SHELL


def record_action_liveness(
    task_id: str | None,
    action: dict[str, Any],
    result: dict[str, Any],
    *,
    at: datetime | None = None,
) -> None:
    """Update task liveness based on an executed action."""
    if not task_id:
        return
    if outcome_resets_no_progress(action, result):
        record_task_progress(task_id, at=at)
        return
    record_task_heartbeat(task_id, at=at)
