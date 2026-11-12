"""BossMod AI — rewind a thread to an earlier message.

A rewind removes one thread message and everything after it, stops this
thread's live replies, and closes the thread's active response rounds. It
re-wakes nobody: to get fresh replies the operator sends again, which opens a
new round through the normal path. It edits messages, rounds, triggers, the
runtime and files together, which is why it is its own module rather than a
route body. It is not a branch of ``core.chat_rewind`` because the store, the
triggers and the round handling all differ from a DM's.
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
from core.models.channel import ChannelArchivedError
from db import attachments as db_att
from db import channel_idle_checks as idle_db
from db.connection import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadRewindResult:
    """What one thread rewind did, as the route reports it to the operator.

    Attributes:
        removed_message_ids: The thread messages deleted.
        removed_attachments: Attachment rows deleted with them.
        unremoved_files: Attachment files that exist but could not be
            deleted. Their rows are gone; the files are logged at ERROR.
        stopped_agent_ids: Agents whose live turn in this thread was cancelled.
        dropped_wakes: This thread's conversation wakes (queued, and the
            stopped turns' claims) that were removed and not queued again.
    """

    removed_message_ids: list[str]
    removed_attachments: int
    unremoved_files: int
    stopped_agent_ids: list[str]
    dropped_wakes: int


async def rewind_thread(channel_id: str, from_message_id: str, *, services: Any) -> ThreadRewindResult:
    """Delete a thread message and everything after it, stopping the thread's live replies.

    Steps, in this order:

    1. In one write transaction: check that the thread exists and is not
       archived and that the message belongs to it, take (delete) the
       thread's queued conversation wakes (``channel_message`` and
       ``channel_response``), and note which agents hold a claimed one,
       meaning a turn in this thread is live.
    2. For each of those agents: ``services.reset_agent_runtime`` cancels
       its active turn and awaits it. A failure queues every taken wake again
       and re-raises; nothing has been deleted yet. If an earlier agent was
       already cancelled, it keeps its claimed row, and the dispatcher's
       live-orphan sweep runs that wake again, so the thread is left as it
       was.
    3. In a second transaction: delete the message and every later row of
       the thread (rounds sourced from them go first), take the thread's
       remaining conversation wakes, queued and claimed, complete every
       active round of the thread, delete the attachment rows of the removed
       messages, and record the thread's new last line as idle-checked.
    4. Repaint the queue visibility of each agent that lost a wake and whose
       turn was not cancelled; the cancel already repaints the others.
    5. Remove the attachment files, and refresh the visible status of each
       agent whose turn was stopped.

    Nothing is queued again on success. Requeueing a stopped speaker's wake
    would find its candidate still ``responding``, and the reply would be
    dropped with the round left active, so the rounds are closed instead and
    the operator sends again to get fresh replies.

    Why the cut is safe: step 1 empties the thread's queue under the write
    lock before anything is cancelled, and a stopped turn's claimed row stays
    until step 3, which keeps the thread's one live speak taken, so no other
    member starts a reply in between. Step 3 runs after every cancel has been
    awaited, so a stopped turn can no longer write; its cut is recomputed, so
    rows written after step 1 are removed too, and wakes queued after step 1
    are taken with it. Like a DM rewind, a turn that starts between the
    snapshot and the cancel is a small accepted race. Task work, other
    threads, DMs and extension events are never cancelled. Tasks, memory,
    notifications, approvals and files the members wrote are untouched.

    Args:
        channel_id: The thread that is rewound.
        from_message_id: The first message to remove.
        services: The runtime services (``core.runtime.runtime_services``):
            ``reset_agent_runtime(agent_id)`` and ``enqueue_trigger(**kw)``.
            Injected, as in ``core.chat_rewind``, so tests need no worker.

    Returns:
        What was removed, stopped and dropped.

    Raises:
        LookupError: The thread does not exist, or the message does not
            exist or belongs to another conversation. Nothing has changed.
        ChannelArchivedError: The thread is archived. Nothing has changed.
        RuntimeError: A live turn could not be cancelled (timeout, or the
            worker died). The taken wakes were queued again and no message
            was deleted.
        Exception: Whatever the cut (step 3) raised, including a
            ``LookupError`` when the message vanished meanwhile. It rolled
            back, and the wakes taken in step 1 were queued again first.
    """
    with transaction():
        channel = db.get_channel(channel_id)
        if channel is None:
            raise LookupError(f"Thread {channel_id!r} does not exist")
        if channel.status == "archived":
            raise ChannelArchivedError(channel_id)
        cut = db.get_channel_message(from_message_id)
        if cut is None or cut.channel_id != channel_id:
            raise LookupError(f"Message {from_message_id!r} is not in thread {channel_id!r}")
        held = db.take_open_channel_wakes(channel_id, statuses=("queued",))
        stop_ids = db.list_agents_with_claimed_channel_wake(channel_id)

    try:
        for agent_id in stop_ids:
            await services.reset_agent_runtime(agent_id)
    except RuntimeError:
        logger.error(
            "Thread rewind for %s could not stop a live turn; queueing %d held thread wakes again",
            channel_id, len(held),
        )
        for trigger in held:
            await _enqueue_again(trigger, services=services)
        raise

    try:
        with transaction():
            removed = db.delete_channel_messages_from(channel_id, from_message_id)
            taken = db.take_open_channel_wakes(channel_id, statuses=("queued", "claimed"))
            db.close_active_channel_rounds(channel_id)
            attachments = db_att.delete_attachments_for_messages(removed)
            # A rewind is an undo, not a prompt: the idle check must not judge
            # the thread's new last line as fresh and wake anyone about it.
            state = idle_db.get_channel_idle_check(channel_id)
            latest = db.get_latest_channel_message(channel_id)
            state["checked_message_id"] = latest.id if latest else ""
            idle_db.save_channel_idle_check(state)
    except Exception:
        # The cut rolled back, which restores the wakes it took, but step 1's
        # taken wakes are already gone: put them back before reporting.
        logger.error(
            "Thread rewind for %s failed at the cut; queueing %d held thread wakes again",
            channel_id, len(held),
        )
        for trigger in held:
            await _enqueue_again(trigger, services=services)
        raise

    dropped = held + taken
    # reset_agent repaints the stopped agents; nothing else repaints the
    # "Queued" line of an agent that only lost a waiting wake.
    for agent_id in sorted({trigger.agent_id for trigger in dropped} - set(stop_ids)):
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
    for agent_id in stop_ids:
        activity_runtime.refresh_agent_status(agent_id)

    logger.info(
        "Thread %s rewound from %s: %d messages, %d attachments (%d files left), "
        "%d turns stopped, %d wakes dropped",
        channel_id, from_message_id, len(removed), len(attachments), unremoved_files,
        len(stop_ids), len(dropped),
    )
    return ThreadRewindResult(
        removed_message_ids=removed,
        removed_attachments=len(attachments),
        unremoved_files=unremoved_files,
        stopped_agent_ids=stop_ids,
        dropped_wakes=len(dropped),
    )


async def _enqueue_again(trigger: AgentTrigger, *, services: Any) -> None:
    """Queue one taken thread wake again exactly as it was.

    Its own ``trigger_type``, ``source_channel``, ``task_id`` and payload, so
    the normal path (queue visibility, the dispatcher wake) runs and the
    wake still answers the round it was bound to.
    """
    await services.enqueue_trigger(
        agent_id=trigger.agent_id,
        trigger_type=trigger.trigger_type,
        source_channel=trigger.source_channel,
        payload=json.loads(trigger.payload) if trigger.payload else {},
        task_id=trigger.task_id,
    )
