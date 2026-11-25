"""Suspend and resume a floor (core/floor_suspend.py), its API, and every gate that honours it."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.dispatcher import TurnDispatcher
from core.floor_moves import MoveRefused, plan_move
from core.floor_suspend import FloorSuspendConflict, resume_floor, suspend_floor
from core.floors import FLOOR_SUSPENDED_DENY, FloorSuspended, bring_back, wake_refusal
from core.messaging import route_human_channel_message, route_human_dm
from core.models.message import HUMAN_SENDER_ID
from core.runtime.services import RuntimeServices
from core.tasking.service import create_or_bind_task
from db.floors import LOBBY_ID, FloorNameTaken, create_floor, get_floor, list_floors, set_floor_suspended
from integrations.telegram.bot import cmd_channels, cmd_thread, handle_plain_text
from integrations.telegram.sessions import get_session, set_floor, upsert_session
from tests._stand_down import StandDownServices, send_on_vacation


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


def _agent(name: str, x: int, *, floor_id: str | None = None):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1, floor_id=floor_id)


def _task(title: str, *, assigned_to: str | None, requester_id: str = HUMAN_SENDER_ID, channel_id: str | None = None):
    return create_or_bind_task(
        title=title, description="Work", project=None, assigned_to=assigned_to, requester_id=requester_id,
        owner_id=None, created_by=requester_id, parent_task_id=None, work_contract=None,
        source_channel="channel" if channel_id else "chat", notification_policy="completion_blocked",
        notification_channel_id=channel_id, audit_author_name="Human Operator", audit_author_type="human",
    ).task


def _flag(floor_id: str) -> None:
    """Set the flag alone (no stand-down), for gate tests."""
    set_floor_suspended(floor_id, datetime.now(timezone.utc))


def _reload_queued() -> bool:
    return db.has_open_runtime_command(["reload_schedules"])


def _clear_reloads() -> None:
    db.execute("DELETE FROM runtime_commands WHERE command_type = 'reload_schedules'")


def _client(monkeypatch: pytest.MonkeyPatch, services: Any) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.setattr("api.routes.floors.runtime_services", services)
    monkeypatch.setattr("api.routes.agents.runtime_services", services)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


# ─── Suspend and resume ───


async def test_suspend_cancels_the_floors_backlog_listed_before_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.floor_suspend as floor_suspend

    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    filed = _task("Filed by Ada", assigned_to=None, requester_id=ada.id)
    flag_when_listed: list[object] = []
    real = floor_suspend.open_task_ids_on_floor

    def _spy(floor_id: str) -> list[str]:
        flag_when_listed.append(get_floor(floor_id).suspended_at)
        return real(floor_id)

    monkeypatch.setattr(floor_suspend, "open_task_ids_on_floor", _spy)

    result = await suspend_floor(finance.id, services=StandDownServices())

    assert flag_when_listed == [None]
    assert result.stand_down.tasks_cancelled == [filed.id]
    cancelled = db.get_task(filed.id)
    assert (cancelled.status, cancelled.status_note) == ("cancelled", "Cancelled: the floor was suspended")


async def test_the_flag_closes_every_residents_gate_before_the_stand_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.floor_suspend as floor_suspend

    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1, floor_id=finance.id), _agent("Bob", 2, floor_id=finance.id)
    _agent("Stays", 3)
    seen: dict[str, str | None] = {}
    real = floor_suspend.stand_down

    async def _spy(agent_ids: list[str], **kwargs: Any):
        seen.update({agent_id: wake_refusal(db.get_agent(agent_id)) for agent_id in agent_ids})
        return await real(agent_ids, **kwargs)

    monkeypatch.setattr(floor_suspend, "stand_down", _spy)
    services = StandDownServices()

    result = await suspend_floor(finance.id, services=services)

    assert seen == {ada.id: FLOOR_SUSPENDED_DENY, bob.id: FLOOR_SUSPENDED_DENY}
    assert result.floor_id == finance.id and result.stand_down.agent_ids == [ada.id, bob.id]
    assert services.released == [[ada.id, bob.id]]
    assert get_floor(finance.id).suspended_at is not None


async def test_suspend_closes_the_floors_active_rounds_and_files_a_reload() -> None:
    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1, floor_id=finance.id), _agent("Bob", 2, floor_id=finance.id)
    thread = db.create_channel(name="Books", member_agent_ids=[ada.id, bob.id], created_by=ada.id)
    said = db.create_channel_message(
        channel_id=thread.id, author_type="human", author_name="Boss", content="Status?", source_channel="channel",
    )
    live = db.create_channel_response_round(channel_id=thread.id, source_message_id=said.id)
    lobby = _agent("Stays", 3)
    other = db.create_channel(name="Lobby", member_agent_ids=[lobby.id], created_by=lobby.id)
    other_said = db.create_channel_message(
        channel_id=other.id, author_type="human", author_name="Boss", content="Hi", source_channel="channel",
    )
    untouched = db.create_channel_response_round(channel_id=other.id, source_message_id=other_said.id)
    _clear_reloads()

    await suspend_floor(finance.id, services=StandDownServices())

    assert db.get_channel_response_round(live.id).status == "completed"
    assert db.get_channel_response_round(untouched.id).status == "active"
    assert _reload_queued()
    # Threads, memberships and homes are kept: a suspend is not a delete.
    assert db.get_channel(thread.id).status == "active"
    assert {row["id"] for row in db.list_channel_member_details(thread.id)} == {ada.id, bob.id}
    assert db.get_agent(ada.id).floor_id == finance.id


async def test_suspend_refuses_lobby_a_missing_floor_and_a_second_suspend() -> None:
    finance = create_floor("Finance")
    with pytest.raises(ValueError, match="Lobby cannot be suspended"):
        await suspend_floor(LOBBY_ID, services=StandDownServices())
    with pytest.raises(LookupError):
        await suspend_floor("nope", services=StandDownServices())
    await suspend_floor(finance.id, services=StandDownServices())
    with pytest.raises(FloorSuspendConflict):
        await suspend_floor(finance.id, services=StandDownServices())
    with pytest.raises(ValueError):
        set_floor_suspended(LOBBY_ID, datetime.now(timezone.utc))


async def test_a_vacationer_is_never_a_resident() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    away = _agent("Away", 2, floor_id=finance.id)
    await send_on_vacation(away.id)

    result = await suspend_floor(finance.id, services=StandDownServices())

    assert result.stand_down.agent_ids == [ada.id]


async def test_resume_clears_the_flag_files_a_reload_and_wakes_nobody() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    await suspend_floor(finance.id, services=StandDownServices())
    _clear_reloads()

    resumed = resume_floor(finance.id)

    assert resumed.id == finance.id and resumed.suspended_at is None
    assert get_floor(finance.id).suspended_at is None
    assert _reload_queued()
    assert wake_refusal(db.get_agent(ada.id)) is None
    assert db.list_agent_triggers(ada.id, status="queued") == []
    with pytest.raises(FloorSuspendConflict):
        resume_floor(finance.id)
    with pytest.raises(LookupError):
        resume_floor("nope")


# ─── API ───


def test_get_floors_rows_carry_suspended_at(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = create_floor("Finance")
    _flag(finance.id)
    client, headers = _client(monkeypatch, StandDownServices())
    rows = {row["id"]: row for row in client.get("/api/floors", headers=headers).json()}
    assert set(rows[finance.id]) == {"id", "name", "created_at", "suspended_at", "has_folder"}
    assert isinstance(rows[finance.id]["suspended_at"], str)
    assert rows[LOBBY_ID]["suspended_at"] is None


def test_the_suspend_and_resume_api_answer_and_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    services = StandDownServices()
    client, headers = _client(monkeypatch, services)
    floors_sent: list[list[dict[str, Any]]] = []
    activities: list[tuple[str, str]] = []

    async def _floors(rows: list[dict[str, Any]]) -> None:
        floors_sent.append(rows)

    async def _activity(event: str, detail: str, **_kwargs: Any) -> None:
        activities.append((event, detail))

    monkeypatch.setattr("api.routes.floors.manager.broadcast_floors_updated", _floors)
    monkeypatch.setattr("api.routes.floors.manager.broadcast_activity", _activity)
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    card = _task("Report", assigned_to=ada.id)

    suspended = client.post(f"/api/floors/{finance.id}/suspend", headers=headers)

    assert suspended.status_code == 200, suspended.text
    assert suspended.json() == {
        "floor_id": finance.id,
        "agent_ids": [ada.id],
        "tasks_cancelled": [card.id],
        "meetings_ended": 0,
        "approvals_closed": 0,
        "consents_closed": 0,
        "process_stopped_agent_ids": [],
    }
    row = next(item for item in floors_sent[-1] if item["id"] == finance.id)
    assert isinstance(row["suspended_at"], str)
    assert ("floor_suspended", 'Floor "Finance" suspended') in activities

    assert client.post(f"/api/floors/{finance.id}/suspend", headers=headers).status_code == 409
    assert client.post(f"/api/floors/{LOBBY_ID}/suspend", headers=headers).status_code == 400
    assert client.post("/api/floors/nope/suspend", headers=headers).status_code == 404

    resumed = client.post(f"/api/floors/{finance.id}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["id"] == finance.id and resumed.json()["suspended_at"] is None
    assert set(resumed.json()) == {"id", "name", "created_at", "suspended_at", "has_folder"}
    assert ("floor_resumed", 'Floor "Finance" resumed') in activities
    assert next(item for item in floors_sent[-1] if item["id"] == finance.id)["suspended_at"] is None
    assert client.post(f"/api/floors/{finance.id}/resume", headers=headers).status_code == 409
    assert client.post("/api/floors/nope/resume", headers=headers).status_code == 404


# ─── Gates ───


def test_every_enqueue_path_refuses_a_suspended_floors_agent() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    _flag(finance.id)

    assert TurnDispatcher().enqueue_trigger(ada.id, "human_chat", "chat", {"content": "hi"}) is False
    assert persist_result_triggers({"trigger_requests": [{
        "agent_id": ada.id, "trigger_type": "task_follow_up", "source_channel": "chat", "payload": {},
    }]}) == []
    assert db.list_agent_triggers(ada.id, status="queued") == []


async def test_the_runtime_services_refuse_before_starting_the_worker() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    _flag(finance.id)
    services = RuntimeServices()

    await services.enqueue_trigger(agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={})

    assert services._process is None, "a refused trigger does not spin the runtime up"
    assert db.list_agent_triggers(ada.id, status="queued") == []


async def test_the_dispatcher_backstop_fails_a_row_written_before_the_flag() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    row = db.create_agent_trigger(
        agent_id=ada.id, trigger_type="human_chat", source_channel="chat", payload={"content": "hi"},
    )
    _flag(finance.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None

    assert await TurnDispatcher()._launch_claimed_trigger(claimed) is False

    failed = db.get_agent_trigger(row.id)
    assert (failed.status, failed.failure_reason) == ("failed", FLOOR_SUSPENDED_DENY)


async def test_operator_messages_to_a_suspended_floor_are_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    thread = db.create_channel(name="Books", member_agent_ids=[ada.id], created_by=ada.id)
    _flag(finance.id)

    with pytest.raises(FloorSuspended):
        await route_human_dm(agent_id=ada.id, content="hi", broadcast_manager=SimpleNamespace(), services=None)
    with pytest.raises(FloorSuspended):
        await route_human_channel_message(
            channel_id=thread.id, channel_name="Books", content="hi",
            broadcast_manager=SimpleNamespace(), services=None,
        )
    assert db.get_human_chat_thread(ada.id) == []
    assert db.list_channel_messages(thread.id, limit=10) == []

    client, headers = _client(monkeypatch, StandDownServices())
    dm = client.post(f"/api/agents/{ada.id}/activate", headers=headers, json={"content": "hi"})
    assert (dm.status_code, dm.json()["detail"]) == (409, FLOOR_SUSPENDED_DENY)
    said = client.post(f"/api/channels/{thread.id}/messages", headers=headers, json={"content": "hi"})
    assert (said.status_code, said.json()["detail"]) == (409, FLOOR_SUSPENDED_DENY)


def test_a_move_is_refused_onto_or_off_a_suspended_floor() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    thread = db.create_channel(name="Books", member_agent_ids=[ada.id], created_by=ada.id)
    bob = _agent("Bob", 2)
    _flag(finance.id)

    with pytest.raises(MoveRefused, match="Finance is suspended. Resume it first."):
        plan_move(finance.id, agent_ids=[bob.id], channel_ids=[])
    with pytest.raises(MoveRefused, match="Finance is suspended"):
        plan_move(LOBBY_ID, agent_ids=[ada.id], channel_ids=[])
    with pytest.raises(MoveRefused, match="Finance is suspended"):
        plan_move(LOBBY_ID, agent_ids=[], channel_ids=[thread.id])


def test_nobody_is_hired_onto_a_suspended_floor() -> None:
    finance = create_floor("Finance")
    _flag(finance.id)
    with pytest.raises(ValueError, match="Floor is suspended"):
        _agent("Ada", 1, floor_id=finance.id)


def test_creating_a_floor_named_like_an_active_one_returns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = create_floor("Finance")
    assert create_floor("  finance ").id == finance.id
    client, headers = _client(monkeypatch, StandDownServices())
    again = client.post("/api/floors", headers=headers, json={"name": "FINANCE"})
    assert again.status_code == 201 and again.json()["id"] == finance.id
    assert len(list_floors()) == 2


def test_creating_a_floor_named_like_a_suspended_one_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returning the suspended floor would switch the operator onto a floor that is off the list."""
    finance = create_floor("Finance")
    _flag(finance.id)
    message = "A suspended floor already uses that name. Resume it from Suspended floors."
    with pytest.raises(FloorNameTaken, match=message):
        create_floor("finance")
    client, headers = _client(monkeypatch, StandDownServices())

    refused = client.post("/api/floors", headers=headers, json={"name": "Finance"})

    assert (refused.status_code, refused.json()["detail"]) == (409, message)
    assert [floor.id for floor in list_floors()] == [LOBBY_ID, finance.id], "no new row"


