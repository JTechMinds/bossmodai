"""Opt-in per-thread review of ``approval_required`` commands.

The thread flag defaults off. When it is on, host guardrails run before
System AI. A pass asks for ``{"allow": bool, "why": str}``. Anything
uncertain, unreadable, or failed stays on the Approve card. Deny,
never-allow, the project git fence, and the path jail are not decided
here — callers leave those blocks in place, and a jail escape found by
the guardrail is returned as a block so System AI cannot approve it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import db
from core.bm_cli import install_layout
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.host_roots import is_within_roots
from core.bm_cli.project_repo import project_directory_for
from core.bm_cli.shell_executor import (
    PathJailError,
    allowed_shell_roots,
    path_candidates_from_token,
    resolve_jailed_path,
)
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.llm.system_completion import complete_text
from core.models import Agent

logger = logging.getLogger(__name__)

AUDIT_PREFIX = "approved-by=system"
SYSTEM_DECISION_BY = "system"
CLI_AUTO_APPROVED_EVENT = "cli_auto_approved"

_REVIEW_KEYS = frozenset({"allow", "why"})
_WRITE_NAMES = frozenset({
    "rm", "rmdir", "unlink", "mv", "cp", "chmod", "chown", "chgrp",
    "touch", "mkdir", "install", "tee", "dd", "truncate", "ln", "shred",
})
# Effects that are not a path under the bound project. Fail closed to a card.
_OPAQUE_NAMES = frozenset({
    "pip", "npm", "curl", "wget", "docker", "kill", "pkill",
})
_SYSTEM_ROOTS = (
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/root"),
    Path("/boot"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr"),
    Path("/lib"),
    Path("/lib64"),
    Path("/var"),
    Path("/opt"),
    Path("/run"),
)

_REVIEW_SYSTEM = (
    "You decide whether one command that already matched approval_required "
    "is safe to run. Reply with JSON only, no markdown: "
    '{"allow": true or false, "why": "short reason"}. '
    "Set allow true only when the command is a routine local change whose "
    "effects stay inside the resolved paths. If you are unsure, set allow "
    "to false. You cannot override a deny, a never-allow rule, a project "
    "git fence, or a path jail."
)

PlanAction = Literal["card", "block", "approve"]


@dataclass(frozen=True, slots=True)
class AutoApprovePlan:
    """What the caller should do with one approval_required command."""

    action: PlanAction
    why: str = ""
    jail_message: str = ""


def audit_line(why: str) -> str:
    """Return the operator-facing audit line for one System AI approval."""
    return f"{AUDIT_PREFIX} {why.strip()}"


def attach_system_audit(result: BossModCliResult, why: str) -> BossModCliResult:
    """Stamp the audit line onto a CLI result the turn and the log both keep."""
    line = audit_line(why)
    data = dict(result.data or {})
    data["audit"] = line
    data["approved_by"] = SYSTEM_DECISION_BY
    prompt = result.prompt_content or ""
    if line not in prompt:
        prompt = f"{line}\n{prompt}" if prompt else line
    detail = result.detail or ""
    if line not in detail:
        detail = f"{detail} {line}".strip()
    return replace(result, data=data, prompt_content=prompt, detail=detail)


def thread_cli_auto_approve_enabled(channel_id: str | None) -> bool:
    """Return whether this live thread opted in. Missing or archived is off."""
    token = (channel_id or "").strip()
    if not token:
        return False
    channel = db.get_channel(token)
    if channel is None or channel.status != "active":
        return False
    return bool(channel.cli_auto_approve)


def plan_thread_auto_approve(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    policy_tier: str,
    channel_id: str | None,
) -> AutoApprovePlan:
    """Decide card, block, or approve. Does not execute or write a decision."""
    if not thread_cli_auto_approve_enabled(channel_id):
        return AutoApprovePlan(action="card")
    if (parsed.name or "") in _OPAQUE_NAMES:
        return AutoApprovePlan(action="card")

    try:
        real_cwd, paths = _resolved_paths(agent, parsed, cwd)
    except PathJailError as exc:
        return AutoApprovePlan(action="block", jail_message=str(exc))
    except ValueError:
        return AutoApprovePlan(action="card")

    refusal = _host_refusal(agent, real_cwd, paths)
    if refusal is not None:
        return AutoApprovePlan(action="block", jail_message=refusal)
    if not _writes_stay_in_scope(agent, parsed, real_cwd, paths):
        return AutoApprovePlan(action="card")

    reviewed = _review(
        command=parsed.raw,
        cwd=cwd,
        policy_tier=policy_tier,
        resolved_paths=tuple(str(path) for path in paths),
    )
    if reviewed is None or not reviewed[0]:
        return AutoApprovePlan(action="card")
    return AutoApprovePlan(action="approve", why=reviewed[1])


def log_system_auto_approve(agent_name: str, line: str) -> None:
    """Persist the audit on the activity feed, and broadcast it when a loop is up."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        db.create_activity_log_entry(
            event=CLI_AUTO_APPROVED_EVENT,
            detail=line,
            agent_name=agent_name,
        )
        return

    async def _send() -> None:
        try:
            from core.runtime.events import runtime_events

            await runtime_events.broadcast_activity(
                event=CLI_AUTO_APPROVED_EVENT,
                detail=line,
                agent_name=agent_name,
            )
        except Exception:
            logger.warning("system auto-approve activity broadcast failed")
            db.create_activity_log_entry(
                event=CLI_AUTO_APPROVED_EVENT,
                detail=line,
                agent_name=agent_name,
            )

    loop.create_task(_send())


