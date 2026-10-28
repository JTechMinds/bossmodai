"""The dispatcher holds work for a paused AI connection instead of running it.

Held wakes stay queued and unpainted; one turn per due connection is let
through to make its test call; a turn that failed because its connection is
paused is parked without using up a retry; and each pause and resume is
announced once.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm
import pytest

import db
from core import config
from core.agent_loop.dispatcher import NOT_RETRIED_NO_REPEAT_REASON, TurnDispatcher
from core.agent_loop.turn_claims import turn_trigger
from core.llm import connection_health
from core.llm.call_budget import budget
from core.llm.client import LLMConnectionSuspendedError, LLMError, completion

_ERROR = "LlmProxy::Error: No active task for this API key's project"
_BASE_URL = "http://127.0.0.1:9/v1"


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


def _connection(name: str = "Proxy key") -> str:
    return db.create_connection(name=name, api_base_url=_BASE_URL, model="test-model").id


def _pause(connection_id: str, kind: str = "rejected") -> None:
    for _ in range(config.require_int("llm_connection_failure_threshold")):
        connection_health.record_failure(connection_id, kind=kind, error=_ERROR)
    assert connection_health.is_suspended(connection_id)


def _agent(name: str, connection_id: str):
    return db.create_agent(name, role="Eng", desk_x=1, desk_y=1, connection_id=connection_id)


def _wake(agent_id: str):
    return db.create_agent_trigger(
        agent_id=agent_id,
        trigger_type="human_chat",
        source_channel="chat",
        payload={"content": "go", "from_name": "Human"},
    )


def test_a_wake_on_a_paused_connection_stays_queued_and_unpainted() -> None:
    connection_id = _connection()
    ada = _agent("Ada", connection_id)
    row = _wake(ada.id)
    _pause(connection_id)

    dispatcher = TurnDispatcher()
    assert dispatcher._claim_available_trigger() is None
    assert db.get_agent_trigger(row.id).status == "queued"
    # Held, not waiting for a lane: no "Queued (n ahead)" is painted for it.
    assert dispatcher._queue_notices == []
    assert budget.inflight() == 0


def test_a_wake_on_a_healthy_connection_is_not_held_by_another_pause() -> None:
    paused_id = _connection("Proxy key")
    healthy_id = _connection("Local")
    ada = _agent("Ada", paused_id)
    bea = _agent("Bea", healthy_id)
    _wake(ada.id)
    held_for_bea = _wake(bea.id)
    _pause(paused_id)

    claimed = TurnDispatcher()._claim_available_trigger()
    assert claimed is not None and claimed.id == held_for_bea.id


def test_a_due_connection_lets_one_turn_through_and_holds_the_rest_until_the_result() -> None:
    connection_id = _connection()
    ada = _agent("Ada", connection_id)
    bea = _agent("Bea", connection_id)
    _wake(ada.id)
    _wake(bea.id)
    _pause(connection_id, kind="transient")
    assert connection_health.retry_now(connection_id)

    dispatcher = TurnDispatcher()
    first = dispatcher._claim_available_trigger()
    assert first is not None
    assert dispatcher._probe_turns == {connection_id: first.agent_id}
    other = bea.id if first.agent_id == ada.id else ada.id
    # The test turn is in flight: the other wake waits for its result.
    assert dispatcher._claim_available_trigger() is None
    assert [t["status"] for t in db.list_agent_triggers(other)] == ["queued"]

    # The test call failed: the connection waits again, and so does the wake.
    assert connection_health.admit(connection_id, lease_seconds=30).probe
    connection_health.record_failure(connection_id, kind="transient", error=_ERROR)
    dispatcher._release_turn_reservations(first.agent_id)
    assert dispatcher._probe_turns == {}
    assert dispatcher._claim_available_trigger() is None

    # A success resumes the connection: the held wake runs.
    connection_health.record_success(connection_id)
    resumed = dispatcher._claim_available_trigger()
    assert resumed is not None and resumed.agent_id == other
    assert dispatcher._probe_turns == {}


def _claimed(agent_id: str) -> dict[str, Any]:
    row = _wake(agent_id)
    claimed = db.claim_trigger(row.id)
    assert claimed is not None
    return turn_trigger(claimed)


@pytest.mark.asyncio
async def test_a_replay_safe_failure_on_a_paused_connection_is_parked_without_a_retry() -> None:
    connection_id = _connection()
    ada = _agent("Ada", connection_id)
    trigger = _claimed(ada.id)
    _pause(connection_id)

    await TurnDispatcher()._supervise_failed_turn(
        agent=ada,
        trigger=trigger,
        failure_detail="AI connection is paused",
        not_retried_reason=None,
        cause=LLMConnectionSuspendedError(connection_id, last_error=_ERROR),
        replay_safe=True,
    )

    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.status == "queued"
    assert row.retry_count == 0
    # Parked, so the claim gate holds it until the connection resumes.
    assert TurnDispatcher()._claim_available_trigger() is None


def _provider_raises(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    async def _fake_acompletion(**_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)


async def _test_call(connection_id: str) -> LLMError:
    with pytest.raises(LLMError) as failed:
        await completion(
            model="test-model",
            messages=[{"role": "user", "content": "go"}],
            api_base=_BASE_URL,
            connection_id=connection_id,
        )
    return failed.value


@pytest.mark.asyncio
async def test_an_uncounted_failure_of_the_test_turn_uses_a_retry_and_is_not_reclaimed_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test turn that fails for a reason that does not count must not loop."""
    connection_id = _connection()
    ada = _agent("Ada", connection_id)
    _wake(ada.id)
    _pause(connection_id, kind="transient")
    assert connection_health.retry_now(connection_id)

    dispatcher = TurnDispatcher()
    claimed = dispatcher._claim_available_trigger()
    assert claimed is not None
    assert dispatcher._probe_turns == {connection_id: ada.id}

    # The test call fails for that one request only: it says nothing about
    # the connection.
    _provider_raises(
        monkeypatch,
        litellm.exceptions.ContextWindowExceededError(message="too long", model="m", llm_provider="openai"),
    )
    failed = await _test_call(connection_id)
    assert failed.connection_failure is None

    await dispatcher._supervise_failed_turn(
        agent=ada,
        trigger=turn_trigger(claimed),
        failure_detail=str(failed),
        not_retried_reason=None,
        cause=failed,
        replay_safe=True,
    )
    dispatcher._release_turn_reservations(ada.id)

    # Retried, not parked: it used a retry.
    row = db.get_agent_trigger(claimed.id)
    assert row.status == "queued"
    assert row.retry_count == 1
    # The undecided test call re-armed the wait, so the connection is held,
    # not due, and the same trigger is not claimed again in the same instant.
    health = db.get_connection_health(connection_id)
    assert health.is_suspended
    assert not connection_health.is_probe_ready(health, datetime.now(timezone.utc))
    assert dispatcher._claim_available_trigger() is None

    # Only the pause holds it: once the connection works again, it runs.
    connection_health.record_success(connection_id)
    resumed = dispatcher._claim_available_trigger()
    assert resumed is not None and resumed.id == claimed.id


