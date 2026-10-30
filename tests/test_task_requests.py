"""Task requests: the requester's own words, saved with the work and shown wherever it is read.

Covers core/tasking/requests.py and the ``tasks.request_messages`` column
(strategy-docs/taskRequestFidelity_bugfix_10292026_1793325121731.md, Part A
and B): which message(s) each trigger saves, the append-only merge, the
transactional append, the model's validation, the round trip through every
task read, the one formatter, and forwarding a parent's request to the
children an ``assign`` or ``deleg`` creates. Everything runs against the
isolated test database (conftest.py). No LLM.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.actions_lifecycle import _handle_delegated
from core.agent_loop.actions_tasks import _handle_delegate_task
from core.models import Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskRequestMessage
from core.tasking.requests import (
    append_task_request,
    format_request_for_context,
    merge_request_messages,
    request_messages_for_trigger,
    request_payload_for_task,
)

_OWN_HEADING = (
    "request (what was asked, word for word, oldest first; where the description leaves something out, "
    "go by this and any document it names):"
)


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


def _agent(name: str = "Ada") -> Agent:
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _task(title: str = "Write the report", **fields: Any) -> Task:
    return db.create_task(title=title, requester_id=HUMAN_SENDER_ID, created_by=HUMAN_SENDER_ID, **fields)


def _human(content: str) -> TaskRequestMessage:
    return TaskRequestMessage(author_type="human", author_name="Boss", content=content)


def _from_agent(agent_id: str, name: str, content: str) -> TaskRequestMessage:
    return TaskRequestMessage(author_type="agent", author_agent_id=agent_id, author_name=name, content=content)


def _set_boss_name(value: str) -> None:
    db.set_setting("boss_name", value, "profile")
    config.refresh_if_changed()


def _dumps(messages: list[TaskRequestMessage]) -> list[dict[str, Any]]:
    return [message.model_dump() for message in messages]


# ─── request_messages_for_trigger: one test per trigger row ───


def test_human_chat_saves_the_message_by_the_boss() -> None:
    messages = request_messages_for_trigger(
        {"type": "human_chat", "content": "Review /projects/app/rubric.md\nand fix it", "from_name": "Human"},
    )
    assert _dumps(messages) == [
        {"author_type": "human", "author_agent_id": None, "author_name": "Boss",
         "content": "Review /projects/app/rubric.md\nand fix it"},
    ]


def test_peer_message_saves_the_message_by_its_agent() -> None:
    messages = request_messages_for_trigger(
        {"type": "peer_message", "content": "Please draft it.", "from_agent": "a-1", "from_name": "Alex"},
    )
    assert _dumps(messages) == [
        {"author_type": "agent", "author_agent_id": "a-1", "author_name": "Alex", "content": "Please draft it."},
    ]


@pytest.mark.parametrize("trigger_type", ["channel_message", "channel_response"])
def test_a_channel_opener_alone_is_saved_with_its_author(trigger_type: str) -> None:
    messages = request_messages_for_trigger({
        "type": trigger_type, "content": "Team, audit the tests.", "author_type": "agent",
        "from_agent": "a-1", "from_name": "Alex", "channel_id": "c-1",
    })
    assert _dumps(messages) == [
        {"author_type": "agent", "author_agent_id": "a-1", "author_name": "Alex", "content": "Team, audit the tests."},
    ]


def test_a_channel_trigger_with_a_human_latest_line_saves_both_messages_and_their_authors() -> None:
    messages = request_messages_for_trigger({
        "type": "channel_message",
        "content": "Team, audit the tests.",
        "author_type": "agent",
        "from_agent": "a-1",
        "from_name": "Alex",
        "latest_content": "Start with /projects/app/tests/test_login.py",
        "latest_author_type": "human",
        "latest_from_agent": "",
        "latest_from_name": "Boss",
    })
    assert _dumps(messages) == [
        {"author_type": "agent", "author_agent_id": "a-1", "author_name": "Alex", "content": "Team, audit the tests."},
        {"author_type": "human", "author_agent_id": None, "author_name": "Boss",
         "content": "Start with /projects/app/tests/test_login.py"},
    ]


@pytest.mark.parametrize("trigger_type", ["session_message", "session_response"])
def test_a_meeting_message_is_saved_with_its_author(trigger_type: str) -> None:
    messages = request_messages_for_trigger({
        "type": trigger_type, "content": "Write up the decisions.", "author_type": "human",
        "from_agent": None, "from_name": "Boss",
    })
    assert _dumps(messages) == [
        {"author_type": "human", "author_agent_id": None, "author_name": "Boss", "content": "Write up the decisions."},
    ]


def test_a_task_update_is_by_its_agent_when_set_and_otherwise_by_the_boss() -> None:
    from_agent = request_messages_for_trigger(
        {"type": "task_update", "content": "Also cover Q3.", "from_agent": "a-1", "from_name": "Alex"},
    )
    from_boss = request_messages_for_trigger(
        {"type": "task_update", "content": "Also cover Q3.", "from_agent": None, "from_name": "Human"},
    )
    assert _dumps(from_agent) == [
        {"author_type": "agent", "author_agent_id": "a-1", "author_name": "Alex", "content": "Also cover Q3."},
    ]
    assert _dumps(from_boss) == [
        {"author_type": "human", "author_agent_id": None, "author_name": "Boss", "content": "Also cover Q3."},
    ]


def test_blank_content_is_skipped() -> None:
    assert request_messages_for_trigger({"type": "human_chat", "content": "   \n "}) == []
    channel = request_messages_for_trigger({
        "type": "channel_response", "content": "Audit the tests.", "author_type": "human", "from_name": "Boss",
        "latest_content": "  ", "latest_author_type": "agent", "latest_from_agent": "a-1", "latest_from_name": "Alex",
    })
    assert [message.content for message in channel] == ["Audit the tests."]


@pytest.mark.parametrize("trigger_type", ["task_assigned", "task_follow_up", "activity_resumed", "watchdog_status_ping"])
def test_a_turn_that_answers_no_message_saves_nothing(trigger_type: str) -> None:
    assert request_messages_for_trigger({"type": trigger_type, "content": "Begin work.", "from_name": "Ada"}) == []


def _request_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == "core.tasking.requests"]


def test_a_message_whose_author_cannot_be_recorded_is_left_out_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.tasking.requests"):
        messages = request_messages_for_trigger({"type": "peer_message", "content": "Draft it.", "from_name": "Alex"})
    assert messages == []
    (warned,) = _request_warnings(caplog)
    assert "peer_message" in warned and "content message" in warned and "author_agent_id" in warned


def test_a_channel_opener_by_an_agent_without_an_id_leaves_only_the_latest_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trigger = {
        "type": "channel_message", "content": "Team, audit the tests.", "author_type": "agent",
        "from_name": "Alex",
        "latest_content": "Start with test_login.py", "latest_author_type": "human",
        "latest_from_agent": "", "latest_from_name": "Boss",
    }
    with caplog.at_level(logging.WARNING, logger="core.tasking.requests"):
        messages = request_messages_for_trigger(trigger)
    assert [(m.author_type, m.content) for m in messages] == [("human", "Start with test_login.py")]
    (warned,) = _request_warnings(caplog)
    assert "channel_message" in warned and "opener message" in warned
    # Without a newer line there is nothing left to save, and still no error.
    opener_only = {key: value for key, value in trigger.items() if not key.startswith("latest_")}
    assert request_messages_for_trigger(opener_only) == []


# ─── merge_request_messages ───


def test_merge_keeps_order_and_drops_duplicates_by_key() -> None:
    first = _human("Write the report.")
    second = _from_agent("a-1", "Alex", "Cover Q3 too.")
    renamed = TaskRequestMessage(author_type="agent", author_agent_id="a-1", author_name="Alexandra",
                                 content="Cover Q3 too.")
    third = _human("And Q4.")
    merged = merge_request_messages([first, second], [renamed, third, third, _human("Write the report.")])
    assert merged == [first, second, third]
    # The same words from a different author are a different message.
    other = _from_agent("a-2", "Bea", "Write the report.")
    assert merge_request_messages([first], [other]) == [first, other]


# ─── append_task_request ───


def test_append_adds_after_what_is_saved_and_a_repeat_adds_nothing() -> None:
    task = _task()
    first = append_task_request(task.id, [_human("Write the report.")])
    assert first.request_messages == [_human("Write the report.")]
    second = append_task_request(task.id, [_human("Write the report."), _human("Cover Q3 too.")])
    assert [m.content for m in second.request_messages] == ["Write the report.", "Cover Q3 too."]
    again = append_task_request(task.id, [_human("Cover Q3 too.")])
    assert again.request_messages == second.request_messages
    assert append_task_request(task.id, []) == db.get_task(task.id)


def test_append_to_a_vanished_task_raises() -> None:
    with pytest.raises(ValueError, match="no longer exists"):
        append_task_request("no-such-task", [_human("Write the report.")])
    with pytest.raises(ValueError, match="no longer exists"):
        append_task_request("no-such-task", [])


# ─── TaskRequestMessage ───


def test_blank_content_and_blank_names_are_refused() -> None:
    with pytest.raises(ValidationError):
        TaskRequestMessage(author_type="human", author_name="Boss", content="  ")
    with pytest.raises(ValidationError):
        TaskRequestMessage(author_type="system", author_name=" ", content="Run it.")
    stripped = TaskRequestMessage(author_type="human", author_name=" Boss ", content="  Line one\nline two  ")
    assert (stripped.author_name, stripped.content) == ("Boss", "Line one\nline two")


def test_an_agent_author_needs_an_id_and_no_other_author_has_one() -> None:
    with pytest.raises(ValidationError, match="author_agent_id"):
        TaskRequestMessage(author_type="agent", author_name="Alex", content="Draft it.")
    with pytest.raises(ValidationError, match="author_agent_id"):
        TaskRequestMessage(author_type="human", author_agent_id="a-1", author_name="Boss", content="Draft it.")
    with pytest.raises(ValidationError, match="author_agent_id"):
        TaskRequestMessage(author_type="system", author_agent_id="a-1", author_name="BossMod", content="Run it.")


def test_unknown_keys_and_author_types_are_refused() -> None:
    with pytest.raises(ValidationError):
        TaskRequestMessage.model_validate(
            {"author_type": "human", "author_name": "Boss", "content": "Draft it.", "sent_at": "now"},
        )
    with pytest.raises(ValidationError):
        TaskRequestMessage(author_type="runtime", author_name="BossMod", content="Draft it.")


# ─── the column, through every task read ───


def test_update_task_round_trips_the_request_through_every_read() -> None:
    agent = _agent()
    task = _task(assigned_to=agent.id)
    other = _task("Something else", assigned_to=agent.id)
    messages = [_human("Write the report.\nUse /projects/app/q3.md"), _from_agent(agent.id, "Ada", "And Q4?")]
    updated = db.update_task(task.id, request_messages=messages)
    assert updated.request_messages == messages
    assert db.get_task(task.id).request_messages == messages
    assert db.get_tasks_by_ids([task.id, other.id])[task.id].request_messages == messages
    listed = {row.id: row for row in db.list_tasks(assigned_to=agent.id)}
    assert listed[task.id].request_messages == messages
    assert listed[other.id].request_messages == []
    assert [row.request_messages for row in db.list_tasks_by_statuses(["pending"]) if row.id == task.id] == [messages]
    recent = {row.id: row for row in db.list_recent_tasks(assigned_to=agent.id, limit=5)}
    assert recent[task.id].request_messages == messages
    backlog = _task("Loose end")
    db.update_task(backlog.id, request_messages=[_human("Triage it.")])
    assert [row.request_messages for row in db.list_backlog_tasks(status="pending") if row.id == backlog.id] == [
        [_human("Triage it.")],
    ]


def test_an_empty_request_is_refused_before_any_write() -> None:
    task = _task()
    db.update_task(task.id, request_messages=[_human("Write the report.")])
    with pytest.raises(ValueError, match="never removed"):
        db.update_task(task.id, request_messages=[], status_note="should not land")
    stored = db.get_task(task.id)
    assert len(stored.request_messages) == 1
    assert stored.status_note is None


def test_null_reads_as_no_request() -> None:
    task = _task()
    row = db.query_one("SELECT request_messages FROM tasks WHERE id = $1", [task.id])
    assert row is not None and row["request_messages"] is None
    assert db.get_task(task.id).request_messages == []


# ─── request_payload_for_task + format_request_for_context ───


def test_an_own_request_formats_as_its_heading_and_one_line_per_message() -> None:
    task = _task()
    append_task_request(task.id, [_human("Fix the tests in\n/projects/app/tests"), _from_agent("a-1", "Alex", "Also lint.")])
    payload = request_payload_for_task(db.get_task(task.id))
    assert payload is not None and payload["parent_title"] is None
    assert format_request_for_context(payload) == [
        _OWN_HEADING,
        "[Boss]: Fix the tests in\n/projects/app/tests",
        "[Alex]: Also lint.",
    ]


def test_a_child_without_its_own_request_is_shown_the_parents_one_level_only() -> None:
    grandparent = _task("Launch")
    append_task_request(grandparent.id, [_human("Launch it.")])
    parent = _task("Ship the release", parent_task_id=grandparent.id)
    append_task_request(parent.id, [_human("Ship it with /projects/app/notes.md")])
    child = _task("Write the notes", parent_task_id=parent.id)
    assert format_request_for_context(request_payload_for_task(child)) == [
        'request behind the parent task "Ship the release" (word for word, oldest first; '
        "your part is this task's description):",
        "[Boss]: Ship it with /projects/app/notes.md",
    ]
    grandchild = _task("Proofread", parent_task_id=child.id)
    assert request_payload_for_task(grandchild) is None
    # A child with a request of its own shows only its own.
    append_task_request(child.id, [_from_agent("a-1", "Alex", "Notes only, please.")])
    assert format_request_for_context(request_payload_for_task(db.get_task(child.id)))[0] == _OWN_HEADING
    assert request_payload_for_task(_task("Loose")) is None
    assert format_request_for_context(None) == []


def test_a_human_is_shown_with_the_current_boss_label_after_a_rename() -> None:
    task = _task()
    append_task_request(task.id, [_human("Write the report.")])
    payload = request_payload_for_task(db.get_task(task.id))
    _set_boss_name("Jordan")
    assert format_request_for_context(payload)[1] == "[Jordan (the boss)]: Write the report."


def test_a_missing_parent_logs_a_warning_and_gives_no_lines(caplog: pytest.LogCaptureFixture) -> None:
    child = _task("Write the notes", parent_task_id="no-such-parent")
    with caplog.at_level(logging.WARNING, logger="core.tasking.requests"):
        lines = format_request_for_context(request_payload_for_task(child))
    assert lines == []
    warned = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warned) == 1 and child.id in warned[0] and "no-such-parent" in warned[0]


# ─── forwarding to the children an assign or a deleg creates ───


def _working_parent(lead: Agent) -> Task:
    """A task the lead is working, created from the boss's message."""
    parent = db.create_task(
        title="Review the rubric", assigned_to=lead.id, owner_id=lead.id,
        requester_id=HUMAN_SENDER_ID, created_by=HUMAN_SENDER_ID, source_channel="chat",
        notification_policy="completion_blocked",
    )
    append_task_request(parent.id, [_human("Take the rubric review: /projects/app/Docs/rubric-review.md")])
    db.update_task(parent.id, status="accepted")
    db.update_task(parent.id, status="active")
    activity_runtime.activate_work_activity(lead.id, db.get_task(parent.id))
    return db.get_task(parent.id)


