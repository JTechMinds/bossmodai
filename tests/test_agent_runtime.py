from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
import db.connection as db_connection
from api.routes import (
    ActivationBody,
    activate_agent,
    clear_agent_chat_history,
    reset_agent_runtime,
)
from api.websocket import manager
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.watchdog import watchdog
from core.llm import client, routing
from core.models.message import HUMAN_SENDER_ID
from core.world.simulation import simulation
from core.world.tilemap import DEFAULT_DESKS


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db.close_connection()
    monkeypatch.setattr(db_connection, "_DB_PATH", str(tmp_path / "test-bossmod.db"))
    db_connection._connection = None
    config._cache.clear()
    config._loaded = False
    db.init_db()
    yield
    db.close_connection()
    db_connection._connection = None
    config._cache.clear()
    config._loaded = False


def _desk_xy() -> tuple[int, int]:
    chair = DEFAULT_DESKS[0]["chair_xy"]
    return int(chair[0]), int(chair[1])


async def _noop(*args, **kwargs):
    return None


async def _record_world_update(target: list[str], *args, **kwargs):
    target.append("world")


def test_human_chat_thread_excludes_work_artifacts(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)

    human = db.create_message(HUMAN_SENDER_ID, agent.id, "How did it go?", message_type="human")
    db.create_message(agent.id, None, "Full internal work artifact", message_type="work")
    reply = db.create_message(agent.id, HUMAN_SENDER_ID, "All done.", message_type="work")

    thread = db.get_human_chat_thread(agent.id, limit=20)
    assert [msg.id for msg in thread] == [human.id, reply.id]


@pytest.mark.asyncio
async def test_activate_agent_queues_human_trigger_even_when_agent_is_busy(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.update_agent_state(agent.id, status="work_active")

    queued: list[dict] = []
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))

    result = await activate_agent(agent.id, ActivationBody(content="Hey Taylor"))

    assert result["message"] == "Message queued"
    assert len(queued) == 1
    assert queued[0]["trigger_type"] == "human_chat"
    assert queued[0]["agent_id"] == agent.id


@pytest.mark.asyncio
async def test_run_turn_keeps_work_artifacts_out_of_human_chat(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, status="work_active")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Please finish the report.", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"action":"work","output":"Report body","tracking":"task","thought":"draft"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"message","recipientType":"human","content":"Here you go Jordan.","thought":"reply"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"idle","thought":"done"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Please finish the report.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    thread = db.get_human_chat_thread(agent.id, limit=20)
    contents = [msg.content for msg in thread]
    assert "Please finish the report." in contents
    assert "Here you go Jordan." in contents
    assert "Report body" not in contents

    artifacts = db.get_recent_work_artifacts(agent.id, limit=10)
    assert any(msg.content == "Report body" for msg in artifacts)


def test_parse_action_requires_explicit_tracking_for_stateful_actions(isolated_db):
    parsed = parse_action('{"action":"walkTo","destination":"desk","thought":"move"}')
    assert parsed["action"] == "_parse_failed"
    assert "tracking" in parsed["_raw_snippet"]


def test_parse_action_requires_tracking_for_attend_meeting(isolated_db):
    parsed = parse_action('{"action":"attendMeeting","topic":"sync","thought":"join"}')
    assert parsed["action"] == "_parse_failed"
    assert "tracking" in parsed["_raw_snippet"]


def test_parse_action_requires_explicit_message_recipient_contract(isolated_db):
    parsed = parse_action('{"action":"message","to":"Human Operator","content":"hi","thought":"reply"}')
    assert parsed["action"] == "_parse_failed"
    assert "recipientType" in parsed["_raw_snippet"]


@pytest.mark.asyncio
async def test_message_action_routes_to_agent_by_explicit_id(isolated_db):
    desk_x, desk_y = _desk_xy()
    sender = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    target = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(sender.id, status="work_active")

    result = await execute_action(
        {
            "action": "message",
            "recipientType": "agent",
            "agentId": target.id,
            "content": "Can you take a look at this?",
            "thought": "delegate a follow-up",
        },
        sender,
        state,
    )

    assert result["queued_triggers"][0]["agent_id"] == target.id
    assert result["queued_triggers"][0]["trigger_type"] == "peer_message"
    assert result["queued_triggers"][0]["payload"]["from_name"] == sender.name


@pytest.mark.asyncio
async def test_attend_meeting_requires_meeting_room(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    result = await execute_action(
        {
            "action": "attendMeeting",
            "topic": "Weekly sync",
            "tracking": "task",
            "thought": "join the meeting",
        },
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    assert "meetingRoom" in result["detail"]


@pytest.mark.asyncio
async def test_attend_meeting_in_room_can_notify_peer(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    target = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=20, y=4, status="work_active")

    result = await execute_action(
        {
            "action": "attendMeeting",
            "agentId": target.id,
            "topic": "Design review",
            "tracking": "task",
            "thought": "join in person",
        },
        agent,
        state,
    )

    assert result["event"] == "meeting_started"
    assert "in-person meeting" in result["detail"]
    assert result["queued_triggers"][0]["agent_id"] == target.id
    assert result["queued_triggers"][0]["payload"]["message_type"] == "meeting"


@pytest.mark.asyncio
async def test_watchdog_enqueues_status_ping_for_quiet_active_task(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="AI report",
        description="Write the report",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        task.id,
        status="active",
        last_activity=datetime.now(timezone.utc) - timedelta(minutes=20),
    )

    queued: list[dict] = []
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))

    await watchdog._check_tasks()

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.watchdog_pinged_at is not None
    assert len(queued) == 1
    assert queued[0]["trigger_type"] == "watchdog_status_ping"
    assert queued[0]["task_id"] == task.id


