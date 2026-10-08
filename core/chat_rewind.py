"""BossMod AI — rewind an agent DM to an earlier message.

A rewind removes one DM message and everything after it, stops the agent's
reply in that DM if one is running, and puts back any operator message that
still stands but lost its wake. It edits messages, triggers, the runtime and
files together, which is why it is its own module rather than a route body.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import db
from core.agent_loop import activity_runtime
from core.agent_loop.queue_visibility import emit_queue_visibility
from core.attachments import remove_attachment_file
from core.models import AgentTrigger
from core.models.message import HUMAN_SENDER_ID
from db import attachments as db_att
from db.connection import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatRewindResult:
    """What one rewind did, as the route reports it to the operator.

    Attributes:
        removed_message_ids: The DM messages deleted.
        removed_attachments: Attachment rows deleted with them.
        unremoved_files: Attachment files that exist but could not be
            deleted. Their rows are gone; the files are logged at ERROR.
        stopped_turn: A ``human_chat`` turn was claimed, and was cancelled.
        requeued: Operator messages that still stand and were queued again.
    """

    removed_message_ids: list[str]
    removed_attachments: int
    unremoved_files: int
    stopped_turn: bool
    requeued: int


async def rewind_human_chat(agent_id: str, from_message_id: str, *, services: Any) -> ChatRewindResult:
    """Delete a DM message and everything after it, stopping a live DM reply.

    Steps, in this order:

    1. In one write transaction: check that the message belongs to this DM,
       take (delete) the agent's queued ``human_chat`` triggers, and note
       whether a ``human_chat`` trigger is claimed, meaning a DM turn is live.
    2. Only if one is live: ``services.reset_agent_runtime`` cancels the
       agent's active turn and awaits it. A failure queues every taken
       trigger again and re-raises; nothing has been deleted yet.
    3. In a second transaction: delete the message and every later DM row,
       take the now-orphaned claimed ``human_chat`` row, and delete the
       attachment rows of the removed messages.
    4. Queue again each taken trigger whose source message was kept, with its
       original payload. When wakes were dropped and no turn was stopped,
       repaint the agent's queue visibility, which nothing else would.
    5. Remove the attachment files, and refresh the agent's visible status
       when a turn was stopped.

    Why this is race-free: step 1 empties the chat queue under the write lock
    before anything is cancelled, so when the cancel wakes the dispatcher it
    finds no ``human_chat`` trigger to claim, and so no turn can start on a
    message about to be removed. Step 3 runs after the cancel has been
    awaited, so the stopped turn can no longer write; its cut is recomputed,
    so rows that turn wrote after step 1 are removed too. Non-chat turns
    (task work, threads, extension events) are never cancelled and never read
    this DM's history. Notifications, tasks, memory and files the agent
    wrote are untouched.

    Args:
        agent_id: The agent whose DM with the operator is rewound.
        from_message_id: The first message to remove.
        services: The runtime services (``core.runtime.runtime_services``):
            ``reset_agent_runtime(agent_id)`` and ``enqueue_trigger(**kw)``.
            Injected, as in ``core.floor_moves``, so tests need no worker.

    Returns:
        What was removed, stopped and queued again.

    Raises:
        LookupError: The message does not exist or belongs to another
            conversation. Nothing has changed.
        RuntimeError: The live turn could not be cancelled (timeout, or the
            worker died). The taken triggers were queued again and no message
            was deleted.
        Exception: Whatever the cut (step 3) raised, including a
            ``LookupError`` when the message vanished meanwhile. It rolled
            back, and the triggers taken in step 1 were queued again first.
    """
    with transaction():
        cut = db.get_message(from_message_id)
        if cut is None or {cut.from_agent, cut.to_agent} != {agent_id, HUMAN_SENDER_ID}:
            raise LookupError(
                f"Message {from_message_id!r} is not in the DM between {agent_id!r} and the operator"
            )
        held = db.take_open_human_chat_triggers(agent_id, statuses=("queued",))
        stopped_turn = any(
            row["trigger_type"] == "human_chat"
            for row in db.list_agent_triggers(agent_id, status="claimed")
        )

    if stopped_turn:
        try:
            await services.reset_agent_runtime(agent_id)
        except RuntimeError:
            logger.error(
                "Chat rewind for %s could not stop the live turn; queueing %d held chat wakes again",
                agent_id, len(held),
            )
            for trigger in held:
                await _enqueue_again(trigger, services=services)
            raise

    try:
        with transaction():
            removed = db.delete_human_chat_from(agent_id, from_message_id)
            taken_claimed = db.take_open_human_chat_triggers(agent_id, statuses=("claimed",))
            attachments = db_att.delete_attachments_for_messages(removed)
        # Joined only once committed: a rollback restores the claimed row, and
        # re-queueing it too would wake the agent twice for one message.
        held += taken_claimed
    except Exception:
        # The cut rolled back (the row vanished under a concurrent clear, or
        # the database refused), but step 1's taken wakes are already gone:
        # put the operator's messages back before reporting the failure.
        logger.error(
            "Chat rewind for %s failed at the cut; queueing %d held chat wakes again",
            agent_id, len(held),
        )
        for trigger in held:
            await _enqueue_again(trigger, services=services)
        raise

    removed_set = set(removed)
    requeued = 0
    for trigger in held:
        source_id = _source_message_id(trigger)
        if source_id is None or source_id in removed_set:
            continue
        await _enqueue_again(trigger, services=services)
        requeued += 1
    if len(held) > requeued and not stopped_turn:
        # Dropped wakes with no cancel: nothing else repaints the "Queued"
        # note (reset_agent emits it on a cancel, enqueue_trigger on a requeue).
        await emit_queue_visibility(agent_id)

    unremoved_files = 0
    for attachment in attachments:
        try:
            remove_attachment_file(attachment)
        except OSError:
            # The rows are committed and the rest of the files still need
            # removing; remove_attachment_file logged this one at ERROR, and
            # the count goes back to the operator.
            unremoved_files += 1
    if stopped_turn:
        activity_runtime.refresh_agent_status(agent_id)

    logger.info(
        "Chat rewound for %s from %s: %d messages, %d attachments (%d files left), "
        "turn stopped=%s, %d requeued",
        agent_id, from_message_id, len(removed), len(attachments), unremoved_files,
        stopped_turn, requeued,
    )
    return ChatRewindResult(
        removed_message_ids=removed,
        removed_attachments=len(attachments),
        unremoved_files=unremoved_files,
        stopped_turn=stopped_turn,
        requeued=requeued,
    )


def _source_message_id(trigger: AgentTrigger) -> str | None:
    """The DM message a ``human_chat`` trigger answers, or None if it has none.

    ``core.messaging.route_human_dm`` always writes one, so a missing id is a
    contract violation: it is logged at ERROR and the trigger is dropped.
    """
    payload = json.loads(trigger.payload) if trigger.payload else {}
    source_id = payload.get("source_message_id") if isinstance(payload, dict) else None
    if not source_id:
        logger.error(
            "human_chat trigger %s for %s has no source_message_id; dropped by the chat rewind",
            trigger.id, trigger.agent_id,
        )
        return None
    return str(source_id)


async def _enqueue_again(trigger: AgentTrigger, *, services: Any) -> None:
    """Queue one taken ``human_chat`` trigger again with its original payload.

    The same call shape ``core.messaging.route_human_dm`` uses, so the normal
    path (queue visibility, the dispatcher wake) runs.
    """
    await services.enqueue_trigger(
        agent_id=trigger.agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload=json.loads(trigger.payload) if trigger.payload else {},
    )
