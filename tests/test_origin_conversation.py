"""The conversation before a task, and finished work that can be found again.

Covers strategy-docs/taskOriginContext_contextgap_11112026_1794423037316.md:
the snapshot saved when a turn answering a DM or thread takes a task on
(core/agent_loop/origin_conversation.py), the window rule, when it is saved
and when it is kept, forwarding one level, the history entry every work turn
of the task shows (core/agent_loop/prompt_history.py), the task detail's
endpoint, and the finished-task rows that now carry the task id and its
files (core/tasking/finished.py) in the prompt, the CLI and the chat
snapshot. Everything runs against the isolated test database (conftest.py).
No LLM.
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
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.communication import build_communication_snapshot
from core.agent_loop.conversation_notes import entry_tokens
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.origin_conversation import (
    ORIGIN_FROM_NAME,
    ORIGIN_ID_PREFIX,
    _line,
    fit_newest,
    origin_history_entry,
    origin_view_for_task,
    save_turn_origin,
)
from core.agent_loop.prompt_history import build_prompt_history_view
from core.agent_loop.turn_context import _get_current_activity, _get_current_task
from core.agent_loop.turn_prompt import measure_instruction_tokens
from core.bm_cli.runtime import execute_bm_cli
from core.boss import boss_label
from core.context_budget import read_context_budget
from core.llm import context_builder
from core.llm.context_builder import _format_recent_completed_tasks
from core.models import Agent, Task
from core.models.message import HUMAN_SENDER_ID
from core.models.task import TaskMessage, TaskOriginConversation
from core.models.work_contract import DeliverableSpec, WorkContract
from core.tasking.finished import FINISHED_TASKS_HEADING
from core.tasking.operator_actions import hold_task_as_operator
from tests._context_budget import hold_reserve, set_context_budget


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


def _agent(name: str = "Jim") -> Agent:
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _human_says(agent: Agent, content: str):
    return db.create_message(HUMAN_SENDER_ID, agent.id, content, message_type="human")


def _agent_says(agent: Agent, content: str, to: str = HUMAN_SENDER_ID):
    return db.create_message(agent.id, to, content, message_type="social")


def _dm_trigger(message) -> dict[str, Any]:
    return {"type": "human_chat", "content": message.content, "from_name": "Human", "source_message_id": message.id}


def _accept(agent: Agent, trigger: dict[str, Any], **decision: Any) -> dict[str, Any]:
    state = db.get_agent_state(agent.id)
    assert state is not None
    return apply_decision(
        {"decision": "accept", "intentKind": "work_request", "commitmentKind": "work", "reply": "On it.", **decision},
        agent,
        state,
        trigger,
    )


def _open_task(agent: Agent, title: str) -> Task:
    tasks = [task for task in db.list_tasks(assigned_to=agent.id) if task.title == title]
    assert len(tasks) == 1, [task.title for task in tasks]
    return tasks[0]


def _said(origin: TaskOriginConversation | None) -> list[tuple[str, str, str]]:
    assert origin is not None
    return [(message.author_type, message.author_name, message.content) for message in origin.messages]


def _snapshot(task_id: str, kind: str, names: list[str], *contents: tuple[str, str]) -> TaskOriginConversation:
    """A stored snapshot: ``contents`` are ``(author, text)``, ``author`` "boss" or an agent's name."""
    messages = [
        TaskMessage(author_type="human", author_name="Boss", content=text) if author == "boss"
        else TaskMessage(author_type="agent", author_agent_id=f"id-{author}", author_name=author, content=text)
        for author, text in contents
    ]
    origin = TaskOriginConversation(
        task_id=task_id, source_kind=kind, source_names=names, messages=messages,
        captured_at=datetime.now(timezone.utc),
    )
    db.set_task_origin_conversation(origin)
    return origin


def _task(agent: Agent, title: str = "Compress the model", **fields: Any) -> Task:
    return db.create_task(title=title, assigned_to=agent.id, requester_id=HUMAN_SENDER_ID,
                          created_by=HUMAN_SENDER_ID, **fields)


def _set_boss_name(value: str) -> None:
    db.set_setting("boss_name", value, "profile")
    config.refresh_if_changed()


# ─── the model and the side table ───


