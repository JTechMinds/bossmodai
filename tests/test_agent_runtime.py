from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import db
import db.connection as db_connection
import db.settings as settings_store
from api.routes import (
    ActivationBody,
    TestConnectionBody as ConnectionTestBody,
    activate_agent,
    clear_agent_chat_history,
    get_agent_messages,
    get_runtime_contracts,
    reseed_application,
    reset_agent_runtime,
    set_setting as set_setting_route,
    test_connection as run_connection_test,
)
from api.websocket import manager
from core import config
from core.agent_loop.action_contract import render_action_contract
from core.agent_loop.decision_contract import parse_decision, render_decision_contract
from core.agent_loop import activity_runtime, loop as loop_module
from core.agent_loop.activity_scheduler import plan_arrival_follow_up, prepare_trigger_context
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.watchdog import watchdog
from core.llm import client, context_builder, routing
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


def _activate_work(agent, task, *, x: int | None = None, y: int | None = None):
    activity_runtime.activate_work_activity(
        agent.id,
        task,
        title=task.title,
        detail=task.description,
    )
    fields = {"status": "work_active"}
    if x is not None:
        fields["x"] = x
    if y is not None:
        fields["y"] = y
    return db.update_agent_state(agent.id, **fields)


def _active_activity(agent_id: str):
    return db.get_active_activity(agent_id)


def _paused_work(agent_id: str, task_id: str):
    items = db.list_activities(agent_id=agent_id, kind="work", limit=20)
    for item in items:
        if item.task_id == task_id and item.status == "paused":
            return item
    return None


def _active_movement(agent_id: str):
    items = db.list_activities(agent_id=agent_id, kind="movement", status="active", limit=20)
    return items[0] if items else None


async def _noop(*args, **kwargs):
    return None


async def _record_world_update(target: list[str], *args, **kwargs):
    target.append("world")


def test_init_db_removes_obsolete_action_contract_setting(isolated_db):
    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}

    assert "action_contract_template" not in advanced_settings
    assert advanced_settings["system_prompt_template"] == settings_store.SYSTEM_PROMPT_TEMPLATE


def test_init_db_prunes_obsolete_action_contract_setting(isolated_db):
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) VALUES ($1, $2, $3, now())",
        ["action_contract_template", "obsolete-value", "advanced"],
    )

    db.init_db()

    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}
    assert "action_contract_template" not in advanced_settings


def test_parse_action_accepts_bm_cli(isolated_db):
    parsed = parse_action('{"action":"bm_cli","command":"me get status","thought":"check status"}')
    assert parsed["action"] == "bm_cli"
    assert parsed["command"] == "me get status"


def test_prompt_context_separates_live_state_from_recent_completed_work(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Generate Words API",
        description="Define the API contract.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        task.id,
        status="complete",
        completion_summary="Finished the Generate Words API specification.",
        watchdog_pinged_at=None,
    )
    db.create_message(
        agent.id,
        None,
        '{"endpoint":"/generateWords","method":"POST"}',
        message_type="work",
        location_x=desk_x,
        location_y=desk_y,
    )

    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={
                "type": "human_chat",
                "content": "Taylor whats your status?",
                "from_name": "Human Operator",
            },
            conversation_history=[],
            reference_materials=[],
            current_activity=None,
            current_task=None,
            nearby_agents=[],
            pending_trigger_count=0,
            contract_kind="decision",
        )
    )

    system_prompt = context[0]["content"]
    assert "## Live Runtime State" in system_prompt
    assert "status: idle" in system_prompt
    assert "current_task: none" in system_prompt
    assert "## Open Tasks" in system_prompt
    assert "datetime | status | task name | description" in system_prompt
    assert "## Recent Work History / Team Directory" in system_prompt
    assert "RECENT COMPLETED TASKS:" in system_prompt
    assert "Generate Words API" in system_prompt
    assert "RECENT WORK ARTIFACTS:" in system_prompt
    assert "For status questions, answer from `Live Runtime State` first." in system_prompt


def test_init_db_omits_removed_unused_tables(isolated_db):
    rows = db.get_connection().execute("SHOW TABLES").fetchall()
    table_names = {row[0] for row in rows}

    assert "memory_nodes" not in table_names
    assert "cli_log" not in table_names
    assert "approvals" not in table_names
    assert "projects" not in table_names
    assert "agent_projects" not in table_names
    assert "schedules" not in table_names


