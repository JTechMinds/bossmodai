"""One knob bounds inflight model calls. No lane means Queued, not thinking."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import datetime
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
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.tasking.transitions import transition_task
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
    for name in ("settings-system.js", "settings-system-meta.js"):
        assert "max_concurrent_llm_calls" not in (ROOT / "ui" / "static" / "js" / "settings" / name).read_text(encoding="utf-8")


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
        first = await completion(model="mock", messages=[{"role": "user", "content": "hi"}], connection_id="test-connection")
        second = await completion(model="mock", messages=[{"role": "user", "content": "repair"}], connection_id="test-connection")
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
        asyncio.create_task(completion(model="mock", messages=[{"role": "user", "content": "hi"}], connection_id="test-connection"))
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

    # (channel_id, phase, ahead) for every agent_presence, and each thinking's since.
    phases: list[tuple[object, str, int | None]] = []
    thinking_since: list[object] = []

    async def _presence(**kwargs: object) -> None:
        ahead = kwargs.get("ahead")
        phases.append((kwargs.get("channel_id"), str(kwargs.get("phase")), ahead if isinstance(ahead, int) else None))
        if kwargs.get("phase") == "thinking":
            thinking_since.append(kwargs.get("since"))

    monkeypatch.setattr("core.agent_loop.dispatcher.manager.broadcast_agent_presence", _presence)

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
    assert (room_c.id, "queued", 2) in phases
    assert "thinking" not in {item[1] for item in phases}

    dispatcher._release_turn_reservations(ada.id)
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
    assert (room_c.id, "thinking", None) in phases
    assert phases.index((room_c.id, "queued", 2)) < phases.index((room_c.id, "thinking", None))
    # The turn's real start, which the transcript counts elapsed time from.
    assert len(thinking_since) == 1
    assert isinstance(thinking_since[0], str)
    assert datetime.fromisoformat(thinking_since[0]).tzinfo is not None


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
    waiting = db.get_agent_trigger(bea_row.id)
    assert waiting is not None
    assert waiting.status == "queued"
    assert bea.id not in dispatcher._turn_lanes
    assert len(dispatcher._queue_notices) == 1
    notice = dispatcher._queue_notices[0]
    assert notice["phase"] == "queued"
    assert notice["agent_id"] == bea.id
    assert notice["channel_id"] == room.id
    assert notice["ahead"] == 1
    assert format_queued_ahead(notice["ahead"]) == "Queued (1 ahead)"
    assert budget.inflight() == 2
    assert dispatcher._claim_available_trigger() is None
    assert db.get_agent_trigger(bea_row.id).status == "queued"

    db.complete_agent_trigger(first.id, claim_generation=first.claim_generation)
    dispatcher._release_turn_reservations(ada.id)
    opened = dispatcher._claim_available_trigger()
    assert opened is not None and opened.agent_id == bea.id
    assert budget.inflight() == 2


@pytest.mark.asyncio
async def test_same_channel_waiter_stays_queued_while_lanes_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knob 4 still allows one live speak per channel. The next wake is Queued."""
    _set_knob("4")
    ada = _agent("Ada")
    bea = _agent("Bea")
    cy = _agent("Cy")
    dee = _agent("Dee")
    room = db.create_channel(name="Room", member_agent_ids=[ada.id, bea.id, cy.id])
    other = db.create_channel(name="Other", member_agent_ids=[dee.id])
    ada_row = _enqueue(ada.id, channel_id=room.id, when="2026-01-01 00:00:01")
    bea_row = _enqueue(bea.id, channel_id=room.id, when="2026-01-01 00:00:02")
    cy_row = _enqueue(cy.id, channel_id=room.id, when="2026-01-01 00:00:03")
    _enqueue(dee.id, channel_id=other.id, when="2026-01-01 00:00:04")
    assert ada_row is not None and bea_row is not None and cy_row is not None

    phases: list[tuple[object, str, str, int | None]] = []

    async def _presence(**kwargs: object) -> None:
        ahead = kwargs.get("ahead")
        phases.append((
            kwargs.get("channel_id"),
            str(kwargs.get("agent_id")),
            str(kwargs.get("phase")),
            ahead if isinstance(ahead, int) else None,
        ))

    monkeypatch.setattr("core.agent_loop.dispatcher.manager.broadcast_agent_presence", _presence)

    dispatcher = TurnDispatcher()
    first = dispatcher._claim_available_trigger()
    second = dispatcher._claim_available_trigger()
    assert first is not None and first.agent_id == ada.id
    assert second is not None and second.agent_id == dee.id
    assert max_concurrent_model_calls() == 4
    assert budget.inflight() == 2
    by_agent = {notice["agent_id"]: notice for notice in dispatcher._queue_notices}
    assert set(by_agent) == {bea.id, cy.id}
    assert by_agent[bea.id]["phase"] == "queued"
    assert by_agent[bea.id]["ahead"] == 1
    assert by_agent[cy.id]["ahead"] == 2
    assert by_agent[bea.id]["channel_id"] == room.id
    assert db.get_agent_trigger(bea_row.id).status == "queued"
    assert db.get_agent_trigger(cy_row.id).status == "queued"
    assert bea.id not in dispatcher._turn_lanes
    assert cy.id not in dispatcher._turn_lanes
    await dispatcher._emit_queue_notices()
    assert (room.id, bea.id, "queued", 1) in phases
    assert (room.id, cy.id, "queued", 2) in phases
    assert "thinking" not in {item[2] for item in phases}

    blocked = dispatcher._claim_available_trigger()
    assert blocked is None
    assert budget.inflight() == 2
    assert db.get_agent_trigger(bea_row.id).status == "queued"

    db.complete_agent_trigger(first.id, claim_generation=first.claim_generation)
    dispatcher._release_turn_reservations(ada.id)
    opened = dispatcher._claim_available_trigger()
    assert opened is not None and opened.agent_id == bea.id
    assert db.get_agent_trigger(cy_row.id).status == "queued"
    waiting = dispatcher._claim_available_trigger()
    assert waiting is None
    cy_notice = dispatcher._queue_notices[0]
    assert cy_notice["agent_id"] == cy.id
    assert cy_notice["ahead"] == 1
    assert cy_notice["phase"] == "queued"
    await dispatcher._emit_queue_notices()
    assert (room.id, cy.id, "queued", 1) in phases
    assert "thinking" not in {item[2] for item in phases}


