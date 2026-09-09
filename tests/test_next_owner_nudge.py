"""Soft next-owner nudge for untagged multi-party thread replies.

Bar: multi-party untagged agent reply nudges once with Debra's copy.
Proceed continues without a tag. Tagged replies skip. Exempts hold.
1:1 Focus / DM never nudges. No hard reject. No invented @everyone.
"""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config
from core.agent_loop.decision_contract import parse_direct_turn_response
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.next_owner import (
    NUDGE_ADD_LABEL,
    NUDGE_COPY,
    NUDGE_FEEDBACK_CODE,
    NUDGE_PROCEED_LABEL,
    NUDGE_SHOWN_KEY,
    extract_next_owner_mentions,
    has_next_owner_tag,
    is_exempt_reply,
    is_multi_party_channel,
    is_pure_reaction,
    is_system_one_liner,
    mention_names_for_channel,
    next_owner_nudge_continuation,
)
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


def _answer(reply: str, *, proceed: bool = False) -> dict:
    payload = {
        "decision": "answer",
        "intentKind": "status_request",
        "reply": reply,
    }
    if proceed:
        payload["proceedUntagged"] = True
    return payload


def test_untagged_multi_party_reply_nudges_with_debra_copy() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    trigger = _channel_trigger(channel)

    result = apply_decision(
        _answer("Findings are ready in the review note."),
        jimothy,
        state,
        trigger,
    )

    assert result["event"] == "world_feedback"
    assert result["feedback_code"] == NUDGE_FEEDBACK_CODE
    assert result["detail"] == NUDGE_COPY
    assert result["nudge_actions"] == [NUDGE_PROCEED_LABEL, NUDGE_ADD_LABEL]
    assert trigger[NUDGE_SHOWN_KEY] is True
    assert db.list_channel_messages(channel.id) == []


def test_proceed_posts_without_a_tag() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    trigger = _channel_trigger(channel)

    nudged = apply_decision(
        _answer("Findings are ready in the review note."),
        jimothy,
        state,
        trigger,
    )
    assert nudged["event"] == "world_feedback"
    assert db.list_channel_messages(channel.id) == []

    posted = apply_decision(
        _answer("Findings are ready in the review note.", proceed=True),
        jimothy,
        state,
        trigger,
    )
    assert posted["event"] == "decision_applied"
    assert posted.get("channel_message")
    assert posted["channel_message"]["content"] == "Findings are ready in the review note."
    assert "@everyone" not in posted["channel_message"]["content"]
    contents = [item.content for item in db.list_channel_messages(channel.id)]
    assert contents == ["Findings are ready in the review note."]


def test_second_untagged_attempt_in_same_turn_is_implicit_proceed() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    trigger = _channel_trigger(channel)

    apply_decision(_answer("Walking through the findings now."), jimothy, state, trigger)
    second = apply_decision(_answer("Walking through the findings now."), jimothy, state, trigger)

    assert second["event"] == "decision_applied"
    assert second.get("channel_message")
    assert "@everyone" not in (second["channel_message"]["content"] or "")
    assert len(db.list_channel_messages(channel.id)) == 1


def test_member_mention_skips_nudge() -> None:
    jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        _answer(f"@{jim.name} please take the review comments next."),
        jimothy,
        state,
        _channel_trigger(channel),
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")
    assert f"@{jim.name}" in result["channel_message"]["content"]


def test_everyone_mention_skips_nudge() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        _answer("@everyone the review note is up."),
        jimothy,
        state,
        _channel_trigger(channel),
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")


def test_human_mention_skips_nudge() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        _answer("@Human the findings are ready for your call."),
        jimothy,
        state,
        _channel_trigger(channel),
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")


def test_self_mention_alone_still_nudges() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        _answer(f"@{jimothy.name} will keep going on the review."),
        jimothy,
        state,
        _channel_trigger(channel),
    )

    assert result["event"] == "world_feedback"
    assert result["detail"] == NUDGE_COPY
    assert db.list_channel_messages(channel.id) == []


def test_unknown_at_token_is_not_a_tag() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    names = mention_names_for_channel(channel.id)
    assert not has_next_owner_tag("@nobody please go next", member_names=names, author_name="Jimothy")
    assert extract_next_owner_mentions("@nobody please go next", member_names=names) == []


