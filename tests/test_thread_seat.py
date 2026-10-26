"""Seat a live agent into an existing thread without wiping history."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from db import channel_host as host_db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.channel_rounds import post_agent_channel_share, start_channel_peer_round
from core.channel_members import ThreadSeatError, remove_agent_from_thread, seat_agent_in_thread
from core.models.message import HUMAN_SENDER_ID


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


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _thread_with_history() -> tuple[Any, Any, Any, Any]:
    debra = db.create_agent("Debra", role="Requirements Analyst")
    jim = db.create_agent("Jim", role="Eng")
    hugh = db.create_agent("Hugh", role="Code Auditor")
    channel = db.create_channel(
        name="Debra, Jim",
        member_agent_ids=[debra.id, jim.id],
        created_by=HUMAN_SENDER_ID,
    )
    first = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Debra, lock the requirements before we ship.",
        source_channel="channel",
    )
    second = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=debra.id,
        author_name=debra.name,
        content="Locked. Waiting on Hugh to CLEAR the review.",
        source_channel="channel",
    )
    return channel, hugh, first, second


def test_seat_adds_live_agent_and_keeps_history() -> None:
    client = _api_client()
    channel, hugh, first, second = _thread_with_history()

    seated = client.post(
        f"/api/channels/{channel.id}/members",
        headers=_headers(),
        json={"agent_id": hugh.id},
    )
    assert seated.status_code == 200
    body = seated.json()
    assert body["id"] == channel.id
    assert body["status"] == "active"
    member_ids = {row["id"] for row in body["members"]}
    assert member_ids == {row["id"] for row in db.list_channel_member_details(channel.id)}
    assert hugh.id in member_ids
    assert len(member_ids) == 3

    stored = db.get_channel(channel.id)
    assert stored is not None
    assert stored.id == channel.id
    assert stored.status == "active"
    messages = db.list_channel_messages(channel.id, limit=80)
    assert [item.id for item in messages] == [first.id, second.id]
    assert [item.content for item in messages] == [
        "Debra, lock the requirements before we ship.",
        "Locked. Waiting on Hugh to CLEAR the review.",
    ]


def test_duplicate_seat_is_fail_closed() -> None:
    client = _api_client()
    channel, hugh, first, second = _thread_with_history()
    first_seat = client.post(
        f"/api/channels/{channel.id}/members",
        headers=_headers(),
        json={"agent_id": hugh.id},
    )
    assert first_seat.status_code == 200

    again = client.post(
        f"/api/channels/{channel.id}/members",
        headers=_headers(),
        json={"agent_id": hugh.id},
    )
    assert again.status_code == 409
    assert "already a member" in again.json()["detail"].lower()
    messages = db.list_channel_messages(channel.id, limit=80)
    assert [item.id for item in messages] == [first.id, second.id]


def test_missing_agent_and_inactive_thread_are_fail_closed() -> None:
    client = _api_client()
    channel, hugh, first, second = _thread_with_history()

    missing = client.post(
        f"/api/channels/{channel.id}/members",
        headers=_headers(),
        json={"agent_id": "does-not-exist"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Agent not found"

    blank = client.post(
        f"/api/channels/{channel.id}/members",
        headers=_headers(),
        json={"agent_id": "   "},
    )
    assert blank.status_code == 400

    unknown = client.post(
        "/api/channels/missing-thread/members",
        headers=_headers(),
        json={"agent_id": hugh.id},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Thread not found"

    db.archive_channel(channel.id)
    archived = client.post(
        f"/api/channels/{channel.id}/members",
        headers=_headers(),
        json={"agent_id": hugh.id},
    )
    assert archived.status_code == 409
    assert "archived" in archived.json()["detail"].lower()
    messages = db.list_channel_messages(channel.id, limit=80)
    assert [item.id for item in messages] == [first.id, second.id]


def test_seated_member_can_post_like_other_members() -> None:
    channel, hugh, _first, _second = _thread_with_history()
    seated = seat_agent_in_thread(channel.id, hugh.id)
    assert seated.id == channel.id

    share, wakes = post_agent_channel_share(
        channel_id=channel.id,
        agent=hugh,
        content="CLEAR — review evidence is on /projects/review.md.",
    )
    assert share["author_agent_id"] == hugh.id
    assert "CLEAR" in share["content"]
    peer_ids = {request["agent_id"] for request in wakes}
    assert hugh.id not in peer_ids
    share_round = wakes[0]["payload"]["round_id"]
    seated_peers = {
        candidate.agent_id
        for candidate in db.list_channel_response_candidates(share_round)
    }
    assert seated_peers == {row["id"] for row in db.list_channel_member_details(channel.id)} - {hugh.id}
    assert peer_ids <= seated_peers
    assert len(wakes) == 1

    human = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Hugh, walk the transcript and CLEAR or send it back.",
        source_channel="channel",
    )
    requests = start_channel_peer_round(
        channel_id=channel.id,
        message_id=human.id,
        content=human.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    woken = {request["agent_id"] for request in requests}
    round_id = requests[0]["payload"]["round_id"]
    seated = {
        candidate.agent_id
        for candidate in db.list_channel_response_candidates(round_id)
    }
    assert hugh.id in seated
    assert seated == {row["id"] for row in db.list_channel_member_details(channel.id)}
    assert woken <= seated
    assert len(requests) == 1


def test_service_does_not_mint_a_new_thread() -> None:
    channel, hugh, first, _second = _thread_with_history()
    before = [item.id for item in db.list_channels()]
    seated = seat_agent_in_thread(channel.id, hugh.id)
    assert seated.id == channel.id
    assert [item.id for item in db.list_channels()] == before
    kept = db.list_channel_messages(channel.id, limit=80)
    assert [item.id for item in kept][0] == first.id
    assert len(kept) == 2
    try:
        seat_agent_in_thread(channel.id, hugh.id)
    except ThreadSeatError as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("duplicate seat must fail closed")


def _queue_trigger(agent_id: str, channel_id: str | None) -> Any:
    payload = {"channel_id": channel_id} if channel_id else {}
    return db.create_agent_trigger(agent_id, "channel_message", "channel", payload)


def test_remove_member_keeps_history_and_cleans_thread_state(monkeypatch: pytest.MonkeyPatch) -> None:
    broadcasts: list[dict[str, Any]] = []

    async def _capture(summary: dict[str, Any]) -> None:
        broadcasts.append(summary)

    monkeypatch.setattr("api.routes.agents.manager.broadcast_channel_updated", _capture)
    client = _api_client()
    channel, _hugh, first, second = _thread_with_history()
    debra_id, jim_id = (row["id"] for row in db.list_channel_member_details(channel.id))
    other = db.create_channel(
        name="Other", member_agent_ids=[debra_id, jim_id], created_by=HUMAN_SENDER_ID,
    )
    here = _queue_trigger(jim_id, channel.id)
    elsewhere = _queue_trigger(jim_id, other.id)
    unbound = _queue_trigger(jim_id, None)
    peer = _queue_trigger(debra_id, channel.id)
    host_db.save_channel_host_state({
        "channel_id": channel.id,
        "pass_streaks": {jim_id: 2, debra_id: 1},
        "demoted_ids": [jim_id, debra_id],
        "protected_ids": [jim_id],
    })

    removed = client.delete(f"/api/channels/{channel.id}/members/{jim_id}", headers=_headers())

    assert removed.status_code == 200
    body = removed.json()
    assert [row["id"] for row in body["members"]] == [debra_id]
    assert [row.agent_id for row in db.list_channel_members(channel.id)] == [debra_id]
    assert [item.id for item in db.list_channel_messages(channel.id, limit=80)] == [first.id, second.id]
    assert db.get_agent_trigger(here.id) is None
    assert db.get_agent_trigger(elsewhere.id) is not None
    assert db.get_agent_trigger(unbound.id) is not None
    assert db.get_agent_trigger(peer.id) is not None
    state = host_db.get_channel_host_state(channel.id)
    assert state["pass_streaks"] == {debra_id: 1}
    assert state["demoted_ids"] == [debra_id]
    assert state["protected_ids"] == []
    assert [summary["id"] for summary in broadcasts] == [channel.id]
    assert [row["id"] for row in broadcasts[0]["members"]] == [debra_id]


def test_remove_member_refusals_are_fail_closed() -> None:
    client = _api_client()
    channel, hugh, first, second = _thread_with_history()
    debra_id, jim_id = (row["id"] for row in db.list_channel_member_details(channel.id))

    not_member = client.delete(f"/api/channels/{channel.id}/members/{hugh.id}", headers=_headers())
    assert not_member.status_code == 404
    unknown = client.delete(f"/api/channels/missing-thread/members/{jim_id}", headers=_headers())
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Thread not found"

    assert client.delete(
        f"/api/channels/{channel.id}/members/{jim_id}", headers=_headers(),
    ).status_code == 200
    last = client.delete(f"/api/channels/{channel.id}/members/{debra_id}", headers=_headers())
    assert last.status_code == 409
    assert "at least one" in last.json()["detail"].lower()
    assert [row.agent_id for row in db.list_channel_members(channel.id)] == [debra_id]

    db.archive_channel(channel.id)
    with pytest.raises(ThreadSeatError) as archived:
        remove_agent_from_thread(channel.id, debra_id)
    assert archived.value.status_code == 409
    assert "archived" in str(archived.value).lower()
    assert [item.id for item in db.list_channel_messages(channel.id, limit=80)] == [first.id, second.id]


def _round_with_seats(channel_id: str, source_id: str, seats: dict[str, str]) -> Any:
    """An active round whose candidates carry the given ``{agent_id: status}``."""
    record = db.create_channel_response_round(channel_id=channel_id, source_message_id=source_id)
    for position, (agent_id, status) in enumerate(seats.items(), start=1):
        db.create_channel_response_candidate(round_id=record.id, agent_id=agent_id)
        db.update_channel_response_candidate(
            round_id=record.id, agent_id=agent_id, status=status, queue_position=position,
        )
    return record


def _seat_status(round_id: str, agent_id: str) -> str:
    return str(db.get_channel_response_candidate(round_id=round_id, agent_id=agent_id).status)


def test_remove_member_closes_unstarted_seats_and_round_still_advances() -> None:
    channel, hugh, first, _second = _thread_with_history()
    debra_id, jim_id = (row["id"] for row in db.list_channel_member_details(channel.id))
    seat_agent_in_thread(channel.id, hugh.id)
    mine = _round_with_seats(
        channel.id, first.id, {debra_id: "responding", jim_id: "pending", hugh.id: "pending"},
    )
    other_channel = db.create_channel(
        name="Other", member_agent_ids=[debra_id, jim_id], created_by=HUMAN_SENDER_ID,
    )
    elsewhere = _round_with_seats(other_channel.id, first.id, {jim_id: "pending"})

    remove_agent_from_thread(channel.id, jim_id)

    assert _seat_status(mine.id, jim_id) == "observed"
    assert _seat_status(mine.id, debra_id) == "responding"
    assert _seat_status(mine.id, hugh.id) == "pending"
    assert _seat_status(elsewhere.id, jim_id) == "pending"
    assert db.get_channel_response_round(mine.id).status == "active"
    # The round's own advance step reads these rows: the removed seat is skipped.
    from core.agent_loop.channel_rounds import _pending_candidates

    db.mark_channel_candidate_responded(round_id=mine.id, agent_id=debra_id)
    pending = _pending_candidates(db.list_channel_response_candidates(mine.id))
    assert [row.agent_id for row in pending] == [hugh.id]

    # A claimed seat is the runtime's to finish: removing its owner leaves it.
    db.update_channel_response_candidate(round_id=mine.id, agent_id=hugh.id, status="responding")
    remove_agent_from_thread(channel.id, hugh.id)
    assert _seat_status(mine.id, hugh.id) == "responding"
    assert db.get_channel_response_round(mine.id).status == "active"


def test_remove_member_completes_a_round_with_nothing_left_open() -> None:
    channel, _hugh, first, _second = _thread_with_history()
    debra_id, jim_id = (row["id"] for row in db.list_channel_member_details(channel.id))
    record = _round_with_seats(channel.id, first.id, {debra_id: "responded", jim_id: "queued"})

    remove_agent_from_thread(channel.id, jim_id)

    assert _seat_status(record.id, jim_id) == "observed"
    assert db.get_channel_response_round(record.id).status == "completed"
