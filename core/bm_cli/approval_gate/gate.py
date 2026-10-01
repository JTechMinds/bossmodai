"""The approval gate: card, block, or System AI approval for one command.

Runs only for ``approval_required`` (and default-tier) shell commands.
Code computes facts and enforces invariants; System AI judges intent and
relevance with the work context. Deny, never-allow and the project git
fence are decided before this module and stay in place. A jail escape or
a system path found here is returned as a block so System AI never sees it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Literal

import db
from core.bm_cli.approval_gate.context import build_review_context
from core.bm_cli.approval_gate.facts import (
    CommandFacts,
    command_facts,
    host_refusal,
)
from core.bm_cli.approval_gate.review import review_command
from core.bm_cli.policy_engine import argv0_basename_after_resolve
from core.bm_cli.shell_executor import PathJailError
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent

logger = logging.getLogger(__name__)

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

PlanAction = Literal["card", "block", "approve"]
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


def auto_approve_effective(agent: Agent, channel_id: str | None) -> bool:
    """Return whether auto-approve applies to this agent's command here.

    Today only the origin thread's flag enables it: True when ``channel_id``
    names an active channel with ``cli_auto_approve`` set. A missing,
    archived or unknown thread is off, and so is ``channel_id=None`` (a DM
    or work with no origin thread). DM enablement (per agent, through
    ``agent``) and the global override arrive in Phase 2 of the auto-approve
    redesign; this is the single resolver they will extend.

    Args:
        agent: The agent running the command (unused until DM enablement).
        channel_id: The origin thread id, or None for a DM.

    Returns:
        Whether the gate should try to approve instead of carding.
    """
    token = (channel_id or "").strip()
    if not token:
        return False
    channel = db.get_channel(token)
    if channel is None or channel.status != "active":
        return False
    return bool(channel.cli_auto_approve)


def plan_auto_approve(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
    *,
    policy_tier: str,
    channel_id: str | None,
) -> AutoApprovePlan:
    """Decide card, block, or approve. Does not execute or write a decision.

    Order: enablement, facts, host refusal, invariants (whole-root delete or
    move, host processes), review context, System AI review.

    Args:
        agent: The agent running the command.
        parsed: The parsed command.
        cwd: The agent's virtual working directory.
        policy_tier: The tier policy assigned (logged context only; the
            gate runs for approval-required commands).
        channel_id: The origin thread, or None for a DM.

    Returns:
        The plan. Every review failure is a card that says why.

    Raises:
        ConfigError: A review-context setting is missing or not an int.
        LookupError: No agent owns ``agent.storage_key``.
    """
    if not auto_approve_effective(agent, channel_id):
        return AutoApprovePlan(action="card")
    try:
        facts = command_facts(agent, parsed, cwd)
    except PathJailError as exc:
        return AutoApprovePlan(action="block", jail_message=str(exc))
    except ValueError:
        return AutoApprovePlan(action="card", card_why=NOT_ASKED_CWD)

    refusal = host_refusal(agent, facts)
    if refusal is not None:
        return AutoApprovePlan(action="block", jail_message=refusal)
    if _removes_or_moves_a_root(parsed, facts):
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


def _removes_or_moves_a_root(parsed: ParsedCliCommand, facts: CommandFacts) -> bool:
    """True when a delete, or a move's source, is a whole workspace root.

    A move's last operand is its destination, which may be a root (moving
    a file into a project). With ``-t``/``--target-directory`` the operand
    order no longer says which is the destination, so every operand is
    checked and the gate fails toward a card.
    """
    if facts.effect == "delete":
        return any(fact.is_root for fact in facts.write_targets)
    if argv0_basename_after_resolve(parsed.name) not in _MOVE_NAMES:
        return False
    sources = facts.write_targets
    if not any(token.startswith(_MV_TARGET_FLAGS) for token in parsed.args):
        sources = sources[:-1]
    return any(fact.is_root for fact in sources)
