"""A failing AI connection is paused, backs off, and is never called while paused.

Every test creates a real ``ai_connections`` row: the health row is keyed to
it, and a connection that does not exist records nothing.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import litellm
import pytest

import db
from core import config
from core.extensions.model_provider import ManagedModel, ProviderError, ProviderTimeout, ProviderUsageLimitError
from core.llm import connection_health
from core.llm.client import (
    LLMConnectionSuspendedError,
    LLMEmptyResponseError,
    LLMError,
    LLMTimeoutError,
    LLMUsageLimitError,
    classify_connection_failure,
    completion,
)
from core.llm.system_completion import complete_text
from db.managed_connections import reconcile

_BASE_URL = "http://127.0.0.1:9/v1"
_DISABLED_KEY = "LlmProxy::Error: No active task for this API key's project"
_MESSAGES = [{"role": "user", "content": "hi"}]


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


def _connection(name: str = "Proxy key") -> str:
    return db.create_connection(name=name, api_base_url=_BASE_URL, api_key="sk-test", model="test-model").id


def _threshold() -> int:
    return config.require_int("llm_connection_failure_threshold")


def _fail(connection_id: str, kind: str, times: int = 1, error: str = _DISABLED_KEY):
    health = None
    for _ in range(times):
        health = connection_health.record_failure(connection_id, kind=kind, error=error)
    return health


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", f"{_BASE_URL}/chat/completions"))


# ─── Classification: the table in the plan, with real exception instances ───


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (litellm.exceptions.APIConnectionError(message="refused", llm_provider="openai", model="m"), "transient"),
        (litellm.exceptions.InternalServerError(message="500", llm_provider="openai", model="m"), "transient"),
        (litellm.exceptions.ServiceUnavailableError(message="503", llm_provider="openai", model="m"), "transient"),
        (litellm.exceptions.BadGatewayError(message="502", llm_provider="openai", model="m"), "transient"),
        (litellm.exceptions.RateLimitError(message="429", llm_provider="openai", model="m"), "transient"),
        (ProviderError("The service is busy."), "transient"),
        (litellm.exceptions.BadRequestError(message=_DISABLED_KEY, model="m", llm_provider="openai"), "rejected"),
        (litellm.exceptions.AuthenticationError(message="401", llm_provider="openai", model="m"), "rejected"),
        (
            litellm.exceptions.PermissionDeniedError(
                message="403", llm_provider="openai", model="m", response=_response(403),
            ),
            "rejected",
        ),
        (litellm.exceptions.NotFoundError(message="404", model="m", llm_provider="openai"), "rejected"),
        (
            litellm.exceptions.UnprocessableEntityError(
                message="422", model="m", llm_provider="openai", response=_response(422),
            ),
            "rejected",
        ),
        (ProviderUsageLimitError("You have reached your usage limit."), "rejected"),
        (litellm.Timeout(message="timed out", model="m", llm_provider="openai"), None),
        (LLMTimeoutError(5, kind="stall"), None),
        (LLMEmptyResponseError("empty"), None),
        (ProviderTimeout(5, "backstop"), None),
        (litellm.exceptions.ContextWindowExceededError(message="too long", model="m", llm_provider="openai"), None),
        (litellm.exceptions.ContentPolicyViolationError(message="policy", model="m", llm_provider="openai"), None),
        (KeyError("a BossMod bug"), None),
        (config.ConfigError("a local config error"), None),
    ],
    ids=[
        "connection", "500", "503", "502", "429", "provider_error",
        "400", "401", "403", "404", "422", "usage_limit",
        "provider_timeout", "llm_timeout", "empty_reply", "extension_timeout",
        "context_window", "content_policy", "bug", "config",
    ],
)
def test_classification_follows_the_exception_class(error: BaseException, kind: str | None) -> None:
    assert classify_connection_failure(error) == kind


# ─── Counting and pausing ───


def test_below_the_threshold_the_connection_is_not_paused() -> None:
    connection_id = _connection()
    health = _fail(connection_id, "rejected", _threshold() - 1)
    assert health is not None
    assert health.failure_count == _threshold() - 1
    assert not health.is_suspended
    assert health.retry_at is None
    assert connection_health.admit(connection_id, lease_seconds=60).allowed


def test_a_rejected_pause_waits_for_the_operator() -> None:
    connection_id = _connection()
    health = _fail(connection_id, "rejected", _threshold())
    assert health.is_suspended
    assert health.failure_kind == "rejected"
    assert health.last_error == _DISABLED_KEY
    assert health.retry_at is None
    assert health.backoff_seconds is None
    assert connection_health.is_suspended(connection_id)


def test_a_transient_pause_tests_again_after_the_first_wait() -> None:
    connection_id = _connection()
    before = _now()
    health = _fail(connection_id, "transient", _threshold())
    first = config.require_int("llm_connection_backoff_seconds")
    assert health.is_suspended
    assert health.backoff_seconds == first
    assert before + timedelta(seconds=first) <= health.retry_at <= _now() + timedelta(seconds=first)


def test_the_last_error_is_collapsed_not_shortened() -> None:
    connection_id = _connection()
    long_error = "line one\n\n   line two " + "x" * 2000
    health = _fail(connection_id, "rejected", error=long_error)
    assert health.last_error == "line one line two " + "x" * 2000


def test_a_success_resets_the_count() -> None:
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold() - 1)
    connection_health.record_success(connection_id)
    assert db.get_connection_health(connection_id) is None
    health = _fail(connection_id, "rejected", _threshold() - 1)
    assert not health.is_suspended


def test_a_threshold_below_one_is_a_config_error() -> None:
    connection_id = _connection()
    db.set_setting("llm_connection_failure_threshold", "0", "llm")
    config.reload()
    with pytest.raises(config.ConfigError):
        _fail(connection_id, "rejected")


# ─── Admission, the test call and its lease ───


def test_admit_denies_while_held_then_grants_exactly_one_test_call() -> None:
    connection_id = _connection()
    _fail(connection_id, "transient", _threshold())
    held = connection_health.admit(connection_id, lease_seconds=30)
    assert not held.allowed
    assert held.health is not None and held.health.last_error == _DISABLED_KEY

    assert connection_health.retry_now(connection_id)
    probe = connection_health.admit(connection_id, lease_seconds=30)
    assert probe.allowed and probe.probe
    assert probe.lease_until is not None
    assert db.get_connection_health(connection_id).retry_at == probe.lease_until
    # The lease blocks a second caller while the test call runs.
    assert not connection_health.admit(connection_id, lease_seconds=30).allowed


def test_failed_test_calls_double_the_wait_up_to_the_cap() -> None:
    db.set_setting("llm_connection_backoff_max_seconds", "200", "llm")
    config.reload()
    connection_id = _connection()
    _fail(connection_id, "transient", _threshold())
    waits = []
    for _ in range(3):
        connection_health.retry_now(connection_id)
        assert connection_health.admit(connection_id, lease_seconds=30).probe
        health = _fail(connection_id, "transient")
        waits.append(health.backoff_seconds)
        assert health.retry_at > _now() + timedelta(seconds=health.backoff_seconds - 5)
    assert waits == [120, 200, 200]


def test_a_rejected_test_call_waits_for_the_operator_and_transient_restarts_the_wait() -> None:
    connection_id = _connection()
    _fail(connection_id, "transient", _threshold())
    connection_health.retry_now(connection_id)
    connection_health.admit(connection_id, lease_seconds=30)
    rejected = _fail(connection_id, "rejected")
    assert rejected.is_suspended and rejected.retry_at is None and rejected.backoff_seconds is None

    connection_health.retry_now(connection_id)
    connection_health.admit(connection_id, lease_seconds=30)
    transient = _fail(connection_id, "transient")
    assert transient.backoff_seconds == config.require_int("llm_connection_backoff_seconds")


def test_a_successful_test_call_deletes_the_row() -> None:
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold())
    connection_health.retry_now(connection_id)
    assert connection_health.admit(connection_id, lease_seconds=30).probe
    connection_health.record_success(connection_id)
    assert db.get_connection_health(connection_id) is None
    assert connection_health.admit(connection_id, lease_seconds=30) == connection_health.Admission(
        allowed=True, probe=False, lease_until=None, health=None,
    )


def test_an_undecided_test_call_rearms_the_current_wait() -> None:
    connection_id = _connection()
    _fail(connection_id, "transient", _threshold())
    # One failed test call doubles the wait, so "not doubled again" is visible.
    connection_health.retry_now(connection_id)
    connection_health.admit(connection_id, lease_seconds=600)
    wait = _fail(connection_id, "transient").backoff_seconds
    connection_health.retry_now(connection_id)
    probe = connection_health.admit(connection_id, lease_seconds=600)
    assert probe.probe

    before = _now()
    connection_health.end_undecided_test(connection_id, lease_until=probe.lease_until)
    health = db.get_connection_health(connection_id)
    assert health.is_suspended and health.backoff_seconds == wait
    assert before + timedelta(seconds=wait) <= health.retry_at <= _now() + timedelta(seconds=wait)
    # Never due at once: the next test call waits for the wait to end.
    assert not connection_health.admit(connection_id, lease_seconds=600).allowed


def test_an_undecided_test_call_on_a_refused_connection_waits_for_the_operator_again() -> None:
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold())
    connection_health.retry_now(connection_id)
    probe = connection_health.admit(connection_id, lease_seconds=600)
    assert probe.probe

    connection_health.end_undecided_test(connection_id, lease_until=probe.lease_until)
    health = db.get_connection_health(connection_id)
    assert health.is_suspended and health.failure_count == _threshold()
    assert health.retry_at is None
    assert not connection_health.admit(connection_id, lease_seconds=600).allowed


def test_an_undecided_test_call_changes_nothing_once_its_lease_was_moved() -> None:
    connection_id = _connection()
    _fail(connection_id, "transient", _threshold())
    connection_health.retry_now(connection_id)
    probe = connection_health.admit(connection_id, lease_seconds=600)

    # A stale lease (the operator or a result moved retry_at) changes nothing.
    connection_health.end_undecided_test(connection_id, lease_until=probe.lease_until - timedelta(seconds=1))
    assert db.get_connection_health(connection_id).retry_at == probe.lease_until


def test_retry_now_is_only_for_a_paused_connection() -> None:
    connection_id = _connection()
    assert connection_health.retry_now(connection_id) is False
    _fail(connection_id, "rejected", _threshold() - 1)
    assert connection_health.retry_now(connection_id) is False
    _fail(connection_id, "rejected")
    assert db.get_connection_health(connection_id).retry_at is None
    assert connection_health.retry_now(connection_id) is True
    assert db.get_connection_health(connection_id).retry_at <= _now()


# ─── The connection's own lifecycle ───


def test_editing_the_key_clears_the_pause_and_a_rename_keeps_it() -> None:
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold())
    db.update_connection(connection_id, name="Renamed")
    assert connection_health.is_suspended(connection_id)
    db.update_connection(connection_id, api_key="sk-new")
    assert db.get_connection_health(connection_id) is None


def test_a_save_that_resends_the_same_call_values_keeps_the_pause() -> None:
    levels = {"high": {"reasoning": {"effort": "high"}}}
    connection_id = db.create_connection(
        name="Proxy key",
        api_base_url=_BASE_URL,
        api_key="sk-test",
        model="test-model",
        extra_body='{"top_k": 5}',
        thinking_levels=levels,
    ).id
    _fail(connection_id, "rejected", _threshold())

    # The Settings form's save: a new name plus the unchanged fields it re-sends.
    db.update_connection(
        connection_id, name="Renamed", api_base_url=_BASE_URL, model="test-model", thinking_levels=levels,
    )
    assert connection_health.is_suspended(connection_id)
    # The same key and extra body again: new ciphertext, the same values.
    db.update_connection(connection_id, api_key="sk-test", extra_body='{"top_k": 5}')
    assert connection_health.is_suspended(connection_id)


def test_an_extension_reconcile_with_the_same_entries_keeps_the_pause() -> None:
    entries = [
        ManagedModel(
            key="model-1", name="Model One", model="model-1",
            thinking_levels={"high": {"reasoning": {"effort": "high"}}},
        ),
    ]
    connection_id = reconcile("demo-provider", "Demo", entries)["model-1"]
    _fail(connection_id, "rejected", _threshold())

    # An extension re-publishes its connections on every status read.
    assert reconcile("demo-provider", "Demo", entries) == {"model-1": connection_id}
    assert connection_health.is_suspended(connection_id)


def test_deleting_the_connection_deletes_its_row() -> None:
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold())
    assert db.delete_connection(connection_id)
    assert db.query_one(
        "SELECT 1 AS present FROM ai_connection_health WHERE connection_id = $1", [connection_id],
    ) is None


def test_recording_for_a_vanished_connection_is_a_no_op() -> None:
    assert connection_health.record_failure("no-such-connection", kind="rejected", error="gone") is None
    connection_health.record_success("no-such-connection")
    assert db.get_connection_health("no-such-connection") is None
    assert not connection_health.is_suspended("no-such-connection")
    assert not connection_health.is_suspended(None)


# ─── The two call sites ───


def _script_acompletion(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise error

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    return calls


@pytest.mark.asyncio
async def test_incident_a_disabled_key_is_called_threshold_times_then_never(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The incident: a 400 from a disabled key, every wake calling it again."""
    connection_id = _connection()
    calls = _script_acompletion(
        monkeypatch, litellm.exceptions.BadRequestError(message=_DISABLED_KEY, model="m", llm_provider="openai"),
    )
    raised: list[type[BaseException]] = []
    for _ in range(5):
        with pytest.raises(LLMError) as error:
            await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
        raised.append(type(error.value))

    assert len(calls) == 3
    assert raised[3:] == [LLMConnectionSuspendedError, LLMConnectionSuspendedError]
    assert all(kind is LLMError for kind in raised[:3])
    assert _DISABLED_KEY in db.get_connection_health(connection_id).last_error