def test_world_state_uses_camel_case_runtime_keys(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    _activate_work(agent, task, x=desk_x, y=desk_y)

    world = db.get_world_state()
    entry = next(item for item in world if item["id"] == agent.id)

    assert "currentActivityKind" in entry
    assert "boundTaskId" in entry
    assert "current_activity_kind" not in entry
    assert "bound_task_id" not in entry


@pytest.mark.asyncio
async def test_settings_route_rejects_obsolete_setting_key(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await set_setting_route("action_contract_template", "obsolete-value", "advanced")

    assert exc_info.value.status_code == 400
    assert "obsolete" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_runtime_contracts_endpoint_returns_code_owned_contracts(isolated_db):
    payload = await get_runtime_contracts()

    assert payload == {
        "decision": render_decision_contract(),
        "execution": render_action_contract(),
    }


@pytest.mark.asyncio
async def test_declined_task_assignment_marks_task_declined(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Prepare the finance deck",
        description="Create tomorrow's deck.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    prepare_trigger_context(agent.id, {"type": "task_assigned", "task_id": task.id})
    state = db.get_agent_state(agent.id)
    assert state is not None

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"decision":"decline","intentKind":"work_request","reply":"I cannot take this on right now.","commitmentKind":"none","thought":"decline the assignment"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    assert outcome.trigger_status == "completed"
    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "declined"
    assert refreshed_task.status_note == "I cannot take this on right now."


@pytest.mark.asyncio
async def test_reseed_application_recreates_database_from_current_schema(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.set_setting("default_temperature", "0.9", "llm")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    result = await reseed_application()

    assert result["status"] == "ok"
    assert db.list_agents() == []
    settings = {item.key: item.value for item in db.get_settings("llm")}
    assert settings["default_temperature"] == "0.7"


@pytest.mark.asyncio
async def test_connection_uses_single_models_endpoint(isolated_db, monkeypatch):
    calls: list[str] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "test-model"}]}

    class FakeAsyncClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, headers: dict[str, str]):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr("api.routes.httpx.AsyncClient", FakeAsyncClient)

    result = await run_connection_test(
        ConnectionTestBody(
            api_base_url="http://localhost:11434/v1",
            api_key="secret",
            model="test-model",
        )
    )

    assert result["ok"] is True
    assert calls == ["http://localhost:11434/v1/models"]


@pytest.mark.asyncio
async def test_connection_rejects_completions_endpoint_base(isolated_db):
    result = await run_connection_test(
        ConnectionTestBody(
            api_base_url="http://localhost:11434/v1/chat/completions",
            api_key="secret",
            model="test-model",
        )
    )

    assert result["ok"] is False
    assert "Use the API base URL" in result["error"]


@pytest.mark.asyncio
async def test_completion_canonicalizes_openai_compatible_custom_base(isolated_db, monkeypatch):
    captured: dict[str, object] = {}

    class FakeMessage:
        content = "ok"

    class FakeChoice:
        message = FakeMessage()

    class FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()
        model = "openai/llama3"

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("core.llm.client.litellm.acompletion", fake_acompletion)

    result = await client.completion(
        model="llama3",
        messages=[{"role": "user", "content": "hello"}],
        api_base="http://localhost:11434/v1",
        api_key=None,
    )

    assert result.model == "openai/llama3"
    assert captured["model"] == "openai/llama3"
    assert captured["api_base"] == "http://localhost:11434/v1"
    assert captured["api_key"] == "local-openai-compatible"


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
    state = db.update_agent_state(agent.id, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Please finish the report.", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"decision":"accept","intentKind":"work_request","reply":"I will take care of the report.","commitmentKind":"work","taskTitle":"Finish the report","taskDescription":"Please finish the report.","thought":"accept the new work"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"work","output":"Report body","thought":"draft"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"complete","summary":"done","thought":"finished"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Please finish the report.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    follow_up = outcome.result["trigger_requests"][0]
    state = db.get_agent_state(agent.id)
    assert state is not None
    await run_turn(
        agent,
        state,
        {
            **follow_up["payload"],
            "type": follow_up["trigger_type"],
            "task_id": follow_up.get("task_id"),
            "source_channel": follow_up["source_channel"],
        },
    )

    thread = db.get_human_chat_thread(agent.id, limit=20)
    contents = [msg.content for msg in thread]
    assert "Please finish the report." in contents
    assert "I will take care of the report." in contents
    assert "Report body" not in contents

    artifacts = db.get_recent_work_artifacts(agent.id, limit=10)
    assert any(msg.content == "Report body" for msg in artifacts)

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "work -> complete"
    detail = db.get_diagnostic(diagnostics[1]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["accept"]
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["work", "complete"]
    assert detail["steps"][0]["context_snapshot"] is None
    assert detail["steps"][0]["result"] is not None


@pytest.mark.asyncio
async def test_run_turn_chat_reply_stops_without_forcing_followup_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "How do you like the office?", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"decision":"answer","intentKind":"question","reply":"I like it here.","commitmentKind":"none","thought":"reply"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"work","output":"This should never be reached.","thought":"oops"}',
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
            "content": "How do you like the office?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    thread = db.get_human_chat_thread(agent.id, limit=20)
    assert [msg.content for msg in thread] == ["How do you like the office?", "I like it here."]
    assert db.get_recent_work_artifacts(agent.id, limit=10) == []
    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"


@pytest.mark.asyncio
async def test_run_turn_status_reply_schedules_activity_resume_for_active_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "How's it going?", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","reply":"Almost done. I need to finish a few more tests.","commitmentKind":"none","thought":"status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "How's it going?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
            "task_id": task.id,
        },
    )

    assert outcome.result["trigger_requests"][0]["trigger_type"] == "activity_resumed"
    assert outcome.result["trigger_requests"][0]["task_id"] == task.id
    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"


