"""Task hold and dependency chaining: "don't start until X is done".

A task created with an unfinished prerequisite is born ``held`` and wakes
nobody; when its prerequisites complete, ``transition_task`` releases it and
queues the normal ``task_assigned`` wake; when one closes without completing,
it is blocked and the agent that set the dependency is told. Drives the real
db, tasking, action, decision and route code against the isolated test
database (conftest.py). No LLM.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import db.tasks as db_tasks
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.decision_contract import (
    _task_is_open_for,
    _trigger_task_is_open_for,
    parse_decision,
)
from core.agent_loop.decision_parse_fail import surface_decision_parse_failure
from core.agent_loop.decision_runtime import apply_decision
from core.default_prompts import load_default_prompt
from core.llm.context_builder import _task_board_context
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskReference
from core.models.work_contract import DeliverableSpec, WorkContract
from core.prompting.runtime_prompt_lint import lint_runtime_prompts
from core.runtime import runtime_services
from core.tasking import dependencies, operator_actions
from core.tasking.board import build_project_summary, build_task_board, next_board_task
from core.tasking.dependencies import (
    DEAD_PREREQUISITE_STATUSES,
    MET_PREREQUISITE_STATUSES,
    PrerequisiteUnavailable,
    has_unfinished_prerequisites,
    release_task,
    resolve_prerequisites,
    settle_dependents,
)
from core.tasking.operator_actions import hold_task_as_operator, release_task_as_operator
from core.tasking.service import create_or_bind_subtask, create_or_bind_task
from core.tasking.transitions import IllegalTaskTransition, TaskStatusChanged, transition_task
from db.crud import build_conditional_update, execute, query_one
from db.floors import create_floor


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


# ─── Helpers ───


def _agent(name: str, x: int, *, floor_id: str | None = None):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1, floor_id=floor_id)


def _team():
    """A PM (Alex) and three teammates, all on the Lobby floor."""
    return _agent("Alex", 1), _agent("Taylor", 2), _agent("Sam", 3), _agent("Jo", 4)


def _requested(
    pm,
    assignee,
    title: str,
    *,
    prerequisite_ids: tuple[str, ...] | list[str] = (),
    parent=None,
    work_contract: WorkContract | None = None,
    references: list[TaskReference] | None = None,
    owner_id: str | None = None,
):
    """A task ``pm`` asked ``assignee`` for, created the way an assign creates it.

    ``owner_id`` omitted is the default owner (a top-level assign owns
    itself); a plan child passes the PM.
    """
    kwargs: dict[str, Any] = dict(
        title=title,
        description=f"{title}.",
        project=None,
        assigned_to=assignee.id,
        requester_id=pm.id,
        owner_id=owner_id,
        created_by=pm.id,
        work_contract=work_contract,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name=pm.name,
        audit_author_type="agent",
        audit_author_agent_id=pm.id,
        prerequisite_ids=prerequisite_ids,
    )
    if parent is not None:
        result = create_or_bind_subtask(parent_task=parent, **kwargs)
    else:
        result = create_or_bind_task(parent_task_id=None, references=references, **kwargs)
    assert result.outcome == "create_new_task", result.outcome
    assert result.task is not None
    return result.task


def _finish(task_id: str, status: str, *, summary: str | None = None):
    """Close a pending task with ``status`` along a legal path."""
    if status == "complete":
        transition_task(task_id, "accepted", reason="accepted", actor="pytest")
        transition_task(task_id, "active", reason="started", actor="pytest")
        return transition_task(task_id, "complete", reason="done", actor="pytest", completion_summary=summary)
    return transition_task(task_id, status, reason=f"closed as {status}", actor="pytest")


def _status(task_id: str) -> str:
    task = db.get_task(task_id)
    assert task is not None
    return task.status


def _triggers(agent_id: str, *, trigger_type: str, task_id: str | None = None) -> list[dict[str, Any]]:
    return [
        row
        for row in db.list_agent_triggers(agent_id, limit=200)
        if row["trigger_type"] == trigger_type and (task_id is None or row["task_id"] == task_id)
    ]


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row["payload"]
    return raw if isinstance(raw, dict) else json.loads(raw)


def _edge_count(task_id: str | None = None) -> int:
    if task_id is None:
        row = query_one("SELECT COUNT(*) AS cnt FROM task_dependencies")
    else:
        row = query_one("SELECT COUNT(*) AS cnt FROM task_dependencies WHERE task_id = $1", [task_id])
    assert row is not None
    return int(row["cnt"])


def _tasks_titled(title: str) -> list[Any]:
    return [task for task in db.list_tasks() if task.title == title]


def _events(task_id: str) -> list[str]:
    return [event.content for event in db.list_task_events(task_id, limit=200)]


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


async def _assign(pm, target, title: str, *, after: list[str] | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {
        "action": "delegateTask",
        "agentId": target.id,
        "taskTitle": title,
        "taskDescription": f"{title}.",
    }
    if after is not None:
        action["after"] = after
    state = db.get_agent_state(pm.id)
    assert state is not None
    return await execute_action(action, pm, state)


def _plan_decision(parent_title: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "decision": "accept",
        "intentKind": "work_request",
        "commitmentKind": "work",
        "taskTitle": parent_title,
        "reply": "On it.",
        "executionPlan": {"mode": "delegate", "delegations": children},
    }


def _accept_plan(pm, parent, children: list[dict[str, Any]]) -> dict[str, Any]:
    state = db.get_agent_state(pm.id)
    assert state is not None
    return apply_decision(
        _plan_decision(parent.title, children),
        pm,
        state,
        {"type": "task_assigned", "task_id": parent.id, "content": parent.title, "from_name": "Human"},
    )


def _unreleased(pm, taylor, sam):
    """Sam's two kinds of unreleased work, both owned by the PM: one held, one blocked on a failed prerequisite."""
    research = _requested(pm, taylor, "Research")
    review = _requested(pm, taylor, "Review")
    held = _requested(pm, sam, "Draft", prerequisite_ids=[research.id], owner_id=pm.id)
    blocked = _requested(pm, sam, "Edit", prerequisite_ids=[review.id], owner_id=pm.id)
    _finish(review.id, "cancelled")
    held, blocked = db.get_task(held.id), db.get_task(blocked.id)
    assert (held.status, blocked.status) == ("held", "blocked")
    return held, blocked


# ─── Release ───


