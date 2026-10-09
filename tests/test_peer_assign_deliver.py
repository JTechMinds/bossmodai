"""Capability pass item (3) — host-path peer assign → wake → edit → deliver.

Uses the same actions/decisions/triggers the live loop calls. No LLM.
Fixture names stay impersonal. Host writes pause for workspace preference
until the operator chooses. Host-roots jail stays fail-closed.
"""

from __future__ import annotations

import json
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
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.decision_runtime import apply_decision
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models.host_path_consent import WORKSPACE_PREFERENCE_KIND
from core.models.message import HUMAN_SENDER_ID
from core.models.work_contract import DeliverableSpec, WorkContract
from core.runtime import runtime_services
from core.tasking import create_or_bind_task
from core.tasking.transitions import transition_task
from tests.test_workspace_preference import choose_edit_host


def _set_host_roots(*roots: Path) -> None:
    db.set_setting(
        "workspace_host_roots",
        "\n".join(str(root) for root in roots),
        "cli_policy",
    )
    config.reload()


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


def _task_api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _queued(agent_id: str, *, trigger_type: str, task_id: str | None = None) -> list[dict[str, Any]]:
    rows = [
        row
        for row in db.list_agent_triggers(agent_id)
        if row["trigger_type"] == trigger_type and row["status"] == "queued"
    ]
    if task_id is not None:
        rows = [row for row in rows if row["task_id"] == task_id]
    return rows


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _file_text(storage_key: str, virtual_path: str) -> str:
    resolved = resolve_cli_path(storage_key, "/me", virtual_path)
    assert resolved.real_path is not None
    assert resolved.exists, f"missing deliverable {virtual_path}"
    return resolved.real_path.read_text(encoding="utf-8")


def _accept_work(agent, state, task, *, from_name: str, from_agent: str | None, reply: str):
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": task.title,
            "reply": reply,
        },
        agent,
        state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "content": task.description,
            "from_name": from_name,
            "from_agent": from_agent,
        },
    )
    persist_result_triggers(result)
    return result


