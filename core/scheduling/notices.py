"""BossMod AI — the operator's note when an agent changes one of its schedules.

An agent can change standing work, so the operator must never have to
discover that later: every agent ``schedules add/edit/on/off/remove`` posts
one system line in the operator's DM with that agent. Kept apart from
core/scheduling/service.py, which holds the rules and stays free of the
chat layer.
"""

from __future__ import annotations

from typing import Any, Literal

from core.agent_loop.notifications import persist_origin_system_note
from core.models import Agent
from core.models.schedule import AgentSchedule
from core.scheduling.recurrence import describe

AgentScheduleVerb = Literal["scheduled", "changed", "switched on", "switched off", "removed"]


def agent_change_line(agent: Agent, verb: AgentScheduleVerb, schedule: AgentSchedule) -> str:
    """The note's sentence, e.g. ``Brian scheduled "Check GitHub": Every weekday at 9:00 AM``."""
    return f'{agent.name} {verb} "{schedule.title}": {describe(schedule.recurrence, clock="12h")}'


def note_agent_change(agent: Agent, verb: AgentScheduleVerb, schedule: AgentSchedule) -> dict[str, Any]:
    """Persist the operator's DM note for one agent change to a schedule.

    Persisted through ``persist_origin_system_note`` (no thread: the agent's
    DM with the operator), which dedupes an identical recent line. It is not
    broadcast here: the agent's CLI runs off the event loop, so the caller
    hands the persisted line back on its result (``data["origin_chrome"]``)
    and the turn broadcasts it with the other origin lines.

    Args:
        agent: The agent that made the change.
        verb: What it did.
        schedule: The schedule as stored after the change (as it was, for ``removed``).

    Returns:
        ``{"chat_message": {...}}`` as persisted, or ``{}`` when an identical
        line was already the latest.
    """
    return persist_origin_system_note(agent, agent_change_line(agent, verb, schedule))