def test_the_snapshot_round_trips_and_a_later_one_replaces_it() -> None:
    agent = _agent()
    task = _task(agent)
    assert db.get_task_origin_conversation(task.id) is None
    first = _snapshot(task.id, "human_dm", ["Jim"], ("boss", "Try the Q4 build."), ("Jim", "It runs in 2 ms."))
    assert db.get_task_origin_conversation(task.id) == first
    second = _snapshot(task.id, "channel", ["Compression Engine"], ("boss", "Now the unquantized one."))
    assert db.get_task_origin_conversation(task.id) == second
    # The hot paths never load it: a task row has no such field.
    assert "origin" not in db.get_task(task.id).model_dump()


@pytest.mark.parametrize(
    ("kind", "names"),
    [("human_dm", []), ("human_dm", ["Jim", "Ada"]), ("agent_dm", ["Jim"]), ("channel", ["  "])],
)
def test_the_snapshot_names_its_source_exactly(kind: str, names: list[str]) -> None:
    from pydantic import ValidationError

    message = TaskMessage(author_type="human", author_name="Boss", content="go")
    with pytest.raises(ValidationError):
        TaskOriginConversation(task_id="t", source_kind=kind, source_names=names, messages=[message],
                               captured_at=datetime.now(timezone.utc))


def test_a_snapshot_without_messages_is_refused() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="at least one message"):
        TaskOriginConversation(task_id="t", source_kind="human_dm", source_names=["Jim"], messages=[],
                               captured_at=datetime.now(timezone.utc))


# ─── capture, for each trigger kind ───


def test_a_dm_accept_saves_the_conversation_before_the_message_it_answers() -> None:
    agent = _agent()
    _human_says(agent, "Can we compress the LFM2 build?")
    _agent_says(agent, "Q4_K_XL keeps it under the 1-3 ms bar.")
    go = _human_says(agent, "go")
    result = _accept(agent, _dm_trigger(go), taskTitle="Compress the model", taskDescription="Q4_K_XL first.")
    assert result["event"] == "decision_applied", result
    task = _open_task(agent, "Compress the model")
    origin = db.get_task_origin_conversation(task.id)
    assert (origin.source_kind, origin.source_names) == ("human_dm", ["Jim"])
    # Oldest first; the trigger's own "go" is the request, not the conversation; the
    # accept reply, posted before the task was created, is the newest row.
    assert _said(origin) == [
        ("human", "Boss", "Can we compress the LFM2 build?"),
        ("agent", "Jim", "Q4_K_XL keeps it under the 1-3 ms bar."),
        ("agent", "Jim", "On it."),
    ]
    assert [message.content for message in task.request_messages] == ["go"]


def test_an_agent_dm_accept_saves_the_dm_named_by_both_agents() -> None:
    jim = _agent("Jim")
    alex = _agent("Alex")
    _agent_says(alex, "Can you profile the decoder?", to=jim.id)
    _agent_says(jim, "Which build?", to=alex.id)
    ask = _agent_says(alex, "The Q4 one.", to=jim.id)
    trigger = {
        "type": "peer_message", "content": ask.content, "from_agent": alex.id, "from_name": "Alex",
        "source_message_id": ask.id,
    }
    assert _accept(jim, trigger, taskTitle="Profile the decoder")["event"] == "decision_applied"
    origin = db.get_task_origin_conversation(_open_task(jim, "Profile the decoder").id)
    assert (origin.source_kind, origin.source_names) == ("agent_dm", ["Jim", "Alex"])
    assert _said(origin) == [("agent", "Alex", "Can you profile the decoder?"), ("agent", "Jim", "Which build?")]
    assert [message.author_agent_id for message in origin.messages] == [alex.id, jim.id]