def test_a_task_waiting_on_unfinished_work_is_born_held_otherwise_pending() -> None:
    pm, taylor, sam, jo = _team()
    research = _requested(pm, taylor, "Research")
    held = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    plain = _requested(pm, jo, "Outline")
    done = _requested(pm, taylor, "Interviews")
    _finish(done.id, "complete")
    met = _requested(pm, jo, "Review", prerequisite_ids=[done.id])

    assert held.status == "held"
    assert held.prerequisites == [research.id]
    assert plain.status == "pending" and plain.prerequisites == []
    # An edge is kept for a met prerequisite too, so the task can be told what came first.
    assert met.status == "pending" and met.prerequisites == [done.id]
    assert any('On hold until "Research" finishes.' in item for item in _events(held.id))


def test_a_chain_releases_each_task_only_after_the_one_before_it() -> None:
    pm, taylor, sam, jo = _team()
    first = _requested(pm, taylor, "Research")
    second = _requested(pm, sam, "Draft", prerequisite_ids=[first.id])
    third = _requested(pm, jo, "Review", prerequisite_ids=[second.id])

    _finish(first.id, "complete")
    assert (_status(second.id), _status(third.id)) == ("pending", "held")

    _finish(second.id, "complete")
    assert _status(third.id) == "pending"


def test_a_diamond_releases_once_both_prerequisites_are_complete() -> None:
    pm, taylor, sam, jo = _team()
    left = _requested(pm, taylor, "Research")
    right = _requested(pm, sam, "Outline")
    joined = _requested(pm, jo, "Draft", prerequisite_ids=[left.id, right.id])

    _finish(left.id, "complete")
    assert _status(joined.id) == "held"
    assert _triggers(jo.id, trigger_type="task_assigned", task_id=joined.id) == []

    _finish(right.id, "complete")
    assert _status(joined.id) == "pending"
    assert len(_triggers(jo.id, trigger_type="task_assigned", task_id=joined.id)) == 1


def test_a_held_task_queues_no_wake_and_its_release_queues_exactly_one() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research", work_contract=WorkContract(deliverables=[
        DeliverableSpec(type="file", path="/projects/shared/notes.md"),
    ]))
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    assert _triggers(sam.id, trigger_type="task_assigned") == []

    _finish(research.id, "complete", summary="Notes saved")

    wakes = _triggers(sam.id, trigger_type="task_assigned", task_id=draft.id)
    assert len(wakes) == 1
    # The released task learns what came before it and where the output is.
    assert _payload(wakes[0])["task_prerequisites"] == (
        "- Research (Taylor): Notes saved; files: /projects/shared/notes.md"
    )
    assert any("held → pending: All prerequisites complete" in item for item in _events(draft.id))


def test_two_settles_for_the_same_dependent_release_it_once(monkeypatch: pytest.MonkeyPatch) -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    finished = _finish(research.id, "complete")
    assert _status(draft.id) == "pending"

    # The other process's settle read the dependent list before this one released it.
    monkeypatch.setattr(db, "list_held_dependents", lambda _prerequisite_id: [draft.id])
    settle_dependents(finished)
    with pytest.raises(TaskStatusChanged):
        release_task(draft)

    releases = [item for item in _events(draft.id) if "held → pending" in item]
    assert len(releases) == 1
    assert len(_triggers(sam.id, trigger_type="task_assigned", task_id=draft.id)) == 1


# ─── Failure ───


@pytest.mark.parametrize("closed_as", sorted(DEAD_PREREQUISITE_STATUSES))
def test_a_prerequisite_closed_without_completing_blocks_the_dependent(closed_as: str) -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])

    _finish(research.id, closed_as)

    stored = db.get_task(draft.id)
    assert stored is not None and stored.status == "blocked"
    sentence = f'Prerequisite "Research" was {closed_as}.'
    assert stored.status_note == sentence
    assert any(event.event_type == "blocker" and event.content == sentence for event in db.list_task_events(draft.id))
    notices = _triggers(pm.id, trigger_type="task_update", task_id=draft.id)
    assert len(notices) == 1
    payload = _payload(notices[0])
    assert payload["attention_kind"] == "blocker"
    assert payload["content"] == f"{sentence} It will not start until the boss resumes or cancels it."
    assert payload["from_name"] == "BossMod"
    assert [row for row in db.list_agent_triggers(sam.id, limit=200) if row["task_id"] == draft.id] == []


def test_a_dependent_whose_parent_is_closed_is_not_settled() -> None:
    pm, taylor, sam, _jo = _team()
    parent = db.create_task(title="Ship the post", assigned_to=pm.id, created_by=HUMAN_SENDER_ID)
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id], parent=parent)
    transition_task(parent.id, "cancelled", reason="dropped", actor="pytest")

    _finish(research.id, "complete")

    assert _status(draft.id) == "held"
    assert _triggers(sam.id, trigger_type="task_assigned", task_id=draft.id) == []


def test_a_prerequisite_on_another_floor_at_release_time_blocks_the_dependent() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    finance = create_floor("Finance")
    db.update_agent(sam.id, floor_id=finance.id)

    _finish(research.id, "complete")

    stored = db.get_task(draft.id)
    assert stored is not None and stored.status == "blocked"
    assert stored.status_note == 'Prerequisite "Research" is on another floor.'
    assert _triggers(sam.id, trigger_type="task_assigned", task_id=draft.id) == []


def test_a_cancelled_prerequisite_with_no_floor_reads_was_cancelled_not_another_floor() -> None:
    pm, _taylor, sam, _jo = _team()
    # No agent on it, so no floor, as a task whose agent was deleted has none.
    budget = db.create_task(title="Budget sign-off", created_by=HUMAN_SENDER_ID)
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[budget.id])
    assert draft.status == "held"

    transition_task(budget.id, "cancelled", reason="dropped", actor="pytest")

    stored = db.get_task(draft.id)
    assert stored is not None and stored.status == "blocked"
    assert stored.status_note == 'Prerequisite "Budget sign-off" was cancelled.'


def test_a_fault_settling_one_dependent_is_logged_and_spares_the_rest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    pm, taylor, sam, jo = _team()
    research = _requested(pm, taylor, "Research")
    broken = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    healthy = _requested(pm, jo, "Outline", prerequisite_ids=[research.id])
    original = dependencies.release_task

    def flaky_release(task):
        if task.id == broken.id:
            raise RuntimeError("wake store unavailable")
        return original(task)

    monkeypatch.setattr(dependencies, "release_task", flaky_release)

    with caplog.at_level(logging.ERROR, logger="core.tasking.dependencies"):
        finished = _finish(research.id, "complete")

    assert finished.status == "complete"
    assert (_status(broken.id), _status(healthy.id)) == ("held", "pending")
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert broken.id in errors[0].getMessage() and research.id in errors[0].getMessage()