@pytest.mark.asyncio
async def test_host_path_owner_assigns_worker_edits_and_deny_stays_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "cap-host"
    host.mkdir()
    fixture = host / "review.py"
    fixture.write_text('print("before-review")\n', encoding="utf-8")
    _set_host_roots(host)

    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    assigner_state = db.get_agent_state(assigner.id)
    worker_state = db.get_agent_state(worker.id)
    assert assigner_state is not None
    assert worker_state is not None

    client = _task_api_client(monkeypatch)
    headers = _headers()
    owned = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "Review host fixture",
            "description": "Review the allowlisted host file, then hand it off.",
            "assigned_to": assigner.id,
            "work_contract": {
                "deliverables": [
                    {"type": "file", "path": str(fixture), "description": "Host review file"},
                ]
            },
        },
    )
    assert owned.status_code == 201
    parent = db.get_task(owned.json()["task"]["id"])
    assert parent is not None
    assert parent.assigned_to == assigner.id
    parent_path = parent.work_contract.deliverables[0].path
    assert Path(parent_path) == fixture.resolve()

    accepted_parent = _accept_work(
        assigner,
        assigner_state,
        parent,
        from_name="Operator",
        from_agent=None,
        reply="I will hand this host-path review to Cap Worker.",
    )
    assert accepted_parent["event"] == "decision_applied"

    assigned = await execute_action(
        {
            "action": "delegateTask",
            "agentId": worker.id,
            "taskTitle": "Edit host review file",
            "taskDescription": "Read and edit the allowlisted host fixture.",
            "deliverables": [
                {"type": "file", "path": str(fixture), "description": "Host review file"},
            ],
        },
        assigner,
        assigner_state,
    )
    assert assigned["event"] == "status_changed"
    persist_result_triggers(assigned)

    children = db.list_tasks(parent_task_id=parent.id, assigned_to=worker.id)
    assert len(children) == 1
    child = children[0]
    assert child.status == "pending"
    assert child.requester_id == assigner.id
    deliverable_path = child.work_contract.deliverables[0].path
    assert Path(deliverable_path) == fixture.resolve()
    assert not deliverable_path.startswith("/projects/")

    wakes = _queued(worker.id, trigger_type="task_assigned", task_id=child.id)
    assert len(wakes) == 1
    assert _payload(wakes[0]).get("from_agent") == assigner.id

    accepted = _accept_work(
        worker,
        worker_state,
        child,
        from_name=assigner.name,
        from_agent=assigner.id,
        reply="I will edit the host fixture.",
    )
    assert accepted["event"] == "decision_applied"
    assert db.get_task(child.id).status == "accepted"

    read = await execute_action(
        {"action": "bm_cli", "command": f"cat {deliverable_path}"},
        worker,
        worker_state,
    )
    assert read["event"] == "bm_cli_result"
    assert "before-review" in read.get("cli_prompt_content", "") + read.get("detail", "")

    written = await execute_action(
        {
            "action": "bm_cli",
            "command": f"write {deliverable_path}",
            "content": 'print("after-review")\n',
        },
        worker,
        worker_state,
    )
    assert written["event"] == "workspace_preference_required"
    assert written.get("consent_required") is True
    card = written.get("host_path_consent") or {}
    assert card.get("kind") == WORKSPACE_PREFERENCE_KIND
    request_id = written.get("consent_request_id")
    assert request_id
    assert fixture.read_text(encoding="utf-8") == 'print("before-review")\n'

    choose_edit_host(client, request_id, headers=headers)

    written = await execute_action(
        {
            "action": "bm_cli",
            "command": f"write {deliverable_path}",
            "content": 'print("after-review")\n',
        },
        worker,
        worker_state,
    )
    assert written["event"] == "bm_cli_result"
    assert fixture.read_text(encoding="utf-8") == 'print("after-review")\n'

    denied = await execute_action(
        {"action": "bm_cli", "command": "cat /etc/passwd"},
        worker,
        worker_state,
    )
    assert denied["event"] == "bm_cli_error"
    deny_text = (denied.get("detail") or "") + (denied.get("cli_prompt_content") or "")
    assert "is not a boss-allowed host path" in deny_text
    assert "request_host_access" in deny_text
    assert fixture.read_text(encoding="utf-8") == 'print("after-review")\n'

    http_deny = client.get("/api/company/files", params={"path": "/etc/passwd"}, headers=headers)
    assert http_deny.status_code == 400
    assert "outside the allowed workspace roots" in http_deny.json()["detail"]

    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Updated the host review file.",
            "followUpMessage": "Host fixture is edited under the allowlisted root.",
        },
        worker,
        worker_state,
    )
    assert completed["event"] == "status_changed"
    persist_result_triggers(completed)

    done = db.get_task(child.id)
    assert done is not None
    assert done.status == "complete"
    assert 'print("after-review")' in _file_text(worker.storage_key, deliverable_path)

    opened = client.get("/api/company/files", params={"path": str(fixture)}, headers=headers)
    assert opened.status_code == 200
    assert opened.json()["content"] == 'print("after-review")\n'

    notes = db.list_notifications(agent_id=assigner.id)
    assert any(item.task_id in {child.id, parent.id} for item in notes)
    observer_triggers = _queued(assigner.id, trigger_type="task_update")
    assert observer_triggers
    assert any(row["task_id"] in {child.id, parent.id} for row in observer_triggers)
    parent_updates = [row for row in observer_triggers if row["task_id"] == parent.id]
    if parent_updates:
        assert "completed" in _payload(parent_updates[0]).get("content", "").lower()


