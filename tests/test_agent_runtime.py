"""Critical-path agent runtime tests — no live LLM.

Covers HA-TEST-P1-01 (smoke-suite module), HA-CORR-P0-01 (reused tasks
wake the assignee), HA-CORR-P0-02 (CLI approval resume),
HA-CORR-P0-03 (skip-turn must not exhaust the trigger),
HA-CORR-P1-05 (reset-runtime blocks every open work task),
HA-CORR-P1-06 (ambiguous match must not create a duplicate), and
HA-PROD-P1-01 (Assign Task API outcomes, including unassigned backlog),
and the task steps an execution turn writes for its bound task.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_scheduler import assignment_wake_trigger, build_task_assigned_trigger
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.loop import run_turn
from core.agent_loop.watchdog import TaskWatchdog
from core.bm_cli.approvals import resume_cli_approval
from core.bm_cli.filesystem import resolve_relative_path
from core.bm_cli.types import BossModCliResult
from core.models.message import HUMAN_SENDER_ID
from core.models.work_contract import DeliverableSpec, WorkContract
from core.runtime import runtime_services
from core.tasking import create_or_bind_task
from core.tasking.transitions import transition_task
from core.llm.client import LLMResponse
from core.runtime.events import NullRuntimeEventSink, runtime_events
from db.settings import get_seed_setting_default
from tests._connections import model_connection


def setup_function() -> None:
    # Important: do not call db.reset_database() in tests; it wipes on-disk artifacts.
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


def _create_agent(name: str = "Ada"):
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _create_task(*, agent_id: str, title: str = "Write report", work_contract: Any | None = None):
    return create_or_bind_task(
        title=title,
        description="Draft the weekly report",
        project=None,
        assigned_to=agent_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=work_contract,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


class _RecordingServices:
    """Stand-in for RuntimeServices.enqueue_trigger that persists + records."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )


# ---------------------------------------------------------------------------
# HA-TEST-P1-01 — critical-path smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_turn_human_chat_without_model_skips_without_crash() -> None:
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None

    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "trigger_id": "test-human-chat",
            "content": "Hello",
            "from_name": "Human",
        },
    )

    assert outcome.trigger_status == "skipped"
    assert outcome.diagnostic_status == "skipped"
    assert outcome.diagnostic_error is not None
    assert "no ai connection" in outcome.diagnostic_error.lower()
    assert "turn skipped" in outcome.result.get("detail", "").lower()


def test_create_or_bind_task_creates_task_assigned_trigger_row() -> None:
    agent = _create_agent()
    creation = _create_task(agent_id=agent.id)
    assert creation.outcome == "create_new_task"

    spec = build_task_assigned_trigger(creation.task)
    db.create_agent_trigger(**spec)

    rows = db.list_agent_triggers(agent.id)
    assert any(row["trigger_type"] == "task_assigned" and row["task_id"] == creation.task.id for row in rows)


@pytest.mark.asyncio
async def test_execute_action_done_with_missing_deliverable_returns_world_feedback() -> None:
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None

    contract = WorkContract(deliverables=[DeliverableSpec(type="file", path="/me/report.md")])
    creation = _create_task(agent_id=agent.id, work_contract=contract)
    activity_runtime.activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Finished",
            "followUpMessage": "Here is the report.",
        },
        agent,
        state,
    )

    assert result["event"] == "world_feedback"
    assert "deliverable" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


def test_resolve_relative_path_rejects_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "notes.md").write_text("ok", encoding="utf-8")

    assert resolve_relative_path(root, "notes.md") == (root / "notes.md").resolve()
    with pytest.raises(ValueError, match="escapes"):
        resolve_relative_path(root, "../secret.txt")


# ---------------------------------------------------------------------------
# HA-CORR-P0-02 — Telegram / desktop approve must resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_cli_approval_approve_enqueues_resolved_trigger() -> None:
    agent = _create_agent()
    request = db.create_cli_approval_request(agent_id=agent.id, command="ls /me")
    services = _RecordingServices()

    approval = await resume_cli_approval(
        request.id,
        approved=True,
        decision_by="telegram",
        services=services,
    )

    assert approval is not None
    assert approval.status == "approved"
    assert approval.decision_by == "telegram"
    assert len(services.calls) == 1
    call = services.calls[0]
    assert call["trigger_type"] == "cli_approval_resolved"
    assert call["agent_id"] == agent.id
    assert call["payload"]["status"] == "approved"
    assert call["payload"]["approval_request_id"] == request.id

    rows = db.list_agent_triggers(agent.id)
    assert any(row["trigger_type"] == "cli_approval_resolved" and row["status"] == "queued" for row in rows)


@pytest.mark.asyncio
async def test_resume_cli_approval_reject_enqueues_rejected_trigger() -> None:
    agent = _create_agent()
    request = db.create_cli_approval_request(agent_id=agent.id, command="rm -rf /")
    services = _RecordingServices()

    rejection = await resume_cli_approval(
        request.id,
        approved=False,
        note="too dangerous",
        decision_by="telegram",
        services=services,
    )

    assert rejection is not None
    assert rejection.status == "rejected"
    assert rejection.decision_note == "too dangerous"
    assert services.calls[0]["payload"]["status"] == "rejected"
    assert services.calls[0]["payload"]["decision_note"] == "too dangerous"
    assert services.calls[0]["trigger_type"] == "cli_approval_resolved"


@pytest.mark.asyncio
async def test_task_watchdog_leaves_old_cli_approval_pending() -> None:
    # Approvals wait for a human: the watchdog must not lapse an old card.
    agent = _create_agent()
    request = db.create_cli_approval_request(agent_id=agent.id, command="ls")
    db.execute(
        "UPDATE cli_approval_requests SET created_at = $1 WHERE id = $2",
        [datetime.now(timezone.utc) - timedelta(days=1), request.id],
    )

    await TaskWatchdog()._check_tasks()

    refreshed = db.get_cli_approval_request(request.id)
    assert refreshed is not None
    assert refreshed.status == "pending"


def test_cli_approval_timeout_setting_is_pruned_on_init() -> None:
    db.execute(
        "INSERT INTO settings (key, value, category) VALUES ($1, '60', 'cli_policy')",
        ["cli_approval_timeout_minutes"],
    )
    db.close_connection()
    db.init_db()
    config.reload()

    assert "cli_approval_timeout_minutes" not in {row.key for row in db.get_settings()}
    assert get_seed_setting_default("cli_approval_timeout_minutes") is None


