"""BossMod AI — claimed trigger rows and the turns they run.

One home for two things the dispatcher and the floor modules both need and
none of them owns: the shape of the trigger dict a turn runs on, built from
its claimed row (``turn_trigger``), and the routine that cancels an agent's
live turn for good and discards its claim (``cancel_turn_and_discard_claim``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import db
from core.agent_loop.channel_rounds import release_channel_seat
from core.models import AgentTrigger

logger = logging.getLogger(__name__)


def turn_trigger(row: Any) -> dict[str, Any]:
    """Return the trigger dict a turn runs on, built from a claimed trigger row.

    The row's JSON payload with the row's identity and claim laid over it.
    Shared by the normal launch and start-up recovery, so a recovered
    orphan is shaped exactly like the trigger its turn ran on.

    Args:
        row: A claimed ``AgentTrigger``.

    Returns:
        The payload plus ``type``, ``trigger_id``, ``task_id``,
        ``source_channel`` and ``claim_generation``.

    Raises:
        json.JSONDecodeError: The stored payload is not valid JSON.
    """
    payload = json.loads(row.payload) if row.payload else {}
    payload.update({
        "type": row.trigger_type,
        "trigger_id": row.id,
        "task_id": row.task_id,
        "source_channel": row.source_channel,
        "claim_generation": row.claim_generation,
    })
    return payload


async def cancel_turn_and_discard_claim(agent_id: str, *, services: Any) -> int:
    """Cancel an agent's live turn for good and delete the claim it ran on.

    ``services.reset_agent_runtime`` cancels the turn but deliberately leaves
    its trigger row ``claimed`` (a chat rewind needs that row afterwards).
    A caller that is ending the turn for good (a floor delete's send-home, a
    floor move) uses this instead, so the claim is neither stranded nor
    re-run by the dispatcher's live-orphan sweep, and a channel turn's round
    seat is closed so the round moves on.

    Why snapshot first: the cancel wakes the dispatcher, which may claim a
    new trigger for this agent the moment the cancel finishes. Deleting
    "every claimed row of this agent" afterwards could delete that fresh,
    live claim. So the claimed rows are listed *before* the cancel, and
    afterwards exactly those are deleted by ``(id, claim_generation)``. A
    row reclaimed under a new generation, or a different row, is never
    touched.

    Steps:
        1. Snapshot the agent's claimed rows.
        2. ``await services.reset_agent_runtime(agent_id)``.
        3. Delete each snapshotted row, generation-guarded. For each one
           deleted, close its channel round seat and collect the wakes the
           round produces (the next member, or a follow-up round).
        4. Queue those wakes through ``services.enqueue_trigger``, so the
           dispatcher is woken and tests need no worker.

    Args:
        agent_id: The agent whose live turn ends.
        services: The runtime services (``core.runtime.runtime_services``):
            ``reset_agent_runtime(agent_id)`` and ``enqueue_trigger(**kw)``.
            Injected, as in ``core.chat_rewind``.

    Returns:
        The number of claims discarded.

    Raises:
        Exception: Whatever ``services.reset_agent_runtime`` raises (for
            example ``RuntimeError`` when the cancel times out), unchanged;
            no row has been deleted then. DB errors from the delete, the
            seat release or the enqueue also propagate.
    """
    # One agent holds at most one turn, so the default listing limit is ample.
    claimed = [
        AgentTrigger.model_validate(row)
        for row in db.list_agent_triggers(agent_id, status="claimed")
    ]
    await services.reset_agent_runtime(agent_id)

    discarded = 0
    wakes: list[dict[str, Any]] = []
    for row in claimed:
        taken = db.take_claimed_trigger(row.id, row.claim_generation)
        if taken is None:
            continue
        discarded += 1
        try:
            trigger = turn_trigger(taken)
        except json.JSONDecodeError:
            logger.error(
                "Discarded claimed trigger %s for agent %s, but its payload is not valid JSON; "
                "its channel round seat (if any) was not closed",
                taken.id,
                agent_id,
            )
            continue
        wakes.extend(release_channel_seat(agent_id, trigger))

    for spec in wakes:
        await services.enqueue_trigger(
            agent_id=spec["agent_id"],
            trigger_type=spec["trigger_type"],
            source_channel=spec["source_channel"],
            payload=spec["payload"],
            task_id=spec.get("task_id"),
        )
    return discarded
