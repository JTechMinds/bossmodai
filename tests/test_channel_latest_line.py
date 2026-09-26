"""Channel wakes show the newest thread line as the current message, resolved at turn time."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import db
from core import config
from core.agent_loop.channel_rounds import ROUND_MARKER_KIND
from core.agent_loop.turn_context import stamp_channel_latest_line
from core.default_prompts import load_default_prompt
from core.llm import context_builder

_LATEST_KEYS = ("latest_from_name", "latest_content", "latest_author_type", "latest_from_agent")


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


def _thread():
    brad = db.create_agent("Brad", role="PM", desk_x=1, desk_y=1)
    charles = db.create_agent("Charles", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[brad.id, charles.id],
        created_by=brad.id,
    )
    opener = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Great news folks. Let's try and continue.",
        source_channel="channel",
    )
    return brad, charles, channel, opener


def _agent_line(channel_id: str, agent, content: str):
    return db.create_channel_message(
        channel_id=channel_id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        content=content,
        source_channel="channel",
    )


def _wake(channel_id: str, opener, trigger_type: str = "channel_response") -> dict[str, Any]:
    return {
        "type": trigger_type,
        "channel_id": channel_id,
        "source_message_id": opener.id,
        "content": opener.content,
        "from_name": "Human Operator",
        "author_type": "human",
    }


def test_opener_as_newest_line_stamps_nothing() -> None:
    _brad, charles, channel, opener = _thread()
    trigger = _wake(channel.id, opener)
    stamp_channel_latest_line(charles.id, trigger)
    assert not any(key in trigger for key in _LATEST_KEYS)
    assert trigger["content"] == opener.content


def test_peer_line_after_the_opener_is_the_current_message() -> None:
    brad, charles, channel, opener = _thread()
    _agent_line(channel.id, brad, "I'm clear on my end. @Charles ping me if the fix regresses.")
    trigger = _wake(channel.id, opener)
    stamp_channel_latest_line(charles.id, trigger)
    assert trigger["latest_from_name"] == "Brad"
    assert trigger["latest_content"] == "I'm clear on my end. @Charles ping me if the fix regresses."
    assert trigger["latest_author_type"] == "agent"
    assert trigger["latest_from_agent"] == brad.id
    # The snapshot opener stays for engine logic.
    assert trigger["content"] == opener.content
    assert trigger["source_message_id"] == opener.id


def test_own_newest_line_is_skipped_for_the_newest_line_by_someone_else() -> None:
    brad, charles, channel, opener = _thread()
    _agent_line(channel.id, brad, "Handing the remediation back to Charles.")
    _agent_line(channel.id, charles, "Resuming the 9-gap remediation now.")
    trigger = _wake(channel.id, opener)
    stamp_channel_latest_line(charles.id, trigger)
    assert trigger["latest_from_name"] == "Brad"
    assert trigger["latest_content"] == "Handing the remediation back to Charles."


def test_own_newest_line_over_the_opener_stamps_nothing() -> None:
    _brad, charles, channel, opener = _thread()
    _agent_line(channel.id, charles, "Resuming the 9-gap remediation now.")
    trigger = _wake(channel.id, opener)
    stamp_channel_latest_line(charles.id, trigger)
    assert not any(key in trigger for key in _LATEST_KEYS)


def test_round_markers_and_system_lines_are_skipped() -> None:
    brad, charles, channel, opener = _thread()
    _agent_line(channel.id, brad, "Charles, the toolkit fix is live.")
    db.create_channel_message(
        channel_id=channel.id,
        author_type="system",
        author_name="BossMod",
        content="Round 2",
        source_channel="channel",
        notification_kind=ROUND_MARKER_KIND,
    )
    db.create_channel_message(
        channel_id=channel.id,
        author_type="system",
        author_name="BossMod",
        content="Task status changed.",
        source_channel="channel",
    )
    trigger = _wake(channel.id, opener, "channel_message")
    stamp_channel_latest_line(charles.id, trigger)
    assert trigger["latest_from_name"] == "Brad"
    assert trigger["latest_content"] == "Charles, the toolkit fix is live."


def test_non_channel_triggers_are_untouched() -> None:
    brad, charles, channel, opener = _thread()
    _agent_line(channel.id, brad, "A newer line.")
    for trigger_type in ("human_chat", "peer_message", "session_message", "task_assigned"):
        trigger = _wake(channel.id, opener, trigger_type)
        trigger["latest_content"] = "left alone"
        before = dict(trigger)
        stamp_channel_latest_line(charles.id, trigger)
        assert trigger == before
    no_channel = {"type": "channel_message", "channel_id": "", "latest_content": "left alone"}
    stamp_channel_latest_line(charles.id, no_channel)
    assert no_channel == {"type": "channel_message", "channel_id": "", "latest_content": "left alone"}


def test_stale_latest_keys_are_cleared_on_a_retry() -> None:
    _brad, charles, channel, opener = _thread()
    trigger = _wake(channel.id, opener)
    trigger.update(
        {
            "latest_from_name": "Brad",
            "latest_content": "An old line from a previous attempt.",
            "latest_author_type": "agent",
            "latest_from_agent": "old-agent",
        }
    )
    stamp_channel_latest_line(charles.id, trigger)
    assert not any(key in trigger for key in _LATEST_KEYS)


def _render(trigger: dict[str, Any]) -> str:
    return context_builder._format_trigger(
        trigger,
        "decision",
        {"runtime_block_trigger_event": load_default_prompt("runtime_block_trigger_event")},
    )


def test_seeded_trigger_block_is_the_shipped_template() -> None:
    assert config.require("runtime_block_trigger_event") == load_default_prompt("runtime_block_trigger_event")


def test_channel_message_block_shows_the_latest_line_and_the_opener() -> None:
    trigger = {
        "type": "channel_message",
        "from_name": "Human Operator",
        "content": "Let's try and continue.",
        "latest_from_name": "Brad",
        "latest_content": "@Charles ping me if the fix regresses.",
    }
    text = _render(trigger)
    assert "CURRENT SHARED CHANNEL MESSAGE FROM [Brad]: @Charles ping me if the fix regresses." in text
    assert "Earlier message that opened this exchange, from [Human Operator]: Let's try and continue." in text
    assert "CURRENT SHARED CHANNEL MESSAGE FROM [Human Operator]" not in text
    assert "Choose speak or pass." in text


def test_channel_message_block_without_latest_keeps_the_opener_form() -> None:
    text = _render({"type": "channel_message", "from_name": "Human Operator", "content": "Where are we?"})
    assert "CURRENT SHARED CHANNEL MESSAGE FROM [Human Operator]: Where are we?" in text
    assert "Earlier message that opened this exchange" not in text
    assert "Choose speak or pass." in text


def test_channel_response_block_shows_the_latest_line_and_the_opener() -> None:
    trigger = {
        "type": "channel_response",
        "from_name": "Human Operator",
        "content": "Let's try and continue.",
        "latest_from_name": "Sarah",
        "latest_content": "Re-cert is ready when Charles posts.",
    }
    text = _render(trigger)
    assert "YOUR TURN TO RESPOND IN THE SHARED CHANNEL after [Sarah] said: Re-cert is ready when Charles posts." in text
    assert "Earlier message that opened this exchange, from [Human Operator]: Let's try and continue." in text
    assert "after [Human Operator] said" not in text
    assert "Choose speak or pass." in text


def test_channel_response_block_without_latest_keeps_the_opener_form() -> None:
    text = _render({"type": "channel_response", "from_name": "Human Operator", "content": "Where are we?"})
    assert "YOUR TURN TO RESPOND IN THE SHARED CHANNEL after [Human Operator] said: Where are we?" in text
    assert "Earlier message that opened this exchange" not in text
    assert "Choose speak or pass." in text


def test_envelope_speaker_follows_the_latest_line() -> None:
    opener = {
        "type": "channel_response",
        "from_name": "Human Operator",
        "author_type": "human",
        "content": "Let's try and continue.",
    }
    assert context_builder._conversation_speaker(opener) == ("human", "Human Operator", "human")
    agent_latest = dict(
        opener,
        latest_from_name="Brad",
        latest_content="@Charles ping me.",
        latest_author_type="agent",
        latest_from_agent="brad-id",
    )
    assert context_builder._conversation_speaker(agent_latest) == ("agent", "Brad", "brad-id")
    human_latest = dict(
        opener,
        from_name="Brad",
        author_type="agent",
        from_agent="brad-id",
        latest_from_name="Human Operator",
        latest_content="Pause for now.",
        latest_author_type="human",
        latest_from_agent="",
    )
    assert context_builder._conversation_speaker(human_latest) == ("human", "Human Operator", "human")
    # Other trigger types ignore latest_* entirely.
    peer = {
        "type": "peer_message",
        "from_name": "Brad",
        "from_agent": "brad-id",
        "latest_from_name": "Sarah",
        "latest_content": "ignored",
        "latest_author_type": "agent",
        "latest_from_agent": "sarah-id",
    }
    assert context_builder._conversation_speaker(peer) == ("agent", "Brad", "brad-id")