def test_send_does_not_paint_thinking_before_a_lane() -> None:
    thread = (ROOT / "ui" / "static" / "js" / "conversation" / "sources" / "thread-source.js").read_text(encoding="utf-8")
    desk = (ROOT / "ui" / "static" / "js" / "context" / "desk-panel.js").read_text(encoding="utf-8")
    transcript = (ROOT / "ui" / "static" / "js" / "conversation" / "transcript.js").read_text(encoding="utf-8")
    presence = (ROOT / "ui" / "static" / "js" / "core" / "presence.js").read_text(encoding="utf-8")
    send = thread.split("async function send", 1)[1].split("function context", 1)[0]
    # Sending paints nothing: a row only ever comes from the server's events.
    assert "presence" not in send
    # The source only selects; core/presence.js is the one writer of the slice.
    assert "store.setState" not in thread
    assert "phase === 'queued'" in presence
    assert "Queued (${ahead} ahead)" in transcript
    assert "Queued (${row.ahead} ahead)" in desk


def _presence_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, str, str]]:
    """Record (channel_id, agent_id, phase) for every agent_presence the dispatcher sends."""
    phases: list[tuple[object, str, str]] = []

    async def _presence(**kwargs: object) -> None:
        phases.append((kwargs.get("channel_id"), str(kwargs.get("agent_id")), str(kwargs.get("phase"))))

    monkeypatch.setattr("core.agent_loop.dispatcher.manager.broadcast_agent_presence", _presence)
    return phases


def _busy_syncs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which agents the dispatcher re-synced the Busy line for."""
    synced: list[str] = []

    async def _sync(agent_id: str) -> dict[str, object]:
        synced.append(agent_id)
        return {"action": "noop", "agent_id": agent_id}

    monkeypatch.setattr("core.agent_loop.dispatcher.emit_queue_visibility", _sync)
    return synced


def _painted_waiter(monkeypatch: pytest.MonkeyPatch):
    """Ada holds the room's live speak; Bea is painted Queued behind her."""
    _set_knob("4")
    ada = _agent("Ada")
    bea = _agent("Bea")
    room = db.create_channel(name="Room", member_agent_ids=[ada.id, bea.id])
    _enqueue(ada.id, channel_id=room.id, when="2026-01-01 00:00:01")
    bea_row = _enqueue(bea.id, channel_id=room.id, when="2026-01-01 00:00:02")
    assert bea_row is not None
    dispatcher = TurnDispatcher()
    first = dispatcher._claim_available_trigger()
    assert first is not None and first.agent_id == ada.id
    assert dispatcher._claim_available_trigger() is None
    return dispatcher, ada, bea, room, first, bea_row


