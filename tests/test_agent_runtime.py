"""Critical-path agent runtime tests — no live LLM.

Covers HA-TEST-P1-01 (smoke-suite module), HA-CORR-P0-02 (CLI approval
resume), and HA-CORR-P0-03 (skip-turn must not exhaust the trigger).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.loop import run_turn
from core.agent_loop.watchdog import TaskWatchdog
from core.bm_cli.approvals import resume_cli_approval
from core.bm_cli.filesystem import resolve_relative_path
from core.models.message import HUMAN_SENDER_ID
from core.models.work_contract import DeliverableSpec, WorkContract
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
