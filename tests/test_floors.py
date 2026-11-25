"""Hard co-mingle isolation. A floor is the only axis. Missing floors deny."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions_lifecycle import _queue_named_next_work
from core.agent_loop.actions_work import _handle_message
from core.agent_loop.decision_contract import ConversationDecision
from core.agent_loop.decision_replies import _persist_reply
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import assignment_wake_trigger, persist_result_triggers
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.channel_rounds import ordered_channel_members, start_channel_peer_round
from core.agent_loop.conversation_notes import ThreadScope, _summarizer_input
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.agent_loop.sticky_slots import _allowed_sources
from core.bm_cli.floor_roots import floor_root
from core.floor_moves import apply_move, plan_move
from core.floors import (
    CROSS_FLOOR_DENY,
    VACATION_DENY,
    AgentOnVacation,
    FloorDenied,
    FloorOccupantsChoiceRequired,
    delete_floor,
    keep_one_floor,
    shared_floor_id,
)
from core.messaging import route_human_dm
from core.models.channel import THREAD_ARCHIVED_CANCEL_LINE
from core.models.agent import AgentUpdate
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from db.floors import LOBBY_ID, FloorNameTaken, create_floor, get_floor, rename_floor, set_floor_suspended
from db.sticky_slots import list_sticky_slots, upsert_sticky_slots
from integrations.telegram.bot import (
    cmd_agents,
    cmd_channels,
    cmd_chat,
    cmd_floor,
    cmd_floors,
    cmd_join,
    cmd_thread,
)
from integrations.telegram.join_list import reset_channel_list_snapshots
from integrations.telegram.sessions import get_session, set_floor, upsert_session
from tests._stand_down import send_on_vacation


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    reset_channel_list_snapshots()


def teardown_function() -> None:
    db.close_connection()
    reset_channel_list_snapshots()


def _agent(name: str, x: int, *, floor_id: str | None = None, connection_id: str | None = None):
    return db.create_agent(
        name,
        role="Eng",
        desk_x=x,
        desk_y=1,
        floor_id=floor_id,
        connection_id=connection_id,
    )


def _bind(*, title: str, assigned_to: str, owner_id: str | None, channel_id: str | None):
    return create_or_bind_task(
        title=title,
        description="Board work",
        project=None,
        assigned_to=assigned_to,
        requester_id=HUMAN_SENDER_ID,
        owner_id=owner_id,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel" if channel_id else None,
        notification_policy="completion_blocked" if channel_id else None,
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


def _telegram_update(user_id: int = 111) -> SimpleNamespace:
    db.set_setting("telegram_allowed_user_ids", str(user_id), "telegram")
    config.reload()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=_FakeMessage(),
    )


def _telegram_context(*args: str) -> SimpleNamespace:
    return SimpleNamespace(args=list(args), bot_data={})


def test_lobby_migration_fills_only_missing_homes() -> None:
    kept = _agent("Kept", 1)
    finance = create_floor("Finance")
    stayed = _agent("Stayed", 2, floor_id=finance.id)
    orphan = _agent("Orphan", 3)
    channel = db.create_channel(
        name="Lobby thread",
        member_agent_ids=[orphan.id],
        created_by=orphan.id,
    )
    db.execute("UPDATE agents SET floor_id = NULL WHERE id = $1", [orphan.id])
    db.execute("UPDATE channels SET floor_id = '' WHERE id = $1", [channel.id])

    db.init_db()

    assert db.get_agent(orphan.id).floor_id == LOBBY_ID
    assert db.get_channel(channel.id).floor_id == LOBBY_ID
    assert db.get_agent(kept.id).floor_id == LOBBY_ID
    assert db.get_agent(stayed.id).floor_id == finance.id


def test_lobby_migration_puts_existing_telegram_users_on_lobby_and_keeps_a_chosen_floor() -> None:
    # A database from before Telegram had a floor: no column, one user.
    db.execute("ALTER TABLE telegram_sessions DROP COLUMN floor_id")
    db.execute("INSERT INTO telegram_sessions (telegram_user_id, session_type) VALUES ($1, 'idle')", [111])

    db.init_db()

    assert get_session(111).floor_id == LOBBY_ID
    finance = create_floor("Finance")
    set_floor(111, finance.id)
    db.init_db()
    assert get_session(111).floor_id == finance.id


def test_same_floor_channel_create_stamps_floor_and_mixed_roster_denies() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    cara = _agent("Cara", 3, floor_id=finance.id)
    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id, bob.id],
        created_by=ada.id,
    )
    assert channel.floor_id == LOBBY_ID
    with pytest.raises(FloorDenied):
        db.create_channel(
            name="Mixed",
            member_agent_ids=[ada.id, cara.id],
            created_by=ada.id,
        )


def test_api_and_self_hosted_share_a_floor() -> None:
    hosted_conn = db.create_connection(name="Hosted", api_base_url="https://example.test/v1", model="m")
    local_conn = db.create_connection(name="Local", api_base_url="http://127.0.0.1:9/v1", model="m")
    hosted = _agent("Hosted", 1, connection_id=hosted_conn.id)
    local = _agent("Local", 2, connection_id=local_conn.id)
    channel = db.create_channel(
        name="Same floor",
        member_agent_ids=[hosted.id, local.id],
        created_by=hosted.id,
    )
    assert hosted.floor_id == local.floor_id == channel.floor_id == LOBBY_ID
    assert hosted.connection_id != local.connection_id


def test_talk_fanout_skips_a_member_moved_off_the_thread_floor() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id, bob.id],
        created_by=ada.id,
    )
    db.update_agent(bob.id, floor_id=finance.id)
    ordered = ordered_channel_members(channel.id, set())
    assert [item["id"] for item in ordered] == [ada.id]

    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="who is up?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert triggers
    round_id = triggers[0]["payload"]["round_id"]
    queued = {item.agent_id for item in db.list_channel_response_candidates(round_id)}
    assert bob.id not in queued
    assert ada.id in queued


def test_board_assign_and_named_next_wake_deny_cross_floor() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2, floor_id=finance.id)
    with pytest.raises(FloorDenied):
        _bind(title="Cross", assigned_to=bob.id, owner_id=ada.id, channel_id=None)

    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id],
        created_by=ada.id,
    )
    with pytest.raises(FloorDenied):
        _bind(title="Thread card", assigned_to=bob.id, owner_id=None, channel_id=channel.id)

    peer = _agent("Peer", 3)
    shared = db.create_channel(
        name="Shared",
        member_agent_ids=[ada.id, peer.id],
        created_by=ada.id,
    )
    created = _bind(title="Next", assigned_to=peer.id, owner_id=None, channel_id=shared.id)
    db.update_agent(peer.id, floor_id=finance.id)
    task = db.get_task(created.task.id)
    assert assignment_wake_trigger(task) is None
    result: dict = {}
    _queue_named_next_work(result, [task])
    assert result.get("trigger_requests", []) == []
    assert db.get_task(task.id) is not None


async def test_peer_dm_denies_cross_floor_and_allows_the_operator() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2, floor_id=finance.id)
    state = db.get_agent_state(ada.id)
    denied = await _handle_message(
        ada,
        state,
        {"recipientType": "agent", "agentId": bob.id, "content": "across"},
    )
    assert denied["detail"] == CROSS_FLOOR_DENY
    allowed = await _handle_message(
        ada,
        state,
        {"recipientType": "human", "content": "operator"},
    )
    assert allowed["detail"] != CROSS_FLOOR_DENY


async def test_a_peer_dm_is_stamped_with_the_shared_floor_and_announced() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    bob = _agent("Bob", 2, floor_id=finance.id)
    state = db.get_agent_state(ada.id)

    result = await _handle_message(
        ada, state, {"recipientType": "agent", "agentId": bob.id, "content": "spec split?"},
    )
    peer = result["peer_message"]
    stored = db.get_message(peer["message_id"])
    assert stored is not None and stored.floor_id == finance.id
    assert peer["floor_id"] == finance.id
    assert (peer["from_agent_id"], peer["to_agent_id"], peer["content"]) == (ada.id, bob.id, "spec split?")
    assert result["trigger_requests"][0]["payload"]["source_message_id"] == stored.id

    to_boss = await _handle_message(ada, state, {"recipientType": "human", "content": "status"})
    assert "peer_message" not in to_boss
    assert db.get_message(to_boss["chat_message"]["message_id"]).floor_id is None


def _peer_reply(ada, bob, *, content: str) -> dict:
    return _persist_reply(
        ada,
        db.get_agent_state(ada.id),
        {"type": "peer_message", "from_agent": bob.id, "content": content,
         "message_type": "social", "reply_chain_depth": 0},
        ConversationDecision(decision="answer", intentKind="other", reply="On it.", workCommit=False),
    )


def test_a_peer_reply_is_announced_whether_or_not_it_wakes_the_sender() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)

    woken = _peer_reply(ada, bob, content="Can you take the audit?")
    assert [request["agent_id"] for request in woken["trigger_requests"]] == [bob.id]
    quiet = _peer_reply(ada, bob, content="Audit landed.")
    assert set(quiet) == {"peer_message"}

    for result in (woken, quiet):
        peer = result["peer_message"]
        assert peer["floor_id"] == LOBBY_ID
        assert db.get_message(peer["message_id"]).floor_id == LOBBY_ID
        assert (peer["from_agent_id"], peer["to_agent_id"]) == (ada.id, bob.id)


async def test_shared_floor_id_names_the_floor_only_when_both_agents_live_on_it() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    cy = _agent("Cy", 3, floor_id=finance.id)
    away = _agent("Away", 4)
    await send_on_vacation(away.id)

    assert shared_floor_id(ada.id, bob.id) == LOBBY_ID
    assert shared_floor_id(ada.id, cy.id) is None
    assert shared_floor_id(ada.id, "no-such-agent") is None
    assert shared_floor_id(ada.id, away.id) is None
    assert shared_floor_id(ada.id, HUMAN_SENDER_ID) is None


async def test_a_peer_dm_is_stamped_with_the_db_floor_not_the_turn_snapshot() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2, floor_id=finance.id)
    # Ada moved mid-turn: the DB row is on Finance, the turn's Agent says Lobby.
    db.update_agent(ada.id, floor_id=finance.id)
    assert ada.floor_id == LOBBY_ID

    result = await _handle_message(
        ada, db.get_agent_state(ada.id),
        {"recipientType": "agent", "agentId": bob.id, "content": "moved up"},
    )
    peer = result["peer_message"]
    assert peer["floor_id"] == finance.id
    assert db.get_message(peer["message_id"]).floor_id == finance.id


def test_a_peer_reply_is_stamped_with_the_db_floor_not_the_turn_snapshot() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2, floor_id=finance.id)
    db.update_agent(ada.id, floor_id=finance.id)
    assert ada.floor_id == LOBBY_ID

    peer = _peer_reply(ada, bob, content="Audit landed.")["peer_message"]
    assert peer["floor_id"] == finance.id
    assert db.get_message(peer["message_id"]).floor_id == finance.id


def test_soft_block_still_blocks_without_mentioning_another_floor() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id, bob.id],
        created_by=HUMAN_SENDER_ID,
    )
    created = _bind(title="Card", assigned_to=ada.id, owner_id=bob.id, channel_id=channel.id)
    activate_work_activity(ada.id, created.task)
    db.update_agent(bob.id, floor_id=finance.id)
    upsert_sticky_slots([{
        "source_id": bob.id,
        "slot_kind": "next_owner",
        "body": "leave this row",
    }])

    result = apply_no_progress_block(
        ada,
        {"type": "channel_response", "channel_id": channel.id},
    )
    assert result["feedback_code"] == "no_progress_block"
    assert "@Bob" not in result["detail"]
    assert "@Boss" in result["detail"]
    assert db.get_task(created.task.id).status == "blocked"
    assert db.get_agent(bob.id) is not None
    kept = list_sticky_slots([bob.id])
    assert kept and kept[0]["body"] == "leave this row"


def test_sticky_next_owner_excludes_other_floor_and_keeps_the_row() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2, floor_id=finance.id)
    task = db.create_task(title="Card", assigned_to=ada.id, owner_id=bob.id)
    upsert_sticky_slots([{
        "source_id": bob.id,
        "slot_kind": "next_owner",
        "body": "still stored",
    }])
    allowed = _allowed_sources(task)
    assert "next_owner" not in allowed.get(bob.id, set())
    assert "next_owner" in allowed.get(ada.id, set())
    kept = list_sticky_slots([bob.id])
    assert kept and kept[0]["body"] == "still stored"


def test_conversation_notes_skip_off_floor_authors() -> None:
    """A channel's summarizer never reads a row by an agent no longer on its floor."""
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id],
        created_by=ada.id,
    )
    db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=ada.id,
        author_name=ada.name,
        content="older turn",
        source_channel="channel",
    )
    db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Boss",
        content="human turn",
        source_channel="channel",
    )
    rows = db.get_formatted_channel_messages(channel.id, human_label="Boss")
    scope = ThreadScope.channel(channel.id)
    assert [row["content"] for row in _summarizer_input(scope, rows)] == ["older turn", "human turn"]
    db.update_agent(ada.id, floor_id=finance.id)
    assert [row["content"] for row in _summarizer_input(scope, rows)] == ["human turn"]


