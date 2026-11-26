"""The Telegram event bridge forwards conversation and action cards only.

Agent speech reaches the users with that DM or thread open. A CLI approval
card and a process check-in card each go once to every allowlisted user.
Notices (receipts, task, memory and queue lines), round markers, activity
events and feed updates stay in the app.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest

import db
from core import config
from core.bm_cli import background_processes
from core.models.cli_policy import CLI_APPROVAL_KIND
from db.floors import LOBBY_ID
from integrations.telegram.bridge import TelegramEventBridge
from integrations.telegram.formatters import format_agent_reply
from integrations.telegram.sessions import set_floor, upsert_session


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    _allow(111, 222)


@pytest.fixture(autouse=True)
def _end_rows() -> Iterator[None]:
    """Process rows stand for this test process; end any left running without a reap."""
    yield
    for row in db.list_running_agent_processes():
        db.mark_agent_process_ended(
            row.id, status="exited", ended_at=datetime.now(timezone.utc), stopped_by=None, stop_reason="test teardown",
        )
    db.close_connection()


def _allow(*user_ids: int) -> None:
    db.set_setting("telegram_allowed_user_ids", ", ".join(str(user_id) for user_id in user_ids), "telegram")
    config.reload()


class _Bot:
    """Records every send. ``fail_for`` chat ids raise, as Telegram does for a user who never sent /start."""

    def __init__(self, *, fail_for: frozenset[int] = frozenset()) -> None:
        self.sent: list[dict] = []
        self._fail_for = fail_for

    async def send_message(self, **kwargs) -> None:
        if kwargs["chat_id"] in self._fail_for:
            raise RuntimeError("Forbidden: bot can't initiate conversation with a user")
        self.sent.append(kwargs)


def _bridge(bot: _Bot | None = None) -> tuple[TelegramEventBridge, _Bot]:
    bot = bot or _Bot()
    return TelegramEventBridge(bot), bot


def _dm(user_id: int, agent) -> None:
    upsert_session(
        user_id, session_type="dm", floor_id=LOBBY_ID, target_agent_id=agent.id, agent_names_key=agent.name.lower(),
    )


def _in_thread(user_id: int, channel) -> None:
    upsert_session(user_id, session_type="group", floor_id=LOBBY_ID, target_channel_id=channel.id)


def _chat_line(agent, content: str, *, from_type: str = "agent", kind: str | None = None, card=None) -> dict:
    # The shape RuntimeServices._dispatch_event hands the bridge for a chat_message.
    return {
        "agent_id": agent.id, "content": content, "from_type": from_type, "from_name": agent.name,
        "notification_kind": kind, "cli_approval": card,
    }


def _thread_line(channel, author, content: str, *, author_type: str = "agent", kind: str | None = None, card=None) -> dict:
    return {
        "channel_id": channel.id, "content": content, "author_type": author_type, "author_name": author.name,
        "author_agent_id": author.id, "notification_kind": kind, "cli_approval": card,
    }


def _chat_ids(bot: _Bot) -> list[int]:
    return [sent["chat_id"] for sent in bot.sent]


# ── conversation ───────────────────────────────────────────────────────


async def test_an_agent_dm_reply_reaches_only_the_dm_holder() -> None:
    ada = db.create_agent("Ada", role="Eng")
    bob = db.create_agent("Bob", role="Eng")
    _dm(111, ada)
    _dm(222, bob)
    set_floor(333, LOBBY_ID)
    bridge, bot = _bridge()

    await bridge.dispatch("chat_message", _chat_line(ada, "Done with the report."))

    assert bot.sent == [{"chat_id": 111, "text": format_agent_reply("Ada", "Done with the report."), "parse_mode": "MarkdownV2"}]


@pytest.mark.parametrize(
    ("from_type", "kind"),
    [
        ("system", "receipt"),
        ("system", "task_update"),
        ("system", "completion"),
        ("system", "memory"),
        ("system", "queue_visibility"),
        ("human", None),
    ],
)
async def test_a_dm_line_the_agent_did_not_say_reaches_nobody(from_type: str, kind: str | None) -> None:
    ada = db.create_agent("Ada", role="Eng")
    _dm(111, ada)
    bridge, bot = _bridge()

    await bridge.dispatch("chat_message", _chat_line(ada, "Ada is heading to the desk.", from_type=from_type, kind=kind))

    assert bot.sent == []


async def test_agent_thread_lines_reach_only_that_threads_holders() -> None:
    ada = db.create_agent("Ada", role="Eng")
    bob = db.create_agent("Bob", role="Eng")
    books = db.create_channel(name="Books", member_agent_ids=[ada.id, bob.id], created_by=ada.id)
    other = db.create_channel(name="Other", member_agent_ids=[bob.id], created_by=bob.id)
    _in_thread(111, books)
    _in_thread(222, other)
    _dm(333, ada)
    bridge, bot = _bridge()

    await bridge.dispatch("channel_message", _thread_line(books, ada, "Ledger balanced."))

    assert bot.sent == [{"chat_id": 111, "text": format_agent_reply("Ada", "Ledger balanced."), "parse_mode": "MarkdownV2"}]


@pytest.mark.parametrize(
    ("author_type", "kind", "content"),
    [
        ("system", "channel_round_marker", "Round 2"),
        ("system", "task_update", "Ada started Ledger."),
        ("system", None, "Ada paused the thread."),
        ("human", None, "Looks good."),
    ],
)
async def test_a_thread_line_no_agent_said_reaches_nobody(author_type: str, kind: str | None, content: str) -> None:
    ada = db.create_agent("Ada", role="Eng")
    books = db.create_channel(name="Books", member_agent_ids=[ada.id], created_by=ada.id)
    _in_thread(111, books)
    bridge, bot = _bridge()

    await bridge.dispatch("channel_message", _thread_line(books, ada, content, author_type=author_type, kind=kind))

    assert bot.sent == []


async def test_activity_and_feed_updates_reach_nobody() -> None:
    ada = db.create_agent("Ada", role="Eng")
    _dm(111, ada)
    bridge, bot = _bridge()

    for event in ("task_created", "task_stalled"):
        await bridge.dispatch("activity", {"event": event, "detail": f"{event} for Ledger", "agent_name": "Ada"})
    await bridge.dispatch(
        "feed_update",
        {"entry": {"category": "task", "notification_kind": "completion", "content": "Ledger done", "agent_name": "Ada"}},
    )

    assert bot.sent == []


# ── approval cards ─────────────────────────────────────────────────────


def _approval_notice(request, *, channel=None) -> tuple[str, dict]:
    """The approval's notice as the bridge receives it: a system line carrying the card."""
    agent = db.get_agent(request.agent_id)
    card = request.as_card()
    if channel is not None:
        return "channel_message", _thread_line(
            channel, agent, "Approve this command?", author_type="system", kind=CLI_APPROVAL_KIND, card=card,
        )
    return "chat_message", _chat_line(agent, "Approve this command?", from_type="system", kind=CLI_APPROVAL_KIND, card=card)


