"""Host posts say before actions on Talk/status/channel; actions-only unchanged."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.channel_host import work_holds_talk
from core.agent_loop.channel_round_plan import DISPATCH_ROUNDS
from core.agent_loop.channel_rounds import start_channel_peer_round
from core.agent_loop.decision_contract import parse_direct_turn_response
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.loop import run_turn
from core.agent_loop.runtime_core import (
    AUDIENCE_SOFT_JUDGMENT,
    SAY_WITH_ACTIONS,
    format_runtime_core_block,
)
from core.agent_loop.say_before_actions import (
    decision_has_side_effect_actions,
    persist_operator_say,
    should_post_say_before_actions,
)
from core.agent_loop.soft_blocks import apply_no_progress_block, clear_soft_block_for_live_work
from core.bm_cli.results import BossModCliResult
from core.llm.client import LLMResponse
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import create_or_bind_task


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


def _llm(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test/mock",
        prompt_tokens=8,
        completion_tokens=4,
        total_tokens=12,
    )


def _script_completions(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> list[str]:
    queue = list(contents)
    seen: list[str] = []

    async def _fake_completion(**kwargs: Any) -> LLMResponse:
        if not queue:
            raise AssertionError("unexpected extra LLM completion")
        content = queue.pop(0)
        seen.append(content)
        return _llm(content)

    monkeypatch.setattr("core.llm.client.completion", _fake_completion)
    return seen


def test_runtime_core_biases_say_with_actions_on_human_ask() -> None:
    agent = db.create_agent("Core Clerk", role="Engineer")
    block = format_runtime_core_block(agent)
    assert SAY_WITH_ACTIONS in block
    assert "When acting on a human ask" in SAY_WITH_ACTIONS
    assert "Bias only" in SAY_WITH_ACTIONS
    assert "Copy that" in SAY_WITH_ACTIONS
    assert AUDIENCE_SOFT_JUDGMENT in block
    # Work stays quiet: soft judgment still allows pass; no forced ack line.
    assert "Speak only when this wake is for you" in AUDIENCE_SOFT_JUDGMENT
    assert "When nothing changed, prefer an engine pass over a status essay." in AUDIENCE_SOFT_JUDGMENT
    assert "must always say" not in block.lower()
    assert "forced ack" not in block.lower()


def test_parse_keeps_operator_say_on_say_plus_cli_envelope() -> None:
    parsed = parse_direct_turn_response(
        '{"say":"Checking the clone.\\n\\n- Listing /me.","actions":[{"act":"cli","data":{"cmd":"ls /me"},"th":"list desk"}]}'
    )
    assert parsed.get("action") == "bm_cli"
    assert parsed.get("command") == "ls /me"
    assert parsed.get("operator_say") == "Checking the clone.\n\n- Listing /me."
    assert parsed.get("thought") == "list desk"
    actions_only = parse_direct_turn_response(
        '{"act":"cli","data":{"cmd":"ls /me"},"th":"list desk"}'
    )
    assert actions_only.get("action") == "bm_cli"
    assert actions_only.get("operator_say") in (None, "")


def test_channel_accept_posts_say_before_work_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    peer = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ship",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="@everyone ship the notes",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    order: list[str] = []
    real_activate = activity_runtime.activate_work_activity

    def _wrapped_activate(*args: Any, **kwargs: Any):
        order.append("work")
        posted = [
            item.content
            for item in db.list_channel_messages(channel.id)
            if item.author_agent_id == agent.id
        ]
        assert any("I'll ship the notes." in (content or "") for content in posted)
        return real_activate(*args, **kwargs)

    monkeypatch.setattr(
        "core.agent_loop.decision_runtime.activity_runtime.activate_work_activity",
        _wrapped_activate,
    )
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Ship the notes",
            "reply": "I'll ship the notes.",
        },
        agent,
        state,
        {
            "type": "channel_message",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "round_id": triggers[0]["payload"]["round_id"],
            "source_message_id": message.id,
            "content": message.content,
            "from_name": "Human Operator",
            "author_type": "human",
            "dispatch_mode": DISPATCH_ROUNDS,
        },
    )
    assert order == ["work"]
    assert result.get("channel_message")
    assert "I'll ship the notes." in (result["channel_message"].get("content") or "")
    assert work_holds_talk(channel.id)


@pytest.mark.asyncio
async def test_human_chat_say_posts_before_cli_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None
    order: list[str] = []

    def _fake_cli(agent_obj, state_obj, command, content=None, **kwargs):
        del agent_obj, state_obj, content, kwargs
        order.append("cli")
        thread = db.get_human_chat_thread(agent.id)
        assert any(
            "Checking the clone." in (item.content or "") for item in thread
        ), "say must post before CLI runs"
        return BossModCliResult(
            command=command,
            ok=True,
            detail="listed",
            prompt_content="BOSSMOD CLI RESULT\nlisted",
        )

    monkeypatch.setattr("core.agent_loop.decision_turn.execute_bm_cli", _fake_cli)
    _script_completions(
        monkeypatch,
        [
            '{"say":"Checking the clone.\\n\\n- Listing /me.","actions":[{"act":"cli","data":{"cmd":"ls /me"},"th":"list"}]}',
            '{"say":"Clone looks ready.","actions":[]}',
        ],
    )
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Status on the clone?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert order == ["cli"]
    assert outcome.result.get("event") == "decision_applied"
    thread = db.get_human_chat_thread(agent.id)
    assert any("Checking the clone." in (item.content or "") for item in thread)
    assert any("Clone looks ready." in (item.content or "") for item in thread)


@pytest.mark.asyncio
async def test_actions_only_cli_does_not_post_early_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = db.create_agent("Jim", role="Engineer", model_work="test/mock")
    state = db.get_agent_state(agent.id)
    assert state is not None

    def _fake_cli(agent_obj, state_obj, command, content=None, **kwargs):
        del agent_obj, state_obj, content, kwargs
        thread = db.get_human_chat_thread(agent.id)
        assert thread == []
        return BossModCliResult(
            command=command,
            ok=True,
            detail="listed",
            prompt_content="BOSSMOD CLI RESULT\nlisted",
        )

    monkeypatch.setattr("core.agent_loop.decision_turn.execute_bm_cli", _fake_cli)
    _script_completions(
        monkeypatch,
        [
            '{"act":"cli","data":{"cmd":"ls /me"},"th":"list"}',
            '{"say":"Listed /me. Ready.","actions":[]}',
        ],
    )
    outcome = await run_turn(
        agent,
        state,
        {
            "type": "human_chat",
            "content": "Peek at /me?",
            "from_name": "Human",
            "from_id": HUMAN_SENDER_ID,
            "source_channel": "chat",
        },
    )
    assert outcome.result.get("event") == "decision_applied"
    thread = db.get_human_chat_thread(agent.id)
    assert len(thread) == 1
    assert "Listed /me. Ready." in thread[0].content


def test_say_only_status_is_not_treated_as_side_effect_actions() -> None:
    from core.agent_loop.decision_contract import ConversationDecision

    decision = ConversationDecision.model_validate(
        {
            "decision": "answer",
            "intentKind": "status_request",
            "reply": "Still on the clone.",
            "commitmentKind": "none",
        }
    )
    assert not decision_has_side_effect_actions(decision)
    assert not should_post_say_before_actions(
        {"type": "human_chat"},
        decision,
    )


def test_archived_channel_say_before_actions_fails_closed() -> None:
    agent = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Archived",
        member_agent_ids=[agent.id],
        created_by=HUMAN_SENDER_ID,
    )
    db.archive_channel(channel.id)
    state = db.get_agent_state(agent.id)
    assert state is not None
    early = persist_operator_say(
        agent,
        state,
        {"type": "channel_response", "channel_id": channel.id},
        "I'll ship the notes.",
    )
    assert early is None
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Ship the notes",
            "reply": "I'll ship the notes.",
        },
        agent,
        state,
        {
            "type": "channel_response",
            "channel_id": channel.id,
            "content": "ship it",
            "from_name": "Human Operator",
            "author_type": "human",
        },
    )
    assert result.get("event") == "agent_error"
    assert "could not post say before actions" in (result.get("detail") or "")
    assert db.list_tasks(assigned_to=agent.id) == []


def test_soft_block_stay_unchanged_by_say_before_actions() -> None:
    agent = db.create_agent("Ada", role="Engineer", desk_x=1, desk_y=1)
    peer = db.create_agent("Bea", role="Reviewer", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ada, Bea",
        member_agent_ids=[agent.id, peer.id],
        created_by=HUMAN_SENDER_ID,
    )
    creation = create_or_bind_task(
        title="Spec",
        description="Author the spec.",
        project=None,
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel="channel",
        notification_policy="completion_blocked",
        notification_channel_id=channel.id,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )
    activity_runtime.activate_work_activity(agent.id, creation.task)
    apply_no_progress_block(agent, {"type": "channel_response", "channel_id": channel.id})
    assert clear_soft_block_for_live_work(agent.id) is None
    # Soft-block stays when work is not live — say-before-actions must not wipe it.
    assert db.get_task(creation.task.id).status == "blocked"
    assert db.get_active_activity(agent.id) is None