def parse_review_payload(raw: str | None) -> tuple[bool, str] | None:
    """Parse a fail-closed review object. None rejects the payload."""
    if not raw or not str(raw).strip():
        return None
    try:
        payload = json.loads(_unwrap_json(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or set(payload) != _REVIEW_KEYS:
        return None
    allow = payload.get("allow")
    why = payload.get("why")
    if not isinstance(allow, bool) or not isinstance(why, str):
        return None
    text = why.strip()
    if allow and not text:
        return None
    return allow, text


def _review(
    *,
    command: str,
    cwd: str,
    policy_tier: str,
    resolved_paths: tuple[str, ...],
) -> tuple[bool, str] | None:
    messages = [
        {"role": "system", "content": _REVIEW_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "command": command,
                    "cwd": cwd,
                    "policy_tier": policy_tier,
                    "resolved_paths": list(resolved_paths),
                },
                ensure_ascii=True,
            ),
        },
    ]
    try:
        raw = complete_text(messages)
    except Exception:
        logger.warning("system auto-approve review failed closed")
        return None
    return parse_review_payload(raw)


def _resolved_paths(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
) -> tuple[Path, list[Path]]:
    from core.bm_cli.locked_clone_outcome import rewrite_virtual_shell_paths
    from core.bm_cli.virtual_fs import resolve_cli_path

    resolved = resolve_cli_path(agent.storage_key, cwd, ".")
    if resolved.real_path is None:
        raise ValueError("cwd is not a real workspace path")
    real_cwd = Path(resolved.real_path).resolve()
    rewritten = rewrite_virtual_shell_paths(agent, parsed, cwd)
    force = rewritten.name in _WRITE_NAMES
    paths: list[Path] = []
    for token in _path_tokens(rewritten.args, force_operands=force):
        paths.append(_resolve_token(token, real_cwd))
    return real_cwd, paths


def _path_tokens(args: tuple[str, ...], *, force_operands: bool) -> list[str]:
    tokens = list(args)
    found: list[str] = []
    end_flags = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {">", ">>"}:
            if index + 1 >= len(tokens):
                raise ValueError("redirect has no target")
            found.append(tokens[index + 1])
            index += 2
            continue
        if not end_flags and token == "--":
            end_flags = True
            index += 1
            continue
        if not end_flags and token.startswith("-") and token != "-":
            found.extend(path_candidates_from_token(token))
            index += 1
            continue
        if force_operands or _looks_like_path(token):
            found.append(token)
        index += 1
    return found


def _looks_like_path(token: str) -> bool:
    if not token or token == "-":
        return False
    if token.startswith("~") or token.startswith("/") or token.startswith("\\"):
        return True
    if token in {".", ".."} or token.startswith("./") or token.startswith("../"):
        return True
    return "/" in token or "\\" in token


def _resolve_token(token: str, cwd: Path) -> Path:
    if token.startswith("~"):
        resolved = resolve_jailed_path(token, cwd=cwd)
        if resolved is None:
            raise PathJailError(f"Path jail: {token!r} is not allowed")
        return resolved
    path = Path(token)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except OSError as exc:
        raise PathJailError(f"Path jail: cannot resolve {token!r}: {exc}") from exc


def _host_refusal(agent: Agent, real_cwd: Path, paths: list[Path]) -> str | None:
    """Refuse system roots, the application install, and jail escapes."""
    roots = allowed_shell_roots(agent.storage_key)
    me = agent_artifact_dir(agent.storage_key).resolve()
    if not is_within_roots(real_cwd, roots):
        return (
            f"Path jail: working directory {str(real_cwd)!r} is outside "
            "the allowed workspace roots"
        )
    for path in (real_cwd, *paths):
        why = _refuse_path(path, jail_roots=roots, me=me)
        if why is not None:
            return why
    return None


def _refuse_path(path: Path, *, jail_roots: tuple[Path, ...], me: Path) -> str | None:
    if path == Path(path.anchor):
        return f"Path jail: {str(path)!r} is the filesystem root"
    for root in _SYSTEM_ROOTS:
        if path == root or root in path.parents:
            return f"Path jail: {str(path)!r} is a host system path"
    install = install_layout.app_install_root().resolve()
    if (path == install or install in path.parents) and not is_within_roots(path, (me,)):
        return f"Path jail: {str(path)!r} is inside the application install"
    if not is_within_roots(path, jail_roots):
        return (
            f"Path jail: {str(path)!r} resolves outside the allowed workspace roots"
        )
    return None


def _writes_stay_in_scope(
    agent: Agent,
    parsed: ParsedCliCommand,
    real_cwd: Path,
    paths: list[Path],
) -> bool:
    """Deletes and writes may only land in the bound project or granted ``/me``."""
    if parsed.name not in _WRITE_NAMES and not _has_redirect(parsed.args):
        return True
    if not paths:
        return False
    allowed = _write_roots(agent, real_cwd)
    return all(is_within_roots(path, allowed) for path in paths)


def _has_redirect(args: tuple[str, ...]) -> bool:
    return any(token in {">", ">>"} for token in args)


def _write_roots(agent: Agent, real_cwd: Path) -> tuple[Path, ...]:
    """Bound project realpath, plus the agent's own ``/me`` workspace."""
    me = agent_artifact_dir(agent.storage_key).resolve()
    roots = [me]
    project = project_directory_for(real_cwd)
    if project is not None:
        roots.insert(0, project.resolve())
    return tuple(roots)


def _unwrap_json(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()
