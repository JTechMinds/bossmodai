from __future__ import annotations

import asyncio
import json
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

import db
import db.connection as db_connection
import db.settings as settings_store
from db.agent_storage import normalize_agent_personal_storage
from api.routes import (
    ActivationBody,
    ChannelCreateBody,
    ChannelMessageBody,
    MeetingMessageBody,
    RuntimeContractPreviewBody,
    RuntimeContractsBody,
    RuntimeControlBody,
    TestConnectionBody as ConnectionTestBody,
    activate_agent,
    clear_agent_chat_history,
    create_agent as create_agent_route,
    create_channel as create_channel_route,
    create_channel_message as create_channel_message_route,
    create_agent_meeting_session_message,
    create_personality as create_personality_route,
    create_task as create_task_route,
    _file_explorer_command,
    get_channel as get_channel_route,
    get_agent_desk,
    get_agent_meeting_session,
    get_agent_messages,
    get_runtime_contracts,
    get_runtime_state as get_runtime_state_route,
    open_agent_desk_folder,
    preview_runtime_contract as preview_runtime_contract_route,
    reseed_application,
    reset_agent_runtime,
    set_setting as set_setting_route,
    set_runtime_contracts as set_runtime_contracts_route,
    set_runtime_state as set_runtime_state_route,
    test_connection as run_connection_test,
)
from api.websocket import manager
from core import config
from core.bm_cli import execute_bm_cli
from core.bm_cli.filesystem import agent_artifact_dir, legacy_agent_artifact_dir, project_artifact_dir
from core.agent_loop.action_contract import render_action_contract
from core.agent_loop.decision_contract import (
    ConversationDecision,
    parse_decision,
    render_decision_contract,
    validate_decision_for_trigger,
)
from core.agent_loop import activity_runtime, loop as loop_module
from core.agent_loop.activity_scheduler import plan_arrival_follow_up, prepare_trigger_context
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.notifications import persist_chat_notification, project_chat_notifications
from core.agent_loop.outcomes import TurnOutcome
from core.agent_loop.prompt_history import build_prompt_history_view
from core.agent_loop.watchdog import watchdog
from core.llm import client, context_builder, routing
from core.models import AIPersonalityCreate, AgentCreate, TaskCreate
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.runtime.worker import RuntimeController
from core.world.simulation import simulation
from core.world.tilemap import DEFAULT_DESKS


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    if runtime_services._process is not None:
        if hasattr(runtime_services._process, "wait"):
            asyncio.run(runtime_services.stop())
        else:
            runtime_services._process = None
    runtime_services._lock = None
    runtime_services._lock_loop = None
    runtime_services._process = None
    runtime_services._reader_task = None
    runtime_services._ready_future = None
    runtime_services._pending = {}
    runtime_services._next_request_id = 0
    db.close_connection()
    db_path = str(tmp_path / "test-bossmod.db")
    monkeypatch.setenv("BOSSMOD_DB_PATH", db_path)
    monkeypatch.setattr(db_connection, "_DB_PATH", db_path)
    db_connection._thread_connections.clear()
    db_connection._thread_local = threading.local()
    config._cache.clear()
    config._loaded = False
    db.init_db()
    yield
    if runtime_services._process is not None:
        if hasattr(runtime_services._process, "wait"):
            asyncio.run(runtime_services.stop())
        else:
            runtime_services._process = None
    runtime_services._lock = None
    runtime_services._lock_loop = None
    runtime_services._process = None
    runtime_services._reader_task = None
    runtime_services._ready_future = None
    runtime_services._pending = {}
    runtime_services._next_request_id = 0
    db.close_connection()
    db_connection._thread_connections.clear()
    db_connection._thread_local = threading.local()
    config._cache.clear()
    config._loaded = False


def _desk_xy() -> tuple[int, int]:
    chair = DEFAULT_DESKS[0]["chair_xy"]
    return int(chair[0]), int(chair[1])


def _reset_agent_workspace(storage_key: str) -> Path:
    root = agent_artifact_dir(storage_key)
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


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


def _record_async(target: list[dict]):
    async def _inner(**kwargs):
        target.append(kwargs)
    return _inner


async def _record_world_update(target: list[str], *args, **kwargs):
    target.append("world")


def _build_turn_context(
    agent,
    state,
    *,
    trigger: dict[str, object] | None = None,
    contract_kind: str = "decision",
    current_activity: dict[str, object] | None = None,
    current_task: dict[str, object] | None = None,
    current_session: dict[str, object] | None = None,
    current_channel: dict[str, object] | None = None,
    reference_materials: list[str] | None = None,
):
    return context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger=trigger
        or {
            "type": "human_chat" if contract_kind == "decision" else "activity_resumed",
            "content": "Provide a quick update.",
            "from_name": "Human Operator",
        },
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=reference_materials or [],
        current_activity=current_activity,
        current_task=current_task,
        current_session=current_session,
        current_channel=current_channel,
        nearby_agents=[],
        pending_trigger_count=0,
        contract_kind=contract_kind,
    )


def test_init_db_removes_obsolete_action_contract_setting(isolated_db):
    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}

    assert "action_contract_template" not in advanced_settings
    assert advanced_settings["system_prompt_template"] == settings_store.SYSTEM_PROMPT_TEMPLATE
    assert advanced_settings["runtime_contract_decision"] == settings_store.RUNTIME_CONTRACT_DECISION_TEMPLATE
    assert advanced_settings["runtime_contract_execution"] == settings_store.RUNTIME_CONTRACT_EXECUTION_TEMPLATE
    assert advanced_settings["runtime_control_state"] == settings_store.RUNTIME_CONTROL_STATE


def test_init_db_prunes_obsolete_action_contract_setting(isolated_db):
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) VALUES ($1, $2, $3, now())",
        ["action_contract_template", "obsolete-value", "advanced"],
    )

    db.init_db()

    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}
    assert "action_contract_template" not in advanced_settings


@pytest.mark.asyncio
async def test_runtime_controller_boot_respects_persisted_pause_state(isolated_db, monkeypatch):
    calls: list[str] = []
    stop_calls: list[str] = []

    async def _stop_watchdog():
        stop_calls.append("watchdog.stop")

    async def _stop_dispatcher():
        stop_calls.append("dispatcher.stop")

    async def _stop_simulation():
        stop_calls.append("simulation.stop")

    monkeypatch.setattr("core.runtime.worker.dispatcher.start", lambda: calls.append("dispatcher.start"))
    monkeypatch.setattr("core.runtime.worker.simulation.start", lambda: calls.append("simulation.start"))
    monkeypatch.setattr("core.runtime.worker.watchdog.start", lambda: calls.append("watchdog.start"))
    monkeypatch.setattr("core.runtime.worker.watchdog.stop", _stop_watchdog)
    monkeypatch.setattr("core.runtime.worker.dispatcher.stop", _stop_dispatcher)
    monkeypatch.setattr("core.runtime.worker.simulation.stop", _stop_simulation)

    db.set_setting("runtime_control_state", "paused", "advanced")
    config.reload()
    controller = RuntimeController()
    await controller.boot(paused=True)
    assert calls == []
    await controller.shutdown()

    db.set_setting("runtime_control_state", "running", "advanced")
    config.reload()
    await controller.boot(paused=False)
    assert calls == ["dispatcher.start", "simulation.start", "watchdog.start"]
    await controller.shutdown()
    assert stop_calls == [
        "watchdog.stop",
        "dispatcher.stop",
        "simulation.stop",
        "watchdog.stop",
        "dispatcher.stop",
        "simulation.stop",
    ]


def test_parse_action_rejects_legacy_bm_cli_shape(isolated_db):
    parsed = parse_action('{"action":"bm_cli","command":"status","thought":"check status"}')
    assert parsed["action"] == "_parse_failed"
    assert 'missing "act"' in parsed["_raw_snippet"]


