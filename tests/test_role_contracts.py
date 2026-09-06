"""Role contracts v1 — hire fields, assign mismatch, checkable done claims."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.role_contracts import (
    format_role_contract_block,
    infer_work_kind,
    match_specialty,
    operator_done_claim_guidance,
    prefer_specialty_match,
    specialty_family,
)
from core.llm import context_preview
from db.unified_feed import classify_category
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking import create_or_bind_task


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


def _api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _bind_task(agent_id: str, title: str = "Note", description: str | None = None):
    return create_or_bind_task(
        title=title,
        description=description,
        project=None,
        assigned_to=agent_id,
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


def _write_me_file(storage_key: str, virtual_path: str, content: str) -> str:
    resolved = resolve_cli_path(storage_key, "/me", virtual_path)
    assert resolved.real_path is not None
    resolved.real_path.parent.mkdir(parents=True, exist_ok=True)
    resolved.real_path.write_text(content, encoding="utf-8")
    return resolved.virtual_path


# ---------------------------------------------------------------------------
# Hire / persistence
# ---------------------------------------------------------------------------


def test_create_and_update_agent_api_persists_specialty_and_done_fail_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    created = client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "name": "Cap Writer",
            "role": "Writer",
            "done_fail_bar": "Good: draft path exists. Fail: empty done.",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Cap Writer"
    assert body["role"] == "Writer"
    assert body["done_fail_bar"] == "Good: draft path exists. Fail: empty done."

    listed = client.get("/api/agents", headers=_headers())
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["id"] == body["id"])
    assert row["role"] == "Writer"
    assert "empty done" in row["done_fail_bar"]

    company = client.get("/api/company/agents", headers=_headers())
    assert company.status_code == 200
    company_row = next(item for item in company.json() if item["id"] == body["id"])
    assert company_row["role"] == "Writer"
    assert company_row["done_fail_bar"] == body["done_fail_bar"]

    patched = client.patch(
        f"/api/agents/{body['id']}",
        headers=_headers(),
        json={
            "role": "Auditor",
            "done_fail_bar": "CLEAR only against a checkable claim.",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["role"] == "Auditor"
    assert patched.json()["done_fail_bar"] == "CLEAR only against a checkable claim."

    persisted = db.get_agent(body["id"])
    assert persisted is not None
    assert persisted.role == "Auditor"
    assert persisted.done_fail_bar == "CLEAR only against a checkable claim."


def test_hire_form_keeps_role_field_and_adds_done_fail_bar() -> None:
    panel = Path("ui/static/js/agent-panel.js").read_text(encoding="utf-8")
    assert 'id="role-contract-card"' in panel
    assert "Role contract" in panel
    assert 'name="role"' in panel
    assert "Specialty" in panel
    assert 'name="done_fail_bar"' in panel
    assert "done_fail_bar: formData.get('done_fail_bar')" in panel
    assert 'id="advanced-toggle"' in panel
    assert "Advanced" in panel
    assert 'name="personality_id"' in panel
    assert panel.index("Role contract") < panel.index("Advanced")
    assert panel.index("Advanced") < panel.index('name="personality_id"')
    tasks_js = Path("ui/static/js/company-tasks.js").read_text(encoding="utf-8")
    assert "specialty_mismatch" in tasks_js
    assert "confirm_specialty_mismatch" in tasks_js
    assert "ct-assign-mismatch" in tasks_js
    assert "specialtyWarningMessage" in tasks_js
    assert "(matches)" in tasks_js
    assert "(mismatch)" in tasks_js
    detail_js = Path("ui/static/js/company-task-detail.js").read_text(encoding="utf-8")
    assert "doneClaimGuidance" in detail_js
    assert "Checkable done claim" in detail_js
    assert "Done claim" in detail_js
    assert "allow/deny proof" in detail_js
    activity_js = Path("ui/static/js/activity.js").read_text(encoding="utf-8")
    assert "world_feedback" in activity_js
    context_js = Path("ui/static/js/agent-context.js").read_text(encoding="utf-8")
    assert "No specialty" in context_js
    assert "done_fail_bar" in context_js
    assert "doneClaimGuidance" in context_js
    board = Path("core/tasking/board.py").read_text(encoding="utf-8")
    assert "done_claim_guidance" in board
    assert "operator_done_claim_guidance" in board


# ---------------------------------------------------------------------------
# Assign / routing
# ---------------------------------------------------------------------------


def test_specialty_inference_is_conservative() -> None:
    assert specialty_family("Writer") == "write"
    assert specialty_family("Lead") == "coordinate"
    assert infer_work_kind("Write the status note", None) == "write"
    assert infer_work_kind("Review the audit package", None) == "review"
    assert infer_work_kind("Coordinate the rollout", None) is None
    assert match_specialty(assignee_role="Writer", work_kind="write") == "match"
    assert match_specialty(assignee_role="Writer", work_kind="review") == "mismatch"
    assert match_specialty(assignee_role="Lead", work_kind="write") == "unknown"
    assert match_specialty(assignee_role="Eng", work_kind="write") == "unknown"


def test_assign_api_soft_denies_specialty_mismatch_until_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=2, desk_y=1)

    denied = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Review the security audit",
            "description": "Audit the package and report findings.",
            "assigned_to": writer.id,
        },
    )
    assert denied.status_code == 409
    body = denied.json()
    assert body["outcome"] == "specialty_mismatch"
    assert body["task"] is None
    assert "Writer" in (body["reason"] or "")
    assert any(item["id"] == auditor.id for item in body["suggested_assignees"])
    assert db.list_tasks() == []

    confirmed = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Review the security audit",
            "description": "Audit the package and report findings.",
            "assigned_to": writer.id,
            "confirm_specialty_mismatch": True,
        },
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["outcome"] == "create_new_task"
    assert confirmed.json()["specialty_warning"]
    assert db.list_tasks(assigned_to=writer.id)


def test_assign_api_matching_specialty_creates_without_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)

    created = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Write the status note",
            "description": "Draft a short status note.",
            "assigned_to": writer.id,
        },
    )
    assert created.status_code == 201
    assert created.json()["outcome"] == "create_new_task"
    assert created.json()["specialty_warning"] is None
    assert created.json()["task"]["assigned_to"] == writer.id


def test_unassigned_create_prefers_matching_specialty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=2, desk_y=1)

    created = client.post(
        "/api/tasks",
        headers=_headers(),
        json={"title": "Review the security audit"},
    )
    assert created.status_code == 201
    suggested = created.json()["suggested_assignees"]
    assert suggested
    assert suggested[0]["id"] == auditor.id
    assert suggested[0]["match"] == "match"


@pytest.mark.asyncio
async def test_delegate_task_mismatch_is_world_feedback() -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=2, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=3, desk_y=1)
    state = db.get_agent_state(assigner.id)
    assert state is not None

    denied = await execute_action(
        {
            "action": "delegateTask",
            "agentId": writer.id,
            "taskTitle": "Review the security audit",
            "taskDescription": "Audit the package.",
        },
        assigner,
        state,
    )
    assert denied["event"] == "world_feedback"
    assert "Writer" in denied["detail"]
    assert any(item["id"] == auditor.id for item in denied["suggested_assignees"])
    assert db.list_tasks() == []

    confirmed = await execute_action(
        {
            "action": "delegateTask",
            "agentId": writer.id,
            "taskTitle": "Review the security audit",
            "taskDescription": "Audit the package.",
            "confirmSpecialtyMismatch": True,
        },
        assigner,
        state,
    )
    assert confirmed["event"] == "status_changed"
    assert confirmed.get("specialty_warning")
    assert db.list_tasks(assigned_to=writer.id)


def test_work_plan_prefers_matching_specialty_on_duplicate_names() -> None:
    lead = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    auditor = db.create_agent("Cap Worker", role="Auditor", desk_x=3, desk_y=1)
    preferred = prefer_specialty_match(
        [writer, auditor],
        title="Write the status note",
        description="Draft the note.",
    )
    assert preferred is not None
    assert preferred.id == writer.id

    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Coordinate the note", assigned_to=lead.id, created_by=lead.id)
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent.title,
            "reply": "Cap Worker can take the note.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [{"agentName": "Cap Worker", "taskTitle": "Write the status note"}],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Coordinate the note",
            "from_name": "Operator",
        },
    )
    assert result["event"] == "decision_applied"
    children = db.list_tasks(parent_task_id=parent.id)
    assert len(children) == 1
    assert children[0].assigned_to == writer.id


def test_work_plan_mismatch_does_not_create_child() -> None:
    lead = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=2, desk_y=1)
    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Coordinate the audit", assigned_to=lead.id, created_by=lead.id)

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent.title,
            "reply": "Cap Writer can take the audit.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [
                    {
                        "agentId": writer.id,
                        "taskTitle": "Review the security audit",
                    }
                ],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Coordinate the audit",
            "from_name": "Operator",
        },
    )
    assert result["event"] == "world_feedback"
    assert "Writer" in result["detail"]
    refreshed = db.get_task(parent.id)
    assert refreshed is not None
    assert refreshed.status != "accepted"
    assert db.list_tasks(parent_task_id=parent.id) == []


# ---------------------------------------------------------------------------
# Checkable done claims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_without_checkable_claim_is_rejected() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {"action": "complete", "summary": "Finished."},
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "checkable claim" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_auditor_clear_without_claim_is_rejected() -> None:
    agent = db.create_agent("Cap Auditor", role="Auditor", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Review the package")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {"action": "complete", "summary": "Looks good."},
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "clear" in result["detail"].lower()
    assert "checkable claim" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_complete_with_tests_claim_succeeds() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Tests passed.",
            "doneClaim": {"type": "tests", "evidence": "pytest tests/test_role_contracts.py: 12 passed"},
        },
        agent,
        state,
    )
    assert result["event"] == "status_changed"
    assert result["done_claim"]["type"] == "tests"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


@pytest.mark.asyncio
async def test_complete_with_missing_artifact_claim_is_rejected() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Wrote the note.",
            "doneClaim": {"type": "artifact", "path": "/me/missing-note.md"},
        },
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "does not exist" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_complete_with_existing_artifact_claim_succeeds() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    path = _write_me_file(agent.storage_key, "/me/note.md", "draft")
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Wrote the note.",
            "doneClaim": {"type": "artifact", "path": path},
        },
        agent,
        state,
    )
    assert result["event"] == "status_changed"
    assert result["done_claim"]["type"] == "artifact"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


def test_parse_done_action_keeps_claim() -> None:
    parsed = parse_action(
        '{"act":"done","data":{"sum":"Draft saved.","msg":"Finished.","claim":{"type":"tests","ev":"12 passed"}},"th":"complete"}'
    )
    assert parsed["action"] == "complete"
    assert parsed["doneClaim"]["type"] == "tests"
    assert parsed["doneClaim"]["ev"] == "12 passed"


def test_role_contract_block_and_done_claim_guidance_are_operator_actionable() -> None:
    agent = db.create_agent(
        "Cap Writer",
        role="Writer",
        done_fail_bar="Good: draft path exists. Fail: empty done.",
        desk_x=1,
        desk_y=1,
    )
    block = format_role_contract_block(agent)
    assert "# Role contract" in block
    assert "Specialty: Writer" in block
    assert "Good: draft path exists" in block
    assert "data.claim" in block
    assert "Empty done is rejected" in block
    guidance = operator_done_claim_guidance(
        auditor=False,
        done_fail_bar=agent.done_fail_bar,
        has_file_deliverables=False,
    )
    assert "tests evidence" in guidance
    assert "artifact path" in guidance
    assert "allow/deny proof" in guidance
    assert "Good: draft path exists" in guidance


def test_task_list_includes_assignee_contract_and_done_claim_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    writer = db.create_agent(
        "Cap Writer",
        role="Writer",
        done_fail_bar="Good: draft path exists. Fail: empty done.",
        desk_x=1,
        desk_y=1,
    )
    _bind_task(writer.id, title="Write a note")
    listed = client.get("/api/tasks", headers=_headers())
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["assigned_to"] == writer.id)
    assert row["assigned_to_role"] == "Writer"
    assert row["assigned_to_done_fail_bar"] == "Good: draft path exists. Fail: empty done."
    assert "checkable claim" in row["done_claim_guidance"]
    assert "Good: draft path exists" in row["done_claim_guidance"]
    assert row["done_claim"] is None


def test_preview_bundle_injects_role_contract() -> None:
    preview = context_preview.preview_prompt_bundle("execution", "activity_resumed")
    contents = "\n".join(str(message.get("content") or "") for message in preview["messages"])
    assert "# Role contract" in contents
    assert "Specialty:" in contents
    assert "data.claim" in contents
    assert "Empty done" in contents


def test_world_feedback_is_a_task_feed_event() -> None:
    assert classify_category("activity_log", "world_feedback") == "task"
