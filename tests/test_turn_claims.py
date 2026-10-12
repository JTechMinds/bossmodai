"""Cancel a live turn for good and discard its claim (core/agent_loop/turn_claims.py)."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import db
from core import config
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.agent_loop.turn_claims import cancel_turn_and_discard_claim


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


class _Services:
    """Records resets and wakes. ``during_reset`` plays the dispatcher reacting to the cancel."""

    def __init__(self, during_reset: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.resets: list[str] = []
        self.enqueued: list[dict[str, Any]] = []
        self._during_reset = during_reset

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)
        if self._during_reset is not None:
            await self._during_reset(agent_id)

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.enqueued.append(kwargs)


def _agent(name: str, x: int):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1)


def _claimed_chat(agent_id: str):
    row = db.create_agent_trigger(
        agent_id=agent_id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    return claimed


async def test_the_snapshotted_claim_is_deleted_after_the_cancel() -> None:
    ada = _agent("Ada", 1)
    claimed = _claimed_chat(ada.id)
    seen_at_reset: list[bool] = []

    async def _check_row_still_there(_agent_id: str) -> None:
        seen_at_reset.append(db.get_agent_trigger(claimed.id) is not None)

    services = _Services(_check_row_still_there)

    discarded = await cancel_turn_and_discard_claim(ada.id, services=services)

    assert discarded == 1
    assert services.resets == [ada.id]
    # The cancel was awaited before the delete: the row still existed then.
    assert seen_at_reset == [True]
    assert db.get_agent_trigger(claimed.id) is None
    assert services.enqueued == []


async def test_a_row_reclaimed_under_a_new_generation_during_the_cancel_survives() -> None:
    ada = _agent("Ada", 1)
    claimed = _claimed_chat(ada.id)
    reclaimed: list[Any] = []

    async def _requeue_and_reclaim(_agent_id: str) -> None:
        db.requeue_all_claimed_triggers()
        again = db.claim_trigger(claimed.id)
        assert again is not None
        reclaimed.append(again)

    discarded = await cancel_turn_and_discard_claim(ada.id, services=_Services(_requeue_and_reclaim))

    assert discarded == 0
    survivor = db.get_agent_trigger(claimed.id)
    assert survivor is not None
    assert (survivor.status, survivor.claim_generation) == ("claimed", reclaimed[0].claim_generation)
    assert survivor.claim_generation != claimed.claim_generation


async def test_a_row_claimed_fresh_during_the_cancel_survives() -> None:
    ada = _agent("Ada", 1)
    claimed = _claimed_chat(ada.id)
    fresh: list[Any] = []

    async def _claim_next(agent_id: str) -> None:
        fresh.append(_claimed_chat(agent_id))

    discarded = await cancel_turn_and_discard_claim(ada.id, services=_Services(_claim_next))

    assert discarded == 1
    assert db.get_agent_trigger(claimed.id) is None
    assert [row["id"] for row in db.list_agent_triggers(ada.id, status="claimed")] == [fresh[0].id]


async def test_a_channel_claim_closes_its_round_seat_and_wakes_the_next_member() -> None:
    jim, laura = _agent("Jim", 1), _agent("Laura", 2)
    channel = db.create_channel(name="Ops", member_agent_ids=[jim.id, laura.id], created_by=jim.id)
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Where are we?",
        source_channel="channel",
    )
    [first, *_rest] = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    row = db.create_agent_trigger(
        agent_id=first["agent_id"],
        trigger_type=first["trigger_type"],
        source_channel=first["source_channel"],
        payload=first["payload"],
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    speaker = first["agent_id"]
    peer = laura.id if speaker == jim.id else jim.id
    round_id = first["payload"]["round_id"]
    services = _Services()

    discarded = await cancel_turn_and_discard_claim(speaker, services=services)

    assert discarded == 1
    assert db.get_agent_trigger(row.id) is None
    seat = db.get_channel_response_candidate(round_id=round_id, agent_id=speaker)
    assert seat is not None
    assert seat.status not in {"pending", "queued", "responding"}
    # The round moved on through the injected services, not a direct write.
    assert [(wake["agent_id"], wake["trigger_type"], wake["source_channel"]) for wake in services.enqueued] == [
        (peer, "channel_message", "channel"),
    ]
    assert services.enqueued[0]["payload"]["channel_id"] == channel.id
    assert services.enqueued[0]["task_id"] is None


async def test_a_non_channel_claim_is_deleted_with_no_wakes() -> None:
    ada = _agent("Ada", 1)
    claimed = _claimed_chat(ada.id)
    services = _Services()

    discarded = await cancel_turn_and_discard_claim(ada.id, services=services)

    assert discarded == 1
    assert db.get_agent_trigger(claimed.id) is None
    assert services.enqueued == []


async def test_an_agent_with_no_claim_only_has_its_turn_cancelled() -> None:
    ada = _agent("Ada", 1)
    queued = db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    services = _Services()

    discarded = await cancel_turn_and_discard_claim(ada.id, services=services)

    assert discarded == 0
    assert services.resets == [ada.id]
    assert db.get_agent_trigger(queued.id).status == "queued"
