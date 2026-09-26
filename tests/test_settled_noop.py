"""Settled agent lines stay out. Progress, questions, and handoffs still wake.

System AI decides. A peer @ on a settled line is not a pin. Operator @
still is. Empty speak on that agent line gets one repair, then stops.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.channel_rounds import advance_channel_round, start_channel_peer_round
from core.agent_loop.runtime_core import AUDIENCE_SOFT_JUDGMENT
from db import channel_host as host_db
from db import channel_response_rounds as channel_round_db
from tests._router_fakes import route_reply

_LOOP_FILES = (
    "channel_router.py",
    "channel_rounds.py",
    "channel_host.py",
    "runtime_core.py",
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


def _trio():
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1, model_work="identity-big")
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1, model_work="identity-big")
    ada = db.create_agent("Ada", role="QA", desk_x=3, desk_y=1, model_work="identity-big")
    channel = db.create_channel(
        name="Ops",
        member_agent_ids=[jim.id, laura.id, ada.id],
        created_by=jim.id,
    )
    return jim, laura, ada, channel


def _enable_system_ai() -> None:
    connection = db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    db.set_setting("system_ai_connection", connection.id, "llm")
    config.reload()


def _script(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> dict[str, Any]:
    calls: dict[str, Any] = {"n": 0, "prompts": []}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        blob = "\n".join(item.get("content") or "" for item in messages)
        calls["prompts"].append(blob)
        if calls["n"] > len(replies):
            raise AssertionError("router was called more times than scripted")
        return route_reply(messages, replies[calls["n"] - 1])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    return calls


def _pending_block(prompt: str) -> str:
    return prompt.split("Pending @:", 1)[1].split("Sticky context:", 1)[0]


def _statuses(round_id: str) -> dict[str, str]:
    return {
        candidate.agent_id: str(candidate.status or "")
        for candidate in db.list_channel_response_candidates(round_id)
    }


def test_runtime_core_prefers_an_engine_pass_when_nothing_changed() -> None:
    assert "When nothing changed, prefer an engine pass over a status essay." in AUDIENCE_SOFT_JUDGMENT
    assert "The recent thread is already in this prompt." in AUDIENCE_SOFT_JUDGMENT
    assert "Repeating that thread is not new work." in AUDIENCE_SOFT_JUDGMENT
    assert "@" not in AUDIENCE_SOFT_JUDGMENT


def test_gate_does_not_string_match_settled_phrases() -> None:
    root = Path(__file__).resolve().parents[1] / "core" / "agent_loop"
    blob = "\n".join((root / name).read_text(encoding="utf-8") for name in _LOOP_FILES)
    lowered = blob.lower()
    assert "signature" not in lowered
    assert "nothing new" not in lowered


def test_operator_at_still_hard_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    _script(monkeypatch, [[jim.id]])
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="@Laura where are we?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert triggers[0]["agent_id"] == laura.id
    round_id = triggers[0]["payload"]["round_id"]
    assert channel_round_db.get_channel_round_meta(round_id)["pinned_ids"][0] == laura.id


@pytest.mark.parametrize(
    "line",
    [
        "Shipped the patch. The suite is green.",
        "Which owner should take the fence?",
        "Handing the review to the next owner.",
    ],
)
def test_progress_question_and_handoff_still_wake(monkeypatch: pytest.MonkeyPatch, line: str) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    calls = _script(monkeypatch, [[laura.id]])
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jim.id,
        author_name=jim.name,
        content=line,
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=line,
        from_name=jim.name,
        author_type="agent",
        exclude_agent_ids={jim.id},
        from_agent=jim.id,
    )
    assert calls["n"] == 1
    assert "Who speaks next?" not in calls["prompts"][-1]
    assert "The latest message is an agent speak." in calls["prompts"][-1]
    assert [item["agent_id"] for item in triggers] == [laura.id]


def test_settled_essay_is_empty_speak_and_stay_out(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    essay = (
        f"@{laura.name} the record is unchanged. My lane is the same as the last pass. "
        "No further action from me."
    )
    calls = _script(
        monkeypatch,
        [
            [],
            [],
        ],
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jim.id,
        author_name=jim.name,
        content=essay,
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=essay,
        from_name=jim.name,
        author_type="agent",
        exclude_agent_ids={jim.id},
        from_agent=jim.id,
    )
    assert calls["n"] == 2
    assert "Who speaks next?" in calls["prompts"][-1]
    assert "Settled status, an echo of a line the thread already shows, or a no-op" in calls["prompts"][0]
    assert "| Laura" not in _pending_block(calls["prompts"][0])
    assert triggers == []
    rounds = db.list_channel_response_rounds(channel.id)
    assert len(rounds) == 1
    assert rounds[0].status == "completed"
    assert _statuses(rounds[0].id) == {laura.id: "observed", ada.id: "observed"}
    assert [item.content for item in db.list_channel_messages(channel.id)] == [essay]


def test_empty_agent_line_repair_can_name_the_next_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    calls = _script(
        monkeypatch,
        [
            [],
            [laura.id],
        ],
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jim.id,
        author_name=jim.name,
        content="The suite failed. Who should take the fix?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name=jim.name,
        author_type="agent",
        exclude_agent_ids={jim.id},
        from_agent=jim.id,
    )
    assert calls["n"] == 2
    assert "Who speaks next?" in calls["prompts"][-1]
    assert [item["agent_id"] for item in triggers] == [laura.id]


def test_peer_at_on_a_settled_line_does_not_open_an_essay_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    essay = (
        f"@{laura.name} @{ada.name} already noted. Lane is unchanged. "
        "Leaving this where it stands."
    )
    calls = _script(
        monkeypatch,
        [
            [jim.id],
            [],
            [],
        ],
    )
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Where are we?",
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
    )
    assert triggers[0]["agent_id"] == jim.id
    round_id = triggers[0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    state = host_db.get_channel_host_state(channel.id)
    state["pass_streaks"] = {laura.id: 2}
    state["demoted_ids"] = [laura.id]
    host_db.save_channel_host_state(state)
    progress = advance_channel_round(
        {
            "content": message.content,
            "channel_id": channel.id,
            "round_id": round_id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": message.id,
            "channel_name": channel.name,
            "dispatch_mode": "rounds",
        },
        spoke=True,
        speaker_id=jim.id,
        spoken_text=essay,
    )
    assert calls["n"] == 3
    follow = calls["prompts"][1]
    assert essay in follow
    assert "The latest message is an agent speak." in follow
    assert "A peer @ on that line is not a pending pin" in follow
    assert "| Laura" not in _pending_block(follow)
    assert "| Ada" not in _pending_block(follow)
    assert "Who speaks next?" in calls["prompts"][-1]
    assert progress["trigger_requests"] == []
    indexes = [
        int(channel_round_db.get_channel_round_meta(row.id)["round_index"])
        for row in db.list_channel_response_rounds(channel.id)
    ]
    assert indexes == [1]
    stayed = host_db.get_channel_host_state(channel.id)
    assert stayed["demoted_ids"] == [laura.id]
    assert stayed["pass_streaks"][laura.id] == 2


def test_words_in_the_line_do_not_override_a_real_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    line = (
        f"@{laura.name} nothing new on the old note, but the signature check "
        "is still open. Can you take it?"
    )
    calls = _script(monkeypatch, [[laura.id]])
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jim.id,
        author_name=jim.name,
        content=line,
        source_channel="channel",
    )
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=line,
        from_name=jim.name,
        author_type="agent",
        exclude_agent_ids={jim.id},
        from_agent=jim.id,
    )
    assert calls["n"] == 1
    assert [item["agent_id"] for item in triggers] == [laura.id]
    assert "| Laura" not in _pending_block(calls["prompts"][0])