def test_a_thread_accept_saves_the_thread_without_repeating_its_latest_line() -> None:
    jim = _agent("Jim")
    bob = _agent("Bob")
    alex = _agent("Alex")
    channel = db.create_channel(name="Compression Engine", member_agent_ids=[jim.id, bob.id, alex.id],
                                created_by=HUMAN_SENDER_ID)
    opener = db.create_channel_message(channel_id=channel.id, author_type="human", author_name="Human Operator",
                                       content="We need a survey of quantization papers.", source_channel="channel")
    db.create_channel_message(channel_id=channel.id, author_type="agent", author_agent_id=bob.id,
                              author_name="Bob", content="Three look relevant.", source_channel="channel")
    latest = "Jim, start with https://arxiv.org/"
    db.create_channel_message(channel_id=channel.id, author_type="agent", author_agent_id=alex.id,
                              author_name="Alex", content=latest, source_channel="channel")
    # What turn_context.stamp_channel_latest_line puts on the wake: Alex's line is the current message.
    trigger = {
        "type": "channel_response", "channel_id": channel.id, "channel_name": channel.name,
        "content": opener.content, "author_type": "human", "from_name": "Boss", "source_message_id": opener.id,
        "latest_content": latest, "latest_author_type": "agent", "latest_from_agent": alex.id,
        "latest_from_name": "Alex",
    }
    assert _accept(jim, trigger, taskTitle="arXiv survey")["event"] == "decision_applied"
    task = _open_task(jim, "arXiv survey")
    origin = db.get_task_origin_conversation(task.id)
    assert (origin.source_kind, origin.source_names) == ("channel", ["Compression Engine"])
    # The opener is excluded by id and the latest line by the request: both are the request. The
    # accept reply was posted before the task was created; creating it posted the thread's
    # "Created" line (a system row the thread's history shows too), so that line is the newest.
    assert _said(origin) == [
        ("agent", "Bob", "Three look relevant."),
        ("agent", "Jim", "On it."),
        ("agent", "Jim", "Jim Created: arXiv survey"),
    ]
    assert [message.content for message in task.request_messages] == [opener.content, latest]


@pytest.mark.parametrize("kind", ["human_dm", "agent_dm", "channel"])
def test_capture_reads_exactly_the_rows_the_turns_history_showed(kind: str) -> None:
    """Parity guard: capture and the turn's history map a trigger to the same conversation."""
    jim = _agent("Jim")
    alex = _agent("Alex")
    if kind == "human_dm":
        _human_says(jim, "First thought.")
        _agent_says(jim, "First answer.")
        ask = _human_says(jim, "Do it.")
        trigger = _dm_trigger(ask)
    elif kind == "agent_dm":
        _agent_says(alex, "First thought.", to=jim.id)
        _agent_says(jim, "First answer.", to=alex.id)
        ask = _agent_says(alex, "Do it.", to=jim.id)
        trigger = {"type": "peer_message", "content": "Do it.", "from_agent": alex.id, "from_name": "Alex",
                   "source_message_id": ask.id}
    else:
        channel = db.create_channel(name="Ops", member_agent_ids=[jim.id, alex.id], created_by=HUMAN_SENDER_ID)
        db.create_channel_message(channel_id=channel.id, author_type="human", author_name="Human Operator",
                                  content="First thought.", source_channel="channel")
        db.create_channel_message(channel_id=channel.id, author_type="agent", author_agent_id=alex.id,
                                  author_name="Alex", content="First answer.", source_channel="channel")
        ask = db.create_channel_message(channel_id=channel.id, author_type="human", author_name="Human Operator",
                                        content="Do it.", source_channel="channel")
        trigger = {"type": "channel_message", "channel_id": channel.id, "content": "Do it.",
                   "author_type": "human", "from_name": "Boss", "source_message_id": ask.id}
    shown = build_prompt_history_view(jim, trigger, budget=read_context_budget(), work_turn=False)
    task = _task(jim)
    save_turn_origin(jim, trigger, task.id)
    origin = db.get_task_origin_conversation(task.id)
    assert [_line(message) for message in origin.messages] == [
        f"[{row['from_name']}]: {row['content']}" for row in shown.conversation_history
    ]
    assert len(origin.messages) == 2


# ─── the window rule ───


def _messages(*contents: str) -> list[TaskMessage]:
    return [TaskMessage(author_type="human", author_name="Boss", content=text) for text in contents]


def test_fit_newest_keeps_the_newest_that_fit_and_never_leaves_a_gap() -> None:
    short_old, long_mid, short_new = _messages("old and short", "middle " * 200, "new and short")
    sizes = [entry_tokens(_line(message)) for message in (short_old, long_mid, short_new)]
    # Room for the newest and the short oldest, but not the long one between them.
    budget = sizes[2] + sizes[0] + 1
    assert budget < sizes[2] + sizes[1]
    assert fit_newest([short_old, long_mid, short_new], budget_tokens=budget) == [short_new]
    assert fit_newest([short_old, long_mid, short_new], budget_tokens=sum(sizes)) == [short_old, long_mid, short_new]
    # The newest alone over the budget keeps nothing.
    assert fit_newest([short_old, long_mid], budget_tokens=sizes[1] - 1) == []