@pytest.mark.asyncio
async def test_walk_request_stays_chat_only_and_creates_no_task(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=14, y=9, status="work_active")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "I'd like for you to return to your office so I can give you some new tasks",
        message_type="human",
    )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"action":"walkTo","destination":"desk","tracking":"chat","thought":"Heading to my desk."}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "I'd like for you to return to your office so I can give you some new tasks",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert db.list_tasks(assigned_to=agent.id) == []
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.current_task_id is None
    assert refreshed_state.status == "in_transit"


@pytest.mark.asyncio
async def test_substantive_request_can_create_task_before_walk(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=14, y=9, status="work_active")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "please fix the API bug", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"action":"walkTo","destination":"desk","tracking":"task","thought":"Need to get to my desk before I fix it."}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "please fix the API bug",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 1
    assert tasks[0].status == "active"
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.current_task_id == tasks[0].id


@pytest.mark.asyncio
async def test_arrival_resumes_active_task_instead_of_waiting_for_watchdog(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the API failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(task.id, status="active")
    db.update_agent_state(agent.id, x=14, y=9, status="in_transit", current_task_id=task.id)

    queued: list[dict] = []
    idle_notifications: list[str] = []
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(dispatcher, "notify_agent_idle", lambda agent_id: idle_notifications.append(agent_id))

    simulation.set_agent_path(agent.id, [(14, 9), (desk_x, desk_y)])
    await simulation._advance_movement()

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "idle"
    assert refreshed_state.current_task_id == task.id
    assert idle_notifications == []
    assert len(queued) == 1
    assert queued[0]["trigger_type"] == "task_resumed"
    assert queued[0]["task_id"] == task.id


@pytest.mark.asyncio
async def test_intermediate_movement_broadcasts_world_state(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.update_agent_state(agent.id, x=14, y=9, status="in_transit")

    world_updates: list[str] = []
    monkeypatch.setattr(manager, "broadcast_world_state", lambda: _record_world_update(world_updates))
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    simulation.set_agent_path(agent.id, [(14, 9), (15, 9), (16, 9)])
    await simulation._advance_movement()

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.x == 15
    assert refreshed_state.y == 9
    assert refreshed_state.status == "in_transit"
    assert world_updates == ["world"]


@pytest.mark.asyncio
async def test_arrival_in_break_room_requests_attention_for_active_task(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the API failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(task.id, status="active")
    db.update_agent_state(agent.id, x=14, y=9, status="in_transit", current_task_id=task.id)

    queued: list[dict] = []
    idle_notifications: list[str] = []
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(dispatcher, "notify_agent_idle", lambda agent_id: idle_notifications.append(agent_id))

    simulation.set_agent_path(agent.id, [(14, 9), (20, 15)])
    await simulation._advance_movement()

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "idle"
    assert refreshed_state.current_task_id == task.id
    assert idle_notifications == []
    assert len(queued) == 1
    assert queued[0]["trigger_type"] == "task_attention_required"
    assert queued[0]["task_id"] == task.id
    assert queued[0]["payload"]["room_name"] == "Break Room"


@pytest.mark.asyncio
async def test_clear_agent_chat_history_only_deletes_direct_chat(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.create_message(HUMAN_SENDER_ID, agent.id, "hi", message_type="human")
    db.create_message(agent.id, HUMAN_SENDER_ID, "hello", message_type="work")
    db.create_message(agent.id, None, "artifact", message_type="work")

    monkeypatch.setattr(manager, "broadcast_chat_reset", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    result = await clear_agent_chat_history(agent.id)

    assert result["deleted_messages"] == 2
    assert db.get_human_chat_thread(agent.id, limit=20) == []
    assert [m.content for m in db.get_recent_work_artifacts(agent.id, limit=10)] == ["artifact"]


@pytest.mark.asyncio
async def test_reset_agent_runtime_blocks_active_task_and_clears_open_triggers(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Investigate bug",
        description="Debug the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(task.id, status="active")
    db.update_agent_state(agent.id, status="work_active", current_task_id=task.id)
    db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "status?"},
    )

    monkeypatch.setattr(dispatcher, "reset_agent", _noop)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    result = await reset_agent_runtime(agent.id)

    assert result["deleted_triggers"] == 1
    refreshed_task = db.get_task(task.id)
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "blocked"
    assert refreshed_task.status_note == "Runtime reset by human operator."
    assert refreshed_state is not None
    assert refreshed_state.status == "idle"
    assert refreshed_state.current_task_id is None
    assert db.count_queued_triggers(agent.id) == 0
