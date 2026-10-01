"""The one schedule-run announcement (core/scheduling/announce.py), against a recording sink."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.models.schedule import RecurrenceRule
from core.scheduling.announce import announce_run
from core.scheduling.runner import ScheduleRun

RULE = RecurrenceRule.model_validate(
    {"frequency": "daily", "interval": 1, "times": ["06:00"], "start_date": "2026-09-01"},
)
CHAT_LINE = {"chat_message": {"agent_id": "a1", "content": 'Created "Status check"', "message_id": "m1"}}


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def broadcast_chat_message(self, **data: Any) -> None:
        self.calls.append(("chat", data))

    async def broadcast_channel_message(self, **data: Any) -> None:
        self.calls.append(("channel", data))

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.calls.append(("activity", {"event": event, "detail": detail, "agent_name": agent_name, "extra": extra}))


def _run(outcome: str, *, changed: bool, fired: bool) -> ScheduleRun:
    return ScheduleRun(
        schedule_id="s1",
        agent_id="a1",
        title="Status check",
        outcome=outcome,
        task=SimpleNamespace(title="Status check") if fired else None,
        trigger=None,
        rule=RULE,
        changed=changed,
        origin_line=CHAT_LINE if fired else {},
    )


def _chat() -> tuple[str, dict[str, Any]]:
    return ("chat", {
        "agent_id": "a1", "content": 'Created "Status check"', "from_type": "system", "from_name": "BossMod",
        "message_type": None, "message_id": "m1", "created_at": None, "notification_kind": None,
    })


def _activity(event: str, detail: str, extra: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    return ("activity", {"event": event, "detail": detail, "agent_name": "Ada", "extra": extra})


async def test_a_fired_run_sends_its_line_then_task_created_then_schedule_ran() -> None:
    sink = RecordingSink()
    await announce_run(sink, _run("fired", changed=True, fired=True), agent_name="Ada")
    assert sink.calls == [
        _chat(),
        _activity("task_created", 'Scheduled task "Status check" created'),
        _activity("schedule_ran", 'Schedule "Status check" ran',
                  {"agent_id": "a1", "schedule_id": "s1", "outcome": "fired"}),
    ]


async def test_a_changed_skip_sends_only_schedule_ran_with_its_outcome_line() -> None:
    sink = RecordingSink()
    await announce_run(sink, _run("skipped_open", changed=True, fired=False), agent_name="Ada")
    assert sink.calls == [
        _activity("schedule_ran", 'Schedule "Status check" skipped a run (the last run is still open)',
                  {"agent_id": "a1", "schedule_id": "s1", "outcome": "skipped_open"}),
    ]


async def test_an_unchanged_skip_sends_nothing() -> None:
    sink = RecordingSink()
    await announce_run(sink, _run("skipped_open", changed=False, fired=False), agent_name="Ada")
    assert sink.calls == []


async def test_run_now_reads_ran_now() -> None:
    sink = RecordingSink()
    await announce_run(sink, _run("fired", changed=True, fired=True), agent_name="Ada", manual=True)
    assert sink.calls == [
        _chat(),
        _activity("task_created", 'Scheduled task "Status check" created'),
        _activity("schedule_ran", 'Schedule "Status check" ran now',
                  {"agent_id": "a1", "schedule_id": "s1", "outcome": "fired"}),
    ]