def test_system_roster_keeps_one_floor_and_drops_a_tie() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    cara = _agent("Cara", 3, floor_id=finance.id)
    assert [item["id"] for item in keep_one_floor([{"id": ada.id}, {"id": bob.id}])] == [ada.id, bob.id]
    assert keep_one_floor([{"id": ada.id}, {"id": cara.id}]) == []


async def test_moving_one_agent_releases_the_seats_it_leaves_behind() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id, bob.id],
        created_by=ada.id,
    )
    task = db.create_task(title="Open card", assigned_to=ada.id, owner_id=ada.id)
    finance = create_floor("Finance")
    plan = plan_move(finance.id, agent_ids=[ada.id], channel_ids=[])
    assert [ref.id for ref in plan.split_threads] == [channel.id]
    assert {row.agent_id for row in db.list_channel_members(channel.id)} == {ada.id, bob.id}

    await apply_move(
        finance.id, agent_ids=[ada.id], channel_ids=[], fingerprint=plan.fingerprint, services=_Services(),
    )
    assert db.get_agent(ada.id).floor_id == finance.id
    assert ada.id not in {row.agent_id for row in db.list_channel_members(channel.id)}
    assert bob.id in {row.agent_id for row in db.list_channel_members(channel.id)}
    assert db.get_task(task.id) is not None
    assert db.get_task(task.id).title == "Open card"