# ─── Resolution ───


def test_resolution_matches_plan_key_id_exact_and_partial_title() -> None:
    pm, taylor, sam, _jo = _team()
    whitepaper = _requested(pm, taylor, "Write the Whitepaper!")
    outline = _requested(pm, sam, "Outline the launch post")

    by_key = resolve_prerequisites(agent=pm, references=["  RESEARCH "], sibling_ids={"research": outline.id})
    by_id = resolve_prerequisites(agent=pm, references=[whitepaper.id])
    exact = resolve_prerequisites(agent=pm, references=["write the whitepaper"])
    partial = resolve_prerequisites(agent=pm, references=["launch post"])

    assert [task.id for task in by_key.tasks] == [outline.id] and by_key.errors == ()
    assert [task.id for task in by_id.tasks] == [whitepaper.id]
    assert [task.id for task in exact.tasks] == [whitepaper.id]
    assert [task.id for task in partial.tasks] == [outline.id]


def test_resolution_reports_ambiguous_short_and_unmatched_entries_together() -> None:
    pm, taylor, sam, _jo = _team()
    _requested(pm, taylor, "Draft the intro")
    _requested(pm, sam, "Draft the outro")
    _requested(pm, sam, "QA pass")

    resolution = resolve_prerequisites(agent=pm, references=["draft", "qa", "budget"])

    assert resolution.tasks == ()
    assert resolution.errors == (
        '"draft" matches more than one task: "Draft the intro" (Taylor, pending); '
        '"Draft the outro" (Sam, pending). Use a more specific title.',
        'Nothing of yours matches "qa". Use the title of one of your open tasks.',
        'Nothing of yours matches "budget". Use the title of one of your open tasks.',
    )


def test_resolution_prefers_an_open_task_to_a_finished_one_with_the_same_title() -> None:
    pm, taylor, sam, _jo = _team()
    old = _requested(pm, taylor, "Research")
    _finish(old.id, "complete")
    current = _requested(pm, sam, "Research")

    resolution = resolve_prerequisites(agent=pm, references=["Research"])

    assert [task.id for task in resolution.tasks] == [current.id]


async def test_a_title_matching_only_a_completed_task_makes_a_met_edge_and_a_pending_task() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    _finish(research.id, "complete")

    result = await _assign(pm, sam, "Draft the post", after=["Research"])

    (draft,) = _tasks_titled("Draft the post")
    assert draft.status == "pending" and draft.prerequisites == [research.id]
    assert result["detail"] == 'Alex assigned "Draft the post" to Sam. "Research" is already complete, so it starts now.'
    assert [item["trigger_type"] for item in result["trigger_requests"]] == ["task_assigned"]


async def test_a_title_matching_only_a_cancelled_task_is_refused() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    _finish(research.id, "cancelled")

    result = await _assign(pm, sam, "Draft the post", after=["Research"])

    assert result["event"] == "world_feedback"
    assert result["detail"] == '"Research" was cancelled, so a task cannot wait on it.'
    assert _tasks_titled("Draft the post") == []


def test_another_requesters_task_and_another_floors_task_are_not_candidates() -> None:
    pm, taylor, sam, jo = _team()
    _requested(jo, taylor, "Budget review")
    elsewhere = _requested(pm, sam, "Vendor list")
    finance = create_floor("Finance")
    db.update_agent(sam.id, floor_id=finance.id)
    assert elsewhere.requester_id == pm.id

    resolution = resolve_prerequisites(agent=pm, references=["Budget review", "Vendor list"])

    assert resolution.tasks == ()
    assert resolution.errors == (
        'Nothing of yours matches "Budget review". Use the title of one of your open tasks.',
        'Nothing of yours matches "Vendor list". Use the title of one of your open tasks.',
    )


async def test_several_errors_are_reported_together_and_nothing_is_created() -> None:
    pm, _taylor, sam, _jo = _team()

    result = await _assign(pm, sam, "Draft the post", after=["Research", "Outline"])

    assert result["event"] == "world_feedback"
    assert result["detail"] == (
        'Nothing of yours matches "Research". Use the title of one of your open tasks. '
        'Nothing of yours matches "Outline". Use the title of one of your open tasks.'
    )
    assert _tasks_titled("Draft the post") == []


def test_a_child_cannot_wait_on_its_parent_or_grandparent() -> None:
    pm, _taylor, _sam, _jo = _team()
    launch = db.create_task(title="Launch", assigned_to=pm.id, requester_id=pm.id, created_by=pm.id)
    post = db.create_task(
        title="Write the post", assigned_to=pm.id, requester_id=pm.id, created_by=pm.id, parent_task_id=launch.id,
    )

    resolution = resolve_prerequisites(agent=pm, references=["Write the post", "Launch"], parent_task_id=post.id)

    assert resolution.tasks == ()
    assert resolution.errors == (
        '"Write the post" is the task this one belongs to, so it cannot wait on it.',
        '"Launch" is the task this one belongs to, so it cannot wait on it.',
    )


async def test_an_assign_and_a_plan_child_cannot_wait_on_the_task_they_belong_to() -> None:
    pm, _taylor, _sam, jo = _team()
    parent = db.create_task(title="Coordinate the post", assigned_to=pm.id, requester_id=pm.id, created_by=pm.id)
    refused = '"Coordinate the post" is the task this one belongs to, so it cannot wait on it.'

    planned = _accept_plan(pm, parent, [
        {"agentName": "Sam", "taskTitle": "Draft", "after": ["Coordinate the post"]},
    ])
    # The assign's parent is the PM's bound work.
    db.create_runtime_activity(pm.id, "work", task_id=parent.id, title=parent.title)
    assigned = await _assign(pm, jo, "Review", after=["Coordinate the post"])

    assert (planned["event"], planned["detail"]) == ("world_feedback", refused)
    assert (assigned["event"], assigned["detail"]) == ("world_feedback", refused)
    assert _tasks_titled("Draft") == [] and _tasks_titled("Review") == []


# ─── Creation ───


