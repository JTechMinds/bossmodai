"""BossMod AI — restart policy for the runtime worker: the crash-loop guard and the outage it ends in.

Pure: no I/O and no asyncio, so the policy is testable on its own. The
process gateway (``core/runtime/services.py``) owns the restarts themselves;
this module only answers "may it try again?" and words what the operator is
told when it may not (the Needs card and the Telegram card share the words).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Activity events the gateway announces. The Needs list re-reads on both
# (ui/static/js/needs/need-shape.js); the Telegram bridge sends a card on the first.
RUNTIME_DOWN_EVENT = "runtime_worker_down"
RUNTIME_RESTARTED_EVENT = "runtime_worker_restarted"

# The heading of the "restarts gave up" Need and of its Telegram card.
OUTAGE_TITLE = "The AI runtime has stopped"


@dataclass(frozen=True)
class WorkerOutage:
    """Automatic restarts of the runtime worker gave up; it stays stopped until someone starts it.

    Attributes:
        since: When the restarts gave up (timezone-aware UTC). Also the
            outage's identity: the Telegram card is sent once per value.
        reason: Why it stopped the last time, as a plain phrase that follows
            "the last time," (e.g. "it stopped responding", "it could not start").
        attempts: How many automatic restarts were tried in the restart
            window; 0 when none could be (a restart setting is unusable).
    """

    since: datetime
    reason: str
    attempts: int

    def describe(self) -> str:
        """The two plain sentences under the outage's title, on the Needs card and the Telegram card alike.

        Returns:
            What the outage means for the operator and what BossMod already
            tried. No exception text: the app log has that.
        """
        lead = "Your agents can't work until it runs again."
        if self.attempts == 0:
            return f"{lead} BossMod could not restart it on its own after {self.reason}."
        times = "1 time" if self.attempts == 1 else f"{self.attempts} times"
        return f"{lead} BossMod tried to restart it {times}; the last time, {self.reason}."


class RestartHistory:
    """The monotonic times of automatic restart attempts, for the sliding-window crash-loop guard.

    Only automatic attempts are recorded; an operator's Restart now clears
    the history instead (``RuntimeServices.restart_worker``).
    """

    def __init__(self) -> None:
        self._times: list[float] = []

    def record(self, now: float, *, window_seconds: float) -> int:
        """Record one attempt at ``now`` and forget the attempts older than the window.

        Args:
            now: The attempt's ``time.monotonic()`` reading.
            window_seconds: How far back attempts still count. An attempt
                exactly ``window_seconds`` old still counts.

        Returns:
            How many attempts, this one included, fall inside the window.
        """
        self._times.append(now)
        cutoff = now - window_seconds
        self._times = [moment for moment in self._times if moment >= cutoff]
        return len(self._times)

    def clear(self) -> None:
        """Forget every recorded attempt."""
        self._times.clear()


def restart_allowed(count: int, *, limit: int) -> bool:
    """Whether the attempt that brought the window's count to ``count`` may run.

    Args:
        count: Attempts inside the window, the one being decided included
            (:meth:`RestartHistory.record`'s return value).
        limit: Most automatic attempts allowed inside the window.

    Returns:
        ``True`` while ``count`` is at most ``limit``.
    """
    return count <= limit