def test_the_notes_are_kept_only_when_every_newer_row_fits() -> None:
    notes = TaskMessage(author_type="system", author_name="Conversation notes", content="Summary: Q4 chosen.")
    rows = _messages("Row one.", "Row two.")
    every = [notes, *rows]
    rows_only = sum(entry_tokens(_line(message)) for message in rows)
    assert fit_newest(every, budget_tokens=rows_only) == rows
    assert fit_newest(every, budget_tokens=rows_only + entry_tokens(_line(notes))) == every


def test_a_dm_with_notes_saves_them_first_when_everything_fits() -> None:
    from datetime import timedelta

    agent = _agent()
    first = _human_says(agent, "The old part of the talk.")
    _agent_says(agent, "Newer reply.")
    go = _human_says(agent, "go")
    assert db.claim_conversation_notes_run("human_dm", agent.id, stale_after=timedelta(minutes=5))
    assert db.commit_conversation_notes(
        "human_dm", agent.id, expected_prior_through_id=None, through_message_id=first.id,
        notes="- Boss wants the Q4 build.", source_tokens=10,
    )
    task = _task(agent)
    save_turn_origin(agent, _dm_trigger(go), task.id)
    origin = db.get_task_origin_conversation(task.id)
    assert _said(origin)[0][:2] == ("system", "Conversation notes")
    assert "- Boss wants the Q4 build." in origin.messages[0].content
    assert _said(origin)[1:] == [("agent", "Jim", "Newer reply.")]


def test_rows_past_the_budget_are_dropped_and_the_newest_kept() -> None:
    hold_reserve()
    budget = set_context_budget(usable=20_000, origin=1)  # 200 tokens
    agent = _agent()
    for index in range(12):
        _human_says(agent, f"line {index}: " + "the quantized build must stay fast " * 3)
    go = _human_says(agent, "go")
    task = _task(agent)
    save_turn_origin(agent, _dm_trigger(go), task.id)
    origin = db.get_task_origin_conversation(task.id)
    kept = [message.content for message in origin.messages]
    assert kept and kept[-1].startswith("line 11:") and len(kept) < 12
    assert sum(entry_tokens(_line(message)) for message in origin.messages) <= budget.origin


def test_the_newest_row_alone_over_the_budget_saves_nothing_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    hold_reserve()
    set_context_budget(usable=10_000, origin=1)  # 100 tokens
    agent = _agent()
    _human_says(agent, "a long paste " * 200)
    go = _human_says(agent, "go")
    task = _task(agent)
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.origin_conversation"):
        save_turn_origin(agent, _dm_trigger(go), task.id)
    assert db.get_task_origin_conversation(task.id) is None
    (warning,) = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert task.id in warning and "100-token" in warning


def test_a_malformed_row_is_left_out_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    agent = _agent()
    _human_says(agent, "Keep this one.")
    blank = _human_says(agent, "   ")
    go = _human_says(agent, "go")
    task = _task(agent)
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.origin_conversation"):
        save_turn_origin(agent, _dm_trigger(go), task.id)
    assert _said(db.get_task_origin_conversation(task.id)) == [("human", "Boss", "Keep this one.")]
    (warning,) = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert "human_chat" in warning and blank.id in warning


# ─── when it is saved ───


def test_a_defer_saves_the_conversation() -> None:
    agent = _agent()
    _human_says(agent, "Look at the decoder after lunch.")
    later = _human_says(agent, "later is fine")
    state = db.get_agent_state(agent.id)
    result = apply_decision(
        {"decision": "defer", "intentKind": "work_request", "commitmentKind": "work",
         "taskTitle": "Look at the decoder", "reply": "After lunch."},
        agent, state, _dm_trigger(later),
    )
    assert result["event"] == "decision_applied", result
    origin = db.get_task_origin_conversation(_open_task(agent, "Look at the decoder").id)
    assert ("human", "Boss", "Look at the decoder after lunch.") in _said(origin)


def test_a_bind_by_title_saves_the_conversation_on_the_existing_task() -> None:
    agent = _agent()
    existing = _task(agent, "Write the memo", owner_id=agent.id)
    _human_says(agent, "Cover seat minimums.")
    go = _human_says(agent, "go on the memo")
    assert _accept(agent, _dm_trigger(go), taskTitle="Write the memo")["event"] == "decision_applied"
    assert _open_task(agent, "Write the memo").id == existing.id
    assert ("human", "Boss", "Cover seat minimums.") in _said(db.get_task_origin_conversation(existing.id))


