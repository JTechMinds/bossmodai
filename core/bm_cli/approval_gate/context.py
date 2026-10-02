"""What System AI knows about the work behind one reviewed command.

A colleague asked "may I run this?" looks at who is asking, what they are
working on, what was just said, and how the operator answered similar
requests before. This module gathers exactly that, bounded by settings
rows. It reads only; it decides nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import db
from core import config
from core.agent_loop.deliverables import get_work_contract
from core.agent_loop.work_binding import bound_task_id
from core.bm_cli.approval_gate.effects import WRITE_NAMES
from core.bm_cli.approval_gate.facts import CommandFacts, looks_like_path
from core.bm_cli.floor_roots import agent_floor_id, floor_root
from core.bm_cli.parser import parse_cli_command
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID

logger = logging.getLogger(__name__)

CONTEXT_MESSAGES_SETTING = "cli_auto_approve_context_messages"
PRECEDENT_LIMIT_SETTING = "cli_auto_approve_precedent_limit"
OPERATOR_SPEAKER = "operator"

Shape = tuple[str, tuple[str, ...], tuple[str, ...]]


@dataclass(frozen=True)
class ReviewContext:
    """The work context sent to System AI beside the command facts.

    Attributes:
        agent: ``{name, specialty, description}`` from the hire contract.
        floor_projects: Virtual ``/projects/<slug>`` for each project on the
            agent's floor (empty for an agent on vacation).
        task: The bound task ``{title, description, status, project,
            deliverables}``, or None when the turn is detached or unbound.
        conversation: Recent ``{speaker, text}`` lines, oldest first.
        precedents: Floor-wide operator decisions ``{command, cwd, decision,
            by_agent, same_shape}``: same-shape first, then the same argv0,
            then the rest, each newest first.
    """

    agent: dict[str, Any]
    floor_projects: list[str]
    task: dict[str, Any] | None
    conversation: list[dict[str, str]]
    precedents: list[dict[str, Any]]


def build_review_context(
    agent: Agent,
    facts: CommandFacts,
    *,
    channel_id: str | None,
) -> ReviewContext:
    """Gather the review context for one command.

    Args:
        agent: The agent running the command.
        facts: The command's facts (its raw command drives same-shape
            precedent ordering).
        channel_id: The origin thread. When set, the conversation is that
            thread's transcript; otherwise it is the agent's DM with the
            operator.

    Returns:
        The context, every list bounded by its settings row.

    Raises:
        ConfigError: ``cli_auto_approve_context_messages`` or
            ``cli_auto_approve_precedent_limit`` is missing or not an int.
        LookupError: No agent owns ``agent.storage_key``.
    """
    floor_id = agent_floor_id(agent.storage_key)
    return ReviewContext(
        agent={
            "name": agent.name,
            "specialty": (agent.role or "").strip() or None,
            "description": (agent.description or "").strip() or None,
        },
        floor_projects=_floor_projects(floor_id),
        task=_bound_task(agent),
        conversation=_conversation(agent, channel_id),
        precedents=_precedents(agent, floor_id, facts.command),
    )


def command_shape(command: str) -> Shape | None:
    """Return ``(name, flags, operands)`` with paths collapsed to ``<path>``.

    Two commands match when this tuple matches. A parse failure is not a
    shape, so it never matches a remembered decision.
    """
    try:
        parsed = parse_cli_command(command or "")
    except ValueError:
        return None
    flags: list[str] = []
    operands: list[str] = []
    end_flags = False
    force = parsed.name in WRITE_NAMES
    tokens = list(parsed.args)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not end_flags and token == "--":
            end_flags = True
            index += 1
            continue
        if not end_flags and token.startswith("-") and token != "-":
            flags.append(token)
            index += 1
            continue
        if force or looks_like_path(token):
            operands.append("<path>")
        else:
            operands.append(token)
        index += 1
    return (parsed.name, tuple(sorted(flags)), tuple(operands))


def _floor_projects(floor_id: str | None) -> list[str]:
    if floor_id is None:
        return []
    root = floor_root(floor_id)
    # Dot folders (``.attachments``) are reserved floor folders, not projects.
    return sorted(
        f"/projects/{entry.name}"
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )


def _bound_task(agent: Agent) -> dict[str, Any] | None:
    task_id = bound_task_id(agent.id)
    if task_id is None:
        return None
    task = db.get_task(task_id)
    if task is None:
        logger.warning("auto-approve review: bound task %s for %s is missing", task_id, agent.id)
        return None
    return {
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "project": task.project,
        "deliverables": [item.path for item in get_work_contract(task).deliverables],
    }


def _conversation(agent: Agent, channel_id: str | None) -> list[dict[str, str]]:
    limit = config.require_int(CONTEXT_MESSAGES_SETTING)
    token = (channel_id or "").strip()
    if token:
        return [
            {
                "speaker": OPERATOR_SPEAKER
                if row["from_agent"] == HUMAN_SENDER_ID
                else str(row["from_name"] or row["from_agent"]),
                "text": row["content"],
            }
            for row in db.get_formatted_channel_messages(token, limit=limit)
        ]
    return [
        {
            "speaker": OPERATOR_SPEAKER if message.from_agent == HUMAN_SENDER_ID else agent.name,
            "text": message.content,
        }
        for message in db.get_human_chat_thread(agent.id, limit=limit)
    ]


def _precedents(agent: Agent, floor_id: str | None, command: str) -> list[dict[str, Any]]:
    from core.bm_cli.approvals import ALWAYS_ALLOWED_NOTE

    limit = config.require_int(PRECEDENT_LIMIT_SETTING)
    # Projects are floor-scoped, so the operator's answers to anyone on the
    # floor are precedent. An agent on vacation has only its own history.
    if floor_id is None:
        names = {agent.id: agent.name}
    else:
        names = {item.id: item.name for item in db.list_agents() if item.floor_id == floor_id}
        names.setdefault(agent.id, agent.name)
    # Two reads, each capped at ``limit``: the same program's history (so an
    # older same-shape answer is not crowded out by unrelated recent ones)
    # and the newest decisions overall.
    argv0 = _argv0(command)
    agent_ids = list(names)
    recent = db.list_human_cli_decisions(
        agent_ids, limit=limit, excluded_note_prefix=ALWAYS_ALLOWED_NOTE,
    )
    same_program = (
        db.list_human_cli_decisions(
            agent_ids, limit=limit, excluded_note_prefix=ALWAYS_ALLOWED_NOTE, argv0=argv0,
        )
        if argv0 is not None
        else []
    )
    rows = list({row.id: row for row in (*same_program, *recent)}.values())
    rows.sort(key=lambda row: (row.decided_at, row.id), reverse=True)
    shape = command_shape(command)
    items: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        same_shape = shape is not None and command_shape(row.command or "") == shape
        if same_shape:
            group = 0
        elif argv0 is not None and _argv0(row.command) == argv0:
            group = 1
        else:
            group = 2
        items.append((group, {
            "command": row.command,
            "cwd": row.cwd or "",
            "decision": row.status,
            "by_agent": names.get(row.agent_id, row.agent_id),
            "same_shape": same_shape,
        }))
    # Stable sort keeps newest first within each group.
    items.sort(key=lambda pair: pair[0])
    return [item for _group, item in items[:limit]]


def _argv0(command: str | None) -> str | None:
    """First whitespace token, as stored commands begin (``LIKE argv0 || ' %'``)."""
    parts = (command or "").split(maxsplit=1)
    return parts[0] if parts else None