def test_role_patch_cannot_carry_a_home_floor() -> None:
    assert "floor_id" not in AgentUpdate.model_fields


def test_channel_api_denies_a_mixed_floor_roster() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2, floor_id=finance.id)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    client = TestClient(app)
    headers = {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}
    response = client.post(
        "/api/channels",
        headers=headers,
        json={"name": "Mixed", "agent_ids": [ada.id, bob.id]},
    )
    assert response.status_code == 403


def test_move_api_refuses_a_plan_the_operator_was_not_shown() -> None:
    ada = _agent("Ada", 1)
    db.create_task(title="Open card", assigned_to=ada.id, owner_id=ada.id)
    finance = create_floor("Finance")
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    client = TestClient(app)
    headers = {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}
    response = client.post(
        f"/api/floors/{finance.id}/move",
        headers=headers,
        json={"agent_ids": [ada.id], "fingerprint": "stale"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "plan_changed"
    assert db.get_agent(ada.id).floor_id == LOBBY_ID


async def test_telegram_list_and_join_stay_on_the_current_floor() -> None:
    ada = _agent("Ada", 1)
    finance = create_floor("Finance")
    bob = _agent("Bob", 2, floor_id=finance.id)
    public = db.create_channel(
        name="Lobby desk",
        member_agent_ids=[ada.id],
        created_by=ada.id,
    )
    secret = db.create_channel(
        name="Confidential",
        member_agent_ids=[bob.id],
        created_by=bob.id,
    )
    listed = _telegram_update()
    await cmd_channels(listed, _telegram_context())
    text = listed.message.replies[-1]
    assert "Lobby" in text
    assert "Lobby desk" in text
    assert "Confidential" not in text

    joined = _telegram_update()
    await cmd_join(joined, _telegram_context("1"))
    session = get_session(111)
    assert session is not None
    assert session.target_channel_id == public.id
    assert session.target_channel_id != secret.id
    assert session.floor_id == LOBBY_ID

    opened = _telegram_update(user_id=222)
    await cmd_thread(opened, _telegram_context())
    opened_channel = db.get_channel(get_session(222).target_channel_id)
    member_ids = {row["id"] for row in db.list_channel_member_details(opened_channel.id)}
    assert ada.id in member_ids
    assert bob.id not in member_ids

    # On Finance the same commands see Finance only.
    moved = _telegram_update()
    await cmd_floor(moved, _telegram_context("2"))
    await cmd_channels(moved, _telegram_context())
    text = moved.message.replies[-1]
    assert "Confidential" in text
    assert "Lobby desk" not in text
    await cmd_join(moved, _telegram_context("1"))
    session = get_session(111)
    assert (session.target_channel_id, session.floor_id) == (secret.id, finance.id)


async def test_telegram_thread_and_channels_take_no_floor_name() -> None:
    _agent("Ada", 1)
    create_floor("Finance")
    for command, usage in (
        (cmd_thread, "Usage: /thread — opens a thread with everyone on your floor. /floor N changes floors."),
        (cmd_channels, "Usage: /channels — lists your floor's threads. /floor N changes floors."),
    ):
        update = _telegram_update()
        await command(update, _telegram_context("Finance"))
        assert update.message.replies == [usage]
    assert get_session(111) is None
    assert db.list_channels() == []


async def test_telegram_floors_numbers_active_floors_lobby_first_and_lists_suspended_apart() -> None:
    create_floor("Zeta")
    create_floor("alpha")
    paused = create_floor("Paused")
    set_floor_suspended(paused.id, datetime.now(timezone.utc))

    update = _telegram_update()
    await cmd_floors(update, _telegram_context())

    assert update.message.replies[-1] == (
        "*Floors*\n\n"
        "1\\. Lobby ← you're here\n"
        "2\\. alpha\n"
        "3\\. Zeta\n"
        "\n"
        "Suspended \\(resume in BossMod\\): Paused\n"
        "\n"
        "Go to a floor with /floor N\\."
    )

    set_floor(111, paused.id)
    await cmd_floors(update, _telegram_context())
    listing = update.message.replies[-1]
    assert "← you're here" not in listing
    assert "You're on Paused, which is suspended\\." in listing


async def test_telegram_floor_n_moves_closes_the_open_chat_and_refuses_a_missing_number() -> None:
    ada = _agent("Ada", 1)
    finance = create_floor("Finance")
    update = _telegram_update()
    await cmd_chat(update, _telegram_context("Ada"))
    assert get_session(111).target_agent_id == ada.id

    await cmd_floor(update, _telegram_context())
    assert update.message.replies[-1] == "You're on Lobby. /floors lists floors; /floor N moves you."
    for wrong, reply in (
        ("3", "No floor 3. Send /floors to see the list."),
        ("0", "No floor 0. Send /floors to see the list."),
        ("two", "Usage: /floor N — N is a number from /floors."),
    ):
        await cmd_floor(update, _telegram_context(wrong))
        assert update.message.replies[-1] == reply
    await cmd_floor(update, _telegram_context("1", "2"))
    assert update.message.replies[-1] == "Usage: /floor N — N is a number from /floors."
    assert get_session(111).target_agent_id == ada.id, "a refused move keeps the chat"

    await cmd_floor(update, _telegram_context("2"))

    assert update.message.replies[-1] == "Now on Finance. Your open chat was closed.\n/agents shows who's here."
    session = get_session(111)
    assert (session.floor_id, session.session_type, session.target_agent_id) == (finance.id, "idle", None)

    await cmd_floor(update, _telegram_context("1"))
    assert update.message.replies[-1] == "Now on Lobby.\n/agents shows who's here."
    assert get_session(111).floor_id == LOBBY_ID


async def test_telegram_floor_to_the_current_floor_keeps_the_chat() -> None:
    ada = _agent("Ada", 1)
    update = _telegram_update()
    await cmd_chat(update, _telegram_context("Ada"))

    await cmd_floor(update, _telegram_context("1"))

    assert update.message.replies[-1] == "You're already on Lobby."
    session = get_session(111)
    assert (session.session_type, session.target_agent_id, session.floor_id) == ("dm", ada.id, LOBBY_ID)


async def test_telegram_agents_and_chat_see_only_the_current_floor() -> None:
    lobby_ada = _agent("Ada", 1)
    finance = create_floor("Finance")
    finance_ada = _agent("Ada", 2, floor_id=finance.id)
    bob = _agent("Bob", 3, floor_id=finance.id)
    update = _telegram_update()

    await cmd_agents(update, _telegram_context())
    listing = update.message.replies[-1]
    assert listing.startswith("*Agents · Lobby*")
    assert listing.count("*Ada*") == 1
    assert "Bob" not in listing
    await cmd_chat(update, _telegram_context("Bob"))
    assert update.message.replies[-1] == (
        "No agent named 'Bob' on Lobby. /agents lists who's here; /floors to change floors."
    )
    assert get_session(111) is None
    await cmd_chat(update, _telegram_context("Ada"))
    assert get_session(111).target_agent_id == lobby_ada.id

    await cmd_floor(update, _telegram_context("2"))
    await cmd_agents(update, _telegram_context())
    listing = update.message.replies[-1]
    assert listing.startswith("*Agents · Finance*")
    assert listing.count("*Ada*") == 1
    assert "*Bob*" in listing
    await cmd_chat(update, _telegram_context("Ada"))
    session = get_session(111)
    assert (session.target_agent_id, session.floor_id) == (finance_ada.id, finance.id)
    assert session.target_agent_id != bob.id


async def test_telegram_join_after_a_floor_move_reads_as_stale() -> None:
    ada = _agent("Ada", 1)
    finance = create_floor("Finance")
    bob = _agent("Bob", 2, floor_id=finance.id)
    db.create_channel(name="Lobby desk", member_agent_ids=[ada.id], created_by=ada.id)
    db.create_channel(name="Confidential", member_agent_ids=[bob.id], created_by=bob.id)
    update = _telegram_update()
    await cmd_channels(update, _telegram_context())

    await cmd_floor(update, _telegram_context("2"))
    await cmd_join(update, _telegram_context("1"))

    assert update.message.replies[-1] == "That list is stale. Send /channels again, then /join N."
    session = get_session(111)
    assert (session.session_type, session.target_channel_id, session.floor_id) == ("idle", None, finance.id)


# ─── Floor rename and delete, vacation ───


class _Services:
    """Records runtime resets. The floor delete must cancel a live turn per agent.

    Also records each extensions reconcile with the agent ids that existed
    when it was filed, and each extensions release (a send-home's stand-down).
    """

    def __init__(self) -> None:
        self.resets: list[str] = []
        self.reconciled: list[list[str]] = []
        self.released: list[list[str]] = []

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)

    def extensions_reconcile(self) -> None:
        self.reconciled.append(sorted(agent.id for agent in db.list_agents()))

    def extensions_release_agents(self, agent_ids: list[str]) -> None:
        self.released.append(list(agent_ids))

    async def enqueue_trigger(self, **_kwargs: object) -> None:
        raise AssertionError("a floor delete must not wake anyone")


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def test_rename_floor_normalizes_and_refuses_a_taken_name() -> None:
    finance = create_floor("Finance")
    create_floor("Legal")
    assert rename_floor(finance.id, "  Money   Room ").name == "Money Room"
    assert rename_floor(finance.id, "money room").name == "money room"
    with pytest.raises(FloorNameTaken):
        rename_floor(finance.id, "LEGAL")
    with pytest.raises(ValueError):
        rename_floor(finance.id, "   ")
    with pytest.raises(LookupError):
        rename_floor("nope", "Anything")
    assert rename_floor(LOBBY_ID, "Front desk").id == LOBBY_ID


def test_floor_rename_api_maps_every_failure() -> None:
    finance = create_floor("Finance")
    create_floor("Legal")
    client, headers = _client()
    ok = client.patch(f"/api/floors/{finance.id}", headers=headers, json={"name": "Money"})
    assert ok.status_code == 200
    assert ok.json()["name"] == "Money"
    taken = client.patch(f"/api/floors/{finance.id}", headers=headers, json={"name": "legal"})
    assert taken.status_code == 409
    missing = client.patch("/api/floors/nope", headers=headers, json={"name": "Else"})
    assert missing.status_code == 404
    blank = client.patch(f"/api/floors/{finance.id}", headers=headers, json={"name": "  "})
    assert blank.status_code == 400
    lobby = client.patch(f"/api/floors/{LOBBY_ID}", headers=headers, json={"name": "Front desk"})
    assert lobby.status_code == 200
    assert lobby.json() == {**lobby.json(), "id": LOBBY_ID, "name": "Front desk"}


def test_floor_delete_api_refuses_lobby_missing_and_an_unchosen_crowd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    monkeypatch.setattr("api.routes.floors.runtime_services", services)
    finance = create_floor("Finance")
    _agent("Ada", 1, floor_id=finance.id)
    _agent("Bob", 2, floor_id=finance.id)
    client, headers = _client()
    assert client.delete(f"/api/floors/{LOBBY_ID}", headers=headers).status_code == 400
    assert client.delete("/api/floors/nope", headers=headers).status_code == 404
    bad = client.delete(f"/api/floors/{finance.id}?occupants=fire", headers=headers)
    assert bad.status_code == 400
    ask = client.delete(f"/api/floors/{finance.id}", headers=headers)
    assert ask.status_code == 409
    assert ask.json()["code"] == "occupants_choice_required"
    assert ask.json()["agent_count"] == 2
    assert get_floor(finance.id) is not None
    assert services.resets == []


async def test_delete_floor_sends_agents_home_and_archives_its_threads() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    bob = _agent("Bob", 2, floor_id=finance.id)
    stays = _agent("Stays", 3)
    desk = db.create_channel(name="Desk", member_agent_ids=[ada.id, bob.id], created_by=ada.id)
    lobby_thread = db.create_channel(name="Lobby", member_agent_ids=[stays.id], created_by=stays.id)
    card = _bind(title="Open card", assigned_to=ada.id, owner_id=bob.id, channel_id=desk.id)
    db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    services = _Services()

    with pytest.raises(FloorOccupantsChoiceRequired):
        await delete_floor(finance.id, occupants=None, services=services)

    result = await delete_floor(finance.id, occupants="send_home", services=services)

    assert sorted(result.agents_sent_home) == sorted([ada.id, bob.id])
    assert result.agents_deleted == []
    assert result.threads_archived == [desk.id]
    assert sorted(services.resets) == sorted([ada.id, bob.id])
    for agent_id in (ada.id, bob.id):
        agent = db.get_agent(agent_id)
        assert agent.floor_id is None
        assert agent.vacation_since is not None
    assert db.list_channel_members(desk.id) == []
    assert [t for t in db.list_queued_triggers() if t.agent_id == ada.id] == []
    assert db.get_channel(desk.id).status == "archived"
    assert db.get_task(card.task.id).status == "cancelled"
    assert db.get_channel(lobby_thread.id).status == "active"
    assert db.get_agent(stays.id).floor_id == LOBBY_ID
    assert get_floor(finance.id) is None


async def test_delete_floor_send_home_discards_the_cancelled_turns_claim() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    row = db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    assert db.claim_trigger(row.id) is not None
    services = _Services()

    await delete_floor(finance.id, occupants="send_home", services=services)

    assert services.resets == [ada.id]
    # The cancelled turn's claim is gone, so the live-orphan sweep never
    # re-runs it.
    assert db.list_agent_triggers(ada.id, status="claimed") == []
    assert db.get_agent_trigger(row.id) is None


async def test_deleting_a_floor_moves_its_telegram_users_to_lobby_with_the_chat_closed() -> None:
    stays = _agent("Stays", 1)
    finance = create_floor("Finance")
    bob = _agent("Bob", 2, floor_id=finance.id)
    upsert_session(111, session_type="dm", floor_id=finance.id, target_agent_id=bob.id, agent_names_key="bob")
    upsert_session(222, session_type="dm", floor_id=LOBBY_ID, target_agent_id=stays.id, agent_names_key="stays")

    await delete_floor(finance.id, occupants="send_home", services=_Services())

    moved = get_session(111)
    assert (moved.floor_id, moved.session_type) == (LOBBY_ID, "idle")
    assert (moved.target_agent_id, moved.target_channel_id, moved.agent_names_key) == (None, None, None)
    untouched = get_session(222)
    assert (untouched.floor_id, untouched.session_type, untouched.target_agent_id) == (LOBBY_ID, "dm", stays.id)


def _dm_task(title: str, assignee_id: str):
    """A task the operator asked for in the agent's DM: it has no thread, so only its assignee ties it here."""
    return create_or_bind_task(
        title=title, description="Work", project=None, assigned_to=assignee_id, requester_id=HUMAN_SENDER_ID,
        owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None, work_contract=None,
        source_channel="chat", notification_policy="completion_blocked", notification_channel_id=None,
        audit_author_name="Human Operator", audit_author_type="human",
    ).task


async def test_send_home_closes_every_gate_in_one_go_before_any_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.stand_down as stand_down_module
    from core.floors import FLOOR_DELETED, send_home

    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1, floor_id=finance.id), _agent("Bob", 2, floor_id=finance.id)
    desk = db.create_channel(name="Desk", member_agent_ids=[ada.id, bob.id], created_by=ada.id)

    with pytest.raises(LookupError, match="nobody"):
        await send_home([ada.id, "nobody"], why=FLOOR_DELETED, extra_task_ids=[], services=_Services())
    assert db.get_agent(ada.id).floor_id == finance.id, "an unknown id is refused before anything is written"

    seen: dict[str, object] = {}
    real = stand_down_module.stand_down

    async def _spy(agent_ids: list[str], **kwargs: object):
        for agent_id in agent_ids:
            agent = db.get_agent(agent_id)
            seen[agent_id] = (agent.floor_id, agent.vacation_since is not None)
        seen["members"] = db.list_channel_members(desk.id)
        return await real(agent_ids, **kwargs)

    monkeypatch.setattr(stand_down_module, "stand_down", _spy)
    services = _Services()

    sent = await send_home([ada.id, bob.id], why=FLOOR_DELETED, extra_task_ids=[], services=services)

    assert seen == {ada.id: (None, True), bob.id: (None, True), "members": []}
    assert sent.agent_ids == [ada.id, bob.id]
    assert services.released == [[ada.id, bob.id]]