@pytest.mark.asyncio
async def test_peer_assignee_decline_wakes_assigner_and_stays_declined() -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    assigner_state = db.get_agent_state(assigner.id)
    worker_state = db.get_agent_state(worker.id)
    assert assigner_state is not None
    assert worker_state is not None

    assigned = await execute_action(
        {
            "action": "delegateTask",
            "agentId": worker.id,
            "taskTitle": "Write declined note",
            "taskDescription": "This assignment should be declined.",
            "project": "cap-peer",
            "deliverables": [
                {"type": "file", "path": "/me/declined-note.md", "description": "Should not exist"},
            ],
        },
        assigner,
        assigner_state,
    )
    persist_result_triggers(assigned)
    task = db.list_tasks(assigned_to=worker.id)[0]
    deliverable_path = task.work_contract.deliverables[0].path if task.work_contract else ""

    declined = apply_decision(
        {
            "decision": "decline",
            "intentKind": "work_request",
            "commitmentKind": "none",
            "reply": "I cannot take this assignment.",
        },
        worker,
        worker_state,
        {
            "type": "task_assigned",
            "task_id": task.id,
            "content": task.description,
            "from_name": assigner.name,
            "from_agent": assigner.id,
        },
    )
    assert declined["event"] == "decision_applied"
    persist_result_triggers(declined)

    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "declined"
    follow_ups = _queued(assigner.id, trigger_type="task_follow_up", task_id=task.id)
    assert follow_ups
    assert "cannot take this assignment" in _payload(follow_ups[0]).get("content", "").lower()
    resolved = resolve_cli_path(worker.storage_key, "/me", deliverable_path)
    assert not resolved.exists