def test_a_later_accept_by_id_replaces_the_snapshot_and_an_empty_one_keeps_it() -> None:
    jim = _agent("Jim")
    alex = _agent("Alex")
    _human_says(jim, "Survey the papers.")
    first = _human_says(jim, "go")
    _accept(jim, _dm_trigger(first), taskTitle="arXiv survey")
    task = _open_task(jim, "arXiv survey")
    before = db.get_task_origin_conversation(task.id)
    assert ("human", "Boss", "Survey the papers.") in _said(before)

    # A later accept by id from the same DM: the newer conversation replaces it.
    _human_says(jim, "Add the unquantized model too.")
    again = _human_says(jim, "go again")
    assert _accept(jim, _dm_trigger(again), taskId=task.id, taskTitle=task.title)["event"] == "decision_applied"
    replaced = db.get_task_origin_conversation(task.id)
    assert ("human", "Boss", "Add the unquantized model too.") in _said(replaced)
    assert replaced.captured_at >= before.captured_at

    # An accept by id whose conversation holds nothing but its request keeps it.
    ask = _agent_says(alex, "Please finish the survey.", to=jim.id)
    peer = {"type": "peer_message", "content": ask.content, "from_agent": alex.id, "from_name": "Alex",
            "source_message_id": ask.id}
    assert _accept(jim, peer, taskId=task.id, taskTitle=task.title)["event"] == "decision_applied"
    assert db.get_task_origin_conversation(task.id) == replaced


def test_a_held_task_saves_nothing() -> None:
    agent = _agent()
    task = _task(agent, "Draft the post")
    hold_task_as_operator(task.id, note=None)
    _human_says(agent, "Draft it now.")
    go = _human_says(agent, "go")
    result = _accept(agent, _dm_trigger(go), taskId=task.id, taskTitle=task.title)
    assert result.get("event") == "world_feedback", result
    assert db.get_task_origin_conversation(task.id) is None


@pytest.mark.parametrize(
    "trigger_type",
    ["task_assigned", "task_follow_up", "activity_resumed", "session_message", "session_response"],
)
def test_a_turn_that_answers_no_dm_or_thread_saves_nothing(trigger_type: str) -> None:
    agent = _agent()
    _human_says(agent, "Something said in the DM.")
    task = _task(agent)
    save_turn_origin(agent, {"type": trigger_type, "task_id": task.id, "content": "Resume.",
                             "session_id": "s-1", "author_type": "human"}, task.id)
    assert db.get_task_origin_conversation(task.id) is None


# ─── origin_view_for_task and origin_history_entry ───


def test_the_view_shows_the_tasks_own_snapshot() -> None:
    agent = _agent()
    task = _task(agent)
    _snapshot(task.id, "human_dm", ["Jim"], ("boss", "Try Q4."), ("Jim", "2 ms."))
    view = origin_view_for_task(db.get_task(task.id), budget_tokens=10_000)
    assert view == {
        "task_id": task.id,
        "parent_title": None,
        "where": "the DM between Boss and Jim",
        "messages": [
            {"author_type": "human", "author_agent_id": None, "author_name": "Boss", "content": "Try Q4."},
            {"author_type": "agent", "author_agent_id": "id-Jim", "author_name": "Jim", "content": "2 ms."},
        ],
    }


def test_a_child_shows_its_parents_one_level_only() -> None:
    agent = _agent()
    parent = _task(agent, "Compress the model")
    _snapshot(parent.id, "channel", ["Compression Engine"], ("boss", "Start with Q4."))
    child = _task(agent, "Profile Q4", parent_task_id=parent.id)
    grandchild = _task(agent, "Profile the decoder", parent_task_id=child.id)
    view = origin_view_for_task(db.get_task(child.id), budget_tokens=10_000)
    assert (view["task_id"], view["parent_title"], view["where"]) == (
        parent.id, "Compress the model", 'the thread "Compression Engine"',
    )
    assert origin_view_for_task(db.get_task(grandchild.id), budget_tokens=10_000) is None
    # A child with its own snapshot shows its own.
    _snapshot(child.id, "agent_dm", ["Jim", "Alex"], ("Alex", "Profile it."))
    own = origin_view_for_task(db.get_task(child.id), budget_tokens=10_000)
    assert (own["parent_title"], own["where"]) == (None, "the DM between Jim and Alex")