def _approval_buttons(sent: dict) -> list[str]:
    return [button.callback_data for button in sent["reply_markup"].inline_keyboard[0]]


async def test_an_approval_card_is_sent_once_when_its_notice_arrives_twice() -> None:
    ada = db.create_agent("Ada", role="Eng")
    request = db.create_cli_approval_request(agent_id=ada.id, command="rm -rf build")
    bridge, bot = _bridge()

    kind, notice = _approval_notice(request)
    await bridge.dispatch(kind, notice)
    await bridge.dispatch(kind, notice)

    assert _chat_ids(bot) == [111, 222]
    for sent in bot.sent:
        assert sent["parse_mode"] == "MarkdownV2"
        assert "rm \\-rf build" in sent["text"]
        assert _approval_buttons(sent) == [f"approve:{request.id}", f"reject:{request.id}", f"ask:{request.id}"]


async def test_an_approval_answered_before_its_notice_drains_sends_nothing() -> None:
    ada = db.create_agent("Ada", role="Eng")
    request = db.create_cli_approval_request(agent_id=ada.id, command="rm -rf build")
    kind, notice = _approval_notice(request)
    db.approve_cli_approval_request(request.id, decision_by="human")
    bridge, bot = _bridge()

    await bridge.dispatch(kind, notice)

    assert bot.sent == []