def _forwarded(payload: dict[str, Any]) -> str:
    return payload["task_request"]


def test_an_assigned_child_carries_the_request_behind_its_parent() -> None:
    lead, helper = _agent("Lead"), _agent("Hal")
    parent = _working_parent(lead)
    result = asyncio.run(_handle_delegate_task(
        lead, db.get_agent_state(lead.id),
        {"agentId": helper.id, "taskTitle": "Check the scoring rows", "taskDescription": "Rows 1-12 only."},
    ))
    assert result["event"] != "world_feedback", result
    wakes = [item for item in result["trigger_requests"] if item["trigger_type"] == "task_assigned"]
    assert len(wakes) == 1
    assert _forwarded(wakes[0]["payload"]) == (
        f'request behind the parent task "{parent.title}" (word for word, oldest first; '
        "your part is this task's description):\n"
        "[Boss]: Take the rubric review: /projects/app/Docs/rubric-review.md"
    )


def test_a_delegated_childs_wake_carries_the_request_behind_its_parent() -> None:
    lead, helper = _agent("Lead"), _agent("Hal")
    parent = _working_parent(lead)
    result = asyncio.run(_handle_delegated(
        lead, db.get_agent_state(lead.id),
        {"agentId": helper.id, "followUpMessage": "Hal is taking this over."},
    ))
    wakes = [item for item in result.get("trigger_requests", []) if item["trigger_type"] == "task_assigned"]
    assert len(wakes) == 1, result
    assert "/projects/app/Docs/rubric-review.md" in _forwarded(wakes[0]["payload"])
    assert _forwarded(wakes[0]["payload"]).startswith(f'request behind the parent task "{parent.title}"')