def test_a_missing_parent_logs_a_warning_and_shows_nothing(caplog: pytest.LogCaptureFixture) -> None:
    agent = _agent()
    orphan = _task(agent, "Orphan", parent_task_id="no-such-task")
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.origin_conversation"):
        assert origin_view_for_task(orphan, budget_tokens=10_000) is None
    assert any("no-such-task" in record.getMessage() for record in caplog.records)


def test_the_view_is_refitted_to_a_lowered_budget() -> None:
    agent = _agent()
    task = _task(agent)
    _snapshot(task.id, "human_dm", ["Jim"], ("boss", "old " * 50), ("Jim", "newest"))
    newest = TaskMessage(author_type="agent", author_agent_id="id-Jim", author_name="Jim", content="newest")
    view = origin_view_for_task(task, budget_tokens=entry_tokens(_line(newest)))
    assert [message["content"] for message in view["messages"]] == ["newest"]
    assert origin_view_for_task(task, budget_tokens=1) is None


def test_the_history_entry_has_the_exact_headings_and_lines() -> None:
    agent = _agent()
    parent = _task(agent, "Compress the model")
    _snapshot(parent.id, "human_dm", ["Jim"], ("boss", "Try Q4."), ("Jim", "2 ms."))
    child = _task(agent, "Profile Q4", parent_task_id=parent.id)
    # Saved as "Boss"; shown with the boss label as it is now.
    _set_boss_name("Jordan")
    boss = boss_label()
    assert boss != "Boss"

    own = origin_history_entry(parent.id, budget_tokens=10_000)
    assert own == {
        "id": f"{ORIGIN_ID_PREFIX}{parent.id}",
        "from_agent": "__system__",
        "from_name": ORIGIN_FROM_NAME,
        "content": (
            f"The conversation in the DM between {boss} and Jim that this task came from, as it stood when the "
            "task was taken on (oldest first). The request itself is under Current Task.\n"
            f"[{boss}]: Try Q4.\n[Jim]: 2 ms."
        ),
    }
    forwarded = origin_history_entry(child.id, budget_tokens=10_000)
    assert forwarded["content"].split("\n")[0] == (
        f'The conversation in the DM between {boss} and Jim that the parent task "Compress the model" came from, '
        "as it stood when that task was taken on (oldest first). Your part is this task's description."
    )
    assert origin_history_entry("no-such-task", budget_tokens=10_000) is None


# ─── what a work turn shows ───


def _working(agent: Agent) -> Task:
    task = _task(agent)
    db.create_task_event(task_id=task.id, author_type="human", author_name="Human Operator",
                         event_type="comment", content="Keep it under 3 ms.")
    assert activity_runtime.activate_work_activity(agent.id, task, task_status="active") is not None
    return task


def test_a_work_turn_shows_the_conversation_first_then_the_task_thread() -> None:
    agent = _agent()
    task = _working(agent)
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Continue."}
    budget = read_context_budget()
    without = build_prompt_history_view(agent, trigger, budget=budget, work_turn=True).conversation_history
    _snapshot(task.id, "human_dm", ["Jim"], ("boss", "Try Q4."))
    with_entry = build_prompt_history_view(agent, trigger, budget=budget, work_turn=True).conversation_history
    # Prepended after the thread was finished: the thread and its sticky slots are unchanged.
    assert with_entry == [origin_history_entry(task.id, budget_tokens=budget.origin), *without]
    assert with_entry[0]["id"] == f"{ORIGIN_ID_PREFIX}{task.id}"
    assert any("Keep it under 3 ms." in row["content"] for row in without)


@pytest.mark.parametrize("trigger_type", ["host_path_consent_resolved", "cli_approval_resolved"])
def test_a_consent_or_approval_work_resume_shows_it_too(trigger_type: str) -> None:
    agent = _agent()
    task = _working(agent)
    _snapshot(task.id, "human_dm", ["Jim"], ("boss", "Try Q4."))
    trigger = {"type": trigger_type, "task_id": task.id, "payload": {"status": "allowed"}}
    budget = read_context_budget()
    work = build_prompt_history_view(agent, trigger, budget=budget, work_turn=True).conversation_history
    chat = build_prompt_history_view(agent, trigger, budget=budget, work_turn=False).conversation_history
    assert work[0]["from_name"] == ORIGIN_FROM_NAME and work[1:] == chat