async def test_sending_a_floor_home_ends_their_work() -> None:
    """Vacation is a clean stop: nothing is left waiting on someone who is never woken."""
    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1, floor_id=finance.id), _agent("Bob", 2, floor_id=finance.id)
    desk = db.create_channel(name="Desk", member_agent_ids=[ada.id, bob.id], created_by=ada.id)
    dm_task = _dm_task("Quarterly report", ada.id)
    bound = _bind(title="Thread card", assigned_to=bob.id, owner_id=None, channel_id=desk.id)
    approval = db.create_cli_approval_request(agent_id=ada.id, command="make deploy")
    consent = db.create_consent_request(agent_id=bob.id, path="/srv/data", grant_root="/srv/data", reason="Read")
    [session] = db.query(
        "INSERT INTO meeting_sessions (room_id, title, status) VALUES ('meeting_room', 'Sync', 'active') RETURNING id",
    )
    db.execute(
        "INSERT INTO meeting_session_meta (session_id, host_agent_id, meeting_mode, phase) "
        "VALUES ($1, $2, 'room', 'active')",
        [session["id"], ada.id],
    )
    services = _Services()
    painted: list[list[dict[str, object]]] = []

    async def _paint(lines: list[dict[str, object]]) -> None:
        painted.append(lines)

    result = await delete_floor(finance.id, occupants="send_home", services=services, on_before_seal=_paint)

    assert sorted(result.tasks_cancelled) == sorted([dm_task.id, bound.task.id])
    for task_id in (dm_task.id, bound.task.id):
        task = db.get_task(task_id)
        assert (task.status, task.status_note) == ("cancelled", "Cancelled: the floor was deleted")
    assert db.get_cli_approval_request(approval.id).status == "rejected"
    assert (db.get_consent_request(consent.id).status, db.get_consent_request(consent.id).decision_by) == (
        "denied", "system",
    )
    assert db.get_meeting_session_meta(session["id"])["phase"] == "ended"
    assert services.released == [[ada.id, bob.id]]
    # The stand-down's lines are painted first, before any thread seals; the
    # archive then finds nothing open and paints no cancel line of its own.
    first = painted[0]
    dm_line = next(item["chat_message"] for item in first if "chat_message" in item)
    assert dm_line["agent_id"] == ada.id and "Cancelled: the floor was deleted" in dm_line["content"]
    assert any("meeting_message" in item for item in first)
    assert painted[1:] == [[]]
    thread_lines = [item.content for item in db.list_channel_messages(desk.id, limit=50)]
    assert len([line for line in thread_lines if "Cancelled" in line]) == 1
    assert THREAD_ARCHIVED_CANCEL_LINE not in thread_lines


