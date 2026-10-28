"""HA-SEC-P1-02 — trigger leases / heartbeat / crash requeue."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.dispatcher import (
    NOT_RETRIED_NO_REPEAT_REASON,
    ORPHAN_CLAIM_AFTER,
    StrandedClaimError,
    TurnDispatcher,
)
from core.agent_loop.outcomes import TurnOutcome
from core.llm.call_budget import budget


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


def _create_agent(name: str = "Ada"):
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1)


def _queued_trigger(agent_id: str):
    return db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "hello", "from_name": "Human"},
    )


def test_completed_trigger_is_not_replayed_on_forced_requeue() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    completed = db.complete_agent_trigger(row.id, claim_generation=claimed.claim_generation)
    assert completed is not None
    assert completed.status == "completed"

    recovered = db.requeue_all_claimed_triggers()
    assert recovered == 0
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "completed"


def test_force_requeue_recovers_mid_claim_once() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    assert claimed.status == "claimed"
    assert claimed.claim_generation == 1
    assert claimed.claim_lease

    first = db.requeue_all_claimed_triggers()
    assert first == 1
    queued = db.get_agent_trigger(row.id)
    assert queued is not None
    assert queued.status == "queued"
    assert queued.claim_lease is None

    second = db.requeue_all_claimed_triggers()
    assert second == 0
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "queued"


def _age_claim(trigger_id: str, seconds: float) -> None:
    stale = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    db.execute("UPDATE agent_triggers SET claimed_at = $1 WHERE id = $2", [stale, trigger_id])


def test_list_orphaned_claims_returns_only_claims_older_than_the_cutoff() -> None:
    agent = _create_agent()
    old = db.claim_trigger(_queued_trigger(agent.id).id)
    older = db.claim_trigger(_queued_trigger(agent.id).id)
    fresh = db.claim_trigger(_queued_trigger(agent.id).id)
    unstamped = db.claim_trigger(_queued_trigger(agent.id).id)
    queued = _queued_trigger(agent.id)
    done = db.claim_trigger(_queued_trigger(agent.id).id)
    assert old and older and fresh and unstamped and done
    _age_claim(old.id, 120)
    _age_claim(older.id, 900)
    db.execute("UPDATE agent_triggers SET claimed_at = NULL WHERE id = $1", [unstamped.id])
    _age_claim(done.id, 900)
    db.complete_agent_trigger(done.id, claim_generation=done.claim_generation)

    rows = db.list_orphaned_claims(claimed_before=datetime.now(timezone.utc) - timedelta(seconds=30))

    # Oldest claim first; NULL claimed_at sorts first and still counts.
    assert [row.id for row in rows] == [unstamped.id, older.id, old.id]
    assert queued.id not in {row.id for row in rows}
    assert fresh.id not in {row.id for row in rows}


def test_list_orphaned_claims_includes_retry_blocked_rows() -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    _age_claim(blocked.id, 900)

    rows = db.list_orphaned_claims(claimed_before=datetime.now(timezone.utc) - timedelta(seconds=30))

    assert [(row.id, row.retry_blocked) for row in rows] == [(blocked.id, True)]
    # Listing changes nothing: the caller decides per row.
    assert db.get_agent_trigger(blocked.id).status == "claimed"


def test_take_claimed_trigger_deletes_the_matching_claim_and_returns_it() -> None:
    agent = _create_agent()
    claimed = db.claim_trigger(_queued_trigger(agent.id).id)
    assert claimed is not None

    taken = db.take_claimed_trigger(claimed.id, claimed.claim_generation)

    assert taken is not None
    assert (taken.id, taken.status, taken.claim_generation) == (claimed.id, "claimed", claimed.claim_generation)
    assert db.get_agent_trigger(claimed.id) is None


def test_take_claimed_trigger_refuses_a_generation_mismatch() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    first = db.claim_trigger(row.id)
    assert first is not None
    db.requeue_all_claimed_triggers()
    second = db.claim_trigger(row.id)
    assert second is not None and second.claim_generation != first.claim_generation

    assert db.take_claimed_trigger(row.id, first.claim_generation) is None
    survivor = db.get_agent_trigger(row.id)
    assert survivor is not None
    assert (survivor.status, survivor.claim_generation) == ("claimed", second.claim_generation)


def test_take_claimed_trigger_refuses_a_queued_row() -> None:
    agent = _create_agent()
    queued = _queued_trigger(agent.id)

    assert db.take_claimed_trigger(queued.id, queued.claim_generation) is None
    assert db.get_agent_trigger(queued.id).status == "queued"


def _reclaim_after_force(agent_id: str):
    row = _queued_trigger(agent_id)
    first = db.claim_trigger(row.id)
    assert first is not None
    db.requeue_all_claimed_triggers()
    second = db.claim_trigger(row.id)
    assert second is not None
    assert second.claim_generation == first.claim_generation + 1
    return row, first, second


def test_stale_generation_cannot_complete_a_reclaimed_trigger() -> None:
    agent = _create_agent()
    row, first, second = _reclaim_after_force(agent.id)

    assert db.complete_agent_trigger(row.id, claim_generation=first.claim_generation) is None
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "claimed"
    assert still.claim_generation == second.claim_generation

    done = db.complete_agent_trigger(row.id, claim_generation=second.claim_generation)
    assert done is not None
    assert done.status == "completed"


def test_stale_generation_cannot_fail_a_reclaimed_trigger() -> None:
    agent = _create_agent()
    row, first, second = _reclaim_after_force(agent.id)

    assert db.fail_agent_trigger(
        row.id, "stale fail", claim_generation=first.claim_generation
    ) is None
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "claimed"
    assert still.claim_generation == second.claim_generation
    assert still.failure_reason is None

    failed = db.fail_agent_trigger(
        row.id, "live fail", claim_generation=second.claim_generation
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_reason == "live fail"


def test_stale_generation_cannot_retry_a_reclaimed_trigger() -> None:
    agent = _create_agent()
    row, first, second = _reclaim_after_force(agent.id)

    assert db.retry_agent_trigger(
        row.id, "stale retry", claim_generation=first.claim_generation, count_retry=True
    ) is None
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "claimed"
    assert still.claim_generation == second.claim_generation
    assert still.retry_count == 0
    assert still.failure_reason is None

    retried = db.retry_agent_trigger(
        row.id, "live retry", claim_generation=second.claim_generation, count_retry=True
    )
    assert retried is not None
    assert retried.status == "queued"
    assert retried.retry_count == 1
    assert retried.failure_reason == "live retry"


def test_unguarded_fail_does_not_flip_completed() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    db.complete_agent_trigger(row.id, claim_generation=claimed.claim_generation)

    assert db.fail_agent_trigger(row.id, "too late") is None
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_stale_supervise_retry_does_not_wreck_reclaimed_claim() -> None:
    agent = _create_agent()
    row, first, second = _reclaim_after_force(agent.id)
    stale = {
        "type": first.trigger_type,
        "trigger_id": first.id,
        "task_id": first.task_id,
        "source_channel": first.source_channel,
        "claim_generation": first.claim_generation,
    }
    await TurnDispatcher()._supervise_failed_turn(
        agent=agent,
        trigger=stale,
        failure_detail="stale boom",
        not_retried_reason=None,
        cause=None,
        replay_safe=True,
    )
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "claimed"
    assert refreshed.claim_generation == second.claim_generation
    assert refreshed.retry_count == 0


@pytest.mark.asyncio
async def test_stale_supervise_exhaust_does_not_fail_reclaimed_claim() -> None:
    agent = _create_agent()
    row, first, second = _reclaim_after_force(agent.id)
    stale = {
        "type": first.trigger_type,
        "trigger_id": first.id,
        "task_id": first.task_id,
        "source_channel": first.source_channel,
        "claim_generation": first.claim_generation,
    }
    await TurnDispatcher()._supervise_failed_turn(
        agent=agent,
        trigger=stale,
        failure_detail="stale exhaust",
        not_retried_reason=NOT_RETRIED_NO_REPEAT_REASON,
        cause=None,
        replay_safe=False,
    )
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "claimed"
    assert refreshed.claim_generation == second.claim_generation


def test_heartbeat_advances_claimed_at() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    db.execute(
        "UPDATE agent_triggers SET claimed_at = $1 WHERE id = $2",
        [past, row.id],
    )
    refreshed = db.heartbeat_trigger_lease(row.id, claimed.claim_generation)
    assert refreshed is not None
    assert refreshed.claimed_at is not None
    claimed_at = refreshed.claimed_at
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)
    assert claimed_at > past
    assert db.heartbeat_trigger_lease(row.id, claimed.claim_generation + 1) is None


@pytest.mark.asyncio
async def test_long_turn_heartbeats_then_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None

    beats: list[int] = []
    original = db.heartbeat_trigger_lease

    def _spy(trigger_id: str, generation: int) -> Any:
        beats.append(generation)
        return original(trigger_id, generation)

    monkeypatch.setattr(db, "heartbeat_trigger_lease", _spy)
    monkeypatch.setattr("core.agent_loop.dispatcher.LEASE_HEARTBEAT_SECONDS", 0.05)

    async def _fake_turn(*_args: Any, **_kwargs: Any) -> TurnOutcome:
        await asyncio.sleep(0.2)
        return TurnOutcome(result={}, trigger_status="completed")

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _fake_turn)

    payload = json.loads(claimed.payload) if claimed.payload else {}
    payload.update(
        {
            "type": claimed.trigger_type,
            "trigger_id": claimed.id,
            "task_id": claimed.task_id,
            "source_channel": claimed.source_channel,
            "claim_generation": claimed.claim_generation,
        }
    )
    state = db.get_agent_state(agent.id)
    assert state is not None
    await TurnDispatcher()._run_trigger(agent, state, payload)

    assert beats
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "completed"


# ─── retry_blocked: a turn that ran a no-retry command is never replayed ───


def _claimed_blocked_trigger(agent_id: str):
    row = _queued_trigger(agent_id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    db.mark_trigger_retry_blocked(row.id)
    return claimed


def test_mark_trigger_retry_blocked_sets_the_flag_and_raises_on_a_missing_row() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    assert db.get_agent_trigger(row.id).retry_blocked is False

    db.mark_trigger_retry_blocked(row.id)

    assert db.get_agent_trigger(row.id).retry_blocked is True
    with pytest.raises(LookupError):
        db.mark_trigger_retry_blocked("no-such-trigger")


def test_force_requeue_leaves_a_blocked_orphan_claimed_and_lists_it() -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    plain = db.claim_trigger(_queued_trigger(agent.id).id)
    assert plain is not None

    assert db.requeue_all_claimed_triggers() == 1

    assert db.get_agent_trigger(blocked.id).status == "claimed"
    assert db.get_agent_trigger(plain.id).status == "queued"
    assert [item.id for item in db.list_claimed_retry_blocked_triggers()] == [blocked.id]


def test_list_claimed_retry_blocked_triggers_skips_queued_and_failed_rows() -> None:
    agent = _create_agent()
    queued = _queued_trigger(agent.id)
    db.mark_trigger_retry_blocked(queued.id)
    failed = _claimed_blocked_trigger(agent.id)
    db.fail_agent_trigger(failed.id, "done", claim_generation=failed.claim_generation)

    assert db.list_claimed_retry_blocked_triggers() == []


async def _start_until_first_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TurnDispatcher, dict[str, str]]:
    """Start a dispatcher, stop it at its first drain, and report row states then."""
    seen: dict[str, str] = {}
    drained = asyncio.Event()

    async def _first_drain(self: TurnDispatcher) -> None:
        for row in db.query("SELECT id, status FROM agent_triggers"):
            seen[str(row["id"])] = str(row["status"])
        drained.set()

    monkeypatch.setattr(TurnDispatcher, "_drain_queue", _first_drain)
    dispatcher = TurnDispatcher()
    dispatcher.start()
    await asyncio.wait_for(drained.wait(), timeout=5)
    await dispatcher.stop()
    return dispatcher, seen


@pytest.mark.asyncio
async def test_dispatcher_start_fails_a_blocked_orphan_and_requeues_an_unblocked_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    plain = db.claim_trigger(_queued_trigger(agent.id).id)
    assert plain is not None
    exhausted: list[dict[str, Any]] = []
    real_exhaust = TurnDispatcher._exhaust_failed_trigger

    async def _spy(self: TurnDispatcher, **kwargs: Any) -> None:
        exhausted.append(kwargs)
        await real_exhaust(self, **kwargs)

    monkeypatch.setattr(TurnDispatcher, "_exhaust_failed_trigger", _spy)

    _dispatcher, seen = await _start_until_first_drain(monkeypatch)

    # Failed before the first claim, never requeued.
    assert seen[blocked.id] == "failed"
    assert seen[plain.id] == "queued"
    row = db.get_agent_trigger(blocked.id)
    assert row.retry_count == 0
    assert row.failure_reason.startswith("The runtime stopped during a turn that had already run")
    [call] = exhausted
    assert call["stall_reason"] == NOT_RETRIED_NO_REPEAT_REASON
    assert call["trigger"]["trigger_id"] == blocked.id
    assert call["trigger"]["claim_generation"] == blocked.claim_generation
    assert call["trigger"]["type"] == "human_chat" and call["trigger"]["content"] == "hello"
    # The operator is told in chat (a human_chat trigger with no task).
    assert any(item.from_agent == agent.id for item in db.get_human_chat_thread(agent.id))


@pytest.mark.asyncio
async def test_dispatcher_start_fails_a_blocked_orphan_whose_agent_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    monkeypatch.setattr(db, "get_agent", lambda _agent_id: None)

    _dispatcher, seen = await _start_until_first_drain(monkeypatch)

    assert seen[blocked.id] == "failed"


# ─── Claims always end in a turn, a requeue or a failure ───


def _lane_rows() -> int:
    return int(db.query("SELECT COUNT(*) AS cnt FROM model_call_lanes")[0]["cnt"])


def _diagnostic_errors(agent_id: str) -> list[str]:
    return [str(row.get("error") or "") for row in db.get_diagnostics(agent_id=agent_id)]


@pytest.mark.asyncio
async def test_queue_visibility_failure_after_claim_requeues_and_frees_the_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget.reset()
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    dispatcher = TurnDispatcher()
    dispatcher._running = True

    def _boom(_agent_id: str) -> None:
        raise RuntimeError("busy line write failed")

    monkeypatch.setattr("core.agent_loop.dispatcher.schedule_queue_visibility", _boom)

    await dispatcher._drain_queue()

    refreshed = db.get_agent_trigger(row.id)
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert "busy line write failed" in refreshed.failure_reason
    assert _lane_rows() == 0
    assert dispatcher._turn_lanes == {}
    assert agent.id not in dispatcher._active_turns
    assert any("busy line write failed" in error for error in _diagnostic_errors(agent.id))


@pytest.mark.asyncio
async def test_queue_notice_failure_after_claim_requeues_and_frees_the_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget.reset()
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    dispatcher = TurnDispatcher()
    dispatcher._running = True

    async def _boom() -> None:
        raise RuntimeError("presence broadcast failed")

    monkeypatch.setattr(dispatcher, "_emit_queue_notices", _boom)

    await dispatcher._drain_queue()

    refreshed = db.get_agent_trigger(row.id)
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert _lane_rows() == 0
    assert dispatcher._turn_lanes == {}
    assert any("presence broadcast failed" in error for error in _diagnostic_errors(agent.id))


@pytest.mark.asyncio
async def test_a_failure_that_is_not_trigger_specific_abandons_one_claim_per_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget.reset()
    ada = _create_agent("Ada")
    bea = _create_agent("Bea")
    rows = [_queued_trigger(ada.id), _queued_trigger(bea.id)]
    dispatcher = TurnDispatcher()
    dispatcher._running = True

    async def _always_boom() -> None:
        raise RuntimeError("presence broadcast failed")

    monkeypatch.setattr(dispatcher, "_emit_queue_notices", _always_boom)

    await dispatcher._drain_queue()

    after = sorted(
        (db.get_agent_trigger(row.id) for row in rows), key=lambda item: item.retry_count,
    )
    assert [(item.status, item.retry_count) for item in after] == [("queued", 0), ("queued", 1)]
    assert _lane_rows() == 0
    assert dispatcher._turn_lanes == {}


@pytest.mark.asyncio
async def test_launch_failure_at_the_retry_limit_fails_and_frees_the_channel_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_loop.channel_rounds import start_channel_peer_round

    budget.reset()
    jim = db.create_agent("Jim", role="PM", desk_x=1, desk_y=1)
    laura = db.create_agent("Laura", role="Eng", desk_x=2, desk_y=1)
    channel = db.create_channel(name="Ops", member_agent_ids=[jim.id, laura.id], created_by=jim.id)
    message = db.create_channel_message(
        channel_id=channel.id,
        author_type="human",
        author_name="Human Operator",
        content="Where are we?",
        source_channel="channel",
    )
    [first, *_rest] = start_channel_peer_round(
        channel_id=channel.id,
        message_id=message.id,
        content=message.content,
        from_name="Human Operator",
        author_type="human",
        channel_name=channel.name,
    )
    row = db.create_agent_trigger(
        agent_id=first["agent_id"],
        trigger_type=first["trigger_type"],
        source_channel=first["source_channel"],
        payload=first["payload"],
    )
    speaker = first["agent_id"]
    peer = laura.id if speaker == jim.id else jim.id
    dispatcher = TurnDispatcher()
    db.execute(
        "UPDATE agent_triggers SET retry_count = $1 WHERE id = $2",
        [dispatcher._retry_limit(), row.id],
    )
    dispatcher._running = True

    def _boom(_agent_id: str) -> None:
        raise RuntimeError("busy line write failed")

    monkeypatch.setattr("core.agent_loop.dispatcher.schedule_queue_visibility", _boom)

    await dispatcher._drain_queue()

    assert db.get_agent_trigger(row.id).status == "failed"
    assert _lane_rows() == 0
    # The round moved on: the next member's wake is queued and claimable,
    # not held behind a dead live speak.
    assert not db.channel_peer_snapshot_is_drafting(channel.id)
    claimed = dispatcher._claim_available_trigger()
    assert claimed is not None
    assert claimed.agent_id == peer
    dispatcher._release_turn_reservations(peer)


def test_raising_claim_releases_the_lane_and_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    budget.reset()
    agent = _create_agent()
    row = _queued_trigger(agent.id)

    def _boom(_trigger_id: str) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "claim_trigger", _boom)
    dispatcher = TurnDispatcher()

    with pytest.raises(RuntimeError, match="database is locked"):
        dispatcher._claim_available_trigger()

    assert _lane_rows() == 0
    assert dispatcher._turn_lanes == {}
    assert db.get_agent_trigger(row.id).status == "queued"


@pytest.mark.asyncio
async def test_sweep_requeues_a_stranded_claim_loudly_and_frees_its_leaked_lane(
    caplog: pytest.LogCaptureFixture,
) -> None:
    budget.reset()
    agent = _create_agent()
    claimed = db.claim_trigger(_queued_trigger(agent.id).id)
    assert claimed is not None
    _age_claim(claimed.id, ORPHAN_CLAIM_AFTER.total_seconds() + 5)
    dispatcher = TurnDispatcher()
    leaked = budget.try_acquire(kind="turn", owner=agent.id)
    assert leaked is not None
    dispatcher._turn_lanes[agent.id] = leaked

    # Not running: _drain_queue runs only its once-per-call sweep.
    with caplog.at_level(logging.ERROR, logger="core.agent_loop.dispatcher"):
        await dispatcher._drain_queue()

    refreshed = db.get_agent_trigger(claimed.id)
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert refreshed.failure_reason == "Turn claim stranded with no live turn"
    assert _lane_rows() == 0
    assert dispatcher._turn_lanes == {}
    assert any(
        record.levelno == logging.ERROR
        and record.getMessage().startswith(f"Recovering stranded trigger claim {claimed.id}")
        for record in caplog.records
    )
    assert any("no live turn" in error for error in _diagnostic_errors(agent.id))


@pytest.mark.asyncio
async def test_sweep_leaves_a_stale_claim_whose_turn_is_alive() -> None:
    agent = _create_agent()
    claimed = db.claim_trigger(_queued_trigger(agent.id).id)
    assert claimed is not None
    _age_claim(claimed.id, ORPHAN_CLAIM_AFTER.total_seconds() + 5)
    dispatcher = TurnDispatcher()
    live = asyncio.create_task(asyncio.sleep(60))
    dispatcher._active_turns[agent.id] = live
    try:
        await dispatcher._drain_queue()
    finally:
        live.cancel()

    refreshed = db.get_agent_trigger(claimed.id)
    assert refreshed.status == "claimed"
    assert refreshed.retry_count == 0
    assert refreshed.claim_generation == claimed.claim_generation
    assert _diagnostic_errors(agent.id) == []


@pytest.mark.asyncio
async def test_sweep_fails_a_retry_blocked_orphan_instead_of_requeueing_it() -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    _age_claim(blocked.id, ORPHAN_CLAIM_AFTER.total_seconds() + 5)

    await TurnDispatcher()._drain_queue()

    refreshed = db.get_agent_trigger(blocked.id)
    assert refreshed.status == "failed"
    assert refreshed.retry_count == 0
    assert refreshed.failure_reason == "Turn claim stranded with no live turn"


@pytest.mark.asyncio
async def test_start_fails_a_blocked_orphan_through_the_shared_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    resolved: list[tuple[str, str, Exception]] = []
    real_resolve = TurnDispatcher._resolve_stranded_claim

    async def _spy(self: TurnDispatcher, row: Any, *, detail: str, exc: Exception) -> None:
        resolved.append((row.id, detail, exc))
        await real_resolve(self, row, detail=detail, exc=exc)

    monkeypatch.setattr(TurnDispatcher, "_resolve_stranded_claim", _spy)

    _dispatcher, seen = await _start_until_first_drain(monkeypatch)

    assert seen[blocked.id] == "failed"
    [(row_id, detail, exc)] = resolved
    assert row_id == blocked.id
    assert detail.startswith("The runtime stopped during a turn that had already run")
    assert isinstance(exc, StrandedClaimError)
    # Start-up recovery is now visible in Diagnostics too.
    assert any(error.startswith("The runtime stopped") for error in _diagnostic_errors(agent.id))
