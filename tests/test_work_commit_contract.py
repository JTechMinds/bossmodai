"""Fix B — ``work_commit`` is a required, structural field on every reply.

A reply cannot promise work it does not schedule: without live work, a
committed reply is repaired in-turn (nothing is posted) unless it is on the
agent's own open task, which is then requeued exactly. No prose is read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from tests._connections import model_connection
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.decision_contract import (
    REPLY_WORK_COMMIT_REQUIRED,
    TASK_ID_NOT_OPEN,
    WORK_COMMIT_CANNOT_START,
    WORK_COMMIT_STARTS_NOTHING,
    ConversationDecision,
    allowed_conversation_acts_for_trigger,
    parse_direct_turn_response,
    validate_decision_for_trigger,
)
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.loop import run_turn
from core.llm.client import LLMResponse
from core.llm.context_builder import _format_trigger
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking import create_or_bind_task
from core.default_prompts import load_default_prompt
from core.tasking.transitions import transition_task
from db.settings import reconcile_work_commit_prompt_contract, seed_defaults


def setup_function() -> None:
    runtime_events.set_sink(NullRuntimeEventSink())
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


def _task(agent_id: str, *, title: str, requester_id: str = HUMAN_SENDER_ID):
    return create_or_bind_task(
        title=title,
        description="Do the work.",
        project=None,
        assigned_to=agent_id,
        requester_id=requester_id,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    ).task


def _script(monkeypatch: pytest.MonkeyPatch, contents: list[str], *, on_call=None) -> list[list[dict[str, str]]]:
    queue = list(contents)
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append(list(kwargs.get("messages") or []))
        if on_call is not None:
            on_call(len(prompts))
        return LLMResponse(
            content=queue.pop(0), model="test/mock", prompt_tokens=8, completion_tokens=4, total_tokens=12
        )

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return prompts


def _human_chat(content: str = "Can you start the migration?") -> dict[str, Any]:
    return {
        "type": "human_chat",
        "content": content,
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "chat",
    }


def _agent_messages(agent_id: str) -> list[Any]:
    return [
        message
        for message in db.get_agent_direct_thread(agent_id, HUMAN_SENDER_ID, limit=50)
        if message.from_agent == agent_id
    ]


def test_reply_without_work_commit_fails_the_shape() -> None:
    parsed = parse_direct_turn_response('{"act":"reply","intent":"status","msg":"Status is green.","th":"s"}')
    assert parsed["decision"] == "_parse_failed"
    assert REPLY_WORK_COMMIT_REQUIRED in parsed["_raw_snippet"]
    say_only = parse_direct_turn_response('{"say":"Status is green.","actions":[]}')
    assert say_only["decision"] == "_parse_failed"
    with pytest.raises(ValueError, match="work_commit"):
        ConversationDecision.model_validate(
            {"decision": "answer", "intentKind": "status_request", "reply": "Green."}
        )
    # Non-reply acts do not carry it.
    clarify = parse_direct_turn_response('{"act":"clarify","intent":"work","msg":"Which repo?","th":"q"}')
    assert clarify["decision"] == "clarify"


@pytest.mark.asyncio
async def test_reply_without_work_commit_is_repaired_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Debra", role="Analyst", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    prompts = _script(
        monkeypatch,
        [
            '{"say":"All green.","actions":[]}',
            '{"say":"All green.","actions":[],"work_commit":false}',
        ],
    )
    outcome = await run_turn(agent, state, _human_chat("Status?"))
    assert len(prompts) == 2
    repair = "\n".join(item["content"] for item in prompts[1] if item["role"] == "system")
    assert REPLY_WORK_COMMIT_REQUIRED in repair
    assert outcome.trigger_status == "completed"
    assert [message.content for message in _agent_messages(agent.id)] == ["All green."]


@pytest.mark.asyncio
async def test_committed_reply_with_no_live_work_is_repaired_and_nothing_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Charles", role="Build Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    posted_before_repair: list[int] = []

    def _count_posts(call: int) -> None:
        if call == 2:
            posted_before_repair.append(len(_agent_messages(agent.id)))

    prompts = _script(
        monkeypatch,
        [
            '{"act":"reply","intent":"work","msg":"Starting the migration now.","work_commit":true,"th":"go"}',
            '{"act":"accept","intent":"work","msg":"On it.","commit":"work",'
            '"data":{"task":{"title":"Run the migration","desc":"Run it."}},"th":"accept"}',
        ],
        on_call=_count_posts,
    )
    outcome = await run_turn(agent, state, _human_chat())
    assert len(prompts) == 2
    assert posted_before_repair == [0], "the invalid reply must not post before it is repaired"
    repair = "\n".join(item["content"] for item in prompts[1] if item["role"] == "system")
    assert WORK_COMMIT_STARTS_NOTHING in repair
    assert outcome.trigger_status == "completed"
    assert any(
        item.get("trigger_type") == "activity_resumed" for item in outcome.result["trigger_requests"]
    ), "accept + commit=work schedules the first execution turn"


def test_committed_reply_is_valid_while_work_is_live() -> None:
    agent = db.create_agent("Charles", role="Build Engineer")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "Continuing.", "workCommit": True}
    )
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="human_chat",
            active_task_id="t1",
            has_live_work=True,
            agent_id=agent.id,
            trigger=_human_chat("Status?"),
        )
        is None
    )


def test_committed_reply_on_own_waiting_task_requeues_exactly_that_task() -> None:
    agent = db.create_agent("Jim", role="Engineer")
    peer = db.create_agent("Laura", role="Reviewer")
    waiting = _task(agent.id, title="Ship the notes")
    other = _task(agent.id, title="Unrelated waiting work")
    activity_runtime.activate_work_activity(agent.id, other, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Waiting on design.", task_status="waiting")
    activity_runtime.activate_work_activity(agent.id, waiting, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Waiting on Laura.", task_status="waiting")
    assert activity_runtime.get_active_work_activity(agent.id) is None
    trigger = {
        "type": "task_follow_up",
        "task_id": waiting.id,
        "task_status": "waiting",
        "task_party": "assignee",
        "attention_kind": "note",
        "from_agent": peer.id,
        "from_name": peer.name,
        "content": "Infra is back, go ahead.",
        "source_channel": "work",
    }
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "Thanks, continuing now.", "workCommit": True}
    )
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="task_follow_up",
            active_task_id=None,
            has_live_work=False,
            agent_id=agent.id,
            trigger=trigger,
        )
        is None
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(decision.model_dump(), agent, state, trigger)
    resumes = [item for item in result["trigger_requests"] if item.get("trigger_type") == "activity_resumed"]
    assert [item["task_id"] for item in resumes] == [waiting.id]
    assert db.get_task(other.id).status == "waiting"


def test_committed_reply_on_someone_elses_task_cannot_start_work() -> None:
    agent = db.create_agent("Laura", role="Reviewer")
    owner = db.create_agent("Jim", role="Engineer")
    task = _task(owner.id, title="Ship the notes", requester_id=agent.id)
    transition_task(task.id, "accepted", reason="test", actor="test")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "I'll handle it.", "workCommit": True}
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="task_follow_up",
        active_task_id=None,
        has_live_work=False,
        agent_id=agent.id,
        trigger={"type": "task_follow_up", "task_id": task.id, "task_status": "accepted", "task_party": "stakeholder"},
    )
    assert error == WORK_COMMIT_CANNOT_START


def test_uncommitted_reply_never_requeues_work() -> None:
    agent = db.create_agent("Jim", role="Engineer")
    blocked = _task(agent.id, title="Blocked work")
    activity_runtime.activate_work_activity(agent.id, blocked, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Blocked on infra.", task_status="waiting")
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(
        {"decision": "answer", "intentKind": "social_request", "reply": "Morning!", "workCommit": False},
        agent,
        state,
        _human_chat("Morning"),
    )
    assert result["trigger_requests"] == []


@pytest.mark.asyncio
async def test_validation_repair_respects_the_shared_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    db.set_setting("decision_repair_attempts", "1", "llm")
    config.reload()
    agent = db.create_agent("Debra", role="Analyst", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    committed = '{"act":"reply","intent":"work","msg":"Starting now.","work_commit":true,"th":"go"}'
    prompts = _script(monkeypatch, [committed, committed])
    outcome = await run_turn(agent, state, _human_chat())
    assert len(prompts) == 2, "one repair, then the budget is spent"
    assert outcome.trigger_status == "failed"
    assert outcome.diagnostic_error == WORK_COMMIT_STARTS_NOTHING
    assert _agent_messages(agent.id) == []


_PROMPT_KEYS = ("system_prompt_template", "runtime_contract_decision")
_MARKER = "work_commit_prompt_contract_reconciled"


def _stored(key: str) -> str:
    row = db.query_one("SELECT value FROM settings WHERE key = $1", [key])
    assert row is not None
    return str(row["value"])


def test_fresh_db_seeds_the_current_contract_and_marks_it() -> None:
    for key in _PROMPT_KEYS:
        assert _stored(key) == load_default_prompt(key)
    assert _stored(_MARKER) == "true"


def test_old_prompt_rows_are_overwritten_once() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [_MARKER])
    for key in _PROMPT_KEYS:
        db.execute("UPDATE settings SET value = $1 WHERE key = $2", ["old contract text", key])
    seed_defaults()
    for key in _PROMPT_KEYS:
        assert _stored(key) == load_default_prompt(key)
    assert "TURN MODEL" in _stored("runtime_contract_decision")


def test_a_later_operator_edit_survives_the_next_run() -> None:
    db.set_setting("runtime_contract_decision", "operator's own contract", "advanced")
    seed_defaults()
    reconcile_work_commit_prompt_contract()
    assert _stored("runtime_contract_decision") == "operator's own contract"


def test_committed_reply_resumes_paused_work_with_no_task_on_the_trigger() -> None:
    agent = db.create_agent("Brad", role="Engineer")
    waiting = _task(agent.id, title="BV cursor test: guest appraisal flow")
    activity_runtime.activate_work_activity(agent.id, waiting, task_status="active")
    activity_runtime.pause_active_work(agent.id, "Waiting on hotfixes.", task_status="waiting")
    assert activity_runtime.get_active_work_activity(agent.id) is None
    trigger = _human_chat("Made some hotfixes. Let's try running an appraisal.")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "work_request", "reply": "Picking it back up now.", "workCommit": True}
    )
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="human_chat",
            active_task_id=None,
            has_live_work=False,
            agent_id=agent.id,
            trigger=trigger,
        )
        is None
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    result = apply_decision(decision.model_dump(), agent, state, trigger)
    resumes = [item for item in result["trigger_requests"] if item.get("trigger_type") == "activity_resumed"]
    assert [item["task_id"] for item in resumes] == [waiting.id]
    live = activity_runtime.get_active_work_activity(agent.id)
    assert live is not None and live.task_id == waiting.id


def test_committed_reply_with_no_open_task_fails_with_the_new_copy() -> None:
    agent = db.create_agent("Brad", role="Engineer")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "work_request", "reply": "On it.", "workCommit": True}
    )
    error = validate_decision_for_trigger(
        decision,
        trigger_type="human_chat",
        active_task_id=None,
        has_live_work=False,
        agent_id=agent.id,
        trigger=_human_chat(),
    )
    assert error == WORK_COMMIT_STARTS_NOTHING
    assert "you have no open task to continue" in error
    assert "do not say you are starting work" in error
    assert "starts nothing — this is your only turn" not in error


_RESUME_MARKER = "work_commit_resume_prompt_reconciled"


def test_shipped_decision_contract_resumes_open_work_and_revises_by_id() -> None:
    text = load_default_prompt("runtime_contract_decision")
    assert "`true` when this reply commits to continuing your own open work (active, paused, or waiting)." in text
    assert "On your own waiting or blocked task's thread" not in text
    assert "`true` with no active work starts nothing" not in text
    assert '"id":"string (one of your open task ids, to revise that task)"' in text
    assert "When new work arrives: if it changes a task you already have, accept it with that task's id" in text
    assert "the runtime will pause the older task automatically" not in text
    channel_block = text.split("{{elseif trigger.type = 'channel_message'}}", 1)[1].split("{{elseif", 1)[0]
    assert "ALLOWED conversation act FOR THIS TURN: observe | reply | accept | clarify | decline | defer" in channel_block
    dm_block = text.split("{{if trigger.type = 'human_chat'}}", 1)[1].split("{{elseif", 1)[0]
    dm_defer = dm_block.split("For defer:", 1)[1]
    assert '"act":"defer"' in dm_defer
    assert '"data":{"task":{"title":"string","desc":"string"}}' in dm_defer
    assert (
        "The one exception is your own open work (active, paused, or waiting): the runtime continues it "
        "after you answer when you set `work_commit` true."
    ) in text
    assert "The one exception is work already active on your Board" not in text


_RESUME_KEYS = ("runtime_contract_decision", "runtime_block_trigger_event")


def test_resume_prompt_reconcile_runs_once() -> None:
    assert _stored(_RESUME_MARKER) == "true"
    for key in _RESUME_KEYS:
        assert _stored(key) == load_default_prompt(key)
    db.execute("DELETE FROM settings WHERE key = $1", [_RESUME_MARKER])
    for key in _RESUME_KEYS:
        db.execute("UPDATE settings SET value = $1 WHERE key = $2", ["old prompt text", key])
    seed_defaults()
    for key in _RESUME_KEYS:
        assert _stored(key) == load_default_prompt(key)
    assert "One line is enough" not in _stored("runtime_block_trigger_event")
    assert _stored(_RESUME_MARKER) == "true"
    for key in _RESUME_KEYS:
        db.set_setting(key, "operator's own text", "advanced")
    seed_defaults()
    for key in _RESUME_KEYS:
        assert _stored(key) == "operator's own text"


_TASK_UPDATE_MARKER = "task_update_prompts_reconciled"


def test_task_update_prompt_reconcile_replaces_stale_rows_once() -> None:
    from db.settings import get_seed_setting_default

    db.execute("DELETE FROM settings WHERE key = $1", [_TASK_UPDATE_MARKER])
    for key in _RESUME_KEYS:
        db.execute("UPDATE settings SET value = $1 WHERE key = $2", [f"stale {key} without the stakeholder branch", key])
    seed_defaults()
    for key in _RESUME_KEYS:
        assert _stored(key) == load_default_prompt(key)
        seeded = get_seed_setting_default(key)
        assert seeded is not None
        row = db.query_one("SELECT category FROM settings WHERE key = $1", [key])
        assert row is not None and row["category"] == seeded[1]
    assert "{{if trigger.task_party = 'stakeholder'}}" in _stored("runtime_contract_decision")
    assert _stored(_TASK_UPDATE_MARKER) == "true"


def test_task_update_prompt_reconcile_keeps_operator_edits_after_marker() -> None:
    assert _stored(_TASK_UPDATE_MARKER) == "true"
    for key in _RESUME_KEYS:
        db.set_setting(key, f"operator's own {key}", "advanced")
    seed_defaults()
    for key in _RESUME_KEYS:
        assert _stored(key) == f"operator's own {key}"


def _accept_by_id(task_id: str, desc: str) -> dict[str, Any]:
    raw = (
        '{"act":"accept","intent":"work","msg":"Revising that task.","commit":"work",'
        f'"data":{{"task":{{"id":"{task_id}","desc":"{desc}"}}}},"th":"revise"}}'
    )
    parsed = parse_direct_turn_response(raw)
    assert parsed["decision"] == "accept", parsed
    return parsed


def test_accept_with_a_task_id_revises_and_resumes_that_task() -> None:
    agent = db.create_agent("Charles", role="Engineer")
    first = _task(agent.id, title="Write the notes")
    activity_runtime.activate_work_activity(agent.id, first, task_status="active")
    second = _task(agent.id, title="Fix the build")
    activity_runtime.activate_work_activity(agent.id, second, task_status="active")
    assert db.get_task(first.id).status == "pending"
    assert db.get_resumable_work_activity(agent.id, first.id) is not None
    tasks_before = len(db.list_tasks(assigned_to=agent.id))

    parsed = _accept_by_id(first.id, "Cover the Q3 numbers too.")
    assert parsed["taskId"] == first.id
    decision = ConversationDecision.model_validate(parsed)
    trigger = _human_chat("Charles, the notes should cover Q3 too.")
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="human_chat",
            active_task_id=second.id,
            has_live_work=True,
            agent_id=agent.id,
            trigger=trigger,
        )
        is None
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    apply_decision(parsed, agent, state, trigger)
    assert len(db.list_tasks(assigned_to=agent.id)) == tasks_before
    revised = db.get_task(first.id)
    assert revised.description == "Cover the Q3 numbers too."
    assert any(
        event.content == "Revised: Cover the Q3 numbers too."
        for event in db.list_task_events(first.id)
    )
    live = activity_runtime.get_active_work_activity(agent.id)
    assert live is not None and live.task_id == first.id
    assert db.get_resumable_work_activity(agent.id, second.id) is not None


def test_task_id_must_be_one_of_your_open_tasks() -> None:
    agent = db.create_agent("Charles", role="Engineer")
    other = db.create_agent("Debra", role="Analyst")
    theirs = _task(other.id, title="Debra's review")
    closed = _task(agent.id, title="Old notes")
    transition_task(closed.id, "accepted", reason="test", actor="test")
    transition_task(closed.id, "complete", reason="test", actor="test")
    for task_id in (theirs.id, closed.id, "no-such-task"):
        decision = ConversationDecision.model_validate(_accept_by_id(task_id, "More scope."))
        error = validate_decision_for_trigger(
            decision,
            trigger_type="human_chat",
            active_task_id=None,
            has_live_work=False,
            agent_id=agent.id,
            trigger=_human_chat(),
        )
        assert error == TASK_ID_NOT_OPEN, task_id
    own = _task(agent.id, title="Current notes")
    deferred = ConversationDecision.model_validate(
        {
            "decision": "defer",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "reply": "Later.",
            "taskId": own.id,
        }
    )
    error = validate_decision_for_trigger(
        deferred,
        trigger_type="human_chat",
        active_task_id=None,
        has_live_work=False,
        agent_id=agent.id,
        trigger=_human_chat(),
    )
    assert error == 'data.task.id is only valid on act "accept" with commit "work"'


def test_defer_on_a_thread_intake_turn_queues_a_pending_task() -> None:
    agent = db.create_agent("Harley", role="Planner")
    peer = db.create_agent("Jimothy", role="Engineer")
    channel = db.create_channel(name="Crew", member_agent_ids=[agent.id, peer.id], created_by=agent.id)
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Harley, draft the Q4 plan when you can.",
        source_channel="channel",
    )
    from core.agent_loop.channel_rounds import start_channel_peer_round

    wakes = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    wake = next(item for item in wakes if item["agent_id"] == agent.id)
    trigger = {**wake["payload"], "type": "channel_message"}
    decision = ConversationDecision.model_validate(
        {
            "decision": "defer",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "reply": "Queued; I'll start it after my current task.",
            "taskTitle": "Draft the Q4 plan",
        }
    )
    assert (
        validate_decision_for_trigger(
            decision,
            trigger_type="channel_message",
            active_task_id=None,
            has_live_work=False,
            agent_id=agent.id,
            trigger=trigger,
        )
        is None
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    apply_decision(decision.model_dump(), agent, state, trigger)
    deferred = [task for task in db.list_tasks(assigned_to=agent.id) if task.title == "Draft the Q4 plan"]
    assert [task.status for task in deferred] == ["pending"]


# ─── task_update: a report on someone else's task ───


def _task_update(*, party: str, task_id: str = "t-report", from_agent: str = "a-reporter") -> dict[str, Any]:
    return {
        "type": "task_update",
        "task_id": task_id,
        "task_title": "Build milestone M0",
        "task_description": "Scaffold the project.",
        "task_status": "complete",
        "task_party": party,
        "attention_kind": "completion_report",
        "from_agent": from_agent,
        "from_name": "Charles",
        "content": 'Completed "Build milestone M0": scaffold is in.',
        "source_channel": "work",
    }


def _validate_on(trigger: dict[str, Any], decision: ConversationDecision, agent_id: str) -> str | None:
    return validate_decision_for_trigger(
        decision,
        trigger_type="task_update",
        active_task_id=None,
        has_live_work=False,
        agent_id=agent_id,
        trigger=trigger,
    )


_STAKEHOLDER_DECISIONS = {
    "observe": {"decision": "observe", "intentKind": "other"},
    "reply": {"decision": "answer", "intentKind": "status_request", "reply": "Thanks, noted.", "workCommit": False},
    "accept": {
        "decision": "accept",
        "intentKind": "work_request",
        "commitmentKind": "work",
        "reply": "Starting M1.",
        "taskTitle": "Build milestone M1",
    },
    "defer": {
        "decision": "defer",
        "intentKind": "work_request",
        "commitmentKind": "work",
        "reply": "Queued M1.",
        "taskTitle": "Build milestone M1",
    },
}


@pytest.mark.parametrize("act", sorted(_STAKEHOLDER_DECISIONS))
def test_stakeholder_task_update_allows_observe_reply_accept_defer(act: str) -> None:
    agent = db.create_agent("Harley", role="Planner")
    decision = ConversationDecision.model_validate(_STAKEHOLDER_DECISIONS[act])
    assert _validate_on(_task_update(party="stakeholder"), decision, agent.id) is None


def test_stakeholder_task_update_still_rejects_clarify_and_decline() -> None:
    agent = db.create_agent("Harley", role="Planner")
    for name in ("clarify", "decline"):
        decision = ConversationDecision.model_validate(
            {"decision": name, "intentKind": "work_request", "reply": "Which milestone?"}
        )
        error = _validate_on(_task_update(party="stakeholder"), decision, agent.id)
        assert error == "this turn only allows decisions: observe, answer, accept, defer"


@pytest.mark.parametrize("act", ["reply", "accept", "defer"])
def test_assignee_task_update_stays_observe_only(act: str) -> None:
    agent = db.create_agent("Charles", role="Build Engineer")
    decision = ConversationDecision.model_validate(_STAKEHOLDER_DECISIONS[act])
    error = _validate_on(_task_update(party="assignee"), decision, agent.id)
    assert error == "this turn only allows decisions: observe"
    observe = ConversationDecision.model_validate(_STAKEHOLDER_DECISIONS["observe"])
    assert _validate_on(_task_update(party="assignee"), observe, agent.id) is None


def test_stakeholder_task_update_reply_needs_text() -> None:
    agent = db.create_agent("Harley", role="Planner")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "   ", "workCommit": False}
    )
    error = _validate_on(_task_update(party="stakeholder"), decision, agent.id)
    assert error == 'conversation turns require a non-empty "reply" unless you choose "observe"'


def test_stakeholder_task_update_work_needs_a_title() -> None:
    agent = db.create_agent("Harley", role="Planner")
    decision = ConversationDecision.model_validate(
        {"decision": "accept", "intentKind": "work_request", "commitmentKind": "work", "reply": "On it."}
    )
    error = _validate_on(_task_update(party="stakeholder"), decision, agent.id)
    assert error == 'conversation work requests must provide a non-empty "taskTitle"'


def test_stakeholder_committed_reply_with_nothing_open_is_told_to_accept() -> None:
    agent = db.create_agent("Harley", role="Planner")
    decision = ConversationDecision.model_validate(
        {"decision": "answer", "intentKind": "status_request", "reply": "Starting M1.", "workCommit": True}
    )
    assert _validate_on(_task_update(party="stakeholder"), decision, agent.id) == WORK_COMMIT_STARTS_NOTHING


def test_task_update_acts_in_the_shipped_contract_match_the_code() -> None:
    text = load_default_prompt("runtime_contract_decision")
    block = text.split("{{elseif trigger.type = 'task_update'}}", 1)[1].split("{{elseif trigger.type", 1)[0]
    stakeholder, assignee = block.split("{{else}}", 1)
    assert stakeholder.lstrip().startswith("{{if trigger.task_party = 'stakeholder'}}")
    stakeholder_acts = " | ".join(allowed_conversation_acts_for_trigger("task_update", {"task_party": "stakeholder"}))
    assignee_acts = " | ".join(allowed_conversation_acts_for_trigger("task_update", {"task_party": "assignee"}))
    assert f"ALLOWED conversation act FOR THIS TURN: {stakeholder_acts}\n" in stakeholder
    assert f"ALLOWED conversation act FOR THIS TURN: {assignee_acts}\n" in assignee
    assert stakeholder_acts == "observe | reply | accept | defer"
    assert assignee_acts == "observe"
    assert allowed_conversation_acts_for_trigger("task_update") == ("observe",)


_TRIGGER_BLOCK_KEY = "runtime_block_trigger_event"


def _render_trigger(trigger: dict[str, Any]) -> str:
    return _format_trigger(
        trigger,
        "decision",
        {_TRIGGER_BLOCK_KEY: load_default_prompt(_TRIGGER_BLOCK_KEY)},
    )


def test_task_update_trigger_renders_the_report_and_its_guidance() -> None:
    text = _render_trigger(_task_update(party="stakeholder"))
    assert 'Update on "Build milestone M0" from [Charles].' in text
    assert "Current task status: complete" in text
    assert "Task description: Scaffold the project." in text
    assert 'Latest note from [Charles]: Completed "Build milestone M0": scaffold is in.' in text
    assert "Reason: someone reported completion and you need to handle the next step." in text
    assert (
        "Guidance: Decide what should happen next. If this work is one step of something larger and the next "
        "step has no owner yet, start it or hand it to the right teammate. If nothing more is needed, observe. "
        "Do NOT redelegate duplicate work."
    ) in text
    assert "You have been activated." not in text


def test_task_follow_up_trigger_keeps_its_lines_and_one_reason_block() -> None:
    trigger = {**_task_update(party="stakeholder"), "type": "task_follow_up", "attention_kind": "blocker"}
    text = _render_trigger(trigger)
    assert 'A task needs your response on "Build milestone M0".' in text
    assert "Task description: Scaffold the project." in text
    assert 'Latest note from [Charles]: Completed "Build milestone M0": scaffold is in.' in text
    assert "Respond within the existing task thread for this task." in text
    assert text.count("Reason: someone reported a blocker and needs a decision or help.") == 1
    assert text.count("Guidance: Do NOT restart the same delegated work.") == 1


def test_non_task_triggers_render_no_reason_block() -> None:
    text = _render_trigger({"type": "human_chat", "content": "Status?"})
    assert "Reason:" not in text
    assert "Guidance:" not in text
