"""Standing backlog: QA agents file unassigned work with ``backlog``; the boss triages it.

Covers the backlog plan (strategy-docs/qaBacklog_analysis_10082026_1791494932557.md):
filing through the real CLI, the floor-scoped list, the owner and floor rules
that keep a filing alive and on its floor, severity (P0–P3, P3 by default),
reference documents, and the migration that gives existing tasks a severity.
Everything runs against the isolated test database and roots (conftest.py).
No LLM.
"""

from __future__ import annotations

import asyncio
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
from core.agent_loop.actions_tasks import _handle_delegate_task
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import build_task_assigned_trigger
from core.agent_loop.turn_context import _get_current_task
from core.agent_loop.watchdog import TaskWatchdog
from core.agent_repository import agent_repository
from core.bm_cli.floor_roots import projects_root_for_storage_key
from core.bm_cli.runtime import execute_bm_cli
from core.default_prompts import load_default_prompt
from core.floors import send_home
from core.llm.context_builder import _format_task, _format_trigger
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID
from core.models.schedule import RecurrenceRule
from core.runtime import runtime_services
from core.scheduling.runner import run_now
from core.tasking.service import list_open_child_tasks
from core.tasking.transitions import transition_task
from db.connection import get_connection
from db.floors import create_floor

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
REFERENCE = "/projects/webapp/qa/issue-login-500.md"


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


# ─── helpers ───


def _agent(name: str, *, floor_id: str | None = None) -> Agent:
    kwargs: dict[str, Any] = {"floor_id": floor_id} if floor_id else {}
    return db.create_agent(name, role="QA", desk_x=1, desk_y=1, **kwargs)


def _cli(agent: Agent, command: str, body: dict[str, Any] | None = None):
    content = json.dumps(body) if body is not None else None
    return execute_bm_cli(agent, db.get_agent_state(agent.id), command, content)


def _file(agent: Agent, **body: Any):
    result = _cli(agent, "backlog add", body)
    assert result.ok, result.prompt_content
    return result


def _write_project_file(agent: Agent, virtual_path: str, text: str = "Steps to reproduce.") -> Path:
    relative = virtual_path.removeprefix("/projects/")
    path = projects_root_for_storage_key(agent.storage_key) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _all_triggers(*agents: Agent) -> list[dict[str, Any]]:
    return [row for agent in agents for row in db.list_agent_triggers(agent.id)]


# ─── filing ───


def test_filing_creates_an_unassigned_unowned_unparented_item_and_wakes_nobody() -> None:
    qa = _agent("Quinn")
    engineer = _agent("Eve")

    result = _file(qa, title="Login returns 500", severity="P1", description="Wrong password.", project="webapp")

    task = db.get_task(result.data["task_id"])
    assert (task.status, task.assigned_to, task.owner_id, task.parent_task_id) == ("pending", None, None, None)
    assert (task.requester_id, task.created_by, task.severity, task.project) == (qa.id, qa.id, "P1", "webapp")
    assert _all_triggers(qa, engineer) == []
    assert result.data["activity"] == {
        "event": "task_created",
        "detail": 'Quinn filed "Login returns 500" to the backlog',
        "extra": {"agent_id": qa.id, "task_id": task.id},
    }
    assert "Filed as P3" not in result.prompt_content


def test_filing_during_a_scheduled_run_leaves_the_run_closable_and_the_next_run_fires() -> None:
    qa = _agent("Quinn")
    schedule = db.create_schedule(
        agent_id=qa.id, title="QA sweep", instructions="Sweep the product.",
        recurrence=RecurrenceRule.model_validate({
            "frequency": "daily", "interval": 1, "times": ["09:00"], "start_date": "2026-10-01",
        }),
        notification_policy="none", enabled=True, created_by=HUMAN_SENDER_ID, agent_can_change=False,
    )
    run = run_now(schedule.id, now=NOW)
    transition_task(run.task.id, "accepted", reason="Test accepted it.", actor="Test")
    transition_task(run.task.id, "active", reason="Test started it.", actor="Test")
    activate_work_activity(qa.id, db.get_task(run.task.id))

    filed = db.get_task(_file(qa, title="Checkout button overlaps footer").data["task_id"])

    assert filed.parent_task_id is None
    # The done handler refuses while a child is open; there is none.
    assert list_open_child_tasks(parent_task_id=run.task.id) == []
    transition_task(run.task.id, "complete", reason="Sweep done.", actor="Test")
    later = run_now(schedule.id, now=NOW + timedelta(days=1))
    assert later.outcome == "fired" and later.task.id != run.task.id
    assert db.get_task(filed.id).status == "pending"