async def test_the_watchdog_leaves_a_vacationer_alone() -> None:
    from datetime import datetime, timedelta, timezone

    from core.agent_loop.watchdog import TaskWatchdog

    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    task = _dm_task("Quarterly report", ada.id)
    long_ago = datetime.now(timezone.utc) - timedelta(days=2)
    db.update_task(
        task.id, status="accepted", last_progress_at=long_ago, last_heartbeat_at=long_ago, watchdog_pinged_at=long_ago,
    )

    await delete_floor(finance.id, occupants="send_home", services=_Services())
    await TaskWatchdog()._check_tasks()

    assert db.get_task(task.id).status == "cancelled", "not stalled"
    assert db.list_agent_triggers(ada.id, status="queued") == [], "not pinged"


async def test_a_vacationer_comes_back_idle_and_seated() -> None:
    from core.floors import bring_back

    finance = create_floor("Finance")
    ada = _agent("Ada", 4, floor_id=finance.id)
    db.create_runtime_activity(ada.id, "movement", title="Walk", destination="Kitchen")
    await delete_floor(finance.id, occupants="send_home", services=_Services())

    returned = bring_back(ada.id, LOBBY_ID)

    assert returned.floor_id == LOBBY_ID
    assert [item for item in db.list_activities(agent_id=ada.id, limit=20) if item.status in {"active", "paused"}] == []
    state = db.get_agent_state(ada.id)
    assert (state.x, state.y) == (returned.desk_x, returned.desk_y)
    assert state.status == "idle"