def test_a_prerequisite_closing_between_resolution_and_insert_refuses_the_create() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    resolved = resolve_prerequisites(agent=pm, references=["Research"])
    _finish(research.id, "abandoned")

    with pytest.raises(PrerequisiteUnavailable) as exc:
        _requested(pm, sam, "Draft", prerequisite_ids=[task.id for task in resolved.tasks])

    assert str(exc.value) == '"Research" was abandoned, so a task cannot wait on it.'
    assert isinstance(exc.value.__cause__, db.PrerequisiteClosed)
    assert _tasks_titled("Draft") == []
    assert _edge_count() == 0


def test_a_create_with_prerequisites_nests_inside_an_open_transaction() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")

    with db.transaction():
        draft = db.create_task(
            "Draft",
            assigned_to=sam.id,
            requester_id=pm.id,
            created_by=pm.id,
            prerequisite_ids=[research.id],
            met_statuses=MET_PREREQUISITE_STATUSES,
            dead_statuses=DEAD_PREREQUISITE_STATUSES,
        )

    stored = db.get_task(draft.id)
    assert stored is not None and stored.status == "held" and stored.prerequisites == [research.id]


def test_a_failure_after_the_insert_rolls_back_the_row_edges_and_side_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    before = query_one("SELECT COUNT(*) AS cnt FROM task_work_contracts")

    def broken_references(_task_id, _references):
        raise RuntimeError("references store unavailable")

    monkeypatch.setattr(db_tasks, "set_task_references", broken_references)
    with pytest.raises(RuntimeError, match="references store unavailable"):
        db.create_task(
            "Draft",
            assigned_to=sam.id,
            requester_id=pm.id,
            created_by=pm.id,
            work_contract=WorkContract(deliverables=[DeliverableSpec(type="file", path="/projects/shared/d.md")]),
            references=[TaskReference(path="/projects/shared/brief.md")],
            prerequisite_ids=[research.id],
            met_statuses=MET_PREREQUISITE_STATUSES,
            dead_statuses=DEAD_PREREQUISITE_STATUSES,
        )

    assert _tasks_titled("Draft") == []
    assert _edge_count() == 0
    assert query_one("SELECT COUNT(*) AS cnt FROM task_work_contracts") == before


def test_a_completion_committed_before_creation_yields_a_task_that_is_not_held() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    resolved = resolve_prerequisites(agent=pm, references=["Research"])
    # The other process completes it after resolution, before the insert.
    _finish(research.id, "complete")

    draft = _requested(pm, sam, "Draft", prerequisite_ids=[task.id for task in resolved.tasks])

    assert draft.status == "pending" and draft.prerequisites == [research.id]
    assert not has_unfinished_prerequisites(draft.id)


def test_prerequisites_keep_insertion_order_not_id_order() -> None:
    pm, taylor, _sam, jo = _team()
    tasks = [_requested(pm, taylor, f"Part {index}") for index in range(4)]
    wanted = sorted((task.id for task in tasks), reverse=True)

    joined = _requested(pm, jo, "Assemble", prerequisite_ids=wanted)

    assert joined.prerequisites == wanted


def test_the_conditional_update_matches_only_while_the_expected_value_holds() -> None:
    pm, taylor, _sam, _jo = _team()
    task = _requested(pm, taylor, "Research")

    assert build_conditional_update(
        "tasks", "id", task.id, {"status_note": "first"}, {"status_note", "status"}, expected={"status": "held"},
    ) is False
    assert db.get_task(task.id).status_note is None
    assert build_conditional_update(
        "tasks", "id", task.id, {"status_note": "second"}, {"status_note", "status"}, expected={"status": "pending"},
    ) is True
    assert db.get_task(task.id).status_note == "second"
    assert db.update_task(task.id, status="accepted", expected_status="held") is None
    assert _status(task.id) == "pending"


# ─── Plan ───


@pytest.mark.parametrize(
    ("children", "message"),
    [
        (
            [
                {"key": "draft", "who": "Sam", "after": ["research"], "task": {"title": "Draft"}},
                {"key": "research", "who": "Taylor", "task": {"title": "Research"}},
            ],
            '"research" is listed after the task that waits on it. Put it first.',
        ),
        (
            [
                {"key": "research", "who": "Taylor", "task": {"title": "Research"}},
                {"key": "Research", "who": "Sam", "task": {"title": "More research"}},
            ],
            'plan child keys must be unique: "Research" is used twice',
        ),
        (
            [{"key": "draft", "who": "Sam", "after": ["Draft"], "task": {"title": "Draft"}}],
            '"Draft" cannot wait on itself',
        ),
        (
            [{"key": "  ", "who": "Sam", "task": {"title": "Draft"}}],
            'a plan child "key" must not be blank',
        ),
    ],
)
def test_the_plan_rejects_forward_duplicate_self_and_blank_keys(children: list[dict[str, Any]], message: str) -> None:
    raw = json.dumps({
        "act": "accept",
        "intent": "work",
        "msg": "On it.",
        "commit": "work",
        "data": {"task": {"title": "Coordinate the post"}, "plan": {"mode": "delegate", "children": children}},
        "th": "plan",
    })

    parsed = parse_decision(raw)

    assert parsed["decision"] == "_parse_failed"
    assert message in parsed["_raw_snippet"]


def test_the_plan_still_rejects_any_other_child_key() -> None:
    raw = json.dumps({
        "act": "accept",
        "intent": "work",
        "msg": "On it.",
        "commit": "work",
        "data": {
            "task": {"title": "Coordinate the post"},
            "plan": {"mode": "delegate", "children": [{"who": "Sam", "before": ["x"], "task": {"title": "Draft"}}]},
        },
        "th": "plan",
    })

    parsed = parse_decision(raw)

    assert parsed["decision"] == "_parse_failed"
    assert "unexpected delegated child keys: before" in parsed["_raw_snippet"]


def test_a_plan_chain_creates_the_later_child_held_and_wakes_only_the_first() -> None:
    pm, taylor, sam, _jo = _team()
    parent = db.create_task(title="Coordinate the post", assigned_to=pm.id, created_by=HUMAN_SENDER_ID)

    result = _accept_plan(pm, parent, [
        {"agentName": "Taylor", "taskTitle": "Research", "key": "research"},
        {"agentName": "Sam", "taskTitle": "Draft", "key": "draft", "after": ["research"]},
    ])

    assert result["event"] == "decision_applied", result
    (research,) = _tasks_titled("Research")
    (draft,) = _tasks_titled("Draft")
    assert (research.status, draft.status) == ("pending", "held")
    assert draft.prerequisites == [research.id]
    assigned = [item for item in result["trigger_requests"] if item["trigger_type"] == "task_assigned"]
    assert [item["task_id"] for item in assigned] == [research.id]
    assert [item["agent_id"] for item in assigned] == [taylor.id]
    assert sam.id not in {item["agent_id"] for item in result["trigger_requests"]}


