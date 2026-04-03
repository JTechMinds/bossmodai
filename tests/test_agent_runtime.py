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
    CliPolicySimulateBody,
    MeetingMessageBody,
    RuntimeContractPreviewBody,
    RuntimeContractTemplateOverridesBody,
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
    get_channel as get_channel_route,
    get_agent_desk,
    get_agent_meeting_session,
    get_agent_messages,
    get_agent_notifications,
    get_task_board,
    get_task_events,
    get_runtime_contracts,
    get_runtime_state as get_runtime_state_route,
    open_agent_desk_folder,
    preview_runtime_contract as preview_runtime_contract_route,
    reset_runtime_contracts,
    seed_cli_policy_rules,
    reseed_application,
    reset_setting_to_default,
    reset_agent_runtime,
    set_setting as set_setting_route,
    set_runtime_contracts as set_runtime_contracts_route,
    set_runtime_state as set_runtime_state_route,
    simulate_cli_policy,
    test_connection as run_connection_test,
)
from api.websocket import manager
from core import config
from core.bm_cli import execute_approved_command, execute_bm_cli
from core.bm_cli.filesystem import agent_artifact_dir, legacy_agent_artifact_dir, project_artifact_dir
from core.bm_cli.types import BossModCliResult
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.managed_writer import run_managed_batch_write, run_managed_write
from core.file_explorer import build_command as build_file_explorer_command
from core.agent_loop.action_contract import render_action_contract
from core.agent_loop.communication import build_communication_snapshot
from core.agent_loop.decision_contract import (
    ConversationDecision,
    parse_decision,
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
from core.default_prompts import load_default_personality_prompt
from core.llm import client, context_builder, routing
from core.messaging import route_human_dm
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


def _bundle_token_total(messages: list[dict[str, str]], *, model: str = "gpt-4o-mini") -> int:
    total = 0
    for message in messages:
        total += client.count_tokens(message.get("content", ""), model=model)
    return total


def test_init_db_removes_obsolete_action_contract_setting(isolated_db):
    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}

    assert "action_contract_template" not in advanced_settings
    assert advanced_settings["system_prompt_template"] == settings_store.SYSTEM_PROMPT_TEMPLATE
    assert advanced_settings["runtime_contract_decision"] == settings_store.RUNTIME_CONTRACT_DECISION_TEMPLATE
    assert advanced_settings["runtime_contract_execution"] == settings_store.RUNTIME_CONTRACT_EXECUTION_TEMPLATE
    assert advanced_settings["runtime_block_trigger_event"] == settings_store.RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE
    assert advanced_settings["runtime_block_conversation_envelope"] == settings_store.RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE
    assert advanced_settings["runtime_block_file_deliverable_guidance"] == settings_store.RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE
    assert advanced_settings["runtime_block_communication_snapshot"] == settings_store.RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE
    assert advanced_settings["runtime_control_state"] == settings_store.RUNTIME_CONTROL_STATE


def test_init_db_prunes_obsolete_action_contract_setting(isolated_db):
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) VALUES ($1, $2, $3, now())",
        ["action_contract_template", "obsolete-value", "advanced"],
    )

    db.init_db()

    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}
    assert "action_contract_template" not in advanced_settings


def test_init_db_seeds_llm_timeout_and_managed_writer_limits(isolated_db):
    llm_settings = {item.key: item.value for item in db.get_settings("llm")}
    advanced_settings = {item.key: item.value for item in db.get_settings("advanced")}

    assert llm_settings["llm_request_timeout_seconds"] == "120"
    assert llm_settings["managed_writer_max_batch_files"] == "8"
    assert llm_settings["managed_writer_max_sections_per_file"] == "8"
    assert advanced_settings["cli_max_read_lines"] == "200"


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


def test_parse_action_accepts_compact_task_message(isolated_db):
    parsed = parse_action(
        '{"act":"taskmsg","data":{"tid":"task-123","msg":"Please tighten the summary."},"th":"continue the task thread"}'
    )
    assert parsed["action"] == "taskMessage"
    assert parsed["taskId"] == "task-123"
    assert parsed["content"] == "Please tighten the summary."


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


def test_parse_decision_accepts_compact_work_delegation_plan(isolated_db):
    parsed = parse_decision(
        '{"act":"accept","intent":"work","msg":"I will get Taylor started.","commit":"work","data":{"task":{"title":"Coordinate avocado whitepaper","desc":"Own the delivery and report back."},"plan":{"mode":"delegate","children":[{"who":"Taylor","task":{"title":"Write avocado whitepaper","desc":"Draft the avocado whitepaper.","outs":[{"type":"file","path":"/me/avocado_white.md"}]}}]}},"th":"accept and delegate"}'
    )
    assert parsed["decision"] == "accept"
    assert parsed["executionPlan"]["mode"] == "delegate"
    assert parsed["executionPlan"]["delegations"] == [
        {
            "agentId": None,
            "agentName": "Taylor",
            "taskTitle": "Write avocado whitepaper",
            "taskDescription": "Draft the avocado whitepaper.",
            "deliverables": [{"type": "file", "path": "/me/avocado_white.md", "description": None}],
        }
    ]


def test_parse_decision_allows_reply_without_commit_or_data(isolated_db):
    parsed = parse_decision(
        '{"act":"reply","intent":"status","msg":"I am drafting the paper now.","th":"share status"}'
    )
    assert parsed["decision"] == "answer"
    assert parsed["intentKind"] == "status_request"
    assert parsed["reply"] == "I am drafting the paper now."
    assert parsed["commitmentKind"] == "none"


def test_render_decision_contract_scopes_human_chat_choices(isolated_db):
    contract = context_builder.preview_runtime_contract("decision", "human_chat")
    assert "Choose the smallest valid object for this turn. Omit unrelated fields." in contract
    assert "If the snapshot already answers the question, reply directly instead of using CLI." in contract
    assert "Decline unsupported or out-of-scope requests cleanly instead of pretending you can do them." in contract
    assert "Use only facts that are present in the snapshot or verified by CLI / document inspection." in contract
    assert "If a task, artifact, teammate update, meeting, or tool result is not known, clarify or check first." in contract
    assert "ALLOWED conversation act FOR THIS TURN: reply | accept | clarify | cancel | decline | defer" in contract
    assert "OPTIONAL LOOKUP ACT FOR ANY DECISION TURN" in contract
    assert "You may use more than one CLI lookup in the same decision turn" in contract
    assert "Once you have enough information, end the turn with a final conversation decision object." in contract
    assert "Use one of these shapes:" in contract
    assert '{"act":"reply","intent":"question | status | social | other","msg":"string","th":"string"}' in contract
    assert '{"act":"cancel","intent":"work","msg":"string","th":"string"}' in contract
    assert '{"act":"defer","intent":"work | other","msg":"string","commit":"work","th":"string"}' in contract
    assert '"plan":{"mode":"self | delegate | mixed"' in contract
    assert 'For `reply`, `clarify`, `cancel`, `decline`, and `observe`, leave `commit` out.' in contract
    assert '`status` belongs in `intent`, never in `act`.' in contract
    assert "If the replacement is explicit, accept the new work; the runtime will pause the older task automatically." in contract
    assert "If it is unclear whether the current task should continue or be replaced, ask a clarifying question before switching tasks." in contract
    assert "If a human clearly says to stop the current active task without replacing it, use `cancel`." in contract
    assert "Treat revisions to finished work as new follow-up work, not as if the completed task were still active." in contract
    assert "Distinguish active work from completed work; prior-work questions do not replace the current active task." in contract
    assert "If the user gives a save or read path, use it." in contract
    assert "If a shared project path is not known yet, clarify before choosing one." in contract
    assert "For self-owned reports or notes without project context, prefer `/me/...`." in contract
    assert "Prefer the existing folder structure when it is already visible." in contract
    assert "If the location is still ambiguous after inspection, clarify before saving." in contract
    assert "If a requester asks you to get another teammate moving on work" not in contract
    assert "Keep teammate autonomy, routing mechanics, and similar internal coordination caveats out of stakeholder-facing replies." not in contract
    assert 'current cwd is `"/me"`' not in contract
    assert "current cwd is `/" in contract
    assert "default save root for this turn is `/me`" in contract
    assert "For more details, view the document itself." in contract
    assert "`cat <path>` for short files" in contract
    assert "`ol <path>` for longer markdown files" in contract
    assert "`rr <path> <start:end>` for a targeted section" in contract
    assert 'If another teammate will do the deliverable you are promising, include that child task in `data.plan.children` on the same accept decision.' in contract
    assert "For a pure coordination handoff, keep the parent task focused on coordination and put the file deliverable on the delegated child task instead of the parent task." in contract
    assert '{"act":"observe","intent":"other","th":"string"}' not in contract


def test_render_decision_contract_scopes_watchdog_status_ping_choices(isolated_db):
    contract = context_builder.preview_runtime_contract("decision", "watchdog_status_ping")
    assert "ALLOWED conversation act FOR THIS TURN: reply" in contract
    assert '{"act":"reply","intent":"status | other","msg":"string","th":"string"}' in contract
    assert '{"act":"accept","intent":"work | meeting | break | move | other"' not in contract
    assert "The runtime will keep the task active and queue work resumption after your reply." in contract


def test_render_action_contract_includes_required_schema(isolated_db):
    contract = render_action_contract()
    assert "REQUIRED JSON SHAPE:" in contract
    assert "FIELD DEFINITIONS:" in contract
    assert '"act": "cli | work | socialmsg | taskmsg | assign | walk | mtg | idle | wait | done | block | deleg | drop"' in contract
    assert '"to": "human | agent"' in contract
    assert '"tid": "string"' in contract
    assert '"kind": "note | status | question | review"' in contract
    assert '"mode": "room | remote"' in contract
    assert "act = the next execution step you are taking" in contract
    assert "data = arguments for that execution step" in contract
    assert "data.body = optional body text or manifest for cli commands that use it" in contract
    assert "cli + bwrite: require data.body as a short manifest with path + goal entries" in contract
    assert "cli + repsect: require data.body as the literal new section body" in contract
    assert "cli + rewsect: require data.body as a short rewrite goal" in contract
    assert "wait: require data.why; use it when the current task stays open but is waiting on another person, review, or external dependency" in contract
    assert "use assign to create new delegated work" in contract
    assert "use taskmsg to continue an existing task thread" in contract
    assert "note = passive comment or acknowledgement" in contract
    assert "questions and review requests ask the runtime to create one response-required task turn" in contract
    assert "during work execution, choose assign for new delegated work and taskmsg for an existing task thread; leave socialmsg for ordinary coworker chat" in contract
    assert "if an open delegated child task owns the deliverable, keep the parent task on coordination/status work; do not finish the parent task until the child task is resolved" in contract
    assert "if the current task stays open but is waiting on delegated work or another dependency, use wait instead of idle" in contract
    assert "short exact text -> write/append with body" in contract
    assert "multiple generated files -> bwrite with a short manifest body" in contract
    assert "inspect markdown structure -> ol <path>" in contract
    assert 'ai-authored markdown section edit -> rewsect <path> "<heading>" with a short goal body' in contract
    assert '{"act":"assign","data":{"aid":"agent-123","task":{"title":"Review API logs","desc":"Inspect failures and summarize the root cause."}},"th":"delegate follow-up"}' in contract
    assert '{"act":"taskmsg","data":{"tid":"task-123","kind":"review","msg":"Please tighten the summary and send it back when ready."},"th":"continue the existing task thread"}' in contract


@pytest.mark.asyncio
async def test_render_decision_contract_uses_saved_runtime_setting(isolated_db):
    await set_setting_route(
        "runtime_contract_decision",
        "{{if trigger.type = 'human_chat'}}CUSTOM HUMAN DECISION{{else}}CUSTOM OTHER DECISION{{end}}",
        "advanced",
    )

    assert context_builder.preview_runtime_contract("decision", "human_chat") == "CUSTOM HUMAN DECISION"
    assert context_builder.preview_runtime_contract("decision", "peer_message") == "CUSTOM OTHER DECISION"


@pytest.mark.asyncio
async def test_render_action_contract_uses_saved_runtime_setting(isolated_db):
    await set_setting_route(
        "runtime_contract_execution",
        "EXEC {{if cli.shell_enabled}}SHELL{{else}}SAFE{{end}}",
        "advanced",
    )

    assert render_action_contract() == "EXEC SAFE"

    await set_setting_route("cli_shell_enabled", "true", "cli_policy")

    assert render_action_contract() == "EXEC SHELL"


def test_render_decision_contract_scopes_task_assignment_choices(isolated_db):
    contract = context_builder.preview_runtime_contract("decision", "task_assigned")
    assert "ALLOWED conversation act FOR THIS TURN: accept | clarify | defer | decline" in contract
    assert "You've been assigned a task. It already exists in the task system." in contract
    assert "`my-board`" in contract
    assert "`task <id>`" in contract
    assert "accept the assignment" in contract
    assert 'Include `commit="work"` when you accept or defer this assignment.' in contract
    assert '{"act":"accept","intent":"work","msg":"string","commit":"work","th":"string"}' in contract
    assert '{"act":"reply","intent":"question | status | social | other","msg":"string","th":"string"}' not in contract


def test_render_decision_contract_scopes_task_follow_up_choices(isolated_db):
    contract = context_builder.preview_runtime_contract(
        "decision",
        "task_follow_up",
        trigger_overrides={"task_status": "pending", "task_party": "assignee"},
    )
    assert "ALLOWED conversation act FOR THIS TURN: accept | clarify | defer | decline" in contract
    assert "TASK ATTENTION NOTE:" in contract
    assert "You already have this task in the task system, and it is still waiting on your decision." in contract
    assert "`my-board`" in contract
    assert "`owned-tasks`" in contract
    assert "`delegated-tasks`" in contract
    assert "`waiting-on-me`" in contract
    assert "`task <id>`" in contract
    assert '{"act":"reply","intent":"question | status | social | other","msg":"string","th":"string"}' not in contract


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


def test_validate_decision_requires_direct_watchdog_reply(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "I am still working through the failing tests.",
            "commitmentKind": "none",
            "thought": "share watchdog status",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="watchdog_status_ping",
        active_task_id="task-123",
    )
    assert error is None

    invalid = ConversationDecision.model_validate(
        {
            "decision": "defer",
            "intentKind": "other",
            "reply": "I will get back to this later.",
            "commitmentKind": "none",
            "thought": "invalid watchdog response",
        }
    )
    error = validate_decision_for_trigger(
        invalid,
        trigger_type="watchdog_status_ping",
        active_task_id="task-123",
    )
    assert error == "watchdog status pings require a direct reply"


def test_validate_decision_cancel_requires_active_work(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "cancel",
            "intentKind": "work_request",
            "reply": "Understood. I am cancelling the active task.",
            "commitmentKind": "none",
            "thought": "close the current work item",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="human_chat",
        active_task_id="task-123",
    )
    assert error is None

    missing_active_error = validate_decision_for_trigger(
        decision,
        trigger_type="human_chat",
        active_task_id=None,
    )
    assert missing_active_error == 'cancel is only valid when there is an active task to close'


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


def test_validate_decision_allows_task_assignment_defer_without_new_title(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "defer",
            "intentKind": "work_request",
            "reply": "I need to finish the payroll audit first, then I can take this on.",
            "commitmentKind": "work",
            "thought": "defer the assignment",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_assigned",
        active_task_id=None,
    )
    assert error is None


def test_validate_decision_task_follow_up_restricts_non_pending_stakeholder_turns(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "I will take it.",
            "commitmentKind": "work",
            "thought": "invalid accept for stakeholder follow-up",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_follow_up",
        active_task_id=None,
        trigger={"task_status": "accepted", "task_party": "stakeholder"},
    )
    assert error == "task follow-up turns only allow reply or clarify unless the pending task is awaiting your decision"

    valid = ConversationDecision.model_validate(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "Understood. I will take it.",
            "commitmentKind": "work",
            "thought": "accept pending follow-up",
        }
    )
    valid_error = validate_decision_for_trigger(
        valid,
        trigger_type="task_follow_up",
        active_task_id=None,
        trigger={"task_status": "pending", "task_party": "assignee"},
    )
    assert valid_error is None


def test_validate_decision_task_follow_up_restricts_pending_assignee_to_real_decision(isolated_db):
    decision = ConversationDecision.model_validate(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "Thanks.",
            "commitmentKind": "none",
            "thought": "acknowledge the pending task",
        }
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_follow_up",
        active_task_id=None,
        trigger={"task_status": "pending", "task_party": "assignee"},
    )
    assert error == "pending task decisions must accept, clarify, defer, or decline the existing task"


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


def test_apply_decision_accept_with_delegation_plan_creates_child_task_before_reply(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "I’ll get Taylor started on the whitepaper and keep the delivery moving.",
            "commitmentKind": "work",
            "taskTitle": "Coordinate edge-device whitepaper",
            "taskDescription": "Own delivery of the edge-device whitepaper and report back to the requester.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [
                    {
                        "agentName": "Taylor",
                        "taskTitle": "Write edge-device whitepaper",
                        "taskDescription": "Write a 3-paragraph whitepaper on the benefits of SLMs on edge devices for social media outreach.",
                        "deliverables": [
                            {"type": "file", "path": "/me/slm-edge-whitepaper.md", "description": "finished delegated whitepaper"}
                        ],
                    }
                ],
            },
            "thought": "accept coordination and create the child task now",
        },
        pm,
        state,
        {
            "type": "human_chat",
            "content": "Can you have Taylor write a 3 paragraph whitepaper on SLMs on edge devices?",
            "from_name": "Human Operator",
            "source_channel": "chat",
        },
    )

    assert result["event"] == "decision_applied"
    assert result["chat_message"]["content"] == "I’ll get Taylor started on the whitepaper and keep the delivery moving."
    assert not any(item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed" for item in result["trigger_requests"])
    assignment_request = next(
        item
        for item in result["trigger_requests"]
        if item["agent_id"] == worker.id and item["trigger_type"] == "task_assigned"
    )

    parent_tasks = db.list_tasks(assigned_to=pm.id)
    assert len(parent_tasks) == 1
    parent = parent_tasks[0]
    assert parent.title == "Coordinate edge-device whitepaper"
    assert parent.work_contract is None
    assert parent.status == "accepted"

    child_tasks = db.list_tasks(assigned_to=worker.id)
    assert len(child_tasks) == 1
    child = child_tasks[0]
    assert child.parent_task_id == parent.id
    assert child.title == "Write edge-device whitepaper"
    assert child.requester_id == pm.id
    assert child.owner_id == pm.id
    assert child.work_contract is not None
    assert [item.model_dump() for item in child.work_contract.deliverables] == [
        {
            "type": "file",
            "path": f"/projects/shared/{child.id}/slm-edge-whitepaper.md",
            "description": "finished delegated whitepaper",
        }
    ]
    assert assignment_request["task_id"] == child.id

    thread = db.get_human_chat_thread(pm.id, limit=10)
    assert thread[-1].content == "I’ll get Taylor started on the whitepaper and keep the delivery moving."


def test_apply_decision_preserves_explicit_absolute_deliverable_path_over_cwd(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    db.update_agent_cli_state(agent.id, cwd="/projects/orchard/reports")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "I will save the personal recap to the requested path.",
            "commitmentKind": "work",
            "taskTitle": "Write personal launch recap",
            "taskDescription": "Draft a personal launch recap and save it.",
            "deliverables": [{"type": "file", "path": "/me/reports/launch_recap.md"}],
            "thought": "use the explicit personal path",
        },
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Write a personal launch recap and save it to /me/reports/launch_recap.md.",
            "from_name": "Human Operator",
            "source_channel": "chat",
        },
    )

    assert result["event"] == "decision_applied"
    task = db.list_tasks(assigned_to=agent.id)[0]
    assert task.work_contract is not None
    assert [item.model_dump() for item in task.work_contract.deliverables] == [
        {"type": "file", "path": "/me/reports/launch_recap.md", "description": None}
    ]