def test_the_same_title_filed_twice_binds_to_the_first() -> None:
    qa = _agent("Quinn")
    first = _file(qa, title="Login returns 500").data["task_id"]

    again = _file(qa, title="Login returns 500", severity="P0")

    assert again.data == {"action": "already_filed", "task_id": first}
    assert f"Already in the backlog as {first} (you filed it). Use taskmsg on it to add detail." in again.prompt_content
    assert "activity" not in again.data
    assert db.get_task(first).severity == "P3"
    assert [task.id for task in db.list_backlog_tasks(status="pending")] == [first]


def test_a_vacationing_agent_has_no_backlog() -> None:
    qa = _agent("Quinn")
    send_home(qa.id)
    qa = db.get_agent(qa.id)

    filed = _cli(qa, "backlog add", {"title": "Anything"})
    listed = _cli(qa, "backlog")

    for result in (filed, listed):
        assert not result.ok
        assert "You are not on a floor, so there is no backlog to file into." in result.prompt_content
    assert db.list_backlog_tasks(status="pending") == []


def test_usage_errors_list_the_forms() -> None:
    qa = _agent("Quinn")
    for command, body in (("backlog add", None), ("backlog sweep", None), ("backlog list", {"x": 1})):
        result = _cli(qa, command, body)
        assert not result.ok
        assert "backlog add" in result.prompt_content and "Usage:" in result.prompt_content


# ─── listing ───


def test_list_shows_only_the_callers_floor_and_searches() -> None:
    finance = create_floor("Finance")
    qa = _agent("Quinn")
    colleague = _agent("Cora")
    away = _agent("Fin", floor_id=finance.id)
    _file(qa, title="Login returns 500", description="Wrong password.")
    _file(colleague, title="Footer overlaps", description="The LOGIN page footer.")
    _file(away, title="Ledger rounding")

    everything = _cli(qa, "backlog")
    searched = _cli(qa, "backlog list login")
    nothing = _cli(qa, "backlog list ledger")

    assert [row["title"] for row in everything.data["backlog"]] == ["Footer overlaps", "Login returns 500"]
    assert "id | severity | title | project | reported by | filed | refs" in everything.prompt_content
    assert "Ledger rounding" not in everything.prompt_content
    # Title or description, any case.
    assert {row["title"] for row in searched.data["backlog"]} == {"Footer overlaps", "Login returns 500"}
    assert nothing.data["backlog"] == [] and 'matches "ledger"' in nothing.prompt_content
    reporters = {row["title"]: row["reported_by"] for row in everything.data["backlog"]}
    assert reporters == {"Footer overlaps": "Cora", "Login returns 500": "Quinn"}


def test_list_is_capped_by_the_setting_and_says_so() -> None:
    qa = _agent("Quinn")
    for index in range(3):
        _file(qa, title=f"Bug {index}")
    db.set_setting("cli_backlog_list_limit", "2", "advanced")
    config.reload()

    result = _cli(qa, "backlog")

    assert len(result.data["backlog"]) == 2 and result.data["total"] == 3
    assert "showing 2 of 3 — add search words to narrow" in result.prompt_content


def test_a_floorless_operator_backlog_item_is_on_no_agents_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    qa = _agent("Quinn")
    response = client.post("/api/tasks", headers=_headers(), json={"title": "Operator idea"})
    assert response.status_code == 201, response.text

    assert _cli(qa, "backlog").data["backlog"] == []


# ─── ownership and floors ───


def test_deleting_the_reporter_keeps_the_item_open() -> None:
    qa = _agent("Quinn")
    task_id = _file(qa, title="Login returns 500").data["task_id"]

    agent_repository.delete(qa.id)

    task = db.get_task(task_id)
    assert task.status == "pending"
    assert task.requester_id is None


def test_assigning_hands_ownership_to_the_assignee_who_can_then_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    qa = _agent("Quinn")
    engineer = _agent("Eve")
    helper = _agent("Hal")
    task_id = _file(qa, title="Login returns 500").data["task_id"]

    response = client.patch(f"/api/tasks/{task_id}", headers=_headers(), json={"assigned_to": engineer.id})

    assert response.status_code == 200, response.text
    task = db.get_task(task_id)
    assert (task.assigned_to, task.owner_id) == (engineer.id, engineer.id)
    transition_task(task_id, "accepted", reason="Test accepted it.", actor="Test")
    transition_task(task_id, "active", reason="Test started it.", actor="Test")
    activate_work_activity(engineer.id, db.get_task(task_id))

    result = asyncio.run(_handle_delegate_task(
        engineer, db.get_agent_state(engineer.id),
        {"agentId": helper.id, "taskTitle": "Write the regression test", "taskDescription": "Cover it."},
    ))
    assert result["event"] != "world_feedback", result
    children = list_open_child_tasks(parent_task_id=task_id)
    assert [(child.assigned_to, child.owner_id) for child in children] == [(helper.id, engineer.id)]