@pytest.mark.asyncio
async def test_deleted_waiter_gets_exactly_one_idle_and_its_busy_line_resynced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases = _presence_log(monkeypatch)
    synced = _busy_syncs(monkeypatch)
    dispatcher, _ada, bea, room, _first, bea_row = _painted_waiter(monkeypatch)
    await dispatcher._emit_queue_notices()
    assert (room.id, bea.id, "queued") in phases

    # The round closed and deleted Bea's wake before it ran.
    execute("DELETE FROM agent_triggers WHERE id = $1", [bea_row.id])
    assert dispatcher._claim_available_trigger() is None
    await dispatcher._emit_queue_notices()
    assert dispatcher._claim_available_trigger() is None
    await dispatcher._emit_queue_notices()

    bea_idle = [item for item in phases if item[1] == bea.id and item[2] == "idle"]
    assert bea_idle == [(room.id, bea.id, "idle")]
    assert synced == [bea.id]


@pytest.mark.asyncio
async def test_waiter_claimed_next_gets_thinking_and_no_retraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases = _presence_log(monkeypatch)
    synced = _busy_syncs(monkeypatch)

    async def _done(*_args: object, **_kwargs: object) -> TurnOutcome:
        return TurnOutcome(result={}, trigger_status="completed")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _done)
    dispatcher, ada, bea, room, first, _bea_row = _painted_waiter(monkeypatch)
    await dispatcher._emit_queue_notices()

    db.complete_agent_trigger(first.id, claim_generation=first.claim_generation)
    dispatcher._release_turn_reservations(ada.id)
    opened = dispatcher._claim_available_trigger()
    assert opened is not None and opened.agent_id == bea.id
    await dispatcher._emit_queue_notices()
    assert [item for item in phases if item[1] == bea.id and item[2] == "idle"] == []
    assert synced == []

    state = db.get_agent_state(bea.id)
    assert state is not None
    await dispatcher._run_trigger(
        bea,
        state,
        {
            "type": opened.trigger_type,
            "trigger_id": opened.id,
            "task_id": opened.task_id,
            "source_channel": opened.source_channel,
            "claim_generation": opened.claim_generation,
            "channel_id": room.id,
            "content": "go",
        },
    )
    bea_channel = [item[2] for item in phases if item[0] == room.id and item[1] == bea.id]
    # The only idle is the turn's own end, after thinking.
    assert bea_channel == ["queued", "thinking", "idle"]
    assert not any(key[0] == bea.id for key in dispatcher._painted_queued)


@pytest.mark.asyncio
async def test_pause_does_not_retract_queued_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    phases = _presence_log(monkeypatch)
    synced = _busy_syncs(monkeypatch)
    dispatcher, _ada, bea, room, _first, bea_row = _painted_waiter(monkeypatch)
    await dispatcher._emit_queue_notices()

    db.set_setting("runtime_control_state", "paused", "advanced")
    config.reload()
    execute("DELETE FROM agent_triggers WHERE id = $1", [bea_row.id])
    assert dispatcher._claim_available_trigger() is None
    await dispatcher._emit_queue_notices()
    assert [item for item in phases if item[2] == "idle"] == []
    assert synced == []
    assert (bea.id, room.id) in dispatcher._painted_queued