@pytest.mark.asyncio
async def test_delegated_parent_points_at_the_childs_shared_file_and_can_complete() -> None:
    """A delegated parent's deliverable follows the child's /projects rewrite.

    Without that, the parent kept its /me path, resolved it on its own
    assignee's desk, and could never be completed after the child delivered.
    """
    lead = db.create_agent("Cap Lead", role="Writer", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    lead_state = db.get_agent_state(lead.id)
    worker_state = db.get_agent_state(worker.id)
    assert lead_state is not None
    assert worker_state is not None
    parent = create_or_bind_task(
        title="Write the launch note",
        description=None,
        project=None,
        assigned_to=lead.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=WorkContract(deliverables=[DeliverableSpec(type="file", path="/me/launch.md")]),
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task
    assert parent is not None
    activate_work_activity(lead.id, parent)

    delegated = await execute_action(
        {
            "action": "delegated",
            "agentId": worker.id,
            "followUpMessage": "Handing this to Worker.",
        },
        lead,
        lead_state,
    )
    assert delegated["event"] == "status_changed"
    children = db.list_tasks(parent_task_id=parent.id)
    assert len(children) == 1
    child = children[0]
    assert child.work_contract is not None
    shared_path = child.work_contract.deliverables[0].path
    assert shared_path.startswith("/projects/")
    refreshed_parent = db.get_task(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.work_contract == child.work_contract

    transition_task(child.id, "accepted", reason="Accepted.", actor=worker.name, actor_type="agent")
    activate_work_activity(worker.id, db.get_task(child.id))
    written = resolve_cli_path(worker.storage_key, "/", shared_path)
    assert written.real_path is not None
    written.real_path.parent.mkdir(parents=True, exist_ok=True)
    written.real_path.write_text("launch note", encoding="utf-8")
    child_done = await execute_action(
        {"action": "complete", "summary": "Launch note written.", "followUpMessage": "Done."},
        worker,
        worker_state,
    )
    assert child_done["event"] == "status_changed", child_done.get("detail")

    activate_work_activity(lead.id, db.get_task(parent.id))
    parent_done = await execute_action(
        {"action": "complete", "summary": "Worker delivered the note.", "followUpMessage": "Done."},
        lead,
        lead_state,
    )
    assert parent_done["event"] == "status_changed", parent_done.get("detail")
    final = db.get_task(parent.id)
    assert final is not None
    assert final.status == "complete"


def test_work_plan_ambiguous_assignee_name_does_not_create_child() -> None:
    lead = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    db.create_agent("Cap Worker", role="Editor", desk_x=3, desk_y=1)
    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Coordinate cap note", assigned_to=lead.id, created_by=lead.id)

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent.title,
            "reply": "Cap Worker can take the note.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [{"agentName": "Cap Worker", "taskTitle": "Write cap status note"}],
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

    assert result["event"] == "world_feedback"
    assert "more than one teammate" in result["detail"].lower()
    refreshed = db.get_task(parent.id)
    assert refreshed is not None
    assert refreshed.status != "accepted"
    assert db.list_tasks(parent_task_id=parent.id) == []


def test_create_task_api_peer_requester_queues_wake_and_lists_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    client = _task_api_client(monkeypatch)
    headers = _headers()

    created = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "Write cap status note",
            "description": "Write a short status note for the capability pass.",
            "project": "cap-peer",
            "assigned_to": worker.id,
            "requester_id": assigner.id,
            "work_contract": {
                "deliverables": [
                    {"type": "file", "path": "/me/status-note.md", "description": "Status note"},
                ]
            },
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["outcome"] == "create_new_task"
    task = body["task"]
    assert task["assigned_to"] == worker.id
    assert task["requester_id"] == assigner.id
    assert task["work_contract"]["deliverables"][0]["path"].startswith(f"/projects/cap-peer/{task['id']}/")

    triggers = client.get(f"/api/agents/{worker.id}/triggers", headers=headers)
    assert triggers.status_code == 200
    rows = triggers.json()
    assert any(
        row["trigger_type"] == "task_assigned"
        and row["task_id"] == task["id"]
        and row["status"] == "queued"
        and row["payload"].get("from_agent") == assigner.id
        for row in rows
    )


def _operator_thread_task_with_me_deliverable(*, assignee_id: str, channel_id: str):
    return create_or_bind_task(
        title="Write the thread note",
        description=None,
        project=None,
        assigned_to=assignee_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=WorkContract(deliverables=[DeliverableSpec(type="file", path="/me/x.md")]),
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel_id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def test_operator_task_on_multi_party_thread_gets_shared_deliverable_path() -> None:
    """Done rejects /me on a multi-party thread, so creation must not store one."""
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=1, desk_y=1)
    peer = db.create_agent("Cap Peer", role="Editor", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Cap Worker, Cap Peer",
        member_agent_ids=[worker.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    task = _operator_thread_task_with_me_deliverable(assignee_id=worker.id, channel_id=channel.id)
    assert task is not None
    assert task.requester_id == HUMAN_SENDER_ID
    assert task.work_contract is not None
    assert task.work_contract.deliverables[0].path == f"/projects/shared/{task.id}/x.md"
    stored = db.get_task(task.id)
    assert stored is not None
    assert stored.work_contract == task.work_contract


def test_operator_task_in_one_to_one_thread_keeps_me_deliverable_path() -> None:
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Cap Worker",
        member_agent_ids=[worker.id],
        created_by=HUMAN_SENDER_ID,
    )
    task = _operator_thread_task_with_me_deliverable(assignee_id=worker.id, channel_id=channel.id)
    assert task is not None
    assert task.work_contract is not None
    assert task.work_contract.deliverables[0].path == "/me/x.md"


def test_create_task_api_host_path_outside_roots_is_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "cap-host"
    host.mkdir()
    _set_host_roots(host)
    agent = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    client = _task_api_client(monkeypatch)

    response = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Escape host jail",
            "assigned_to": agent.id,
            "work_contract": {
                "deliverables": [
                    {"type": "file", "path": "/etc/passwd", "description": "Denied"},
                ]
            },
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "is not a boss-allowed host path" in detail
    assert "request_host_access" in detail
    assert db.list_tasks() == []


@pytest.mark.asyncio
async def test_delegate_task_host_path_outside_roots_fails_closed(tmp_path: Path) -> None:
    host = tmp_path / "cap-host"
    host.mkdir()
    _set_host_roots(host)
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    state = db.get_agent_state(assigner.id)
    assert state is not None

    result = await execute_action(
        {
            "action": "delegateTask",
            "agentId": worker.id,
            "taskTitle": "Escape host jail",
            "taskDescription": "This path must stay denied.",
            "deliverables": [
                {"type": "file", "path": "/etc/passwd", "description": "Denied"},
            ],
        },
        assigner,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "is not a boss-allowed host path" in result["detail"]
    assert db.list_tasks(assigned_to=worker.id) == []


def test_create_task_api_unknown_assignee_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _task_api_client(monkeypatch)
    response = client.post(
        "/api/tasks",
        headers=_headers(),
        json={"title": "Orphan assign", "assigned_to": "missing-agent"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Assigned agent not found"
    assert db.list_tasks() == []


def test_get_agent_triggers_unknown_agent_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _task_api_client(monkeypatch)
    response = client.get("/api/agents/missing-agent/triggers", headers=_headers())
    assert response.status_code == 404


# ─── A completion report lets the requester start the next step ───


def _record_tool_evidence(agent_id: str) -> None:
    db.create_bm_cli_event(
        agent_id=agent_id,
        command="cat /projects/m0.md",
        content_present=False,
        executor="virtual",
        cwd_before="/",
        cwd_after="/",
        policy_tier="read",
        decision="allowed",
        exit_code=0,
        result_kind="read",
        stdout_preview="ok",
        stderr_preview=None,
        changed_paths=None,
        trigger_type="activity_resumed",
    )


async def _complete_active(agent, *, summary: str) -> dict[str, Any]:
    state = db.get_agent_state(agent.id)
    assert state is not None
    _record_tool_evidence(agent.id)
    completed = await execute_action(
        {
            "action": "complete",
            "summary": summary,
            "followUpMessage": summary,
            "doneClaim": {"type": "proof", "ev": "work checked in"},
        },
        agent,
        state,
    )
    assert completed["event"] == "status_changed", completed.get("detail")
    persist_result_triggers(completed)
    return completed


def _turn_trigger(row: dict[str, Any]) -> dict[str, Any]:
    """The trigger dict a turn runs on (``dispatcher._turn_trigger``)."""
    return {
        **_payload(row),
        "type": row["trigger_type"],
        "trigger_id": row["id"],
        "task_id": row["task_id"],
        "source_channel": row["source_channel"],
    }


def _decide(agent, trigger: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate one decision object as the turn would, then apply it."""
    from core.agent_loop.decision_contract import (
        ConversationDecision,
        parse_decision,
        validate_decision_for_trigger,
    )

    parsed = parse_decision(json.dumps({**raw, "work_commit": False} if raw.get("act") == "reply" else raw))
    assert parsed.get("decision") != "_parse_failed", parsed
    decision = ConversationDecision.model_validate(parsed)
    error = validate_decision_for_trigger(
        decision,
        trigger_type=trigger["type"],
        active_task_id=None,
        has_live_work=False,
        agent_id=agent.id,
        trigger=trigger,
    )
    assert error is None, error
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(decision.model_dump(), agent, state, trigger)
    assert result["event"] == "decision_applied", result.get("detail")
    persist_result_triggers(result)
    return result


def _reported_milestone(lead, worker, channel):
    task = create_or_bind_task(
        title="Build milestone M0",
        description="Scaffold the project.",
        project=None,
        assigned_to=worker.id,
        requester_id=lead.id,
        owner_id=None,
        created_by=lead.id,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="none",
        notification_channel_id=channel.id,
        audit_author_name=lead.name,
        audit_author_type="agent",
        audit_author_agent_id=lead.id,
    ).task
    assert task is not None
    activate_work_activity(worker.id, task)
    return task


async def _completion_report_to(lead, worker, channel) -> tuple[Any, dict[str, Any]]:
    reported = _reported_milestone(lead, worker, channel)
    await _complete_active(worker, summary="M0 scaffold is in.")
    rows = _queued(lead.id, trigger_type="task_update", task_id=reported.id)
    assert len(rows) == 1
    trigger = _turn_trigger(rows[0])
    assert trigger["task_party"] == "stakeholder"
    assert trigger["attention_kind"] == "completion_report"
    assert trigger["from_agent"] == worker.id
    assert trigger["channel_id"] == channel.id
    return reported, trigger


@pytest.mark.asyncio
async def test_requester_accepts_the_next_step_from_a_completion_report() -> None:
    lead = db.create_agent("Cap Planner", role="Planner", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Builder", role="Engineer", desk_x=2, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=3, desk_y=1)
    channel = db.create_channel(
        name="Pipeline",
        member_agent_ids=[lead.id, worker.id, auditor.id],
        created_by=lead.id,
    )
    reported, trigger = await _completion_report_to(lead, worker, channel)

    _decide(
        lead,
        trigger,
        {
            "act": "accept",
            "intent": "work",
            "msg": "M0 is in. Getting the audit started.",
            "commit": "work",
            "data": {
                "task": {"title": "Coordinate M0 audit", "desc": "Own the M0 audit and report back."},
                "plan": {
                    "mode": "delegate",
                    "children": [{"who": auditor.name, "task": {"title": "Audit M0", "desc": "Review the scaffold."}}],
                },
            },
            "th": "start the next step",
        },
    )

    coordination = next(task for task in db.list_tasks(assigned_to=lead.id) if task.title == "Coordinate M0 audit")
    assert coordination.requester_id == lead.id
    assert coordination.owner_id == lead.id
    assert coordination.created_by == lead.id
    assert coordination.parent_task_id is None
    assert coordination.source_channel == "channel"
    assert coordination.notification_channel_id == channel.id
    assert coordination.notification_policy == "none"
    assert db.get_task(reported.id).status == "complete"

    children = db.list_tasks(parent_task_id=coordination.id)
    assert [(child.title, child.assigned_to, child.status) for child in children] == [
        ("Audit M0", auditor.id, "pending"),
    ]
    child = children[0]
    assert child.requester_id == lead.id
    assert child.notification_channel_id == channel.id
    assert _queued(auditor.id, trigger_type="task_assigned", task_id=child.id)

    # The child's completion reaches the coordination task through the parent path.
    _accept_work(
        auditor,
        db.get_agent_state(auditor.id),
        child,
        from_name=lead.name,
        from_agent=lead.id,
        reply="On it.",
    )
    transition_task(coordination.id, "waiting", reason="Waiting on the audit.", actor=lead.name, actor_type="agent")
    await _complete_active(auditor, summary="M0 audit passed.")
    parent_rows = _queued(lead.id, trigger_type="task_update", task_id=coordination.id)
    assert len(parent_rows) == 1
    parent_trigger = _turn_trigger(parent_rows[0])
    assert parent_trigger["task_party"] == "assignee"
    resumed = _decide(lead, parent_trigger, {"act": "observe", "intent": "other", "th": "audit done"})
    resumes = [item for item in resumed["trigger_requests"] if item.get("trigger_type") == "activity_resumed"]
    assert [item["task_id"] for item in resumes] == [coordination.id]


@pytest.mark.asyncio
async def test_requester_defers_the_next_step_as_its_own_pending_work() -> None:
    lead = db.create_agent("Cap Planner", role="Planner", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Builder", role="Engineer", desk_x=2, desk_y=1)
    channel = db.create_channel(name="Pipeline", member_agent_ids=[lead.id, worker.id], created_by=lead.id)
    _, trigger = await _completion_report_to(lead, worker, channel)

    _decide(
        lead,
        trigger,
        {
            "act": "defer",
            "intent": "work",
            "msg": "Queued M1 behind my current work.",
            "commit": "work",
            "data": {"task": {"title": "Plan milestone M1", "desc": "Break M1 into cards."}},
            "th": "queue the next step",
        },
    )
    deferred = next(task for task in db.list_tasks(assigned_to=lead.id) if task.title == "Plan milestone M1")
    assert deferred.status == "pending"
    assert deferred.requester_id == lead.id
    assert deferred.notification_channel_id == channel.id


@pytest.mark.asyncio
async def test_requester_reply_on_a_completion_report_goes_to_the_reporter() -> None:
    lead = db.create_agent("Cap Planner", role="Planner", desk_x=1, desk_y=1)
    worker = db.create_agent("Cap Builder", role="Engineer", desk_x=2, desk_y=1)
    channel = db.create_channel(name="Pipeline", member_agent_ids=[lead.id, worker.id], created_by=lead.id)
    reported, trigger = await _completion_report_to(lead, worker, channel)
    before = {item.id for item in db.list_notifications(agent_id=worker.id)}

    result = _decide(
        lead,
        trigger,
        {"act": "reply", "intent": "status", "msg": "Nice work on M0, thanks.", "th": "acknowledge"},
    )

    events = [event for event in db.list_task_events(reported.id) if event.event_type == "answer"]
    assert [(event.author_agent_id, event.content) for event in events] == [(lead.id, "Nice work on M0, thanks.")]
    notes = [item for item in db.list_notifications(agent_id=worker.id) if item.id not in before]
    assert [(item.task_id, item.content) for item in notes] == [(reported.id, "Nice work on M0, thanks.")]
    assert all(item.content != "Nice work on M0, thanks." for item in db.list_notifications(agent_id=lead.id))
    assert not result.get("channel_message")
    assert not result.get("chat_message")
