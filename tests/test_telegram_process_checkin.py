"""Telegram's background-process check-in: the card, its buttons, and the bridge that sends it."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

import db
from core import config
from core.bm_cli import background_processes
from integrations.telegram.bridge import TelegramEventBridge
from integrations.telegram.process_checkin import format_checkin_card, handle_process_checkin_callback
from integrations.telegram.sessions import upsert_session

_ALLOWED = 111


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    db.set_setting("telegram_allowed_user_ids", str(_ALLOWED), "telegram")
    config.reload()


@pytest.fixture(autouse=True)
def _end_rows() -> Iterator[None]:
    """Rows stand for this test process; end any left running without a reap."""
    yield
    for row in db.list_running_agent_processes():
        db.mark_agent_process_ended(
            row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="test teardown",
        )
    db.close_connection()


def _row(agent, *, command: str = "npx vite --port 5199"):
    now = datetime.now(timezone.utc)
    return db.create_agent_process(
        process_id=f"{os.urandom(4).hex()}-feed-0000-0000-000000000000", agent_id=agent.id, command=command,
        cwd="/projects/site", run_tag=os.urandom(8).hex(), pid=os.getpid(),
        pid_create_time=psutil.Process().create_time(), log_path="/nonexistent/log",
        started_at=now - timedelta(hours=3), checkin_due_at=now - timedelta(seconds=1),
    )


def _asked(agent):
    row = _row(agent)
    assert [item.id for item in background_processes.sweep().checkins] == [row.id]
    return row


class _Sink:
    def __init__(self) -> None:
        self.activities: list[tuple[str, str, dict | None]] = []

    async def broadcast_activity(self, event, detail, agent_name=None, extra=None) -> None:
        self.activities.append((event, detail, extra))


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered = False
        self.edits: list[str] = []

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str) -> None:
        self.edits.append(text)


async def _press(data: str, *, user_id: int = _ALLOWED) -> tuple[_Query, _Sink]:
    query, sink = _Query(data), _Sink()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=user_id), callback_query=query)
    context = SimpleNamespace(bot_data={"broadcast_manager": sink})
    await handle_process_checkin_callback(update, context)
    return query, sink


# ── the card ───────────────────────────────────────────────────────────


def test_the_card_names_the_agent_shows_the_command_and_offers_keep_and_stop() -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _row(agent)

    text, keyboard = format_checkin_card(row, "Charles")

    assert text == "*Charles's background process is still running*\n`npx vite \\-\\-port 5199`"
    ((keep, stop),) = keyboard.inline_keyboard
    assert (keep.text, keep.callback_data) == ("Keep running", f"pkeep:{row.id}")
    assert (stop.text, stop.callback_data) == ("Stop", f"pstop:{row.id}")


# ── the buttons ────────────────────────────────────────────────────────


async def test_keep_rearms_the_checkin_and_announces() -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _asked(agent)

    query, sink = await _press(f"pkeep:{row.id}")

    assert query.answered and query.edits == ["Kept running: npx vite --port 5199"]
    kept = db.get_agent_process(row.id)
    assert kept is not None and kept.status == "running" and kept.checkin_requested_at is None
    assert sink.activities == [(
        "agent_process_changed", "Kept a background process running: npx vite --port 5199",
        {"agent_id": agent.id, "process_id": row.id},
    )]


async def test_stop_stops_it_as_the_operator_and_announces() -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _asked(agent)

    query, sink = await _press(f"pstop:{row.id}")

    assert query.edits == ["Stopped: npx vite --port 5199"]
    stopped = db.get_agent_process(row.id)
    assert stopped is not None and stopped.status == "stopped"
    assert (stopped.stopped_by, stopped.stop_reason) == ("operator", "Stopped by you")
    assert [event for event, _detail, _extra in sink.activities] == ["agent_process_changed"]


@pytest.mark.parametrize("action", ["pkeep", "pstop"])
async def test_a_process_that_already_ended_says_so_and_announces_nothing(action: str) -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _asked(agent)
    db.mark_agent_process_ended(
        row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="gone",
    )

    query, sink = await _press(f"{action}:{row.id}")

    assert query.edits == ["This process is no longer running."]
    assert sink.activities == []


async def test_a_user_off_the_allowlist_changes_nothing() -> None:
    agent = db.create_agent("Charles", role="Eng")
    row = _asked(agent)

    query, sink = await _press(f"pstop:{row.id}", user_id=999)

    assert not query.answered and query.edits == [] and sink.activities == []
    assert db.get_agent_process(row.id).status == "running"


# ── the bridge ─────────────────────────────────────────────────────────


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)


def _checkin_event(row) -> dict:
    # The runtime sink's activity payload: extra nested under "extra".
    return {
        "event": "agent_process_checkin", "detail": f"A background process is still running: {row.command}",
        "agent_name": "Charles", "extra": {"agent_id": row.agent_id, "process_id": row.id},
    }


async def test_the_bridge_sends_the_card_only_for_a_live_asked_process() -> None:
    agent = db.create_agent("Charles", role="Eng")
    upsert_session(_ALLOWED, session_type="dm", target_agent_id=agent.id, agent_names_key="charles")
    bot = _Bot()
    bridge = TelegramEventBridge(bot)
    not_asked = _row(agent)
    db.execute(
        "UPDATE agent_processes SET checkin_due_at = $1 WHERE id = $2",
        [datetime.now(timezone.utc) + timedelta(hours=2), not_asked.id],
    )
    asked = _asked(agent)

    await bridge.dispatch("activity", _checkin_event(not_asked))
    await bridge.dispatch("activity", _checkin_event(asked))
    db.mark_agent_process_ended(
        asked.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="gone",
    )
    await bridge.dispatch("activity", _checkin_event(asked))

    assert len(bot.sent) == 1
    (sent,) = bot.sent
    assert sent["chat_id"] == _ALLOWED and sent["parse_mode"] == "MarkdownV2"
    assert sent["text"].startswith("*Charles's background process is still running*")
    assert sent["reply_markup"].inline_keyboard[0][0].callback_data == f"pkeep:{asked.id}"