def test_floor_delete_api_announces_cancelled_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    services = _Services()
    monkeypatch.setattr("api.routes.floors.runtime_services", services)
    activities: list[tuple[str, str]] = []

    async def _activity(event: str, detail: str, **_kwargs: object) -> None:
        activities.append((event, detail))

    monkeypatch.setattr("api.routes.floors.manager.broadcast_activity", _activity)
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    task = _dm_task("Quarterly report", ada.id)
    client, headers = _client()

    response = client.delete(f"/api/floors/{finance.id}?occupants=send_home", headers=headers)

    assert response.status_code == 200
    assert response.json()["tasks_cancelled"] == [task.id]
    assert ("task_cancelled", "Cancelled 1 task: the floor was deleted") in activities


async def test_delete_floor_can_delete_its_agents() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    services = _Services()
    result = await delete_floor(finance.id, occupants="delete", services=services)
    assert result.agents_deleted == [ada.id]
    assert db.get_agent(ada.id) is None
    assert get_floor(finance.id) is None


def test_floor_delete_api_reports_what_it_did(monkeypatch: pytest.MonkeyPatch) -> None:
    services = _Services()
    monkeypatch.setattr("api.routes.floors.runtime_services", services)
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    desk = db.create_channel(name="Desk", member_agent_ids=[ada.id], created_by=ada.id)
    floor_root(finance.id)
    client, headers = _client()
    response = client.delete(f"/api/floors/{finance.id}?occupants=send_home", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "floor_id": finance.id,
        "agents_sent_home": [ada.id],
        "agents_deleted": [],
        "threads_archived": [desk.id],
        "folder_archived": True,
        "process_stopped_agent_ids": [],
        "tasks_cancelled": [],
    }


