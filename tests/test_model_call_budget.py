"""One knob bounds inflight model calls. No lane means Queued, not thinking."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.dispatcher import TurnDispatcher
from core.agent_loop.outcomes import TurnOutcome
from core.llm.call_budget import (
    SYSTEM_LANE_RESERVE,
    bind_turn_lane,
    budget,
    format_queued_ahead,
    local_capacity_warning,
    max_concurrent_model_calls,
    reset_turn_lane,
    slots_from_payload,
)
from core.llm.client import completion
from core.llm.system_completion import complete_text
from db.crud import execute
from db.settings import prune_obsolete_settings, seed_defaults

ROOT = Path(__file__).resolve().parents[1]


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    budget.reset()


def teardown_function() -> None:
    budget.reset()
    db.close_connection()


def _set_knob(value: str) -> None:
    db.set_setting("max_concurrent_agent_turns", value, "llm")
    config.reload()


def _agent(name: str):
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _enqueue(agent_id: str, *, channel_id: str | None = None, when: str):
    payload = {"content": "go", "from_name": "Human"}
    trigger_type = "human_chat"
    source = "chat"
    if channel_id:
        payload["channel_id"] = channel_id
        trigger_type = "channel_message"
        source = "channel"
    row = db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type=trigger_type,
        source_channel=source,
        payload=payload,
    )
    execute(
        "UPDATE agent_triggers SET created_at = $1 WHERE id = $2",
        [when, row.id],
    )
    return db.get_agent_trigger(row.id)


class _Message:
    content = "ok"


class _Choice:
    message = _Message()


class _Response:
    choices = [_Choice()]
    usage = None
    model = "mock"


def test_knob_is_the_only_concurrency_setting_and_is_not_wiped() -> None:
    _set_knob("4")
    execute(
        "INSERT INTO settings (key, value, category, updated_at) "
        "VALUES ($1, $2, $3, current_timestamp)",
        ["max_concurrent_llm_calls", "5", "llm"],
    )
    seed_defaults()
    prune_obsolete_settings()
    config.reload()
    keys = {row.key for row in db.get_settings()}
    assert "max_concurrent_llm_calls" not in keys
    assert config.get("max_concurrent_agent_turns") == "4"
    assert max_concurrent_model_calls() == 4
    assert "max_concurrent_llm_calls" not in (ROOT / "core" / "llm" / "client.py").read_text(encoding="utf-8")
    assert "max_concurrent_llm_calls" not in (ROOT / "ui" / "static" / "js" / "settings" / "settings-system.js").read_text(encoding="utf-8")


def test_local_server_capacity_is_a_warning_when_lower_than_the_knob() -> None:
    assert slots_from_payload([{"id": 0}]) == 1
    assert slots_from_payload({"slots": [{}, {}]}) == 2
    assert slots_from_payload({"data": []}) is None
    warning = local_capacity_warning(1, limit=2)
    assert warning is not None
    assert "below Max concurrent model calls (2)" in warning
    assert local_capacity_warning(2, limit=2) is None
    assert local_capacity_warning(None, limit=2) is None
    keys = {row.key for row in db.get_settings()}
    assert not any("capacity" in key or "parallel" in key for key in keys)


def test_at_most_n_lanes_and_system_reserve_stays_inside_the_knob() -> None:
    _set_knob("2")
    assert SYSTEM_LANE_RESERVE == 1
    first = budget.try_acquire(kind="turn", owner="ada")
    second = budget.try_acquire(kind="turn", owner="bea")
    assert first is not None and second is not None
    assert budget.try_acquire(kind="turn", owner="cy") is None
    assert budget.try_acquire(kind="system", owner="system-ai") is None
    assert budget.try_acquire(kind="call", owner="extra") is None
    assert budget.inflight() == 2
    budget.release(second)
    system = budget.try_acquire(kind="system", owner="system-ai")
    assert system is not None
    assert budget.inflight() == 2
    assert budget.try_acquire(kind="system", owner="system-ai-2") is None
    assert budget.try_acquire(kind="turn", owner="cy") is None
    budget.release(system)
    opened = budget.try_acquire(kind="turn", owner="cy")
    assert opened is not None
    assert budget.inflight() == 2


def test_system_route_counts_against_n(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_knob("2")
    db.create_connection(
        name="Local",
        api_base_url="http://127.0.0.1:9/v1",
        model="mock-small",
    )
    calls: list[int] = []

    def _fake(**_kwargs: object) -> _Response:
        calls.append(budget.inflight())
        return _Response()

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake)
    held = budget.try_acquire(kind="turn", owner="ada")
    assert held is not None
    text = complete_text([{"role": "user", "content": "route"}])
    assert text == "ok"
    assert calls == [2]
    other = budget.try_acquire(kind="turn", owner="bea")
    assert other is not None
    assert complete_text([{"role": "user", "content": "again"}]) is None
    assert calls == [2]


@pytest.mark.asyncio
async def test_repair_reuses_the_turn_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_knob("2")
    calls = 0

    async def _fake(**_kwargs: object) -> _Response:
        nonlocal calls
        calls += 1
        assert budget.inflight() == 2
        return _Response()

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake)
    turn = budget.try_acquire(kind="turn", owner="ada")
    other = budget.try_acquire(kind="turn", owner="bea")
    assert turn is not None and other is not None
    token = bind_turn_lane(turn)
    try:
        first = await completion(model="mock", messages=[{"role": "user", "content": "hi"}])
        second = await completion(model="mock", messages=[{"role": "user", "content": "repair"}])
    finally:
        reset_turn_lane(token)
    assert first.content == "ok"
    assert second.content == "ok"
    assert calls == 2
    assert budget.inflight() == 2
    assert budget.try_acquire(kind="call", owner="fifth") is None


@pytest.mark.asyncio
async def test_completion_never_opens_a_fifth_call_when_n_is_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_knob("2")
    inflight = 0
    peak = 0
    gate = asyncio.Event()
    hold = asyncio.Event()

    async def _fake(**_kwargs: object) -> _Response:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        if inflight >= 2:
            gate.set()
        await hold.wait()
        inflight -= 1
        return _Response()

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake)
    tasks = [
        asyncio.create_task(completion(model="mock", messages=[{"role": "user", "content": "hi"}]))
        for _ in range(5)
    ]
    await asyncio.wait_for(gate.wait(), timeout=2)
    assert peak == 2
    assert inflight == 2
    assert budget.inflight() == 2
    hold.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
    assert len(results) == 5
    assert peak == 2
    assert budget.inflight() == 0


@pytest.mark.asyncio
async def test_third_agent_turn_stays_queued_until_a_lane_frees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_knob("2")
    ada = _agent("Ada")
    bea = _agent("Bea")
    cy = _agent("Cy")
    # Different rooms. The knob still allows two drafters; one room does not.
    room_a = db.create_channel(name="Room A", member_agent_ids=[ada.id])
    room_b = db.create_channel(name="Room B", member_agent_ids=[bea.id])
    room_c = db.create_channel(name="Room C", member_agent_ids=[cy.id])
    ada_row = _enqueue(ada.id, channel_id=room_a.id, when="2026-01-01 00:00:01")
    bea_row = _enqueue(bea.id, channel_id=room_b.id, when="2026-01-01 00:00:02")
    cy_row = _enqueue(cy.id, channel_id=room_c.id, when="2026-01-01 00:00:03")
    assert ada_row is not None and bea_row is not None and cy_row is not None

    phases: list[tuple[str, str, int | None]] = []

    async def _capture(kind: str, **kwargs: object) -> None:
        ahead = kwargs.get("ahead")
        phases.append((kind, str(kwargs.get("phase")), ahead if isinstance(ahead, int) else None))

    async def _desk(**kwargs: object) -> None:
        await _capture("desk", **kwargs)

    async def _channel(**kwargs: object) -> None:
        await _capture("channel", **kwargs)

    monkeypatch.setattr("core.agent_loop.dispatcher.manager.broadcast_agent_presence", _desk)
    monkeypatch.setattr("core.agent_loop.dispatcher.manager.broadcast_channel_presence", _channel)

    async def _done(*_args: object, **_kwargs: object) -> TurnOutcome:
        return TurnOutcome(result={}, trigger_status="completed")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _done)

    dispatcher = TurnDispatcher()
    first = dispatcher._claim_available_trigger()
    second = dispatcher._claim_available_trigger()
    third = dispatcher._claim_available_trigger()
    assert first is not None and first.agent_id == ada.id
    assert second is not None and second.agent_id == bea.id
    assert third is None
    waiting = db.get_agent_trigger(cy_row.id)
    assert waiting is not None
    assert waiting.status == "queued"
    assert len(dispatcher._queue_notices) == 1
    notice = dispatcher._queue_notices[0]
    assert notice["phase"] == "queued"
    assert notice["ahead"] == 2
    assert notice["agent_id"] == cy.id
    assert format_queued_ahead(notice["ahead"]) == "Queued (2 ahead)"
    await dispatcher._emit_queue_notices()
    assert phases
    assert {item[1] for item in phases} == {"queued"}
    assert ("desk", "queued", 2) in phases
    assert ("channel", "queued", 2) in phases
    assert "thinking" not in {item[1] for item in phases}

    dispatcher._release_turn_lane(ada.id)
    opened = dispatcher._claim_available_trigger()
    assert opened is not None
    assert opened.agent_id == cy.id
    assert db.get_agent_trigger(cy_row.id).status == "claimed"
    state = db.get_agent_state(cy.id)
    assert state is not None
    payload = {
        "type": opened.trigger_type,
        "trigger_id": opened.id,
        "task_id": opened.task_id,
        "source_channel": opened.source_channel,
        "claim_generation": opened.claim_generation,
        "channel_id": room_c.id,
        "content": "go",
    }
    await dispatcher._run_trigger(cy, state, payload)
    assert ("channel", "thinking", None) in phases
    assert ("desk", "thinking", None) in phases
    assert phases.index(("channel", "queued", 2)) < phases.index(("channel", "thinking", None))


def test_same_snapshot_peers_do_not_draft_in_parallel() -> None:
    """A free lane starts another room, not a second drafter on this snapshot."""
    _set_knob("2")
    ada = _agent("Ada")
    bea = _agent("Bea")
    dee = _agent("Dee")
    room = db.create_channel(name="Room", member_agent_ids=[ada.id, bea.id])
    other = db.create_channel(name="Other", member_agent_ids=[dee.id])
    ada_row = _enqueue(ada.id, channel_id=room.id, when="2026-01-01 00:00:01")
    bea_row = _enqueue(bea.id, channel_id=room.id, when="2026-01-01 00:00:02")
    _enqueue(dee.id, channel_id=other.id, when="2026-01-01 00:00:03")
    assert ada_row is not None and bea_row is not None

    dispatcher = TurnDispatcher()
    first = dispatcher._claim_available_trigger()
    second = dispatcher._claim_available_trigger()
    assert first is not None and first.agent_id == ada.id
    assert second is not None and second.agent_id == dee.id
    assert db.get_agent_trigger(bea_row.id).status == "queued"
    assert budget.inflight() == 2
    assert dispatcher._claim_available_trigger() is None

    db.complete_agent_trigger(first.id, claim_generation=first.claim_generation)
    dispatcher._release_turn_lane(ada.id)
    opened = dispatcher._claim_available_trigger()
    assert opened is not None and opened.agent_id == bea.id
    assert budget.inflight() == 2


def test_send_does_not_paint_thinking_before_a_lane() -> None:
    thread = (ROOT / "ui" / "static" / "js" / "conversation" / "sources" / "thread-source.js").read_text(encoding="utf-8")
    desk = (ROOT / "ui" / "static" / "js" / "context" / "desk-panel.js").read_text(encoding="utf-8")
    transcript = (ROOT / "ui" / "static" / "js" / "conversation" / "transcript.js").read_text(encoding="utf-8")
    send = thread.split("async function send", 1)[1].split("function subscribe", 1)[0]
    assert "presence.start(" not in send
    assert "phase === 'queued'" in thread
    assert "Queued (${ahead} ahead)" in transcript
    assert "Queued (${ahead} ahead)" in desk
