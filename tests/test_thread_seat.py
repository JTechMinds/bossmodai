"""Seat a live agent into an existing thread without wiping history."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.channel_rounds import post_agent_channel_share, start_channel_peer_round
from core.channel_members import ThreadSeatError, seat_agent_in_thread
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
    assert {row["id"] for row in db.list_channel_member_details(channel.id)} - {hugh.id} == peer_ids

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
    assert hugh.id in woken
    assert woken == {row["id"] for row in db.list_channel_member_details(channel.id)}


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
