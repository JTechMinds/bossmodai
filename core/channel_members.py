"""Seat one live agent into an existing shared thread.

Membership is fixed at create today. This is the one write that adds a
hired agent to a live room without archiving or minting a replacement
thread. Catch-up is the existing transcript — no summary is generated.
"""

from __future__ import annotations

from core.models import Channel
import db


class ThreadSeatError(ValueError):
    """Fail-closed seat outcome the API maps to an operator-visible status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def seat_agent_in_thread(channel_id: str, agent_id: str) -> Channel:
    """Add one hired agent to an active thread. History is not rewritten.

    Raises ``ThreadSeatError`` when the thread is missing or inactive, the
    agent is missing, or they are already a member. Those are operator
    errors, never a silent no-op.
    """
    token = (channel_id or "").strip()
    agent_token = (agent_id or "").strip()
    if not token:
        raise ThreadSeatError("Thread not found", status_code=404)
    if not agent_token:
        raise ThreadSeatError("Select a live agent", status_code=400)

    channel = db.get_channel(token)
    if channel is None:
        raise ThreadSeatError("Thread not found", status_code=404)
    if channel.status != "active":
        raise ThreadSeatError(
            "Thread is archived — reopen it before adding someone",
            status_code=409,
        )

    agent = db.get_agent(agent_token)
    if agent is None:
        raise ThreadSeatError("Agent not found", status_code=404)

    already = {member.agent_id for member in db.list_channel_members(channel.id)}
    if agent.id in already:
        raise ThreadSeatError(
            f"{agent.name} is already a member of this thread",
            status_code=409,
        )

    added = db.add_channel_members(channel.id, [agent.id])
    if added != 1:
        raise ThreadSeatError(
            f"{agent.name} is already a member of this thread",
            status_code=409,
        )

    seated = db.get_channel(channel.id)
    if seated is None:
        raise ThreadSeatError("Thread not found", status_code=404)
    return seated