def test_chat_turns_and_non_work_resumes_never_show_it() -> None:
    agent = _agent()
    task = _working(agent)
    _snapshot(task.id, "human_dm", ["Jim"], ("boss", "Try Q4."))
    _human_says(agent, "Status?")
    budget = read_context_budget()
    for trigger, work_turn in (
        ({"type": "activity_resumed", "task_id": task.id, "content": "Continue."}, False),
        ({"type": "human_chat", "content": "Status?"}, False),
        ({"type": "task_assigned", "task_id": task.id}, False),
    ):
        history = build_prompt_history_view(agent, trigger, budget=budget, work_turn=work_turn).conversation_history
        assert all(not str(row.get("id") or "").startswith(ORIGIN_ID_PREFIX) for row in history), trigger


def test_the_entry_is_fitted_to_the_origin_share() -> None:
    hold_reserve()
    budget = set_context_budget(usable=20_000, origin=1)  # 200 tokens
    agent = _agent()
    task = _working(agent)
    _snapshot(task.id, "human_dm", ["Jim"], *[("boss", f"line {index}: " + "keep it fast " * 8) for index in range(10)])
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Continue."}
    entry = build_prompt_history_view(agent, trigger, budget=budget, work_turn=True).conversation_history[0]
    lines = entry["content"].split("\n")[1:]
    assert lines and lines[-1].startswith("[Boss]: line 9:") and len(lines) < 10
    assert sum(entry_tokens(line) for line in lines) <= budget.origin


def test_the_entry_never_counts_toward_the_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.llm.context_builder.now_local", lambda: datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc))
    agent = _agent()
    task = _working(agent)
    _snapshot(task.id, "human_dm", ["Jim"], ("boss", "Try Q4."), ("Jim", "2 ms."))
    trigger = {"type": "activity_resumed", "task_id": task.id, "content": "Continue."}
    history = build_prompt_history_view(agent, trigger, budget=read_context_budget(), work_turn=True).conversation_history
    assert history[0]["from_name"] == ORIGIN_FROM_NAME

    def measured(rows: list[dict[str, Any]]) -> int:
        context = context_builder.build_context(context_builder.TurnContext(
            agent=agent, state=db.get_agent_state(agent.id), trigger=trigger, conversation_history=rows,
            prompt_notifications=[], reference_materials=[], current_activity=_get_current_activity(agent.id),
            current_task=_get_current_task(agent.id), contract_kind="execution", paused_work_view=None,
        ))
        return measure_instruction_tokens(context, history_count=len(rows), paused_work=None)

    assert measured(history) == measured(history[1:])


# ─── the task detail's endpoint ───


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def test_the_origin_endpoint_answers_none_own_forwarded_and_unknown() -> None:
    client, headers = _client()
    agent = _agent()
    parent = _task(agent, "Compress the model")
    child = _task(agent, "Profile Q4", parent_task_id=parent.id)
    url = "/api/tasks/{}/origin-conversation"

    assert client.get(url.format("no-such-task"), headers=headers).status_code == 404
    none = client.get(url.format(parent.id), headers=headers)
    assert none.status_code == 200 and none.json() == {"origin": None}

    _snapshot(parent.id, "human_dm", ["Jim"], ("boss", "Try Q4."))
    own = client.get(url.format(parent.id), headers=headers).json()["origin"]
    assert own == origin_view_for_task(db.get_task(parent.id), budget_tokens=read_context_budget().origin)
    assert own["parent_title"] is None and own["messages"][0]["content"] == "Try Q4."
    forwarded = client.get(url.format(child.id), headers=headers).json()["origin"]
    assert (forwarded["task_id"], forwarded["parent_title"]) == (parent.id, "Compress the model")


def test_the_origin_endpoint_says_where_to_fix_invalid_context_settings(caplog: pytest.LogCaptureFixture) -> None:
    client, headers = _client()
    task = _task(_agent())
    db.set_setting("context_reply_percent", "0", "context")
    config.reload()
    with caplog.at_level(logging.ERROR, logger="api.routes.tasks"):
        res = client.get(f"/api/tasks/{task.id}/origin-conversation", headers=headers)
    assert res.status_code == 500
    assert res.json()["detail"] == "The Context Window settings are invalid. Fix them in Settings → Context Window."
    assert any(record.levelno == logging.ERROR for record in caplog.records)


# ─── finished work is findable ───


