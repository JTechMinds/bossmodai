"""Test helpers for agents leaving service (core/stand_down.py).

Vacation is an async stand-down now (``core.floors.send_home``), and it calls
three runtime services. Tests that only need "this agent is on vacation" send
it home the one way the product does, through these.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.stand_down import StandDownResult


class StandDownServices:
    """The runtime services a stand-down calls, recorded instead of run.

    Attributes:
        resets: Each ``reset_agent_runtime`` agent id, in call order.
        wakes: Each ``enqueue_trigger`` call's keyword arguments. A real
            ``RuntimeServices`` refuses these for an agent whose gate is
            closed; tests assert on them instead.
        released: Each ``extensions_release_agents`` call's agent ids.
    """

    def __init__(self) -> None:
        self.resets: list[str] = []
        self.wakes: list[dict[str, Any]] = []
        self.released: list[list[str]] = []

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.wakes.append(kwargs)

    def extensions_release_agents(self, agent_ids: list[str]) -> None:
        self.released.append(list(agent_ids))


async def send_on_vacation(*agent_ids: str) -> StandDownResult:
    """Send agents home as a floor delete's "Send them home" does, with recorded services."""
    # Local: core.floors must not be the first import of a test module (it
    # reaches db, which reaches back into core.floors while it loads).
    from core.floors import FLOOR_DELETED, send_home

    return await send_home(list(agent_ids), why=FLOOR_DELETED, extra_task_ids=[], services=StandDownServices())