async def test_nobody_comes_back_from_vacation_onto_a_suspended_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = create_floor("Finance")
    away = _agent("Away", 1)
    await send_on_vacation(away.id)
    _flag(finance.id)

    with pytest.raises(ValueError, match="Finance is suspended. Resume it first."):
        bring_back(away.id, finance.id)
    client, headers = _client(monkeypatch, StandDownServices())
    refused = client.post(f"/api/agents/{away.id}/return", headers=headers, json={"floor_id": finance.id})

    assert (refused.status_code, refused.json()["detail"]) == (409, "Finance is suspended. Resume it first.")
    still = db.get_agent(away.id)
    assert still.vacation_since is not None and still.floor_id is None, "still on vacation"


def test_thread_create_task_create_and_reassign_answer_403(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1, floor_id=finance.id), _agent("Bob", 2, floor_id=finance.id)
    # Open before the flag: a stale window still shows it.
    stale = _task("Report", assigned_to=ada.id)
    _flag(finance.id)
    client, headers = _client(monkeypatch, StandDownServices())

    thread = client.post("/api/channels", headers=headers, json={"agent_ids": [ada.id, bob.id]})
    assert (thread.status_code, thread.json()["detail"]) == (403, FLOOR_SUSPENDED_DENY)
    created = client.post("/api/tasks", headers=headers, json={"title": "New", "assigned_to": ada.id})
    assert (created.status_code, created.json()["detail"]) == (403, FLOOR_SUSPENDED_DENY)
    moved = client.patch(f"/api/tasks/{stale.id}", headers=headers, json={"assigned_to": bob.id})
    assert (moved.status_code, moved.json()["detail"]) == (403, FLOOR_SUSPENDED_DENY)