@pytest.mark.asyncio
async def test_a_timed_out_test_call_waits_for_the_operator_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """One "Retry now" allows one contact, whatever the test call's outcome."""
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold())
    connection_health.retry_now(connection_id)
    calls = _script_acompletion(monkeypatch, litellm.Timeout(message="slow", model="m", llm_provider="openai"))
    with pytest.raises(LLMTimeoutError):
        await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
    health = db.get_connection_health(connection_id)
    assert health.is_suspended and health.failure_count == _threshold()
    assert health.retry_at is None

    with pytest.raises(LLMConnectionSuspendedError):
        await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_refused_call_carries_rejected_and_an_empty_reply_carries_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = _connection()
    _script_acompletion(
        monkeypatch, litellm.exceptions.BadRequestError(message=_DISABLED_KEY, model="m", llm_provider="openai"),
    )
    with pytest.raises(LLMError) as refused:
        await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
    assert refused.value.connection_failure == "rejected"
    assert db.get_connection_health(connection_id).failure_kind == "rejected"

    db.set_setting("llm_transient_retries", "0", "llm")
    config.reload()

    async def _empty_reply(**_kwargs: Any) -> Any:
        return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "model": "m"}

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _empty_reply)
    with pytest.raises(LLMEmptyResponseError) as empty:
        await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
    assert empty.value.connection_failure is None
    # Not counted: the count is still the refusal's.
    assert db.get_connection_health(connection_id).failure_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolve_error", "provider_error", "raised", "kind"),
    [
        (None, ProviderUsageLimitError("You have reached your usage limit."), LLMUsageLimitError, "rejected"),
        (None, ProviderError("The service is busy."), LLMError, "transient"),
        # A local error before any provider contact records nothing and carries nothing.
        (ProviderError("Enable Demo in Extensions to use this connection."), None, LLMError, None),
    ],
    ids=["usage_limit", "provider_error", "disabled_extension"],
)
async def test_an_extension_provider_failure_carries_the_kind_it_recorded(
    monkeypatch: pytest.MonkeyPatch,
    resolve_error: BaseException | None,
    provider_error: BaseException | None,
    raised: type[LLMError],
    kind: str | None,
) -> None:
    connection_id = _connection()

    def _resolve(_ext_id: str) -> object:
        if resolve_error is not None:
            raise resolve_error
        return object()

    async def _complete(_provider: object, _request: Any) -> Any:
        raise provider_error

    monkeypatch.setattr("core.extensions.model_provider.provider_id", lambda _api_base: "demo-provider")
    monkeypatch.setattr("core.extensions.model_provider.resolve_provider", _resolve)
    monkeypatch.setattr("core.extensions.model_provider.complete", _complete)
    with pytest.raises(LLMError) as error:
        await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
    assert type(error.value) is raised
    assert error.value.connection_failure == kind
    if kind is None:
        assert db.get_connection_health(connection_id) is None
    else:
        assert db.get_connection_health(connection_id).failure_kind == kind