def test_one_bad_child_rolls_back_the_whole_plan() -> None:
    pm, _taylor, _sam, _jo = _team()
    parent = db.create_task(title="Coordinate the post", assigned_to=pm.id, created_by=HUMAN_SENDER_ID)

    result = _accept_plan(pm, parent, [
        {"agentName": "Taylor", "taskTitle": "Research", "key": "research"},
        {"agentName": "Sam", "taskTitle": "Draft", "key": "draft", "after": ["Budget sign-off"]},
    ])

    assert result["event"] == "world_feedback"
    assert result["detail"] == 'Nothing of yours matches "Budget sign-off". Use the title of one of your open tasks.'
    assert _tasks_titled("Research") == [] and _tasks_titled("Draft") == []
    assert _edge_count() == 0


# ─── Assign: what the PM reads back ───


async def test_an_assign_on_hold_says_what_it_waits_on_and_wakes_nobody() -> None:
    pm, taylor, sam, jo = _team()
    _requested(pm, taylor, "Research")
    _requested(pm, jo, "Outline")

    single = await _assign(pm, sam, "Draft the post", after=["Research"])
    double = await _assign(pm, sam, "Edit the post", after=["Research", "Outline"])
    plain = await _assign(pm, sam, "Share the post")

    assert single["detail"] == 'Alex assigned "Draft the post" to Sam. It starts when "Research" (Taylor) finishes.'
    assert double["detail"] == (
        'Alex assigned "Edit the post" to Sam. It starts when "Research" (Taylor) and "Outline" (Jo) finish.'
    )
    assert plain["detail"] == 'Alex assigned "Share the post" to Sam.'
    assert single["trigger_requests"] == [] and double["trigger_requests"] == []
    assert single["chat_notification"]["reason"] == 'It starts when "Research" (Taylor) finishes.'
    assert "reason" not in plain["chat_notification"]
    (draft,) = _tasks_titled("Draft the post")
    assert draft.status == "held"
    assert (
        'Assigned "Draft the post" to Sam. It starts when "Research" (Taylor) finishes.'
        in _events(draft.id)
    )


async def test_after_is_ignored_on_a_bind_and_the_pm_is_told() -> None:
    pm, taylor, sam, _jo = _team()
    _requested(pm, taylor, "Research")
    existing = _requested(pm, sam, "Draft the post")

    result = await _assign(pm, sam, "Draft the post", after=["Research"])

    assert result["event"] == "world_feedback"
    assert result["detail"].endswith(' The "after" was not applied to the existing task.')
    stored = db.get_task(existing.id)
    assert stored is not None and stored.status == "pending" and stored.prerequisites == []


def test_the_assign_wire_reads_after_and_checks_only_its_shape() -> None:
    parsed = parse_action(json.dumps({
        "act": "assign",
        "data": {"aid": "a1", "after": ["Research"], "task": {"title": "Draft", "desc": "Draft it."}},
        "th": "assign",
    }))
    blank = parse_action(json.dumps({
        "act": "assign",
        "data": {"aid": "a1", "after": ["  "], "task": {"title": "Draft", "desc": "Draft it."}},
        "th": "assign",
    }))
    text = parse_action(json.dumps({
        "act": "assign",
        "data": {"aid": "a1", "after": "Research", "task": {"title": "Draft", "desc": "Draft it."}},
        "th": "assign",
    }))

    assert parsed["action"] == "delegateTask" and parsed["after"] == ["Research"]
    for rejected in (blank, text):
        assert rejected["action"] == "_parse_failed"
        assert '"after" must be a list of non-empty strings' in rejected["_raw_snippet"]


# ─── State machine, operator overrides, visibility ───


def test_a_held_task_cannot_be_accepted_started_or_completed() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])

    for target in ("accepted", "active", "complete"):
        with pytest.raises(IllegalTaskTransition):
            transition_task(draft.id, target, reason="try", actor="pytest")
    assert _status(draft.id) == "held"


def test_an_operator_edit_of_a_held_task_does_not_wake_it(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    pm, taylor, sam, jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])

    edited = client.patch(f"/api/tasks/{draft.id}", headers=_headers(), json={"description": "A sharper brief."})
    moved = client.patch(f"/api/tasks/{draft.id}", headers=_headers(), json={"assigned_to": jo.id})

    assert edited.status_code == 200 and moved.status_code == 200, (edited.text, moved.text)
    assert moved.json()["status"] == "held"
    assert db.get_task(draft.id).prerequisites == [research.id]
    assert _triggers(sam.id, trigger_type="task_assigned") == []
    assert _triggers(jo.id, trigger_type="task_assigned") == []