# ---------------------------------------------------------------------------
# HA-CORR-P0-03 — skip must not exhaust / stall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_skipped_outcome_does_not_exhaust_trigger() -> None:
    agent = _create_agent()
    creation = _create_task(agent_id=agent.id)
    trigger_row = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "Please start", "from_name": "Human"},
        task_id=creation.task.id,
    )
    claimed = db.claim_trigger(trigger_row.id)
    assert claimed is not None

    payload = json.loads(claimed.payload) if claimed.payload else {}
    payload.update(
        {
            "type": claimed.trigger_type,
            "trigger_id": claimed.id,
            "task_id": claimed.task_id,
            "source_channel": claimed.source_channel,
        }
    )
    state = db.get_agent_state(agent.id)
    assert state is not None

    await TurnDispatcher()._run_trigger(agent, state, payload)

    refreshed = db.get_agent_trigger(trigger_row.id)
    assert refreshed is not None
    assert refreshed.status != "failed"
    assert refreshed.status == "completed"

    task = db.get_task(creation.task.id)
    assert task is not None
    assert task.status not in {"stalled", "blocked"}

    diagnostics = db.get_diagnostics(agent.id)
    assert any(
        row["status"] == "skipped" and row["error"] and "no ai connection" in row["error"].lower()
        for row in diagnostics
    )


# ---------------------------------------------------------------------------
# HA-CORR-P0-01 — reused tasks must wake the assignee
# ---------------------------------------------------------------------------


def _task_api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """HTTP client that persists triggers without starting the runtime worker."""

    async def _persist_trigger(**kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _persist_trigger)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _task_api_headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _queued_assignment_triggers(agent_id: str, task_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == "task_assigned"
        and row["task_id"] == task_id
        and row["status"] == "queued"
    ]


def test_assignment_wake_trigger_requires_open_assignee() -> None:
    agent = _create_agent()
    creation = _create_task(agent_id=agent.id)
    spec = assignment_wake_trigger(creation.task)
    assert spec is not None
    assert spec["trigger_type"] == "task_assigned"
    assert spec["agent_id"] == agent.id
    assert spec["task_id"] == creation.task.id

    db.update_task(creation.task.id, assigned_to=None)
    unassigned = db.get_task(creation.task.id)
    assert unassigned is not None
    assert assignment_wake_trigger(unassigned) is None

    db.update_task(creation.task.id, assigned_to=agent.id)
    transition_task(creation.task.id, "accepted", reason="test accept", actor="pytest")
    transition_task(creation.task.id, "active", reason="test start", actor="pytest")
    transition_task(creation.task.id, "complete", reason="test complete", actor="pytest")
    completed = db.get_task(creation.task.id)
    assert completed is not None
    assert assignment_wake_trigger(completed) is None


