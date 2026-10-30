"""Decision parse failures repair up to N, then note and re-queue once."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from tests._connections import model_connection
from core import config
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.decision_contract import (
    TASK_ID_NOT_OPEN,
    WORK_COMMIT_STARTS_NOTHING,
    act_for_decision,
    parse_direct_turn_response,
    repair_kept_act,
)
from core.agent_loop.decision_parse_fail import (
    DEFAULT_DECISION_REPAIR_ATTEMPTS,
    PARSE_FAIL_IDLE_NOTE,
    PARSE_FAIL_NOTE,
    decision_repair_attempt_limit,
    surface_decision_parse_failure,
)
from core.agent_loop.loop import run_turn
from core.agent_loop.soft_blocks import apply_no_progress_block
from core.agent_loop.turn_helpers import _build_decision_repair_messages
from core.bm_cli.policy_engine import policy_engine
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID

_REPAIR_MARK = "one JSON object, no fences/markdown"
_ENVELOPE = '{"say":"string","actions":[],"work_commit":false}'


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _limit_decision_repairs(attempts: int) -> None:
    db.set_setting("decision_repair_attempts", str(attempts), "llm")
    config.reload()


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
    )


def _script_completions(
    monkeypatch: pytest.MonkeyPatch,
    contents: list[str],
) -> tuple[list[str], list[list[dict[str, str]]]]:
    queue = list(contents)
    seen: list[str] = []
    prompts: list[list[dict[str, str]]] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        prompts.append(list(kwargs.get("messages") or []))
        content = queue.pop(0)
        seen.append(content)
        return _llm(content)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen, prompts


def _joined_repair(kind: str, snippet: str, kept_act: str | None = None) -> str:
    messages = _build_decision_repair_messages(parsed_error=snippet, kind=kind, kept_act=kept_act)
    return "\n".join(item["content"] for item in messages)


def _added_system_text(prompts: list[list[dict[str, str]]], call: int) -> str:
    """Return the system messages call ``call`` saw that the call before it did not."""
    added = prompts[call][len(prompts[call - 1]):]
    return "\n".join(item.get("content") or "" for item in added if item.get("role") == "system")


def test_decision_repair_attempts_defaults_to_six() -> None:
    assert config.get("decision_repair_attempts") == "6"
    assert decision_repair_attempt_limit() == DEFAULT_DECISION_REPAIR_ATTEMPTS == 6
    # Setting labels and descriptions live in the System settings catalog.
    settings_js = Path("ui/static/js/settings/settings-system-meta.js").read_text(encoding="utf-8")
    assert "decision_repair_attempts" in settings_js
    assert "Decision Repair Attempts" in settings_js


@pytest.mark.parametrize(
    ("kind", "snippet"),
    [
        ("prose_status", "I'll do the tests next."),
        ("invalid_decision", '{"act":"reply","work_commit":false,"th2":"x"}'),
        ("invalid_json", '{"act":'),
        ("invalid_value", 'Value error, plan mode "delegate" needs at least one child with "who" or "aid"'),
    ],
)
def test_every_decision_repair_kind_includes_corrective_steer(kind: str, snippet: str) -> None:
    text = _joined_repair(kind, snippet)
    assert snippet in text or snippet[:20] in text
    assert _REPAIR_MARK in text
    assert _ENVELOPE in text
    assert "Allowed keys only" in text
    assert "not Done" in text
    assert "fake Done" in text or "Do not invent Board status" in text


@pytest.mark.asyncio
async def test_parse_fail_notes_thread_and_requeues_without_approve_or_wipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(1)
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    prose = "I'll do the clone next. No JSON."
    seen, prompts = _script_completions(monkeypatch, [prose, prose])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 2
    repair_text = "\n".join(item.get("content") or "" for item in prompts[1])
    assert "prose status" in repair_text
    assert _REPAIR_MARK in repair_text
    assert _ENVELOPE in repair_text
    assert outcome.result.get("event") == "agent_error"
    assert outcome.result.get("parse_steer") is True
    assert outcome.trigger_status == "completed"
    events = db.list_task_events(task.id)
    assert sum(1 for item in events if (item.content or "").strip() == PARSE_FAIL_NOTE) == 1
    wakes = [
        item
        for item in outcome.result.get("trigger_requests") or []
        if item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
    ]
    assert len(wakes) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert db.get_agent(agent.id) is not None
    assert db.get_agent_state(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []
    assert db.list_consent_requests(status="pending") == []

    persist_result_triggers(outcome.result)
    again = surface_decision_parse_failure(
        agent=agent,
        trigger={"type": "human_chat", "task_id": task.id},
    )
    assert again.get("trigger_requests") == []
    events = db.list_task_events(task.id)
    assert sum(1 for item in events if (item.content or "").strip() == PARSE_FAIL_NOTE) == 1


@pytest.mark.asyncio
async def test_soft_blocked_commitment_is_not_left_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(0)
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"), desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Write notes", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    apply_no_progress_block(agent, {"type": "human_chat"})
    assert db.get_task(task.id).status == "blocked"
    seen, _prompts = _script_completions(monkeypatch, ["I'll do that next."])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 1
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.status != "complete"
    assert any(
        item.get("trigger_type") == "activity_resumed" and item.get("task_id") == task.id
        for item in outcome.result.get("trigger_requests") or []
    )
    assert any((item.content or "").strip() == PARSE_FAIL_NOTE for item in db.list_task_events(task.id))
    assert db.get_agent(agent.id) is not None
    assert db.list_cli_approval_requests(status="pending", agent_id=agent.id) == []


@pytest.mark.asyncio
async def test_repair_can_recover_before_fail_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(6)
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Validate clone", assigned_to=agent.id)
    activate_work_activity(agent.id, task)
    seen, prompts = _script_completions(
        monkeypatch,
        [
            '{"act":"reply","work_commit":false,"msg":"Continuing.","th2":"nope"}',
            '{"say":"Still on the clone.","actions":[],"work_commit":false}',
        ],
    )
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 2
    repair_text = "\n".join(item.get("content") or "" for item in prompts[1])
    assert "invented or disallowed keys" in repair_text
    assert _REPAIR_MARK in repair_text
    assert outcome.result.get("event") == "decision_applied"
    assert not any((item.content or "").strip() == PARSE_FAIL_NOTE for item in db.list_task_events(task.id))
    assert db.get_task(task.id).status != "complete"


@pytest.mark.asyncio
async def test_truncated_json_repairs_then_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit_decision_repairs(2)
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen, prompts = _script_completions(monkeypatch, ['{"say":', '{"act":', "still prose"])
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert len(seen) == 3
    for prompt in prompts[1:]:
        text = "\n".join(item.get("content") or "" for item in prompt)
        assert _REPAIR_MARK in text
        assert _ENVELOPE in text
    assert outcome.result.get("parse_steer") is True
    assert outcome.result.get("chat_message")
    # Nothing was open to re-queue, so the note must not claim a re-queue.
    assert PARSE_FAIL_IDLE_NOTE in str(outcome.result["chat_message"].get("content") or "")
    assert PARSE_FAIL_NOTE not in str(outcome.result["chat_message"].get("content") or "")
    notes = db.list_notifications(agent_id=agent.id, limit=8)
    assert sum(1 for item in notes if (item.content or "").strip() == PARSE_FAIL_IDLE_NOTE) == 1
    assert outcome.result.get("trigger_requests") == []


_KEEP_ACCEPT = 'You answered with act "accept", which this turn allows. Keep "accept" and everything it carried'


def _accept(**extra: Any) -> str:
    """Return a work accept for the eval4 audit, with ``extra`` keys merged in."""
    payload: dict[str, Any] = {
        "act": "accept",
        "intent": "work",
        "msg": "Understood — I'll perform the read-only audit and write the report.",
        "commit": "work",
        "data": {
            "task": {
                "title": "Eval4 read-only audit",
                "desc": "Audit the eval4 project read-only, as /projects/eval4/instructions.md asks.",
            },
        },
        "th": "accept work",
    }
    for key, value in extra.items():
        if key == "data":
            payload["data"] = {**payload["data"], **value}
        elif key == "task":
            payload["data"]["task"] = {**payload["data"]["task"], **value}
        else:
            payload[key] = value
    return json.dumps(payload)


def _eval4_chat() -> dict[str, Any]:
    return {
        "type": "human_chat",
        "content": "I've got a new task for you, see /projects/eval4/instructions.md",
        "from_name": "Human",
        "from_id": HUMAN_SENDER_ID,
        "source_channel": "chat",
    }


def _assert_task_started(outcome: Any, agent_id: str) -> None:
    assert outcome.trigger_status == "completed"
    assert [task.title for task in db.list_tasks(assigned_to=agent_id)] == ["Eval4 read-only audit"]
    assert any(
        item.get("trigger_type") == "activity_resumed" for item in outcome.result.get("trigger_requests") or []
    ), "accept + commit=work schedules the first execution turn"


@pytest.mark.asyncio
async def test_eng_boxies_self_plan_children_repair_keeps_the_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live 2026-10-30 (Eng-Boxies): an accept whose plan children were the
    # agent's own sub-steps (no who/aid) was refused, the repair called it
    # broken JSON and offered only the no-work reply, and no work started.
    agent = db.create_agent("Eng-Boxies", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    own_steps = _accept(
        data={
            "plan": {
                "mode": "self",
                "children": [
                    {"task": {"title": "Inventory the eval4 files"}},
                    {"task": {"title": "Audit each file against the instructions"}},
                    {"task": {"title": "Write the audit report"}},
                ],
            }
        }
    )
    seen, prompts = _script_completions(monkeypatch, [own_steps, _accept()])

    outcome = await run_turn(agent, state, _eval4_chat())

    assert len(seen) == 2
    repair_text = _added_system_text(prompts, 1)
    assert _KEEP_ACCEPT in repair_text
    # Uncut (the repair trims snippets past 180 characters with "..."), and the
    # kept-act sentence follows it in the same paragraph.
    assert (
        'a value that does not fit this turn: Value error, each plan child is a teammate\'s task and needs '
        '"who" or "aid". For your own sub-steps, leave "plan" out and use checklist add once you start. '
        f"{_KEEP_ACCEPT}"
    ) in repair_text
    assert 'is a teammate\'s task and needs "who" or "aid"' in repair_text
    assert 'leave "plan" out and use checklist add' in repair_text
    assert "truncated or broken JSON" not in repair_text
    assert "agentId" not in repair_text and "agentName" not in repair_text
    assert "Envelope for a reply that starts no work (no Board change):" in repair_text
    assert "Minimal allowed envelope" not in repair_text
    assert len([step for step in outcome.steps if "decision_repair_requested" in str(step.get("result"))]) == 1
    _assert_task_started(outcome, agent.id)


@pytest.mark.asyncio
async def test_a_turn_validation_repair_keeps_the_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    seen, prompts = _script_completions(monkeypatch, [_accept(task={"id": "task-that-is-not-open"}), _accept()])

    outcome = await run_turn(agent, state, _eval4_chat())

    assert len(seen) == 2
    repair_text = _added_system_text(prompts, 1)
    assert TASK_ID_NOT_OPEN in repair_text
    assert _KEEP_ACCEPT in repair_text
    _assert_task_started(outcome, agent.id)


@pytest.mark.asyncio
async def test_a_refused_remember_repair_keeps_the_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    db.set_setting("standing_prefs_line_max_chars", "40", "context")
    config.reload()
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    too_long = "The boss keeps eval instructions under /projects/eval4, one file per run."
    seen, prompts = _script_completions(monkeypatch, [_accept(remember=too_long), _accept()])

    outcome = await run_turn(agent, state, _eval4_chat())

    assert len(seen) == 2
    repair_text = _added_system_text(prompts, 1)
    assert "memory not saved:" in repair_text
    assert _KEEP_ACCEPT in repair_text
    _assert_task_started(outcome, agent.id)


_KEEP_ANY_ACT = "You answered with act"


@pytest.mark.asyncio
async def test_a_committed_reply_with_nothing_open_is_not_told_to_keep_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The validator rejects the reply itself and says "use accept"; a "keep
    # reply" line beside it would steer back into the stalled start.
    agent = db.create_agent("Eng-Boxies", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    committed = '{"say":"Understood — starting the read-only audit now.","actions":[],"work_commit":true}'
    seen, prompts = _script_completions(monkeypatch, [committed, _accept()])

    outcome = await run_turn(agent, state, _eval4_chat())

    assert len(seen) == 2
    repair_text = _added_system_text(prompts, 1)
    assert WORK_COMMIT_STARTS_NOTHING in repair_text
    assert _KEEP_ANY_ACT not in repair_text
    _assert_task_started(outcome, agent.id)


@pytest.mark.asyncio
async def test_a_cancel_with_nothing_active_is_not_told_to_keep_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", connection_id=model_connection("test/mock"))
    state = db.get_agent_state(agent.id)
    assert state is not None
    cancel = json.dumps({"act": "cancel", "intent": "work", "msg": "Cancelling the audit.", "th": "cancel"})
    reply = '{"say":"Nothing is running, so there is nothing to cancel.","actions":[],"work_commit":false}'
    seen, prompts = _script_completions(monkeypatch, [cancel, reply])

    outcome = await run_turn(agent, state, {**_eval4_chat(), "content": "Cancel the eval4 audit."})

    assert len(seen) == 2
    repair_text = _added_system_text(prompts, 1)
    assert "cancel is only valid when there is an active task to close" in repair_text
    assert _KEEP_ANY_ACT not in repair_text
    assert outcome.result.get("event") == "decision_applied"


@pytest.mark.asyncio
async def test_a_work_accept_on_a_peer_message_is_not_told_to_keep_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Eng-Boxies", role="Engineer", connection_id=model_connection("test/mock"))
    peer = db.create_agent("Ett-PO", role="Product Owner")
    state = db.get_agent_state(agent.id)
    assert state is not None
    reply = '{"say":"Assign it to me as a task and I will start.","actions":[],"work_commit":false}'
    seen, prompts = _script_completions(monkeypatch, [_accept(), reply])
    peer_message = {
        "type": "peer_message",
        "from_agent": peer.id,
        "from_name": peer.name,
        "content": "Can you audit eval4 for me?",
    }

    outcome = await run_turn(agent, state, peer_message)

    assert len(seen) == 2
    repair_text = _added_system_text(prompts, 1)
    assert "peer messages are conversational only" in repair_text
    assert _KEEP_ANY_ACT not in repair_text
    assert outcome.result.get("event") == "decision_applied"
    assert db.list_tasks(assigned_to=agent.id) == []


def test_the_kept_act_sentence_joins_the_first_paragraph_and_leaves_no_blank_run() -> None:
    error = TASK_ID_NOT_OPEN
    without = _build_decision_repair_messages(parsed_error=error, kept_act=None)[0]["content"]
    assert "\n\n\n" not in without
    assert without.startswith(
        f"Your last response did not fit this conversation turn: {error}.\n\nReturn one JSON object"
    )
    kept = _build_decision_repair_messages(parsed_error=error, kept_act="accept")[0]["content"]
    assert "\n\n\n" not in kept
    assert kept.splitlines()[0] == (
        f'Your last response did not fit this conversation turn: {error}. You answered with act "accept", '
        'which this turn allows. Keep "accept" and everything it carried, and change only what is named above.'
    )
    assert kept.splitlines()[1:] == without.splitlines()[1:]


def test_repair_kept_act_gives_none_for_an_act_the_turn_does_not_allow() -> None:
    assignee_update = {"type": "task_update", "task_party": "assignee", "task_id": "t1"}
    kept = repair_kept_act("accept", trigger_type="task_update", trigger=assignee_update)
    assert kept is None
    assert "You answered with act" not in _joined_repair("invalid_value", "Value error, bad plan", kept)
    assert repair_kept_act(None, trigger_type="human_chat", trigger=_eval4_chat()) is None
    assert repair_kept_act("cli", trigger_type="human_chat", trigger=_eval4_chat()) is None
    # A stakeholder's task_update allows accept, so the same act is kept there.
    stakeholder_update = {**assignee_update, "task_party": "stakeholder"}
    assert repair_kept_act("accept", trigger_type="task_update", trigger=stakeholder_update) == "accept"
    assert _KEEP_ACCEPT in _joined_repair("invalid_value", "Value error, bad plan", "accept")


def test_a_say_only_envelope_keeps_reply() -> None:
    parsed = parse_direct_turn_response('{"say":"Still reading the brief.","actions":[],"work_commit":false}')
    assert parsed["decision"] == "answer"
    act = act_for_decision(parsed["decision"])
    assert act == "reply"
    assert repair_kept_act(act, trigger_type="human_chat", trigger=_eval4_chat()) == "reply"
    assert act_for_decision("accept") == "accept"
    assert act_for_decision("_parse_failed") is None