@pytest.mark.asyncio
async def test_row_in_another_thread_is_retracted_after_the_agents_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases = _presence_log(monkeypatch)
    synced = _busy_syncs(monkeypatch)

    async def _done(*_args: object, **_kwargs: object) -> TurnOutcome:
        return TurnOutcome(result={}, trigger_status="completed")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _done)
    _set_knob("4")
    ada = _agent("Ada")
    bea = _agent("Bea")
    cy = _agent("Cy")
    room_x = db.create_channel(name="Room X", member_agent_ids=[ada.id, bea.id])
    room_y = db.create_channel(name="Room Y", member_agent_ids=[cy.id, bea.id])
    _enqueue(ada.id, channel_id=room_x.id, when="2026-01-01 00:00:01")
    cy_row = _enqueue(cy.id, channel_id=room_y.id, when="2026-01-01 00:00:02")
    bea_x = _enqueue(bea.id, channel_id=room_x.id, when="2026-01-01 00:00:03")
    _enqueue(bea.id, channel_id=room_y.id, when="2026-01-01 00:00:04")
    assert cy_row is not None and bea_x is not None
    dispatcher = TurnDispatcher()
    assert dispatcher._claim_available_trigger().agent_id == ada.id
    cy_claim = dispatcher._claim_available_trigger()
    assert cy_claim is not None and cy_claim.agent_id == cy.id
    assert dispatcher._claim_available_trigger() is None
    await dispatcher._emit_queue_notices()
    assert set(dispatcher._painted_queued) == {(bea.id, room_x.id), (bea.id, room_y.id)}

    # Cy posts in Y; Bea's Y wake is claimed while X is still held behind Ada.
    db.complete_agent_trigger(cy_claim.id, claim_generation=cy_claim.claim_generation)
    dispatcher._release_turn_reservations(cy.id)
    opened = dispatcher._claim_available_trigger()
    assert opened is not None and opened.agent_id == bea.id
    # X's round closes during Bea's turn, deleting her X wake.
    execute("DELETE FROM agent_triggers WHERE id = $1", [bea_x.id])
    assert dispatcher._claim_available_trigger() is None
    await dispatcher._emit_queue_notices()
    assert [item for item in phases if item[1] == bea.id and item[2] == "idle"] == []
    assert (bea.id, room_x.id) in dispatcher._painted_queued

    state = db.get_agent_state(bea.id)
    assert state is not None
    await dispatcher._run_trigger(
        bea,
        state,
        {
            "type": opened.trigger_type,
            "trigger_id": opened.id,
            "task_id": opened.task_id,
            "source_channel": opened.source_channel,
            "claim_generation": opened.claim_generation,
            "channel_id": room_y.id,
            "content": "go",
        },
    )
    idle_before = [item[0] for item in phases if item[1] == bea.id and item[2] == "idle"]
    synced_before = synced.count(bea.id)
    assert dispatcher._claim_available_trigger() is None
    await dispatcher._emit_queue_notices()
    idle_after = [item[0] for item in phases if item[1] == bea.id and item[2] == "idle"]
    # One idle closed the Y turn; the next emit retracts the stale X row.
    assert (idle_before, idle_after) == ([room_y.id], [room_y.id, room_x.id])
    assert dispatcher._painted_queued == {}
    # The retraction also re-syncs Bea's Busy line once.
    assert synced.count(bea.id) == synced_before + 1


class _WorldStateSink(NullRuntimeEventSink):
    """Drops every runtime event, and counts the world snapshots asked for."""

    def __init__(self) -> None:
        self.world_states = 0

    async def broadcast_world_state(self) -> None:
        self.world_states += 1


@pytest.fixture
def world_sink() -> Iterator[_WorldStateSink]:
    """Counting sink on the process-wide ``runtime_events``; the old sink is restored even on failure."""
    previous = runtime_events._sink
    sink = _WorldStateSink()
    runtime_events.set_sink(sink)
    try:
        yield sink
    finally:
        runtime_events.set_sink(previous)


@pytest.mark.asyncio
async def test_a_claim_skipped_for_a_complete_task_still_sends_the_world(world_sink: _WorldStateSink) -> None:
    """A claim check that refreshes status and starts no turn sends the snapshot.

    The complete-task ``activity_resumed`` skip refreshes the agent's status
    and returns False, so no turn runs and no turn broadcast follows.
    refresh_agent_status asks for the snapshot itself, so the rail still hears.
    """
    ada = _agent("Ada")
    task = db.create_task("Ship notes", assigned_to=ada.id)
    transition_task(task.id, "active", reason="started", actor="pytest")
    transition_task(task.id, "complete", reason="finished", actor="pytest")
    row = db.create_agent_trigger(
        agent_id=ada.id,
        trigger_type="activity_resumed",
        source_channel="work",
        payload={"content": "Resume."},
        task_id=task.id,
    )
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    for _ in range(3):
        await asyncio.sleep(0)
    assert world_sink.world_states == 0

    started = await TurnDispatcher()._launch_claimed_trigger(claimed)

    assert started is False
    assert db.get_agent_trigger(row.id).status == "completed"
    for _ in range(3):
        await asyncio.sleep(0)
    assert world_sink.world_states == 1