def test_create_task_api_returns_outcome_and_queues_task_assigned(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _create_agent()
    client = _task_api_client(monkeypatch)

    response = client.post(
        "/api/tasks",
        headers=_task_api_headers(),
        json={"title": "Write report", "description": "Draft the weekly report", "assigned_to": agent.id},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "create_new_task"
    assert body["task"]["title"] == "Write report"
    assert body["task"]["assigned_to"] == agent.id
    assert _queued_assignment_triggers(agent.id, body["task"]["id"])


def test_repost_same_task_wakes_assignee_after_trigger_consumed(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _create_agent()
    client = _task_api_client(monkeypatch)
    headers = _task_api_headers()
    payload = {"title": "Write report", "description": "Draft the weekly report", "assigned_to": agent.id}

    created = client.post("/api/tasks", headers=headers, json=payload)
    assert created.status_code == 201
    created_body = created.json()
    task_id = created_body["task"]["id"]
    first_triggers = _queued_assignment_triggers(agent.id, task_id)
    assert len(first_triggers) == 1
    db.complete_agent_trigger(first_triggers[0]["id"])
    assert not _queued_assignment_triggers(agent.id, task_id)

    reused = client.post("/api/tasks", headers=headers, json=payload)
    assert reused.status_code == 201
    reused_body = reused.json()
    assert reused_body["outcome"] == "bind_existing_task"
    assert reused_body["task"]["id"] == task_id
    assert _queued_assignment_triggers(agent.id, task_id)


def test_repost_same_task_coalesces_already_queued_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _create_agent()
    client = _task_api_client(monkeypatch)
    headers = _task_api_headers()
    payload = {"title": "Write report", "assigned_to": agent.id}

    created = client.post("/api/tasks", headers=headers, json=payload)
    task_id = created.json()["task"]["id"]
    first_id = _queued_assignment_triggers(agent.id, task_id)[0]["id"]

    reused = client.post("/api/tasks", headers=headers, json=payload)
    assert reused.json()["outcome"] == "bind_existing_task"
    queued = _queued_assignment_triggers(agent.id, task_id)
    assert len(queued) == 1
    assert queued[0]["id"] == first_id


# ---------------------------------------------------------------------------
# HA-CORR-P1-06 / HA-PROD-P1-01 — clarify + unassigned assign
# ---------------------------------------------------------------------------


def _open_tasks_with_title(title: str, *, assigned_to: str | None = None) -> list:
    rows = db.list_tasks(assigned_to=assigned_to) if assigned_to else db.list_tasks()
    return [task for task in rows if task.title == title]


def test_create_or_bind_task_ambiguous_match_does_not_create_duplicate() -> None:
    agent = _create_agent()
    first = db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    second = db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    assert first.id != second.id

    creation = create_or_bind_task(
        title="Plan",
        description="Which plan?",
        project=None,
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )

    assert creation.outcome == "clarify_ambiguous_match"
    assert creation.task is None
    assert {item.id for item in creation.resolution.candidates} == {first.id, second.id}
    assert len(_open_tasks_with_title("Plan", assigned_to=agent.id)) == 2


def test_create_task_api_ambiguous_match_returns_candidates_without_third_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _create_agent()
    first = db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    second = db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    client = _task_api_client(monkeypatch)

    response = client.post(
        "/api/tasks",
        headers=_task_api_headers(),
        json={"title": "Plan", "assigned_to": agent.id},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["outcome"] == "clarify_ambiguous_match"
    assert body["task"] is None
    candidate_ids = {item["id"] for item in body["candidates"]}
    assert candidate_ids == {first.id, second.id}
    assert len(_open_tasks_with_title("Plan", assigned_to=agent.id)) == 2
    assert not db.list_agent_triggers(agent.id)


def test_create_task_api_bind_task_id_reuses_chosen_ambiguous_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _create_agent()
    first = db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    client = _task_api_client(monkeypatch)
    headers = _task_api_headers()

    clarified = client.post("/api/tasks", headers=headers, json={"title": "Plan", "assigned_to": agent.id})
    assert clarified.status_code == 409

    bound = client.post(
        "/api/tasks",
        headers=headers,
        json={"title": "Plan", "assigned_to": agent.id, "bind_task_id": first.id},
    )

    assert bound.status_code == 201
    body = bound.json()
    assert body["outcome"] == "bind_existing_task"
    assert body["task"]["id"] == first.id
    assert len(_open_tasks_with_title("Plan", assigned_to=agent.id)) == 2
    assert _queued_assignment_triggers(agent.id, first.id)


def test_create_task_api_unassigned_backlog_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _task_api_client(monkeypatch)

    response = client.post(
        "/api/tasks",
        headers=_task_api_headers(),
        json={"title": "Inbox later", "description": "No owner yet"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "create_new_task"
    assert body["task"]["title"] == "Inbox later"
    assert body["task"]["assigned_to"] is None
    assert db.get_task(body["task"]["id"]) is not None


def test_apply_decision_accept_ambiguous_match_returns_world_feedback() -> None:
    """Regression: `_require_bound_task` used to raise ValueError into apply_decision."""
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)

    try:
        result = apply_decision(
            {
                "decision": "accept",
                "intentKind": "work_request",
                "commitmentKind": "work",
                "taskTitle": "Plan",
                "reply": "I'll take the plan.",
            },
            agent,
            state,
            {"type": "human_chat", "content": "Please plan this", "from_name": "Human"},
        )
    except ValueError as exc:
        pytest.fail(f"ambiguous accept must not raise ValueError (turn crash): {exc}")

    assert result["event"] == "world_feedback"
    assert result["event"] != "decision_applied"
    assert "multiple open tasks" in result["detail"].lower()
    assert result.get("expected_action") == "clarify"
    assert len(_open_tasks_with_title("Plan", assigned_to=agent.id)) == 2
    assert activity_runtime.get_active_work_activity(agent.id) is None
    assert all(task.status != "accepted" for task in _open_tasks_with_title("Plan", assigned_to=agent.id))


def _status_updates(task_id: str) -> list:
    return [event for event in db.list_task_events(task_id) if event.event_type == "status_update"]


def test_apply_decision_decline_writes_one_status_update() -> None:
    """One pending→declined jump must not leave two status_update rows."""
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task(title="Write report", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    assert _status_updates(task.id) == []

    result = apply_decision(
        {
            "decision": "decline",
            "intentKind": "work_request",
            "commitmentKind": "none",
            "reply": "I cannot take this.",
        },
        agent,
        state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "content": "Please write the report",
            "from_name": "Human",
        },
    )

    assert result["event"] == "decision_applied"
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "declined"
    updates = _status_updates(task.id)
    assert len(updates) == 1
    assert "pending → declined" in updates[0].content


def test_apply_decision_defer_does_not_double_log_status_update() -> None:
    """Defer must not append a leftover status_update after transition_task."""
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    pending = db.create_task(title="Write report", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    assert pending.status == "pending"

    pending_result = apply_decision(
        {
            "decision": "defer",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Write report",
            "reply": "Later.",
        },
        agent,
        state,
        {
            "type": "task_assigned",
            "task_id": pending.id,
            "content": "Please write the report",
            "from_name": "Human",
        },
    )
    assert pending_result["event"] == "decision_applied"
    still_pending = db.get_task(pending.id)
    assert still_pending is not None
    assert still_pending.status == "pending"
    # pending→pending is not a jump; the new note is the one status_update.
    pending_updates = _status_updates(pending.id)
    assert len(pending_updates) == 1
    assert pending_updates[0].content == "Later."
    assert pending_updates[0].author_agent_id == agent.id

    accepted = _create_task(agent_id=agent.id, title="Draft brief")
    transition_task(accepted.task.id, "accepted", reason="setup accepted", actor="pytest")
    before = len(_status_updates(accepted.task.id))

    accepted_result = apply_decision(
        {
            "decision": "defer",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Draft brief",
            "reply": "Park this for now.",
        },
        agent,
        state,
        {
            "type": "task_follow_up",
            "task_id": accepted.task.id,
            "task_status": "accepted",
            "task_party": "assignee",
            "content": "Any update?",
            "from_name": "Human",
        },
    )
    assert accepted_result["event"] == "decision_applied"
    deferred = db.get_task(accepted.task.id)
    assert deferred is not None
    assert deferred.status == "pending"
    after = _status_updates(accepted.task.id)
    assert len(after) == before + 1
    assert "accepted → pending" in after[-1].content


def test_apply_decision_defer_ambiguous_match_returns_world_feedback() -> None:
    """Regression: defer work used the same `_require_bound_task` raise."""
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)
    db.create_task(title="Plan", assigned_to=agent.id, owner_id=agent.id, created_by=HUMAN_SENDER_ID)

    try:
        result = apply_decision(
            {
                "decision": "defer",
                "intentKind": "work_request",
                "commitmentKind": "work",
                "taskTitle": "Plan",
                "reply": "Later.",
            },
            agent,
            state,
            {"type": "human_chat", "content": "Please plan this", "from_name": "Human"},
        )
    except ValueError as exc:
        pytest.fail(f"ambiguous defer must not raise ValueError (turn crash): {exc}")

    assert result["event"] == "world_feedback"
    assert result.get("expected_action") == "clarify"
    assert len(_open_tasks_with_title("Plan", assigned_to=agent.id)) == 2
    assert activity_runtime.get_active_work_activity(agent.id) is None


def test_apply_decision_plan_materialize_ambiguous_child_fails_honestly() -> None:
    """Regression: `_materialize_work_execution_plan` used to `continue` and accept the parent."""
    lead = _create_agent("Ada")
    helper = _create_agent("Bea")
    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Ship release", assigned_to=lead.id, created_by=HUMAN_SENDER_ID)
    db.create_task(
        title="Notes",
        assigned_to=helper.id,
        requester_id=lead.id,
        owner_id=lead.id,
        created_by=lead.id,
        parent_task_id=parent.id,
    )
    db.create_task(
        title="Notes",
        assigned_to=helper.id,
        requester_id=lead.id,
        owner_id=lead.id,
        created_by=lead.id,
        parent_task_id=parent.id,
    )

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Ship release",
            "reply": "Bea can take notes.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [{"agentId": helper.id, "taskTitle": "Notes"}],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Ship the release",
            "from_name": "Human",
        },
    )

    assert result["event"] == "world_feedback"
    assert result["event"] != "decision_applied"
    assert "accepted work" not in result.get("detail", "").lower()
    assert "multiple open tasks" in result["detail"].lower()
    assert "notes" in result["detail"].lower()
    assert result.get("expected_action") == "clarify"
    assert len(_open_tasks_with_title("Notes", assigned_to=helper.id)) == 2
    refreshed = db.get_task(parent.id)
    assert refreshed is not None
    assert refreshed.status != "accepted"
    assert activity_runtime.get_active_work_activity(lead.id) is None
    assert not any(row["trigger_type"] == "task_assigned" and row["task_id"] != parent.id for row in db.list_agent_triggers(helper.id))


def test_apply_decision_multi_delegation_rolls_back_earlier_children_on_clarify() -> None:
    """PR #9 follow-up: a later clarify must not leave earlier new child rows."""
    lead = _create_agent("Ada")
    helper = _create_agent("Bea")
    writer = _create_agent("Cara")
    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Ship release", assigned_to=lead.id, created_by=HUMAN_SENDER_ID)
    db.create_task(
        title="Notes",
        assigned_to=helper.id,
        requester_id=lead.id,
        owner_id=lead.id,
        created_by=lead.id,
        parent_task_id=parent.id,
    )
    db.create_task(
        title="Notes",
        assigned_to=helper.id,
        requester_id=lead.id,
        owner_id=lead.id,
        created_by=lead.id,
        parent_task_id=parent.id,
    )

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Ship release",
            "reply": "Cara writes the changelog; Bea takes notes.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [
                    {"agentId": writer.id, "taskTitle": "Write changelog"},
                    {"agentId": helper.id, "taskTitle": "Notes"},
                ],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Ship the release",
            "from_name": "Human",
        },
    )

    assert result["event"] == "world_feedback"
    assert result.get("expected_action") == "clarify"
    assert _open_tasks_with_title("Write changelog", assigned_to=writer.id) == []
    assert len(_open_tasks_with_title("Notes", assigned_to=helper.id)) == 2
    refreshed = db.get_task(parent.id)
    assert refreshed is not None
    assert refreshed.status != "accepted"
    assert not any(
        row["trigger_type"] == "task_assigned" and row["task_id"] != parent.id
        for row in db.list_agent_triggers(writer.id)
    )


