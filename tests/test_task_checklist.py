"""Task checklists: the assignee plans its current task in parts and checks them off.

Covers the checklist plan (strategy-docs/agentTaskChecklist_feature_10202026_1792507153795.md):
the item model, the ``task_checklists`` side table read through every task
query, the domain rules (append only, resolve by number), the ``checklist``
CLI command and its declared side effects, liveness, the agent's ``done``
check (the operator's completion is not gated), and what the agent sees
under Current Task and in the task CLI views. Everything runs against the
isolated test database and roots (conftest.py). No LLM.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action
from core.agent_loop.liveness import command_mutates_project
from core.agent_loop.turn_context import _get_current_task
from core.agent_loop.work_binding import bind_turn
from core.bm_cli.checklist_commands import parse_checklist_body
from core.bm_cli.command_registry import CHECKLIST_FORMS
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.types import BossModCliResult
from core.llm.context_builder import _format_task
from core.models import Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskChecklistItem
from core.tasking import (
    ChecklistError,
    add_checklist_items,
    checklist_progress,
    format_checklist_for_context,
    open_checklist_items,
    resolve_checklist_item,
)
from core.tasking.operator_actions import complete_task_as_operator
from tests._connections import model_connection

_DETACHED = {"type": "extension_event", "source_channel": "system", "task_id": None}
_AUDIT = "Audit core/tasking\nAudit db/tasks.py\nWrite the findings summary"


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


def _agent(name: str = "Charles") -> Agent:
    return db.create_agent(name, role="Build Engineer", desk_x=1, desk_y=1, connection_id=model_connection("test/mock"))


def _task(agent: Agent, title: str = "Audit the codebase", **fields: Any) -> Task:
    return db.create_task(
        title=title,
        description="Audit everything the last change touched.",
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        created_by=HUMAN_SENDER_ID,
        **fields,
    )


def _working(name: str = "Charles") -> tuple[Agent, Task]:
    agent = _agent(name)
    task = _task(agent)
    assert activity_runtime.activate_work_activity(agent.id, task, task_status="active") is not None
    return agent, task


def _cli(agent: Agent, command: str, body: str | None = None) -> BossModCliResult:
    return execute_bm_cli(agent, db.get_agent_state(agent.id), command, body)


def _items(*specs: tuple[str, str, str | None]) -> list[TaskChecklistItem]:
    return [TaskChecklistItem(text=text, status=status, note=note) for text, status, note in specs]


def _done_evidence(agent_id: str) -> None:
    db.create_bm_cli_event(
        agent_id=agent_id,
        command="cat /projects/audit.md",
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


_DONE_ACTION = {
    "action": "complete",
    "summary": "Audit finished.",
    "doneClaim": {"type": "proof", "ev": "audit notes in /projects/audit.md"},
}


# ─── model ───


def test_an_item_needs_text() -> None:
    with pytest.raises(ValidationError):
        TaskChecklistItem(text="   ")
    item = TaskChecklistItem(text="  Audit db  ", note="   ")
    assert (item.text, item.status, item.note) == ("Audit db", "open", None)


def test_a_skipped_item_needs_a_reason() -> None:
    with pytest.raises(ValidationError):
        TaskChecklistItem(text="Audit the plugins", status="skipped")
    with pytest.raises(ValidationError):
        TaskChecklistItem(text="Audit the plugins", status="skipped", note="  ")
    assert TaskChecklistItem(text="Audit the plugins", status="skipped", note=" Dropped. ").note == "Dropped."


def test_an_item_refuses_unknown_keys_and_statuses() -> None:
    with pytest.raises(ValidationError):
        TaskChecklistItem.model_validate({"text": "Audit db", "owner": "Brad"})
    with pytest.raises(ValidationError):
        TaskChecklistItem.model_validate({"text": "Audit db", "status": "maybe"})


# ─── db ───


def test_a_task_without_a_row_has_no_checklist() -> None:
    task = _task(_agent())
    assert task.checklist == []
    assert db.get_task(task.id).checklist == []


def test_update_task_round_trips_the_checklist_through_every_read() -> None:
    agent = _agent()
    task = _task(agent)
    other = _task(agent, title="Something else")
    items = _items(("Audit core", "done", "2 issues"), ("Audit db", "open", None))
    updated = db.update_task(task.id, checklist=items)
    assert updated.checklist == items
    assert db.get_task(task.id).checklist == items
    assert db.get_tasks_by_ids([task.id, other.id])[task.id].checklist == items
    listed = {row.id: row for row in db.list_tasks(assigned_to=agent.id)}
    assert listed[task.id].checklist == items
    assert listed[other.id].checklist == []
    assert [row.checklist for row in db.list_tasks_by_statuses(["pending"]) if row.id == task.id] == [items]
    recent = {row.id: row for row in db.list_recent_tasks(assigned_to=agent.id, limit=5)}
    assert recent[task.id].checklist == items
    # The backlog query (the sixth task read) joins the table too.
    backlog = db.create_task(title="Loose end", requester_id=HUMAN_SENDER_ID, created_by=HUMAN_SENDER_ID)
    db.update_task(backlog.id, checklist=_items(("Triage it", "open", None)))
    assert [row.checklist for row in db.list_backlog_tasks(status="pending") if row.id == backlog.id] == [
        _items(("Triage it", "open", None)),
    ]

    replaced = _items(("Audit core", "done", "2 issues"), ("Audit db", "skipped", "Out of scope"))
    assert db.update_task(task.id, checklist=replaced).checklist == replaced


def test_an_empty_checklist_is_refused_before_any_write() -> None:
    task = _task(_agent())
    db.update_task(task.id, checklist=_items(("Audit core", "open", None)))
    with pytest.raises(ValueError, match="never removed"):
        db.update_task(task.id, checklist=[], status_note="should not land")
    stored = db.get_task(task.id)
    assert len(stored.checklist) == 1
    assert stored.status_note is None


# ─── domain ───


def test_add_appends_and_numbers_stay_stable() -> None:
    task = _task(_agent())
    first = add_checklist_items(task.id, ["Audit core", "Audit db"])
    assert [item.text for item in first] == ["Audit core", "Audit db"]
    resolve_checklist_item(task.id, 1, status="done", note="2 issues")
    after = add_checklist_items(task.id, ["Write the summary"])
    assert [(item.text, item.status) for item in after] == [
        ("Audit core", "done"), ("Audit db", "open"), ("Write the summary", "open"),
    ]
    assert db.get_task(task.id).checklist == after
    with pytest.raises(ChecklistError):
        add_checklist_items(task.id, [])
    with pytest.raises(ChecklistError):
        add_checklist_items(task.id, ["   "])
    with pytest.raises(ChecklistError):
        add_checklist_items("no-such-task", ["Audit core"])


def test_resolving_marks_items_and_the_latest_answer_wins() -> None:
    task = _task(_agent())
    add_checklist_items(task.id, ["Audit core", "Audit plugins", "Write the summary"])
    item, items = resolve_checklist_item(task.id, 2, status="skipped", note="The boss dropped the plugins.")
    assert (item.status, item.note) == ("skipped", "The boss dropped the plugins.")
    assert items[1] == item
    item, _ = resolve_checklist_item(task.id, 2, status="done", note="Audited after all: clean.")
    assert (item.text, item.status, item.note) == ("Audit plugins", "done", "Audited after all: clean.")
    item, _ = resolve_checklist_item(task.id, 1, status="done", note=None)
    assert (item.status, item.note) == ("done", None)
    stored = db.get_task(task.id)
    assert [i.status for i in stored.checklist] == ["done", "done", "open"]
    assert open_checklist_items(stored) == [(3, stored.checklist[2])]
    assert checklist_progress(stored.checklist) == (2, 3)


def test_a_bad_number_names_how_many_items_there_are() -> None:
    task = _task(_agent())
    with pytest.raises(ChecklistError, match="empty"):
        resolve_checklist_item(task.id, 1, status="done", note=None)
    add_checklist_items(task.id, ["Audit core", "Audit db", "Write the summary"])
    with pytest.raises(ChecklistError, match="There is no item 4: your checklist has 3 items."):
        resolve_checklist_item(task.id, 4, status="done", note=None)
    with pytest.raises(ChecklistError, match="no item 0"):
        resolve_checklist_item(task.id, 0, status="done", note=None)
    with pytest.raises(ChecklistError, match="checklist skip 2 needs a body"):
        resolve_checklist_item(task.id, 2, status="skipped", note=None)
    assert [item.status for item in db.get_task(task.id).checklist] == ["open", "open", "open"]


def test_the_formatter_lines_are_exact() -> None:
    items = _items(
        ("Audit core/tasking", "done", "3 issues, in /projects/app/audit.md"),
        ("Audit db/tasks.py", "open", None),
        ("Audit the plugins", "skipped", "the boss dropped them"),
    )
    expected = [
        "checklist (2 of 3 checked off):",
        "1. [done] Audit core/tasking — 3 issues, in /projects/app/audit.md",
        "2. [open] Audit db/tasks.py",
        "3. [skipped] Audit the plugins — the boss dropped them",
    ]
    assert format_checklist_for_context(items) == expected
    assert format_checklist_for_context([item.model_dump() for item in items]) == expected
    assert format_checklist_for_context([]) == []


# ─── the checklist command ───


def test_every_form_works_through_the_cli() -> None:
    agent, task = _working()

    empty = _cli(agent, "checklist")
    assert empty.ok and empty.kind == "checklist"
    assert "CHECKLIST — Audit the codebase:" in empty.prompt_content
    assert "empty — add items with checklist add" in empty.prompt_content
    assert empty.data == {"task_id": task.id, "checklist": []}

    added = _cli(agent, "checklist add", _AUDIT)
    assert added.ok and added.kind == "checklist"
    assert "checklist (0 of 3 checked off):" in added.prompt_content
    assert "3. [open] Write the findings summary" in added.prompt_content
    assert [item["text"] for item in added.data["checklist"]] == _AUDIT.split("\n")

    done = _cli(agent, "checklist done 1", "3 issues, written to /projects/app/audit.md")
    assert done.ok and done.kind == "checklist_item_resolved"
    assert "1. [done] Audit core/tasking — 3 issues, written to /projects/app/audit.md" in done.prompt_content

    bare = _cli(agent, "checklist done 2")
    assert bare.ok and bare.data["checklist"][1] == {"text": "Audit db/tasks.py", "status": "done", "note": None}

    skipped = _cli(agent, "checklist skip 3", "The boss wants the raw notes only.")
    assert skipped.ok and skipped.kind == "checklist_item_resolved"
    assert "3. [skipped] Write the findings summary — The boss wants the raw notes only." in skipped.prompt_content

    shown = _cli(agent, "checklist")
    assert "checklist (3 of 3 checked off):" in shown.prompt_content
    assert [item.status for item in db.get_task(task.id).checklist] == ["done", "done", "skipped"]


def test_with_no_current_task_the_command_errors() -> None:
    idle = _agent("Idle")
    result = _cli(idle, "checklist add", "Audit core")
    assert not result.ok
    assert "You have no current task. A checklist belongs to the task you are working on." in result.prompt_content

    agent, task = _working()
    with bind_turn(agent.id, _DETACHED):
        detached = _cli(agent, "checklist add", "Audit core")
    assert not detached.ok
    assert "You have no current task." in detached.prompt_content
    assert db.get_task(task.id).checklist == []


@pytest.mark.parametrize(
    ("command", "body", "message"),
    [
        ("checklist add", None, "checklist add needs a body: one item per line."),
        ("checklist add", " \n \n", "checklist add needs a body: one item per line."),
        ("checklist skip 1", None, "checklist skip <n> needs a body: why the item is not needed."),
        ("checklist skip 1", "   ", "checklist skip <n> needs a body: why the item is not needed."),
        ("checklist done two", None, "Name the item by its number, e.g. checklist done 2."),
        ("checklist done 0", None, "Name the item by its number, e.g. checklist done 2."),
        ("checklist done +1", None, "Name the item by its number, e.g. checklist done 2."),
        ("checklist done", None, "Name the item by its number, e.g. checklist done 2."),
        ("checklist skip x", "why", "Name the item by its number, e.g. checklist done 2."),
        ("checklist done 9", None, "There is no item 9: your checklist has 1 item."),
    ],
)
def test_malformed_calls_error_in_the_agents_words(command: str, body: str | None, message: str) -> None:
    agent, task = _working()
    add_checklist_items(task.id, ["Audit core"])
    result = _cli(agent, command, body)
    assert not result.ok
    assert result.data == {"error": message}
    assert [item.status for item in db.get_task(task.id).checklist] == ["open"]


def test_an_unknown_form_or_a_stray_body_lists_the_forms() -> None:
    agent, _task_row = _working()
    for command, body in (("checklist remove 1", None), ("checklist", "Audit core"), ("checklist add now", "x")):
        result = _cli(agent, command, body)
        assert not result.ok, command
        for form in CHECKLIST_FORMS:
            assert form in result.prompt_content, (command, form)


def test_list_markers_are_stripped_once() -> None:
    assert parse_checklist_body("- Audit core\n* Audit db\n• Audit ui\n1. Audit api\n2) Audit cli\n\n  ") == [
        "Audit core", "Audit db", "Audit ui", "Audit api", "Audit cli",
    ]
    assert parse_checklist_body("1. 1. Audit core\n3.5 hours of logs\n-no space\n2024 budget") == [
        "1. Audit core", "3.5 hours of logs", "-no space", "2024 budget",
    ]
    agent, task = _working()
    _cli(agent, "checklist add", "1. Audit core\n2. Audit db")
    assert [item.text for item in db.get_task(task.id).checklist] == ["Audit core", "Audit db"]


def test_changes_and_check_offs_declare_task_updated() -> None:
    agent, task = _working()
    view = _cli(agent, "checklist")
    assert "activity" not in view.data

    added = _cli(agent, "checklist add", "Audit core\nAudit db")
    assert added.data["activity"] == {
        "event": "task_updated",
        "detail": 'Charles updated the checklist on "Audit the codebase"',
        "extra": {"agent_id": agent.id, "task_id": task.id},
    }

    done = _cli(agent, "checklist done 1", "clean")
    assert done.data["activity"]["event"] == "task_updated"
    skipped = _cli(agent, "checklist skip 2", "not needed")
    assert skipped.data["activity"]["event"] == "task_updated"


# ─── liveness ───


def test_check_offs_are_progress_and_the_view_is_not() -> None:
    assert command_mutates_project("checklist done 1", kind="checklist_item_resolved") is True
    assert command_mutates_project("checklist skip 1", kind="checklist_item_resolved") is True
    assert command_mutates_project("checklist", kind="checklist") is False
    assert command_mutates_project("checklist add", kind="checklist") is False


# ─── the done check ───


@pytest.mark.asyncio
async def test_done_is_refused_while_items_are_open() -> None:
    agent, task = _working()
    add_checklist_items(task.id, ["Audit core", "Audit db", "Audit ui", "Write the summary"])
    resolve_checklist_item(task.id, 1, status="done", note=None)
    resolve_checklist_item(task.id, 3, status="skipped", note="No UI changed.")
    _done_evidence(agent.id)
    result = await execute_action(dict(_DONE_ACTION), agent, db.get_agent_state(agent.id))
    assert result["event"] == "world_feedback"
    assert result["task_id"] == task.id
    assert result["detail"] == (
        'Your checklist still has open items: 2. "Audit db", 4. "Write the summary". Mark each one with '
        "checklist done <n>, or checklist skip <n> with the reason as the body, then finish."
    )
    assert db.get_task(task.id).status == "active"


@pytest.mark.asyncio
async def test_done_completes_once_every_item_is_done_or_skipped() -> None:
    agent, task = _working()
    add_checklist_items(task.id, ["Audit core", "Audit plugins"])
    resolve_checklist_item(task.id, 1, status="done", note="2 issues")
    resolve_checklist_item(task.id, 2, status="skipped", note="Dropped by the boss.")
    _done_evidence(agent.id)
    result = await execute_action(dict(_DONE_ACTION), agent, db.get_agent_state(agent.id))
    assert result["event"] == "status_changed"
    assert db.get_task(task.id).status == "complete"


def test_the_operator_completes_a_task_with_open_items() -> None:
    _agent_row, task = _working()
    add_checklist_items(task.id, ["Audit core", "Audit db"])
    outcome = complete_task_as_operator(task.id, summary="Good enough for now.")
    assert outcome.task.status == "complete"
    # The record stays truthful about what was left open.
    assert [item.status for item in outcome.task.checklist] == ["open", "open"]


# ─── what the agent sees ───


def test_current_task_carries_the_checklist_into_the_prompt_and_the_cli() -> None:
    agent, task = _working()
    add_checklist_items(task.id, ["Audit core", "Audit db"])
    resolve_checklist_item(task.id, 1, status="done", note="2 issues")
    lines = ["checklist (1 of 2 checked off):", "1. [done] Audit core — 2 issues", "2. [open] Audit db"]

    current = _get_current_task(agent.id)
    assert current["checklist"] == [item.model_dump() for item in db.get_task(task.id).checklist]
    block = _format_task(current)
    assert "\n".join(lines) in block
    assert block.index("checklist (1 of 2") < block.index("done_claim:")

    for command in ("current-task", "my-board", f"task {task.id}"):
        result = _cli(agent, command)
        assert result.ok, command
        assert "\n".join(lines) in result.prompt_content, command


def test_a_task_without_items_adds_no_checklist_lines() -> None:
    agent, _task_row = _working()
    assert "checklist (" not in _format_task(_get_current_task(agent.id))
    assert "checklist (" not in _cli(agent, "current-task").prompt_content


def test_an_owner_sees_a_delegated_childs_checklist_with_task() -> None:
    owner = _agent("Olivia")
    worker = _agent("Wes")
    parent = _task(owner, title="Coordinate the audit", owner_id=owner.id)
    child = db.create_task(
        title="Audit the db layer",
        description="Your part of the audit.",
        assigned_to=worker.id,
        requester_id=owner.id,
        owner_id=owner.id,
        created_by=owner.id,
        parent_task_id=parent.id,
    )
    activity_runtime.activate_work_activity(worker.id, child, task_status="active")
    assert _cli(worker, "checklist add", "Audit tasks.py\nAudit settings.py").ok
    assert _cli(worker, "checklist done 1", "clean").ok

    seen = _cli(owner, f"task {child.id}")
    assert seen.ok
    assert "checklist (1 of 2 checked off):" in seen.prompt_content
    assert "1. [done] Audit tasks.py — clean" in seen.prompt_content
    assert seen.data["task"]["checklist"][1] == {"text": "Audit settings.py", "status": "open", "note": None}
    # The owner's own turn has no bound task, so it cannot write the child's checklist.
    assert not _cli(owner, "checklist done 2", "done").ok