def test_the_world_state_leaves_out_a_suspended_floors_agents() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    bob = _agent("Bob", 2)
    _flag(finance.id)
    listed = {row["id"] for row in db.get_world_state()}
    assert bob.id in listed and ada.id not in listed


async def test_needs_leave_out_agents_out_of_service(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    away = _agent("Away", 2)
    here = _agent("Here", 3)
    await send_on_vacation(away.id)
    _flag(finance.id)
    for agent in (ada, away, here):
        db.create_cli_approval_request(agent_id=agent.id, command=f"echo {agent.name}")
    client, headers = _client(monkeypatch, StandDownServices())

    needs = client.get("/api/needs", headers=headers).json()

    assert [item["agent_id"] for item in needs] == [here.id]


# ─── Telegram ───


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


def _telegram_update(user_id: int = 111, text: str = "") -> SimpleNamespace:
    db.set_setting("telegram_allowed_user_ids", str(user_id), "telegram")
    config.reload()
    message = _FakeMessage()
    message.text = text
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), message=message)


def _telegram_context(*args: str) -> SimpleNamespace:
    return SimpleNamespace(args=list(args), bot_data={"services": None, "broadcast_manager": SimpleNamespace()})


async def test_telegram_says_a_floor_or_its_agent_is_suspended() -> None:
    finance = create_floor("Finance")
    ada = _agent("Ada", 1, floor_id=finance.id)
    thread = db.create_channel(name="Books", member_agent_ids=[ada.id], created_by=ada.id)
    _flag(finance.id)
    set_floor(111, finance.id)

    for command in (cmd_thread, cmd_channels):
        update = _telegram_update()
        await command(update, _telegram_context())
        assert update.message.replies == ["Finance is suspended. Resume it in BossMod, or send /floors to switch."]

    upsert_session(111, session_type="dm", floor_id=finance.id, target_agent_id=ada.id, agent_names_key="ada")
    dm = _telegram_update(text="hi")
    await handle_plain_text(dm, _telegram_context())
    assert dm.message.replies == ["Ada's floor is suspended. Resume it in BossMod first."]
    assert get_session(111).session_type == "idle", "the session is closed"

    upsert_session(111, session_type="group", floor_id=finance.id, target_channel_id=thread.id, agent_names_key="ada")
    group = _telegram_update(text="hi")
    await handle_plain_text(group, _telegram_context())
    assert group.message.replies == ["That thread's floor is suspended."]
    assert get_session(111).session_type == "idle", "the session is closed"
    assert db.list_channel_messages(thread.id, limit=10) == []
