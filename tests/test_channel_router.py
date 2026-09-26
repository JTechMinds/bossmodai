"""System AI channel routes: fail-closed JSON, speak cap, engine pass."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.channel_round_plan import DISPATCH_FANOUT, DISPATCH_ROUNDS
from core.agent_loop.channel_router import (
    ROUTER_SPEAK_CAP,
    build_router_messages,
    finalize_router_lists,
    format_member_line,
    parse_router_payload,
    role_blurb,
    short_sticky_context,
)
from core.agent_loop.channel_rounds import advance_channel_round, start_channel_peer_round
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.prompt_history import build_prompt_history_view
from core.agent_loop.standing_prefs import standing_prefs_file
from core.bm_cli import filesystem
from core.llm.system_completion import (
    SYSTEM_COMPLETION_TIMEOUT_SECONDS,
    complete_text,
)
from db import channel_host as host_db
from db import channel_response_rounds as channel_round_db
from tests._router_fakes import route_reply, router_numbers, speak_reply


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


def _message(channel_id: str, content: str):
    return db.create_channel_message(
        channel_id=channel_id,
        author_type="human",
        author_name="Human Operator",
        content=content,
        source_channel="channel",
    )


def _enable_system_ai() -> None:
    connection = db.create_connection(
        name="System",
        api_base_url="http://127.0.0.1:9/v1",
        api_key="secret",
        model="mock-small",
    )
    db.set_setting("system_ai_connection", connection.id, "llm")
    config.reload()


def _statuses(round_id: str) -> dict[str, str]:
    return {
        candidate.agent_id: str(candidate.status or "")
        for candidate in db.list_channel_response_candidates(round_id)
    }


def _ordered(round_id: str) -> list[str]:
    return [
        candidate.agent_id
        for candidate in sorted(
            db.list_channel_response_candidates(round_id),
            key=lambda candidate: candidate.queue_position or 0,
        )
    ]


def test_speak_cap_and_stall_defaults_stay_put() -> None:
    assert ROUTER_SPEAK_CAP == 2
    assert SYSTEM_COMPLETION_TIMEOUT_SECONDS == 20
    assert config.get("system_ai_max_tokens") == "6144"
    assert config.get("max_concurrent_agent_turns") == "2"
    assert config.get("llm_stall_timeout_seconds") == "120"
    assert config.get("llm_request_timeout_seconds") == "720"
    assert config.get("channel_response_round_cap") == "64"


def test_parser_rejects_invented_keys_and_unknown_numbers() -> None:
    members = {1: "jim", 2: "laura"}
    assert parse_router_payload('{"speak": [1], "stay_out": [2]}', members) is None
    assert parse_router_payload('{"speak": [1], "done": true}', members) is None
    assert parse_router_payload('{"stay_out": [2]}', members) is None
    assert parse_router_payload("{}", members) is None
    assert parse_router_payload('{"speak": [3]}', members) is None
    assert parse_router_payload('{"speak": [0]}', members) is None
    assert parse_router_payload('{"speak": [1, 1]}', members) is None
    assert parse_router_payload('{"speak": ["1"]}', members) is None
    assert parse_router_payload('{"speak": ["jim"]}', members) is None
    assert parse_router_payload('{"speak": [true]}', members) is None
    assert parse_router_payload('{"speak": [1.0]}', members) is None
    assert parse_router_payload('{"speak": 1}', members) is None
    assert parse_router_payload('{"speak": null}', members) is None
    assert parse_router_payload("not json", members) is None
    assert parse_router_payload("[1]", members) is None
    assert parse_router_payload('```json\n{"speak": [2]}\n```', members) == ["laura"]
    assert parse_router_payload('{"speak": [2, 1]}', members) == ["laura", "jim"]
    assert parse_router_payload('{"speak": []}', members) == []
    speak, stay = finalize_router_lists(
        ["jim", "laura", "ada"],
        forced_ids=[],
        speak=["ada", "laura", "jim"],
        cap=ROUTER_SPEAK_CAP,
    )
    assert speak == ["ada", "laura"]
    assert stay == ["jim"]


def test_human_mentions_stay_first_and_are_not_dropped_for_the_cap() -> None:
    speak, stay = finalize_router_lists(
        ["jim", "laura", "ada"],
        forced_ids=["laura", "ada", "jim"],
        speak=["jim"],
        cap=ROUTER_SPEAK_CAP,
    )
    assert speak == ["laura", "ada", "jim"]
    assert stay == []

    speak, stay = finalize_router_lists(
        ["jim", "laura", "ada"],
        forced_ids=["laura"],
        speak=["ada", "jim"],
        cap=ROUTER_SPEAK_CAP,
    )
    assert speak == ["laura", "ada"]
    assert stay == ["jim"]


def test_prompt_lists_specialty_pending_mentions_and_sticky() -> None:
    messages, number_map = build_router_messages(
        members=[
            {"id": "jim", "name": "Jim", "role": "PM"},
            {"id": "laura", "name": "Laura", "role": "Eng"},
        ],
        latest_message="Where are we?",
        pending_mention_ids=["laura"],
        sticky="Jim: keep notes short",
    )
    assert number_map == {1: "jim", 2: "laura"}
    blob = "\n".join(item["content"] for item in messages)
    assert "1 | Jim | PM" in blob
    assert "2 | Laura | Eng" in blob
    assert "Where are we?" in blob
    assert _block(blob, "Pending @:", "Sticky context:") == "2 | Laura"
    assert "Jim: keep notes short" in blob
    assert '"speak"' in blob
    assert "stay_out" not in blob
    assert "exactly one list" not in blob
    assert f"at most {ROUTER_SPEAK_CAP}" in blob
    assert "role blurb" in blob
    assert "left out of this slice is not finished" in blob


def test_member_line_keeps_specialty_and_one_role_blurb() -> None:
    essay = (
        "owns M0 build / stack lock\n\n"
        + ("biography paragraph " * 20)
    )
    line = format_member_line(
        {
            "id": "charles",
            "name": "Charles",
            "role": "Engineer",
            "description": essay,
        },
        3,
    )
    assert line == "3 | Charles | Engineer — owns M0 build / stack lock"
    assert role_blurb(essay) == "owns M0 build / stack lock"
    long_first = "owns the product requirements " + ("detail " * 30)
    clipped = role_blurb(long_first)
    assert clipped.startswith("owns the product requirements")
    assert len(clipped) <= 80
    assert clipped.endswith("...")
    assert long_first not in clipped
    bare = format_member_line({"id": "jim", "name": "Jim", "role": "PM"}, 1)
    assert bare == "1 | Jim | PM"


def _block(blob: str, start: str, end: str | None) -> str:
    tail = blob.split(start, 1)[1]
    return (tail.split(end, 1)[0] if end else tail).strip()


def test_prompt_numbers_members_and_shows_no_member_id() -> None:
    members = [
        {"id": "5d0c1f7e-aaaa-4bbb-8ccc-000000000001", "name": "Brad", "role": "PM"},
        {"id": "5d0c1f7e-aaaa-4bbb-8ccc-000000000002", "name": "Sarah", "role": "QA"},
        {"id": "5d0c1f7e-aaaa-4bbb-8ccc-000000000003", "name": "Charles", "role": "Eng"},
    ]
    outsider = "5d0c1f7e-aaaa-4bbb-8ccc-000000000009"
    messages, number_map = build_router_messages(
        members=members,
        latest_message="Great news folks. Let's try and continue.",
        pending_mention_ids=[members[2]["id"]],
        sticky="",
        already_spoke_ids=[members[0]["id"], outsider],
        work_bind_ids=[members[1]["id"]],
    )
    assert number_map == {index + 1: member["id"] for index, member in enumerate(members)}
    blob = "\n".join(item["content"] for item in messages)
    for member_id in [member["id"] for member in members] + [outsider]:
        assert member_id not in blob
    assert _block(blob, "Members:", "Pending @:") == "1 | Brad | PM\n2 | Sarah | QA\n3 | Charles | Eng"
    assert _block(blob, "Pending @:", "Sticky context:") == "3 | Charles"
    # The outsider is not a member, so it gets no number and is left out.
    assert _block(blob, "Already spoke:", "Work-bound:") == "1 | Brad"
    assert _block(blob, "Work-bound:", None) == "2 | Sarah"


def test_route_sees_the_hire_blurb_and_not_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    db.update_agent(
        jim.id,
        description="owns the product brief\n\nStanding note body that must stay off the roster.",
        prompt_template="FULL PROMPT SHOULD NOT APPEAR",
    )
    _enable_system_ai()
    seen: list[str] = []
    seen_messages: list[list[dict[str, str]]] = []

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        seen.append("\n".join(item["content"] for item in messages))
        seen_messages.append(messages)
        return speak_reply(messages, [laura.id])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Who should own the requirements?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == laura.id
    prompt = seen[0]
    numbers = router_numbers(seen_messages[0])
    assert f"{numbers['Jim']} | Jim | PM — owns the product brief" in prompt
    assert "Standing note body" not in prompt
    assert "FULL PROMPT" not in prompt
    laura_line = f"{numbers['Laura']} | Laura | Eng"
    assert laura_line in prompt
    assert " — " not in prompt.split(laura_line, 1)[1].split("\n", 1)[0]
    for agent in (jim, laura, ada):
        assert agent.id not in prompt


def test_unset_system_ai_keeps_drain_order_and_does_not_call_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: Any) -> None:
        raise AssertionError("system model was called")

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _boom)
    jim, laura, ada, channel = _trio()
    message = _message(channel.id, "@Laura where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert [item["agent_id"] for item in triggers] == [laura.id]
    round_id = triggers[0]["payload"]["round_id"]
    meta = channel_round_db.get_channel_round_meta(round_id)
    assert meta["router_mode"] == "fallback"
    assert meta["pinned_ids"][0] == laura.id
    assert _statuses(round_id)[laura.id] == "queued"
    assert set(_statuses(round_id)) == {jim.id, laura.id, ada.id}
    assert all(status != "observed" for status in _statuses(round_id).values())


def test_route_skips_unselected_identity_model_and_posts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    seen: list[list[dict[str, str]]] = []

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        seen.append(messages)
        return speak_reply(messages, [ada.id])

    def _identity(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("identity model or warm context was built")

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    monkeypatch.setattr("core.llm.client.completion", _identity)
    monkeypatch.setattr("core.llm.context_builder.build_context", _identity)
    message = _message(channel.id, "Where are we?")
    before = [item.id for item in db.list_channel_messages(channel.id)]
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert len(seen) == 1
    prompt = "\n".join(item["content"] for item in seen[0])
    assert "PM" in prompt and "Eng" in prompt and "QA" in prompt
    assert [item["agent_id"] for item in triggers] == [ada.id]
    assert triggers[0]["trigger_type"] == "channel_message"
    assert triggers[0]["payload"]["dispatch_mode"] == DISPATCH_ROUNDS
    round_id = triggers[0]["payload"]["round_id"]
    status = _statuses(round_id)
    assert status[ada.id] == "queued"
    assert status[jim.id] == "observed"
    assert status[laura.id] == "observed"
    assert [item.id for item in db.list_channel_messages(channel.id)] == before
    for agent in (jim, laura):
        diags = db.get_diagnostics(agent_id=agent.id)
        passed = [row for row in diags if row.get("action_name") == "channel_pass"]
        assert passed
        assert passed[0].get("model") in {None, ""}
        assert passed[0].get("context") in {None, ""}
    meta = channel_round_db.get_channel_round_meta(round_id)
    assert meta["router_mode"] == "system"
    assert jim.id in meta["stepped_out"]
    assert laura.id in meta["stepped_out"]


def test_human_mention_is_woken_ahead_of_the_router_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return speak_reply(messages, [ada.id])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "@Laura where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == laura.id
    round_id = triggers[0]["payload"]["round_id"]
    assert _ordered(round_id)[0] == laura.id
    assert _statuses(round_id)[laura.id] == "queued"
    assert _statuses(round_id)[ada.id] == "pending"
    assert _statuses(round_id)[jim.id] == "observed"
    assert channel_round_db.get_channel_round_meta(round_id)["pinned_ids"][0] == laura.id


def test_waiting_human_mention_is_not_passed_on_redecide(monkeypatch: pytest.MonkeyPatch) -> None:
    _jim, laura, ada, channel = _trio()
    _enable_system_ai()
    calls = {"n": 0}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("a still-waiting human mention is not re-routed off the queue")
        return speak_reply(messages, [ada.id])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "@Laura @Ada where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == laura.id
    round_id = triggers[0]["payload"]["round_id"]
    assert _statuses(round_id)[_jim.id] == "observed"
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=laura.id)
    progress = advance_channel_round(
        {
            "content": message.content,
            "channel_id": channel.id,
            "round_id": round_id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": message.id,
            "dispatch_mode": DISPATCH_ROUNDS,
        },
        spoke=True,
        speaker_id=laura.id,
        spoken_text="Shipping today.",
    )
    assert [item["agent_id"] for item in progress["trigger_requests"]] == [ada.id]
    assert _statuses(round_id)[ada.id] == "queued"
    assert calls["n"] == 1


def test_all_stay_out_completes_the_round_without_a_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return speak_reply(messages, [])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers == []
    rounds = db.list_channel_response_rounds(channel.id)
    assert len(rounds) == 1
    assert rounds[0].status == "completed"
    assert [item.id for item in db.list_channel_messages(channel.id)] == [message.id]
    for agent in (jim, laura, ada):
        assert any(row.get("action_name") == "channel_pass" for row in db.get_diagnostics(agent_id=agent.id))


def test_bad_json_falls_back_to_drain_order(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return '{"speak": ["x"], "stay_out": [], "note": "nope"}'

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == jim.id
    round_id = triggers[0]["payload"]["round_id"]
    assert channel_round_db.get_channel_round_meta(round_id)["router_mode"] == "fallback"
    assert set(_statuses(round_id)) == {jim.id, laura.id, ada.id}
    assert all(status != "observed" for status in _statuses(round_id).values())


def test_fanout_does_not_ask_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        raise AssertionError("fan-out must not route")

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "@everyone ship the notes")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert {item["agent_id"] for item in triggers} == {jim.id, laura.id, ada.id}
    assert triggers[0]["payload"]["dispatch_mode"] == DISPATCH_FANOUT


def test_selected_member_still_posts_a_speak_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _jim, laura, _ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [laura.id],
        [],
        [],
    ]

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    state = db.get_agent_state(laura.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "Shipping today.",
            "proceedUntagged": True,
        },
        laura,
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
    assert result.get("channel_message", {}).get("content") == "Shipping today."
    assert result["trigger_requests"] == []


def test_redecide_after_a_speak_passes_the_rest_without_a_second_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        [],
        [],
        [],
    ]

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert [item["agent_id"] for item in triggers] == [jim.id]
    round_id = triggers[0]["payload"]["round_id"]
    assert _statuses(round_id)[laura.id] == "pending"
    assert _statuses(round_id)[ada.id] == "observed"
    before = [item.id for item in db.list_channel_messages(channel.id)]
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    progress = advance_channel_round(
        {
            "content": message.content,
            "channel_id": channel.id,
            "round_id": round_id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": message.id,
            "channel_name": channel.name,
            "dispatch_mode": DISPATCH_ROUNDS,
            "round_index": 1,
        },
        spoke=True,
        speaker_id=jim.id,
        spoken_text="Shipping today.",
    )
    assert progress["trigger_requests"] == []
    assert _statuses(round_id)[laura.id] == "observed"
    assert [item.id for item in db.list_channel_messages(channel.id)] == before
    queued = [
        candidate.agent_id
        for candidate in db.list_channel_response_candidates(round_id)
        if candidate.status in {"queued", "responding"}
    ]
    assert queued == []
    laura_pass = [
        row for row in db.get_diagnostics(agent_id=laura.id) if row.get("action_name") == "channel_pass"
    ]
    assert laura_pass
    assert laura_pass[0].get("model") in {None, ""}
    ada_pass = [row for row in db.get_diagnostics(agent_id=ada.id) if row.get("action_name") == "channel_pass"]
    assert ada_pass
    assert ada_pass[0].get("model") in {None, ""}


def test_failed_redecide_keeps_the_remaining_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        "nope",
    ]

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    round_id = triggers[0]["payload"]["round_id"]
    assert triggers[0]["agent_id"] == jim.id
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    progress = advance_channel_round(
        {
            "content": message.content,
            "channel_id": channel.id,
            "round_id": round_id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": message.id,
            "dispatch_mode": DISPATCH_ROUNDS,
        },
        spoke=True,
        speaker_id=jim.id,
        spoken_text="Next.",
    )
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    assert _statuses(round_id)[laura.id] == "queued"
    assert _statuses(round_id)[ada.id] == "observed"
    assert channel_round_db.get_channel_round_meta(round_id)["router_mode"] == "fallback"


def test_follow_up_round_uses_a_new_route(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, _ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        [laura.id],
        [jim.id],
    ]
    calls = {"n": 0}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    base = {
        "content": message.content,
        "channel_id": channel.id,
        "from_name": "Human Operator",
        "author_type": "human",
        "source_message_id": message.id,
        "channel_name": channel.name,
        "dispatch_mode": DISPATCH_ROUNDS,
    }
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
        exclude_agent_ids={_ada.id},
    )
    assert triggers[0]["agent_id"] == jim.id
    current = dict(base)
    current["round_id"] = triggers[0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=current["round_id"], agent_id=jim.id)
    progress = advance_channel_round(current, spoke=True, speaker_id=jim.id, spoken_text="Still here.")
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    laura_round = progress["trigger_requests"][0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=laura_round, agent_id=laura.id)
    spoken = dict(base)
    spoken["round_id"] = laura_round
    progress = advance_channel_round(spoken, spoke=True, speaker_id=laura.id, spoken_text="Agreed.")
    assert calls["n"] == 3
    assert [item["agent_id"] for item in progress["trigger_requests"]] == [jim.id]
    follow_id = progress["trigger_requests"][0]["payload"]["round_id"]
    assert follow_id != laura_round
    assert _statuses(follow_id)[jim.id] == "queued"
    assert _statuses(follow_id)[laura.id] == "observed"
    assert progress["round_marker"]["content"] == "Round 2"


def test_named_plan_longer_than_the_slice_wakes_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return speak_reply(messages, [jim.id, laura.id, ada.id])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert [item["agent_id"] for item in triggers] == [jim.id]
    round_id = triggers[0]["payload"]["round_id"]
    assert _statuses(round_id)[jim.id] == "queued"
    assert _statuses(round_id)[laura.id] == "pending"
    assert _statuses(round_id)[ada.id] == "pending"
    assert ROUTER_SPEAK_CAP == 2


def test_later_route_names_someone_the_first_slice_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        [ada.id],
    ]

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert [item["agent_id"] for item in triggers] == [jim.id]
    round_id = triggers[0]["payload"]["round_id"]
    assert _statuses(round_id)[ada.id] == "observed"
    assert _statuses(round_id)[laura.id] == "pending"
    prior = "Need a fixture check before we call this done."
    db.create_channel_message(
        channel_id=channel.id,
        author_type="agent",
        author_agent_id=jim.id,
        author_name="Jim",
        content=prior,
        source_channel="channel",
    )
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    progress = advance_channel_round(
        {
            "content": message.content,
            "channel_id": channel.id,
            "round_id": round_id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": message.id,
            "channel_name": channel.name,
            "dispatch_mode": DISPATCH_ROUNDS,
        },
        spoke=True,
        speaker_id=jim.id,
        spoken_text=prior,
    )
    assert [item["agent_id"] for item in progress["trigger_requests"]] == [ada.id]
    assert _statuses(round_id)[ada.id] == "queued"
    assert _statuses(round_id)[laura.id] == "observed"
    ada_agent = db.get_agent(ada.id)
    assert ada_agent is not None
    history = build_prompt_history_view(
        ada_agent,
        {
            "type": "channel_message",
            "channel_id": channel.id,
            "source_message_id": message.id,
        },
    )
    heard = " ".join(str(item.get("content") or "") for item in history.conversation_history)
    assert prior in heard
    streaks = host_db.get_channel_host_state(channel.id)["pass_streaks"]
    assert ada.id not in streaks


def test_empty_reroute_leaves_the_outsider_and_restores_stay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        [],
        [],
        [],
    ]

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    round_id = triggers[0]["payload"]["round_id"]
    state = host_db.get_channel_host_state(channel.id)
    state["pass_streaks"] = {laura.id: 2}
    state["demoted_ids"] = [laura.id]
    host_db.save_channel_host_state(state)
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    progress = advance_channel_round(
        {
            "content": message.content,
            "channel_id": channel.id,
            "round_id": round_id,
            "from_name": "Human Operator",
            "author_type": "human",
            "source_message_id": message.id,
            "dispatch_mode": DISPATCH_ROUNDS,
        },
        spoke=True,
        speaker_id=jim.id,
        spoken_text="Signature is on record. Nothing further from me.",
    )
    assert progress["trigger_requests"] == []
    assert _statuses(round_id)[ada.id] == "observed"
    stayed = host_db.get_channel_host_state(channel.id)
    assert stayed["demoted_ids"] == [laura.id]
    assert stayed["pass_streaks"][laura.id] == 2


def test_sticky_context_is_short_and_skips_note_bodies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    # The prefs store is system-owned, outside the agents root; keep it per-test too.
    monkeypatch.setattr(filesystem, "_SYSTEM_ROOT", tmp_path / "system")
    jim, _laura, _ada, _channel = _trio()
    agent = db.get_agent(jim.id)
    assert agent is not None
    path = standing_prefs_file(agent.storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    pref_text = ("keep status lines short " + ("word " * 30)).strip()[:160]
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "prefs": [
                    {
                        "id": "notes",
                        "kind": "preference",
                        "text": pref_text,
                        "sources": ["operator"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sticky = short_sticky_context(
        [{"id": jim.id, "name": "Jim", "role": "PM"}],
        opening_message="Opening question that is not the latest line.",
    )
    assert sticky.startswith("Opening:")
    assert "Jim:" in sticky
    assert "keep status lines short" in sticky
    assert pref_text not in sticky
    assert len(sticky) <= 400


def test_system_completion_uses_the_connection_model_not_an_identity_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_system_ai()
    seen: dict[str, Any] = {}

    def _fake(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"choices": [{"message": {"content": "{\"ok\":true}"}}]}

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake)
    text = complete_text([{"role": "user", "content": "hi"}])
    assert text == '{"ok":true}'
    assert seen["model"] == "openai/mock-small"
    assert seen["temperature"] == 0
    assert seen["max_tokens"] == 6144
    assert seen["timeout"] == SYSTEM_COMPLETION_TIMEOUT_SECONDS
    assert seen["stream"] is False
    assert seen["api_key"] == "secret"
    assert "identity-big" not in str(seen["model"])


def test_system_completion_cap_follows_the_setting_and_explicit_caps_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_system_ai()
    seen: list[int] = []

    def _fake(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["max_tokens"])
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake)
    db.set_setting("system_ai_max_tokens", "9000", "llm")
    config.reload()
    assert complete_text([{"role": "user", "content": "hi"}]) == "ok"
    assert complete_text([{"role": "user", "content": "hi"}], max_tokens=180) == "ok"
    assert seen == [9000, 180]


def test_system_completion_logs_truncation_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _enable_system_ai()

    class _Message:
        content = '{"speak": [1'

    class _Choice:
        message = _Message()
        finish_reason = "length"

    class _Response:
        choices = [_Choice()]

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", lambda **_kwargs: _Response())
    with caplog.at_level(logging.WARNING, logger="core.llm.system_completion"):
        text = complete_text([{"role": "user", "content": "hi"}])
    assert text == '{"speak": [1'
    assert "system completion truncated at max_tokens=6144" in caplog.text

    caplog.clear()
    monkeypatch.setattr(
        "core.llm.system_completion.litellm.completion",
        lambda **_kwargs: {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    )
    with caplog.at_level(logging.WARNING, logger="core.llm.system_completion"):
        assert complete_text([{"role": "user", "content": "hi"}]) is None
    assert "system completion truncated at max_tokens=6144" in caplog.text
    assert "system completion returned empty text" in caplog.text


def test_rejected_or_missing_route_logs_a_fallback_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    replies: list[str | None] = ['{"speak": [1', None]
    monkeypatch.setattr(
        "core.agent_loop.channel_router.complete_text",
        lambda _messages, **_kwargs: replies.pop(0),
    )
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.channel_router"):
        rejected = start_channel_peer_round(
            channel_id=channel.id,
            message_id=_message(channel.id, "Where are we?").id,
            content="Where are we?",
            from_name="Human Operator",
            author_type="human",
            channel_name=channel.name,
        )
    assert "channel router fell back to drain order: rejected" in caplog.text
    assert channel_round_db.get_channel_round_meta(rejected[0]["payload"]["round_id"])["router_mode"] == "fallback"
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.channel_router"):
        missing = start_channel_peer_round(
            channel_id=channel.id,
            message_id=_message(channel.id, "And now?").id,
            content="And now?",
            from_name="Human Operator",
            author_type="human",
            channel_name=channel.name,
        )
    assert "channel router fell back to drain order: no_completion" in caplog.text
    assert channel_round_db.get_channel_round_meta(missing[0]["payload"]["round_id"])["router_mode"] == "fallback"
    assert replies == []


def test_reroute_prompt_states_echo_fail_closed() -> None:
    routed, _numbers = build_router_messages(
        members=[
            {"id": "jim", "name": "Jim", "role": "PM"},
            {"id": "laura", "name": "Laura", "role": "Eng"},
        ],
        latest_message="The plan is filed.",
        pending_mention_ids=[],
        sticky="",
        already_spoke_ids=["jim"],
        work_bind_ids=["laura"],
    )
    blob = "\n".join(item["content"] for item in routed)
    assert "Human snapshot:" not in blob
    assert "Round:" not in blob
    assert _block(blob, "Already spoke:", "Work-bound:") == "1 | Jim"
    assert _block(blob, "Work-bound:", None) == "2 | Laura"
    assert "If you are unsure whether an already-spoke member would add new substance" in blob
    assert "Do not name someone because they might have something" in blob
    assert "A member who has not spoken may still be named" in blob
    assert "Leave work-bound members out of speak" in blob
    plain, _numbers = build_router_messages(
        members=[{"id": "jim", "name": "Jim", "role": "PM"}],
        latest_message="Where are we?",
        pending_mention_ids=[],
        sticky="",
    )
    plain_blob = "\n".join(item["content"] for item in plain)
    assert "If you are unsure whether an already-spoke member would add new substance" not in plain_blob
    assert "Already spoke:" not in plain_blob


def _scripted_route(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> list[str]:
    prompts: list[str] = []

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        prompts.append("\n".join(item.get("content") or "" for item in messages))
        if len(prompts) > len(replies):
            raise AssertionError("router was called more times than scripted")
        return route_reply(messages, replies[len(prompts) - 1])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    return prompts


def _human_round(channel, content: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    message = _message(channel.id, content)
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    base = {
        "content": message.content,
        "channel_id": channel.id,
        "from_name": "Human Operator",
        "author_type": "human",
        "source_message_id": message.id,
        "channel_name": channel.name,
        "dispatch_mode": DISPATCH_ROUNDS,
    }
    return base, triggers


def test_reroute_after_speakers_stays_out_on_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    prompts = _scripted_route(
        monkeypatch,
        [
            [jim.id, laura.id],
            [laura.id],
            [],
            [],
        ],
    )
    base, triggers = _human_round(channel, "Where are we?")
    assert [item["agent_id"] for item in triggers] == [jim.id]
    round_id = triggers[0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    current = dict(base)
    current["round_id"] = round_id
    progress = advance_channel_round(current, spoke=True, speaker_id=jim.id, spoken_text="Plan: ship the notes.")
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    laura_round = progress["trigger_requests"][0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=laura_round, agent_id=laura.id)
    spoken = dict(base)
    spoken["round_id"] = laura_round
    progress = advance_channel_round(spoken, spoke=True, speaker_id=laura.id, spoken_text="Plan: ship the notes.")
    assert progress["trigger_requests"] == []
    both = []
    for blob in prompts:
        if "Already spoke:" not in blob:
            continue
        spoke = blob.split("Already spoke:", 1)[1].split("Work-bound:", 1)[0]
        if "| Jim" in spoke and "| Laura" in spoke:
            both.append(blob)
    assert both
    assert any(
        "If you are unsure whether an already-spoke member would add new substance" in blob for blob in both
    )


def test_reroute_still_speaks_for_new_substance(monkeypatch: pytest.MonkeyPatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    prompts = _scripted_route(
        monkeypatch,
        [
            [jim.id, laura.id],
            [laura.id],
            [jim.id],
        ],
    )
    base, triggers = _human_round(channel, "Where are we?")
    round_id = triggers[0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=round_id, agent_id=jim.id)
    current = dict(base)
    current["round_id"] = round_id
    progress = advance_channel_round(current, spoke=True, speaker_id=jim.id, spoken_text="Plan: ship the notes.")
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    laura_round = progress["trigger_requests"][0]["payload"]["round_id"]
    db.mark_channel_candidate_responded(round_id=laura_round, agent_id=laura.id)
    spoken = dict(base)
    spoken["round_id"] = laura_round
    progress = advance_channel_round(
        spoken,
        spoke=True,
        speaker_id=laura.id,
        spoken_text="The fixture failed. Who takes the fix?",
    )
    assert [item["agent_id"] for item in progress["trigger_requests"]] == [jim.id]
    assert any("If you are unsure whether an already-spoke member would add new substance" in blob for blob in prompts)


def test_system_completion_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_system_ai()

    def _fake(**_kwargs: Any) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake)
    assert complete_text([{"role": "user", "content": "hi"}]) is None