def test_only_a_floor_delete_that_deletes_its_agents_reconciles_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _Services()
    monkeypatch.setattr("api.routes.floors.runtime_services", services)
    finance = create_floor("Finance")
    legal = create_floor("Legal")
    ada = _agent("Ada", 1, floor_id=finance.id)
    bob = _agent("Bob", 2, floor_id=legal.id)
    stays = _agent("Stays", 3)
    client, headers = _client()

    assert client.delete(f"/api/floors/{finance.id}", headers=headers).status_code == 409
    assert client.delete(f"/api/floors/{legal.id}?occupants=send_home", headers=headers).status_code == 200
    assert services.reconciled == [], "a refused delete or a send-home deletes no agent"

    deleted = client.delete(f"/api/floors/{finance.id}?occupants=delete", headers=headers)
    assert deleted.status_code == 200 and deleted.json()["agents_deleted"] == [ada.id]
    # Filed once, after Ada's rows are gone.
    assert services.reconciled == [sorted([bob.id, stays.id])]


def test_floor_create_rename_delete_each_broadcast_the_whole_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every open window's switcher is kept current from the server, not a reload.

    Each successful change sends the full list; a refused one sends nothing,
    because nothing changed.
    """
    monkeypatch.setattr("api.routes.floors.runtime_services", _Services())
    sent: list[list[dict[str, object]]] = []

    async def _capture(floors: list[dict[str, object]]) -> None:
        sent.append(floors)

    monkeypatch.setattr("api.routes.floors.manager.broadcast_floors_updated", _capture)
    lobby_name = get_floor(LOBBY_ID).name
    client, headers = _client()

    def listed() -> set[tuple[object, object]]:
        return {(floor["id"], floor["name"]) for floor in sent[-1]}

    created = client.post("/api/floors", headers=headers, json={"name": "Finance"})
    assert created.status_code == 201
    finance_id = created.json()["id"]
    assert len(sent) == 1
    assert listed() == {(LOBBY_ID, lobby_name), (finance_id, "Finance")}
    # JSON-ready as sent: the datetime is already a string.
    assert all(isinstance(floor["created_at"], str) for floor in sent[-1])

    renamed = client.patch(f"/api/floors/{finance_id}", headers=headers, json={"name": "Money"})
    assert renamed.status_code == 200
    assert len(sent) == 2
    assert listed() == {(LOBBY_ID, lobby_name), (finance_id, "Money")}

    refused = client.patch("/api/floors/nope", headers=headers, json={"name": "Else"})
    assert refused.status_code == 404
    assert len(sent) == 2

    deleted = client.delete(f"/api/floors/{finance_id}", headers=headers)
    assert deleted.status_code == 200
    assert len(sent) == 3
    assert listed() == {(LOBBY_ID, lobby_name)}


async def test_init_db_never_pulls_a_vacationer_back_into_lobby() -> None:
    ada = _agent("Ada", 1)
    await send_on_vacation(ada.id)
    db.init_db()
    agent = db.get_agent(ada.id)
    assert agent.floor_id is None
    assert agent.vacation_since is not None


async def test_a_vacationer_is_hidden_unassignable_and_never_woken() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    await send_on_vacation(ada.id)
    since = db.get_agent(ada.id).vacation_since
    assert since is not None
    again = await send_on_vacation(ada.id)  # idempotent
    assert again.agent_ids == [ada.id] and again.tasks_cancelled == []
    assert db.get_agent(ada.id).vacation_since == since

    assert ada.id not in {row["id"] for row in db.get_world_state()}
    assert bob.id in {row["id"] for row in db.get_world_state()}

    with pytest.raises(FloorDenied):
        _bind(title="Card", assigned_to=ada.id, owner_id=bob.id, channel_id=None)
    with pytest.raises(FloorDenied):
        await apply_move(LOBBY_ID, agent_ids=[ada.id], channel_ids=[], fingerprint="", services=_Services())

    TurnDispatcher().enqueue_trigger(ada.id, "human_chat", "chat", {"content": "hi"})
    persisted = persist_result_triggers({"trigger_requests": [{
        "agent_id": ada.id, "trigger_type": "task_follow_up", "source_channel": "chat", "payload": {},
    }]})
    assert persisted == []
    assert [t for t in db.list_queued_triggers() if t.agent_id == ada.id] == []

    with pytest.raises(AgentOnVacation):
        await route_human_dm(
            agent_id=ada.id,
            content="hello",
            broadcast_manager=SimpleNamespace(),
            services=_Services(),
        )
    # Refused before anything is written: no message that looks delivered.
    assert db.get_human_chat_thread(ada.id) == []


async def test_dispatcher_fails_a_trigger_written_for_a_vacationer() -> None:
    ada = _agent("Ada", 1)
    await send_on_vacation(ada.id)
    # Written straight to the queue, the way a row from before the vacation
    # or from a path that bypasses enqueue would be.
    row = db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    started = await TurnDispatcher()._launch_claimed_trigger(claimed)
    assert started is False
    failed = db.get_agent_trigger(row.id)
    assert failed.status == "failed"
    assert failed.failure_reason == VACATION_DENY


async def test_vacation_api_lists_and_brings_back() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    finance = create_floor("Finance")
    await send_on_vacation(ada.id)
    client, headers = _client()

    listed = client.get("/api/agents/vacation", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["id"] for row in rows] == [ada.id]
    assert set(rows[0]) == {"id", "name", "role", "color", "vacation_since"}
    assert rows[0]["vacation_since"]

    missing_floor = client.post(f"/api/agents/{ada.id}/return", headers=headers, json={"floor_id": "nope"})
    assert missing_floor.status_code == 404
    not_away = client.post(f"/api/agents/{bob.id}/return", headers=headers, json={"floor_id": LOBBY_ID})
    assert not_away.status_code == 409
    back = client.post(f"/api/agents/{ada.id}/return", headers=headers, json={"floor_id": finance.id})
    assert back.status_code == 200
    agent = db.get_agent(ada.id)
    assert agent.floor_id == finance.id
    assert agent.vacation_since is None
    assert client.get("/api/agents/vacation", headers=headers).json() == []


async def test_activate_answers_409_for_a_vacationer() -> None:
    ada = _agent("Ada", 1)
    await send_on_vacation(ada.id)
    client, headers = _client()
    response = client.post(f"/api/agents/{ada.id}/activate", headers=headers, json={"content": "hi"})
    assert response.status_code == 409
    assert response.json()["detail"] == VACATION_DENY


async def test_bring_back_moves_a_returner_off_a_chair_taken_on_the_target_floor() -> None:
    """A vacationer keeps its old desk; on the floor it returns to that chair
    may belong to someone else, and two bodies on one chair is the stack."""
    from core.floors import bring_back

    # The returner is the EARLIER hire and still yields: residents keep their
    # desks, newcomers take the next free one.
    away = db.create_agent("Away", desk_x=3, desk_y=4)
    await send_on_vacation(away.id)
    finance = create_floor("Finance")
    sitter = db.create_agent("Sitter", desk_x=3, desk_y=4, floor_id=finance.id)

    returned = bring_back(away.id, finance.id)

    # The response already carries the reconciled desk.
    assert returned.floor_id == finance.id
    assert (returned.desk_x, returned.desk_y) == (7, 4)
    assert (db.get_agent(sitter.id).desk_x, db.get_agent(sitter.id).desk_y) == (3, 4)
    state = db.get_agent_state(away.id)
    assert state is not None
    assert (state.x, state.y) == (7, 4)


async def test_bring_back_keeps_a_desk_that_is_free_on_the_target_floor() -> None:
    from core.floors import bring_back

    away = db.create_agent("Away", desk_x=11, desk_y=4)
    await send_on_vacation(away.id)
    finance = create_floor("Finance")
    db.create_agent("Sitter", desk_x=3, desk_y=4, floor_id=finance.id)

    returned = bring_back(away.id, finance.id)
    assert (returned.desk_x, returned.desk_y) == (11, 4)