def test_parse_action_accepts_compact_bm_cli(isolated_db):
    parsed = parse_action('{"act":"cli","data":{"cmd":"status"},"th":"check status"}')
    assert parsed["action"] == "bm_cli"
    assert parsed["command"] == "status"


def test_parse_action_rejects_legacy_bm_cli_with_content_shape(isolated_db):
    parsed = parse_action('{"action":"bm_cli","command":"write report.md","content":"hello world","thought":"save report"}')
    assert parsed["action"] == "_parse_failed"
    assert 'missing "act"' in parsed["_raw_snippet"]


def test_parse_action_accepts_compact_delegate_task(isolated_db):
    parsed = parse_action(
        '{"act":"assign","data":{"aid":"agent-123","task":{"title":"Review API logs","desc":"Inspect failures and summarize the root cause.","outs":[{"type":"file","path":"review.md"}]}},"th":"delegate follow-up"}'
    )
    assert parsed["action"] == "delegateTask"
    assert parsed["taskTitle"] == "Review API logs"
    assert parsed["deliverables"] == [{"type": "file", "path": "review.md", "description": None}]


def test_parse_decision_rejects_legacy_work_deliverable_shape(isolated_db):
    parsed = parse_decision(
        '{"decision":"accept","intentKind":"work_request","reply":"I will save it as avocado_white.md.","commitmentKind":"work","taskTitle":"Write avocado whitepaper","taskDescription":"Create a concise whitepaper.","deliverables":[{"type":"file","path":"avocado_white.md"}],"thought":"accept the work"}'
    )
    assert parsed["decision"] == "_parse_failed"
    assert 'missing "act"' in parsed["_raw_snippet"]


def test_parse_decision_accepts_compact_work_deliverables(isolated_db):
    parsed = parse_decision(
        '{"act":"accept","intent":"work","msg":"I will save it as avocado_white.md.","commit":"work","data":{"task":{"title":"Write avocado whitepaper","desc":"Create a concise whitepaper.","outs":[{"type":"file","path":"avocado_white.md"}]}},"th":"accept the work"}'
    )
    assert parsed["decision"] == "accept"
    assert parsed["commitmentKind"] == "work"
    assert parsed["deliverables"] == [{"type": "file", "path": "avocado_white.md", "description": None}]


def test_render_decision_contract_scopes_human_chat_choices(isolated_db):
    contract = render_decision_contract("human_chat")
    assert "REQUIRED JSON SHAPE:" in contract
    assert "FIELD DEFINITIONS:" in contract
    assert '"act": "reply | accept | clarify | decline | defer"' in contract
    assert '"intent": "question | status | meeting | work | move | break | social | other"' in contract
    assert "act = the response mode you choose for this turn" in contract
    assert "intent = what the incoming message is about" in contract
    assert "obs" not in contract or "obs" not in contract.split("ALLOWED act FOR THIS TURN:")[1].splitlines()[0]
    assert '"status" belongs in "intent", never in "act"' in contract
    assert "ALLOWED act FOR THIS TURN: reply | accept | clarify | decline | defer" in contract


def test_render_action_contract_includes_required_schema(isolated_db):
    contract = render_action_contract()
    assert "REQUIRED JSON SHAPE:" in contract
    assert "FIELD DEFINITIONS:" in contract
    assert '"act": "cli | work | msg | assign | walk | mtg | idle | done | block | deleg | drop"' in contract
    assert '"to": "human | agent"' in contract
    assert '"mode": "room | remote"' in contract
    assert "act = the next execution step you are taking" in contract
    assert "data = arguments for that execution step" in contract


def test_render_decision_contract_scopes_task_assignment_choices(isolated_db):
    contract = render_decision_contract("task_assigned")
    assert "ALLOWED act FOR THIS TURN: accept | clarify | defer | decline" in contract
    assert "this is an offered assignment; use accept | clarify | defer | decline" in contract.lower()


def test_validate_decision_allows_task_assignment_clarify(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "clarify",
            "intentKind": "work_request",
            "reply": "Do you want the summary or the full report?",
            "commitmentKind": "none",
            "thought": "need assignment scope",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_assigned",
        active_task_id=None,
    )
    assert error is None


def test_validate_decision_allows_task_assignment_accept_without_new_title(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "I will take it.",
            "commitmentKind": "work",
            "thought": "accept assignment",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_assigned",
        active_task_id=None,
    )
    assert error is None


def test_apply_decision_persists_normalized_task_work_contract(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    db.update_agent_cli_state(agent.id, cwd="/projects/orchard/reports")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "I will write and save the report.",
            "commitmentKind": "work",
            "taskTitle": "Write orchard report",
            "taskDescription": "Draft the orchard report and save it.",
            "deliverables": [{"type": "file", "path": "avocado_white.md"}],
            "thought": "accept the work",
        },
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Write the orchard report and save it as avocado_white.md.",
            "from_name": "Human Operator",
            "source_channel": "chat",
        },
    )

    assert result["event"] == "decision_applied"
    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.work_contract is not None
    assert [item.model_dump() for item in task.work_contract.deliverables] == [
        {"type": "file", "path": "/projects/orchard/reports/avocado_white.md", "description": None}
    ]

    active = db.get_active_activity(agent.id)
    assert active is not None
    assert "work_contract" not in (active.metadata or {})


def test_apply_decision_task_assignment_clarify_replies_to_assigner(isolated_db):
    desk_x, desk_y = _desk_xy()
    assigner = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y)
    assignee = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(assignee.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Review rollout plan",
        description="Review the rollout plan and summarize concerns.",
        assigned_to=assignee.id,
        created_by=assigner.id,
        source_channel="peer",
        notification_policy="none",
    )

    result = apply_decision(
        {
            "decision": "clarify",
            "intentKind": "work_request",
            "reply": "Do you want a short summary or a full annotated review?",
            "commitmentKind": "none",
            "thought": "need assignment scope",
        },
        assignee,
        state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "from_agent": assigner.id,
            "from_name": assigner.name,
            "source_channel": "work",
        },
    )

    assert result["detail"] == "Taylor asked for clarification"
    assert result["trigger_requests"][0]["trigger_type"] == "peer_message"
    assert result["trigger_requests"][0]["agent_id"] == assigner.id


@pytest.mark.asyncio
async def test_create_task_route_normalizes_assigned_work_contract(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    db.update_agent_cli_state(agent.id, cwd="/projects/orchard/reports")

    task = await create_task_route(
        TaskCreate(
            title="Write orchard report",
            description="Draft the orchard report and save it.",
            assigned_to=agent.id,
            work_contract={"deliverables": [{"type": "file", "path": "avocado_white.md"}]},
        )
    )

    assert task.work_contract is not None
    assert [item.model_dump() for item in task.work_contract.deliverables] == [
        {"type": "file", "path": "/projects/orchard/reports/avocado_white.md", "description": None}
    ]
    assert task.created_by == HUMAN_SENDER_ID
    assert task.source_channel == "api"
    assert task.notification_policy == "completion_blocked"


def test_execute_bm_cli_exposes_expanded_read_commands(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Draft summary",
        description="Prepare a concise summary",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)
    db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content="Produced a concise draft summary artifact.",
        message_type="work",
    )

    current_task = execute_bm_cli(agent, state, "current-task")
    assert current_task.ok is True
    assert current_task.kind == "current_task"
    assert current_task.data is not None
    assert current_task.data["current_task"]["title"] == "Draft summary"
    assert "CURRENT TASK:" in current_task.prompt_content

    tasks = execute_bm_cli(agent, state, "tasks")
    assert tasks.ok is True
    assert tasks.kind == "tasks"
    assert "OPEN TASKS:" in tasks.prompt_content
    assert tasks.data is not None
    assert tasks.data["open_tasks"] == []

    recent_work = execute_bm_cli(agent, state, "recent-work")
    assert recent_work.ok is True
    assert recent_work.kind == "recent_work"
    assert recent_work.data is not None
    assert len(recent_work.data["recent_work_artifacts"]) == 1
    assert "RECENT WORK ARTIFACTS:" in recent_work.prompt_content

    runtime = execute_bm_cli(agent, state, "runtime")
    assert runtime.ok is True
    assert runtime.kind == "runtime"
    assert runtime.data is not None
    assert runtime.data["runtime"]["current_task"] == "Draft summary"

    status = execute_bm_cli(agent, state, "status")
    assert status.ok is True
    assert status.kind == "status"
    assert status.cwd == "/me"
    assert "RUNTIME STATUS:" in status.prompt_content

    location = execute_bm_cli(agent, state, "location")
    assert location.ok is True
    assert location.kind == "location"
    assert location.data is not None
    assert location.data["room"] == "Main Workspace"


