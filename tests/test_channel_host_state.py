"""Talk / Work / Paused host brakes. No identity-model calls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import db
from core import config
from core.agent_loop.channel_host import (
    PAUSE_LINE,
    RESUME_LINE,
    is_ack_phrase,
    is_pause_phrase,
    is_thread_paused,
    pause_thread,
    resume_thread,
    shape_follow_up_speak,
    work_holds_talk,
)
from core.agent_loop.channel_round_plan import DISPATCH_FANOUT, DISPATCH_ROUNDS
from core.agent_loop.channel_rounds import advance_channel_round, start_channel_peer_round
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.task_origin_mirrors import persist_origin_status_line
from core.messaging import route_human_channel_message
from db import channel_host as host_db
from db import channel_response_rounds as channel_round_db
from db.settings import reconcile_factory_round_cap
from tests._router_fakes import route_reply, speak_reply


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
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    ada = db.create_agent("Ada", role="QA", desk_x=3, desk_y=1)
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


def _base(channel, message) -> dict[str, Any]:
    return {
        "content": message.content,
        "channel_id": channel.id,
        "from_name": "Human Operator",
        "author_type": "human",
        "source_message_id": message.id,
        "channel_name": channel.name,
        "dispatch_mode": DISPATCH_ROUNDS,
    }


def _queue_ids(round_id: str) -> list[str]:
    candidates = db.list_channel_response_candidates(round_id)
    candidates.sort(key=lambda candidate: (candidate.queue_position or 9999, candidate.created_at))
    return [candidate.agent_id for candidate in candidates]


def _finish(round_id: str, agent_id: str, trigger: dict[str, Any], *, spoke: bool, text: str = "") -> dict[str, Any]:
    if spoke:
        db.mark_channel_candidate_responded(round_id=round_id, agent_id=agent_id)
    else:
        db.mark_channel_candidate_observed(round_id=round_id, agent_id=agent_id)
    current = dict(trigger)
    current["round_id"] = round_id
    return advance_channel_round(current, spoke=spoke, speaker_id=agent_id, spoken_text=text)


def _queue_work(agent_id: str, channel_id: str) -> None:
    db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Continue the bound task.", "channel_id": channel_id},
    )


def _work_still_queued(agent_id: str) -> bool:
    return any(
        item.agent_id == agent_id and item.trigger_type == "activity_resumed"
        for item in db.list_queued_triggers(limit=20)
    )


class _SilentBroadcast:
    async def broadcast_channel_message(self, **_kwargs: Any) -> None:
        return None


class _RecordingServices:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_pause_and_ack_phrases_stay_narrow() -> None:
    assert is_pause_phrase("let's pause here")
    assert is_pause_phrase("Let's pause.")
    assert is_pause_phrase("stop")
    assert is_pause_phrase("hold")
    assert is_pause_phrase("please hold on")
    assert not is_pause_phrase("stop the deploy")
    assert not is_pause_phrase("let's pause the rollout until the tests are green")
    assert is_ack_phrase("Copy that.")
    assert is_ack_phrase("got it")
    assert not is_ack_phrase("Copy that, but the queue should keep draining")
    assert not is_ack_phrase("I think we should keep the explicit drain")


def test_empty_speak_stops_the_snapshot_and_leaves_work_wakes(monkeypatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    calls = {"n": 0}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return speak_reply(messages, [jim.id, laura.id])
        if calls["n"] == 2:
            return speak_reply(messages, [laura.id])
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
    assert triggers[0]["agent_id"] == jim.id
    base = _base(channel, message)
    _queue_work(jim.id, channel.id)
    progress = _finish(triggers[0]["payload"]["round_id"], jim.id, base, spoke=True, text="The drain should stay ordered.")
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    laura_round = progress["trigger_requests"][0]["payload"]["round_id"]
    progress = _finish(laura_round, laura.id, base, spoke=True, text="Agreed, and the cap is not the brake.")
    assert progress["trigger_requests"] == []
    assert _work_still_queued(jim.id)
    # Follow-up empty speak gets one repair, then the snapshot stops.
    assert calls["n"] == 4


def test_empty_speak_stops_after_a_passed_human_mention(monkeypatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    calls = {"n": 0}

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return speak_reply(messages, [jim.id])
        return speak_reply(messages, [])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "@Jim where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == jim.id
    assert host_db.get_channel_host_state(channel.id)["protected_ids"][0] == jim.id
    speak, stay = shape_follow_up_speak(
        channel.id,
        mode="system",
        speak=[],
        ordered=[jim.id, laura.id, ada.id],
        required_ids=[jim.id],
        mention_ids=[jim.id],
    )
    assert speak == []
    assert stay == []
    progress = _finish(
        triggers[0]["payload"]["round_id"],
        jim.id,
        _base(channel, message),
        spoke=False,
    )
    assert progress["trigger_requests"] == []
    indexes = [
        int(channel_round_db.get_channel_round_meta(row.id)["round_index"])
        for row in db.list_channel_response_rounds(channel.id)
    ]
    assert indexes == [1]
    assert calls["n"] == 2


def test_two_passes_demote_until_a_peer_speaks_or_a_human_at(monkeypatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        [jim.id, laura.id],
        [jim.id, laura.id, ada.id],
        [laura.id],
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
    base = _base(channel, message)
    progress = _finish(triggers[0]["payload"]["round_id"], jim.id, base, spoke=False)
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    progress = _finish(progress["trigger_requests"][0]["payload"]["round_id"], laura.id, base, spoke=False)
    assert progress["trigger_requests"][0]["agent_id"] == jim.id
    progress = _finish(progress["trigger_requests"][0]["payload"]["round_id"], jim.id, base, spoke=False)
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    laura_round = progress["trigger_requests"][0]["payload"]["round_id"]
    progress = _finish(laura_round, laura.id, base, spoke=False)
    assert progress["trigger_requests"] == []
    demoted = set(host_db.get_channel_host_state(channel.id)["demoted_ids"])
    assert jim.id in demoted
    assert laura.id in demoted

    progress = _finish(
        laura_round,
        laura.id,
        base,
        spoke=True,
        text="The second pass should not stick once someone actually speaks.",
    )
    assert jim.id not in host_db.get_channel_host_state(channel.id)["demoted_ids"]
    assert progress["trigger_requests"][0]["agent_id"] == laura.id


def test_human_mention_is_not_demoted_away(monkeypatch) -> None:
    jim, laura, _ada, channel = _trio()
    _enable_system_ai()
    replies = [
        [jim.id, laura.id],
        [laura.id],
        [laura.id],
    ]

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return route_reply(messages, replies.pop(0))

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "@Jim where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers[0]["agent_id"] == jim.id
    protected = host_db.get_channel_host_state(channel.id)["protected_ids"]
    assert protected[0] == jim.id
    base = _base(channel, message)
    progress = _finish(triggers[0]["payload"]["round_id"], jim.id, base, spoke=False)
    progress = _finish(progress["trigger_requests"][0]["payload"]["round_id"], laura.id, base, spoke=False)
    # The router left Jim out. The human @ stays first anyway.
    assert progress["trigger_requests"][0]["agent_id"] == jim.id
    progress = _finish(progress["trigger_requests"][0]["payload"]["round_id"], jim.id, base, spoke=False)
    assert jim.id in host_db.get_channel_host_state(channel.id)["demoted_ids"]
    assert progress["trigger_requests"][0]["agent_id"] == laura.id
    progress = _finish(progress["trigger_requests"][0]["payload"]["round_id"], laura.id, base, spoke=False)
    assert jim.id in host_db.get_channel_host_state(channel.id)["demoted_ids"]
    assert progress["trigger_requests"][0]["agent_id"] == jim.id


def test_pause_ends_the_snapshot_and_a_phrase_does_too() -> None:
    jim, laura, _ada, channel = _trio()
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert triggers
    _queue_work(jim.id, channel.id)
    marker = pause_thread(channel.id)
    assert marker is not None
    assert marker["content"] == PAUSE_LINE
    assert "Resume by restarting the conversation" in marker["content"]
    assert db.list_channel_response_rounds(channel.id, status="active") == []
    assert is_thread_paused(channel.id)
    assert _work_still_queued(jim.id)
    assert start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content="Still going?",
        from_name=laura.name,
        author_type="agent",
        channel_name=channel.name,
        from_agent=laura.id,
    ) == []

    phrase = _message(channel.id, "let's pause here")
    assert start_channel_peer_round(
        channel_id=channel.id,
        message_id=phrase.id,
        content=phrase.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    ) == []
    assert is_thread_paused(channel.id)

    # A design sentence that merely contains those words is not Pause.
    resume_thread(channel.id)
    talking = _message(channel.id, "stop the deploy and tell me the status")
    resumed = start_channel_peer_round(
        channel_id=channel.id,
        message_id=talking.id,
        content=talking.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    assert resumed
    assert not is_thread_paused(channel.id)


def test_clear_pause_words_pause_and_a_new_human_line_resumes() -> None:
    jim, _laura, _ada, channel = _trio()
    message = _message(channel.id, "stop")
    assert start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    ) == []
    assert is_thread_paused(channel.id)
    assert any(item.content == PAUSE_LINE for item in db.list_channel_messages(channel.id))

    hold = _message(channel.id, "hold")
    # Already paused: the phrase does not open Talk and does not stack markers.
    before = [item.content for item in db.list_channel_messages(channel.id)]
    assert start_channel_peer_round(
        channel_id=channel.id,
        message_id=hold.id,
        content=hold.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    ) == []
    assert [item.content for item in db.list_channel_messages(channel.id)] == before


async def _resume_by_restarting(channel_id: str, channel_name: str) -> list[dict[str, Any]]:
    services = _RecordingServices()
    await route_human_channel_message(
        channel_id=channel_id,
        channel_name=channel_name,
        content="Let's pick the design back up.",
        from_name="Human Operator",
        broadcast_manager=_SilentBroadcast(),
        services=services,
    )
    return services.calls


def test_restarting_the_conversation_resumes_without_opening_from_resume_alone() -> None:
    import asyncio

    _jim, _laura, _ada, channel = _trio()
    pause_thread(channel.id)
    marker = resume_thread(channel.id)
    assert marker is not None
    assert marker["content"] == RESUME_LINE
    assert db.list_channel_response_rounds(channel.id, status="active") == []
    pause_thread(channel.id)
    calls = asyncio.run(_resume_by_restarting(channel.id, channel.name))
    assert calls
    assert calls[0]["trigger_type"] == "channel_message"
    assert not is_thread_paused(channel.id)


def test_narrow_dup_ack_stops_and_a_real_reply_does_not() -> None:
    jim, laura, ada, channel = _trio()
    message = _message(channel.id, "Where are we?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    base = _base(channel, message)
    order = _queue_ids(triggers[0]["payload"]["round_id"])
    assert order[0] == jim.id
    second, third = order[1], order[2]
    progress = _finish(triggers[0]["payload"]["round_id"], jim.id, base, spoke=True, text="Copy that.")
    assert progress["trigger_requests"][0]["agent_id"] == second
    progress = _finish(
        progress["trigger_requests"][0]["payload"]["round_id"],
        second,
        base,
        spoke=True,
        text="Copy that",
    )
    assert progress["trigger_requests"] == []

    message = _message(channel.id, "Where are we now?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    base = _base(channel, message)
    progress = _finish(triggers[0]["payload"]["round_id"], jim.id, base, spoke=True, text="Copy that.")
    progress = _finish(
        progress["trigger_requests"][0]["payload"]["round_id"],
        second,
        base,
        spoke=True,
        text="I disagree — the drain should stay explicit for this design.",
    )
    assert progress["trigger_requests"][0]["agent_id"] == third


def test_substantive_rounds_are_not_stopped_by_the_old_cap_of_four(monkeypatch) -> None:
    jim, laura, ada, channel = _trio()
    _enable_system_ai()

    def _route(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        return speak_reply(messages, [jim.id])

    monkeypatch.setattr("core.agent_loop.channel_router.complete_text", _route)
    message = _message(channel.id, "Let's design the queue.")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    base = _base(channel, message)
    wake = triggers[0]
    seen: list[int] = []
    for index in range(1, 6):
        seen.append(int(wake["payload"]["round_index"]))
        progress = _finish(
            wake["payload"]["round_id"],
            jim.id,
            base,
            spoke=True,
            text=f"Design point {index}: keep Talk on the router, not the cap.",
        )
        assert progress["trigger_requests"], index
        wake = progress["trigger_requests"][0]
    assert max(seen) >= 4
    assert int(wake["payload"]["round_index"]) >= 5


def test_work_bind_ends_peer_talk_and_speak_worthy_reentry_does_not_ping() -> None:
    jim, laura, _ada, channel = _trio()
    message = _message(channel.id, "Please take the notes.")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    state = db.get_agent_state(jim.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Write the notes",
            "reply": "I'll take the notes task.",
        },
        jim,
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
    kinds = [item.get("trigger_type") for item in result["trigger_requests"]]
    assert "activity_resumed" in kinds
    assert "channel_message" not in kinds
    assert work_holds_talk(channel.id)
    assert start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content="I have a thought.",
        from_name=laura.name,
        author_type="agent",
        from_agent=laura.id,
        channel_name=channel.name,
    ) == []
    _queue_work(jim.id, channel.id)
    assert _work_still_queued(jim.id)

    task = next(item for item in db.list_tasks(assigned_to=jim.id) if item.title == "Write the notes")
    rounds_before = len(db.list_channel_response_rounds(channel.id))
    posted = persist_origin_status_line(
        task=task,
        agent=jim,
        content=f"{jim.name} Done — notes",
        kind="completion",
    )
    assert posted.get("channel_message")
    assert not work_holds_talk(channel.id)
    assert len(db.list_channel_response_rounds(channel.id)) == rounds_before
    assert _work_still_queued(jim.id)

    # Prose without a task does not close peer Talk.
    message = _message(channel.id, "What do you think?")
    triggers = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    state = db.get_agent_state(jim.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "answer",
            "workCommit": False,
            "intentKind": "status_request",
            "reply": "The drain should stay ordered for this design.",
            "proceedUntagged": True,
        },
        jim,
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
    order = _queue_ids(triggers[0]["payload"]["round_id"])
    assert order[0] == jim.id
    wakes = [
        (item.get("trigger_type"), item.get("agent_id"))
        for item in result["trigger_requests"]
    ]
    assert wakes == [("channel_message", order[1])]


def test_work_bind_stops_fanout_peer_queues() -> None:
    jim, laura, ada, channel = _trio()
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
    round_id = triggers[0]["payload"]["round_id"]
    for item in triggers:
        if item["agent_id"] == jim.id:
            continue
        db.create_agent_trigger(
            agent_id=item["agent_id"],
            trigger_type="channel_message",
            source_channel="channel",
            payload=dict(item["payload"]),
        )
    _queue_work(jim.id, channel.id)
    state = db.get_agent_state(jim.id)
    assert state is not None
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": "Ship the notes",
            "reply": "I'll ship the notes.",
        },
        jim,
        state,
        {
            "type": "channel_message",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "round_id": round_id,
            "source_message_id": message.id,
            "content": message.content,
            "from_name": "Human Operator",
            "author_type": "human",
            "dispatch_mode": DISPATCH_FANOUT,
        },
    )
    kinds = [item.get("trigger_type") for item in result["trigger_requests"]]
    assert "activity_resumed" in kinds
    assert "channel_message" not in kinds
    assert work_holds_talk(channel.id)
    assert db.list_channel_response_rounds(channel.id, status="active") == []
    queued = db.list_queued_triggers(limit=20)
    assert not any(
        item.trigger_type == "channel_message" and item.agent_id in {laura.id, ada.id}
        for item in queued
    )
    assert _work_still_queued(jim.id)


def test_factory_round_cap_bump_leaves_a_custom_value() -> None:
    assert config.get("channel_response_round_cap") == "64"
    db.set_setting("channel_response_round_cap", "4", "llm")
    reconcile_factory_round_cap()
    config.reload()
    assert config.get("channel_response_round_cap") == "64"
    db.set_setting("channel_response_round_cap", "8", "llm")
    reconcile_factory_round_cap()
    config.reload()
    assert config.get("channel_response_round_cap") == "8"