def test_bare_name_without_at_is_not_a_tag() -> None:
    _jim, laura, _jimothy, channel = _three_members()
    names = mention_names_for_channel(channel.id)
    assert not has_next_owner_tag(
        f"{laura.name} should take the next pass.",
        member_names=names,
        author_name="Jimothy",
    )


def test_standing_by_parks_the_ball() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None

    result = apply_decision(
        _answer("Standing by if the team wants another pass."),
        jimothy,
        state,
        _channel_trigger(channel),
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")


def test_system_one_liners_are_exempt() -> None:
    assert is_system_one_liner("Created: Share review findings")
    assert is_system_one_liner("Accepted: Share review findings")
    assert is_system_one_liner("Writing /me/review.md")
    assert is_system_one_liner("Done — /me/review.md")
    assert is_system_one_liner("Busy — 2 queued")
    assert is_exempt_reply("Accepted: Share review findings")
    assert not is_system_one_liner("Findings are ready in the review note.")


def test_consent_and_preference_cards_are_exempt() -> None:
    assert is_exempt_reply("Need /tmp/app", trigger={"notification_kind": "host_path_consent"})
    assert is_exempt_reply("Work in your workspace?", trigger={"notification_kind": "workspace_preference"})
    assert is_exempt_reply("Need /tmp/app", trigger={"consent_id": "consent-1"})


def test_pure_reactions_are_exempt() -> None:
    assert is_pure_reaction("👍")
    assert is_pure_reaction("ok")
    assert is_pure_reaction("thanks")
    assert not is_pure_reaction("ok — should I start the rewrite?")
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    result = apply_decision(_answer("👍"), jimothy, state, _channel_trigger(channel))
    assert result["event"] == "decision_applied"
    assert result.get("channel_message")


def test_one_agent_thread_never_nudges() -> None:
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


def test_focus_dm_never_nudges() -> None:
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
    notes = db.get_human_chat_thread(ada.id)
    assert any(item.content == "Findings are ready in the review note." for item in notes)

    dm = apply_decision(
        {
            "decision": "answer",
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


def test_explicit_proceed_on_first_attempt_skips_nudge() -> None:
    _jim, _laura, jimothy, channel = _three_members()
    state = db.get_agent_state(jimothy.id)
    assert state is not None
    trigger = _channel_trigger(channel)

    result = apply_decision(
        _answer("Findings are ready in the review note.", proceed=True),
        jimothy,
        state,
        trigger,
    )

    assert result["event"] == "decision_applied"
    assert result.get("channel_message")
    assert NUDGE_SHOWN_KEY not in trigger


def test_compact_proceed_flag_parses() -> None:
    parsed = parse_direct_turn_response(
        '{"act":"reply","intent":"status","msg":"Ready.","data":{"proceed":true},"th":"go"}'
    )
    assert parsed["decision"] == "answer"
    assert parsed["proceedUntagged"] is True
    assert parsed["reply"] == "Ready."


def test_task_follow_up_to_multi_party_channel_nudges() -> None:
    jim, laura, jimothy, channel = _three_members()
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
    trigger = {
        "type": "task_follow_up",
        "task_id": creation.task.id,
        "content": "Share the review findings with the team.",
        "from_name": "Human Operator",
    }

    nudged = apply_decision(
        _answer("Jtech-CLI review summary is ready."),
        jimothy,
        state,
        trigger,
    )
    assert nudged["event"] == "world_feedback"
    assert nudged["detail"] == NUDGE_COPY

    tagged = apply_decision(
        _answer(f"@{jim.name} @{laura.name} review summary is ready."),
        jimothy,
        state,
        trigger,
    )
    assert tagged["event"] == "decision_applied"
    assert tagged.get("channel_message")


def test_continuation_keeps_debra_copy_and_buttons() -> None:
    text = next_owner_nudge_continuation(member_names=["Jim", "Laura", "Human"])[0]["content"]
    assert NUDGE_COPY in text
    assert NUDGE_PROCEED_LABEL in text
    assert NUDGE_ADD_LABEL in text
    assert "soft nudge" in text
    assert "@Jim" in text
    assert "Do not invent @everyone" in text