def test_operator_release_drops_unfinished_edges_and_wakes_the_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    pm, taylor, sam, jo = _team()
    done = _requested(pm, jo, "Interviews")
    _finish(done.id, "complete")
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[done.id, research.id])

    response = client.post(f"/api/tasks/{draft.id}/release", headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending" and body["operator_can_release"] is False
    stored = db.get_task(draft.id)
    assert stored is not None and stored.prerequisites == [done.id]
    assert not has_unfinished_prerequisites(draft.id)
    assert 'The boss released the task without waiting for: "Research".' in _events(draft.id)
    assert len(_triggers(sam.id, trigger_type="task_assigned", task_id=draft.id)) == 1
    # Finishing the dropped prerequisite later does nothing more to it.
    _finish(research.id, "complete")
    assert len(_triggers(sam.id, trigger_type="task_assigned", task_id=draft.id)) == 1


def test_operator_resume_of_a_task_blocked_by_a_failed_prerequisite_drops_the_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    _finish(research.id, "cancelled")
    assert _status(draft.id) == "blocked"

    response = client.post(f"/api/tasks/{draft.id}/resume", headers=_headers())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert db.get_task(draft.id).prerequisites == []
    assert 'The boss restarted the task without waiting for: "Research".' in _events(draft.id)
    assert len(_triggers(sam.id, trigger_type="task_assigned", task_id=draft.id)) == 1


def test_operator_edit_of_a_task_blocked_by_a_failed_prerequisite_drops_the_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    _finish(research.id, "declined")

    response = client.patch(f"/api/tasks/{draft.id}", headers=_headers(), json={"description": "Write it anyway."})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert not has_unfinished_prerequisites(draft.id)
    assert len(_triggers(sam.id, trigger_type="task_assigned", task_id=draft.id)) == 1


def test_operator_hold_parks_a_pending_task_until_release(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    db.create_agent_trigger(
        agent_id=sam.id, trigger_type="task_assigned", source_channel="work", payload={}, task_id=draft.id,
    )

    held = client.post(f"/api/tasks/{draft.id}/hold", headers=_headers(), json={"note": "  Wait for the budget.  "})

    assert held.status_code == 200, held.text
    body = held.json()
    assert body["status"] == "held" and body["operator_can_hold"] is False and body["operator_can_release"] is True
    stored = db.get_task(draft.id)
    assert stored is not None and stored.status_note == "Wait for the budget."
    assert [row for row in _triggers(sam.id, trigger_type="task_assigned") if row["status"] == "queued"] == []
    events = _events(draft.id)
    assert "The boss put the task on hold." in events and "Wait for the budget." in events

    released = client.post(f"/api/tasks/{draft.id}/release", headers=_headers())

    assert released.status_code == 200, released.text
    assert released.json()["status"] == "pending"
    assert len([row for row in _triggers(sam.id, trigger_type="task_assigned") if row["status"] == "queued"]) == 1


def test_hold_and_release_refuse_the_wrong_state(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    unassigned = db.create_task(title="Backlog idea", created_by=HUMAN_SENDER_ID)
    closed = _requested(pm, sam, "Old")
    _finish(closed.id, "cancelled")

    assert client.post("/api/tasks/no-such-task/hold", headers=_headers()).status_code == 404
    assert client.post(f"/api/tasks/{unassigned.id}/hold", headers=_headers()).status_code == 400
    assert client.post(f"/api/tasks/{closed.id}/hold", headers=_headers()).status_code == 409
    assert client.post(f"/api/tasks/{draft.id}/hold", headers=_headers(), json={"why": "x"}).status_code == 422
    assert client.post("/api/tasks/no-such-task/release", headers=_headers()).status_code == 404
    not_held = client.post(f"/api/tasks/{draft.id}/release", headers=_headers())
    assert not_held.status_code == 400 and not_held.json()["detail"] == "Only a task on hold can be released"
    assert client.post(f"/api/tasks/{closed.id}/release", headers=_headers()).status_code == 409


def test_operator_hold_cancels_the_tasks_paused_work() -> None:
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    # As an earlier defer leaves it: resumable work on a pending task.
    paused = db.create_runtime_activity(sam.id, "work", status="paused", task_id=draft.id, title=draft.title)

    hold_task_as_operator(draft.id, note=None)

    stored = db.get_activity(paused.id)
    assert stored is not None and stored.status == "cancelled"
    assert stored.detail == "The boss put the task on hold"
    assert db.get_resumable_work_activity(sam.id, draft.id) is None


def test_a_hold_that_loses_to_a_concurrent_accept_cancels_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    read_by_the_hold = db.get_task(draft.id)
    # Sam accepts after the hold read the task as pending, before it writes:
    # live work on it, and that work's queued resume.
    transition_task(draft.id, "accepted", reason="accepted", actor="pytest")
    work = db.create_runtime_activity(sam.id, "work", task_id=draft.id, title=draft.title)
    db.create_agent_trigger(
        agent_id=sam.id, trigger_type="activity_resumed", source_channel="work", payload={}, task_id=draft.id,
    )
    real_get_task = db.get_task
    stale_reads = [read_by_the_hold]

    def read_before_the_accept(task_id: str):
        if task_id == draft.id and stale_reads:
            return stale_reads.pop()
        return real_get_task(task_id)

    monkeypatch.setattr(db, "get_task", read_before_the_accept)

    with pytest.raises(ValueError, match="This task just changed. Refresh and try again."):
        hold_task_as_operator(draft.id, note="Wait for the budget.")

    assert _status(draft.id) == "accepted"
    stored = db.get_activity(work.id)
    assert stored is not None and stored.status == "active"
    assert [row["status"] for row in _triggers(sam.id, trigger_type="activity_resumed")] == ["queued"]
    events = _events(draft.id)
    assert "The boss put the task on hold." not in events and "Wait for the budget." not in events


def test_operator_release_clears_the_hold_note() -> None:
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    hold_task_as_operator(draft.id, note=None)
    assert db.get_task(draft.id).status_note == "Put on hold by the boss"

    released = release_task_as_operator(draft.id)

    assert released.task.status == "pending" and released.task.status_note is None


def test_a_lost_release_race_keeps_the_edges_and_adds_no_event(monkeypatch: pytest.MonkeyPatch) -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    before = _events(draft.id)
    drop = operator_actions._drop_unfinished_prerequisites

    def drop_then_lose_the_race(task, *, verb):
        drop(task, verb=verb)
        # Another release moves the task after the edges were dropped. The
        # rollback undoes this stand-in write as well as the drop.
        execute("UPDATE tasks SET status = 'pending' WHERE id = $1", [task.id])

    monkeypatch.setattr(operator_actions, "_drop_unfinished_prerequisites", drop_then_lose_the_race)

    with pytest.raises(ValueError, match="This task was just released or changed. Refresh and try again."):
        release_task_as_operator(draft.id)

    stored = db.get_task(draft.id)
    assert stored is not None and stored.prerequisites == [research.id]
    assert has_unfinished_prerequisites(draft.id)
    assert _events(draft.id) == before
    assert _triggers(sam.id, trigger_type="task_assigned") == []


def test_task_rows_carry_the_operator_flags_and_prerequisite_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    pm, taylor, sam, jo = _team()
    research = _requested(pm, taylor, "Research")
    outline = _requested(pm, jo, "Outline")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id, outline.id])

    listed = {row["id"]: row for row in client.get("/api/tasks", headers=_headers()).json()}
    single = client.get(f"/api/tasks/{draft.id}", headers=_headers()).json()

    for row in (listed[draft.id], single):
        assert row["operator_can_release"] is True and row["operator_can_hold"] is False
        assert row["prerequisite_rows"] == [
            {"id": research.id, "title": "Research", "status": "pending", "assigned_to_name": "Taylor"},
            {"id": outline.id, "title": "Outline", "status": "pending", "assigned_to_name": "Jo"},
        ]
    assert listed[research.id]["operator_can_hold"] is True
    assert listed[research.id]["prerequisite_rows"] == []


def test_the_assignee_never_sees_unreleased_work_and_the_requester_sees_it_on_hold() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    review = _requested(pm, taylor, "Review")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    edit = _requested(pm, sam, "Edit", prerequisite_ids=[review.id])
    _finish(review.id, "cancelled")
    assert (_status(draft.id), _status(edit.id)) == ("held", "blocked")

    self_board = build_task_board(sam.id, scope="self")
    owned_board = build_task_board(pm.id, scope="owned")

    # The open-work sections (recent_completed_tasks is history, read separately).
    for name in ("my_open_tasks", "my_waiting_tasks", "my_blocked_tasks", "tasks_waiting_on_me"):
        assert {task.id for task in self_board["sections"][name]}.isdisjoint({draft.id, edit.id}), name
    for task_id in (draft.id, edit.id):
        assert not _task_is_open_for(task_id, sam.id)
        assert not _trigger_task_is_open_for({"task_id": task_id}, sam.id)
    assert [task.id for task in owned_board["sections"]["held_tasks_i_requested"]] == [draft.id]
    assert draft.id not in {task.id for task in owned_board["sections"]["tasks_i_delegated"]}

    pm_board = _task_board_context(pm.id, None, work_commit_target=None)["value"]
    assert "ON HOLD:" in pm_board
    assert f'{draft.id} | ' in pm_board and '| held, after "Research" |' in pm_board
    sam_board = _task_board_context(sam.id, None, work_commit_target=None)["value"]
    assert draft.id not in sam_board and edit.id not in sam_board


def test_a_blocked_row_names_the_prerequisite_that_closed() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    # Owned by the PM, as a plan child is, so it is on the PM's board.
    _requested(pm, sam, "Draft", prerequisite_ids=[research.id], owner_id=pm.id)
    _finish(research.id, "cancelled")

    pm_board = _task_board_context(pm.id, None, work_commit_target=None)["value"]

    assert '| blocked, "Research" was cancelled |' in pm_board


def test_live_wake_and_work_commit_never_continue_unreleased_work() -> None:
    pm, taylor, sam, _jo = _team()
    held, blocked = _unreleased(pm, taylor, sam)
    # Paused work on each, and live work on one: every branch has a candidate.
    for task in (held, blocked):
        db.create_runtime_activity(sam.id, "work", status="paused", task_id=task.id, title=task.title)
    db.create_runtime_activity(sam.id, "work", task_id=blocked.id, title=blocked.title)

    assert activity_runtime.resolve_live_wake_task(sam.id) is None
    assert activity_runtime.work_commit_target(sam.id, None) is None
    for task in (held, blocked):
        assert activity_runtime.work_commit_target(sam.id, {"task_id": task.id}) is None

    share = _requested(pm, sam, "Share")
    assert activity_runtime.resolve_live_wake_task(sam.id).id == share.id
    assert activity_runtime.work_commit_target(sam.id, {"task_id": held.id}).id == share.id


def test_an_idle_agent_whose_blocked_work_waits_on_a_failed_prerequisite_reads_idle() -> None:
    pm, taylor, sam, _jo = _team()
    _unreleased(pm, taylor, sam)

    assert activity_runtime.refresh_agent_status(sam.id).status == "idle"

    own = _requested(pm, sam, "Share")
    transition_task(own.id, "blocked", reason="stuck", actor="pytest")
    assert activity_runtime.refresh_agent_status(sam.id).status == "blocked"


def test_the_project_summary_hides_unreleased_work_from_its_assignee_not_its_owner() -> None:
    pm, taylor, sam, _jo = _team()
    held, blocked = _unreleased(pm, taylor, sam)
    share = _requested(pm, sam, "Share", owner_id=pm.id)
    for task in (held, blocked, share):
        db.update_task(task.id, project="Launch")

    def shown(agent_id: str) -> set[str]:
        return {item["title"] for project in build_project_summary(agent_id) for item in project["latest_tasks"]}

    assert shown(sam.id) == {"Share"}
    assert shown(pm.id) == {"Draft", "Edit", "Share"}


def test_the_project_summary_hides_unreleased_top_level_work_from_its_assignee_who_owns_it() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    review = _requested(pm, taylor, "Review")
    # Top-level assigns: each owns itself, so Sam is their owner as well.
    held = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    blocked = _requested(pm, sam, "Edit", prerequisite_ids=[review.id])
    _finish(review.id, "cancelled")
    share = _requested(pm, sam, "Share")
    for task in (held, blocked, share):
        db.update_task(task.id, project="Launch")
    assert {db.get_task(task.id).owner_id for task in (held, blocked)} == {sam.id}
    assert (_status(held.id), _status(blocked.id)) == ("held", "blocked")

    (launch,) = build_project_summary(sam.id)

    assert [item["title"] for item in launch["latest_tasks"]] == ["Share"]
    assert launch["counts"] == {"pending": 1}


def test_the_next_card_after_a_handoff_skips_unreleased_work() -> None:
    pm, taylor, sam, jo = _team()
    parent = db.create_task(title="Coordinate the post", assigned_to=pm.id, created_by=HUMAN_SENDER_ID)
    first = _requested(pm, taylor, "Research", parent=parent)
    review = _requested(pm, taylor, "Review")
    held = _requested(pm, sam, "Draft", prerequisite_ids=[first.id], parent=parent)
    blocked = _requested(pm, sam, "Edit", prerequisite_ids=[review.id], parent=parent)
    _finish(review.id, "cancelled")
    later = _requested(pm, jo, "Share", parent=parent)
    # created_at has whole seconds; spread them so "after" is unambiguous.
    for minute, task in enumerate((first, held, blocked, later)):
        execute(
            "UPDATE tasks SET created_at = $1 WHERE id = $2",
            [datetime(2026, 1, 1, 9, minute, tzinfo=timezone.utc), task.id],
        )

    card = next_board_task(db.get_task(first.id), author_id=taylor.id)

    assert card is not None and card.id == later.id


def test_a_parse_failure_never_requeues_or_notes_unreleased_work() -> None:
    pm, taylor, sam, _jo = _team()
    held, blocked = _unreleased(pm, taylor, sam)
    db.create_runtime_activity(sam.id, "work", status="paused", task_id=blocked.id, title=blocked.title)
    before = {task.id: _events(task.id) for task in (held, blocked)}

    for task in (held, blocked):
        result = surface_decision_parse_failure(agent=sam, trigger={"type": "task_assigned", "task_id": task.id})
        assert result["trigger_requests"] == []

    assert (_status(held.id), _status(blocked.id)) == ("held", "blocked")
    assert {task.id: _events(task.id) for task in (held, blocked)} == before


async def test_a_requester_question_on_a_held_task_is_saved_and_wakes_nobody() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])
    state = db.get_agent_state(pm.id)
    assert state is not None

    result = await execute_action(
        {"action": "taskMessage", "taskId": draft.id, "content": "Use the Q3 numbers.", "messageKind": "question"},
        pm,
        state,
    )

    assert result == {
        "event": "message_sent",
        "detail": 'Saved on "Draft". Sam sees it when the task starts.',
        "agent_name": "Alex",
    }
    assert "Use the Q3 numbers." in _events(draft.id)
    assert [item for item in db.list_notifications(agent_id=sam.id) if item.task_id == draft.id] == []