def test_a_filed_item_cannot_be_assigned_across_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    finance = create_floor("Finance")
    qa = _agent("Quinn")
    away = _agent("Fin", floor_id=finance.id)
    task_id = _file(qa, title="Login returns 500").data["task_id"]

    response = client.patch(f"/api/tasks/{task_id}", headers=_headers(), json={"assigned_to": away.id})

    assert response.status_code == 403
    assert db.get_task(task_id).assigned_to is None
    assert db.list_agent_triggers(away.id) == []


@pytest.mark.asyncio
async def test_the_watchdog_never_pings_a_backlog_item() -> None:
    qa = _agent("Quinn")
    task_id = _file(qa, title="Login returns 500").data["task_id"]
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    db.update_task(task_id, last_activity=long_ago, last_progress_at=long_ago, last_heartbeat_at=long_ago)

    await TaskWatchdog()._check_tasks()

    assert db.get_task(task_id).watchdog_pinged_at is None
    assert db.list_agent_triggers(qa.id) == []


# ─── severity ───


def test_an_omitted_severity_files_as_p3_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    qa = _agent("Quinn")
    result = _file(qa, title="Typo on the pricing page")

    assert db.get_task(result.data["task_id"]).severity == "P3"
    assert "Filed as P3 (the default). If it is more serious, say so with taskmsg." in result.prompt_content
    listed = _client(monkeypatch).get("/api/tasks", headers=_headers()).json()
    assert [row["severity"] for row in listed if row["id"] == result.data["task_id"]] == ["P3"]


def test_an_invalid_severity_is_refused_with_the_allowed_values() -> None:
    qa = _agent("Quinn")
    result = _cli(qa, "backlog add", {"title": "Bad", "severity": "P5"})

    assert not result.ok
    assert "severity:" in result.prompt_content
    assert "Severity must be P0 (critical), P1 (high), P2 (medium) or P3 (low)." in result.prompt_content
    assert db.list_backlog_tasks(status="pending") == []