# ---------------------------------------------------------------------------
# Task requests — the requester's words are saved with accepted or deferred
# work and forwarded to its children (core/tasking/requests.py)
# ---------------------------------------------------------------------------

_REQUEST_HEADING = (
    "request (what was asked, word for word, oldest first; where the description leaves something out, "
    "go by this and any document it names):"
)
_LONG_ASK = (
    "Take the systems rubric review of our tests: /projects/app/Docs/rubric-review.md\n"
    "Resolve and validate each finding, rows 1-12 first."
)


def _accept_work(agent, trigger: dict[str, Any], **decision: Any) -> dict[str, Any]:
    state = db.get_agent_state(agent.id)
    assert state is not None
    return apply_decision(
        {"decision": "accept", "intentKind": "work_request", "commitmentKind": "work", "reply": "On it.", **decision},
        agent,
        state,
        trigger,
    )


def _request_contents(task_id: str) -> list[str]:
    task = db.get_task(task_id)
    assert task is not None
    return [message.content for message in task.request_messages]


def test_an_accept_keeps_the_desc_as_the_brief_and_the_message_as_the_request() -> None:
    agent = _create_agent()
    result = _accept_work(
        agent,
        {"type": "human_chat", "content": _LONG_ASK, "from_name": "Human"},
        taskTitle="Review the rubric", taskDescription="Start with rows 1-12.",
    )
    assert result["event"] == "decision_applied", result
    (task,) = _open_tasks_with_title("Review the rubric", assigned_to=agent.id)
    assert task.description == "Start with rows 1-12."
    assert [m.model_dump() for m in task.request_messages] == [
        {"author_type": "human", "author_agent_id": None, "author_name": "Boss", "content": _LONG_ASK},
    ]


def test_an_accept_without_a_desc_has_no_description_and_keeps_the_message() -> None:
    agent = _create_agent()
    _accept_work(agent, {"type": "human_chat", "content": _LONG_ASK, "from_name": "Human"}, taskTitle="Review it")
    (task,) = _open_tasks_with_title("Review it", assigned_to=agent.id)
    assert task.description is None
    assert _request_contents(task.id) == [_LONG_ASK]


def test_a_task_accepted_without_a_desc_reads_no_description_in_the_prompt() -> None:
    from core.agent_loop.turn_context import _get_current_task
    from core.llm.context_builder import _format_task

    agent = _create_agent()
    _accept_work(agent, {"type": "human_chat", "content": _LONG_ASK, "from_name": "Human"}, taskTitle="Review it")
    current = _get_current_task(agent.id)
    assert current is not None and current["description"] is None
    block = _format_task(current)
    assert "description: No description" in block.split("\n")
    assert "None" not in block


def test_an_accept_on_a_channel_line_whose_author_cannot_be_recorded_still_creates_the_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    lead = _create_agent("Ada")
    channel = db.create_channel(name="Ops", member_agent_ids=[lead.id], created_by=HUMAN_SENDER_ID)
    trigger = {
        # An agent opener with no from_agent: the request cannot name its author.
        "type": "channel_response", "channel_id": channel.id, "channel_name": channel.name,
        "content": "Team, audit the tests.", "author_type": "agent", "from_name": "Alex",
        "latest_content": "Start with test_login.py", "latest_author_type": "human",
        "latest_from_agent": "", "latest_from_name": "Boss",
    }
    with caplog.at_level(logging.WARNING, logger="core.tasking.requests"):
        result = _accept_work(lead, trigger, taskTitle="Audit the tests", taskDescription="Login tests first.")
    assert result["event"] == "decision_applied", result
    (task,) = _open_tasks_with_title("Audit the tests", assigned_to=lead.id)
    assert task.status == "accepted"
    assert _request_contents(task.id) == ["Start with test_login.py"]
    assert any("opener message" in record.getMessage() for record in caplog.records)