async def test_a_notice_whose_request_was_gone_sends_nothing() -> None:
    ada = db.create_agent("Ada", role="Eng")
    bridge, bot = _bridge()

    await bridge.dispatch("chat_message", _chat_line(ada, "Approve this command?", from_type="system", kind=CLI_APPROVAL_KIND))

    assert bot.sent == []


async def test_dm_and_thread_origin_approvals_each_send_their_card() -> None:
    ada = db.create_agent("Ada", role="Eng")
    books = db.create_channel(name="Books", member_agent_ids=[ada.id], created_by=ada.id)
    from_dm = db.create_cli_approval_request(agent_id=ada.id, command="make dm")
    from_thread = db.create_cli_approval_request(agent_id=ada.id, command="make thread", channel_id=books.id)
    _allow(111)
    bridge, bot = _bridge()

    await bridge.dispatch(*_approval_notice(from_dm))
    await bridge.dispatch(*_approval_notice(from_thread, channel=books))

    assert _chat_ids(bot) == [111, 111]
    assert [_approval_buttons(sent)[0] for sent in bot.sent] == [f"approve:{from_dm.id}", f"approve:{from_thread.id}"]


# ── who gets an action card ────────────────────────────────────────────


def _asked_process(agent):
    """A running process whose check-in the watchdog just asked."""
    now = datetime.now(timezone.utc)
    row = db.create_agent_process(
        process_id=f"{os.urandom(4).hex()}-feed-0000-0000-000000000000", agent_id=agent.id, command="npx vite",
        cwd="/projects/site", run_tag=os.urandom(8).hex(), pid=os.getpid(),
        pid_create_time=psutil.Process().create_time(), log_path="/nonexistent/log",
        started_at=now - timedelta(hours=3), checkin_due_at=now - timedelta(seconds=1), task_id=None,
    )
    assert [item.id for item in background_processes.sweep().checkins] == [row.id]
    return row


def _checkin_event(row) -> dict:
    # The runtime sink's activity payload: extra nested under "extra".
    return {
        "event": "agent_process_checkin", "detail": f"A background process is still running: {row.command}",
        "agent_name": "Ada", "extra": {"agent_id": row.agent_id, "process_id": row.id},
    }


async def test_action_cards_reach_every_allowlisted_user_and_nobody_else() -> None:
    ada = db.create_agent("Ada", role="Eng")
    # 111 has never opened a chat (no row), 222 closed theirs (idle row),
    # 333 has a DM open but is not on the allowlist.
    set_floor(222, LOBBY_ID)
    _dm(333, ada)
    request = db.create_cli_approval_request(agent_id=ada.id, command="rm -rf build")
    process = _asked_process(ada)
    bridge, bot = _bridge()

    await bridge.dispatch(*_approval_notice(request))
    await bridge.dispatch("activity", _checkin_event(process))

    assert _chat_ids(bot) == [111, 222, 111, 222]
    assert [_approval_buttons(sent)[0] for sent in bot.sent] == [
        f"approve:{request.id}", f"approve:{request.id}", f"pkeep:{process.id}", f"pkeep:{process.id}",
    ]


async def test_a_failed_send_is_logged_and_the_other_recipients_still_get_the_card(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ada = db.create_agent("Ada", role="Eng")
    request = db.create_cli_approval_request(agent_id=ada.id, command="rm -rf build")
    bridge, bot = _bridge(_Bot(fail_for=frozenset({111})))

    with caplog.at_level(logging.WARNING, logger="integrations.telegram.bridge"):
        await bridge.dispatch(*_approval_notice(request))

    assert _chat_ids(bot) == [222]
    failures = [record for record in caplog.records if record.name == "integrations.telegram.bridge"]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert "111" in failures[0].getMessage()
    assert "bot can't initiate conversation" in failures[0].getMessage()
