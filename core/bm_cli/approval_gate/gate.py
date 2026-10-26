"""The approval gate: card, block, or System AI approval for one command.

Runs only for ``approval_required`` (and default-tier) shell commands.
Code computes facts and enforces invariants; System AI judges intent and
relevance with the work context. Deny, never-allow and the project git
fence are decided before this module and stay in place. A jail escape or
a system path found here is returned as a block so System AI never sees it.

Yolo mode (``approval_mode`` → ``"yolo"``) approves right after those two
blocks, before the card-producing invariants and the System AI review: it
removes asking, never a check the system can perform.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

import db
from core import config
from core.config import ConfigError
from core.bm_cli.approval_gate.context import build_review_context
from core.bm_cli.approval_gate.facts import (
    CommandFacts,
    GateSegment,
    command_facts,
    effective_parsed_commands,
    host_refusal,
    script_facts,
)
from core.bm_cli.approval_gate.review import review_command
from core.bm_cli.policy_engine import argv0_basename_after_resolve
from core.bm_cli.shell_executor import PathJailError
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent

logger = logging.getLogger(__name__)

GLOBAL_AUTO_APPROVE_SETTING = "cli_auto_approve_global"
AUDIT_PREFIX = "approved-by=system"
SYSTEM_DECISION_BY = "system"
CLI_AUTO_APPROVED_EVENT = "cli_auto_approved"
UNSURE_PREFIX = "System AI unsure / refused: "
REVIEW_ERROR_PREFIX = "System AI review error: "
NOT_ASKED_CWD = (
    "System AI was not asked: the working directory is not a workspace path"
)
NOT_ASKED_ROOT = (
    "System AI was not asked: this removes or moves a whole project, clone, /me or .git"
)
NOT_ASKED_HOST_PROCESS = (
    "System AI was not asked: this affects host processes or containers the path jail cannot bound"
)

YOLO_THREAD_WHY = "[yolo] Yolo mode is on in this thread"
YOLO_DM_WHY = "[yolo] Yolo mode is on for this agent's DM"
YOLO_NOT_RUN_CWD = "Yolo mode did not run this: the working directory is not a workspace path"

PlanAction = Literal["card", "block", "approve"]
ApprovalMode = Literal["ask", "review", "yolo"]
_MOVE_NAMES = frozenset({"mv"})
_MV_TARGET_FLAGS = ("-t", "--target-directory")


@dataclass(frozen=True, slots=True)
class AutoApprovePlan:
    """What the caller should do with one approval_required command.

    Attributes:
        action: ``card`` (ask the operator), ``block`` (path jail), or
            ``approve`` (run with a System AI audit line).
        why: For ``approve``, ``"[<basis>] <reason>"``.
        jail_message: For ``block``, the ``Path jail: ...`` message.
        card_why: For ``card`` when the gate was on, why it did not approve.
    """

    action: PlanAction
    why: str = ""
    jail_message: str = ""
    card_why: str = ""


def global_auto_approve_enabled() -> bool:
    """Return whether Settings → Advanced "Global auto-approve" is on.

    A live read, not the process cache: the runtime worker must see a
    Settings save from the app process on its next command, the same reason
    ``cli_default_policy`` is read live. The API reads it through here too,
    so the UI greys out the conversation switches by the same answer.

    Returns:
        True when the setting is ``"true"``, False when it is ``"false"``.

    Raises:
        ConfigError: The setting is missing or is neither ``"true"`` nor
            ``"false"``. It is seeded, so either is a bug to surface, not
            an "off" to assume.
    """
    value = config.get_live(GLOBAL_AUTO_APPROVE_SETTING)
    if value is None:
        raise ConfigError(
            f"Required setting '{GLOBAL_AUTO_APPROVE_SETTING}' is not configured"
        )
    if value not in {"true", "false"}:
        raise ConfigError(
            f"Setting '{GLOBAL_AUTO_APPROVE_SETTING}' must be true or false, got {value!r}"
        )
    return value == "true"


def approval_mode(agent: Agent, channel_id: str | None) -> ApprovalMode:
    """Return how an approval_required command from this agent is decided here.

    The single resolver for every place auto-approve or Yolo can be turned
    on. Its modes:

    * ``"ask"``: the operator decides on an Approve card.
    * ``"review"``: the gate runs (invariants, then the System AI review).
    * ``"yolo"``: approved **without** review or card. The path-jail and
      host-refusal blocks still apply, as do never-allow, the consent gates
      and the executor's own jail, which sit outside the gate.

    Precedence, highest first; no flag ever overwrites another, so turning
    one off restores whatever the others say:

    1. The conversation's Yolo flag: an active ``channel_id`` thread's
       ``cli_yolo``, or (``channel_id`` None) the agent's ``cli_yolo_dm``.
    2. Global auto-approve on: ``"review"`` everywhere.
    3. The conversation's own auto-approve flag: the active thread's
       ``cli_auto_approve``, or the agent's ``cli_auto_approve_dm``.

    ``channel_id`` None covers the agent's DM and its work with no origin
    thread (DM-assigned, Focus or scheduled tasks, whose cards also land on
    the DM/Focus surface). A missing, archived or unknown thread has no
    flags of its own: ``"review"`` with global on, else ``"ask"``.

    Every flag is read from the database here rather than from ``agent``,
    so a toggle the operator flips mid-turn applies to the next command.

    Args:
        agent: The agent running the command.
        channel_id: The origin thread id, or None for a DM or no thread.

    Returns:
        ``"ask"``, ``"review"`` or ``"yolo"``.

    Raises:
        ConfigError: The global setting is missing or not a boolean. It is
            read first, so a bad value surfaces even when Yolo is on.
        LookupError: ``channel_id`` is None and the agent row is gone.
    """
    global_on = global_auto_approve_enabled()
    token = (channel_id or "").strip()
    if token:
        channel = db.get_channel(token)
        live = channel is not None and channel.status == "active"
        if live and channel.cli_yolo:
            return "yolo"
        if global_on or (live and channel.cli_auto_approve):
            return "review"
        return "ask"
    # The agent row is the only place the DM's Yolo flag lives, so it is
    # read even when global auto-approve alone would decide "review".
    current = db.get_agent(agent.id)
    if current is None:
        raise LookupError(f"Agent {agent.id} not found")
    if current.cli_yolo_dm:
        return "yolo"
    if global_on or current.cli_auto_approve_dm:
        return "review"
    return "ask"


def plan_auto_approve(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    policy_tier: str,
    channel_id: str | None,
    segments: Sequence[GateSegment] = (),
) -> AutoApprovePlan:
    """Decide card, block, or approve. Does not execute or write a decision.

    Order: enablement (``approval_mode``; ``"ask"`` is a plain card), facts,
    host refusal, then Yolo, then invariants (whole-root delete or move,
    host processes), review context, System AI review. Yolo approves after
    the two block steps and before everything that would ask, so a jail
    escape or a system path is still blocked under Yolo. When the working
    directory cannot be resolved, the host refusal cannot run, so Yolo
    fails closed to a card that says so. A script is one decision: its
    facts are the union over *segments* (with each segment's facts listed
    for the reviewer), and every invariant applies to every segment.

    Args:
        agent: The agent running the command.
        parsed: The parsed command; for a script, its ``raw`` is the full
            script text and its argv is not used.
        cwd: The agent's virtual working directory.
        policy_tier: The tier policy assigned (logged context only; the
            gate runs for approval-required commands).
        channel_id: The origin thread, or None for a DM.
        segments: A script's simple commands; empty for a single command.

    Returns:
        The plan. Every review failure is a card that says why. A Yolo
        approval's ``why`` is ``YOLO_THREAD_WHY`` or ``YOLO_DM_WHY``.

    Raises:
        ConfigError: The global auto-approve setting is missing or not a
            boolean, or a review-context setting is missing or not an int.
        LookupError: No agent owns ``agent.storage_key``, or (for a DM)
            ``agent.id``.
    """
    mode = approval_mode(agent, channel_id)
    if mode == "ask":
        return AutoApprovePlan(action="card")
    units = tuple(segments) or (GateSegment(parsed, cwd),)
    try:
        facts = (
            script_facts(agent, units, command=parsed.raw) if segments
            else command_facts(agent, parsed, cwd)
        )
    except PathJailError as exc:
        return AutoApprovePlan(action="block", jail_message=str(exc))
    except ValueError:
        card_why = YOLO_NOT_RUN_CWD if mode == "yolo" else NOT_ASKED_CWD
        return AutoApprovePlan(action="card", card_why=card_why)

    refusal = host_refusal(agent, facts)
    if refusal is not None:
        return AutoApprovePlan(action="block", jail_message=refusal)
    if mode == "yolo":
        why = YOLO_THREAD_WHY if (channel_id or "").strip() else YOLO_DM_WHY
        return AutoApprovePlan(action="approve", why=why)
    each = facts.segments or (facts,)
    if any(
        _removes_or_moves_a_root(agent, unit.parsed, unit.cwd, unit_facts)
        for unit, unit_facts in zip(units, each, strict=True)
    ):
        return AutoApprovePlan(action="card", card_why=NOT_ASKED_ROOT)
    if facts.effect == "host_process":
        return AutoApprovePlan(action="card", card_why=NOT_ASKED_HOST_PROCESS)

    context = build_review_context(agent, facts, channel_id=channel_id)
    outcome = review_command(facts, context)
    if outcome.verdict is None:
        logger.info("auto-approve card (%s, tier %s): %s", parsed.raw, policy_tier, outcome.error)
        return AutoApprovePlan(action="card", card_why=review_error_card_why(outcome.error))
    verdict = outcome.verdict
    if verdict.decision != "approve":
        return AutoApprovePlan(action="card", card_why=unsure_card_why(verdict.why))
    return AutoApprovePlan(action="approve", why=f"[{verdict.basis}] {verdict.why}")


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


def unsure_card_why(why: str) -> str:
    """Operator line when System AI answered and did not allow the command."""
    text = why.strip() or "no reason given"
    return f"{UNSURE_PREFIX}{text}"


def review_error_card_why(detail: str) -> str:
    """Operator line when the review call or its reply could not be used."""
    return f"{REVIEW_ERROR_PREFIX}{detail.strip()}"


def _removes_or_moves_a_root(
    agent: Agent, parsed: ParsedCliCommand, cwd: str, facts: CommandFacts,
) -> bool:
    """True when a delete, or a move's source, is a whole workspace root.

    A move's last operand is its destination, which may be a root (moving
    a file into a project). With ``-t``/``--target-directory`` the operand
    order no longer says which is the destination, so every operand is
    checked and the gate fails toward a card. Moves are found among the
    effective commands, so a wrapped ``timeout 5 mv`` or a ``find -exec
    mv`` is checked too; each move's own operands decide.
    """
    if facts.effect == "delete":
        return any(fact.is_root for fact in facts.write_targets)
    for command in effective_parsed_commands(parsed):
        if argv0_basename_after_resolve(command.name) not in _MOVE_NAMES:
            continue
        sources = command_facts(agent, command, cwd).write_targets
        if not any(token.startswith(_MV_TARGET_FLAGS) for token in command.args):
            sources = sources[:-1]
        if any(fact.is_root for fact in sources):
            return True
    return False