def test_a_defer_keeps_the_request_and_the_messages_attachments() -> None:
    agent = _create_agent()
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "defer", "intentKind": "work_request", "commitmentKind": "work",
            "taskTitle": "Review the rubric", "reply": "After lunch.",
        },
        agent,
        state,
        {"type": "human_chat", "content": _LONG_ASK, "from_name": "Human", "attachment_ids": ["att-1", "att-2"]},
    )
    assert result["event"] == "decision_applied", result
    (task,) = _open_tasks_with_title("Review the rubric", assigned_to=agent.id)
    assert task.status == "pending"
    assert _request_contents(task.id) == [_LONG_ASK]
    assert task.origin_attachment_ids == ["att-1", "att-2"]


def test_an_accept_naming_its_task_appends_the_new_message_after_the_first() -> None:
    agent = _create_agent()
    _accept_work(agent, {"type": "human_chat", "content": "Write the Q3 memo.", "from_name": "Human"},
                 taskTitle="Write the memo", taskDescription="One page.")
    (task,) = _open_tasks_with_title("Write the memo", assigned_to=agent.id)
    _accept_work(agent, {"type": "human_chat", "content": "Cover seat minimums too.", "from_name": "Human"},
                 taskId=task.id, taskTitle="Write the memo", taskDescription="One page, with seat minimums.")
    assert _request_contents(task.id) == ["Write the Q3 memo.", "Cover seat minimums too."]
    assert db.get_task(task.id).description == "One page, with seat minimums."


def test_a_channel_plan_forwards_the_request_to_each_child() -> None:
    from core.agent_loop.channel_rounds import start_channel_peer_round
    from core.agent_loop.turn_context import _get_current_task
    from core.llm.context_builder import _format_task

    lead = _create_agent("Ada")
    helper = _create_agent("Bea")
    channel = db.create_channel(name="Ops", member_agent_ids=[lead.id, helper.id], created_by=HUMAN_SENDER_ID)
    message = db.create_channel_message(
        channel_id=channel.id, author_type="human", author_name="Human Operator", content=_LONG_ASK,
        source_channel="channel",
    )
    wakes = start_channel_peer_round(
        channel_id=channel.id, message_id=message.id, content=message.content,
        from_name="Human Operator", author_type="human",
    )
    # The lead's turn as the active responder (channel_response): which
    # member the round wakes first is not fixed, and a channel_message turn
    # by a member who is not first is only queued.
    result = _accept_work(
        lead,
        {
            "type": "channel_response", "channel_id": channel.id, "channel_name": channel.name,
            "round_id": wakes[0]["payload"]["round_id"], "source_message_id": message.id,
            "content": message.content, "from_name": "Human Operator", "author_type": "human",
            "dispatch_mode": wakes[0]["payload"]["dispatch_mode"],
        },
        taskTitle="Coordinate the rubric review",
        taskDescription="Split the findings and check the fixes.",
        executionPlan={
            "mode": "delegate",
            "delegations": [{"agentId": helper.id, "taskTitle": "Check rows 1-12", "taskDescription": "Rows 1-12."}],
        },
    )
    assert result["event"] == "decision_applied", result
    forwarded = [
        'request behind the parent task "Coordinate the rubric review" (word for word, oldest first; '
        "your part is this task's description):",
        f"[Boss]: {_LONG_ASK}",
    ]
    (assigned,) = [item for item in result["trigger_requests"] if item["trigger_type"] == "task_assigned"]
    assert assigned["agent_id"] == helper.id
    assert assigned["payload"]["task_request"] == "\n".join(forwarded)

    child = db.get_task(assigned["task_id"])
    assert child is not None and child.request_messages == []
    activity_runtime.activate_work_activity(helper.id, child, task_status="active")
    current = _get_current_task(helper.id)
    assert current is not None
    # The forwarded request sits between the status line and the child's own brief.
    assert "\n".join(["status: active", *forwarded, "description: Rows 1-12."]) in _format_task(current)


def test_the_execution_prompt_shows_the_request_above_the_description() -> None:
    from core.agent_loop.turn_context import _get_current_activity, _get_current_task
    from core.llm import context_builder

    agent = _create_agent()
    _accept_work(
        agent,
        {"type": "human_chat", "content": "Summarize /projects/app/brief.md for the launch call.", "from_name": "Human"},
        taskTitle="Summarize the brief", taskDescription="Two short paragraphs.",
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    turn = context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger={"type": "activity_resumed", "content": "Resume.", "source_channel": "work"},
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=[],
        current_activity=_get_current_activity(agent.id),
        current_task=_get_current_task(agent.id),
        contract_kind="execution",
    )
    system_prompt = context_builder.build_context(turn)[0]["content"]
    current_task = system_prompt.split("## Current Task", 1)[1].split("## Task Board", 1)[0]
    assert _REQUEST_HEADING in current_task
    assert "[Boss]: Summarize /projects/app/brief.md for the launch call." in current_task
    assert current_task.index("/projects/app/brief.md") < current_task.index("description: Two short paragraphs.")


# ---------------------------------------------------------------------------
# HA-CORR-P1-03 — watchdog covers accepted / waiting
# ---------------------------------------------------------------------------


def _quiet_assigned_task(*, agent_id: str, title: str, status: str):
    creation = _create_task(agent_id=agent_id, title=title)
    if status != "pending":
        path = {
            "accepted": ("accepted",),
            "active": ("accepted", "active"),
            "waiting": ("accepted", "active", "waiting"),
        }[status]
        for step in path:
            transition_task(creation.task.id, step, reason=f"setup {step}", actor="pytest")
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    db.update_task(
        creation.task.id,
        last_progress_at=past,
        last_heartbeat_at=past,
        last_activity=past,
        watchdog_pinged_at=None,
    )
    return db.get_task(creation.task.id)


def _watchdog_pings(agent_id: str, task_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == "watchdog_status_ping"
        and row["task_id"] == task_id
        and row["status"] == "queued"
    ]


@pytest.mark.asyncio
async def test_watchdog_pings_quiet_accepted_task() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Accepted draft", status="accepted")
    assert task is not None

    await TaskWatchdog()._check_tasks()

    assert _watchdog_pings(agent.id, task.id)
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert refreshed.watchdog_pinged_at is not None


