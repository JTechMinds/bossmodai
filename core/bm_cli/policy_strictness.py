"""Combine several policy decisions for one command line into the strictest.

One command can run several programs (a wrapper's command, ``find -exec``,
later each segment of a script). Each gets its own decision; this module
picks the one that governs the whole line. Pure; the decision type is
imported for typing only, because the policy engine imports this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.bm_cli.policy_engine import CommandPolicyDecision


def strictest(decisions: Sequence[CommandPolicyDecision]) -> CommandPolicyDecision:
    """Return the strictest of several decisions for one command line.

    Order: never_allowed, then any other hard deny (``cli_default_policy``
    deny), then approval_required, then a default-tier approval, then
    allowed. A hard deny ranks above approval so an approval card can never
    run a command the default policy denies. Among allowed decisions a
    rule with a ``cwd_prefix`` wins (a non-nest prefix first, then the
    longest), so the scoped-Always containment check sees it. Otherwise
    the first decision wins a tie.

    Args:
        decisions: One decision per effective command, in order.

    Returns:
        The strictest decision.

    Raises:
        ValueError: *decisions* is empty.
    """
    if not decisions:
        raise ValueError("strictest() needs at least one decision")
    return max(decisions, key=_strictness)


def _strictness(decision: CommandPolicyDecision) -> tuple[int, int, int]:
    if decision.tier == "never_allowed":
        return (5, 0, 0)
    if not decision.allowed and not decision.approval_required:
        return (4, 0, 0)
    if decision.tier == "approval_required":
        return (3, 0, 0)
    if decision.approval_required:
        return (2, 0, 0)
    from core.bm_cli.cli_always import NEST_CWD_PREFIX

    prefix = (decision.matched_cwd_prefix or "").replace("\\", "/").strip()
    if not prefix:
        return (1, 0, 0)
    return (1, 1 if prefix == NEST_CWD_PREFIX else 2, len(prefix))