def test_recent_completed_recall_leaves_out_unreleased_work() -> None:
    pm, taylor, sam, _jo = _team()
    _held, blocked = _unreleased(pm, taylor, sam)
    stuck = _requested(pm, sam, "Share")
    transition_task(stuck.id, "blocked", reason="stuck", actor="pytest")
    interviews = _requested(pm, taylor, "Interviews")
    _finish(interviews.id, "complete")
    # Its only prerequisite completed, so it was released: recall keeps it.
    summary = _requested(pm, sam, "Summarize", prerequisite_ids=[interviews.id])
    _finish(summary.id, "complete")

    recalled = {row["id"] for row in db.get_recent_completed_tasks(sam.id, limit=10)}

    assert recalled == {stuck.id, summary.id}
    assert blocked.id not in recalled


def test_a_held_task_born_on_a_thread_says_so_in_its_created_line() -> None:
    pm, taylor, sam, _jo = _team()
    channel = db.create_channel(name="Launch", member_agent_ids=[pm.id, taylor.id, sam.id], created_by=HUMAN_SENDER_ID)
    research = _requested(pm, taylor, "Research")
    created = create_or_bind_task(
        title="Draft",
        description="Draft it.",
        project=None,
        assigned_to=sam.id,
        requester_id=pm.id,
        owner_id=None,
        created_by=pm.id,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name=pm.name,
        audit_author_type="agent",
        audit_author_agent_id=pm.id,
        prerequisite_ids=[research.id],
    )

    assert created.task is not None and created.task.status == "held"
    contents = [item.content for item in db.list_channel_messages(channel.id, limit=20)]
    assert 'Sam Created: Draft — on hold until "Research" finishes' in contents