@pytest.mark.asyncio
async def test_watchdog_still_pings_quiet_active_task() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Active draft", status="active")
    assert task is not None

    await TaskWatchdog()._check_tasks()

    assert _watchdog_pings(agent.id, task.id)
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_watchdog_pings_quiet_waiting_task_but_does_not_stall() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Waiting on human", status="waiting")
    assert task is not None

    await TaskWatchdog()._check_tasks()
    pings = _watchdog_pings(agent.id, task.id)
    assert pings

    db.complete_agent_trigger(pings[0]["id"])
    past_ping = datetime.now(timezone.utc) - timedelta(hours=2)
    db.update_task(task.id, watchdog_pinged_at=past_ping)

    await TaskWatchdog()._check_tasks()

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "waiting"
    assert refreshed.status != "stalled"


@pytest.mark.asyncio
async def test_watchdog_active_still_escalates_after_ignored_ping() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Active ignored", status="active")
    assert task is not None

    await TaskWatchdog()._check_tasks()
    pings = _watchdog_pings(agent.id, task.id)
    assert pings
    db.complete_agent_trigger(pings[0]["id"])
    past_ping = datetime.now(timezone.utc) - timedelta(hours=2)
    db.update_task(task.id, watchdog_pinged_at=past_ping)

    await TaskWatchdog()._check_tasks()

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "stalled"


@pytest.mark.asyncio
async def test_watchdog_skips_pending_assigned_task() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Still pending", status="pending")
    assert task is not None

    await TaskWatchdog()._check_tasks()

    assert not _watchdog_pings(agent.id, task.id)
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.watchdog_pinged_at is None


def _watchdog_ping_trigger(task: Any) -> dict[str, Any]:
    return {
        "type": "watchdog_status_ping",
        "trigger_id": "trigger-watchdog-1",
        "task_id": task.id,
        "source_channel": "system",
        "content": f'Watchdog check: are you still working on "{task.title}"? Provide a status update.',
        "from_name": "System",
        "task_title": task.title,
    }


def test_watchdog_reply_sets_status_note_and_logs_agent_status_update() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Accepted review", status="accepted")
    assert task is not None
    state = db.get_agent_state(agent.id)
    assert state is not None
    before = _status_updates(task.id)

    apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "commitmentKind": "none",
            "workCommit": False,
            "reply": "Review is in progress. Blocked on one approval gate.",
        },
        agent,
        state,
        _watchdog_ping_trigger(task),
    )

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status_note == "Review is in progress. Blocked on one approval gate."
    assert refreshed.watchdog_pinged_at is None
    new_events = _status_updates(task.id)[len(before) :]
    assert len(new_events) == 1
    assert new_events[0].content == "Review is in progress. Blocked on one approval gate."
    assert new_events[0].author_type == "agent"
    assert new_events[0].author_agent_id == agent.id
    assert new_events[0].author_name == agent.name
    assert new_events[0].source_trigger_id == "trigger-watchdog-1"


def test_watchdog_empty_reply_logs_no_status_update() -> None:
    agent = _create_agent()
    task = _quiet_assigned_task(agent_id=agent.id, title="Accepted silent", status="accepted")
    assert task is not None
    state = db.get_agent_state(agent.id)
    assert state is not None
    before = _status_updates(task.id)

    apply_decision(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "commitmentKind": "none",
            "workCommit": False,
            "reply": "   ",
        },
        agent,
        state,
        _watchdog_ping_trigger(task),
    )

    assert _status_updates(task.id) == before
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.watchdog_pinged_at is None


# ---------------------------------------------------------------------------
# HA-CORR-P1-05 — reset-runtime + skip-turn hygiene
# ---------------------------------------------------------------------------


def _open_two_work_activities(agent_id: str) -> tuple[Any, Any]:
    """Create two in-flight work activities (one paused, one active)."""
    first = _create_task(agent_id=agent_id, title="First open work")
    second = _create_task(agent_id=agent_id, title="Second open work")
    transition_task(
        first.task.id,
        "active",
        reason="Test fixture: first work is in flight.",
        actor="pytest",
    )
    transition_task(
        second.task.id,
        "active",
        reason="Test fixture: second work is in flight.",
        actor="pytest",
    )
    db.create_runtime_activity(
        agent_id=agent_id,
        kind="work",
        status="paused",
        task_id=first.task.id,
        title=first.task.title,
        detail="Paused for newer work.",
    )
    db.create_runtime_activity(
        agent_id=agent_id,
        kind="work",
        status="active",
        task_id=second.task.id,
        title=second.task.title,
        detail="Current work.",
    )
    return first.task, second.task


def test_reset_runtime_blocks_all_open_work_activity_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _create_agent()
    first, second = _open_two_work_activities(agent.id)
    assert first.id != second.id

    async def _noop_reset(agent_id: str) -> None:
        assert agent_id == agent.id

    monkeypatch.setattr(runtime_services, "reset_agent_runtime", _noop_reset)
    client = _task_api_client(monkeypatch)

    response = client.post(f"/api/agents/{agent.id}/reset-runtime", headers=_task_api_headers())
    assert response.status_code == 200
    body = response.json()
    assert set(body["blocked_task_ids"]) == {first.id, second.id}
    assert body["blocked_task_id"] in {first.id, second.id}

    refreshed_first = db.get_task(first.id)
    refreshed_second = db.get_task(second.id)
    assert refreshed_first is not None and refreshed_first.status == "blocked"
    assert refreshed_second is not None and refreshed_second.status == "blocked"
    assert "Runtime reset by the boss" in (refreshed_first.status_note or "")
    assert "Runtime reset by the boss" in (refreshed_second.status_note or "")

    open_activities = [
        activity
        for activity in db.list_activities(agent_id=agent.id, limit=50)
        if activity.status in {"active", "paused"}
    ]
    assert open_activities == []


