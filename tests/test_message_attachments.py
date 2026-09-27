"""BossMod AI — Message attachment linkage tests.

Linking happens in ``core.messaging`` in the same transaction as the message
insert. A refused link must leave no message row, no broadcast and no wake.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import db
from core import config
from core.messaging import route_human_channel_message, route_human_dm
from db import attachments as db_att
from db.attachments import AttachmentLinkError


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


class _Recorder:
    """Stands in for the WebSocket manager and runtime services."""

    def __init__(self) -> None:
        self.broadcasts: list[dict[str, Any]] = []
        self.triggers: list[dict[str, Any]] = []

    async def broadcast_chat_message(self, **kwargs: Any) -> None:
        self.broadcasts.append(kwargs)

    async def broadcast_channel_message(self, **kwargs: Any) -> None:
        self.broadcasts.append(kwargs)

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.triggers.append(kwargs)


def _agent(name: str, x: int):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1)


def _pending(context_type: str, context_id: str, name: str = "f.txt") -> str:
    return db_att.create_attachment(
        message_id="pending",
        file_name=name,
        file_size=100,
        mime_type="text/plain",
        storage_path="/tmp/dummy",
        preview_tier="text",
        context_type=context_type,
        context_id=context_id,
    ).id


async def test_dm_links_attachments_and_carries_ids_on_the_trigger() -> None:
    ada = _agent("Ada", 1)
    a1 = _pending("direct", ada.id, "a.txt")
    a2 = _pending("direct", ada.id, "b.txt")
    rec = _Recorder()

    result = await route_human_dm(
        agent_id=ada.id, content="look", from_name="You",
        broadcast_manager=rec, services=rec, attachment_ids=[a1, a2],
    )

    linked = db_att.get_attachments_for_message(result["message_id"])
    assert {a.id for a in linked} == {a1, a2}
    assert [a["id"] for a in rec.broadcasts[0]["attachments"]] == [a1, a2]
    assert rec.triggers[0]["payload"]["attachment_ids"] == [a1, a2]


async def test_dm_without_attachments_keeps_the_old_payload() -> None:
    ada = _agent("Ada", 1)
    rec = _Recorder()
    await route_human_dm(
        agent_id=ada.id, content="plain text", from_name="You",
        broadcast_manager=rec, services=rec,
    )
    assert rec.broadcasts[0]["attachments"] is None
    assert "attachment_ids" not in rec.triggers[0]["payload"]


async def test_dm_with_unknown_attachment_writes_nothing() -> None:
    ada = _agent("Ada", 1)
    good = _pending("direct", ada.id)
    rec = _Recorder()

    with pytest.raises(AttachmentLinkError) as excinfo:
        await route_human_dm(
            agent_id=ada.id, content="hi", from_name="You",
            broadcast_manager=rec, services=rec, attachment_ids=[good, "missing-id"],
        )

    assert excinfo.value.missing_ids == ["missing-id"]
    assert db.get_human_chat_thread(ada.id) == []
    assert db_att.get_attachment_by_id(good).message_id == "pending"
    assert rec.broadcasts == [] and rec.triggers == []


async def test_dm_refuses_an_attachment_uploaded_for_another_agent() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    bobs = _pending("direct", bob.id)
    rec = _Recorder()

    with pytest.raises(AttachmentLinkError):
        await route_human_dm(
            agent_id=ada.id, content="hi", from_name="You",
            broadcast_manager=rec, services=rec, attachment_ids=[bobs],
        )
    assert db.get_human_chat_thread(ada.id) == []
    assert db_att.get_attachment_by_id(bobs).message_id == "pending"


async def test_dm_over_the_per_message_cap_writes_nothing() -> None:
    ada = _agent("Ada", 1)
    cap = config.require_int("bossmod.attach.max_per_message")
    ids = [_pending("direct", ada.id, f"f{i}.txt") for i in range(cap + 1)]
    rec = _Recorder()

    with pytest.raises(AttachmentLinkError):
        await route_human_dm(
            agent_id=ada.id, content="many", from_name="You",
            broadcast_manager=rec, services=rec, attachment_ids=ids,
        )
    assert db.get_human_chat_thread(ada.id) == []
    assert all(db_att.get_attachment_by_id(i).message_id == "pending" for i in ids)
    assert rec.triggers == []


async def test_thread_attachment_only_post_links_and_wakes_with_ids() -> None:
    ada = _agent("Ada", 1)
    channel = db.create_channel(name="Room", member_agent_ids=[ada.id])
    att = _pending("thread", channel.id, "img.png")
    rec = _Recorder()

    result = await route_human_channel_message(
        channel_id=channel.id, channel_name=channel.name, content="",
        from_name="Human Operator", broadcast_manager=rec, services=rec,
        attachment_ids=[att],
    )

    assert [a.id for a in db_att.get_attachments_for_message(result["message_id"])] == [att]
    assert rec.triggers, "the thread's member is woken"
    assert all(t["payload"]["attachment_ids"] == [att] for t in rec.triggers)


async def test_thread_refused_link_leaves_no_message() -> None:
    ada = _agent("Ada", 1)
    channel = db.create_channel(name="Room", member_agent_ids=[ada.id])
    dm_upload = _pending("direct", ada.id)
    rec = _Recorder()

    with pytest.raises(AttachmentLinkError):
        await route_human_channel_message(
            channel_id=channel.id, channel_name=channel.name, content="hi",
            from_name="Human Operator", broadcast_manager=rec, services=rec,
            attachment_ids=[dm_upload],
        )
    assert db.list_channel_messages(channel.id) == []
    assert rec.broadcasts == [] and rec.triggers == []
