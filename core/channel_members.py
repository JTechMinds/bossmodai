"""Seat one live agent into, or take one out of, an existing shared thread.

Membership is fixed at create. These are the two writes that change it on a
live room without archiving or minting a replacement thread. Catch-up for a
seat is the existing transcript — no summary is generated.
"""

from __future__ import annotations

from core.models import Channel
import db
from db import channel_host as host_db


# A seat that has not started its turn. ``responding`` is claimed and excluded.
_UNSTARTED_SEAT_STATUSES = frozenset({"pending", "queued"})


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

    from core.floors import CROSS_FLOOR_DENY, FloorDenied, assert_on_channel

    try:
        assert_on_channel(agent.id, channel.id)
    except FloorDenied as exc:
        raise ThreadSeatError(str(exc) or CROSS_FLOOR_DENY, status_code=403) from exc

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


def remove_agent_from_thread(channel_id: str, agent_id: str) -> Channel:
    """Take one member out of an active thread. History is not rewritten.

    The runtime reads membership live, so the row delete alone would leave the
    agent's thread-scoped state behind. In one transaction this also prunes
    the agent from the host's pass/demotion/protection lists and deletes their
    queued triggers bound to this thread. Their ``pending``/``queued`` seats
    in the thread's active response rounds are closed as ``observed`` (and a
    round with nothing left open completes), so the round never wakes someone
    who is no longer a member; the next ``pending`` member still advances when
    the current turn finishes. A ``responding`` seat is a claimed in-flight
    turn and is the runtime's to finish, as on a floor move.

    Raises ``ThreadSeatError``: 404 when the thread is missing or the agent is
    not a member, 409 when the thread is archived or this is the last member
    (an empty room wakes nobody, and create requires at least one).
    """
    token = (channel_id or "").strip()
    agent_token = (agent_id or "").strip()
    if not token:
        raise ThreadSeatError("Thread not found", status_code=404)
    if not agent_token:
        raise ThreadSeatError("Select a member to remove", status_code=400)

    channel = db.get_channel(token)
    if channel is None:
        raise ThreadSeatError("Thread not found", status_code=404)
    if channel.status != "active":
        raise ThreadSeatError(
            "Thread is archived — reopen it before removing someone",
            status_code=409,
        )

    member_ids = {member.agent_id for member in db.list_channel_members(channel.id)}
    if agent_token not in member_ids:
        raise ThreadSeatError("That agent is not in this thread", status_code=404)
    if len(member_ids) == 1:
        raise ThreadSeatError(
            "A thread needs at least one agent — add someone else first",
            status_code=409,
        )

    with db.transaction():
        removed = db.remove_channel_member(channel.id, agent_token)
        if removed != 1:
            raise ThreadSeatError("That agent is not in this thread", status_code=404)
        state = host_db.get_channel_host_state(channel.id)
        state["pass_streaks"] = {
            key: count for key, count in state["pass_streaks"].items() if key != agent_token
        }
        for key in ("demoted_ids", "protected_ids"):
            state[key] = [item for item in state[key] if item != agent_token]
        host_db.save_channel_host_state(state)
        db.delete_queued_triggers_for_agent_channels(agent_token, {channel.id})
        for round_record in db.list_channel_response_rounds(channel.id, status="active"):
            seat = db.get_channel_response_candidate(round_id=round_record.id, agent_id=agent_token)
            if seat is not None and str(seat.status or "") in _UNSTARTED_SEAT_STATUSES:
                db.mark_channel_candidate_observed(round_id=round_record.id, agent_id=agent_token)
                db.maybe_complete_channel_response_round(round_record.id)

    remaining = db.get_channel(channel.id)
    if remaining is None:
        raise ThreadSeatError("Thread not found", status_code=404)
    return remaining
