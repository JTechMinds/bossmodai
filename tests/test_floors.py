"""Hard co-mingle isolation. A floor is the only axis. Missing floors deny."""

from __future__ import annotations

import os
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
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import assignment_wake_trigger, persist_result_triggers
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.channel_rounds import _ordered_members, start_channel_peer_round
from core.agent_loop.chat_fade import _run_fade_job
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
    send_home,
)
from core.messaging import route_human_dm
from core.models.agent import AgentUpdate
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task
from db.floors import LOBBY_ID, FloorNameTaken, create_floor, get_floor, rename_floor
from db.sticky_slots import list_sticky_slots, upsert_sticky_slots
from integrations.telegram.bot import cmd_channels, cmd_join, cmd_thread
from integrations.telegram.join_list import reset_channel_list_snapshots
from integrations.telegram.sessions import get_session


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


def _agent(name: str, x: int, *, floor_id: str | None = None, api_base_url: str | None = None):
    return db.create_agent(
        name,
        role="Eng",
        desk_x=x,
        desk_y=1,
        floor_id=floor_id,
        api_base_url=api_base_url,
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
    hosted = _agent("Hosted", 1, api_base_url="https://example.test/v1")
    local = _agent("Local", 2, api_base_url=None)
    channel = db.create_channel(
        name="Same floor",
        member_agent_ids=[hosted.id, local.id],
        created_by=hosted.id,
    )
    assert hosted.floor_id == local.floor_id == channel.floor_id == LOBBY_ID
    assert hosted.api_base_url != local.api_base_url


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
    ordered = _ordered_members(channel.id, set())
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
    assert "@Human Operator" in result["detail"]
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


def test_chat_fade_skips_off_floor_authors(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1)
    channel = db.create_channel(
        name="Desk",
        member_agent_ids=[ada.id],
        created_by=ada.id,
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=ada.id,
        author_name=ada.name,
        content="older turn",
        source_channel="channel",
    )
    db.update_agent(ada.id, floor_id=finance.id)
    monkeypatch.setattr("core.agent_loop.chat_fade._mode", lambda: "pressure_only")
    monkeypatch.setattr("core.agent_loop.chat_fade.system_ai_is_configured", lambda: True)
    called = {"n": 0}

    def _complete(*_args, **_kwargs):
        called["n"] += 1
        return "should not land"

    monkeypatch.setattr("core.agent_loop.chat_fade.complete_text", _complete)
    _run_fade_job(channel.id, (message.id,), "kept summary")
    assert called["n"] == 0


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


async def test_telegram_list_and_join_stay_on_the_named_floor() -> None:
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

    opened = _telegram_update(user_id=222)
    await cmd_thread(opened, _telegram_context())
    opened_channel = db.get_channel(get_session(222).target_channel_id)
    member_ids = {row["id"] for row in db.list_channel_member_details(opened_channel.id)}
    assert ada.id in member_ids
    assert bob.id not in member_ids


# ─── Floor rename and delete, vacation ───


class _Services:
    """Records runtime resets. The floor delete must cancel a live turn per agent."""

    def __init__(self) -> None:
        self.resets: list[str] = []

    async def reset_agent_runtime(self, agent_id: str) -> None:
        self.resets.append(agent_id)

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
    }


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


def test_init_db_never_pulls_a_vacationer_back_into_lobby() -> None:
    ada = _agent("Ada", 1)
    send_home(ada.id)
    db.init_db()
    agent = db.get_agent(ada.id)
    assert agent.floor_id is None
    assert agent.vacation_since is not None


async def test_a_vacationer_is_hidden_unassignable_and_never_woken() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    send_home(ada.id)
    assert send_home(ada.id).vacation_since is not None  # idempotent

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
            from_name="You",
            broadcast_manager=SimpleNamespace(),
            services=_Services(),
        )
    # Refused before anything is written: no message that looks delivered.
    assert db.get_human_chat_thread(ada.id) == []


async def test_dispatcher_fails_a_trigger_written_for_a_vacationer() -> None:
    ada = _agent("Ada", 1)
    send_home(ada.id)
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


def test_vacation_api_lists_and_brings_back() -> None:
    ada = _agent("Ada", 1)
    bob = _agent("Bob", 2)
    finance = create_floor("Finance")
    send_home(ada.id)
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


def test_activate_answers_409_for_a_vacationer() -> None:
    ada = _agent("Ada", 1)
    send_home(ada.id)
    client, headers = _client()
    response = client.post(f"/api/agents/{ada.id}/activate", headers=headers, json={"content": "hi"})
    assert response.status_code == 409
    assert response.json()["detail"] == VACATION_DENY