_SURVEY_SUMMARY = (
    "Surveyed 14 papers on post-training quantization for small LMs. The LFM2 Q4_K_XL build is the only one "
    "that keeps decode under the 1-3 ms bar on the target; the full ranking and the verdict are in the report."
)


def _finished(agent: Agent, title: str = "arXiv survey", *, files: list[str] = (), summary: str = _SURVEY_SUMMARY):
    contract = WorkContract(deliverables=[DeliverableSpec(type="file", path=path) for path in files]) if files else None
    task = _task(agent, title, work_contract=contract)
    assert db.update_task(task.id, status="active") is not None
    updated = db.update_task(task.id, status="complete", completion_summary=summary)
    assert updated is not None and updated.status == "complete"
    return updated


def test_recent_completed_rows_carry_their_work_contract() -> None:
    agent = _agent()
    with_files = _finished(agent, files=["/projects/jtech/reports/research-survey.md"])
    without = _finished(agent, "Quick check", summary="Fine.")
    rows = {row["id"]: row for row in db.get_recent_completed_tasks(agent.id, limit=5)}
    assert json.loads(rows[with_files.id]["work_contract"])["deliverables"][0]["path"] == (
        "/projects/jtech/reports/research-survey.md"
    )
    assert rows[without.id]["work_contract"] is None


def test_the_prompts_finished_rows_carry_the_id_and_files_and_cut_at_160() -> None:
    agent = _agent()
    task = _finished(agent, files=["/projects/jtech/reports/research-survey.md", "/projects/jtech/ranking.csv"])
    lines = _format_recent_completed_tasks(agent.id).split("\n")
    assert lines[:2] == [FINISHED_TASKS_HEADING, "task id | finished | status | task name | files | summary"]
    cells = lines[2].split(" | ")
    assert cells[0] == task.id and cells[2:5] == [
        "complete", "arXiv survey", "/projects/jtech/reports/research-survey.md, /projects/jtech/ranking.csv",
    ]
    assert cells[5] == _SURVEY_SUMMARY[:157] + "..."
    assert _format_recent_completed_tasks(_agent("Ada").id).split("\n")[2:] == ["none"]


def test_the_cli_finished_rows_carry_the_id_and_files_and_cut_at_240() -> None:
    agent = _agent()
    long_summary = _SURVEY_SUMMARY + " " + _SURVEY_SUMMARY
    task = _finished(agent, files=["/projects/jtech/reports/research-survey.md"], summary=long_summary)
    _finished(agent, "No files", summary="")
    for command in ("tasks", "recent-work", "status"):
        result = execute_bm_cli(agent, db.get_agent_state(agent.id), command, None)
        assert result.ok, command
        lines = result.prompt_content.split("\n")
        header = lines.index("task id | finished | status | task name | files | summary")
        rows = {line.split(" | ")[0]: line.split(" | ") for line in lines[header + 1: header + 3]}
        assert rows[task.id][4] == "/projects/jtech/reports/research-survey.md", command
        assert rows[task.id][5] == long_summary[:237] + "...", command
        bare = next(cells for task_id, cells in rows.items() if task_id != task.id)
        assert bare[4:] == ["-", "-"], command


def test_task_detail_lists_the_deliverables() -> None:
    agent = _agent()
    task = _finished(agent, files=["/projects/jtech/reports/research-survey.md"])
    lines = execute_bm_cli(agent, db.get_agent_state(agent.id), f"task {task.id}", None).prompt_content.split("\n")
    at = lines.index("deliverables:")
    assert lines[at + 1] == "- file: /projects/jtech/reports/research-survey.md"
    assert lines.index(f"description: {task.description or '-'}") < at
    plain = _finished(agent, "No files")
    assert "deliverables:" not in execute_bm_cli(
        agent, db.get_agent_state(agent.id), f"task {plain.id}", None,
    ).prompt_content


def test_the_chat_snapshots_finished_rows_carry_their_files() -> None:
    agent = _agent()
    task = _finished(agent, files=["/projects/jtech/reports/research-survey.md"])
    snapshot = build_communication_snapshot(
        agent=agent, state=db.get_agent_state(agent.id), trigger={"type": "human_chat", "content": "Status?"},
    )
    (row,) = snapshot["recent_completed_tasks"]
    assert (row["task_id"], row["files"]) == (task.id, ["/projects/jtech/reports/research-survey.md"])
