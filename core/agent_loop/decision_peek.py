"""Decision-turn CLI peek budget: total-peek cap, fingerprints, identical-loop stop.

The soft cap is 10 CLI peeks on the decision path. Fingerprints collapse
path tweaks and detect a triple repeat; they do not recycle quota.
``request_host_access`` is not a peek. Deep host review is accept → execution.

The first refusal in a turn STEERS: the command does not run, the model is
told why and gets to reply (or accept the work). Any later refusal in the
same turn STOPS the turn, as every refusal used to.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.agent_loop.liveness import command_fingerprint

SOFT_PEEK_BUDGET = 10
IDENTICAL_PEEK_STREAK_LIMIT = 3

SOFT_PEEK_STEER = "peek budget exhausted — decide or accept work"
IDENTICAL_PEEK_STEER = "looping — decide or accept work"


@dataclass(frozen=True, slots=True)
class PeekBudgetVerdict:
    """Whether one more CLI peek may run on the decision path."""

    allowed: bool
    reason: str | None = None
    steer: str = ""
    # For a refusal: False the first time in a turn (steer the model), True
    # on any later refusal (stop the turn).
    stop: bool = False


@dataclass
class DecisionPeekBudget:
    """Track total CLI peeks and identical-in-a-row streaks.

    Fingerprints collapse path tweaks and detect a triple repeat. They do
    not recycle quota: every allowed ``bm_cli`` peek spends one of the 10.
    """

    peek_count: int = 0
    last_fingerprint: str | None = None
    identical_streak: int = 0
    # Whether this turn already had its one steer.
    steered: bool = False

    def consider(self, command: str, content: str | None = None) -> PeekBudgetVerdict:
        """Record one CLI peek and return whether it may execute.

        A refusal's ``stop`` is False the first time (the caller steers the
        model and continues) and True afterwards (the caller ends the turn).
        """
        fingerprint = command_fingerprint(command, content)
        next_streak = self.identical_streak + 1 if fingerprint == self.last_fingerprint else 1

        if next_streak >= IDENTICAL_PEEK_STREAK_LIMIT:
            return self._refuse("identical_loop", IDENTICAL_PEEK_STEER)
        if self.peek_count >= SOFT_PEEK_BUDGET:
            return self._refuse("soft_budget", SOFT_PEEK_STEER)

        self.last_fingerprint = fingerprint
        self.identical_streak = next_streak
        self.peek_count += 1
        return PeekBudgetVerdict(allowed=True)

    def _refuse(self, reason: str, steer: str) -> PeekBudgetVerdict:
        stop = self.steered
        self.steered = True
        return PeekBudgetVerdict(allowed=False, reason=reason, steer=steer, stop=stop)