@pytest.mark.asyncio
async def test_run_turn_status_request_can_use_bm_cli_before_final_answer(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Generate Words API",
        description="Define the API contract.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        task.id,
        status="complete",
        completion_summary="Finished the Generate Words API specification.",
        watchdog_pinged_at=None,
    )
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Taylor whats your status?", message_type="human")

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"action":"bm_cli","command":"me get status","thought":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","reply":"I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier.","commitmentKind":"none","thought":"share grounded status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Taylor whats your status?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 2
    assert any(
        "BOSSMOD CLI RESULT" in message["content"]
        for message in captured_messages[1]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert all("BOSSMOD CLI RESULT" not in msg.content for msg in thread)

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> answer(none)"


@pytest.mark.asyncio
async def test_run_turn_status_request_forces_bm_cli_after_stale_first_answer(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Generate Words API",
        description="Define the API contract.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        task.id,
        status="complete",
        completion_summary="Finished the Generate Words API specification.",
        watchdog_pinged_at=None,
    )
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "What is the status on the api?", message_type="human")

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","reply":"I am currently working on building the API that generates words from letters.","commitmentKind":"none","thought":"status update"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"bm_cli","command":"me get status","thought":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","reply":"I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier.","commitmentKind":"none","thought":"share grounded status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "What is the status on the api?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 3
    assert any(
        "This direct request is a factual status query." in message["content"]
        for message in captured_messages[1]
        if message["role"] == "system"
    )
    assert any(
        "BOSSMOD CLI RESULT" in message["content"]
        for message in captured_messages[2]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> answer(none)"


@pytest.mark.asyncio
async def test_run_turn_status_request_cannot_drift_into_new_commitment_after_bm_cli(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "great hows it going?", message_type="human")

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","reply":"I just finished the task and I am idle now.","commitmentKind":"none","thought":"status update"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"bm_cli","command":"me get status","thought":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"decision":"accept","intentKind":"meeting_request","reply":"Sure, I am heading to the meeting room now.","commitmentKind":"meeting","destination":"meetingRoom","title":"Team Sync Meeting","detail":"Join the scheduled team sync meeting.","thought":"accept the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","reply":"I am currently idle at the Main Workspace with no active tasks.","commitmentKind":"none","thought":"share grounded status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "great hows it going?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 4
    assert any(
        "Stay on the current status question." in message["content"]
        for message in captured_messages[3]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am currently idle at the Main Workspace with no active tasks."
    assert db.get_active_activity(agent.id) is None

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> answer(none)"


@pytest.mark.asyncio
async def test_run_turn_yields_after_work_when_human_chat_is_queued(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)
    db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "How's it going?", "from_name": "Human Operator"},
    )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    call_count = 0

    async def fake_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        return client.LLMResponse(
            content='{"action":"work","output":"Implemented the endpoint scaffold.","thought":"progress"}',
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
            "type": "activity_resumed",
            "content": 'Resume work on "Fix the API bug".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    assert call_count == 1
    queued = db.list_agent_triggers(agent.id, status="queued", limit=10)
    trigger_types = [entry["trigger_type"] for entry in queued]
    assert trigger_types.count("human_chat") == 1
    assert trigger_types.count("activity_resumed") == 0

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "work"


@pytest.mark.asyncio
async def test_activity_resumed_conversation_reply_ends_turn_before_follow_up_actions(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    activity_runtime.start_conversation_activity(
        agent.id,
        title="Meeting Room Conversation",
        detail="Discuss the project timeline.",
    )
    state = db.get_agent_state(agent.id)
    assert state is not None

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    call_count = 0
    responses = iter([
        client.LLMResponse(
            content="{\"action\":\"message\",\"recipientType\":\"human\",\"content\":\"Sure, let's discuss the project timeline.\",\"thought\":\"reply in the room\"}",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"walkTo","destination":"desk","thought":"leave the room"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "content": "You arrived at Meeting Room. Continue the conversation.",
            "source_channel": "chat",
        },
    )

    assert call_count == 1

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"

    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "conversation"

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "message"


@pytest.mark.asyncio
async def test_activity_resumed_attend_meeting_ends_turn_and_emits_system_receipt(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    db.update_agent_state(agent.id, x=19, y=4, status="work_active")
    activity_runtime.start_meeting_activity(
        agent.id,
        title="Project timeline meeting",
        detail="Discuss the project timeline.",
    )
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Can you head to the meeting room for a meeting?", message_type="human")
    state = db.get_agent_state(agent.id)
    assert state is not None

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    call_count = 0
    responses = iter([
        client.LLMResponse(
            content='{"action":"attendMeeting","topic":"Project timeline","thought":"join the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"work","output":"This should never be reached.","thought":"oops"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "content": "You arrived at Meeting Room. Continue the meeting.",
            "source_channel": "chat",
            "source_message_id": human_msg.id,
        },
    )

    assert call_count == 1

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert thread[-1].message_type == "system"
    assert thread[-1].content == "Taylor joined the meeting."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "attendMeeting"


@pytest.mark.asyncio
async def test_meeting_execution_recovers_from_early_attend_and_walks_to_room(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")
    activity_runtime.start_meeting_activity(
        agent.id,
        title="Project planning",
        detail="Meet with the human operator in the meeting room.",
        metadata={"preferred_destination": "meetingRoom"},
    )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"action":"attendMeeting","topic":"Project planning","thought":"join the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"action":"walkTo","destination":"meetingRoom","thought":"Need to walk there first."}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "content": "Follow through on the accepted meeting.",
            "source_channel": "chat",
        },
    )

    assert outcome.trigger_status == "completed"
    assert outcome.result["event"] == "agent_moved"
    assert outcome.result["path"]
    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "movement"

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "attendMeeting -> walkTo"


@pytest.mark.asyncio
async def test_human_relocation_request_emits_agent_reply_and_plans_follow_up(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Can you head to the meeting room?", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"decision":"accept","intentKind":"relocation_request","reply":"I am heading to the meeting room now.","commitmentKind":"conversation","destination":"meetingRoom","title":"Direct conversation","detail":"Continue the direct conversation in the meeting room.","thought":"accept the move request"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Can you head to the meeting room?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am heading to the meeting room now."

    api_messages = await get_agent_messages(agent.id, limit=10)
    assert api_messages[-1]["from"] == "agent"
    assert api_messages[-1]["message_type"] == "social"

    assert outcome.result["trigger_requests"][0]["trigger_type"] == "activity_resumed"

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["mode"] == "decision"


@pytest.mark.asyncio
async def test_acknowledged_relocation_does_not_emit_duplicate_walk_receipt(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")
    db.create_message(HUMAN_SENDER_ID, agent.id, "Can you head to the meeting room?", message_type="human")
    db.create_message(agent.id, HUMAN_SENDER_ID, "I am heading to the meeting room now.", message_type="social")
    activity_runtime.start_conversation_activity(
        agent.id,
        title="Direct conversation",
        detail="Continue the direct conversation in the meeting room.",
        metadata={
            "preferred_destination": "meetingRoom",
            "acknowledged_by_reply": True,
        },
    )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"action":"walkTo","destination":"meetingRoom","thought":"Head to the meeting room."}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "content": "Continue the direct conversation in the meeting room.",
            "source_channel": "chat",
        },
    )

    assert outcome.result["event"] == "agent_moved"
    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]


def test_prompt_history_excludes_system_receipts(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.create_message(HUMAN_SENDER_ID, agent.id, "Head to the meeting room.", message_type="human")
    db.create_message(agent.id, HUMAN_SENDER_ID, "Taylor is heading to the Meeting Room.", message_type="system")
    activity_runtime.start_conversation_activity(
        agent.id,
        title="Direct conversation",
        detail="Head to the meeting room.",
    )

    history = loop_module._get_conversation_history(
        agent.id,
        {"type": "activity_resumed", "source_channel": "chat"},
    )

    assert [msg["content"] for msg in history] == ["Head to the meeting room."]
    assert all(msg["message_type"] != "system" for msg in history)


@pytest.mark.asyncio
async def test_run_turn_direct_request_without_reply_fails_context_validation(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "How's it going?", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"decision":"answer","intentKind":"status_request","commitmentKind":"none","thought":"nothing to say"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "How's it going?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.trigger_status == "failed"
    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["status"] == "error"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert len(detail["steps"]) == 1
    assert 'non-empty "reply"' in (detail["steps"][0]["error"] or "")
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == task.id


@pytest.mark.asyncio
async def test_execution_turn_completes_active_task_without_task_id(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"action":"complete","summary":"done","thought":"finished"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "activity_resumed",
            "content": 'Resume work on "Fix the API bug".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    assert outcome.trigger_status == "completed"
    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["status"] == "success"

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "complete"
    assert refreshed_task.completion_summary == "done"

    assert _active_activity(agent.id) is None


def test_parse_action_accepts_walk_to_minimal_payload(isolated_db):
    parsed = parse_action('{"action":"walkTo","destination":"desk","thought":"move"}')
    assert parsed["action"] == "walkTo"


def test_parse_action_accepts_attend_meeting_minimal_payload(isolated_db):
    parsed = parse_action('{"action":"attendMeeting","topic":"sync","thought":"join"}')
    assert parsed["action"] == "attendMeeting"


def test_parse_action_rejects_removed_start_task_action(isolated_db):
    parsed = parse_action('{"action":"startTask","description":"task details","thought":"formalize"}')
    assert parsed["action"] == "_parse_failed"
    assert "unsupported action" in parsed["_raw_snippet"]


def test_parse_action_requires_explicit_message_recipient_contract(isolated_db):
    parsed = parse_action('{"action":"message","to":"Human Operator","content":"hi","thought":"reply"}')
    assert parsed["action"] == "_parse_failed"
    assert "recipientType" in parsed["_raw_snippet"]


def test_parse_action_rejects_task_id_for_complete(isolated_db):
    parsed = parse_action('{"action":"complete","taskId":"api_bug","summary":"done","thought":"finished"}')
    assert parsed["action"] == "_parse_failed"
    assert "must not include" in parsed["_raw_snippet"]


def test_parse_decision_requires_task_title_for_direct_work_accept(isolated_db):
    parsed = parse_decision('{"decision":"accept","intentKind":"work_request","reply":"I will do it.","commitmentKind":"work","thought":"accept"}')
    assert parsed["decision"] == "_parse_failed"
    assert "taskTitle" in parsed["_raw_snippet"]


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

    assert result["trigger_requests"][0]["agent_id"] == target.id
    assert result["trigger_requests"][0]["trigger_type"] == "peer_message"
    assert result["trigger_requests"][0]["payload"]["from_name"] == sender.name


@pytest.mark.asyncio
async def test_complete_action_uses_active_task_without_task_id(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "done",
            "thought": "finished",
        },
        agent,
        state,
    )

    assert result["event"] == "status_changed"

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "complete"

    assert _active_activity(agent.id) is None


@pytest.mark.asyncio
async def test_attend_meeting_requires_meeting_room(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    result = await execute_action(
        {
            "action": "attendMeeting",
            "topic": "Weekly sync",
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
            "thought": "join in person",
        },
        agent,
        state,
    )

    assert result["event"] == "meeting_started"
    assert "in-person meeting" in result["detail"]
    assert result["trigger_requests"][0]["agent_id"] == target.id
    assert result["trigger_requests"][0]["payload"]["message_type"] == "meeting"


@pytest.mark.asyncio
async def test_walk_action_includes_activity_path_metadata(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=20, y=4, status="work_active")

    result = await execute_action(
        {
            "action": "walkTo",
            "destination": "desk",
            "thought": "heading back",
        },
        agent,
        state,
    )

    assert result["event"] == "agent_moved"
    assert result["agent_id"] == agent.id
    assert result["activity_extra"]["agent_id"] == agent.id
    assert result["activity_extra"]["path"] == result["path"]
    assert result["activity_extra"]["tiles_per_second"] > 0


@pytest.mark.asyncio
async def test_walk_action_already_at_destination_stays_out_of_transit(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    result = await execute_action(
        {
            "action": "walkTo",
            "destination": "desk",
            "thought": "already here",
        },
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"


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
        last_progress_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=20),
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
            content='{"decision":"accept","intentKind":"relocation_request","reply":"I am heading back to my desk.","commitmentKind":"conversation","destination":"desk","title":"Desk conversation","detail":"Return to the desk for a direct conversation.","thought":"accept the relocation"}',
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
    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "conversation"
    assert active.metadata["preferred_destination"] == "desk"


@pytest.mark.asyncio
async def test_meeting_interrupt_pauses_active_task_before_walking(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Meet me in the meeting room.", message_type="human")

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"decision":"accept","intentKind":"meeting_request","reply":"I am on my way to the meeting room.","commitmentKind":"meeting","destination":"meetingRoom","title":"Direct meeting","detail":"Meet with the human operator in the meeting room.","thought":"accept the meeting"}',
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
            "content": "Meet me in the meeting room.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "pending"
    assert refreshed_task.status_note == "Replaced by a newer accepted meeting."

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"
    assert _paused_work(agent.id, task.id) is not None
    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "meeting"
    assert active.metadata["preferred_destination"] == "meetingRoom"


@pytest.mark.asyncio
async def test_substantive_request_can_start_task_before_walk(isolated_db, monkeypatch):
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

    responses = iter([
        client.LLMResponse(
            content='{"decision":"accept","intentKind":"work_request","reply":"I will fix the API bug next.","commitmentKind":"work","taskTitle":"Fix the API bug","taskDescription":"please fix the API bug","thought":"accept the task"}',
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
            "content": "please fix the API bug",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 1
    assert tasks[0].status == "accepted"
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"
    assert _paused_work(agent.id, tasks[0].id) is None


def test_arrival_follow_up_for_work_uses_desk_label_and_clears_preference(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write brief",
        description="Draft a short brief",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_agent_state(agent.id, x=19, y=4, status="work_active")
    work_activity = activity_runtime.activate_work_activity(
        agent.id,
        task,
        title=task.title,
        detail=task.description,
        task_status="accepted",
        metadata={"preferred_destination": "desk"},
    )
    movement = activity_runtime.start_movement_activity(
        agent.id,
        destination="desk",
        parent_activity_id=work_activity.id,
        detail="Walking to desk",
        metadata={"destination": "desk", "destination_x": desk_x, "destination_y": desk_y},
    )
    assert movement.kind == "movement"
    assert _active_movement(agent.id) is not None

    db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="in_transit")

    resumed = activity_runtime.resolve_arrival(agent.id)
    assert resumed is not None
    assert resumed.kind == "work"
    assert resumed.metadata.get("preferred_destination") is None

    queued = plan_arrival_follow_up(agent.id, resumed, "Main Workspace")
    assert len(queued) == 1
    assert queued[0]["payload"]["content"] == 'You arrived at your desk. Continue work on "Write brief".'


@pytest.mark.asyncio
async def test_new_human_assignment_pauses_older_active_task_before_starting_new_one(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    old_task = db.create_task(
        title="Hey Taylor meet me in the meeting room for a new assignment",
        description="Hey Taylor meet me in the meeting room for a new assignment",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, old_task, x=20, y=4)
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "We need to make a new API that generates random sentences using letters. Please head to your desk and begin working",
        message_type="human",
    )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"decision":"accept","intentKind":"work_request","reply":"I will switch to the new sentence API now.","commitmentKind":"work","taskTitle":"Build the new sentence API","taskDescription":"We need to make a new API that generates random sentences using letters.","thought":"accept the new assignment"}',
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
            "content": "We need to make a new API that generates random sentences using letters. Please head to your desk and begin working",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    refreshed_old_task = db.get_task(old_task.id)
    assert refreshed_old_task is not None
    assert refreshed_old_task.status == "pending"
    assert refreshed_old_task.status_note == "Paused for newer accepted work."

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 2
    newest_task = tasks[-1]
    assert newest_task.id != old_task.id
    assert newest_task.status == "accepted"
    assert "sentence API" in newest_task.title

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"
    assert _paused_work(agent.id, newest_task.id) is None


@pytest.mark.asyncio
async def test_work_output_promotes_accepted_task_to_active(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Generate Words API",
        description="Define the API contract.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")
    activity_runtime.activate_work_activity(
        agent.id,
        task,
        title=task.title,
        detail=task.description,
        task_status="accepted",
        metadata={"preferred_destination": "desk"},
    )

    result = await execute_action(
        {"action": "work", "output": "POST /generateWords with letters input"},
        agent,
        state,
    )

    assert result["event"] == "agent_updated"
    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "active"


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
    _activate_work(agent, task, x=14, y=9)
    activity_runtime.start_movement_activity(
        agent.id,
        destination="desk",
        detail="Walking to desk",
        metadata={"destination": "desk", "destination_x": desk_x, "destination_y": desk_y},
    )

    queued: list[dict] = []
    idle_notifications: list[str] = []
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(dispatcher, "notify_agent_idle", lambda agent_id: idle_notifications.append(agent_id))

    simulation.set_agent_path(agent.id, [(14, 9), (desk_x, desk_y)])
    await simulation._advance_movement(1.0)

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"
    assert idle_notifications == []
    assert len(queued) == 1
    assert queued[0]["trigger_type"] == "activity_resumed"
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
    await simulation._advance_movement(0.25)

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.x == 15
    assert refreshed_state.y == 9
    assert refreshed_state.status == "in_transit"
    assert world_updates == ["world"]


@pytest.mark.asyncio
async def test_movement_speed_uses_elapsed_time_not_tick_count(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.update_agent_state(agent.id, x=14, y=9, status="in_transit")

    original_get_float = config.get_float

    def fake_get_float(key: str):
        if key == "movement_tiles_per_second":
            return 4.0
        return original_get_float(key)

    monkeypatch.setattr(config, "get_float", fake_get_float)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    simulation.set_agent_path(agent.id, [(14, 9), (15, 9), (16, 9)])
    await simulation._advance_movement(0.10)

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.x == 14
    assert refreshed_state.y == 9

    await simulation._advance_movement(0.15)

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.x == 15
    assert refreshed_state.y == 9


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
    _activate_work(agent, task, x=14, y=9)
    activity_runtime.start_movement_activity(
        agent.id,
        destination="breakRoom",
        detail="Walking to break room",
        metadata={"destination": "breakRoom", "destination_x": 20, "destination_y": 15},
    )

    queued: list[dict] = []
    idle_notifications: list[str] = []
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(dispatcher, "notify_agent_idle", lambda agent_id: idle_notifications.append(agent_id))

    simulation.set_agent_path(agent.id, [(14, 9), (20, 15)])
    await simulation._advance_movement(3.0)

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"
    assert idle_notifications == []
    assert len(queued) == 1
    assert queued[0]["trigger_type"] == "activity_resumed"
    assert queued[0]["task_id"] == task.id
    assert "Break Room" in queued[0]["payload"]["content"]


@pytest.mark.asyncio
async def test_dispatcher_preserves_active_task_on_human_chat_trigger(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    _activate_work(agent, task)
    db.update_agent_state(agent.id, status="idle")
    db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "How's it going?", "from_name": "Human Operator"},
    )

    async def fake_run_trigger(agent_arg, state_arg, trigger_arg):
        return None

    monkeypatch.setattr(dispatcher, "_run_trigger", fake_run_trigger)

    await dispatcher._drain_queue()
    await asyncio.sleep(0)

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == task.id
    dispatcher._active_turns.clear()


@pytest.mark.asyncio
async def test_prepare_trigger_context_materializes_assignment_activity_without_auto_activating_task(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Investigate bug",
        description="Debug the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.create_agent_trigger(
        agent.id,
        trigger_type="task_assigned",
        source_channel="work",
        payload={"task_title": task.title, "task_description": task.description},
        task_id=task.id,
    )

    prepare_trigger_context(agent.id, {"type": "task_assigned", "task_id": task.id})

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "pending"

    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "assignment"
    assert active.task_id == task.id


@pytest.mark.asyncio
async def test_dispatcher_marks_failed_turns_as_failed_triggers(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, status="idle")
    trigger = db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "hello", "from_name": "Human Operator"},
    )

    async def fake_run_turn(agent_arg, state_arg, trigger_arg):
        return TurnOutcome.failure(
            result={"event": "agent_error", "detail": "bad json", "agent_name": agent_arg.name},
            error="bad json",
            action={"action": "_parse_failed"},
            action_summary="",
            raw_response="{",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", fake_run_turn)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    await dispatcher._run_trigger(
        agent,
        state,
        {
            **json.loads(trigger.payload),
            "type": trigger.trigger_type,
            "trigger_id": trigger.id,
            "task_id": trigger.task_id,
            "source_channel": trigger.source_channel,
        },
    )

    refreshed = db.get_agent_trigger(trigger.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.failure_reason == "bad json"


@pytest.mark.asyncio
async def test_dispatcher_exception_reconciles_status_with_active_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Investigate bug",
        description="Debug the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)
    trigger = db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "hello", "from_name": "Human Operator"},
    )

    async def fake_run_turn(agent_arg, state_arg, trigger_arg):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", fake_run_turn)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    await dispatcher._run_trigger(
        agent,
        state,
        {
            **json.loads(trigger.payload),
            "type": trigger.trigger_type,
            "trigger_id": trigger.id,
            "task_id": trigger.task_id,
            "source_channel": trigger.source_channel,
        },
    )

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"

    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == task.id

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["status"] == "error"
    assert diagnostics[0]["trigger_type"] == "human_chat"


def test_apply_decision_does_not_persist_reply_before_work_accept_succeeds(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(activity_runtime, "activate_work_activity", boom)

    with pytest.raises(RuntimeError, match="boom"):
        apply_decision(
            {
                "decision": "accept",
                "intentKind": "work_request",
                "reply": "I will start drafting the whitepaper now.",
                "commitmentKind": "work",
                "taskTitle": "Write Whitepaper",
                "taskDescription": "Draft a whitepaper.",
                "thought": "accept the work",
            },
            agent,
            state,
            {
                "type": "human_chat",
                "content": "Please write a whitepaper.",
                "from_name": "Human Operator",
            },
        )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == []


def test_dispatcher_enqueued_human_chat_prunes_stale_rebuildable_triggers(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)

    db.create_agent_trigger(
        agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "resume"},
    )
    db.create_agent_trigger(
        agent.id,
        trigger_type="watchdog_status_ping",
        source_channel="work",
        payload={"content": "status"},
    )
    db.create_agent_trigger(
        agent.id,
        trigger_type="social",
        source_channel="chat",
        payload={"content": "hello"},
    )

    dispatcher.enqueue_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "new priority", "from_name": "Human Operator"},
    )

    queued = db.list_agent_triggers(agent.id, status="queued", limit=10)
    assert [entry["trigger_type"] for entry in queued] == ["human_chat"]


@pytest.mark.asyncio
async def test_dispatcher_rebuilds_backlog_after_human_redirects_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    older_task = db.create_task(
        title="Older task",
        description="Do the older thing",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    newer_task = db.create_task(
        title="Newer task",
        description="Do the newer thing",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = db.update_agent_state(agent.id, status="idle")
    trigger = db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "Switch to the newer task", "from_name": "Human Operator"},
    )
    db.create_agent_trigger(
        agent.id,
        trigger_type="task_assigned",
        source_channel="work",
        payload={"task_title": older_task.title, "task_description": older_task.description or ""},
        task_id=older_task.id,
    )
    db.create_agent_trigger(
        agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "resume old work"},
        task_id=older_task.id,
    )

    async def fake_run_turn(agent_arg, state_arg, trigger_arg):
        activity_runtime.activate_work_activity(
            agent_arg.id,
            newer_task,
            title=newer_task.title,
            detail=newer_task.description,
        )
        return TurnOutcome.success(
            result={"event": "task_started", "detail": "switched", "agent_name": agent_arg.name},
            action={"decision": "accept", "commitmentKind": "work"},
            action_summary="accept(work)",
            raw_response='{"decision":"accept","commitmentKind":"work"}',
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", fake_run_turn)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    await dispatcher._run_trigger(
        agent,
        state,
        {
            **json.loads(trigger.payload),
            "type": trigger.trigger_type,
            "trigger_id": trigger.id,
            "task_id": trigger.task_id,
            "source_channel": trigger.source_channel,
        },
    )

    queued = db.list_agent_triggers(agent.id, status="queued", limit=10)
    assert [entry["trigger_type"] for entry in queued] == ["task_assigned"]
    assert queued[0]["task_id"] == older_task.id


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
    _activate_work(agent, task)
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
    assert _active_activity(agent.id) is None
    assert db.count_queued_triggers(agent.id) == 0


@pytest.mark.asyncio
async def test_watchdog_skips_tasks_when_agent_has_open_triggers(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Investigate bug",
        description="Debug the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.update_task(
        task.id,
        status="active",
        last_progress_at=old,
        last_heartbeat_at=old,
        last_activity=old,
    )
    db.create_agent_trigger(
        agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "status?"},
    )

    queued: list[dict] = []
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(dispatcher, "is_active", lambda _agent_id: False)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    await watchdog._check_tasks()

    assert queued == []
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.watchdog_pinged_at is None


@pytest.mark.asyncio
async def test_watchdog_respects_recent_heartbeat_without_progress(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Investigate bug",
        description="Debug the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.update_task(
        task.id,
        status="active",
        last_progress_at=old,
        last_heartbeat_at=recent,
        last_activity=recent,
    )

    queued: list[dict] = []
    monkeypatch.setattr(dispatcher, "enqueue_trigger", lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(dispatcher, "is_active", lambda _agent_id: False)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)

    await watchdog._check_tasks()

    assert queued == []
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.watchdog_pinged_at is None
