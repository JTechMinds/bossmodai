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
from core.agent_loop.dispatcher import TurnDispatcher
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


def test_stale_generation_cannot_complete_a_reclaimed_trigger() -> None:
    agent = _create_agent()
    row = _queued_trigger(agent.id)
    first = db.claim_trigger(row.id)
    assert first is not None
    db.requeue_stale_triggers(1, force=True)
    second = db.claim_trigger(row.id)
    assert second is not None
    assert second.claim_generation == first.claim_generation + 1

    assert db.complete_agent_trigger(row.id, claim_generation=first.claim_generation) is None
    still = db.get_agent_trigger(row.id)
    assert still is not None
    assert still.status == "claimed"
    assert still.claim_generation == second.claim_generation

    done = db.complete_agent_trigger(row.id, claim_generation=second.claim_generation)
    assert done is not None
    assert done.status == "completed"


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
