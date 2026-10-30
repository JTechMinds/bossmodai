"""The turn's request scope: what a CLI command that makes a record can save.

Covers core/agent_loop/turn_request.py: outside a turn there is nothing; a
conversation turn's scope answers with the message it is answering, read
live from the trigger (so a channel line stamped after the scope opened is
included); another agent's lookup gets nothing; the scope follows the turn
into ``asyncio.to_thread``; and the work variant falls back to the bound
task's own request. Runs against the isolated test database (conftest.py).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.turn_request import bind_turn_request, turn_request_messages, turn_work_request_messages
from core.agent_loop.work_binding import bind_turn
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskRequestMessage
from core.tasking.requests import append_task_request


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


def _contents(messages: list[TaskRequestMessage]) -> list[str]:
    return [message.content for message in messages]


def test_outside_a_turn_there_is_no_request() -> None:
    assert turn_request_messages("agent-1") == []
    assert turn_work_request_messages("agent-1") == []


def test_a_human_chat_turn_answers_with_its_message() -> None:
    with bind_turn_request("agent-1", {"type": "human_chat", "content": "File the login bug.", "from_name": "Human"}):
        messages = turn_request_messages("agent-1")
    assert [message.model_dump() for message in messages] == [
        {"author_type": "human", "author_agent_id": None, "author_name": "Boss", "content": "File the login bug."},
    ]
    assert turn_request_messages("agent-1") == []


def test_a_channel_line_stamped_after_the_scope_opened_is_included() -> None:
    trigger = {
        "type": "channel_message", "content": "Team, audit the tests.", "author_type": "agent",
        "from_agent": "agent-2", "from_name": "Alex",
    }
    with bind_turn_request("agent-1", trigger):
        # What turn_context.stamp_channel_latest_line does once the turn runs.
        trigger.update({
            "latest_content": "Start with test_login.py", "latest_author_type": "human",
            "latest_from_agent": "", "latest_from_name": "Boss",
        })
        messages = turn_request_messages("agent-1")
    assert _contents(messages) == ["Team, audit the tests.", "Start with test_login.py"]


def test_another_agents_lookup_inside_the_scope_gets_nothing() -> None:
    with bind_turn_request("agent-1", {"type": "human_chat", "content": "File the login bug."}):
        assert turn_request_messages("agent-2") == []
        assert turn_work_request_messages("agent-2") == []


def test_the_scope_follows_the_turn_into_a_worker_thread() -> None:
    async def _turn() -> list[TaskRequestMessage]:
        with bind_turn_request("agent-1", {"type": "human_chat", "content": "File the login bug."}):
            return await asyncio.to_thread(turn_request_messages, "agent-1")

    assert _contents(asyncio.run(_turn())) == ["File the login bug."]


def test_a_work_turn_falls_back_to_the_bound_tasks_own_request() -> None:
    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    task = db.create_task(title="Send the Monday report", assigned_to=agent.id, created_by=HUMAN_SENDER_ID)
    append_task_request(task.id, [
        TaskRequestMessage(author_type="human", author_name="Boss", content="Every Monday, send me the report."),
    ])
    activity_runtime.activate_work_activity(agent.id, db.get_task(task.id), task_status="active")
    work_trigger = {"type": "activity_resumed", "content": "Continue.", "task_id": task.id}
    with bind_turn(agent.id, work_trigger), bind_turn_request(agent.id, work_trigger):
        # The work turn answers no message, so the backlog's lookup is empty...
        assert turn_request_messages(agent.id) == []
        # ...and a schedule set up now is the bound task's standing ask.
        assert _contents(turn_work_request_messages(agent.id)) == ["Every Monday, send me the report."]
    chat = {"type": "human_chat", "content": "Make it Tuesdays."}
    with bind_turn(agent.id, chat), bind_turn_request(agent.id, chat):
        # A message being answered wins over the bound task's request.
        assert _contents(turn_work_request_messages(agent.id)) == ["Make it Tuesdays."]