# ─── Decision turn on unreleased work ───


_ON_HOLD_FEEDBACK = {"event": "world_feedback", "detail": '"Draft" is not ready to start yet, so leave it for now.', "agent_name": "Sam"}


def _decide(agent, decision: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any]:
    state = db.get_agent_state(agent.id)
    assert state is not None
    return apply_decision(decision, agent, state, trigger)


@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "accept", "intentKind": "work_request", "commitmentKind": "work", "taskTitle": "Draft", "reply": "On it."},
        {"decision": "defer", "intentKind": "work_request", "commitmentKind": "work", "taskTitle": "Draft", "reply": "Later."},
        {"decision": "decline", "intentKind": "work_request", "reply": "Not for me."},
    ],
    ids=["accept", "defer", "decline"],
)
def test_a_decision_on_a_task_held_mid_turn_is_refused_and_changes_nothing(decision: dict[str, Any]) -> None:
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    # Sam's wake was already claimed when the boss held the task.
    hold_task_as_operator(draft.id, note=None)
    before = _events(draft.id)

    result = _decide(sam, decision, {
        "type": "task_assigned", "task_id": draft.id, "content": "Draft.", "from_name": "Alex", "from_agent": pm.id,
    })

    assert result == _ON_HOLD_FEEDBACK
    assert _status(draft.id) == "held"
    assert _events(draft.id) == before
    assert db.list_activities(agent_id=sam.id) == []


def test_an_accept_naming_a_task_held_since_validation_writes_no_revision() -> None:
    pm, _taylor, sam, _jo = _team()
    draft = _requested(pm, sam, "Draft")
    # Validation saw it pending; the boss held it before the decision landed.
    hold_task_as_operator(draft.id, note=None)
    before = _events(draft.id)

    result = _decide(
        sam,
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskId": draft.id,
            "taskTitle": "Draft",
            "taskDescription": "A sharper brief.",
            "reply": "Picking the draft up.",
        },
        {"type": "peer_message", "from_agent": pm.id, "from_name": "Alex", "content": "Can you take the draft?"},
    )

    assert result == _ON_HOLD_FEEDBACK
    stored = db.get_task(draft.id)
    assert stored is not None and stored.status == "held" and stored.description == "Draft."
    assert _events(draft.id) == before
    # A refused bind saves no request either.
    assert stored.request_messages == []


def test_an_accept_whose_title_binds_a_held_task_is_refused() -> None:
    pm, taylor, sam, _jo = _team()
    research = _requested(pm, taylor, "Research")
    draft = _requested(pm, sam, "Draft", prerequisite_ids=[research.id])

    result = _decide(
        sam,
        {"decision": "accept", "intentKind": "work_request", "commitmentKind": "work", "taskTitle": "Draft", "reply": "On it."},
        {"type": "peer_message", "from_agent": pm.id, "from_name": "Alex", "content": "Please draft it."},
    )

    assert result == _ON_HOLD_FEEDBACK
    # Bound to the held task, not a duplicate created beside it.
    assert [task.id for task in _tasks_titled("Draft")] == [draft.id]
    assert _status(draft.id) == "held"
    # A refused bind saves no request either.
    assert db.get_task(draft.id).request_messages == []
    assert db.list_activities(agent_id=sam.id) == []


# ─── Prompts ───


def test_the_prompts_teach_after_and_lint_clean() -> None:
    execution = load_default_prompt("runtime_contract_execution")
    decision = load_default_prompt("runtime_contract_decision")
    trigger_event = load_default_prompt("runtime_block_trigger_event")
    assert (
        "If a task needs another task's output, add `after`. Never message a teammate to wait, "
        "and do not assign work that cannot start yet without `after`."
    ) in execution
    assert '"after":["Find the cause of the POST /orders 500s"]' in execution
    assert '"key":"draft","who":"Sam","after":["research"]' in decision
    assert "These tasks finished first:\n{{trigger.task_prerequisites}}" in trigger_event

    report = lint_runtime_prompts()
    keys = {"runtime_contract_execution", "runtime_contract_decision", "runtime_block_trigger_event"}
    assert [issue for issue in report.issues if issue.severity == "error" and issue.surface_key in keys] == []
