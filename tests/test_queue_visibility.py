"""In-thread queue visibility: increment / decrement / clear.

One agent, one turn queue. The Busy line updates only when waiting depth
changes. Idle or an empty wait list clears it. No parallel shells.
"""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from db.crud import execute
from core.agent_loop.next_owner import is_system_one_liner
from core.agent_loop.queue_visibility import (
    QUEUE_VISIBILITY_KIND,
    agent_is_busy,
    format_busy_queued_line,
    resolve_visibility_target,
    sync_queue_visibility,
    waiting_queue_depth,
)
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _agent(name: str = "Ada"):
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _enqueue(agent_id: str, *, content: str = "follow up", channel_id: str | None = None, task_id: str | None = None):
    payload: dict = {"content": content, "from_name": "Human"}
    if channel_id:
        payload["channel_id"] = channel_id
    return db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="channel_message" if channel_id else "human_chat",
        source_channel="channel" if channel_id else "chat",
        payload=payload,
        task_id=task_id,
    )


def _busy_chat_lines(agent_id: str) -> list[str]:
    return [
        item.content
        for item in db.list_notifications(agent_id=agent_id, chat_visible=True)
        if item.kind == QUEUE_VISIBILITY_KIND
    ]


def test_locked_busy_copy() -> None:
    assert format_busy_queued_line(1) == "Busy — 1 queued"
    assert format_busy_queued_line(3) == "Busy — 3 queued"
    assert is_system_one_liner("Busy — 2 queued")
    assert not is_system_one_liner("I am busy with the report.")


def test_idle_with_queued_work_stays_quiet() -> None:
    agent = _agent()
    _enqueue(agent.id)
    assert waiting_queue_depth(agent.id) == 1
    assert agent_is_busy(agent.id) is False
    change = sync_queue_visibility(agent.id)
    assert change["action"] == "noop"
    assert _busy_chat_lines(agent.id) == []


def test_increment_decrement_clear_on_focus() -> None:
    agent = _agent()
    current = _enqueue(agent.id, content="first")
    claimed = db.claim_trigger(current.id)
    assert claimed is not None
    second = _enqueue(agent.id, content="second")
    assert waiting_queue_depth(agent.id) == 1
    assert agent_is_busy(agent.id) is True

    first = sync_queue_visibility(agent.id)
    assert first["action"] == "upsert"
    assert first["target"] == "chat"
    assert first["content"] == "Busy — 1 queued"
    assert _busy_chat_lines(agent.id) == ["Busy — 1 queued"]
    first_id = first["message_id"]

    third = _enqueue(agent.id, content="third")
    incremented = sync_queue_visibility(agent.id)
    assert incremented["action"] == "upsert"
    assert incremented["content"] == "Busy — 2 queued"
    assert incremented["message_id"] == first_id
    assert _busy_chat_lines(agent.id) == ["Busy — 2 queued"]

    execute("DELETE FROM agent_triggers WHERE id = $1 AND status = 'queued'", [third.id])
    decremented = sync_queue_visibility(agent.id)
    assert decremented["action"] == "upsert"
    assert decremented["content"] == "Busy — 1 queued"
    assert decremented["message_id"] == first_id
    assert _busy_chat_lines(agent.id) == ["Busy — 1 queued"]
    assert second.id

    db.delete_queued_triggers(agent.id, trigger_types=["human_chat"])
    cleared = sync_queue_visibility(agent.id)
    assert cleared["action"] == "clear"
    assert _busy_chat_lines(agent.id) == []


def test_clear_when_agent_goes_idle() -> None:
    agent = _agent()
    current = _enqueue(agent.id, content="first")
    claimed = db.claim_trigger(current.id)
    assert claimed is not None
    _enqueue(agent.id, content="second")
    sync_queue_visibility(agent.id)
    assert _busy_chat_lines(agent.id) == ["Busy — 1 queued"]

    db.complete_agent_trigger(current.id, claim_generation=claimed.claim_generation)
    db.delete_queued_triggers(agent.id)
    db.update_agent_state(agent.id, status="idle")
    cleared = sync_queue_visibility(agent.id)
    assert cleared["action"] == "clear"
    assert agent_is_busy(agent.id) is False
    assert _busy_chat_lines(agent.id) == []


def test_same_depth_does_not_repost() -> None:
    agent = _agent()
    current = _enqueue(agent.id, content="first")
    db.claim_trigger(current.id)
    _enqueue(agent.id, content="second")
    first = sync_queue_visibility(agent.id)
    again = sync_queue_visibility(agent.id)
    assert again["action"] == "noop"
    assert again["message_id"] == first["message_id"]
    assert _busy_chat_lines(agent.id) == ["Busy — 1 queued"]


def test_channel_origin_gets_the_busy_line() -> None:
    agent = _agent("Jimothy")
    channel = db.create_channel(name="Review", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    current = _enqueue(agent.id, content="first", channel_id=channel.id)
    db.claim_trigger(current.id)
    _enqueue(agent.id, content="second", channel_id=channel.id)

    assert resolve_visibility_target(agent.id) == ("channel", channel.id)
    posted = sync_queue_visibility(agent.id)
    assert posted["action"] == "upsert"
    assert posted["target"] == "channel"
    assert posted["channel_id"] == channel.id
    assert posted["content"] == "Busy — 1 queued"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Busy — 1 queued") == 1
    assert _busy_chat_lines(agent.id) == []

    _enqueue(agent.id, content="third", channel_id=channel.id)
    updated = sync_queue_visibility(agent.id)
    assert updated["content"] == "Busy — 2 queued"
    assert updated["message_id"] == posted["message_id"]
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents.count("Busy — 2 queued") == 1
    assert "Busy — 1 queued" not in contents

    db.delete_queued_triggers(agent.id)
    cleared = sync_queue_visibility(agent.id)
    assert cleared["action"] == "clear"
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert "Busy — 2 queued" not in contents
    assert "Busy — 1 queued" not in contents


def test_channel_task_origin_wins_over_focus_queue() -> None:
    agent = _agent("Bea")
    channel = db.create_channel(name="Ship", member_agent_ids=[agent.id], created_by=HUMAN_SENDER_ID)
    creation = create_or_bind_task(
        title="Share review findings",
        description="Post the review.",
        project=None,
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )
    assert creation.task is not None
    current = _enqueue(agent.id, content="working", task_id=creation.task.id)
    db.claim_trigger(current.id)
    _enqueue(agent.id, content="later dm")

    posted = sync_queue_visibility(agent.id)
    assert posted["target"] == "channel"
    assert posted["channel_id"] == channel.id
    assert posted["content"] == "Busy — 1 queued"


def test_one_agent_one_queue_no_parallel_slots() -> None:
    """Visibility never invents a second in-flight turn."""
    from core.agent_loop.dispatcher import TurnDispatcher

    dispatcher = TurnDispatcher()
    agent = _agent()
    first = _enqueue(agent.id, content="a")
    second = _enqueue(agent.id, content="b")
    db.claim_trigger(first.id)
    assert first.id != second.id
    assert dispatcher.is_active(agent.id) is False
    sync_queue_visibility(agent.id)
    assert waiting_queue_depth(agent.id) == 1
    assert len(db.list_agent_triggers(agent.id, status="claimed")) == 1
    assert _busy_chat_lines(agent.id) == ["Busy — 1 queued"]
