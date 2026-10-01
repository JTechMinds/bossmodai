"""HA-SEC-P1-02 — trigger leases / heartbeat / crash requeue."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.agent_loop.dispatcher import NOT_RETRIED_NO_REPEAT_REASON, TurnDispatcher
from core.agent_loop.outcomes import TurnOutcome


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

    recovered = db.requeue_stale_triggers(1, force=True)
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

    first = db.requeue_stale_triggers(300, force=True)
    assert first == 1
    queued = db.get_agent_trigger(row.id)
    assert queued is not None
    assert queued.status == "queued"
    assert queued.claim_lease is None

    second = db.requeue_stale_triggers(300, force=True)
    assert second == 0
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "queued"


def test_live_worker_does_not_steal_an_old_claim() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None

    db.mark_runtime_worker_running(pid=os.getpid())
    db.record_runtime_worker_heartbeat(pid=os.getpid())
    stale = datetime.now(timezone.utc) - timedelta(seconds=900)
    db.execute(
        "UPDATE agent_triggers SET claimed_at = $1 WHERE id = $2",
        [stale, row.id],
    )

    recovered = db.requeue_stale_triggers(1, force=False, worker_stale_after_seconds=15)
    assert recovered == 0
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "claimed"


def test_dead_worker_requeues_stale_claim() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    db.mark_runtime_worker_stopped(pid=os.getpid())
    stale = datetime.now(timezone.utc) - timedelta(seconds=900)
    db.execute(
        "UPDATE agent_triggers SET claimed_at = $1 WHERE id = $2",
        [stale, row.id],
    )

    recovered = db.requeue_stale_triggers(1, force=False, worker_stale_after_seconds=15)
    assert recovered == 1
    refreshed = db.get_agent_trigger(row.id)
    assert refreshed is not None
    assert refreshed.status == "queued"


def _reclaim_after_force(agent_id: str):
    row = _queued_trigger(agent_id)
    first = db.claim_trigger(row.id)
    assert first is not None
    db.requeue_stale_triggers(1, force=True)
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
        row.id, "stale retry", claim_generation=first.claim_generation
    ) is None
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "claimed"
    assert still.claim_generation == second.claim_generation
    assert still.retry_count == 0
    assert still.failure_reason is None

    retried = db.retry_agent_trigger(
        row.id, "live retry", claim_generation=second.claim_generation
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

    assert db.requeue_stale_triggers(300, force=True) == 1

    assert db.get_agent_trigger(blocked.id).status == "claimed"
    assert db.get_agent_trigger(plain.id).status == "queued"
    assert [item.id for item in db.list_claimed_retry_blocked_triggers()] == [blocked.id]


def test_stale_requeue_leaves_a_blocked_orphan_claimed() -> None:
    agent = _create_agent()
    blocked = _claimed_blocked_trigger(agent.id)
    db.mark_runtime_worker_stopped(pid=os.getpid())
    stale = datetime.now(timezone.utc) - timedelta(seconds=900)
    db.execute("UPDATE agent_triggers SET claimed_at = $1 WHERE id = $2", [stale, blocked.id])

    assert db.requeue_stale_triggers(1, force=False, worker_stale_after_seconds=15) == 0
    assert db.get_agent_trigger(blocked.id).status == "claimed"


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
