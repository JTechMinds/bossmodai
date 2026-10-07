"""Thread replies post without a next-owner nudge; mention helpers stay.

The router decides who is addressed, so an untagged reply in a multi-party
thread posts on its first attempt, with no tag-or-proceed round-trip and no
``data.proceed`` flag. The kept helpers (@-mention extraction, floor mention
candidates, multi-party, status one-liners, pure reactions) still hold.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.decision_contract import parse_direct_turn_response
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.next_owner import (
    extract_next_owner_mentions,
    is_multi_party_channel,
    is_pure_reaction,
    is_system_one_liner,
    mention_names_for_channel,
)
from core.default_prompts import prompt_file_path
from core.models.message import HUMAN_SENDER_ID
from core.prompting.runtime_prompt_registry import runtime_prompt_surface_map
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


def _three_members():
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    jimothy = db.create_agent("Jimothy", role="Eng", desk_x=3, desk_y=1)
    channel = db.create_channel(
        name="Jim, Laura, Jimothy",
        member_agent_ids=[jim.id, laura.id, jimothy.id],
        created_by=jimothy.id,
    )
    return jim, laura, jimothy, channel


def _solo_channel():
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    channel = db.create_channel(
        name="Ada",
        member_agent_ids=[ada.id],
        created_by=HUMAN_SENDER_ID,
    )
    return ada, channel


def _channel_trigger(channel, *, trigger_type: str = "channel_response") -> dict:
    return {
        "type": trigger_type,
        "channel_id": channel.id,
        "content": "Share the review findings.",
        "from_name": "Human Operator",
        "author_type": "human",
        "channel_name": channel.name,
    }


def _answer(reply: str) -> dict:
    return {
        "decision": "answer",
        "workCommit": False,
        "intentKind": "status_request",
        "reply": reply,
    }


def test_untagged_multi_party_reply_posts_on_the_first_attempt() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    assert is_multi_party_channel(channel.id)
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        _answer("Findings are ready in the review note."),
        jimothy,
        state,
        _channel_trigger(channel),
    )

    assert result["event"] == "decision_applied"
    assert "feedback_code" not in result
    assert result["channel_message"]["content"] == "Findings are ready in the review note."
    assert [item.content for item in db.list_channel_messages(channel.id)] == [
        "Findings are ready in the review note."
    ]


def test_untagged_reply_on_a_channel_task_posts_without_a_nudge() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    creation = create_or_bind_task(
        title="Share review findings",
        description="Post the review summary for the team.",
        project=None,
        assigned_to=jimothy.id,
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
    assert creation.task is not None
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(
        _answer("Jtech-CLI review summary is ready."),
        jimothy,
        state,
        {
            "type": "task_follow_up",
            "task_id": creation.task.id,
            "content": "Share the review findings with the team.",
            "from_name": "Human Operator",
        },
    )
    assert result["event"] == "decision_applied"
    assert "feedback_code" not in result


def test_proceed_flag_is_no_longer_part_of_the_contract() -> None:
    parsed = parse_direct_turn_response(
        '{"act":"reply","work_commit":false,"intent":"status","msg":"Ready.","data":{"proceed":true},"th":"go"}'
    )
    assert parsed["decision"] == "_parse_failed"
    assert "proceed" in parsed["_raw_snippet"]
    assert "internal_loop_decision_next_owner_nudge" not in runtime_prompt_surface_map()
    with pytest.raises(KeyError):
        prompt_file_path("internal_loop_decision_next_owner_nudge")


def test_mentions_need_an_at_and_a_known_name() -> None:
    jim, laura, _jimothy, channel = _three_members()
    names = mention_names_for_channel(channel.id)
    assert extract_next_owner_mentions("@nobody please go next", member_names=names) == []
    assert extract_next_owner_mentions(f"{laura.name} should take the next pass.", member_names=names) == []
    assert extract_next_owner_mentions(f"@{jim.name} please review", member_names=names) == [jim.name]
    assert extract_next_owner_mentions("@everyone and @all", member_names=names) == ["everyone", "everyone"]
    assert extract_next_owner_mentions("@Boss the call is yours", member_names=names) == ["Boss"]
    # The retired aliases for the human no longer tag anyone.
    assert extract_next_owner_mentions("@Human or @Operator, the call is yours", member_names=names) == []


def test_system_one_liners_are_recognised() -> None:
    assert is_system_one_liner("Created: Share review findings")
    assert is_system_one_liner("Jimothy Created: Share review findings")
    assert is_system_one_liner("Accepted: Share review findings")
    assert is_system_one_liner("Jimothy Accepted: Share review findings")
    assert is_system_one_liner("Writing /me/review.md")
    assert is_system_one_liner("Jimothy Writing /me/review.md")
    assert is_system_one_liner("Done — /me/review.md")
    assert is_system_one_liner("Jimothy Done — /me/review.md")
    assert is_system_one_liner("Busy — 2 queued")
    assert is_system_one_liner("Ada Busy — 2 queued")
    assert not is_system_one_liner("Findings are ready in the review note.")


def test_pure_reactions_are_recognised() -> None:
    assert is_pure_reaction("👍")
    assert is_pure_reaction("ok")
    assert is_pure_reaction("thanks")
    assert not is_pure_reaction("ok — should I start the rewrite?")


def test_one_agent_thread_is_not_multi_party() -> None:
    ada, channel = _solo_channel()
    assert is_multi_party_channel(channel.id) is False
    state = db.get_agent_state(ada.id)
    assert state is not None
    result = apply_decision(
        _answer("Findings are ready in the review note."),
        ada,
        state,
        _channel_trigger(channel),
    )
    assert result["event"] == "decision_applied"
    assert result.get("channel_message")


def test_focus_and_dm_replies_post() -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    peer = db.create_agent("Bea", role="Writer", desk_x=2, desk_y=1)
    state = db.get_agent_state(ada.id)
    assert state is not None

    focus = apply_decision(
        _answer("Findings are ready in the review note."),
        ada,
        state,
        {"type": "human_chat", "content": "How is it going?", "from_name": "Human Operator"},
    )
    assert focus["event"] == "decision_applied"
    assert focus.get("chat_message")

    dm = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "question",
            "reply": "Can you take the rewrite?",
        },
        ada,
        state,
        {
            "type": "peer_message",
            "from_agent": peer.id,
            "from_name": peer.name,
            "content": "Need a second pair of eyes?",
            "message_type": "work",
        },
    )
    assert dm["event"] == "decision_applied"
    assert dm.get("trigger_requests")