def test_execute_bm_cli_virtual_shell_navigates_me_and_projects(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    personal_root = agent_artifact_dir(agent.storage_key)
    (personal_root / "todo.txt").write_text("remember the launch checklist", encoding="utf-8")

    project_root = project_artifact_dir("orchard")
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "brief.md").write_text("avocado market brief", encoding="utf-8")

    cwd = execute_bm_cli(agent, state, "pwd")
    assert cwd.ok is True
    assert cwd.data == {"cwd": "/me"}

    personal_listing = execute_bm_cli(agent, state, "ls")
    assert personal_listing.ok is True
    assert "- todo.txt" in personal_listing.prompt_content

    personal_file = execute_bm_cli(agent, state, "cat todo.txt")
    assert personal_file.ok is True
    assert "remember the launch checklist" in personal_file.prompt_content

    root_listing = execute_bm_cli(agent, state, "ls /")
    assert root_listing.ok is True
    assert "- me/" in root_listing.prompt_content
    assert "- projects/" in root_listing.prompt_content

    project_listing = execute_bm_cli(agent, state, "ls /projects/orchard")
    assert project_listing.ok is True
    assert "- brief.md" in project_listing.prompt_content

    moved = execute_bm_cli(agent, state, "cd /projects/orchard")
    assert moved.ok is True
    assert moved.cwd == "/projects/orchard"

    project_pwd = execute_bm_cli(agent, state, "pwd")
    assert project_pwd.ok is True
    assert project_pwd.data == {"cwd": "/projects/orchard"}

    project_file = execute_bm_cli(agent, state, "cat brief.md")
    assert project_file.ok is True
    assert "avocado market brief" in project_file.prompt_content