def test_apply_decision_defaults_personal_relative_deliverable_to_current_me_workspace(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    db.update_agent_cli_state(agent.id, cwd="/me/reports")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "reply": "I will draft the recap and keep it in my reports folder.",
            "commitmentKind": "work",
            "taskTitle": "Write personal launch recap",
            "taskDescription": "Draft a personal launch recap and save it.",
            "deliverables": [{"type": "file", "path": "launch_recap.md"}],
            "thought": "use my current personal workspace",
        },
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Write a personal launch recap and save it.",
            "from_name": "Human Operator",
            "source_channel": "chat",
        },
    )

    assert result["event"] == "decision_applied"
    task = db.list_tasks(assigned_to=agent.id)[0]
    assert task.work_contract is not None
    assert [item.model_dump() for item in task.work_contract.deliverables] == [
        {"type": "file", "path": "/me/reports/launch_recap.md", "description": None}
    ]


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
    assert result["trigger_requests"][0]["trigger_type"] == "task_follow_up"
    assert result["trigger_requests"][0]["agent_id"] == assigner.id
    assert result["trigger_requests"][0]["task_id"] == task.id
    assert result["trigger_requests"][0]["payload"]["task_status"] == "pending"
    assert result["trigger_requests"][0]["payload"]["task_party"] == "stakeholder"
    assert result["trigger_requests"][0]["payload"]["attention_kind"] == "clarification_requested"


def test_apply_decision_task_follow_up_clarification_loop_blocks_task_and_stops_retrigger(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(worker.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Write paper",
        description="Draft the paper and send it back.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
    )
    for idx in range(5):
        author = pm if idx % 2 == 0 else worker
        db.create_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=author.id,
            author_name=author.name,
            event_type="clarification",
            content=f"Clarification ping {idx}.",
        )

    result = apply_decision(
        {
            "decision": "clarify",
            "intentKind": "work_request",
            "reply": "What exact format and length do you want for the paper?",
            "commitmentKind": "none",
            "thought": "need missing details",
        },
        worker,
        state,
        {
            "type": "task_follow_up",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "task_status": "pending",
            "task_party": "assignee",
            "from_agent": pm.id,
            "from_name": pm.name,
            "source_channel": "work",
        },
    )

    assert result["detail"] == "Taylor asked for clarification"
    assert not any(item["trigger_type"] == "task_follow_up" for item in result["trigger_requests"])
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "blocked"


def test_apply_decision_task_follow_up_reply_to_assignment_clarification_routes_back_as_task_follow_up(isolated_db):
    desk_x, desk_y = _desk_xy()
    delegator = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    assignee = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(delegator.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Research foundation doc",
        description="Draft the research foundation for the SLM edge white paper.",
        assigned_to=assignee.id,
        requester_id=delegator.id,
        owner_id=delegator.id,
        created_by=delegator.id,
        source_channel="peer",
        notification_policy="none",
    )

    result = apply_decision(
        {
            "decision": "answer",
            "intentKind": "work_request",
            "reply": "Tomorrow EOD works. Focus on the research foundation first and use the existing file path.",
            "commitmentKind": "none",
            "thought": "answer the assignment clarification",
        },
        delegator,
        state,
        {
            "type": "task_follow_up",
            "content": "Can you confirm the angle and due date?",
            "from_agent": assignee.id,
            "from_name": assignee.name,
            "message_type": "work",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "task_status": "pending",
            "task_party": "stakeholder",
            "source_channel": "work",
        },
    )

    assert result["detail"] == "Michael answered the request"
    routed = result["trigger_requests"][0]
    assert routed["agent_id"] == assignee.id
    assert routed["trigger_type"] == "task_follow_up"
    assert routed["task_id"] == task.id
    assert routed["payload"]["task_title"] == task.title
    assert routed["payload"]["task_description"] == task.description
    assert routed["payload"]["task_status"] == "pending"
    assert routed["payload"]["task_party"] == "assignee"
    assert routed["payload"]["attention_kind"] == "decision_needed"
    assert routed["payload"]["content"] == (
        "Tomorrow EOD works. Focus on the research foundation first and use the existing file path."
    )


@pytest.mark.asyncio
async def test_run_turn_assignment_clarification_follow_up_stays_in_assignment_lane(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    delegator = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    assignee = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    delegator_state = db.update_agent_state(delegator.id, x=desk_x, y=desk_y, status="idle")
    assignee_state = db.update_agent_state(assignee.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Research foundation doc",
        description="Draft the research foundation for the SLM edge white paper.",
        assigned_to=assignee.id,
        requester_id=delegator.id,
        owner_id=delegator.id,
        created_by=delegator.id,
        source_channel="peer",
        notification_policy="none",
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
            content='{"act":"clarify","intent":"work","msg":"Can you confirm the angle and due date?","th":"need assignment detail"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"reply","intent":"other","msg":"Tomorrow EOD works. Focus on the research foundation first and use the existing file path.","th":"answer the clarification"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"accept","intent":"work","msg":"Understood. I will produce the research foundation doc at the assigned path and report back when it is ready.","commit":"work","th":"accept the clarified assignment"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    first_outcome = await run_turn(
        assignee,
        assignee_state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "from_agent": delegator.id,
            "from_name": delegator.name,
            "source_channel": "work",
        },
    )

    clarify_trigger = first_outcome.result["trigger_requests"][0]
    assert clarify_trigger["trigger_type"] == "task_follow_up"
    assert clarify_trigger["payload"]["task_status"] == "pending"
    assert clarify_trigger["payload"]["task_party"] == "stakeholder"
    assert clarify_trigger["payload"]["attention_kind"] == "clarification_requested"

    reply_outcome = await run_turn(
        delegator,
        delegator_state,
        {
            **clarify_trigger["payload"],
            "type": "task_follow_up",
            "task_id": clarify_trigger["task_id"],
            "source_channel": clarify_trigger["source_channel"],
        },
    )

    follow_up_trigger = reply_outcome.result["trigger_requests"][0]
    assert follow_up_trigger["trigger_type"] == "task_follow_up"
    assert follow_up_trigger["task_id"] == task.id
    assert follow_up_trigger["payload"]["task_status"] == "pending"
    assert follow_up_trigger["payload"]["task_party"] == "assignee"
    assert follow_up_trigger["payload"]["attention_kind"] == "decision_needed"
    assert follow_up_trigger["payload"]["content"] == (
        "Tomorrow EOD works. Focus on the research foundation first and use the existing file path."
    )

    second_outcome = await run_turn(
        assignee,
        assignee_state,
        {
            **follow_up_trigger["payload"],
            "type": "task_follow_up",
            "task_id": follow_up_trigger["task_id"],
            "source_channel": follow_up_trigger["source_channel"],
        },
    )

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert len(db.list_tasks(assigned_to=assignee.id)) == 1
    assert second_outcome.result["detail"] == 'Taylor accepted work on "Research foundation doc"'

    diagnostics = db.get_diagnostics(agent_id=assignee.id, limit=5)
    assert diagnostics[0]["trigger_type"] == "task_follow_up"


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
    assert task.requester_id == HUMAN_SENDER_ID
    assert task.owner_id == agent.id
    assert task.source_channel == "api"
    assert task.notification_policy == "completion_blocked"