def test_a_severity_edit_changes_it_without_waking_anyone(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    qa = _agent("Quinn")
    engineer = _agent("Eve")
    task_id = _file(qa, title="Login returns 500").data["task_id"]
    assigned = client.patch(f"/api/tasks/{task_id}", headers=_headers(), json={"assigned_to": engineer.id})
    assert assigned.status_code == 200, assigned.text
    db.delete_queued_triggers_for_task(task_id)

    response = client.patch(f"/api/tasks/{task_id}", headers=_headers(), json={"severity": "P0"})

    assert response.status_code == 200, response.text
    assert response.json()["severity"] == "P0"
    assert db.get_task(task_id).severity == "P0"
    assert [row for row in db.list_agent_triggers(engineer.id) if row["status"] == "queued"] == []
    events = db.list_task_events(task_id, limit=50)
    assert any(item.content == "The boss edited the task: severity." for item in events)


def test_a_null_severity_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    qa = _agent("Quinn")
    task_id = _file(qa, title="Login returns 500", severity="P2").data["task_id"]

    response = client.patch(f"/api/tasks/{task_id}", headers=_headers(), json={"severity": None})

    # FastAPI answers a body that fails the request model with 422, as it
    # does for a blank title (test_operator_task_actions.py).
    assert response.status_code == 422
    assert db.get_task(task_id).severity == "P2"


# ─── references ───


def test_references_keep_projects_files_and_report_the_rest() -> None:
    qa = _agent("Quinn")
    _write_project_file(qa, REFERENCE)
    outside = Path(os.environ["BOSSMOD_COMPANY_ROOT"]).parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = projects_root_for_storage_key(qa.storage_key) / "webapp" / "escape.md"
    link.symlink_to(outside)

    result = _file(qa, title="Login returns 500", references=[
        {"path": REFERENCE, "description": "Steps to reproduce"},
        {"path": "/projects/webapp/qa/../qa/issue-login-500.md"},
        {"path": "/me/notes/private.md"},
        {"path": "/projects/webapp/missing.md"},
        {"path": "/projects/webapp/escape.md"},
    ])

    task = db.get_task(result.data["task_id"])
    assert [ref.model_dump() for ref in task.references] == [{"path": REFERENCE, "description": "Steps to reproduce"}]
    reasons = {item["path"]: item["reason"] for item in result.data["rejected_references"]}
    assert reasons["/me/notes/private.md"] == "only /projects files can be shared; /me is private to you"
    assert reasons["/projects/webapp/missing.md"] == "no such file"
    assert reasons["/projects/webapp/escape.md"] == "Path escapes the allowed artifact directory"
    assert len(reasons) == 3  # the duplicate path is dropped, not rejected
    assert "Not linked: /me/notes/private.md — only /projects files can be shared" in result.prompt_content
    assert _cli(qa, "backlog").data["backlog"][0]["references"] == 1


def test_references_and_severity_reach_the_engineers_prompt() -> None:
    qa = _agent("Quinn")
    engineer = _agent("Eve")
    _write_project_file(qa, REFERENCE)
    task_id = _file(qa, title="Login returns 500", severity="P1", description="Wrong password.", references=[
        {"path": REFERENCE, "description": "Steps to reproduce"},
    ]).data["task_id"]
    db.update_task(task_id, assigned_to=engineer.id, owner_id=engineer.id)
    task = db.get_task(task_id)

    payload = build_task_assigned_trigger(task)["payload"]
    assert payload["task_severity"] == "P1"
    assert payload["task_references"] == f"- {REFERENCE} — Steps to reproduce"
    prompts = {"runtime_block_trigger_event": load_default_prompt("runtime_block_trigger_event")}
    for contract_kind in ("decision", "execution"):
        text = _format_trigger({"type": "task_assigned", **payload}, contract_kind, prompts)
        assert "Severity: P1" in text
        assert f"Reference documents (read these before starting):\n- {REFERENCE} — Steps to reproduce" in text

    transition_task(task_id, "accepted", reason="Test accepted it.", actor="Test")
    activate_work_activity(engineer.id, db.get_task(task_id))
    current = _format_task(_get_current_task(engineer.id))
    assert "severity: P1" in current
    assert f"references (read before starting):\n- {REFERENCE} — Steps to reproduce" in current


def test_a_task_without_references_has_none_and_clearing_removes_them() -> None:
    qa = _agent("Quinn")
    _write_project_file(qa, REFERENCE)
    task_id = _file(qa, title="Login returns 500", references=[{"path": REFERENCE}]).data["task_id"]

    assert db.update_task(task_id, references=[]).references == []
    assert db.get_task(task_id).references == []
    plain = db.create_task(title="Plain", created_by=HUMAN_SENDER_ID)
    assert (plain.references, plain.severity) == ([], "P3")


# ─── migration ───


_OLD_TASKS_TABLE = """
CREATE TABLE tasks (
    id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    title          VARCHAR NOT NULL,
    description    TEXT,
    project        VARCHAR,
    assigned_to    VARCHAR,
    requester_id   VARCHAR,
    owner_id       VARCHAR,
    created_by     VARCHAR,
    status         VARCHAR DEFAULT 'pending'
                       CHECK (status IN ({statuses})),
    parent_task_id VARCHAR,
    cost_ceiling   DECIMAL,
    completion_summary TEXT,
    status_note    TEXT,
    watchdog_pinged_at TIMESTAMP,
    last_progress_at TIMESTAMP DEFAULT current_timestamp,
    last_heartbeat_at TIMESTAMP DEFAULT current_timestamp,
    last_activity  TIMESTAMP DEFAULT current_timestamp,
    created_at     TIMESTAMP DEFAULT current_timestamp
)
"""
_CURRENT_STATUSES = (
    "'pending', 'accepted', 'active', 'waiting', 'blocked', 'complete', "
    "'stalled', 'abandoned', 'delegated', 'declined', 'cancelled'"
)
_PRE_WAITING_STATUSES = "'pending', 'accepted', 'active', 'blocked', 'complete', 'stalled', 'abandoned'"


@pytest.mark.parametrize("statuses", [_CURRENT_STATUSES, _PRE_WAITING_STATUSES], ids=["current", "pre-waiting"])
def test_the_migration_gives_existing_tasks_p3_and_creates_task_references(statuses: str) -> None:
    """An existing tasks table without severity gains it as P3; task_references appears.

    The pre-waiting table also goes through the status rebuild first, which
    drops the newer columns; the add-column migrations then bring them back.
    """
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    con = get_connection()
    con.execute(_OLD_TASKS_TABLE.format(statuses=statuses))
    con.execute("INSERT INTO tasks (id, title, status) VALUES ('old-1', 'From before', 'pending')")
    db.close_connection()

    db.init_db()

    con = get_connection()
    columns = {row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
    assert {"severity", "closed_at", "schedule_id"} <= columns
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert "task_references" in tables
    old = db.get_task("old-1")
    assert (old.severity, old.references) == ("P3", [])
    with pytest.raises(Exception, match="CHECK constraint failed"):
        con.execute("UPDATE tasks SET severity = 'P9' WHERE id = 'old-1'")