@pytest.mark.asyncio
async def test_a_failure_the_connection_caused_is_still_parked(monkeypatch: pytest.MonkeyPatch) -> None:
    connection_id = _connection()
    ada = _agent("Ada", connection_id)
    trigger = _claimed(ada.id)
    _pause(connection_id)
    assert connection_health.retry_now(connection_id)

    # The operator's test call is refused again: the provider's own answer
    # about the connection, which stays paused.
    _provider_raises(
        monkeypatch,
        litellm.exceptions.BadRequestError(message=_ERROR, model="m", llm_provider="openai"),
    )
    failed = await _test_call(connection_id)
    assert failed.connection_failure == "rejected"
    assert connection_health.is_suspended(connection_id)

    await TurnDispatcher()._supervise_failed_turn(
        agent=ada,
        trigger=trigger,
        failure_detail=str(failed),
        not_retried_reason=None,
        cause=failed,
        replay_safe=True,
    )

    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.status == "queued"
    assert row.retry_count == 0


@pytest.mark.asyncio
async def test_a_failure_that_is_not_replay_safe_follows_the_stall_path() -> None:
    connection_id = _connection()
    ada = _agent("Ada", connection_id)
    trigger = _claimed(ada.id)
    _pause(connection_id)

    await TurnDispatcher()._supervise_failed_turn(
        agent=ada,
        trigger=trigger,
        failure_detail="AI connection is paused",
        not_retried_reason=NOT_RETRIED_NO_REPEAT_REASON,
        cause=LLMConnectionSuspendedError(connection_id, last_error=_ERROR),
        replay_safe=False,
    )

    row = db.get_agent_trigger(trigger["trigger_id"])
    assert row.status == "failed"
    assert row.retry_count == 0


class _Recorder:
    """Stands in for the runtime event sink; keeps every activity broadcast."""

    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []

    async def broadcast_activity(self, **kwargs: Any) -> None:
        self.activities.append(kwargs)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [item for item in self.activities if item["event"] == name]


@pytest.mark.asyncio
async def test_pause_and_resume_are_each_announced_once(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("core.agent_loop.dispatcher.manager", recorder)
    connection_id = _connection()
    dispatcher = TurnDispatcher()

    await dispatcher._announce_connection_health()
    assert recorder.activities == []

    _pause(connection_id)
    await dispatcher._announce_connection_health()
    await dispatcher._announce_connection_health()
    assert recorder.events("ai_connection_suspended") == [{
        "event": "ai_connection_suspended",
        "detail": f'AI connection "Proxy key" is paused: {_ERROR}',
        "extra": {"connection_id": connection_id, "failure_kind": "rejected", "retry_at": None},
    }]

    connection_health.record_success(connection_id)
    await dispatcher._announce_connection_health()
    await dispatcher._announce_connection_health()
    assert recorder.events("ai_connection_resumed") == [{
        "event": "ai_connection_resumed",
        "detail": 'AI connection "Proxy key" is working again',
        "extra": {"connection_id": connection_id},
    }]
    assert len(recorder.activities) == 2


@pytest.mark.asyncio
async def test_a_deleted_paused_connection_is_announced_as_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("core.agent_loop.dispatcher.manager", recorder)
    connection_id = _connection()
    _pause(connection_id)
    dispatcher = TurnDispatcher()
    await dispatcher._announce_connection_health()

    assert db.delete_connection(connection_id)
    await dispatcher._announce_connection_health()
    assert [item["detail"] for item in recorder.events("ai_connection_resumed")] == [
        "A paused AI connection was deleted",
    ]