@pytest.mark.asyncio
async def test_the_test_call_lease_covers_every_attempt_and_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in (
        ("llm_request_timeout_seconds", "100"),
        ("llm_transient_retries", "2"),
        ("llm_transient_retry_backoff_seconds", "3"),
    ):
        db.set_setting(key, value, "llm")
    config.reload()
    connection_id = _connection()
    leases: list[float] = []
    real_admit = connection_health.admit

    def _admit(connection_id: str, *, lease_seconds: float) -> connection_health.Admission:
        leases.append(lease_seconds)
        return real_admit(connection_id, lease_seconds=lease_seconds)

    monkeypatch.setattr(connection_health, "admit", _admit)
    _script_acompletion(
        monkeypatch, litellm.exceptions.BadRequestError(message=_DISABLED_KEY, model="m", llm_provider="openai"),
    )
    with pytest.raises(LLMError):
        await completion(model="test-model", messages=_MESSAGES, api_base=_BASE_URL, connection_id=connection_id)
    # max_attempts * backstop + backoff * (2 ** retries - 1): three 100 s
    # attempts, plus the 3 s and 6 s waits between them.
    assert leases == [3 * 100 + 3 * (2 ** 2 - 1)]


def test_complete_text_does_not_contact_the_provider_while_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    connection_id = _connection()
    _fail(connection_id, "rejected", _threshold())
    calls: list[dict[str, Any]] = []

    def _fake_completion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise AssertionError("a paused connection must not be called")

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake_completion)
    assert complete_text([{"role": "user", "content": "route"}]) is None
    assert calls == []


def test_complete_text_counts_a_refused_call_and_a_success_clears_it(monkeypatch: pytest.MonkeyPatch) -> None:
    connection_id = _connection()
    replies: list[Any] = [
        litellm.exceptions.AuthenticationError(message="401", llm_provider="openai", model="m"),
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
    ]

    def _fake_completion(**_kwargs: Any) -> Any:
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr("core.llm.system_completion.litellm.completion", _fake_completion)
    assert complete_text([{"role": "user", "content": "route"}]) is None
    assert db.get_connection_health(connection_id).failure_count == 1
    assert complete_text([{"role": "user", "content": "route"}]) == "ok"
    assert db.get_connection_health(connection_id) is None