def test_execute_bm_cli_write_commands_create_reviewable_files(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    _reset_agent_workspace(agent.storage_key)

    mkdir = execute_bm_cli(agent, state, "mkdir reports")
    assert mkdir.ok is True

    moved = execute_bm_cli(agent, state, "cd reports")
    assert moved.ok is True
    assert moved.cwd == "/me/reports"

    personal_write = execute_bm_cli(agent, state, "write summary.md", "Avocado report draft\nSecond line")
    assert personal_write.ok is True
    assert personal_write.kind == "write"
    personal_path = agent_artifact_dir(agent.storage_key) / "reports" / "summary.md"
    assert personal_path.read_text(encoding="utf-8") == "Avocado report draft\nSecond line\n"

    appended = execute_bm_cli(agent, state, "append summary.md", "Third line")
    assert appended.ok is True
    assert personal_path.read_text(encoding="utf-8") == "Avocado report draft\nSecond line\nThird line\n"

    project_write = execute_bm_cli(agent, state, "write /projects/orchard/deliverables/avocados.md", "Project avocado memo")
    assert project_write.ok is True
    assert project_write.kind == "write"
    project_path = project_artifact_dir("orchard") / "deliverables" / "avocados.md"
    assert project_path.read_text(encoding="utf-8") == "Project avocado memo\n"


def test_execute_bm_cli_tracks_personal_workspace_in_git(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    personal_root = _reset_agent_workspace(agent.storage_key)

    result = execute_bm_cli(agent, state, "write report.md", "hello tracked workspace")

    assert result.ok is True
    assert result.data is not None
    assert result.data["path"] == "/me/report.md"
    assert result.data["git_commit"]
    assert (personal_root / ".git").exists()
    assert (personal_root / ".gitignore").exists()

    status = execute_bm_cli(agent, state, "git status")
    assert status.ok is True
    assert "report.md" not in status.data["output"]

    history = execute_bm_cli(agent, state, "git log 5")
    assert history.ok is True
    assert "bm_cli write /me/report.md" in history.data["output"]


def test_execute_bm_cli_keeps_scratchpad_untracked(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    personal_root = _reset_agent_workspace(agent.storage_key)

    result = execute_bm_cli(agent, state, "write /me/scratchpad/draft.txt", "throwaway notes")

    assert result.ok is True
    assert result.data is not None
    assert "git_commit" not in result.data
    assert (personal_root / "scratchpad" / "draft.txt").read_text(encoding="utf-8") == "throwaway notes\n"

    status = execute_bm_cli(agent, state, "git status")
    assert status.ok is True
    assert "scratchpad" not in status.data["output"]


def test_execute_bm_cli_git_restore_reverts_file_from_previous_revision(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    personal_root = _reset_agent_workspace(agent.storage_key)

    execute_bm_cli(agent, state, "write report.md", "first draft")
    execute_bm_cli(agent, state, "write report.md", "second draft")

    restore = execute_bm_cli(agent, state, "git restore --source HEAD~1 /me/report.md")

    assert restore.ok is True
    assert restore.data is not None
    assert restore.data["commit"]
    assert (personal_root / "report.md").read_text(encoding="utf-8") == "first draft\n"


def test_execute_bm_cli_gates_restricted_commands(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    result = execute_bm_cli(agent, state, "rm summary.md")

    assert result.ok is False
    assert result.kind == "approval_required"
    assert result.approval_required is True
    assert "requires operator approval" in result.detail

    events = db.list_bm_cli_events(agent_id=agent.id, limit=5)
    assert events[0]["command"] == "rm summary.md"
    assert events[0]["decision"] == "approval_required"
    assert events[0]["executor"] == "shell"
    assert events[0]["policy_tier"] == "restricted"


def test_execute_bm_cli_audits_virtual_command_lifecycle(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    execute_bm_cli(agent, state, "mkdir reports")
    execute_bm_cli(agent, state, "cd reports")
    execute_bm_cli(agent, state, "write summary.md", "hello audit trail")

    events = db.list_bm_cli_events(agent_id=agent.id, limit=10)
    assert len(events) >= 3

    write_event = events[0]
    assert write_event["command"] == "write summary.md"
    assert write_event["decision"] == "allowed"
    assert write_event["executor"] == "virtual"
    assert write_event["cwd_before"] == "/me/reports"
    assert write_event["cwd_after"] == "/me/reports"
    assert write_event["result_kind"] == "write"
    assert write_event["content_present"] is True
    assert "summary.md" in (write_event["changed_paths"] or "")

    cd_event = events[1]
    assert cd_event["command"] == "cd reports"
    assert cd_event["cwd_before"] == "/me"
    assert cd_event["cwd_after"] == "/me/reports"
    assert cd_event["result_kind"] == "cwd"


@pytest.mark.asyncio
async def test_work_completion_requires_requested_saved_file(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write avocado whitepaper",
        description="Create a concise 2-3 sentence whitepaper on avocado growth and save it as avocado_white.md.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
        work_contract={
            "deliverables": [{"type": "file", "path": "/me/avocado_white.md"}],
        },
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    activity_runtime.activate_work_activity(
        agent.id,
        task,
        title=task.title,
        detail=task.description,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    work_result = await execute_action(
        {"action": "work", "output": "Drafted a concise avocado whitepaper."},
        agent,
        state,
    )
    assert work_result["event"] == "agent_updated"
    assert work_result["missing_deliverables"] == [{"type": "file", "path": "/me/avocado_white.md", "description": None}]

    blocked_complete = await execute_action(
        {"action": "complete", "summary": "Drafted and saved the avocado whitepaper."},
        agent,
        state,
    )
    assert blocked_complete["event"] == "world_feedback"
    assert blocked_complete["missing_deliverables"] == [{"type": "file", "path": "/me/avocado_white.md", "description": None}]

    write_result = await execute_action(
        {
            "action": "bm_cli",
            "command": "write /me/avocado_white.md",
            "content": "Avocado trees thrive in warm climates.",
        },
        agent,
        state,
    )
    assert write_result["event"] == "bm_cli_result"

    completed = await execute_action(
        {"action": "complete", "summary": "Drafted and saved the avocado whitepaper."},
        agent,
        state,
    )
    assert completed["event"] == "status_changed"
    assert "completed task" in completed["detail"]
    assert completed["chat_notification"]["kind"] == "completion"
    assert completed["chat_notification"]["human_visible"] is True


def test_project_chat_notifications_emits_completion_notice(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")

    notifications = project_chat_notifications(
        agent=agent,
        trigger={"type": "activity_resumed", "source_channel": "work"},
        active_activity=None,
        action={"action": "complete"},
        result={
            "event": "status_changed",
            "chat_notification": {
                "kind": "completion",
                "task_title": "Write SLM training whitepaper",
                "deliverables": [{"type": "file", "path": "/me/slm_training_white.md"}],
                "source_channel": "chat",
                "policy": "completion_blocked",
                "task_id": "task-1",
                "human_visible": True,
            },
        },
    )

    assert [item.content for item in notifications] == [
        'Taylor finished "Write SLM training whitepaper" and saved it to /me/slm_training_white.md.'
    ]
    assert notifications[0].desk_path == "/me/slm_training_white.md"


@pytest.mark.asyncio
async def test_completion_notification_exposes_structured_desk_link_in_chat_api(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write learning Spanish whitepaper",
        description="Draft a concise whitepaper.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )

    notification = project_chat_notifications(
        agent=agent,
        trigger={"type": "activity_resumed", "source_channel": "work"},
        active_activity=None,
        action={"action": "complete"},
        result={
            "event": "status_changed",
            "chat_notification": {
                "kind": "completion",
                "task_title": "Write learning Spanish whitepaper",
                    "deliverables": [{"type": "file", "path": "/me/learning_spanish_whitepaper.md"}],
                    "source_channel": "chat",
                    "policy": "completion_blocked",
                    "task_id": task.id,
                    "human_visible": True,
                },
            },
    )[0]

    payload = persist_chat_notification(agent, notification)
    assert payload["desk_path"] == "/me/learning_spanish_whitepaper.md"

    api_messages = await get_agent_messages(agent.id, limit=10)
    system_messages = [item for item in api_messages if item["message_type"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["desk_path"] == "/me/learning_spanish_whitepaper.md"


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
            prompt_notifications=[],
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
    assert "RECENT RUNTIME NOTIFICATIONS:" in system_prompt
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
async def test_runtime_contracts_endpoint_returns_settings_backed_templates(isolated_db):
    payload = await get_runtime_contracts()

    assert payload["decision"] == settings_store.RUNTIME_CONTRACT_DECISION_TEMPLATE
    assert payload["execution"] == settings_store.RUNTIME_CONTRACT_EXECUTION_TEMPLATE
    assert any(item["name"] == "trigger.type" for item in payload["allowed_variables"])
    assert any(item["name"] == "activity.preferred_destination" for item in payload["allowed_variables"])
    assert any(example.startswith("{{if trigger.type = 'human_chat'}}") for example in payload["template_syntax"])
    assert "human_chat" in payload["preview_triggers"]


@pytest.mark.asyncio
async def test_runtime_state_route_pauses_and_resumes_services(isolated_db, monkeypatch):
    requests: list[str] = []
    start_calls: list[str] = []
    activity_events: list[str] = []
    runtime_state_broadcasts: list[str] = []

    async def _broadcast_activity(*, event: str, detail: str, agent_name=None, extra=None):
        activity_events.append(event)

    async def _broadcast_runtime_state(payload):
        runtime_state_broadcasts.append(str(payload["state"]))

    def _create_runtime_command(command_type: str, payload=None):
        requests.append(command_type)
        return type("Command", (), {"id": command_type})()

    async def _wait_for_command(command_id: str):
        if command_id == "pause_runtime":
            runtime_services._process = None
        elif command_id == "resume_runtime":
            runtime_services._process = type("Proc", (), {"returncode": None})()

    async def _start_unlocked():
        start_calls.append("runtime.start")
        runtime_services._process = type("Proc", (), {"returncode": None})()

    runtime_services._process = type("Proc", (), {"returncode": None})()
    monkeypatch.setattr("core.runtime.services.db.create_runtime_command", _create_runtime_command)
    monkeypatch.setattr(runtime_services, "_wait_for_command", _wait_for_command)
    monkeypatch.setattr(runtime_services, "_start_unlocked", _start_unlocked)
    monkeypatch.setattr(manager, "broadcast_activity", _broadcast_activity)
    monkeypatch.setattr(manager, "broadcast_runtime_state", _broadcast_runtime_state)

    paused_payload = await set_runtime_state_route(RuntimeControlBody(paused=True))

    assert paused_payload["state"] == "paused"
    assert paused_payload["paused"] is True
    assert requests == ["pause_runtime"]
    assert activity_events == ["runtime_paused"]
    assert runtime_state_broadcasts == ["paused"]
    assert config.require("runtime_control_state") == "paused"
    current_paused = await get_runtime_state_route()
    assert current_paused["state"] == "paused"
    assert current_paused["paused"] is True

    resumed_payload = await set_runtime_state_route(RuntimeControlBody(paused=False))

    assert resumed_payload["state"] == "running"
    assert resumed_payload["paused"] is False
    assert start_calls == ["runtime.start"]
    assert requests == ["pause_runtime", "resume_runtime"]
    assert activity_events == ["runtime_paused", "runtime_resumed"]
    assert runtime_state_broadcasts == ["paused", "running"]
    assert config.require("runtime_control_state") == "running"
    current_running = await get_runtime_state_route()
    assert current_running["state"] == "running"
    assert current_running["paused"] is False


@pytest.mark.asyncio
async def test_set_runtime_contracts_persists_live_templates_without_restart(isolated_db):
    await set_runtime_contracts_route(
        RuntimeContractsBody(
            decision="{{if trigger.type = 'human_chat'}}HUMAN DECISION{{else}}OTHER DECISION{{end}}",
            execution="EXECUTION FOR {{trigger.type}}",
        )
    )

    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(
        name="Taylor",
        role="Operations Analyst",
        prompt_template="Stay concise for {{agent_name}}.",
        desk_x=desk_x,
        desk_y=desk_y,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    human_context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "human_chat",
                "content": "What is your status?",
                "from_name": "Human Operator",
            },
            contract_kind="decision",
        )
    )
    peer_context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "peer_message",
                "content": "Can you review this?",
                "from_name": "Morgan",
                "from_agent": "agent-morgan",
            },
            contract_kind="decision",
        )
    )
    execution_context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "activity_resumed",
                "content": "Continue the current work activity.",
            },
            contract_kind="execution",
        )
    )

    assert human_context[1]["content"] == "HUMAN DECISION"
    assert peer_context[1]["content"] == "OTHER DECISION"
    assert execution_context[1]["content"] == "EXECUTION FOR activity_resumed"


@pytest.mark.asyncio
async def test_prompt_templates_render_conditionals_for_personality_and_system_prompt(isolated_db):
    await set_setting_route(
        "system_prompt_template",
        "HEADER\n{{personality}}\n{{if trigger.type = 'human_chat'}}CHAT{{else}}OTHER{{end}}",
        "advanced",
    )

    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(
        name="Taylor",
        role="Operations Analyst",
        prompt_template="PERSONA {{if trigger.type = 'human_chat'}}HUMAN{{else}}OTHER{{end}}",
        desk_x=desk_x,
        desk_y=desk_y,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    human_context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "human_chat",
                "content": "What is your status?",
                "from_name": "Human Operator",
            },
            contract_kind="decision",
        )
    )
    peer_context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "peer_message",
                "content": "Need a quick update.",
                "from_name": "Morgan",
                "from_agent": "agent-morgan",
            },
            contract_kind="decision",
        )
    )

    assert "PERSONA HUMAN" in human_context[0]["content"]
    assert "CHAT" in human_context[0]["content"]
    assert "PERSONA OTHER" in peer_context[0]["content"]
    assert "OTHER" in peer_context[0]["content"]