@pytest.mark.asyncio
async def test_skip_turn_does_not_block_either_of_two_open_work_tasks() -> None:
    agent = _create_agent()
    first, second = _open_two_work_activities(agent.id)
    trigger_row = db.create_agent_trigger(
        agent_id=agent.id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "Please continue", "from_name": "Human"},
        task_id=first.id,
    )
    claimed = db.claim_trigger(trigger_row.id)
    assert claimed is not None

    payload = json.loads(claimed.payload) if claimed.payload else {}
    payload.update(
        {
            "type": claimed.trigger_type,
            "trigger_id": claimed.id,
            "task_id": claimed.task_id,
            "source_channel": claimed.source_channel,
        }
    )
    state = db.get_agent_state(agent.id)
    assert state is not None

    await TurnDispatcher()._run_trigger(agent, state, payload)

    refreshed_first = db.get_task(first.id)
    refreshed_second = db.get_task(second.id)
    assert refreshed_first is not None
    assert refreshed_second is not None
    assert refreshed_first.status not in {"blocked", "stalled"}
    assert refreshed_second.status not in {"blocked", "stalled"}
    assert refreshed_first.status == "active"
    assert refreshed_second.status == "active"


# ---------------------------------------------------------------------------
# Task steps — each execution step on the bound task is one durable row
# ---------------------------------------------------------------------------

# idle is refused on an active task; each refusal is fed back until the repair
# budget (execution_max_consecutive_repairs, 2) is spent, which ends the turn.
_IDLE_UNTIL_THE_TURN_ENDS = ['{"act":"idle","data":{},"th":"looked"}'] * 3