@pytest.mark.asyncio
async def test_create_task_route_reuses_existing_open_workstream(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")

    first = await create_task_route(
        TaskCreate(
            title="Write orchard report",
            description="Draft the orchard report and save it.",
            assigned_to=agent.id,
            project="orchard",
        )
    )
    second = await create_task_route(
        TaskCreate(
            title="Write orchard report",
            description="Draft the orchard report and save it.",
            assigned_to=agent.id,
            project="orchard",
        )
    )

    assert second.id == first.id
    assert len(db.list_tasks(assigned_to=agent.id)) == 1
    events = db.list_task_events(first.id, limit=10)
    assert [event.event_type for event in events] == ["assignment", "system"]


def test_list_tasks_can_filter_by_owner_requester_and_parent(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    other = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y)

    parent = db.create_task(
        title="Launch initiative",
        assigned_to=pm.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=pm.id,
        created_by=HUMAN_SENDER_ID,
    )
    child = db.create_task(
        title="Draft launch checklist",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        parent_task_id=parent.id,
    )
    db.create_task(
        title="Unrelated task",
        assigned_to=other.id,
        requester_id=other.id,
        owner_id=other.id,
        created_by=other.id,
    )

    assert [task.id for task in db.list_tasks(owner_id=pm.id)] == [parent.id, child.id]
    assert [task.id for task in db.list_tasks(requester_id=pm.id)] == [child.id]
    assert [task.id for task in db.list_tasks(parent_task_id=parent.id)] == [child.id]


@pytest.mark.asyncio
async def test_task_board_route_returns_owned_manager_sections(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Launch initiative",
        assigned_to=pm.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=pm.id,
        created_by=HUMAN_SENDER_ID,
        project="orchard",
    )
    child = db.create_task(
        title="Draft launch checklist",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        parent_task_id=parent.id,
        project="orchard",
    )
    db.create_task_event(
        task_id=child.id,
        author_type="agent",
        author_agent_id=pm.id,
        author_name=pm.name,
        event_type="assignment",
        content='Created child task "Draft launch checklist" for delegated work.',
    )

    board = await get_task_board(agent_id=pm.id, scope="owned")

    assert board["scope"] == "owned"
    assert board["sections"]["tasks_i_delegated"][0]["id"] == child.id
    assert board["child_tasks_by_parent"][parent.id][0]["id"] == child.id
    assert board["assignee_rollup"][0]["agent_name"] == "Taylor"
    assert board["sections"]["tasks_waiting_on_me"][0]["id"] == child.id


@pytest.mark.asyncio
async def test_task_events_route_returns_durable_thread(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Draft launch memo",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.create_task_event(
        task_id=task.id,
        author_type="human",
        author_name="Human Operator",
        event_type="assignment",
        content='Created task "Draft launch memo" for Taylor.',
    )
    db.create_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        event_type="status_update",
        content='Accepted work on "Draft launch memo".',
    )

    events = await get_task_events(task.id)

    assert [event["event_type"] for event in events] == ["assignment", "status_update"]
    assert events[-1]["author_name"] == "Taylor"


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
    db.upsert_artifact(
        agent_id=agent.id,
        task_id=task.id,
        virtual_path="/me/draft-summary.md",
        absolute_path=str(agent_artifact_dir(agent.storage_key) / "draft-summary.md"),
        title="draft-summary.md",
        kind="file",
        category="output",
        size_bytes=128,
        source_command="write /me/draft-summary.md",
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
    assert recent_work.data["recent_work_artifacts"][0]["path"] == "/me/draft-summary.md"
    assert "RECENT WORK ARTIFACTS:" in recent_work.prompt_content
    assert "/me/draft-summary.md" in recent_work.prompt_content

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


def test_execute_bm_cli_task_board_commands_expose_manager_views(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")
    parent = db.create_task(
        title="Coordinate orchard launch",
        assigned_to=pm.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=pm.id,
        created_by=HUMAN_SENDER_ID,
        project="orchard",
    )
    child = db.create_task(
        title="Draft orchard launch checklist",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        parent_task_id=parent.id,
        project="orchard",
    )
    db.create_task_event(
        task_id=child.id,
        author_type="agent",
        author_agent_id=pm.id,
        author_name=pm.name,
        event_type="assignment",
        content='Created child task "Draft orchard launch checklist" for delegated work.',
    )

    owned = execute_bm_cli(pm, state, "owned-tasks")
    assert owned.ok is True
    assert owned.kind == "owned_tasks"
    assert "TASKS I DELEGATED:" in owned.prompt_content
    assert owned.data["sections"]["tasks_i_delegated"][0]["id"] == child.id

    detail = execute_bm_cli(pm, state, f"task {child.id}")
    assert detail.ok is True
    assert detail.kind == "task_detail"
    assert detail.data["task"]["id"] == child.id
    assert "TASK THREAD:" in detail.prompt_content


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


def test_execute_bm_cli_document_edit_commands_target_sections_precisely(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    personal_root = _reset_agent_workspace(agent.storage_key)

    source = (
        "# Launch Plan\n\n"
        "Overview.\n\n"
        "## Recommendation\n\n"
        "Use the current launch plan.\n\n"
        "## Risks\n\n"
        "Watch the rollout closely.\n"
    )
    execute_bm_cli(agent, state, "write report.md", source)

    outline = execute_bm_cli(agent, state, "outline report.md")
    assert outline.ok is True
    assert "line 1: # Launch Plan" in outline.prompt_content
    assert "line 5:   ## Recommendation" in outline.prompt_content
    assert outline.data["sections"][1]["heading"] == "## Recommendation"

    read_range = execute_bm_cli(agent, state, "read-range report.md 5:9")
    assert read_range.ok is True
    assert "   5 | ## Recommendation" in read_range.prompt_content
    assert "   7 | Use the current launch plan." in read_range.prompt_content

    replaced = execute_bm_cli(
        agent,
        state,
        'replace-section report.md "## Recommendation"',
        "Focus the launch on the highest-confidence market and keep scope tight.",
    )
    assert replaced.ok is True
    assert replaced.kind == "replace-section"

    report_path = personal_root / "report.md"
    assert report_path.read_text(encoding="utf-8") == (
        "# Launch Plan\n\n"
        "Overview.\n\n"
        "## Recommendation\n\n"
        "Focus the launch on the highest-confidence market and keep scope tight.\n\n"
        "## Risks\n\n"
        "Watch the rollout closely.\n"
    )

    compact_source = "# Title\n## A\none\n## B\ntwo\n"
    execute_bm_cli(agent, state, "write compact.md", compact_source)
    compact_replaced = execute_bm_cli(agent, state, 'replace-section compact.md "## A"', "new")
    assert compact_replaced.ok is True
    assert (personal_root / "compact.md").read_text(encoding="utf-8") == "# Title\n## A\nnew\n## B\ntwo\n"

    events = db.list_bm_cli_events(agent_id=agent.id, limit=10)
    assert [event["result_kind"] for event in events].count("replace-section") == 2

    artifacts = db.list_artifacts(agent_id=agent.id, limit=10)
    assert any(item.virtual_path == "/me/report.md" for item in artifacts)


def test_execute_bm_cli_file_command_aliases_and_discovery_are_ai_friendly(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    personal_root = _reset_agent_workspace(agent.storage_key)

    execute_bm_cli(
        agent,
        state,
        "write plan.md",
        "# Plan\n\n## Recommendation\n\nUse a narrower launch plan.\n",
    )

    alias_result = execute_bm_cli(
        agent,
        state,
        'repsect plan.md "## Recommendation"',
        "Focus the launch on one market and one owner.",
    )
    assert alias_result.ok is True
    assert alias_result.kind == "replace-section"
    assert (personal_root / "plan.md").read_text(encoding="utf-8") == (
        "# Plan\n\n"
        "## Recommendation\n\n"
        "Focus the launch on one market and one owner.\n"
    )

    categories = execute_bm_cli(agent, state, "categories")
    assert categories.ok is True
    assert "files — Inspect, create, and edit files" in categories.prompt_content
    assert "bwrite" in categories.prompt_content
    assert "repsect" in categories.prompt_content
    assert "rewsect" in categories.prompt_content
    assert "batch-write" not in categories.prompt_content
    assert "replace-section" not in categories.prompt_content
    assert "rewrite-section" not in categories.prompt_content
    assert 'replace-section <path> "<heading>"' not in categories.prompt_content
    assert "Type \"fsearch <category|keyword>\" to review the commands inside a category." in categories.prompt_content

    search = execute_bm_cli(agent, state, "fsearch section")
    assert search.ok is True
    assert 'repsect <path> "<heading>" — body = literal replacement section text; target by quoted markdown heading' in search.prompt_content
    assert 'rewsect <path> "<heading>" — body = short rewrite goal; runtime rewrites only the targeted section' in search.prompt_content
    assert "[aliases:" not in search.prompt_content

    learn = execute_bm_cli(agent, state, "learn repsect")
    assert learn.ok is True
    assert "Command:   repsect" in learn.prompt_content
    assert "Aliases:" not in learn.prompt_content


def test_simulate_cli_policy_matches_virtual_alias_execution(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")

    simulated = asyncio.run(
        simulate_cli_policy(
            CliPolicySimulateBody(
                command='repsect plan.md "## Recommendation"',
                agent_id=agent.id,
            )
        )
    )

    assert simulated["decision"]["allowed"] is True
    assert simulated["decision"]["tier"] == "virtual"
    assert simulated["decision"]["executor"] == "virtual"


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

    history = execute_bm_cli(agent, state, "git log 50")
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
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()

    result = execute_bm_cli(agent, state, "rm summary.md")

    assert result.ok is False
    assert result.kind == "approval_required"
    assert result.approval_required is True
    assert "approval required" in result.detail.lower()

    events = db.list_bm_cli_events(agent_id=agent.id, limit=5)
    assert events[0]["command"] == "rm summary.md"
    assert events[0]["decision"] == "approval_required"
    assert events[0]["executor"] == "shell"
    assert events[0]["policy_tier"] == "approval_required"


def test_execute_approved_command_uses_reviewed_cwd(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    personal_root = _reset_agent_workspace(agent.storage_key)

    execute_bm_cli(agent, state, "mkdir reports")
    set_cli_cwd(agent.id, "/me")
    approval = db.create_cli_approval_request(
        agent_id=agent.id,
        command="pwd",
        cwd="/me/reports",
    )

    result = execute_approved_command(
        agent,
        state,
        "pwd",
        approval_request_id=approval.id,
        cwd="/me/reports",
    )

    assert result.ok is True
    assert str(personal_root / "reports") in result.prompt_content

    events = db.list_bm_cli_events(agent_id=agent.id, limit=5)
    assert events[0]["command"] == "pwd"
    assert events[0]["cwd_before"] == "/me/reports"
    assert events[0]["approval_request_id"] == approval.id


@pytest.mark.asyncio
async def test_cli_approval_resolved_trigger_uses_reviewed_cwd(isolated_db, monkeypatch):
    import core.bm_cli.runtime as bm_cli_runtime

    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    approval = db.create_cli_approval_request(
        agent_id=agent.id,
        command="pwd",
        cwd="/me/reports",
    )
    captured: dict[str, str | None] = {}

    def fake_execute_approved_command(
        _agent,
        _state,
        command,
        content=None,
        *,
        approval_request_id,
        cwd=None,
        trigger_type=None,
    ):
        captured["command"] = command
        captured["approval_request_id"] = approval_request_id
        captured["cwd"] = cwd
        captured["trigger_type"] = trigger_type
        return BossModCliResult(
            command=command,
            ok=True,
            detail="approved command executed",
            prompt_content="BOSSMOD CLI RESULT\ncommand: pwd\n\nSTDOUT:\n/me/reports",
            kind="shell",
            cwd=cwd,
            executor="shell",
            exit_code=0,
        )

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"idle","th":"done"}',
            model="test-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})
    monkeypatch.setattr(client, "completion", fake_completion)
    monkeypatch.setattr(bm_cli_runtime, "execute_approved_command", fake_execute_approved_command)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "cli_approval_resolved",
            "payload": {
                "approval_request_id": approval.id,
                "command": "pwd",
                "cwd": "/me/reports",
                "status": "approved",
            },
        },
    )

    assert outcome.trigger_status == "completed"
    assert captured["command"] == "pwd"
    assert captured["approval_request_id"] == approval.id
    assert captured["cwd"] == "/me/reports"
    assert captured["trigger_type"] == "cli_approval_resolved"


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
async def test_work_completion_requires_requested_saved_file(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Please write the avocado whitepaper and save it as avocado_white.md.",
        message_type="human",
    )
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
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

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
            content='{"act":"work","data":{"out":"Drafted a concise avocado whitepaper."},"th":"draft the requested whitepaper"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"drafted avocado whitepaper",'
                '"msg":"Finished the avocado whitepaper and saved it for you."},'
                '"th":"try to complete the task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"cli","data":{"cmd":"write /me/avocado_white.md","body":"Avocado trees thrive in warm climates."},'
                '"th":"save the required file"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"drafted avocado whitepaper",'
                '"msg":"Finished the avocado whitepaper and saved it for you."},'
                '"th":"complete the task now that the file exists"}'
            ),
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
            "content": 'Resume work on "Write avocado whitepaper".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.trigger_status == "completed"
    assert outcome.result["event"] == "status_changed"
    assert outcome.result["chat_message"]["content"] == "Finished the avocado whitepaper and saved it for you."
    assert outcome.result["chat_notification"]["kind"] == "completion"
    assert outcome.result["chat_notification"]["human_visible"] is True

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "complete"
    assert refreshed_task.completion_summary == "drafted avocado whitepaper"

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "Finished the avocado whitepaper and saved it for you."

    api_messages = await get_agent_messages(agent.id, limit=10)
    system_messages = [item for item in api_messages if item["message_type"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["desk_path"] == "/me/avocado_white.md"

    desk_payload = await get_agent_desk(agent.id, path="/me/avocado_white.md")
    assert desk_payload["kind"] == "file"
    assert desk_payload["artifact"]["virtual_path"] == "/me/avocado_white.md"
    assert "Avocado trees thrive in warm climates." in desk_payload["content"]

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "work -> complete -> bm_cli -> complete"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    blocked_step = json.loads(detail["steps"][1]["result"])
    assert blocked_step["event"] == "world_feedback"
    assert blocked_step["missing_deliverables"] == [
        {"type": "file", "path": "/me/avocado_white.md", "description": None}
    ]


@pytest.mark.asyncio
async def test_run_turn_work_completion_can_deliver_file_and_summary_together(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Please write the launch recap and save it as launch_recap.md, then give me a short summary too.",
        message_type="human",
    )
    task = db.create_task(
        title="Write launch recap",
        description="Write the launch recap, save it as launch_recap.md, and provide a short summary.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        work_contract={"deliverables": [{"type": "file", "path": "/me/launch_recap.md"}]},
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

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
            content=(
                '{"act":"cli","data":{"cmd":"write /me/launch_recap.md","body":"Launch recap\\n\\nThe launch outperformed forecast and onboarding held steady."},'
                '"th":"save the requested launch recap"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"saved launch recap",'
                '"msg":"Finished the launch recap and saved it. Short summary: launch outperformed forecast and onboarding stayed steady."},'
                '"th":"deliver the file and summary together"}'
            ),
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
            "content": 'Resume work on "Write launch recap".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.trigger_status == "completed"
    assert outcome.result["event"] == "status_changed"
    assert outcome.result["chat_message"]["content"] == (
        "Finished the launch recap and saved it. Short summary: launch outperformed forecast and onboarding stayed steady."
    )
    assert outcome.result["chat_notification"]["kind"] == "completion"
    assert outcome.result["chat_notification"]["human_visible"] is True
    assert outcome.result["chat_notification"]["deliverables"] == [
        {"type": "file", "path": "/me/launch_recap.md", "description": None}
    ]

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "complete"
    assert refreshed_task.completion_summary == "saved launch recap"

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "Finished the launch recap and saved it. Short summary: launch outperformed forecast and onboarding stayed steady."
    )

    api_messages = await get_agent_messages(agent.id, limit=10)
    system_messages = [item for item in api_messages if item["message_type"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["desk_path"] == "/me/launch_recap.md"

    desk_payload = await get_agent_desk(agent.id, path="/me/launch_recap.md")
    assert desk_payload["kind"] == "file"
    assert desk_payload["artifact"]["virtual_path"] == "/me/launch_recap.md"
    assert "Launch recap" in desk_payload["content"]

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> complete"


@pytest.mark.asyncio
async def test_large_work_output_with_file_deliverable_is_redirected_to_managed_write(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write backend whitepaper",
        description="Create and save a backend whitepaper.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
        work_contract={
            "deliverables": [{"type": "file", "path": "/me/backend-tech-stack-whitepaper.md"}],
        },
    )
    activity_runtime.activate_work_activity(
        agent.id,
        task,
        title=task.title,
        detail=task.description,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    result = await execute_action(
        {"action": "work", "output": "x" * 2500},
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    assert "/me/backend-tech-stack-whitepaper.md" in result["detail"]
    assert "Use BossMod CLI write with no body" in result["detail"]
    assert result["missing_deliverables"] == [
        {
            "type": "file",
            "path": "/me/backend-tech-stack-whitepaper.md",
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_large_work_output_with_multiple_file_deliverables_is_redirected_to_batch_write(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write package",
        description="Create and save a summary and appendix.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
        work_contract={
            "deliverables": [
                {"type": "file", "path": "/me/summary.md"},
                {"type": "file", "path": "/me/appendix.md"},
            ],
        },
    )
    activity_runtime.activate_work_activity(
        agent.id,
        task,
        title=task.title,
        detail=task.description,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    result = await execute_action(
        {"action": "work", "output": "x" * 2500},
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    assert "multiple files" in result["detail"]
    assert "Use BossMod CLI bwrite with a short manifest body" in result["detail"]
    assert result["missing_deliverables"] == [
        {
            "type": "file",
            "path": "/me/summary.md",
            "description": None,
        },
        {
            "type": "file",
            "path": "/me/appendix.md",
            "description": None,
        },
    ]


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


@pytest.mark.asyncio
async def test_complete_action_reports_to_requester_and_owner_agents(isolated_db):
    desk_x, desk_y = _desk_xy()
    requester = db.create_agent(name="Avery", desk_x=desk_x, desk_y=desk_y)
    owner = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Review rollout checklist",
        description="Review and summarize the rollout checklist.",
        assigned_to=worker.id,
        requester_id=requester.id,
        owner_id=owner.id,
        created_by=requester.id,
        source_channel="peer",
        notification_policy="none",
    )
    state = _activate_work(worker, task, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "done",
            "followUpMessage": "Finished the checklist review. Want the short summary or the full notes?",
        },
        worker,
        state,
    )

    stakeholder_updates = sorted(
        item["agent_id"]
        for item in result["trigger_requests"]
        if item["trigger_type"] == "task_follow_up"
    )
    assert stakeholder_updates == [requester.id]

    requester_thread = db.get_agent_direct_thread(worker.id, requester.id, limit=10)
    owner_thread = db.get_agent_direct_thread(worker.id, owner.id, limit=10)
    assert requester_thread == []
    assert owner_thread == []
    events = db.list_task_events(task.id, limit=10)
    assert "Finished the checklist review. Want the short summary or the full notes?" in [event.content for event in events]
    assert db.list_notifications(agent_id=requester.id, limit=10)[0].kind == "task_update"
    assert db.list_notifications(agent_id=owner.id, limit=10)[0].kind == "task_update"


@pytest.mark.asyncio
async def test_complete_action_blocks_parent_task_while_child_work_is_open(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Coordinate whitepaper delivery",
        description="Own delivery of the whitepaper and report back.",
        assigned_to=pm.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=pm.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    child = db.create_task(
        title="Write whitepaper draft",
        description="Write the first draft and send it back to Pat.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        parent_task_id=parent.id,
        source_channel="peer",
        notification_policy="none",
    )
    state = _activate_work(pm, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "done",
            "followUpMessage": "The paper is done.",
        },
        pm,
        state,
    )

    assert result["event"] == "world_feedback"
    assert 'Resolve or replan "Write whitepaper draft" before completing the parent task.' in result["detail"]
    assert result["task_ids"] == [child.id]
    refreshed_parent = db.get_task(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.status == "active"


@pytest.mark.asyncio
async def test_child_completion_appends_parent_status_update_and_resumes_waiting_parent(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Coordinate whitepaper delivery",
        description="Own delivery of the whitepaper and report back.",
        assigned_to=pm.id,
        created_by=HUMAN_SENDER_ID,
    )
    child = db.create_task(
        title="Write whitepaper",
        description="Draft the whitepaper and send it back.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        parent_task_id=parent.id,
    )
    db.update_task(parent.id, status="waiting", status_note="Waiting on Taylor.")
    state = _activate_work(worker, child, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Draft is finished.",
            "thought": "complete the child task",
        },
        worker,
        state,
        trigger={"type": "activity_resumed", "task_id": child.id, "source_channel": "work"},
    )

    parent_events = db.list_task_events(parent.id, limit=10)
    assert any("Child task" in event.content and "completed" in event.content for event in parent_events)
    assert any(
        item["agent_id"] == pm.id
        and item["trigger_type"] == "task_update"
        and item.get("task_id") == parent.id
        and item["payload"]["attention_kind"] == "completion_report"
        for item in result.get("trigger_requests", [])
    )


@pytest.mark.asyncio
async def test_complete_action_hides_agent_requested_subtask_from_human_chat(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Draft appendix",
        description="Draft the appendix section.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        source_channel="peer",
        notification_policy="completion_blocked",
    )
    state = _activate_work(worker, task, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "done",
            "followUpMessage": "Finished the appendix draft. Want me to fold it into the main paper next?",
        },
        worker,
        state,
    )

    assert result["chat_notification"]["human_visible"] is False


@pytest.mark.asyncio
async def test_get_agent_notifications_returns_hidden_task_updates(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.create_notification(
        agent_id=agent.id,
        kind="task_update",
        content='Worker completed "Draft appendix".',
        source_channel="task",
        policy="none",
        chat_visible=False,
        prompt_visibility=False,
    )

    result = await get_agent_notifications(agent.id, limit=10)

    assert result[0]["kind"] == "task_update"
    assert result[0]["chat_visible"] is False


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
    db.upsert_artifact(
        agent_id=agent.id,
        task_id=task.id,
        virtual_path="/me/generate_words_api.md",
        absolute_path=str(agent_artifact_dir(agent.storage_key) / "generate_words_api.md"),
        title="generate_words_api.md",
        kind="file",
        category="output",
        size_bytes=256,
        source_command="write /me/generate_words_api.md",
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
    assert "## Current Local Time" in system_prompt
    assert "## Live Runtime State" in system_prompt
    assert "status: idle" in system_prompt
    assert "current_task: none" in system_prompt
    assert "## Task Board" in system_prompt
    assert "## Open Tasks" not in system_prompt
    assert "Treat `Task Board` as authoritative" in system_prompt
    assert "## Historical References / Team Directory" in system_prompt
    assert "RECENT COMPLETED TASKS:" in system_prompt
    assert "Generate Words API" in system_prompt
    assert "RECENT WORK ARTIFACTS:" in system_prompt
    assert "/me/generate_words_api.md" in system_prompt
    assert "RECENT RUNTIME NOTIFICATIONS:" in system_prompt
    assert "For status questions, answer from `Live Runtime State` first." in system_prompt


def test_decision_contract_renders_known_project_folder_guidance_from_context(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    db.update_agent_cli_state(agent.id, cwd="/projects/orchard/reports")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "human_chat",
                "content": "What does the orchard brief say?",
                "from_name": "Human Operator",
                "source_channel": "chat",
            },
            contract_kind="decision",
            current_task={
                "id": "task-1",
                "title": "Prepare orchard launch plan",
                "status": "accepted",
                "description": "Prepare the orchard launch plan.",
                "project": "orchard",
            },
        )
    )

    contract = context[1]["content"]
    assert "Known project folder for this turn: `/projects/orchard`" in contract
    assert "For project details, start with `ls /projects/orchard`." in contract
    assert "For shared project work without an explicit path, save under `/projects/orchard/...`." in contract
    assert "project-folder lookup starts with `ls /projects/orchard`" in contract
    assert "default save root for this turn is `/projects/orchard/reports`" in contract


def test_project_manager_personality_renders_coordination_ownership_guidance(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(
        name="Michael",
        role="Project Manager",
        prompt_template=load_default_personality_prompt("Project Manager"),
        desk_x=desk_x,
        desk_y=desk_y,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "human_chat",
                "content": "Can you get Taylor to write a whitepaper and manage him for me?",
                "from_name": "Human Operator",
                "source_channel": "chat",
            },
            contract_kind="decision",
        )
    )

    system_prompt = context[0]["content"]
    assert "Translate stakeholder asks into owned plans and decisions instead of acting like a passive relay" in system_prompt
    assert "Never dump internal routing mechanics or teammate-autonomy caveats onto stakeholders" in system_prompt
    assert "respond as the accountable coordinator" in system_prompt
    assert "Default intelligently when the choice is low-risk and reversible." in system_prompt
    assert "Ask clarifying questions when the missing answer materially changes scope, risk, ownership, or delivery." in system_prompt


def test_default_personality_seed_uses_file_backed_prompt(isolated_db):
    personalities = {personality.name: personality for personality in db.list_personalities()}
    assert personalities["Project Manager"].prompt_template == load_default_personality_prompt("Project Manager")


def test_task_assigned_trigger_renders_latest_assignment_note(isolated_db):
    desk_x, desk_y = _desk_xy()
    assigner = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y)
    assignee = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(assignee.id, x=desk_x, y=desk_y, status="idle")

    context = context_builder.build_context(
        _build_turn_context(
            assignee,
            state,
            trigger={
                "type": "task_assigned",
                "task_id": "task-1",
                "task_title": "Research foundation doc",
                "task_description": "Draft the research foundation for the SLM edge white paper.",
                "content": "Tomorrow EOD works. Focus on the research foundation first and use the existing file path.",
                "from_agent": assigner.id,
                "from_name": assigner.name,
                "source_channel": "work",
            },
            contract_kind="decision",
        )
    )

    trigger_block = context[-1]["content"]
    assert '[Michael] assigned you a task: "Research foundation doc".' in trigger_block
    assert "This task already exists on your task board." in trigger_block
    assert (
        "Latest note from [Michael]: Tomorrow EOD works. Focus on the research foundation first and use the existing file path."
        in trigger_block
    )


def test_task_follow_up_trigger_renders_task_thread_context(isolated_db):
    desk_x, desk_y = _desk_xy()
    sender = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y)
    recipient = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(recipient.id, x=desk_x, y=desk_y, status="idle")

    context = context_builder.build_context(
        _build_turn_context(
            recipient,
            state,
            trigger={
                "type": "task_follow_up",
                "task_id": "task-1",
                "task_title": "Research foundation doc",
                "task_description": "Draft the research foundation for the SLM edge white paper.",
                "task_status": "pending",
                "task_party": "assignee",
                "content": "Tomorrow EOD works. Focus on the research foundation first and use the existing file path.",
                "from_agent": sender.id,
                "from_name": sender.name,
                "source_channel": "work",
            },
            contract_kind="decision",
        )
    )

    trigger_block = context[-1]["content"]
    assert 'A task needs your response on "Research foundation doc".' in trigger_block
    assert "Current task status: pending" in trigger_block
    assert (
        "Latest note from [Michael]: Tomorrow EOD works. Focus on the research foundation first and use the existing file path."
        in trigger_block
    )
    assert "This task already exists and is still waiting on your decision." in trigger_block


def test_communication_snapshot_includes_recent_artifact_paths(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Write quarterly report",
        description="Draft the quarterly report and save it.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    db.update_task(task.id, status="complete", completion_summary="saved the quarterly report")
    db.upsert_artifact(
        agent_id=agent.id,
        task_id=task.id,
        virtual_path="/me/quarterly-report.md",
        absolute_path=str(agent_artifact_dir(agent.storage_key) / "quarterly-report.md"),
        title="quarterly-report.md",
        kind="file",
        category="output",
        size_bytes=512,
        source_command="write /me/quarterly-report.md",
    )

    snapshot = build_communication_snapshot(
        agent=agent,
        state=state,
        trigger={
            "type": "human_chat",
            "content": "What did you learn from the report?",
            "from_name": "Human Operator",
            "source_channel": "chat",
        },
    )

    assert snapshot["recent_work_artifacts"] == [
        {
            "task_id": task.id,
            "task_title": "Write quarterly report",
            "path": "/me/quarterly-report.md",
            "title": "quarterly-report.md",
            "type": "file",
            "created_at": snapshot["recent_work_artifacts"][0]["created_at"],
        }
    ]


def test_communication_snapshot_includes_current_cwd_and_project_paths(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    db.update_agent_cli_state(agent.id, cwd="/projects/orchard/reports")
    task = db.create_task(
        title="Prepare orchard launch plan",
        description="Prepare the launch plan for orchard.",
        project="orchard",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    db.update_task(task.id, status="accepted", watchdog_pinged_at=None)

    snapshot = build_communication_snapshot(
        agent=agent,
        state=state,
        trigger={
            "type": "human_chat",
            "content": "Can you check the orchard project folder?",
            "from_name": "Human Operator",
            "source_channel": "chat",
        },
    )

    assert snapshot["runtime"]["cwd"] == "/projects/orchard/reports"
    assert "assigned_open_tasks" not in snapshot
    assert "owned_open_tasks" not in snapshot
    assert snapshot["task_board"]["project_summary"] == [
        {
            "project": "orchard",
            "path": "/projects/orchard",
            "counts": {"accepted": 1},
            "latest_tasks": [
                {
                    "title": "Prepare orchard launch plan",
                    "status": "accepted",
                    "assigned_to": agent.id,
                    "assignee_name": "Taylor",
                }
            ],
        }
    ]
    assert "project_rollups" not in snapshot
    assert snapshot["referenced_records"]["projects"] == [
        {
            "project": "orchard",
            "path": "/projects/orchard",
            "counts": {"accepted": 1},
            "latest_tasks": [
                {
                    "title": "Prepare orchard launch plan",
                    "status": "accepted",
                    "assigned_to": agent.id,
                    "assignee_name": "Taylor",
                }
            ],
        }
    ]


def test_preview_prompt_bundles_stay_under_instruction_budget(isolated_db):
    cases = [
        ("decision", "human_chat"),
        ("decision", "peer_message"),
        ("decision", "task_follow_up"),
        ("decision", "watchdog_status_ping"),
        ("execution", "activity_resumed"),
    ]

    for contract_kind, trigger_type in cases:
        bundle = context_builder.preview_prompt_bundle(contract_kind, trigger_type)
        total = _bundle_token_total(bundle["messages"])
        assert total > 0
        assert total < 3000, f"{contract_kind}/{trigger_type} prompt bundle exceeded budget: {total} tokens"


def test_prompt_bundle_renders_only_relevant_dynamic_blocks(isolated_db):
    human_bundle = context_builder.preview_prompt_bundle("decision", "human_chat")["rendered"]
    execution_bundle = context_builder.preview_prompt_bundle("execution", "activity_resumed")["rendered"]

    assert "CONVERSATION ENVELOPE:" in human_bundle
    assert "AUTHORITATIVE COMMUNICATION SNAPSHOT (JSON):" in human_bundle
    assert "FILE DELIVERABLE GUIDANCE:" not in human_bundle

    assert "FILE DELIVERABLE GUIDANCE:" in execution_bundle
    assert "CONVERSATION ENVELOPE:" not in execution_bundle
    assert "AUTHORITATIVE COMMUNICATION SNAPSHOT (JSON):" not in execution_bundle


def test_prompt_bundle_uses_board_first_task_context_without_legacy_keys(isolated_db):
    human_bundle = context_builder.preview_prompt_bundle("decision", "human_chat")["rendered"]

    assert "## Task Board" in human_bundle
    assert "## Open Tasks" not in human_bundle
    assert '"task_board"' in human_bundle
    assert '"assigned_open_tasks"' not in human_bundle
    assert '"owned_open_tasks"' not in human_bundle
    assert '"project_rollups"' not in human_bundle


def test_template_variable_metadata_includes_current_time_variables(isolated_db):
    names = {item["name"] for item in context_builder.template_variable_metadata()}
    assert "current_date_time" in names
    assert "current_time.iso_local" in names
    assert "current_time.iso_utc" in names
    assert "task_board" in names
    assert "cli.cwd" in names
    assert "workspace.default_save_root" in names
    assert "workspace.project_root" in names
    assert "current_time.date" in names
    assert "current_time.time" in names
    assert "current_time.day_name" in names
    assert "current_time.timezone" in names


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
    assert payload["trigger_event"] == settings_store.RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE
    assert payload["conversation_envelope"] == settings_store.RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE
    assert payload["file_deliverable_guidance"] == settings_store.RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE
    assert payload["communication_snapshot"] == settings_store.RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE
    assert any(item["name"] == "trigger.type" for item in payload["allowed_variables"])
    assert any(item["name"] == "activity.preferred_destination" for item in payload["allowed_variables"])
    assert any(item["name"] == "conversation.speaker_name" for item in payload["allowed_variables"])
    assert any(item["name"] == "communication_snapshot.json" for item in payload["allowed_variables"])
    assert any(example.startswith("{{if trigger.type = 'human_chat'}}") for example in payload["template_syntax"])
    assert "human_chat" in payload["preview_triggers"]
    assert payload["prompt_health"]["ok"] is True
    assert payload["prompt_health"]["status"] == "clean"


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
            trigger_event="TRIGGER kind={{trigger.type}} who={{trigger.from_name}}",
            conversation_envelope="ENVELOPE speaker={{conversation.speaker_name}} audience={{conversation.audience_mode}}",
            file_deliverable_guidance="GUIDANCE files={{file_guidance.required_files}}",
            communication_snapshot="SNAPSHOT {{communication_snapshot.json}}",
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
            current_task={
                "id": "task-1",
                "title": "Write the backend whitepaper",
                "status": "active",
                "description": "Draft and save the backend whitepaper.",
                "work_contract": {
                    "deliverables": [{"type": "file", "path": "/me/backend-tech-stack-whitepaper.md"}],
                },
            },
        )
    )
    snapshot_context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={
                "type": "human_chat",
                "content": "What is your status?",
                "from_name": "Human Operator",
            },
            conversation_history=[],
            prompt_notifications=[],
            reference_materials=[],
            nearby_agents=[],
            pending_trigger_count=0,
            contract_kind="decision",
            communication_snapshot_json='{"runtime":{"status":"idle"}}',
        )
    )

    assert human_context[1]["content"] == "HUMAN DECISION"
    assert peer_context[1]["content"] == "OTHER DECISION"
    assert execution_context[1]["content"] == "EXECUTION FOR activity_resumed"
    assert human_context[2]["content"] == "ENVELOPE speaker=Human Operator audience=direct"
    assert human_context[-1]["content"] == "TRIGGER kind=human_chat who=Human Operator"
    assert any(msg["content"] == "GUIDANCE files=/me/backend-tech-stack-whitepaper.md" for msg in execution_context if msg["role"] == "system")
    assert any(msg["content"] == 'SNAPSHOT {"runtime":{"status":"idle"}}' for msg in snapshot_context if msg["role"] == "system")


def test_execution_context_adds_file_deliverable_guidance_for_file_contract(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(
        name="Taylor",
        role="Operations Analyst",
        desk_x=desk_x,
        desk_y=desk_y,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "activity_resumed",
                "content": "Continue the whitepaper task.",
            },
            contract_kind="execution",
            current_task={
                "id": "task-1",
                "title": "Write the backend whitepaper",
                "status": "active",
                "description": "Draft and save the backend whitepaper.",
                "work_contract": {
                    "deliverables": [{"type": "file", "path": "/me/backend-tech-stack-whitepaper.md"}],
                },
            },
        )
    )

    guidance = [
        msg["content"]
        for msg in context
        if msg["role"] == "system" and "FILE DELIVERABLE GUIDANCE:" in msg["content"]
    ]
    assert len(guidance) == 1
    assert "/me/backend-tech-stack-whitepaper.md" in guidance[0]
    assert "write <path> with no body" in guidance[0]
    assert "Use work.out for short progress/status text" in guidance[0]


def test_execution_context_adds_batch_write_guidance_for_multiple_file_deliverables(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(
        name="Taylor",
        role="Operations Analyst",
        desk_x=desk_x,
        desk_y=desk_y,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="work_active")

    context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "activity_resumed",
                "content": "Continue the package task.",
            },
            contract_kind="execution",
            current_task={
                "id": "task-2",
                "title": "Write the package",
                "status": "active",
                "description": "Draft and save the package files.",
                "work_contract": {
                    "deliverables": [
                        {"type": "file", "path": "/me/summary.md"},
                        {"type": "file", "path": "/me/appendix.md"},
                    ],
                },
            },
        )
    )

    guidance = [
        msg["content"]
        for msg in context
        if msg["role"] == "system" and "FILE DELIVERABLE GUIDANCE:" in msg["content"]
    ]
    assert len(guidance) == 1
    assert "bwrite" in guidance[0]
    assert "Do not put long-form document bodies into CLI JSON." in guidance[0]


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
async def test_system_prompt_renders_current_time_variables(isolated_db, monkeypatch):
    fixed_now = datetime(2026, 3, 30, 9, 42, 15, tzinfo=timezone(timedelta(hours=-4), name="EDT"))

    monkeypatch.setattr(context_builder, "now_local", lambda: fixed_now)

    await set_setting_route(
        "system_prompt_template",
        (
            "TIME {{current_date_time}}\n"
            "DATE {{current_time.date}}\n"
            "CLOCK {{current_time.time}}\n"
            "DAY {{current_time.day_name}}\n"
            "TZ {{current_time.timezone}}\n"
            "UTC {{current_time.iso_utc}}\n"
            "{{personality}}"
        ),
        "advanced",
    )

    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(
        name="Taylor",
        role="Operations Analyst",
        prompt_template="PERSONA {{agent_name}}",
        desk_x=desk_x,
        desk_y=desk_y,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")

    context = context_builder.build_context(
        _build_turn_context(
            agent,
            state,
            trigger={
                "type": "human_chat",
                "content": "What time is it?",
                "from_name": "Human Operator",
            },
            contract_kind="decision",
        )
    )

    system_prompt = context[0]["content"]
    assert "TIME 2026-03-30 09:42:15 EDT" in system_prompt
    assert "DATE 2026-03-30" in system_prompt
    assert "CLOCK 09:42:15" in system_prompt
    assert "DAY Monday" in system_prompt
    assert "TZ EDT" in system_prompt
    assert "UTC 2026-03-30T13:42:15+00:00" in system_prompt
    assert "PERSONA Taylor" in system_prompt


@pytest.mark.asyncio
async def test_settings_route_rejects_invalid_system_prompt_template(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await set_setting_route("system_prompt_template", "{{missing_value}}", "advanced")

    assert exc_info.value.status_code == 400
    assert "template variable" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_reset_setting_to_default_restores_seeded_system_prompt_template(isolated_db):
    await set_setting_route(
        "system_prompt_template",
        "CUSTOM HEADER\n{{personality}}",
        "advanced",
    )

    result = await reset_setting_to_default("system_prompt_template")

    assert result.key == "system_prompt_template"
    assert result.category == "advanced"
    assert result.value == settings_store.SYSTEM_PROMPT_TEMPLATE
    assert config.require("system_prompt_template") == settings_store.SYSTEM_PROMPT_TEMPLATE


@pytest.mark.asyncio
async def test_runtime_contract_save_rejects_invalid_template(isolated_db):
    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_contracts_route(
            RuntimeContractsBody(
                decision="{{missing_value}}",
                execution="EXECUTION",
                trigger_event="TRIGGER",
                conversation_envelope="ENVELOPE",
                file_deliverable_guidance="GUIDANCE",
                communication_snapshot="SNAPSHOT",
            )
        )

    assert exc_info.value.status_code == 400
    assert "template variable" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_reset_runtime_contracts_restores_seed_defaults(isolated_db):
    await set_runtime_contracts_route(
        RuntimeContractsBody(
            decision="CUSTOM DECISION",
            execution="CUSTOM EXECUTION",
            trigger_event="CUSTOM TRIGGER",
            conversation_envelope="CUSTOM ENVELOPE",
            file_deliverable_guidance="CUSTOM GUIDANCE",
            communication_snapshot="CUSTOM SNAPSHOT",
        )
    )

    payload = await reset_runtime_contracts()

    assert payload["decision"] == settings_store.RUNTIME_CONTRACT_DECISION_TEMPLATE
    assert payload["execution"] == settings_store.RUNTIME_CONTRACT_EXECUTION_TEMPLATE
    assert payload["trigger_event"] == settings_store.RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE
    assert payload["conversation_envelope"] == settings_store.RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE
    assert payload["file_deliverable_guidance"] == settings_store.RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE
    assert payload["communication_snapshot"] == settings_store.RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE
    assert config.require("runtime_contract_decision") == settings_store.RUNTIME_CONTRACT_DECISION_TEMPLATE
    assert config.require("runtime_contract_execution") == settings_store.RUNTIME_CONTRACT_EXECUTION_TEMPLATE
    assert config.require("runtime_block_trigger_event") == settings_store.RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE
    assert config.require("runtime_block_conversation_envelope") == settings_store.RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE
    assert config.require("runtime_block_file_deliverable_guidance") == settings_store.RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE
    assert config.require("runtime_block_communication_snapshot") == settings_store.RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE


@pytest.mark.asyncio
async def test_runtime_contract_preview_supports_unsaved_template_override(isolated_db):
    payload = await preview_runtime_contract_route(
        RuntimeContractPreviewBody(
            contract_kind="decision",
            trigger_type="task_assigned",
            scope="contract",
            templates=RuntimeContractTemplateOverridesBody(
                decision="{{if trigger.type = 'task_assigned'}}ASSIGNMENT PREVIEW{{else}}OTHER PREVIEW{{end}}",
            ),
        )
    )

    assert payload["contract_kind"] == "decision"
    assert payload["trigger_type"] == "task_assigned"
    assert payload["scope"] == "contract"
    assert payload["rendered"] == "ASSIGNMENT PREVIEW"
    assert payload["messages"] == []
    assert payload["prompt_health"]["ok"] is True


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
async def test_run_turn_deferred_task_assignment_stays_pending_and_notifies_delegator(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    delegator = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Review customer churn notes",
        description="Review the churn notes and summarize the retention risks.",
        assigned_to=worker.id,
        requester_id=delegator.id,
        owner_id=delegator.id,
        created_by=delegator.id,
        parent_task_id=None,
    )
    prepare_trigger_context(worker.id, {"type": "task_assigned", "task_id": task.id})
    state = db.get_agent_state(worker.id)
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
            content=(
                '{"act":"defer","intent":"work","msg":"I need to finish the payroll audit first. Please leave this queued for me.",'
                '"commit":"work","th":"defer until current workload clears"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        worker,
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
    assert outcome.result["event"] == "decision_applied"

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "pending"
    assert refreshed_task.status_note == "I need to finish the payroll audit first. Please leave this queued for me."
    assert db.list_tasks(assigned_to=worker.id) == [refreshed_task]

    active = _active_activity(worker.id)
    assert active is None

    queued = outcome.result["trigger_requests"][0]
    assert queued["trigger_type"] == "task_follow_up"
    assert queued["agent_id"] == delegator.id
    assert queued["payload"]["content"] == "I need to finish the payroll audit first. Please leave this queued for me."
    assert queued["payload"]["task_status"] == "pending"
    assert queued["payload"]["task_party"] == "stakeholder"

    events = db.list_task_events(task.id, limit=10)
    assert events[-1].content == "I need to finish the payroll audit first. Please leave this queued for me."

    diagnostics = db.get_diagnostics(agent_id=worker.id, limit=5)
    assert diagnostics[0]["action_name"] == "defer(work)"


@pytest.mark.asyncio
async def test_run_turn_declined_task_assignment_notifies_delegator_and_marks_declined(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    delegator = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Prepare partner call brief",
        description="Prepare the partner call brief for tomorrow morning.",
        assigned_to=worker.id,
        requester_id=delegator.id,
        owner_id=delegator.id,
        created_by=delegator.id,
        parent_task_id=None,
    )
    prepare_trigger_context(worker.id, {"type": "task_assigned", "task_id": task.id})
    state = db.get_agent_state(worker.id)
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
            content='{"act":"decline","intent":"work","msg":"I cannot take this assignment right now.","th":"decline the delegated task"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        worker,
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
    assert outcome.result["event"] == "decision_applied"

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "declined"
    assert refreshed_task.status_note == "I cannot take this assignment right now."

    active = _active_activity(worker.id)
    assert active is None

    queued = outcome.result["trigger_requests"][0]
    assert queued["trigger_type"] == "task_follow_up"
    assert queued["agent_id"] == delegator.id
    assert queued["payload"]["content"] == "I cannot take this assignment right now."
    assert queued["payload"]["task_status"] == "declined"
    assert queued["payload"]["task_party"] == "stakeholder"

    events = db.list_task_events(task.id, limit=10)
    assert events[-1].content == "I cannot take this assignment right now."

    diagnostics = db.get_diagnostics(agent_id=worker.id, limit=5)
    assert diagnostics[0]["action_name"] == "decline(none)"


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
async def test_llm_completion_times_out_using_settings(isolated_db, monkeypatch):
    db.set_setting("llm_request_timeout_seconds", "0.01", "llm")
    config.reload()
    client._llm_semaphore = None

    async def _slow_completion(**kwargs):
        await asyncio.sleep(0.05)
        return None

    monkeypatch.setattr(client.litellm, "acompletion", _slow_completion)

    with pytest.raises(client.LLMError) as exc_info:
        await client.completion(
            model="test-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert "timed out" in str(exc_info.value).lower()


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
    assert captured["max_tokens"] == 8192


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
async def test_route_human_dm_queues_direct_message_turn_without_creating_task(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)

    queued: list[dict] = []
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr("core.runtime.services.runtime_services.enqueue_trigger", _record_async(queued))

    result = await route_human_dm(
        agent_id=agent.id,
        content="Please review the rollout checklist.",
        from_name="You",
        broadcast_manager=manager,
        services=runtime_services,
    )

    assert result["routed_as"] == "human_chat"
    assert queued[0]["trigger_type"] == "human_chat"
    assert "route_hint" not in result
    assert "route_hint" not in queued[0]["payload"]
    assert db.list_tasks(assigned_to=agent.id) == []


@pytest.mark.asyncio
async def test_route_human_dm_does_not_add_routing_metadata(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)

    queued: list[dict] = []
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr("core.runtime.services.runtime_services.enqueue_trigger", _record_async(queued))

    result = await route_human_dm(
        agent_id=agent.id,
        content="status: What's your status right now?",
        from_name="You",
        broadcast_manager=manager,
        services=runtime_services,
    )

    assert result["routed_as"] == "human_chat"
    assert queued[0]["trigger_type"] == "human_chat"
    assert "route_hint" not in result
    assert "route_hint" not in queued[0]["payload"]
    assert db.list_tasks(assigned_to=agent.id) == []


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
            content='{"act":"done","data":{"sum":"done","msg":"Finished the report and saved the work."},"th":"finished"}',
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
async def test_run_turn_watchdog_status_reply_refreshes_liveness_and_resumes_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Fix the API bug",
        description="Debug and patch the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    pinged_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    heartbeat_before = pinged_at - timedelta(minutes=10)
    db.update_task(
        task.id,
        status="active",
        watchdog_pinged_at=pinged_at,
        last_heartbeat_at=heartbeat_before,
        last_activity=heartbeat_before,
    )
    state = _activate_work(agent, task)

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am still working through the failing tests and have isolated the root cause.","th":"reply to watchdog"}',
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
            "type": "watchdog_status_ping",
            "content": 'Watchdog check: are you still working on "Fix the API bug"? Provide a status update.',
            "from_name": "System",
            "source_channel": "system",
            "task_id": task.id,
            "task_title": task.title,
        },
    )

    assert outcome.result["trigger_requests"][0]["trigger_type"] == "activity_resumed"
    assert outcome.result["trigger_requests"][0]["task_id"] == task.id

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.watchdog_pinged_at is None
    assert refreshed_task.last_heartbeat_at is not None
    assert refreshed_task.last_heartbeat_at > heartbeat_before
    assert refreshed_task.last_activity is not None
    assert refreshed_task.last_activity > heartbeat_before
    assert refreshed_task.status_note == "I am still working through the failing tests and have isolated the root cause."

    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.status == "active"
    assert active.task_id == task.id
    assert active.detail == "I am still working through the failing tests and have isolated the root cause."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"
    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == []


@pytest.mark.asyncio
async def test_run_turn_human_chat_grounded_question_uses_grounded_lane(isolated_db, monkeypatch):
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

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier.","commit":"none","th":"share grounded status"}',
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
            "content": "Taylor whats your status?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 1
    assert any(
        "AUTHORITATIVE COMMUNICATION SNAPSHOT" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )
    assert not any(
        "BOSSMOD CLI RESULT" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am idle right now at the Main Workspace. I finished the Generate Words API specification earlier."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["answer"]


@pytest.mark.asyncio
async def test_run_turn_human_chat_prior_work_summary_answers_from_recent_completed_context(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Quarterly report",
        description="Summarize the quarter and save the report.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        task.id,
        status="complete",
        completion_summary=(
            "Key findings: activation rose 18%, churn fell after onboarding emails, and enterprise pipeline grew."
        ),
        watchdog_pinged_at=None,
    )
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "What were the main takeaways from the quarterly report?",
        message_type="human",
    )

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return client.LLMResponse(
            content=(
                '{"act":"reply","intent":"question","msg":"The main takeaways were stronger activation, lower churn after the onboarding emails, and a larger enterprise pipeline.",'
                '"th":"answer from recent completed work context"}'
            ),
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
            "content": "What were the main takeaways from the quarterly report?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 1
    assert any(
        "RECENT COMPLETED TASKS:" in message["content"]
        and "activation rose 18%" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "The main takeaways were stronger activation, lower churn after the onboarding emails, and a larger enterprise pipeline."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_prior_document_question_can_read_artifact_before_reply(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    task = db.create_task(
        title="Quarterly report",
        description="Summarize the quarter and save the report.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        task.id,
        status="complete",
        completion_summary="Saved the quarterly report.",
        watchdog_pinged_at=None,
    )
    report_path = agent_artifact_dir(agent.storage_key) / "quarterly-report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Quarterly Report",
                "",
                "## Key Findings",
                "- Activation rose 18% after the onboarding refresh.",
                "- Churn fell 6% in the SMB segment.",
                "- Enterprise pipeline grew 22% quarter over quarter.",
                "- Referral signups outperformed paid social.",
                "- Support tickets dropped after the billing fix.",
            ]
        ),
        encoding="utf-8",
    )
    db.upsert_artifact(
        agent_id=agent.id,
        task_id=task.id,
        virtual_path="/me/quarterly-report.md",
        absolute_path=str(report_path),
        title="quarterly-report.md",
        kind="file",
        category="output",
        size_bytes=report_path.stat().st_size,
        source_command="write /me/quarterly-report.md",
    )
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "What are the top five things you learned from the quarterly report?",
        message_type="human",
    )

    captured_messages: list[list[dict[str, str]]] = []
    responses = iter([
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"cat /me/quarterly-report.md"},"th":"read the completed report"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"reply","intent":"question","msg":"The top five takeaways were stronger activation, lower SMB churn, bigger enterprise pipeline, better referral performance, and fewer support tickets after the billing fix.",'
                '"th":"answer from the report itself"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "What are the top five things you learned from the quarterly report?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 2
    assert any(
        "BOSSMOD CLI RESULT" in message["content"] and "Activation rose 18%" in message["content"]
        for message in captured_messages[1]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "The top five takeaways were stronger activation, lower SMB churn, bigger enterprise pipeline, better referral performance, and fewer support tickets after the billing fix."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> answer(none)"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["bm_cli", "answer"]
    first_action = json.loads(detail["steps"][0]["parsed_action"])
    assert first_action["command"] == "cat /me/quarterly-report.md"


@pytest.mark.asyncio
async def test_run_turn_human_chat_project_question_can_review_project_folder_before_reply(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    db.update_agent_cli_state(agent.id, cwd="/projects/orchard/reports")
    task = db.create_task(
        title="Prepare orchard launch plan",
        description="Prepare the launch plan for orchard.",
        project="orchard",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    db.update_task(task.id, status="accepted", watchdog_pinged_at=None)
    project_root = project_artifact_dir("orchard")
    project_root.mkdir(parents=True, exist_ok=True)
    brief_path = project_root / "brief.md"
    brief_path.write_text(
        "# Orchard Brief\n\nThe orchard launch focuses on avocado distribution readiness and a phased regional rollout.\n",
        encoding="utf-8",
    )
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "What does the orchard project brief say about the launch?",
        message_type="human",
    )

    captured_messages: list[list[dict[str, str]]] = []
    responses = iter([
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"ls /projects/orchard"},"th":"check the project folder"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"cat /projects/orchard/brief.md"},"th":"read the project brief"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"reply","intent":"question","msg":"The orchard brief says the launch is focused on avocado distribution readiness and a phased regional rollout.",'
                '"th":"answer from the project brief"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "What does the orchard project brief say about the launch?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 3
    assert any(
        '"path": "/projects/orchard"' in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )
    assert any(
        "BOSSMOD CLI RESULT" in message["content"] and "brief.md" in message["content"]
        for message in captured_messages[1]
        if message["role"] == "system"
    )
    assert any(
        "BOSSMOD CLI RESULT" in message["content"] and "avocado distribution readiness" in message["content"]
        for message in captured_messages[2]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "The orchard brief says the launch is focused on avocado distribution readiness and a phased regional rollout."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> bm_cli -> answer(none)"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["bm_cli", "bm_cli", "answer"]
    first_action = json.loads(detail["steps"][0]["parsed_action"])
    second_action = json.loads(detail["steps"][1]["parsed_action"])
    assert first_action["command"] == "ls /projects/orchard"
    assert second_action["command"] == "cat /projects/orchard/brief.md"


@pytest.mark.asyncio
async def test_run_turn_human_chat_ambiguous_project_save_location_can_clarify(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Write the launch report and save it in the project files.",
        message_type="human",
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
            content=(
                '{"act":"clarify","intent":"work","msg":"Which project folder should I use for the report?",'
                '"th":"need the target project location"}'
            ),
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
            "content": "Write the launch report and save it in the project files.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert db.list_tasks(assigned_to=agent.id) == []

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "Which project folder should I use for the report?"

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "clarify(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_prior_work_question_does_not_replace_active_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    active_task = db.create_task(
        title="Prepare launch checklist",
        description="Prepare the launch checklist for tomorrow.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    completed_task = db.create_task(
        title="Quarterly report",
        description="Summarize the quarter and save the report.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    db.update_task(
        completed_task.id,
        status="complete",
        completion_summary="Key findings: activation rose 18%, churn fell, and enterprise pipeline grew.",
        watchdog_pinged_at=None,
    )
    state = _activate_work(agent, active_task, x=desk_x, y=desk_y)
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Are you still working on the quarterly report, and what did it find?",
        message_type="human",
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
            content=(
                '{"act":"reply","intent":"question","msg":"The quarterly report is already finished. Its main findings were stronger activation, lower churn, and a larger enterprise pipeline. I am currently working on the launch checklist.",'
                '"th":"answer about prior work without replacing current work"}'
            ),
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
            "content": "Are you still working on the quarterly report, and what did it find?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["trigger_requests"][0]["trigger_type"] == "activity_resumed"
    assert outcome.result["trigger_requests"][0]["task_id"] == active_task.id

    active = _active_activity(agent.id)
    assert active is not None
    assert active.task_id == active_task.id

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert thread[0].message_type == "human"
    assert thread[-1].content == (
        "The quarterly report is already finished. Its main findings were stronger activation, lower churn, and a larger enterprise pipeline. I am currently working on the launch checklist."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_work_request_creates_task_and_accepts_assignment(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Please write a marketing paper on protein shakes.", message_type="human")

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
            content='{"act":"accept","intent":"work","msg":"On it. I will draft the paper and send you the finished version.","commit":"work","data":{"task":{"title":"Write marketing paper on protein shakes","desc":"Draft a marketing paper on protein shakes and save the finished document."}},"th":"accept the created assignment"}',
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
            "content": "Please write a marketing paper on protein shakes.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 1
    assert tasks[0].title == "Write marketing paper on protein shakes"
    assert tasks[0].requester_id == HUMAN_SENDER_ID
    assert tasks[0].owner_id == agent.id
    assert tasks[0].status == "accepted"
    active = db.get_active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == tasks[0].id

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "On it. I will draft the paper and send you the finished version."


@pytest.mark.asyncio
async def test_run_turn_human_chat_manager_accept_with_delegation_plan_creates_child_task_before_reply(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        pm.id,
        "Can you have Taylor write a 3 paragraph whitepaper on SLMs on edge devices?",
        message_type="human",
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
            content=(
                '{"act":"accept","intent":"work","msg":"I’ll get Taylor started on the whitepaper and keep the delivery moving.",'
                '"commit":"work","data":{"task":{"title":"Coordinate edge-device whitepaper","desc":"Own delivery of the edge-device whitepaper and report back to the requester."},'
                '"plan":{"mode":"delegate","children":[{"who":"Taylor","task":{"title":"Write edge-device whitepaper","desc":"Write a 3-paragraph whitepaper on the benefits of SLMs on edge devices for social media outreach.","outs":[{"type":"file","path":"/me/slm-edge-whitepaper.md"}]}}]}},"th":"accept coordination and create Taylor task now"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        pm,
        state,
        {
            "type": "human_chat",
            "content": "Can you have Taylor write a 3 paragraph whitepaper on SLMs on edge devices?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["chat_message"]["content"] == "I’ll get Taylor started on the whitepaper and keep the delivery moving."
    assert not any(item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed" for item in outcome.result["trigger_requests"])
    assignment_request = next(
        item
        for item in outcome.result["trigger_requests"]
        if item["agent_id"] == worker.id and item["trigger_type"] == "task_assigned"
    )

    parent_tasks = db.list_tasks(assigned_to=pm.id)
    child_tasks = db.list_tasks(assigned_to=worker.id)
    assert len(parent_tasks) == 1
    assert len(child_tasks) == 1
    assert child_tasks[0].parent_task_id == parent_tasks[0].id
    assert parent_tasks[0].work_contract is None
    assert child_tasks[0].work_contract is not None
    assert assignment_request["task_id"] == child_tasks[0].id

    diagnostics = db.get_diagnostics(agent_id=pm.id, limit=5)
    assert diagnostics[0]["action_name"] == "accept(work)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_revision_request_after_completion_creates_follow_up_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    completed = db.create_task(
        title="Write marketing paper on protein shakes",
        description="Draft a marketing paper on protein shakes and save the finished document.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        work_contract={"deliverables": [{"type": "file", "path": "/me/protein_shakes.md"}]},
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    db.update_task(
        completed.id,
        status="complete",
        completion_summary="finished the first protein shake paper draft",
        watchdog_pinged_at=None,
    )
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Please revise the protein shake paper to emphasize recovery benefits and tighten the introduction.",
        message_type="human",
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
            content=(
                '{"act":"accept","intent":"work","msg":"Understood. I will revise the finished paper and send you the updated version.",'
                '"commit":"work","data":{"task":{"title":"Revise protein shake paper","desc":"Revise the completed protein shake paper to emphasize recovery benefits and tighten the introduction."}},'
                '"th":"create follow-up revision work for the completed deliverable"}'
            ),
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
            "content": "Please revise the protein shake paper to emphasize recovery benefits and tighten the introduction.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
            "task_id": completed.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 2

    refreshed_completed = db.get_task(completed.id)
    assert refreshed_completed is not None
    assert refreshed_completed.status == "complete"
    assert refreshed_completed.completion_summary == "finished the first protein shake paper draft"

    follow_up = next(task for task in tasks if task.id != completed.id)
    assert follow_up.title == "Revise protein shake paper"
    assert follow_up.status == "accepted"
    assert follow_up.parent_task_id == completed.id
    assert follow_up.requester_id == HUMAN_SENDER_ID
    assert follow_up.owner_id == agent.id
    assert follow_up.work_contract is not None
    assert [item.model_dump() for item in follow_up.work_contract.deliverables] == [
        {"type": "file", "path": "/me/protein_shakes.md", "description": None}
    ]

    resume_request = next(
        item for item in outcome.result["trigger_requests"] if item["trigger_type"] == "activity_resumed"
    )
    assert resume_request["task_id"] == follow_up.id

    active = db.get_active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == follow_up.id

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "Understood. I will revise the finished paper and send you the updated version."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "accept(work)"


@pytest.mark.asyncio
async def test_run_turn_human_requested_task_can_block_and_report_to_human(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Investigate why the nightly import job is failing.",
        message_type="human",
    )
    task = db.create_task(
        title="Investigate nightly import failure",
        description="Find the root cause of the nightly import failure.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

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
            content=(
                '{"act":"block","data":{"why":"I do not have access to the import logs needed to diagnose the failure.",'
                '"msg":"I am blocked because I do not have access to the import logs needed to diagnose the failure."},'
                '"th":"report the blocker and wait for access"}'
            ),
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
            "content": 'Resume work on "Investigate nightly import failure".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.trigger_status == "completed"
    assert outcome.result["event"] == "status_changed"
    assert outcome.result["chat_message"]["content"] == (
        "I am blocked because I do not have access to the import logs needed to diagnose the failure."
    )
    assert outcome.result["chat_notification"]["kind"] == "blocked"
    assert outcome.result["chat_notification"]["human_visible"] is True

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "blocked"
    assert refreshed_task.status_note == "I do not have access to the import logs needed to diagnose the failure."

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am blocked because I do not have access to the import logs needed to diagnose the failure."

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "blocked"
    assert _active_activity(agent.id) is None

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "blocked"


@pytest.mark.asyncio
async def test_run_turn_human_chat_grounded_question_can_clarify_when_snapshot_is_insufficient(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Can you give me a status on the latest project?", message_type="human")

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
            content='{"act":"clarify","intent":"question","msg":"Which project do you mean? I do not have a single latest project identified in my current queue.","commit":"none","th":"need a project name"}',
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
            "content": "Can you give me a status on the latest project?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "Which project do you mean? I do not have a single latest project identified in my current queue."


@pytest.mark.asyncio
async def test_run_turn_human_chat_unsupported_request_declines_without_creating_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Can you transfer money from the company bank account for me?",
        message_type="human",
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
            content=(
                '{"act":"decline","intent":"other","msg":"I cannot do that from here. If you need a finance approval or transfer, use the proper company process.",'
                '"th":"decline the unsupported request cleanly"}'
            ),
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
            "content": "Can you transfer money from the company bank account for me?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert db.list_tasks(assigned_to=agent.id) == []
    assert _active_activity(agent.id) is None

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "I cannot do that from here. If you need a finance approval or transfer, use the proper company process."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "decline(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_missing_file_question_clarifies_after_cli_error(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Can you summarize /me/q4_report.md for me?",
        message_type="human",
    )

    captured_messages: list[list[dict[str, str]]] = []
    responses = iter([
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"cat /me/q4_report.md"},"th":"check whether the report exists"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"clarify","intent":"question","msg":"I do not have /me/q4_report.md available in my current files. If you want, point me to the right file and I can review it.",'
                '"th":"do not invent missing file contents"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Can you summarize /me/q4_report.md for me?",
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
    assert db.list_tasks(assigned_to=agent.id) == []

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "I do not have /me/q4_report.md available in my current files. If you want, point me to the right file and I can review it."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> clarify(none)"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["bm_cli", "clarify"]


@pytest.mark.asyncio
async def test_run_turn_manager_status_question_does_not_invent_worker_completion(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    parent = db.create_task(
        title="Coordinate competitor research",
        description="Coordinate the competitor research and report back.",
        assigned_to=pm.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=pm.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    db.create_task(
        title="Investigate competitor launch",
        description="Review the competitor launch and summarize findings.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        parent_task_id=parent.id,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        pm.id,
        "Did Taylor finish the competitor launch research yet?",
        message_type="human",
    )

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return client.LLMResponse(
            content=(
                '{"act":"reply","intent":"status","msg":"Not yet. Taylor still has the competitor launch assignment open, and I do not have a completion from them yet.",'
                '"th":"report only the known worker status"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_turn(
        pm,
        state,
        {
            "type": "human_chat",
            "content": "Did Taylor finish the competitor launch research yet?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 1
    assert any(
        "AUTHORITATIVE COMMUNICATION SNAPSHOT" in message["content"]
        and "Investigate competitor launch" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(pm.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "Not yet. Taylor still has the competitor launch assignment open, and I do not have a completion from them yet."
    )

    diagnostics = db.get_diagnostics(agent_id=pm.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_unknown_meeting_clarifies_instead_of_inventing_schedule(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "What room is the 2pm design review in?",
        message_type="human",
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
            content=(
                '{"act":"clarify","intent":"question","msg":"I do not have a 2pm design review in my current schedule or recent meeting context. If you mean a specific meeting, send me the details and I can help.",'
                '"th":"do not invent a meeting that is not in context"}'
            ),
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
            "content": "What room is the 2pm design review in?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert db.list_tasks(assigned_to=agent.id) == []

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == (
        "I do not have a 2pm design review in my current schedule or recent meeting context. If you mean a specific meeting, send me the details and I can help."
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "clarify(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_can_chain_cli_discovery_before_final_answer(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "What's your current runtime status? If needed, figure out the right CLI command first.",
        message_type="human",
    )

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
            content='{"act":"cli","data":{"cmd":"help"},"th":"discover the right CLI command first"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"runtime"},"th":"check the live runtime state"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"reply","intent":"status","msg":"I checked the runtime. I am idle in the Main Workspace right now with no active task.",'
                '"th":"answer using the authoritative runtime result"}'
            ),
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
            "content": "What's your current runtime status? If needed, figure out the right CLI command first.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 3
    assert any(
        "BOSSMOD CLI RESULT" in message["content"]
        for message in captured_messages[1]
        if message["role"] == "system"
    )
    assert any(
        "Respond next with a final JSON decision" in message["content"]
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
    assert thread[-1].content == "I checked the runtime. I am idle in the Main Workspace right now with no active task."
    assert db.list_tasks(assigned_to=agent.id) == []

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "bm_cli -> bm_cli -> answer(none)"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["bm_cli", "bm_cli", "answer"]

    first_action = json.loads(detail["steps"][0]["parsed_action"])
    second_action = json.loads(detail["steps"][1]["parsed_action"])
    assert first_action["command"] == "help"
    assert second_action["command"] == "runtime"


@pytest.mark.asyncio
async def test_run_turn_human_chat_scope_change_clarifies_before_replacing_active_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    old_task = db.create_task(
        title="Write API brief",
        description="Draft the API brief for the current release.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, old_task, x=desk_x, y=desk_y)
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Instead of the API brief, should this become a launch memo, or do you want to keep the brief and add a memo too?",
        message_type="human",
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
            content=(
                '{"act":"clarify","intent":"work","msg":"Do you want me to replace the current API brief with a launch memo, or keep the brief and create the memo as separate follow-up work?",'
                '"th":"clarify whether the active task should be replaced"}'
            ),
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
            "content": "Instead of the API brief, should this become a launch memo, or do you want to keep the brief and add a memo too?",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
            "task_id": old_task.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["trigger_requests"][0]["trigger_type"] == "activity_resumed"
    assert outcome.result["trigger_requests"][0]["task_id"] == old_task.id

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 1
    assert tasks[0].id == old_task.id
    assert tasks[0].status == "active"

    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == old_task.id

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "work"]
    assert thread[-1].content == (
        "Do you want me to replace the current API brief with a launch memo, or keep the brief and create the memo as separate follow-up work?"
    )

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "clarify(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_explicit_reprioritization_pauses_old_task_and_accepts_new_work(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    old_task = db.create_task(
        title="Write API brief",
        description="Draft the API brief for the current release.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, old_task, x=desk_x, y=desk_y)
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Stop the API brief for now. It is lower priority. Switch immediately to a launch memo for tomorrow's meeting.",
        message_type="human",
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
            content=(
                '{"act":"accept","intent":"work","msg":"Understood. I am pausing the API brief and switching to the launch memo now.",'
                '"commit":"work","data":{"task":{"title":"Write launch memo","desc":"Draft the launch memo for tomorrow\\u2019s meeting."}},'
                '"th":"replace the active task with the higher-priority launch memo"}'
            ),
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
            "content": "Stop the API brief for now. It is lower priority. Switch immediately to a launch memo for tomorrow's meeting.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
            "task_id": old_task.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"

    tasks = db.list_tasks(assigned_to=agent.id)
    assert len(tasks) == 2
    refreshed_old_task = db.get_task(old_task.id)
    assert refreshed_old_task is not None
    assert refreshed_old_task.status == "pending"
    assert refreshed_old_task.status_note == "Paused for newer accepted work."

    newest_task = next(task for task in tasks if task.id != old_task.id)
    assert newest_task.title == "Write launch memo"
    assert newest_task.status == "accepted"

    resume_request = next(
        item for item in outcome.result["trigger_requests"] if item["trigger_type"] == "activity_resumed"
    )
    assert resume_request["task_id"] == newest_task.id

    active = _active_activity(agent.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == newest_task.id
    assert _paused_work(agent.id, old_task.id) is not None

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "work"]
    assert thread[-1].content == "Understood. I am pausing the API brief and switching to the launch memo now."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "accept(work)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_cancel_active_task_without_replacement(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    active_task = db.create_task(
        title="Write API brief",
        description="Draft the API brief for the current release.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, active_task, x=desk_x, y=desk_y)
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        agent.id,
        "Cancel the API brief. Do not replace it with anything else.",
        message_type="human",
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
            content=(
                '{"act":"cancel","intent":"work","msg":"Understood. I am cancelling the API brief and I will not replace it with new work.",'
                '"th":"close the active task per the human request"}'
            ),
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
            "content": "Cancel the API brief. Do not replace it with anything else.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
            "task_id": active_task.id,
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["trigger_requests"] == []

    refreshed_task = db.get_task(active_task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "abandoned"
    assert refreshed_task.status_note == "Cancelled by human request."

    assert _active_activity(agent.id) is None
    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "idle"

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "work"]
    assert thread[-1].content == "Understood. I am cancelling the API brief and I will not replace it with new work."

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "cancel(none)"


@pytest.mark.asyncio
async def test_run_turn_human_chat_chat_lane_uses_standard_decision_turn(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "Can you head to the meeting room?", message_type="human")

    captured_messages: list[list[dict[str, str]]] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
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

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 1
    assert any(
        "CONVERSATION TURN" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert [msg.message_type for msg in thread] == ["human", "social"]
    assert thread[-1].content == "I am heading to the meeting room now."


@pytest.mark.asyncio
async def test_run_turn_peer_message_grounded_question_uses_shared_communication_lane(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    jason = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")

    captured_messages: list[list[dict[str, str]]] = []
    queued_peer: list[dict] = []

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
        return client.LLMResponse(
            content='{"act":"reply","intent":"status","msg":"I am idle at my desk right now.","commit":"none","th":"share current status"}',
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
            "type": "peer_message",
            "content": "Taylor, what are you up to right now?",
            "from_agent": jason.id,
            "from_name": jason.name,
            "message_type": "social",
            "source_channel": "chat",
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert len(captured_messages) == 1
    assert any(
        "AUTHORITATIVE COMMUNICATION SNAPSHOT" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )
    assert not any(
        "BOSSMOD CLI RESULT" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )
    queued_peer = outcome.result["trigger_requests"]
    assert queued_peer[0]["trigger_type"] == "peer_message"
    assert queued_peer[0]["payload"]["content"] == "I am idle at my desk right now."

    diagnostics = db.get_diagnostics(agent_id=taylor.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert [step["action_name"] for step in detail["steps"]] == ["answer"]


@pytest.mark.asyncio
async def test_run_turn_peer_message_social_greeting_replies_conversationally(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    jason = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")

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
            content='{"act":"reply","intent":"social","msg":"Morning Jason. I am here and ready to help.","th":"return the greeting"}',
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
            "type": "peer_message",
            "content": "Good morning Taylor.",
            "from_agent": jason.id,
            "from_name": jason.name,
            "message_type": "social",
            "source_channel": "chat",
        },
    )

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["trigger_requests"] == []
    assert db.list_tasks(assigned_to=taylor.id) == []

    diagnostics = db.get_diagnostics(agent_id=taylor.id, limit=5)
    assert diagnostics[0]["action_name"] == "answer(none)"


@pytest.mark.asyncio
async def test_run_turn_peer_message_meeting_request_accepts_and_walks_to_meeting(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    jason = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")

    responses = iter([
        client.LLMResponse(
            content=(
                '{"act":"accept","intent":"meeting","msg":"I am on my way to the meeting room now.",'
                '"commit":"meeting","data":{"dst":"meeting","title":"Direct meeting","detail":"Meet Jason in the meeting room for a quick sync."},'
                '"th":"accept the peer meeting request"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"walk","data":{"dst":"meeting"},"th":"head to the meeting room"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    monkeypatch.setattr(manager, "broadcast_world_state", _noop)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)
    monkeypatch.setattr(manager, "broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    decision_outcome = await run_turn(
        taylor,
        state,
        {
            "type": "peer_message",
            "content": "Meet me in the meeting room for a quick sync.",
            "from_agent": jason.id,
            "from_name": jason.name,
            "message_type": "social",
            "source_channel": "chat",
        },
    )

    assert decision_outcome.result["event"] == "decision_applied"
    queued_peer = next(
        item for item in decision_outcome.result["trigger_requests"] if item["trigger_type"] == "peer_message"
    )
    assert queued_peer["payload"]["content"] == "I am on my way to the meeting room now."

    resume_request = next(
        item for item in decision_outcome.result["trigger_requests"] if item["trigger_type"] == "activity_resumed"
    )
    assert resume_request["agent_id"] == taylor.id

    active = _active_activity(taylor.id)
    assert active is not None
    assert active.kind == "meeting"

    resumed_state = db.get_agent_state(taylor.id)
    assert resumed_state is not None
    execution_outcome = await run_turn(
        taylor,
        resumed_state,
        {
            "type": "activity_resumed",
            "content": "Meet Jason in the meeting room for a quick sync.",
            "source_channel": "chat",
        },
    )

    assert execution_outcome.trigger_status == "completed"
    assert execution_outcome.result["event"] == "agent_moved"
    assert execution_outcome.result["path"]
    movement = _active_movement(taylor.id)
    assert movement is not None

    diagnostics = db.get_diagnostics(agent_id=taylor.id, limit=5)
    assert diagnostics[0]["action_name"] == "walkTo"


@pytest.mark.asyncio
async def test_activity_resumed_wait_pauses_active_work_until_a_dependency_resolves(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Coordinate competitor launch research",
        description="Delegate the investigation and wait for Taylor's report.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

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
            content='{"act":"wait","data":{"why":"Waiting for Taylor to finish the delegated investigation.","msg":"I delegated the investigation to Taylor and I am waiting for the report back."},"th":"pause until Taylor reports back"}',
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
            "content": 'Resume work on "Coordinate competitor launch research".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    assert outcome.result["event"] == "status_changed"
    assert outcome.result["detail"] == 'Pat is waiting on "Coordinate competitor launch research" — Waiting for Taylor to finish the delegated investigation.'

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "waiting"
    assert refreshed_task.status_note == "Waiting for Taylor to finish the delegated investigation."

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "waiting"

    active = _active_activity(agent.id)
    assert active is None

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["action_name"] == "waiting"


@pytest.mark.asyncio
async def test_run_turn_manager_repairs_wrong_agent_chat_lane_with_assign(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    pm_state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        pm.id,
        "Please coordinate the research brief and delegate the writing to Taylor.",
        message_type="human",
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
            content=(
                '{"act":"accept","intent":"work","msg":"I will coordinate the brief and get Taylor started.",'
                '"commit":"work","data":{"task":{"title":"Coordinate research brief","desc":"Coordinate the research brief and delegate the writing to Taylor."}},'
                '"th":"accept the coordination task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
            client.LLMResponse(
                content=json.dumps(
                    {
                        "act": "socialmsg",
                        "data": {
                            "to": "agent",
                            "aid": worker.id,
                            "msg": "Please start the research brief now.",
                        },
                    "th": "try to chat Taylor directly",
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=json.dumps(
                {
                    "act": "assign",
                    "data": {
                        "aid": worker.id,
                        "task": {
                            "title": "Write research brief",
                            "desc": "Draft the research brief and report back to Pat.",
                        },
                    },
                    "th": "create the delegated task instead",
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"wait","data":{"why":"Waiting for Taylor to work the assigned task.","msg":"I delegated the writing to Taylor and I am waiting for the draft."},"th":"pause until Taylor starts the task"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    initial_outcome = await run_turn(
        pm,
        pm_state,
        {
            "type": "human_chat",
            "content": "Please coordinate the research brief and delegate the writing to Taylor.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    pm_resume_request = next(
        item
        for item in initial_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed"
    )

    pm_resume_state = activity_runtime.refresh_agent_status(pm.id)
    assert pm_resume_state is not None
    delegation_outcome = await run_turn(
        pm,
        pm_resume_state,
        {
            **pm_resume_request["payload"],
            "type": "activity_resumed",
            "task_id": pm_resume_request["task_id"],
            "source_channel": pm_resume_request["source_channel"],
        },
    )

    assert not any(item["trigger_type"] == "peer_message" for item in delegation_outcome.result["trigger_requests"])
    assignment_request = next(
        item
        for item in delegation_outcome.result["trigger_requests"]
        if item["agent_id"] == worker.id and item["trigger_type"] == "task_assigned"
    )
    assert assignment_request["payload"]["task_title"] == "Write research brief"

    worker_tasks = db.list_tasks(assigned_to=worker.id)
    assert len(worker_tasks) == 1
    assert worker_tasks[0].parent_task_id is not None

    diagnostics = db.get_diagnostics(agent_id=pm.id, limit=5)
    assert diagnostics[0]["action_name"] == "message -> delegateTask -> waiting"


@pytest.mark.asyncio
async def test_run_turn_end_to_end_manager_delegation_chain_reports_back_to_human(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    pm_state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        pm.id,
        "Please coordinate the competitor launch research and delegate the investigation to Taylor.",
        message_type="human",
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
            content=(
                '{"act":"accept","intent":"work","msg":"I will coordinate the research handoff and get Taylor started.",'
                '"commit":"work","data":{"task":{"title":"Coordinate competitor launch research",'
                '"desc":"Coordinate the competitor launch research assignment and delegate the investigation to Taylor."}},'
                '"th":"accept the coordination task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=json.dumps(
                {
                    "act": "assign",
                    "data": {
                        "aid": worker.id,
                        "task": {
                            "title": "Investigate competitor launches",
                            "desc": "Review recent competitor launches and summarize the useful takeaways for Pat.",
                        },
                    },
                    "th": "delegate the investigation to Taylor",
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"wait","data":{"why":"Waiting for Taylor to finish the delegated investigation.","msg":"Taylor owns the investigation draft and I am waiting for the findings before I wrap the coordination task."},"th":"pause until Taylor reports back"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"accept","intent":"work","msg":"I will take the investigation and report back with the findings.",'
                '"commit":"work","th":"accept the delegated task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"researched competitor launches",'
                '"msg":"I finished the competitor launch investigation and summarized the useful takeaways."},'
                '"th":"report completion to Pat"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"observe","intent":"other","th":"review the completion update and continue coordination work"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"coordinated the competitor launch research",'
                '"msg":"Taylor finished the competitor launch investigation. I reviewed the takeaways and the summary is ready for you."},'
                '"th":"report the finished coordination back to the human"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    def _materialize_trigger(request: dict[str, object]) -> dict[str, object]:
        trigger = dict(request["payload"])
        trigger["type"] = request["trigger_type"]
        trigger["source_channel"] = request["source_channel"]
        if request.get("task_id") is not None:
            trigger["task_id"] = request["task_id"]
        return trigger

    async def _run_trigger_request(request: dict[str, object]):
        agent = db.get_agent(request["agent_id"])
        assert agent is not None
        trigger = _materialize_trigger(request)
        prepare_trigger_context(agent.id, trigger)
        state = activity_runtime.refresh_agent_status(agent.id)
        assert state is not None
        return await run_turn(agent, state, trigger)

    monkeypatch.setattr(client, "completion", fake_completion)

    initial_outcome = await run_turn(
        pm,
        pm_state,
        {
            "type": "human_chat",
            "content": "Please coordinate the competitor launch research and delegate the investigation to Taylor.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    parent_tasks = db.list_tasks(assigned_to=pm.id)
    assert len(parent_tasks) == 1
    parent = parent_tasks[0]
    assert parent.title == "Coordinate competitor launch research"
    assert parent.status == "accepted"

    pm_resume_request = next(
        item
        for item in initial_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed"
    )
    delegation_outcome = await _run_trigger_request(pm_resume_request)

    refreshed_parent = db.get_task(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.status == "waiting"
    assert refreshed_parent.completion_summary is None

    refreshed_pm_state = db.get_agent_state(pm.id)
    assert refreshed_pm_state is not None
    assert refreshed_pm_state.status == "waiting"

    pm_active = _active_activity(pm.id)
    assert pm_active is None

    worker_tasks = db.list_tasks(assigned_to=worker.id)
    assert len(worker_tasks) == 1
    child = worker_tasks[0]
    assert child.title == "Investigate competitor launches"
    assert child.parent_task_id == parent.id
    assert child.requester_id == pm.id
    assert child.owner_id == pm.id
    assert child.status == "pending"

    assignment_request = next(
        item
        for item in delegation_outcome.result["trigger_requests"]
        if item["agent_id"] == worker.id and item["trigger_type"] == "task_assigned"
    )
    assignment_outcome = await _run_trigger_request(assignment_request)

    assert not any(
        item["agent_id"] == pm.id and item["trigger_type"] == "task_follow_up"
        for item in assignment_outcome.result["trigger_requests"]
    )

    worker_resume_request = next(
        item
        for item in assignment_outcome.result["trigger_requests"]
        if item["agent_id"] == worker.id and item["trigger_type"] == "activity_resumed"
    )
    completion_outcome = await _run_trigger_request(worker_resume_request)

    completion_update = next(
        item
        for item in completion_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "task_update"
    )
    assert completion_update["payload"]["attention_kind"] == "completion_report"
    assert completion_update["task_id"] == parent.id
    assert completion_update["payload"]["content"] == (
        'Child task "Investigate competitor launches" completed by Taylor: '
        "I finished the competitor launch investigation and summarized the useful takeaways."
    )

    refreshed_child = db.get_task(child.id)
    assert refreshed_child is not None
    assert refreshed_child.status == "complete"
    assert refreshed_child.completion_summary == "researched competitor launches"

    pm_reply_outcome = await _run_trigger_request(completion_update)
    assert not any(
        item["agent_id"] == worker.id and item["trigger_type"] == "task_follow_up"
        for item in pm_reply_outcome.result["trigger_requests"]
    )

    pm_final_resume = next(
        item
        for item in pm_reply_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed"
    )
    pm_final_outcome = await _run_trigger_request(pm_final_resume)

    refreshed_parent = db.get_task(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.status == "complete"
    assert refreshed_parent.completion_summary == "coordinated the competitor launch research"

    manager_thread = db.get_human_chat_thread(pm.id, limit=20)
    manager_contents = [msg.content for msg in manager_thread]
    assert "Please coordinate the competitor launch research and delegate the investigation to Taylor." in manager_contents
    assert "I will coordinate the research handoff and get Taylor started." in manager_contents
    assert (
        "Taylor finished the competitor launch investigation. I reviewed the takeaways and the summary is ready for you."
        in manager_contents
    )

    worker_events = db.list_task_events(child.id, limit=50)
    worker_contents = [event.content for event in worker_events]
    assert "I will take the investigation and report back with the findings." in worker_contents
    assert "I finished the competitor launch investigation and summarized the useful takeaways." in worker_contents

    parent_events = db.list_task_events(parent.id, limit=50)
    parent_contents = [event.content for event in parent_events]
    assert 'Child task "Investigate competitor launches" completed by Taylor:' in " ".join(parent_contents)

    assert pm_final_outcome.result["chat_message"]["content"] == (
        "Taylor finished the competitor launch investigation. I reviewed the takeaways and the summary is ready for you."
    )

    assert _active_activity(pm.id) is None
    assert _active_activity(worker.id) is None


@pytest.mark.asyncio
async def test_run_turn_end_to_end_manager_reassigns_after_worker_block_and_reports_back_to_human(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    first_worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    second_worker = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    pm_state = db.update_agent_state(pm.id, x=desk_x, y=desk_y, status="idle")
    human_msg = db.create_message(
        HUMAN_SENDER_ID,
        pm.id,
        "Please coordinate the competitor launch research and delegate it.",
        message_type="human",
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
            content=(
                '{"act":"accept","intent":"work","msg":"I will coordinate the research handoff and keep the delegation moving.",'
                '"commit":"work","data":{"task":{"title":"Coordinate competitor launch research",'
                '"desc":"Coordinate the competitor launch research assignment, handle blockers, and report the result back to the human."}},'
                '"th":"accept the coordination task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=json.dumps(
                {
                    "act": "assign",
                    "data": {
                        "aid": first_worker.id,
                        "task": {
                            "title": "Investigate competitor launches",
                            "desc": "Review recent competitor launches and summarize the useful takeaways for Pat.",
                        },
                    },
                    "th": "delegate the first investigation attempt to Taylor",
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"wait","data":{"why":"Waiting for Taylor to report back or block.","msg":"Taylor owns the first investigation pass and I am waiting for the outcome before I continue the parent task."},"th":"pause until Taylor updates the task"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"accept","intent":"work","msg":"I will investigate the launches and report back if I hit anything blocking.",'
                '"commit":"work","th":"accept the first delegated task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"block","data":{"why":"I do not have access to the launch archive.",'
                '"msg":"I am blocked because I do not have access to the launch archive."},'
                '"th":"report the blocker to Pat"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"observe","intent":"other","th":"review the blocker update and continue coordination work"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=json.dumps(
                {
                    "act": "assign",
                    "data": {
                        "aid": second_worker.id,
                        "task": {
                            "title": "Investigate competitor launches",
                            "desc": "Review recent competitor launches and summarize the useful takeaways for Pat.",
                        },
                    },
                    "th": "reassign the investigation to Morgan",
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"wait","data":{"why":"Waiting for Morgan to complete the reassigned investigation.","msg":"I reassigned the investigation to Morgan and I am waiting for the updated findings before I complete the parent task."},"th":"pause until Morgan reports back"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"accept","intent":"work","msg":"I will take the reassigned investigation and report back with the summary.",'
                '"commit":"work","th":"accept the reassigned task"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"researched competitor launches after reassignment",'
                '"msg":"I finished the reassigned competitor launch investigation and the takeaways are ready."},'
                '"th":"report completion to Pat after reassignment"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"observe","intent":"other","th":"review the completion update and continue coordination work"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                '{"act":"done","data":{"sum":"coordinated the competitor launch research after a reassignment",'
                '"msg":"Taylor was blocked on archive access, so I reassigned the investigation to Morgan and now have the finished summary for you."},'
                '"th":"report the reassigned completion back to the human"}'
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    def _materialize_trigger(request: dict[str, object]) -> dict[str, object]:
        trigger = dict(request["payload"])
        trigger["type"] = request["trigger_type"]
        trigger["source_channel"] = request["source_channel"]
        if request.get("task_id") is not None:
            trigger["task_id"] = request["task_id"]
        return trigger

    async def _run_trigger_request(request: dict[str, object]):
        agent = db.get_agent(request["agent_id"])
        assert agent is not None
        trigger = _materialize_trigger(request)
        prepare_trigger_context(agent.id, trigger)
        state = activity_runtime.refresh_agent_status(agent.id)
        assert state is not None
        return await run_turn(agent, state, trigger)

    monkeypatch.setattr(client, "completion", fake_completion)

    initial_outcome = await run_turn(
        pm,
        pm_state,
        {
            "type": "human_chat",
            "content": "Please coordinate the competitor launch research and delegate it.",
            "from_name": "Human Operator",
            "source_message_id": human_msg.id,
        },
    )

    parent = db.list_tasks(assigned_to=pm.id)[0]

    pm_first_resume = next(
        item
        for item in initial_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed"
    )
    first_delegate_outcome = await _run_trigger_request(pm_first_resume)

    first_assignment_request = next(
        item
        for item in first_delegate_outcome.result["trigger_requests"]
        if item["agent_id"] == first_worker.id and item["trigger_type"] == "task_assigned"
    )
    first_assignment_outcome = await _run_trigger_request(first_assignment_request)

    first_worker_resume = next(
        item
        for item in first_assignment_outcome.result["trigger_requests"]
        if item["agent_id"] == first_worker.id and item["trigger_type"] == "activity_resumed"
    )
    first_block_outcome = await _run_trigger_request(first_worker_resume)

    blocked_update = next(
        item
        for item in first_block_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "task_update"
    )
    assert blocked_update["payload"]["attention_kind"] == "blocker"
    assert blocked_update["task_id"] == parent.id
    assert blocked_update["payload"]["content"] == (
        'Child task "Investigate competitor launches" blocked by Taylor: '
        "I am blocked because I do not have access to the launch archive."
    )

    pm_ack_outcome = await _run_trigger_request(blocked_update)
    assert not any(
        item["agent_id"] == first_worker.id and item["trigger_type"] == "task_follow_up"
        for item in pm_ack_outcome.result["trigger_requests"]
    )

    pm_second_resume = next(
        item
        for item in pm_ack_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed"
    )
    second_delegate_outcome = await _run_trigger_request(pm_second_resume)

    second_assignment_request = next(
        item
        for item in second_delegate_outcome.result["trigger_requests"]
        if item["agent_id"] == second_worker.id and item["trigger_type"] == "task_assigned"
    )
    second_assignment_outcome = await _run_trigger_request(second_assignment_request)

    second_worker_resume = next(
        item
        for item in second_assignment_outcome.result["trigger_requests"]
        if item["agent_id"] == second_worker.id and item["trigger_type"] == "activity_resumed"
    )
    second_completion_outcome = await _run_trigger_request(second_worker_resume)

    completion_update = next(
        item
        for item in second_completion_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "task_update"
    )
    assert completion_update["payload"]["attention_kind"] == "completion_report"
    assert completion_update["task_id"] == parent.id
    assert completion_update["payload"]["content"] == (
        'Child task "Investigate competitor launches" completed by Morgan: '
        "I finished the reassigned competitor launch investigation and the takeaways are ready."
    )

    pm_reply_outcome = await _run_trigger_request(completion_update)
    assert not any(
        item["agent_id"] == second_worker.id and item["trigger_type"] == "task_follow_up"
        for item in pm_reply_outcome.result["trigger_requests"]
    )

    pm_final_resume = next(
        item
        for item in pm_reply_outcome.result["trigger_requests"]
        if item["agent_id"] == pm.id and item["trigger_type"] == "activity_resumed"
    )
    pm_final_outcome = await _run_trigger_request(pm_final_resume)

    first_child = db.list_tasks(assigned_to=first_worker.id)[0]
    second_child = db.list_tasks(assigned_to=second_worker.id)[0]
    refreshed_parent = db.get_task(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.status == "complete"
    assert refreshed_parent.completion_summary == "coordinated the competitor launch research after a reassignment"

    assert first_child.parent_task_id == parent.id
    assert first_child.status == "blocked"
    assert first_child.status_note == "I do not have access to the launch archive."

    assert second_child.parent_task_id == parent.id
    assert second_child.status == "complete"
    assert second_child.completion_summary == "researched competitor launches after reassignment"

    manager_thread = db.get_human_chat_thread(pm.id, limit=20)
    manager_contents = [msg.content for msg in manager_thread]
    assert "Please coordinate the competitor launch research and delegate it." in manager_contents
    assert "I will coordinate the research handoff and keep the delegation moving." in manager_contents
    assert (
        "Taylor was blocked on archive access, so I reassigned the investigation to Morgan and now have the finished summary for you."
        in manager_contents
    )

    first_worker_events = db.list_task_events(first_child.id, limit=50)
    first_worker_contents = [event.content for event in first_worker_events]
    assert "I am blocked because I do not have access to the launch archive." in first_worker_contents

    second_worker_events = db.list_task_events(second_child.id, limit=50)
    second_worker_contents = [event.content for event in second_worker_events]
    assert "I will take the reassigned investigation and report back with the summary." in second_worker_contents
    assert "I finished the reassigned competitor launch investigation and the takeaways are ready." in second_worker_contents

    parent_events = db.list_task_events(parent.id, limit=80)
    parent_contents = [event.content for event in parent_events]
    assert 'Child task "Investigate competitor launches" blocked by Taylor:' in " ".join(parent_contents)
    assert 'Child task "Investigate competitor launches" completed by Morgan:' in " ".join(parent_contents)

    assert pm_final_outcome.result["chat_message"]["content"] == (
        "Taylor was blocked on archive access, so I reassigned the investigation to Morgan and now have the finished summary for you."
    )

    assert _active_activity(pm.id) is None
    assert _active_activity(first_worker.id) is None
    assert _active_activity(second_worker.id) is None


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
            content="{\"act\":\"socialmsg\",\"data\":{\"to\":\"human\",\"msg\":\"Sure, let's discuss the project timeline.\"},\"th\":\"reply in the room\"}",
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


def test_prompt_history_view_human_chat_excludes_current_trigger_message(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    earlier = db.create_message(HUMAN_SENDER_ID, agent.id, "Earlier request.", message_type="human")
    db.create_message(agent.id, HUMAN_SENDER_ID, "Earlier reply.", message_type="social")
    current = db.create_message(HUMAN_SENDER_ID, agent.id, "Current request.", message_type="human")

    view = build_prompt_history_view(
        agent,
        {
            "type": "human_chat",
            "source_channel": "chat",
            "from_name": "Human Operator",
            "content": current.content,
            "source_message_id": current.id,
        },
        token_model="test-model",
    )

    assert earlier.id != current.id
    assert [msg["content"] for msg in view.conversation_history] == ["Earlier request.", "Earlier reply."]


def test_prompt_history_view_peer_message_excludes_current_trigger_message(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    other = db.create_agent(name="Michael", desk_x=desk_x, desk_y=desk_y)
    db.create_message(other.id, agent.id, "Earlier note.", message_type="social")
    db.create_message(agent.id, other.id, "Earlier response.", message_type="social")
    current = db.create_message(other.id, agent.id, "Current note.", message_type="social")

    view = build_prompt_history_view(
        agent,
        {
            "type": "peer_message",
            "source_channel": "chat",
            "from_agent": other.id,
            "from_name": other.name,
            "content": current.content,
            "source_message_id": current.id,
        },
        token_model="test-model",
    )

    assert [msg["content"] for msg in view.conversation_history] == ["Earlier note.", "Earlier response."]


def test_prompt_history_view_task_follow_up_uses_task_thread_and_excludes_current_source_message(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Write paper",
        description="Draft the paper and send it back.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
    )
    db.create_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=pm.id,
        author_name=pm.name,
        event_type="assignment",
        content='Assigned "Write paper" to Taylor.',
    )
    db.create_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=worker.id,
        author_name=worker.name,
        event_type="clarification",
        content="What format should this be in?",
    )
    db.create_message(pm.id, worker.id, "Unrelated DM thread note.", message_type="social")
    current = db.create_message(pm.id, worker.id, "Current follow-up note.", message_type="work")
    db.create_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=pm.id,
        author_name=pm.name,
        event_type="clarification",
        content=current.content,
        source_message_id=current.id,
    )

    view = build_prompt_history_view(
        worker,
        {
            "type": "task_follow_up",
            "source_channel": "work",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "task_status": "pending",
            "task_party": "assignee",
            "from_agent": pm.id,
            "from_name": pm.name,
            "content": current.content,
            "source_message_id": current.id,
        },
        token_model="test-model",
    )

    contents = [msg["content"] for msg in view.conversation_history]
    assert contents == [
        '(assignment) Assigned "Write paper" to Taylor.',
        "(clarification) What format should this be in?",
    ]
    assert "Unrelated DM thread note." not in contents


def test_prompt_history_view_task_assigned_uses_task_thread(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Write paper",
        description="Draft the paper and send it back.",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
    )
    db.create_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=pm.id,
        author_name=pm.name,
        event_type="assignment",
        content='Assigned "Write paper" to Taylor.',
    )

    view = build_prompt_history_view(
        worker,
        {
            "type": "task_assigned",
            "source_channel": "work",
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "from_agent": pm.id,
            "from_name": pm.name,
        },
        token_model="test-model",
    )

    assert [msg["content"] for msg in view.conversation_history] == ['(assignment) Assigned "Write paper" to Taylor.']


def test_build_context_human_chat_includes_current_request_once(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.update_agent_state(agent.id, x=desk_x, y=desk_y, status="idle")
    db.create_message(HUMAN_SENDER_ID, agent.id, "Earlier request.", message_type="human")
    db.create_message(agent.id, HUMAN_SENDER_ID, "Earlier reply.", message_type="social")
    current = db.create_message(HUMAN_SENDER_ID, agent.id, "Current request.", message_type="human")

    history = build_prompt_history_view(
        agent,
        {
            "type": "human_chat",
            "source_channel": "chat",
            "from_name": "Human Operator",
            "content": current.content,
            "source_message_id": current.id,
        },
        token_model="test-model",
    )

    context = context_builder.build_context(
        context_builder.TurnContext(
            agent=agent,
            state=state,
            trigger={
                "type": "human_chat",
                "source_channel": "chat",
                "from_name": "Human Operator",
                "content": current.content,
                "source_message_id": current.id,
            },
            conversation_history=history.conversation_history,
            prompt_notifications=[],
            reference_materials=[],
            nearby_agents=[],
            pending_trigger_count=0,
            contract_kind="decision",
        )
    )

    rendered_user_text = "\n".join(message["content"] for message in context if message["role"] == "user")
    assert rendered_user_text.count("Current request.") == 1


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


@pytest.mark.asyncio
async def test_activity_resumed_managed_writer_saves_long_file_and_commits_once(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write the research paper",
        description="Draft a long markdown paper about frontend technology choices.",
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

    responses = iter([
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"write /me/paper.md"},"th":"open managed writer"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content=(
                "# Frontend Stack Review\n\n"
                "This paper compares delivery options for modern frontend systems.\n\n"
                "## Recommendation\n\n"
                "Prefer a small, typed React stack with deliberate performance budgets.\n"
                "<<BOSSMOD_FILE_DONE>>"
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"done","data":{"sum":"saved paper"},"th":"complete task"}',
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
            "content": 'Resume work on "Write the research paper".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    paper_path = agent_artifact_dir(agent.storage_key) / "paper.md"
    assert paper_path.read_text(encoding="utf-8") == (
        "# Frontend Stack Review\n\n"
        "This paper compares delivery options for modern frontend systems.\n\n"
        "## Recommendation\n\n"
        "Prefer a small, typed React stack with deliberate performance budgets.\n"
    )
    events = db.list_bm_cli_events(agent_id=agent.id, limit=10)
    assert [event["result_kind"] for event in events if event["result_kind"] in {"write", "append"}] == ["write"]
    history = execute_bm_cli(agent, state, "git log 50")
    assert history.ok is True
    assert "bm_cli write /me/paper.md" in history.data["output"]
    assert outcome.trigger_status == "completed"

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    assert detail["steps"][0]["prompt_tokens"] == 20
    assert detail["steps"][0]["completion_tokens"] == 20
    assert detail["steps"][0]["total_tokens"] == 40
    cli_step = json.loads(detail["steps"][0]["result"])
    assert cli_step["managed_writer"]["used"] is True
    assert cli_step["managed_writer"]["attempted"] is True
    assert cli_step["managed_writer"]["completed"] is True
    assert cli_step["managed_writer"]["strategy"] == "single_pass"
    assert cli_step["managed_writer"]["calls"] == 1
    assert cli_step["managed_writer"]["chunks"] == 1
    assert cli_step["managed_writer"]["total_tokens"] == 20
    assert "via managed writer (single-pass, 1 call)" in cli_step["detail"]


@pytest.mark.asyncio
async def test_activity_resumed_batch_writer_saves_multiple_files_and_commits_once(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write the package",
        description="Draft the summary and appendix files.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
        work_contract={
            "deliverables": [
                {"type": "file", "path": "/me/summary.md"},
                {"type": "file", "path": "/me/appendix.md"},
            ],
        },
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

    responses = iter([
        client.LLMResponse(
            content=json.dumps(
                {
                    "act": "cli",
                    "data": {
                        "cmd": "batch-write",
                        "body": "/me/summary.md :: One-page executive summary\n/me/appendix.md :: Detailed appendix with supporting notes",
                    },
                    "th": "write both files",
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content="# Summary\n\nA concise executive summary.\n<<BOSSMOD_FILE_DONE>>",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content="# Appendix\n\nSupporting detail and notes.\n<<BOSSMOD_FILE_DONE>>",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"done","data":{"sum":"saved package"},"th":"complete task"}',
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
            "content": 'Resume work on "Write the package".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    summary_path = agent_artifact_dir(agent.storage_key) / "summary.md"
    appendix_path = agent_artifact_dir(agent.storage_key) / "appendix.md"
    assert summary_path.read_text(encoding="utf-8") == "# Summary\n\nA concise executive summary.\n"
    assert appendix_path.read_text(encoding="utf-8") == "# Appendix\n\nSupporting detail and notes.\n"

    events = db.list_bm_cli_events(agent_id=agent.id, limit=10)
    assert [event["result_kind"] for event in events if event["result_kind"] in {"write", "append", "batch-write"}] == ["batch-write"]

    history = execute_bm_cli(agent, state, "git log 50")
    assert history.ok is True
    assert "bm_cli bwrite 2 files" in history.data["output"]

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "complete"
    assert outcome.trigger_status == "completed"

    artifacts = db.list_artifacts(agent_id=agent.id, limit=10)
    assert {artifact.virtual_path for artifact in artifacts} >= {"/me/summary.md", "/me/appendix.md"}

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    cli_step = json.loads(detail["steps"][0]["result"])
    assert cli_step["managed_writer"]["used"] is True
    assert cli_step["managed_writer"]["strategy"] == "single_pass"
    assert cli_step["managed_writer"]["calls"] == 2
    assert cli_step["batch_writer"]["used"] is True
    assert cli_step["batch_writer"]["file_count"] == 2
    assert "via batch writer" in cli_step["detail"]


@pytest.mark.asyncio
async def test_activity_resumed_rewrite_section_rewrites_only_target_section(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Tighten the launch brief",
        description="Rewrite the recommendation section to be more executive-friendly.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)

    report_path = agent_artifact_dir(agent.storage_key) / "report.md"
    report_path.write_text(
        "# Launch Brief\n\n"
        "## Recommendation\n\n"
        "Use the broad launch plan with many optional paths.\n\n"
        "## Risks\n\n"
        "Coordination may slip.\n",
        encoding="utf-8",
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
            content='{"act":"cli","data":{"cmd":"rewsect /me/report.md \\"## Recommendation\\"","body":"Make this tighter, more executive-friendly, and action-oriented."},"th":"rewrite the recommendation"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content="## Recommendation\n\nFocus the launch on the highest-confidence path, commit to one owner, and cut optional workstreams.\n",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content='{"act":"done","data":{"sum":"rewrote recommendation"},"th":"complete task"}',
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
            "content": 'Resume work on "Tighten the launch brief".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    assert report_path.read_text(encoding="utf-8") == (
        "# Launch Brief\n\n"
        "## Recommendation\n\n"
        "Focus the launch on the highest-confidence path, commit to one owner, and cut optional workstreams.\n\n"
        "## Risks\n\n"
        "Coordination may slip.\n"
    )

    events = db.list_bm_cli_events(agent_id=agent.id, limit=10)
    assert [event["result_kind"] for event in events if event["result_kind"] in {"rewrite-section"}] == ["rewrite-section"]
    history = execute_bm_cli(agent, state, "git log 50")
    assert history.ok is True
    assert "bm_cli rewsect /me/report.md ## Recommendation" in history.data["output"]
    assert outcome.trigger_status == "completed"

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    detail = db.get_diagnostic(diagnostics[0]["id"])
    assert detail is not None
    cli_step = json.loads(detail["steps"][0]["result"])
    assert cli_step["managed_writer"]["used"] is True
    assert cli_step["managed_writer"]["strategy"] == "section_rewrite"
    assert cli_step["managed_writer"]["calls"] == 1
    assert "via managed writer (section-rewrite, 1 call)" in cli_step["detail"]


@pytest.mark.asyncio
async def test_managed_writer_uses_section_plan_when_single_pass_defers(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.get_agent_state(agent.id)
    assert state is not None

    responses = iter([
        client.LLMResponse(
            content="<<BOSSMOD_PLAN_REQUIRED>>",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content=json.dumps(
                {
                    "sections": [
                        {"heading": "# Protein Shakes", "goal": "Introduce the document and summarize the core benefits."},
                        {"heading": "## Recommendation", "goal": "Explain the practical recommendation and usage guidance."},
                    ]
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content="Protein shakes are a practical way to increase protein intake and support recovery.\n",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content='## Recommendation\n\nUse them when convenience matters and match the product to the dietary goal.\n',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    ])

    async def fake_completion(**kwargs):
        return next(responses)

    monkeypatch.setattr(client, "completion", fake_completion)

    outcome = await run_managed_write(
        agent=agent,
        state=state,
        command="write /me/paper.md",
        model="test-model",
        api_config={},
        base_context=[],
        action_response='{"act":"cli","data":{"cmd":"write /me/paper.md"},"th":"start"}',
        trigger_type="activity_resumed",
    )

    assert outcome.cli_result.ok is True
    assert outcome.chunks == 4
    assert (agent_artifact_dir(agent.storage_key) / "paper.md").read_text(encoding="utf-8") == (
        "# Protein Shakes\n\n"
        "Protein shakes are a practical way to increase protein intake and support recovery.\n\n"
        "## Recommendation\n\n"
        "Use them when convenience matters and match the product to the dietary goal.\n"
    )
    data = outcome.cli_result.data or {}
    assert data["managed_writer_attempted"] is True
    assert data["managed_writer_used"] is True
    assert data["managed_writer_completed"] is True
    assert data["managed_strategy"] == "sectioned"
    assert data["managed_calls"] == 4
    assert data["managed_chunks"] == 4
    assert data["managed_sections"] == 2
    assert data["managed_prompt_tokens"] == 40
    assert data["managed_completion_tokens"] == 20
    assert data["managed_total_tokens"] == 60
    assert data["managed_bytes"] > 0


@pytest.mark.asyncio
async def test_activity_resumed_managed_writer_broadcasts_progress_updates(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Write the progress paper",
        description="Draft a markdown paper and show writing progress.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)

    progress_snapshots: list[dict[str, Any]] = []

    async def _broadcast_activity(*, event: str, detail: str, agent_name=None, extra=None):
        if event != "managed_writer_progress":
            return
        current_task = db.get_task(task.id)
        current_activity = db.get_active_activity(agent.id)
        progress_snapshots.append(
            {
                "detail": detail,
                "extra": extra or {},
                "status_note": current_task.status_note if current_task else None,
                "activity_detail": current_activity.detail if current_activity else None,
            }
        )

    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_world_state", _noop)
    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_activity", _broadcast_activity)
    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_feed_update", _noop)
    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_chat_message", _noop)
    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_diagnostic", _noop)
    monkeypatch.setattr("core.runtime.events.runtime_events.broadcast_thought", _noop)
    monkeypatch.setattr(routing, "select_model_with_source", lambda _agent, _mode: ("test-model", "agent"))
    monkeypatch.setattr(routing, "get_api_config", lambda _agent: {})

    responses = iter([
        client.LLMResponse(
            content='{"act":"cli","data":{"cmd":"write /me/paper.md"},"th":"start writing"}',
            model="test-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
        client.LLMResponse(
            content="<<BOSSMOD_PLAN_REQUIRED>>",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content=json.dumps(
                {
                    "sections": [
                        {"heading": "# Overview", "goal": "Introduce the topic."},
                        {"heading": "## Recommendation", "goal": "Explain the recommended approach."},
                    ]
                }
            ),
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content="Protein shakes are useful when convenience matters.\n",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content="Use them to supplement, not replace, whole-food meals.\n",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        client.LLMResponse(
            content='{"act":"done","data":{"sum":"saved paper"},"th":"complete task"}',
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
            "content": 'Resume work on "Write the progress paper".',
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "source_channel": "work",
        },
    )

    assert outcome.trigger_status == "completed"
    assert [item["detail"] for item in progress_snapshots] == [
        "Taylor: Writing /me/paper.md",
        "Taylor: Planned 2 sections for /me/paper.md",
        "Taylor: Writing section 1/2 of /me/paper.md: # Overview",
        "Taylor: Writing section 2/2 of /me/paper.md: ## Recommendation",
        "Taylor: Saved /me/paper.md",
    ]
    assert progress_snapshots[0]["status_note"] == "Writing /me/paper.md"
    assert progress_snapshots[0]["activity_detail"] == "Writing /me/paper.md"
    assert progress_snapshots[1]["extra"]["strategy"] == "sectioned"
    assert progress_snapshots[3]["extra"]["counts_as_progress"] is True
    assert progress_snapshots[-1]["status_note"] == "Saved /me/paper.md"
    assert progress_snapshots[-1]["activity_detail"] == "Saved /me/paper.md"


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
    parsed = parse_action('{"act":"socialmsg","data":{"msg":"hi"},"th":"reply"}')
    assert parsed["action"] == "_parse_failed"
    assert "recipientType" in parsed["_raw_snippet"]


def test_parse_action_rejects_task_id_for_complete(isolated_db):
    parsed = parse_action('{"act":"done","data":{"taskId":"api_bug","sum":"done"},"th":"finished"}')
    assert parsed["action"] == "_parse_failed"
    assert "unexpected data keys" in parsed["_raw_snippet"]


def test_parse_action_accepts_lifecycle_follow_up_message(isolated_db):
    parsed = parse_action(
        '{"act":"done","data":{"sum":"done","msg":"Finished the report and saved it."},"th":"finished"}'
    )
    assert parsed["action"] == "complete"
    assert parsed["followUpMessage"] == "Finished the report and saved it."


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
async def test_message_action_rejects_agent_chat_during_active_work_without_task_lane(isolated_db):
    desk_x, desk_y = _desk_xy()
    manager_agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    target = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Coordinate release notes",
        description="Coordinate the release-notes draft.",
        assigned_to=manager_agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(manager_agent, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "message",
            "recipientType": "agent",
            "agentId": target.id,
            "content": "Please draft the notes.",
            "thought": "nudge Morgan",
        },
        manager_agent,
        state,
        trigger={"type": "activity_resumed", "task_id": parent.id, "source_channel": "work"},
    )

    assert result["event"] == "world_feedback"
    assert 'Use "assign" to create delegated work, or "taskmsg" to continue an existing task thread.' in result["detail"]


@pytest.mark.asyncio
async def test_message_action_rejects_agent_chat_when_existing_child_task_thread_exists(isolated_db):
    desk_x, desk_y = _desk_xy()
    manager_agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    target = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Coordinate release notes",
        description="Coordinate the release-notes draft.",
        assigned_to=manager_agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    child = db.create_task(
        title="Draft release notes",
        description="Draft the release notes and send them back.",
        assigned_to=target.id,
        requester_id=manager_agent.id,
        owner_id=manager_agent.id,
        created_by=manager_agent.id,
        parent_task_id=parent.id,
    )
    state = _activate_work(manager_agent, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "message",
            "recipientType": "agent",
            "agentId": target.id,
            "content": "Any update?",
            "thought": "follow up on the child task",
        },
        manager_agent,
        state,
        trigger={"type": "activity_resumed", "task_id": parent.id, "source_channel": "work"},
    )

    assert result["event"] == "world_feedback"
    assert child.id in result["detail"]
    assert '"taskmsg"' in result["detail"]
    assert result["task_id"] == child.id


@pytest.mark.asyncio
async def test_task_message_action_routes_existing_task_follow_up(isolated_db):
    desk_x, desk_y = _desk_xy()
    manager_agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Coordinate release notes",
        description="Coordinate the release-notes draft.",
        assigned_to=manager_agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    child = db.create_task(
        title="Draft release notes",
        description="Draft the release notes and send them back.",
        assigned_to=worker.id,
        requester_id=manager_agent.id,
        owner_id=manager_agent.id,
        created_by=manager_agent.id,
        parent_task_id=parent.id,
    )
    state = _activate_work(manager_agent, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "taskMessage",
            "taskId": child.id,
            "messageKind": "review",
            "content": "Please tighten the executive summary before you send it back.",
            "thought": "continue the child task thread",
        },
        manager_agent,
        state,
        trigger={"type": "activity_resumed", "task_id": parent.id, "source_channel": "work"},
    )

    assert result["event"] == "message_sent"
    assert result["trigger_requests"][0]["trigger_type"] == "task_follow_up"
    assert result["trigger_requests"][0]["task_id"] == child.id
    assert result["trigger_requests"][0]["agent_id"] == worker.id
    assert result["trigger_requests"][0]["payload"]["attention_kind"] == "review_request"
    assert result["trigger_requests"][0]["payload"]["content"] == (
        "Please tighten the executive summary before you send it back."
    )

    events = db.list_task_events(child.id, limit=10)
    assert events[-1].event_type == "status_update"
    assert events[-1].content == "Please tighten the executive summary before you send it back."


@pytest.mark.asyncio
async def test_task_message_action_keeps_passive_status_update_on_task_thread(isolated_db):
    desk_x, desk_y = _desk_xy()
    manager_agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Morgan", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Coordinate release notes",
        description="Coordinate the release-notes draft.",
        assigned_to=manager_agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    child = db.create_task(
        title="Draft release notes",
        description="Draft the release notes and send them back.",
        assigned_to=worker.id,
        requester_id=manager_agent.id,
        owner_id=manager_agent.id,
        created_by=manager_agent.id,
        parent_task_id=parent.id,
    )
    state = _activate_work(manager_agent, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "taskMessage",
            "taskId": child.id,
            "messageKind": "status",
            "content": "I reviewed the outline. Keep going with the current draft.",
            "thought": "leave a passive task update",
        },
        manager_agent,
        state,
        trigger={"type": "activity_resumed", "task_id": parent.id, "source_channel": "work"},
    )

    assert result["event"] == "message_sent"
    assert "trigger_requests" not in result

    events = db.list_task_events(child.id, limit=10)
    assert events[-1].event_type == "status_update"
    assert events[-1].content == "I reviewed the outline. Keep going with the current draft."


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
    assert delegated.requester_id == delegator.id
    assert delegated.owner_id == delegator.id
    assert delegated.parent_task_id == parent.id
    assert delegated.work_contract is not None
    assert [item.model_dump() for item in delegated.work_contract.deliverables] == [
        {"type": "file", "path": "/projects/release/reports/checklist_review.md", "description": None}
    ]


@pytest.mark.asyncio
async def test_delegate_task_action_inherits_parent_owner_and_reports_upstream(isolated_db):
    desk_x, desk_y = _desk_xy()
    pm = db.create_agent(name="Pat", desk_x=desk_x, desk_y=desk_y)
    worker = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    target = db.create_agent(name="Jason", desk_x=desk_x, desk_y=desk_y)
    parent = db.create_task(
        title="Plan rollout",
        description="Coordinate the release",
        assigned_to=worker.id,
        requester_id=pm.id,
        owner_id=pm.id,
        created_by=pm.id,
        source_channel="peer",
        notification_policy="none",
    )
    db.update_agent_cli_state(worker.id, cwd="/projects/release/reports")
    state = _activate_work(worker, parent, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "delegateTask",
            "agentId": target.id,
            "taskTitle": "Review rollout checklist",
            "taskDescription": "Review the checklist and save notes to checklist_review.md.",
            "deliverables": [{"type": "file", "path": "checklist_review.md"}],
            "thought": "delegate the checklist review",
        },
        worker,
        state,
        trigger={"type": "activity_resumed", "source_channel": "work"},
    )

    assert result["event"] == "world_feedback"
    assert "Only the task owner can delegate new child tasks" in result["detail"]
    assert result.get("task_id") == parent.id
    assert db.list_tasks(assigned_to=target.id) == []


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
async def test_complete_action_requires_requester_facing_message_for_human_requested_task(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Write the marketing paper",
        description="Draft and save the marketing paper.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "done",
            "thought": "finished",
        },
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    assert 'Include data.msg in your "done" action.' in result["detail"]


@pytest.mark.asyncio
async def test_complete_action_can_reply_to_human_requester_when_follow_up_message_is_provided(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Write the marketing paper",
        description="Draft and save the marketing paper.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "done",
            "followUpMessage": "Finished the marketing paper and saved it. Want a short summary too?",
            "thought": "finished",
        },
        agent,
        state,
    )

    assert result["event"] == "status_changed"
    assert result["chat_message"]["content"] == "Finished the marketing paper and saved it. Want a short summary too?"

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert thread[-1].content == "Finished the marketing paper and saved it. Want a short summary too?"


@pytest.mark.asyncio
async def test_blocked_action_requires_requester_facing_message_for_human_requested_task(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Investigate the import failure",
        description="Diagnose the import failure and report the root cause.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "blocked",
            "reason": "I do not have access to the import logs.",
            "thought": "report the blocker",
        },
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    assert 'Include data.msg in your "block" action.' in result["detail"]


@pytest.mark.asyncio
async def test_blocked_action_can_reply_to_human_requester_when_follow_up_message_is_provided(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Investigate the import failure",
        description="Diagnose the import failure and report the root cause.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=agent.id,
        created_by=HUMAN_SENDER_ID,
        source_channel="chat",
        notification_policy="completion_blocked",
    )
    state = _activate_work(agent, task, x=desk_x, y=desk_y)

    result = await execute_action(
        {
            "action": "blocked",
            "reason": "I do not have access to the import logs.",
            "followUpMessage": "I am blocked because I do not have access to the import logs yet.",
            "thought": "report the blocker",
        },
        agent,
        state,
    )

    assert result["event"] == "status_changed"
    assert result["chat_message"]["content"] == "I am blocked because I do not have access to the import logs yet."

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "blocked"
    assert refreshed_task.status_note == "I do not have access to the import logs."

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert thread[-1].content == "I am blocked because I do not have access to the import logs yet."

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
async def test_session_message_can_create_follow_up_work_item(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=20, y=4, status="work_active")
    session = db.ensure_room_meeting_session("meeting_room", title="Planning", created_by_agent_id=taylor.id)
    source = db.create_meeting_session_message(
        session_id=session.id,
        author_type="human",
        author_name="Human Operator",
        content="Taylor, please capture the action item to write a launch summary after this meeting.",
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
            content=(
                '{"act":"accept","intent":"work","msg":"I will capture that action item and write the launch summary after the meeting.",'
                '"commit":"work","data":{"task":{"title":"Write launch summary","desc":"Write the launch summary after the planning meeting."}},'
                '"th":"turn the meeting action item into follow-up work"}'
            ),
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

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["meeting_message"]["content"] == (
        "I will capture that action item and write the launch summary after the meeting."
    )

    tasks = db.list_tasks(assigned_to=taylor.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.title == "Write launch summary"
    assert task.status == "accepted"
    assert task.requester_id == HUMAN_SENDER_ID
    assert task.owner_id == taylor.id
    assert task.source_channel == "meeting"
    assert task.notification_policy == "completion_blocked"

    resume_request = next(
        item for item in outcome.result["trigger_requests"] if item["trigger_type"] == "activity_resumed"
    )
    assert resume_request["task_id"] == task.id

    active = _active_activity(taylor.id)
    assert active is not None
    assert active.kind == "work"
    assert active.task_id == task.id

    candidate = db.get_meeting_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    assert candidate is not None
    assert candidate.status == "responded"


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
    captured_messages: list[list[dict[str, str]]] = []

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
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
    assert len(captured_messages) == 1
    assert any(
        "AUTHORITATIVE COMMUNICATION SNAPSHOT" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )
    candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    assert candidate is not None
    assert candidate.status == "responded"
    assert candidate.queue_position == 1


@pytest.mark.asyncio
async def test_channel_message_can_observe_without_reply(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")
    channel = db.create_channel(name="Planning", member_agent_ids=[taylor.id], created_by=HUMAN_SENDER_ID)
    source = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Posting the updated planning notes here for visibility.",
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
            content='{"act":"observe","intent":"other","th":"no reply needed for this channel update"}',
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

    assert outcome.result["event"] == "decision_applied"
    assert outcome.result["detail"] == "Taylor chose to observe the shared channel"
    assert outcome.result["trigger_requests"] == []
    assert "channel_message" not in outcome.result

    candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    assert candidate is not None
    assert candidate.status == "observed"

    round_state = db.get_channel_response_round(round_record.id)
    assert round_state is not None
    assert round_state.status == "completed"

    transcript = db.list_channel_messages(channel.id, limit=10)
    assert len(transcript) == 1
    assert transcript[-1].author_name == "Human Operator"

    diagnostics = db.get_diagnostics(agent_id=taylor.id, limit=5)
    assert diagnostics[0]["action_name"] == "observe(none)"


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
    captured_messages: list[list[dict[str, str]]] = []

    async def fake_completion(**kwargs):
        captured_messages.append(kwargs["messages"])
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
    assert len(captured_messages) == 1
    assert any(
        "AUTHORITATIVE COMMUNICATION SNAPSHOT" in message["content"]
        for message in captured_messages[0]
        if message["role"] == "system"
    )
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
async def test_channel_response_observe_advances_queue_without_reply(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    taylor = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    joe = db.create_agent(name="Joe", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    taylor_state = db.update_agent_state(taylor.id, x=desk_x, y=desk_y, status="idle")
    channel = db.create_channel(name="Planning", member_agent_ids=[taylor.id, joe.id], created_by=HUMAN_SENDER_ID)
    source = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Heads up: the roadmap doc is updated. No direct answer needed unless you have concerns.",
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
            content='{"act":"observe","intent":"other","th":"nothing to add in channel"}',
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

    assert outcome.result["event"] == "decision_applied"
    assert "channel_message" not in outcome.result
    next_trigger = outcome.result["trigger_requests"][0]
    assert next_trigger["trigger_type"] == "channel_response"
    assert next_trigger["agent_id"] == joe.id

    taylor_candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=taylor.id)
    joe_candidate = db.get_channel_response_candidate(round_id=round_record.id, agent_id=joe.id)
    assert taylor_candidate is not None and taylor_candidate.status == "observed"
    assert joe_candidate is not None and joe_candidate.status == "responding"

    transcript = db.list_channel_messages(channel.id, limit=10)
    assert len(transcript) == 1
    assert transcript[-1].author_name == "Human Operator"

    diagnostics = db.get_diagnostics(agent_id=taylor.id, limit=5)
    assert diagnostics[0]["action_name"] == "observe(none)"


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
async def test_desk_file_preview_uses_configurable_preview_limit(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    state = db.get_agent_state(agent.id)
    assert state is not None

    db.set_setting("desk_preview_max_chars", "12", "desk")
    config.reload()
    execute_bm_cli(agent, state, "write /me/test_report.md", "abcdefghijklmnopqrstuvwxyz", trigger_type="human_chat")

    file_payload = await get_agent_desk(agent.id, path="/me/test_report.md")

    assert file_payload["kind"] == "file"
    assert file_payload["content"] == "abcdefghijkl"
    assert file_payload["truncated"] is True


@pytest.mark.asyncio
async def test_seed_cli_policy_rules_clears_linked_approval_rule_references(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    rule = db.create_cli_policy_rule(
        tier="approval_required",
        pattern="curl",
        match_mode="prefix",
        description="Network access",
        category="network",
    )
    approval = db.create_cli_approval_request(
        agent_id=agent.id,
        command="curl https://example.com",
        matched_rule_id=rule.id,
    )

    payload = await seed_cli_policy_rules()

    assert payload["ok"] is True
    refreshed = db.get_cli_approval_request(approval.id)
    assert refreshed is not None
    assert refreshed.matched_rule_id is None
    assert db.list_cli_policy_rules()


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
    monkeypatch.setattr("core.file_explorer.sys.platform", "linux")
    monkeypatch.setattr(
        "core.file_explorer.shutil.which",
        lambda binary: f"/usr/bin/{binary}" if binary == "thunar" else None,
    )

    command = build_file_explorer_command(Path("/tmp/demo"), opener="thunar")

    assert command == ["thunar", "--new-window", "/tmp/demo"]


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
async def test_dispatcher_retries_failed_turns_before_marking_trigger_failed(isolated_db, monkeypatch):
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
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)

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
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert refreshed.failure_reason == "bad json"

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert thread == []


@pytest.mark.asyncio
async def test_dispatcher_exhausted_failed_turn_stalls_task_and_notifies_human(isolated_db, monkeypatch):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y, model_work="test-model")
    task = db.create_task(
        title="Investigate bug",
        description="Debug the failure",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )
    state = _activate_work(agent, task)
    db.set_setting("turn_failure_retry_limit", "0", "advanced")
    config.reload()
    trigger = db.create_agent_trigger(
        agent.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "resume"},
        task_id=task.id,
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
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)

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

    refreshed_trigger = db.get_agent_trigger(trigger.id)
    assert refreshed_trigger is not None
    assert refreshed_trigger.status == "failed"
    assert refreshed_trigger.retry_count == 0
    assert refreshed_trigger.failure_reason == "bad json"

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "stalled"
    assert "Runtime exhausted automatic retries" in (refreshed_task.status_note or "")

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "blocked"

    active = _active_activity(agent.id)
    assert active is None

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert len(thread) == 1
    assert 'Investigate bug' in thread[0].content
    assert 'stalled' in thread[0].content

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics == []


@pytest.mark.asyncio
async def test_dispatcher_exception_uses_same_retry_supervisor(isolated_db, monkeypatch):
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
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "resume"},
        task_id=task.id,
    )

    async def fake_run_turn(agent_arg, state_arg, trigger_arg):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", fake_run_turn)
    monkeypatch.setattr(manager, "broadcast_activity", _noop)
    monkeypatch.setattr(manager, "broadcast_feed_update", _noop)
    monkeypatch.setattr(manager, "broadcast_chat_message", _noop)
    monkeypatch.setattr(manager, "broadcast_diagnostic", _noop)

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

    refreshed_trigger = db.get_agent_trigger(trigger.id)
    assert refreshed_trigger is not None
    assert refreshed_trigger.status == "queued"
    assert refreshed_trigger.retry_count == 1
    assert refreshed_trigger.failure_reason == "boom"

    refreshed_task = db.get_task(task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "active"

    refreshed_state = db.get_agent_state(agent.id)
    assert refreshed_state is not None
    assert refreshed_state.status == "work_active"

    thread = db.get_human_chat_thread(agent.id, limit=10)
    assert thread == []

    diagnostics = db.get_diagnostics(agent_id=agent.id, limit=5)
    assert diagnostics[0]["status"] == "error"
    assert diagnostics[0]["trigger_type"] == "activity_resumed"


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


def test_create_agent_trigger_dedupes_task_follow_up_by_task(isolated_db):
    desk_x, desk_y = _desk_xy()
    agent = db.create_agent(name="Taylor", desk_x=desk_x, desk_y=desk_y)
    task = db.create_task(
        title="Write paper",
        description="Draft the paper.",
        assigned_to=agent.id,
        created_by=HUMAN_SENDER_ID,
    )

    first = db.create_agent_trigger(
        agent.id,
        trigger_type="task_follow_up",
        source_channel="work",
        payload={
            "task_title": task.title,
            "task_description": task.description or "",
            "task_status": "pending",
            "task_party": "assignee",
            "attention_kind": "question",
            "from_agent": "agent-1",
            "from_name": "Pat",
            "content": "First question.",
            "source_message_id": "msg-1",
        },
        task_id=task.id,
    )
    second = db.create_agent_trigger(
        agent.id,
        trigger_type="task_follow_up",
        source_channel="work",
        payload={
            "task_title": task.title,
            "task_description": task.description or "",
            "task_status": "pending",
            "task_party": "assignee",
            "attention_kind": "question",
            "from_agent": "agent-1",
            "from_name": "Pat",
            "content": "Updated question.",
            "source_message_id": "msg-2",
        },
        task_id=task.id,
    )

    assert first.id == second.id
    queued = db.list_agent_triggers(agent.id, status="queued", limit=10)
    follow_ups = [entry for entry in queued if entry["trigger_type"] == "task_follow_up" and entry["task_id"] == task.id]
    assert len(follow_ups) == 1
    payload = json.loads(follow_ups[0]["payload"])
    assert payload["content"] == "Updated question."


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
    assert queued["payload"]["reply_chain_depth"] == 1
    thread = db.get_agent_direct_thread(sender.id, recipient.id, limit=10)
    assert thread[-1].message_type == "social"
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
    assert refreshed_state.status == "blocked"
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
