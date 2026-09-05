"""Critical-path agent runtime tests — no live LLM.

Covers HA-TEST-P1-01 (smoke-suite module), HA-CORR-P0-01 (reused tasks
wake the assignee), HA-CORR-P0-02 (CLI approval resume),
HA-CORR-P0-03 (skip-turn must not exhaust the trigger),
HA-CORR-P1-06 (ambiguous match must not create a duplicate), and
HA-PROD-P1-01 (Assign Task API outcomes, including unassigned backlog).
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
from core.agent_loop.loop import run_turn
from core.agent_loop.watchdog import TaskWatchdog
from core.bm_cli.approvals import resume_cli_approval
from core.bm_cli.filesystem import resolve_relative_path
from core.models.message import HUMAN_SENDER_ID
from core.models.work_contract import DeliverableSpec, WorkContract
from core.runtime import runtime_services
from core.tasking import create_or_bind_task


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
    assert "no model configured" in outcome.diagnostic_error.lower()
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
async def test_task_watchdog_expires_stale_cli_approvals() -> None:
    agent = _create_agent()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    request = db.create_cli_approval_request(
        agent_id=agent.id,
        command="ls",
        expires_at=past,
    )

    await TaskWatchdog()._check_tasks()

    refreshed = db.get_cli_approval_request(request.id)
    assert refreshed is not None
    assert refreshed.status == "expired"


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
        row["status"] == "skipped" and row["error"] and "no model configured" in row["error"].lower()
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

    db.update_task(creation.task.id, assigned_to=agent.id, status="complete")
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
    first = db.create_task(title="Plan", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    second = db.create_task(title="Plan", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
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
    first = db.create_task(title="Plan", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    second = db.create_task(title="Plan", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
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
    first = db.create_task(title="Plan", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    db.create_task(title="Plan", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
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
