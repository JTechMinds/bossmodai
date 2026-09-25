"""BossMod AI — Task liveness bookkeeping.

Each execution step is classified by :func:`classify_step`. Successful
project writes and other mutating outcomes are ``progress`` and reset the
no-progress streak. A successful first read of something new is ``novel``
and leaves the streak alone. A repeat of a step already taken on this work
activity, or any failed command, is ``stale`` and grows it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

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


StepKind = Literal["progress", "novel", "stale"]

# Non-CLI results that mean the step did not do what it asked for.
_FAILED_EVENTS = frozenset({"agent_error", "bm_cli_error"})


def classify_step(
    action: dict[str, Any],
    result: dict[str, Any],
    seen_fingerprints: set[str] | frozenset[str],
) -> StepKind:
    """Classify one executed step for the no-progress guardian.

    Args:
        action: The parsed execution action.
        result: The turn-local action result.
        seen_fingerprints: Fingerprints of every step already taken on this
            work activity, across pauses. Not mutated.

    Returns:
        ``progress`` for a mutation or lifecycle outcome, ``novel`` for a
        successful step with an unseen fingerprint, ``stale`` for a repeat
        fingerprint or any failed command.
    """
    if outcome_resets_no_progress(action, result):
        return "progress"
    if result.get("event") in _FAILED_EVENTS:
        return "stale"
    if step_fingerprint(action) in seen_fingerprints:
        return "stale"
    return "novel"


def next_stale_streak(streak: int, kind: StepKind) -> int:
    """Return the consecutive-stale streak after one classified step.

    ``progress`` resets it, ``novel`` leaves it, ``stale`` adds one. The
    guardian compares the result with ``guardian_no_progress_threshold``.
    """
    if kind == "progress":
        return 0
    if kind == "novel":
        return max(streak, 0)
    return max(streak, 0) + 1


def step_fingerprint(action: dict[str, Any]) -> str:
    """Stable identity for one execution step.

    A CLI step is its :func:`command_fingerprint`. Any other action is its
    name plus its canonical JSON arguments; the free-text ``thought`` is not
    part of the identity.
    """
    if action.get("action") == "bm_cli":
        content = action.get("content") if isinstance(action.get("content"), str) else None
        return command_fingerprint(str(action.get("command") or ""), content)
    arguments = {
        key: value
        for key, value in action.items()
        if key != "thought" and not str(key).startswith("_")
    }
    return json.dumps(arguments, sort_keys=True, default=str)


def command_fingerprint(command: str, content: str | None = None) -> str:
    """Stable identity for a CLI command.

    Path tweaks must not dodge the check: ``ls a`` ≡ ``ls a/`` ≡ ``ls ./a``.
    Command aliases and extra whitespace collapse. Write-body content is part
    of the identity when present.
    """
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        collapsed = " ".join((command or "").split())
        return _join_fingerprint(collapsed.lower(), content)

    args = tuple(
        normalized
        for normalized in (_normalize_fingerprint_arg(arg) for arg in parsed.args)
        if normalized
    )
    body = " ".join((parsed.name, *args)).strip()
    return _join_fingerprint(body, content)


def _join_fingerprint(command_body: str, content: str | None) -> str:
    extra = (content or "").strip()
    if extra:
        return f"{command_body}\n{extra}"
    return command_body


def _normalize_fingerprint_arg(arg: str) -> str:
    token = arg.strip()
    if token.startswith("-") and token != "-":
        return token
    return _normalize_fingerprint_path(token)


def _normalize_fingerprint_path(raw: str) -> str:
    text = raw.strip().replace("\\", "/")
    if not text:
        return ""
    while "//" in text:
        text = text.replace("//", "/")
    if text != "/":
        text = text.rstrip("/")
    while text.startswith("./"):
        text = text[2:]
        if text != "/":
            text = text.rstrip("/")
    if text in {"", "."}:
        return ""

    absolute = text.startswith("/")
    parts: list[str] = []
    for item in text.split("/"):
        if item in {"", "."}:
            continue
        if item == "..":
            if parts:
                parts.pop()
            continue
        parts.append(item)
    if absolute:
        return "/" + "/".join(parts) if parts else "/"
    return "/".join(parts)


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