class _StepSink(NullRuntimeEventSink):
    """Records every ``task_step`` event the turn sends."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    async def broadcast_task_step(self, step: dict[str, Any]) -> None:
        self.steps.append(step)


@pytest.fixture()
def step_sink():
    sink = _StepSink()
    runtime_events.set_sink(sink)
    yield sink
    runtime_events.set_sink(NullRuntimeEventSink())


def _script_llm(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> None:
    queue = list(contents)

    async def _fake_completion(**_kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        return LLMResponse(content=queue.pop(0), model="test/mock", prompt_tokens=8,
                           completion_tokens=4, total_tokens=12)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)


def _working_agent():
    """An agent on a model, with an active task as its live, bound work."""
    agent = db.create_agent("Ettie", role="Eng", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))
    task = _create_task(agent_id=agent.id).task
    activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    return agent, task


def _stub_cli(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any], seen: list[Any] | None = None, *, task_id: str = "",
              sink: _StepSink | None = None) -> None:
    """Answer every ``bm_cli`` action with ``result``; record what was stored and sent when it ran."""
    import core.agent_loop.execution_turn as execution_turn

    real = execution_turn.execute_action

    async def _fake_execute(action: dict[str, Any], agent: Any, state: Any, trigger: Any, **kwargs: Any) -> dict[str, Any]:
        if action["action"] != "bm_cli":
            return await real(action, agent, state, trigger, **kwargs)
        if seen is not None:
            seen.append((db.list_task_steps(task_id, limit=10), list(sink.steps) if sink else []))
        return {**result, "agent_name": agent.name}

    monkeypatch.setattr(execution_turn, "execute_action", _fake_execute)


@pytest.mark.asyncio
async def test_a_bound_cli_step_is_stored_and_sent_before_it_runs_then_finished(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    seen: list[Any] = []
    blocked = "BossMod CLI error: Blocked — host path is outside the locked clone"
    _stub_cli(
        monkeypatch,
        {"event": "bm_cli_error", "detail": blocked, "suppress_world_broadcast": True,
         "suppress_activity_broadcast": True},
        seen, task_id=task.id, sink=step_sink,
    )
    _script_llm(monkeypatch, [
        '{"act":"cli","data":{"cmd":"cat /srv/app/log"},"th":"Read the host log"}',
        *_IDLE_UNTIL_THE_TURN_ENDS,
    ])
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Resume.", "source_channel": "work"}

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    # While the command ran, its row was already stored, open, and announced.
    [(stored, sent)] = seen
    [running] = stored
    assert (running.action, running.note, running.command) == ("bm_cli", "Read the host log", "cat /srv/app/log")
    assert (running.agent_id, running.agent_name) == (agent.id, "Ettie")
    assert (running.outcome, running.result, running.ended_at) == (None, None, None)
    assert [(event["id"], event["ended_at"]) for event in sent] == [(running.id, None)]
    # Afterwards it is finished with the outcome its result calls for, and sent again.
    [finished] = db.list_task_steps(task.id, limit=10)
    assert finished.id == running.id
    assert (finished.outcome, finished.result) == ("failed", blocked)
    assert finished.ended_at is not None and finished.ended_at >= finished.started_at
    assert [(event["id"], event["outcome"]) for event in step_sink.steps] == [(running.id, None), (running.id, "failed")]
    assert step_sink.steps[-1]["result"] == blocked
    # Steps are their own record: the task's thread got none of it.
    assert all(blocked not in event.content for event in db.list_task_events(task.id, limit=50))


@pytest.mark.asyncio
async def test_a_detached_turn_writes_no_steps_on_the_paused_task(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    ran: list[Any] = []
    _stub_cli(monkeypatch, {"event": "bm_cli_result", "detail": "command completed (exit 0, 1ms)"}, ran,
              task_id=task.id)
    _script_llm(monkeypatch, [
        '{"act":"cli","data":{"cmd":"status"},"th":"check what arrived"}',
        '{"act":"idle","th":"handled the email"}',
    ])
    trigger = {
        "type": "extension_event", "source_channel": "system", "task_id": None,
        "extension_id": "ms365-mail", "extension_name": "Microsoft 365 Mailbox",
        "from_name": "Microsoft 365 Mailbox", "title": "New email", "lines": [], "content": "New email",
    }

    outcome = await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert outcome.trigger_status == "completed"
    assert len(ran) == 1, "the detached turn never ran its command"
    assert db.list_task_steps(task.id, limit=10) == []
    assert step_sink.steps == []
    assert db.get_task(task.id).status == "active"


@pytest.mark.asyncio
async def test_a_parse_failure_writes_no_step(monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink) -> None:
    agent, task = _working_agent()
    _stub_cli(monkeypatch, {"event": "bm_cli_result", "detail": "command completed (exit 0, 1ms)"})
    _script_llm(monkeypatch, [
        "I will look around first.",
        '{"act":"cli","data":{"cmd":"pwd"},"th":"Where am I"}',
        *_IDLE_UNTIL_THE_TURN_ENDS,
    ])
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Resume.", "source_channel": "work"}

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    # Only the valid action is a step; the unparseable reply and the refused
    # idles never reached the action.
    [step] = db.list_task_steps(task.id, limit=10)
    assert (step.note, step.command, step.outcome) == ("Where am I", "pwd", "ok")
    assert [event["outcome"] for event in step_sink.steps] == [None, "ok"]


# A command the operator approved or permitted runs in the resume prelude,
# before the action loop; it is a step of its own.

_APPROVED_COMMAND = "cargo test --workspace"


def _stub_resumed_command(
    monkeypatch: pytest.MonkeyPatch, name: str, cli_result: BossModCliResult, *, task_id: str, sink: _StepSink,
) -> list[Any]:
    """Answer ``core.bm_cli.runtime.<name>`` with ``cli_result``, never a real shell.

    Returns the list it fills, one entry per run: the command, and the steps
    stored and sent at the moment it ran.
    """
    seen: list[Any] = []

    def _fake(agent: Any, state: Any, command: str, content: str | None = None, **kwargs: Any) -> BossModCliResult:
        seen.append((command, db.list_task_steps(task_id, limit=10), list(sink.steps)))
        return cli_result

    monkeypatch.setattr(f"core.bm_cli.runtime.{name}", _fake)
    return seen


def _cli_result(command: str, *, ok: bool, detail: str) -> BossModCliResult:
    return BossModCliResult(command=command, ok=ok, detail=detail, kind="shell",
                            prompt_content=f"BOSSMOD CLI RESULT\ncommand: {command}\n\n{detail}")


def _assert_one_resumed_step(seen: list[Any], sink: _StepSink, task_id: str, *, command: str, outcome: str,
                             result: str) -> None:
    """The run was one step: stored and sent open before it ran, finished after."""
    [(ran, stored, sent)] = seen
    assert ran == command
    [running] = stored
    assert (running.action, running.command, running.note) == ("bm_cli", command, None)
    assert (running.outcome, running.result, running.ended_at) == (None, None, None)
    assert [(event["id"], event["ended_at"]) for event in sent] == [(running.id, None)]
    [finished] = db.list_task_steps(task_id, limit=10)
    assert finished.id == running.id
    assert (finished.outcome, finished.result) == (outcome, result)
    assert finished.ended_at is not None and finished.ended_at >= finished.started_at
    assert [(event["id"], event["outcome"]) for event in sink.steps] == [(running.id, None), (running.id, outcome)]
    assert sink.steps[-1]["result"] == result


@pytest.mark.asyncio
async def test_an_approved_command_resumed_on_its_task_is_one_step(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    detail = "command completed (exit 0, 225s)"
    seen = _stub_resumed_command(monkeypatch, "execute_approved_command",
                                 _cli_result(_APPROVED_COMMAND, ok=True, detail=detail),
                                 task_id=task.id, sink=step_sink)
    _script_llm(monkeypatch, _IDLE_UNTIL_THE_TURN_ENDS)
    trigger = {
        "type": "cli_approval_resolved", "task_id": task.id, "source_channel": "work", "status": "approved",
        "command": _APPROVED_COMMAND, "cwd": "/me", "approval_request_id": "approval-1",
    }

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    _assert_one_resumed_step(seen, step_sink, task.id, command=_APPROVED_COMMAND, outcome="ok", result=detail)


@pytest.mark.asyncio
async def test_a_rejected_approval_runs_nothing_and_writes_no_step(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    seen = _stub_resumed_command(monkeypatch, "execute_approved_command",
                                 _cli_result(_APPROVED_COMMAND, ok=True, detail="ran"),
                                 task_id=task.id, sink=step_sink)
    _script_llm(monkeypatch, _IDLE_UNTIL_THE_TURN_ENDS)
    trigger = {
        "type": "cli_approval_resolved", "task_id": task.id, "source_channel": "work", "status": "rejected",
        "command": _APPROVED_COMMAND, "decision_note": "Not now.", "approval_request_id": "approval-1",
    }

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert seen == []
    assert db.list_task_steps(task.id, limit=10) == []
    assert step_sink.steps == []


@pytest.mark.asyncio
async def test_a_detached_approved_resume_runs_its_command_but_writes_no_step(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    seen = _stub_resumed_command(monkeypatch, "execute_approved_command",
                                 _cli_result("pip install pytest", ok=True, detail="command completed (exit 0, 2s)"),
                                 task_id=task.id, sink=step_sink)
    _script_llm(monkeypatch, ['{"act":"idle","th":"handled the email"}'])
    trigger = {
        "type": "cli_approval_resolved", "source_channel": "system", "task_id": None, "detached_origin": True,
        "status": "approved", "command": "pip install pytest", "approval_request_id": "approval-1",
    }

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert [command for command, _stored, _sent in seen] == ["pip install pytest"]
    assert db.list_task_steps(task.id, limit=10) == []
    assert step_sink.steps == []


@pytest.mark.asyncio
async def test_a_permitted_command_resumed_on_its_task_is_one_step(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    command = "cat /srv/app/log"
    detail = "BossMod CLI error: cat: /srv/app/log: No such file or directory"
    seen = _stub_resumed_command(monkeypatch, "execute_bm_cli", _cli_result(command, ok=False, detail=detail),
                                 task_id=task.id, sink=step_sink)
    _script_llm(monkeypatch, _IDLE_UNTIL_THE_TURN_ENDS)
    trigger = {
        "type": "host_path_consent_resolved", "task_id": task.id, "source_channel": "work",
        "status": "allowed_once", "command": command, "consent_request_id": "consent-1",
    }

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    _assert_one_resumed_step(seen, step_sink, task.id, command=command, outcome="failed", result=detail)


@pytest.mark.asyncio
async def test_a_host_access_grant_runs_no_command_and_writes_no_step(
    monkeypatch: pytest.MonkeyPatch, step_sink: _StepSink,
) -> None:
    agent, task = _working_agent()
    seen = _stub_resumed_command(monkeypatch, "execute_bm_cli",
                                 _cli_result("request_host_access", ok=True, detail="granted"),
                                 task_id=task.id, sink=step_sink)
    _script_llm(monkeypatch, _IDLE_UNTIL_THE_TURN_ENDS)
    trigger = {
        "type": "host_path_consent_resolved", "task_id": task.id, "source_channel": "work",
        "status": "allowed_once", "command": "request_host_access", "path": "/srv/app",
        "consent_request_id": "consent-1",
    }

    await run_turn(agent, db.get_agent_state(agent.id), trigger)

    assert seen == []
    assert db.list_task_steps(task.id, limit=10) == []
    assert step_sink.steps == []