@pytest.mark.asyncio
async def test_settings_route_rejects_invalid_system_prompt_template(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await set_setting_route("system_prompt_template", "{{missing_value}}", "advanced")

    assert exc_info.value.status_code == 400
    assert "template variable" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_runtime_contract_save_rejects_invalid_template(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_contracts_route(
            RuntimeContractsBody(
                decision="{{missing_value}}",
                execution="EXECUTION",
            )
        )

    assert exc_info.value.status_code == 400
    assert "template variable" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_runtime_contract_preview_supports_unsaved_template_override(isolated_db):
    payload = await preview_runtime_contract_route(
        RuntimeContractPreviewBody(
            contract_kind="decision",
            trigger_type="task_assigned",
            template="{{if trigger.type = 'task_assigned'}}ASSIGNMENT PREVIEW{{else}}OTHER PREVIEW{{end}}",
        )
    )

    assert payload["contract_kind"] == "decision"
    assert payload["trigger_type"] == "task_assigned"
    assert payload["rendered"] == "ASSIGNMENT PREVIEW"


@pytest.mark.asyncio
async def test_create_agent_route_rejects_invalid_prompt_template(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await create_agent_route(
            AgentCreate(
                name="Taylor",
                prompt_template="{{missing_value}}",
            )
        )

    assert exc_info.value.status_code == 400
    assert "template variable" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_create_personality_route_rejects_invalid_prompt_template(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await create_personality_route(
            AIPersonalityCreate(
                name="Ops Personality",
                prompt_template="{{missing_value}}",
            )
        )

    assert exc_info.value.status_code == 400
    assert "template variable" in str(exc_info.value.detail).lower()


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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"decline","intent":"work","msg":"I cannot take this on right now.","commit":"none","th":"decline the assignment"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    monkeypatch.setattr("core.runtime.services.runtime_services.enqueue_trigger", _record_async(queued))

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"accept","intent":"work","msg":"I will take care of the report.","commit":"work","data":{"task":{"title":"Finish the report","desc":"Please finish the report."}},"th":"accept the new work"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"work","data":{"out":"Report body"},"th":"draft"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"done","data":{"sum":"done"},"th":"finished"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"reply","intent":"question","msg":"I like it here.","commit":"none","th":"reply"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"work","data":{"out":"This should never be reached."},"th":"oops"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"Almost done. I need to finish a few more tests.","commit":"none","th":"status"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier.","commit":"none","th":"share grounded status"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am currently working on building the API that generates words from letters.","commit":"none","th":"status update"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier.","commit":"none","th":"share grounded status"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I just finished the task and I am idle now.","commit":"none","th":"status update"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"accept","intent":"meeting","msg":"Sure, I am heading to the meeting room now.","commit":"meeting","data":{"dst":"meeting","title":"Team Sync Meeting","detail":"Join the scheduled team sync meeting."},"th":"accept the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am currently idle at the Main Workspace with no active tasks.","commit":"none","th":"share grounded status"}',
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
async def test_run_turn_status_request_repairs_invalid_decision_name_before_bm_cli(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "How are you doing?", message_type="human")

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am doing well, thanks for asking.","commit":"none","th":"answer the status check"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"status","intent":"status","msg":"I am currently finishing the API tests.","commit":"none","th":"provide status update"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am idle right now at the Main Workspace with no active tasks.","commit":"none","th":"share grounded status"}',
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
            "content": "How are you doing?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 4
    assert any(
        '"status" only in "intent", never in "act"' in message["content"]
        for message in captured_messages[2]
        if message["role"] == "system"
    )
    assert any(
        '{"act":"cli","data":{"cmd":"status"},"th":"check live status"}' in message["content"]
        for message in captured_messages[2]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am idle right now at the Main Workspace with no active tasks."


@pytest.mark.asyncio
async def test_run_turn_status_request_repairs_invalid_decision_name_after_bm_cli(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "How are you doing?", message_type="human")

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am doing well, thanks for asking.","commit":"none","th":"answer the status check"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"status"},"th":"check live status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"status","intent":"status","msg":"I am idle right now.","commit":"none","th":"share grounded status"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am idle right now at the Main Workspace with no active tasks.","commit":"none","th":"share grounded status"}',
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
            "content": "How are you doing?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 4
    assert any(
        '{"act":"reply","intent":"status","msg":"...","commit":"none","th":"..."}' in message["content"]
        for message in captured_messages[3]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am idle right now at the Main Workspace with no active tasks."


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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
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
            content='{"act":"work","data":{"out":"Implemented the endpoint scaffold."},"th":"progress"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    call_count = 0
    responses = iter([
        client.LLMResponse(
            content="{\"act\":\"msg\",\"data\":{\"to\":\"human\",\"msg\":\"Sure, let's discuss the project timeline.\"},\"th\":\"reply in the room\"}",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"walk","data":{"dst":"desk"},"th":"leave the room"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    call_count = 0
    responses = iter([
        client.LLMResponse(
            content='{"act":"mtg","data":{"mode":"room","topic":"Project timeline"},"th":"join the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"work","data":{"out":"This should never be reached."},"th":"oops"}',
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
    assert [msg.message_type for msg in thread] == ["human"]
    notifications = db.list_notifications(agent_id=agent.id, limit=10, chat_visible=True)
    assert notifications[0].content == "Taylor joined the meeting."
    assert notifications[0].kind == "receipt"

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"mtg","data":{"mode":"room","topic":"Project planning"},"th":"join the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"walk","data":{"dst":"meeting"},"th":"Need to walk there first."}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"accept","intent":"move","msg":"I am heading to the meeting room now.","commit":"conversation","data":{"dst":"meeting","title":"Direct conversation","detail":"Continue the direct conversation in the meeting room."},"th":"accept the move request"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"walk","data":{"dst":"meeting"},"th":"Head to the meeting room."}',
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


def test_prompt_history_view_excludes_non_prompt_visible_notifications(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.create_message(HUMAN_SENDER_ID, agent.id, "Head to the meeting room.", message_type="human")
    db.create_notification(
        agent_id=agent.id,
        kind="receipt",
        content="Taylor is heading to the Meeting Room.",
        source_channel="chat",
        policy="all",
        chat_visible=True,
        prompt_visibility=False,
    )
    activity_runtime.start_conversation_activity(
        agent.id,
        title="Direct conversation",
        detail="Head to the meeting room.",
    )

    view = build_prompt_history_view(
        agent,
        {"type": "activity_resumed", "source_channel": "chat"},
        token_model="test-model",
    )

    assert [msg["content"] for msg in view.conversation_history] == ["Head to the meeting room."]
    assert view.prompt_notifications == []


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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","commit":"none","th":"nothing to say"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"done","data":{"sum":"done"},"th":"finished"}',
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
    parsed = parse_action('{"act":"walk","data":{"dst":"desk"},"th":"move"}')
    assert parsed["action"] == "walkTo"


def test_parse_action_accepts_attend_meeting_minimal_payload(isolated_db):
    parsed = parse_action('{"act":"mtg","data":{"mode":"room","topic":"sync"},"th":"join"}')
    assert parsed["action"] == "attendMeeting"


def test_parse_action_accepts_remote_meeting_payload(isolated_db):
    parsed = parse_action('{"act":"mtg","data":{"mode":"remote","aid":"agent-123","topic":"sync"},"th":"join remotely"}')
    assert parsed["action"] == "remoteMeeting"
    assert parsed["agentId"] == "agent-123"


def test_parse_action_rejects_removed_start_task_action(isolated_db):
    parsed = parse_action('{"act":"startTask","data":{"description":"task details"},"th":"formalize"}')
    assert parsed["action"] == "_parse_failed"
    assert "unexpected data keys" in parsed["_raw_snippet"]


def test_parse_action_requires_explicit_message_recipient_contract(isolated_db):
    parsed = parse_action('{"act":"msg","data":{"msg":"hi"},"th":"reply"}')
    assert parsed["action"] == "_parse_failed"
    assert "recipientType" in parsed["_raw_snippet"]


def test_parse_action_rejects_task_id_for_complete(isolated_db):
    parsed = parse_action('{"act":"done","data":{"taskId":"api_bug","sum":"done"},"th":"finished"}')
    assert parsed["action"] == "_parse_failed"
    assert "unexpected data keys" in parsed["_raw_snippet"]


def test_parse_action_accepts_delegate_task(isolated_db):
    parsed = parse_action(
        '{"act":"assign","data":{"aid":"agent-123","task":{"title":"Review API logs","desc":"Inspect failures and summarize the root cause.","outs":[{"type":"file","path":"review.md"}]}},"th":"delegate the follow-up"}'
    )
    assert parsed["action"] == "delegateTask"
    assert parsed["taskTitle"] == "Review API logs"
    assert parsed["deliverables"] == [{"type": "file", "path": "review.md", "description": None}]


def test_validate_decision_requires_task_title_for_direct_work_accept(isolated_db):
    parsed = parse_decision(
        '{"act":"accept","intent":"work","msg":"I will do it.","commit":"work","th":"accept"}'
    )
    error = validate_decision_for_trigger(
        ConversationDecision.model_validate(parsed),
        trigger_type="human_chat",
        active_task_id=None,
    )
    assert error is not None
    assert "taskTitle" in error


def test_validate_decision_rejects_peer_message_work_commitment(isolated_db):
    decision = parse_decision(
        '{"act":"accept","intent":"work","msg":"I will take it.","commit":"work","data":{"task":{"title":"Review API logs","desc":"Inspect failures."}},"th":"accept the task"}'
    )
    error = validate_decision_for_trigger(
        ConversationDecision.model_validate(decision),
        trigger_type="peer_message",
        active_task_id=None,
    )
    assert error is not None
    assert "peer messages are conversational only" in error


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
    assert result["trigger_requests"][0]["payload"]["message_type"] == "social"
    assert result["trigger_requests"][0]["source_channel"] == "chat"


@pytest.mark.asyncio
async def test_delegate_task_action_creates_assignment_for_other_agent(isolated_db):
    desk_x, desk_y = _desk_xy()
    delegator = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    target = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Plan rollout",
        description="Coordinate the release",
        assigned_to=delegator.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_agent_cli_state(delegator.id, cwd="/projects/release/reports")
    state = _activate_work(delegator, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "delegateTask",
            "agentId": target.id,
            "taskTitle": "Review rollout checklist",
            "taskDescription": "Review the checklist and save notes to checklist_review.md.",
            "deliverables": [{"type": "file", "path": "checklist_review.md"}],
            "thought": "delegate the checklist review",
        },
        delegator,
        state,
        trigger={"type": "activity_resumed", "source_channel": "work"},
    )

    assert result["trigger_requests"][0]["agent_id"] == target.id
    assert result["trigger_requests"][0]["trigger_type"] == "task_assigned"

    tasks = db.list_tasks(assigned_to=target.id)
    assert len(tasks) == 1
    delegated = tasks[0]
    assert delegated.created_by == delegator.id
    assert delegated.parent_task_id == parent.id
    assert delegated.work_contract is not None
    assert [item.model_dump() for item in delegated.work_contract.deliverables] == [
        {"type": "file", "path": "/projects/release/reports/checklist_review.md", "description": None}
    ]


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
async def test_attend_meeting_creates_shared_session_and_join_message(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=20, y=4, status="work_active")

    result = await execute_action(
        {
            "action": "attendMeeting",
            "topic": "Weekly sync",
            "thought": "join in person",
        },
        agent,
        state,
    )

    session = db.get_active_meeting_session_for_agent(agent.id)
    assert session is not None
    assert session.room_id == "meeting_room"
    assert session.title == "Weekly sync"
    messages = db.list_meeting_session_messages(session.id, limit=10)
    assert len(messages) == 1
    assert messages[0].author_type == "system"
    assert messages[0].content == "Taylor joined the meeting."
    assert result["meeting_message"]["session_id"] == session.id
    assert result["meeting_message"]["content"] == "Taylor joined the meeting."


@pytest.mark.asyncio
async def test_meeting_session_route_and_human_message_fanout(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    morgan = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)

    taylor_state = db.update_agent_state(taylor.id, x=20, y=4, status="work_active")
    morgan_state = db.update_agent_state(morgan.id, x=20, y=4, status="work_active")

    await execute_action({"action": "attendMeeting", "topic": "Planning", "thought": "join"}, taylor, taylor_state)
    await execute_action({"action": "attendMeeting", "topic": "Planning", "thought": "join"}, morgan, morgan_state)

    broadcasted: list[dict[str, object]] = []
    queued: list[dict] = []

    async def _record_meeting_message(**kwargs):
        broadcasted.append(kwargs)

    monkeypatch.setattr(manager, "broadcast_meeting_message", _record_meeting_message)
    monkeypatch.setattr("core.runtime.services.runtime_services.enqueue_trigger", _record_async(queued))

    session_payload = await get_agent_meeting_session(taylor.id)
    assert session_payload["active"] is True
    session = session_payload["session"]
    assert session["title"] == "Planning"
    assert {item["name"] for item in session["participants"]} == {"Taylor", "Morgan"}

    result = await create_agent_meeting_session_message(
        taylor.id,
        MeetingMessageBody(content="Let's align on the plan."),
    )

    assert result["status"] == "ok"
    assert result["participant_count"] == 2
    assert broadcasted[0]["session_id"] == session["id"]
    assert broadcasted[0]["author_type"] == "human"
    assert broadcasted[0]["content"] == "Let's align on the plan."

    assert len(queued) == 2
    payloads = [item["payload"] for item in queued]
    assert all(item["session_id"] == session["id"] for item in payloads)
    assert all(item["round_id"] for item in payloads)
    assert all(item["author_type"] == "human" for item in payloads)
    assert all(item["content"] == "Let's align on the plan." for item in payloads)

    round_id = payloads[0]["round_id"]
    candidates = db.list_meeting_response_candidates(round_id)
    assert {item.agent_id for item in candidates} == {taylor.id, morgan.id}
    assert all(item.status == "pending" for item in candidates)

    refreshed = await get_agent_meeting_session(taylor.id)
    transcript = refreshed["session"]["messages"]
    assert transcript[-1]["content"] == "Let's align on the plan."
    assert transcript[-1]["author_type"] == "human"


@pytest.mark.asyncio
async def test_session_message_first_responder_answers_immediately(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=20, y=4, status="work_active")
    session = db.ensure_room_meeting_session("meeting_room", title="Planning", created_by_agent_id=taylor.id)
    source = db.create_meeting_session_message(
        session_id=session.id,
        author_type="human",
        author_name="Human Operator",
        content="Taylor, can you answer this?",
        source_channel="meeting",
    )
    round_record = db.create_meeting_response_round(session_id=session.id, source_message_id=source.id)
    db.create_meeting_response_candidate(round_id=round_record.id, agent_id=taylor.id)

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_meeting_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am wrapping up the planning notes.","commit":"none","th":"answer the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        taylor,
        state,
        {
            "type": "session_message",
            "content": source.content,
            "session_id": session.id,
            "round_id": round_record.id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": source.id,
            "source_channel": "chat",
        },
    )

    assert outcome.result["meeting_message"]["content"] == "I am wrapping up the planning notes."
    candidate = db.get_meeting_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    assert candidate is not None
    assert candidate.status == "responded"
    assert candidate.queue_position == 1


@pytest.mark.asyncio
async def test_session_response_serializes_and_advances_queue(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    joe = db.create_agent(name="Joe", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    taylor_state = db.update_agent_state(taylor.id, x=20, y=4, status="work_active")
    joe_state = db.update_agent_state(joe.id, x=20, y=4, status="work_active")
    session = db.ensure_room_meeting_session("meeting_room", title="Planning", created_by_agent_id=taylor.id)
    source = db.create_meeting_session_message(
        session_id=session.id,
        author_type="human",
        author_name="Human Operator",
        content="Taylor and Joe, what's going on?",
        source_channel="meeting",
    )
    round_record = db.create_meeting_response_round(session_id=session.id, source_message_id=source.id)
    db.create_meeting_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    db.create_meeting_response_candidate(round_id=round_record.id, agent_id=joe.id)
    db.update_meeting_response_candidate(round_id=round_record.id, agent_id=taylor.id, status="responding", queue_position=1)
    db.update_meeting_response_candidate(round_id=round_record.id, agent_id=joe.id, status="queued", queue_position=2)

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_meeting_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am wrapping up the planning notes.","commit":"none","th":"answer the meeting"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        taylor,
        taylor_state,
        {
            "type": "session_response",
            "content": source.content,
            "session_id": session.id,
            "round_id": round_record.id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": source.id,
            "source_channel": "chat",
        },
    )

    assert outcome.result["meeting_message"]["content"] == "I am wrapping up the planning notes."
    next_trigger = outcome.result["trigger_requests"][0]
    assert next_trigger["trigger_type"] == "session_response"
    assert next_trigger["agent_id"] == joe.id

    taylor_candidate = db.get_meeting_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    joe_candidate = db.get_meeting_response_candidate(round_id=round_record.id, agent_id=joe.id)
    assert taylor_candidate is not None and taylor_candidate.status == "responded"
    assert joe_candidate is not None and joe_candidate.status == "responding"

    transcript = db.list_meeting_session_messages(session.id, limit=10)
    assert transcript[-1].author_name == "Taylor"
    assert transcript[-1].content == "I am wrapping up the planning notes."


@pytest.mark.asyncio
async def test_channel_routes_and_human_message_fanout(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    joe = db.create_agent(name="Joe", desk_x=desk_x, desk_y=desk_y)

    broadcasted: list[dict[str, object]] = []
    updated: list[dict[str, object]] = []
    queued: list[dict] = []

    async def _record_channel_message(**kwargs):
        broadcasted.append(kwargs)

    async def _record_channel_updated(channel):
        updated.append(channel)

    monkeypatch.setattr(manager, "broadcast_channel_message", _record_channel_message)
    monkeypatch.setattr(manager, "broadcast_channel_updated", _record_channel_updated)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr("core.runtime.services.runtime_services.enqueue_trigger", _record_async(queued))

    created = await create_channel_route(
        ChannelCreateBody(agent_ids=[taylor.id, joe.id]),
    )
    assert created["member_count"] == 2

    detail = await get_channel_route(created["id"])
    assert detail["channel"]["name"]
    assert {item["name"] for item in detail["channel"]["members"]} == {"Taylor", "Joe"}

    result = await create_channel_message_route(
        created["id"],
        ChannelMessageBody(content="Taylor and Joe, what is your status?"),
    )
    assert result["status"] == "ok"
    assert result["member_count"] == 2
    assert broadcasted[0]["channel_id"] == created["id"]
    assert broadcasted[0]["author_type"] == "human"

    assert len(queued) == 2
    payloads = [item["payload"] for item in queued]
    assert all(item["channel_id"] == created["id"] for item in payloads)
    assert all(item["round_id"] for item in payloads)
    assert all(item["author_type"] == "human" for item in payloads)

    round_id = payloads[0]["round_id"]
    candidates = db.list_channel_response_candidates(round_id)
    assert {item.agent_id for item in candidates} == {taylor.id, joe.id}
    assert all(item.status == "pending" for item in candidates)
    assert updated[-1]["id"] == created["id"]
    assert updated[-1]["latest_message"]["content"] == "Taylor and Joe, what is your status?"


@pytest.mark.asyncio
async def test_channel_message_first_responder_answers_immediately(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")
    channel = db.create_channel(name="Planning", member_agent_ids=[taylor.id], created_by=HUMAN_SENDER_ID)
    source = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Taylor, can you answer this?",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(channel_id=channel.id, source_message_id=source.id)
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_meeting_message", _noop)
    monkeypatch.setattr(manager, "broadcast_channel_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am wrapping up the planning notes.","commit":"none","th":"answer the channel"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        taylor,
        state,
        {
            "type": "channel_message",
            "content": source.content,
            "channel_id": channel.id,
            "round_id": round_record.id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": source.id,
            "source_channel": "channel",
        },
    )

    assert outcome.result["channel_message"]["content"] == "I am wrapping up the planning notes."
    candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    assert candidate is not None
    assert candidate.status == "responded"
    assert candidate.queue_position == 1


@pytest.mark.asyncio
async def test_channel_response_serializes_and_advances_queue(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    joe = db.create_agent(name="Joe", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    taylor_state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")
    channel = db.create_channel(name="Planning", member_agent_ids=[taylor.id, joe.id], created_by=HUMAN_SENDER_ID)
    source = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Taylor and Joe, what's going on?",
        source_channel="channel",
    )
    round_record = db.create_channel_response_round(channel_id=channel.id, source_message_id=source.id)
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    db.create_channel_response_candidate(round_id=round_record.id, agent_id=joe.id)
    db.update_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id, status="responding", queue_position=1)
    db.update_channel_response_candidate(round_id=round_record.id, agent_id=joe.id, status="queued", queue_position=2)

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_meeting_message", _noop)
    monkeypatch.setattr(manager, "broadcast_channel_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am wrapping up the planning notes.","commit":"none","th":"answer the channel"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        taylor,
        taylor_state,
        {
            "type": "channel_response",
            "content": source.content,
            "channel_id": channel.id,
            "round_id": round_record.id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": source.id,
            "source_channel": "channel",
        },
    )

    assert outcome.result["channel_message"]["content"] == "I am wrapping up the planning notes."
    next_trigger = outcome.result["trigger_requests"][0]
    assert next_trigger["trigger_type"] == "channel_response"
    assert next_trigger["agent_id"] == joe.id

    taylor_candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    joe_candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=joe.id)
    assert taylor_candidate is not None and taylor_candidate.status == "responded"
    assert joe_candidate is not None and joe_candidate.status == "responding"

    transcript = db.list_channel_messages(channel.id, limit=10)
    assert transcript[-1].author_name == "Taylor"
    assert transcript[-1].content == "I am wrapping up the planning notes."


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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"accept","intent":"move","msg":"I am heading back to my desk.","commit":"conversation","data":{"dst":"desk","title":"Desk conversation","detail":"Return to the desk for a direct conversation."},"th":"accept the relocation"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"accept","intent":"meeting","msg":"I am on my way to the meeting room.","commit":"meeting","data":{"dst":"meeting","title":"Direct meeting","detail":"Meet with the human operator in the meeting room."},"th":"accept the meeting"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"accept","intent":"work","msg":"I will fix the API bug next.","commit":"work","data":{"task":{"title":"Fix the API bug","desc":"please fix the API bug"}},"th":"accept the task"}',
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


@pytest.mark.asyncio
async def test_work_acceptance_at_desk_does_not_set_desk_preference(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "please write the summary",
        message_type="human",
    )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"accept","intent":"work","msg":"I will write the summary now.","commit":"work","data":{"task":{"title":"Write summary","desc":"please write the summary"}},"th":"accept the task"}',
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
            "content": "please write the summary",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 1
    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.metadata.get("preferred_destination") is None


@pytest.mark.asyncio
async def test_bm_cli_write_registers_artifact_and_desk_view_can_open_it(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.get_agent_state(agent.id)
    assert state is not None

    result = execute_bm_cli(
        agent,
        state,
        "write /me/test_report.md",
        "artifact body",
        trigger_type="human_chat",
    )
    assert result.ok is True

    artifacts = db.list_artifacts(agent_id=agent.id)
    assert len(artifacts) == 1
    assert artifacts[0].virtual_path == "/me/test_report.md"
    assert artifacts[0].category == "output"

    root_payload = await get_agent_desk(agent.id, path="/")
    assert root_payload["kind"] == "directory"
    root_paths = {entry["path"] for entry in root_payload["entries"]}
    assert root_paths == {"/me", "/projects"}

    desk_payload = await get_agent_desk(agent.id, path="/me")
    assert desk_payload["kind"] == "directory"
    output_paths = {entry["path"] for entry in desk_payload["entries"]}
    assert "/me/test_report.md" in output_paths
    output_names = {entry["name"] for entry in desk_payload["entries"]}
    assert ".git" not in output_names
    assert ".gitignore" not in output_names

    file_payload = await get_agent_desk(agent.id, path="/me/test_report.md")
    assert file_payload["kind"] == "file"
    assert file_payload["artifact"]["virtual_path"] == "/me/test_report.md"
    assert "artifact body" in file_payload["content"]


@pytest.mark.asyncio
async def test_open_agent_desk_folder_reveals_parent_directory_for_file(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.get_agent_state(agent.id)
    assert state is not None
    execute_bm_cli(agent, state, "write /me/test_report.md", "artifact body", trigger_type="human_chat")

    opened: list[str] = []
    monkeypatch.setattr("api.routes.config.get", lambda key: "thunar" if key == "desktop_open_folder_handler" else None)
    monkeypatch.setattr("api.routes._launch_file_explorer", lambda path, *, opener: opened.append(f"{opener}:{path}"))

    result = await open_agent_desk_folder(agent.id, path="/me/test_report.md")

    assert result["status"] == "ok"
    assert opened == [f"thunar:{agent_artifact_dir(agent.storage_key)}"]


def test_file_explorer_command_prefers_real_linux_file_manager(monkeypatch):
    monkeypatch.setattr("api.routes.sys.platform", "linux")
    monkeypatch.setattr(
        "api.routes.shutil.which",
        lambda binary: f"/usr/bin/{binary}" if binary == "thunar" else None,
    )

    command = _file_explorer_command(Path("/tmp/demo"), opener="thunar")

    assert command == ["thunar", "/tmp/demo"]


@pytest.mark.asyncio
async def test_open_agent_desk_folder_requires_chooser_when_unconfigured(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.get_agent_state(agent.id)
    assert state is not None
    execute_bm_cli(agent, state, "write /me/test_report.md", "artifact body", trigger_type="human_chat")
    monkeypatch.setattr("api.routes.config.get", lambda key: None)
    monkeypatch.setattr(
        "api.routes._available_folder_opener_options",
        lambda: [{"value": "thunar", "label": "Thunar", "description": "Open folders with Thunar."}],
    )

    with pytest.raises(HTTPException) as exc_info:
        await open_agent_desk_folder(agent.id, path="/me/test_report.md")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "desk_open_folder_handler_required"


@pytest.mark.asyncio
async def test_agent_personal_storage_normalization_preserves_me_after_rename(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    legacy_root = legacy_agent_artifact_dir(agent.name)
    legacy_file = legacy_root / "notes" / "handoff.md"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text("legacy handoff note", encoding="utf-8")
    db.upsert_artifact(
        agent_id=agent.id,
        task_id=None,
        virtual_path="/me/notes/handoff.md",
        absolute_path=str(legacy_file.resolve()),
        title="handoff.md",
        kind="file",
        category="note",
        size_bytes=legacy_file.stat().st_size,
        source_command="write /me/notes/handoff.md",
    )

    db.update_agent(agent.id, name="Taylor Renamed")
    renamed_agent = db.get_agent(agent.id)
    assert renamed_agent is not None

    normalize_agent_personal_storage(renamed_agent)

    normalized_file = agent_artifact_dir(renamed_agent.storage_key) / "notes" / "handoff.md"
    assert normalized_file.exists()
    assert normalized_file.read_text(encoding="utf-8") == "legacy handoff note"
    assert not legacy_root.exists()
    assert db.get_artifact_by_absolute_path(str(legacy_file.resolve())) is None
    normalized_artifact = db.get_artifact_by_absolute_path(str(normalized_file.resolve()))
    assert normalized_artifact is not None
    assert normalized_artifact.virtual_path == "/me/notes/handoff.md"

    desk_payload = await get_agent_desk(agent.id, path="/me/notes/handoff.md")
    assert desk_payload["kind"] == "file"
    assert desk_payload["artifact"]["virtual_path"] == "/me/notes/handoff.md"
    assert "legacy handoff note" in desk_payload["content"]


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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"accept","intent":"work","msg":"I will switch to the new sentence API now.","commit":"work","data":{"task":{"title":"Build the new sentence API","desc":"We need to make a new API that generates random sentences using letters."}},"th":"accept the new assignment"}',
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
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
    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_world_state", lambda: _record_world_update(world_updates))
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
async def test_social_trigger_message_to_peer_stays_social(isolated_db):
    desk_x, desk_y = _desk_xy()
    sender = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    recipient = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(sender.id, status="idle", x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "message",
            "recipientType": "agent",
            "agentId": recipient.id,
            "content": "Hey Jason! How's your day going?",
        },
        sender,
        state,
        trigger={"type": "social", "content": "Start a casual chat"},
    )

    queued = result["trigger_requests"][0]
    assert queued["source_channel"] == "chat"
    assert queued["payload"]["message_type"] == "social"

    thread = db.get_agent_direct_thread(sender.id, recipient.id, limit=10)
    assert thread[-1].message_type == "social"


def test_peer_message_social_reply_stays_social(isolated_db):
    desk_x, desk_y = _desk_xy()
    sender = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    recipient = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(sender.id, status="idle", x=desk_x, y=desk_y)

    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "social_request",
            "reply": "Doing well over here.",
            "commitmentKind": "none",
            "thought": "social reply",
        },
        sender,
        state,
        {
            "type": "peer_message",
            "content": "Hey Taylor! How are you doing?",
            "from_agent": recipient.id,
            "from_name": recipient.name,
            "message_type": "social",
            "source_channel": "chat",
        },
    )

    queued = result["trigger_requests"][0]
    assert queued["source_channel"] == "chat"
    assert queued["payload"]["message_type"] == "social"
    assert db.list_tasks(assigned_to=sender.id) == []


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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    db.create_notification(
        agent_id=agent.id,
        kind="completion",
        content='Taylor finished "Task".',
        source_channel="chat",
        policy="completion_blocked",
        chat_visible=True,
        prompt_visibility=True,
    )

    monkeypatch.setattr(manager, "broadcast_chat_reset", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

    result = await clear_agent_chat_history(agent.id)

    assert result["deleted_messages"] == 2
    assert result["deleted_notifications"] == 1
    assert db.get_human_chat_thread(agent.id, limit=20) == []
    assert db.list_notifications(agent_id=agent.id, limit=20) == []
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

    monkeypatch.setattr("core.runtime.services.runtime_services.reset_agent_runtime", _noop)
    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

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
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)

    await watchdog._check_tasks()

    assert queued == []
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.watchdog_pinged_at is None
