"""Telegram thread commands share the BossMod channel list.

/help aliases /start. /thread replaces /group and /meeting. /channels stamps
row numbers and /join N rejoins that row only while the list is unchanged.
A thread opened from Telegram is a normal channel the GUI list returns.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from integrations.telegram.bot import (
    TELEGRAM_START_COMMAND_HELP,
    cmd_channels,
    cmd_help,
    cmd_join,
    cmd_start,
    cmd_thread,
)
from integrations.telegram.join_list import reset_channel_list_snapshots
from integrations.telegram.sessions import clear_session, get_session

_REPO_ROOT = Path(__file__).resolve().parents[1]


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    reset_channel_list_snapshots()


def teardown_function() -> None:
    db.close_connection()
    reset_channel_list_snapshots()


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


class _Broadcast:
    def __init__(self) -> None:
        self.channel_updates: list[dict[str, object]] = []

    async def broadcast_channel_updated(self, summary: dict[str, object]) -> None:
        self.channel_updates.append(summary)


def _authorized_update(user_id: int = 111) -> SimpleNamespace:
    db.set_setting("telegram_allowed_user_ids", str(user_id), "telegram")
    config.reload()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=_FakeMessage(),
    )


def _context(*args: str, broadcast: _Broadcast | None = None) -> SimpleNamespace:
    bot_data: dict[str, object] = {}
    if broadcast is not None:
        bot_data["broadcast_manager"] = broadcast
    return SimpleNamespace(args=list(args), bot_data=bot_data)


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _stamp(channel_id: str, when: datetime) -> None:
    db.execute(
        "UPDATE channels SET updated_at = $1, created_at = $1 WHERE id = $2",
        [when, channel_id],
    )


def test_help_is_start_alias_and_prints_the_command_map() -> None:
    help_text = TELEGRAM_START_COMMAND_HELP
    for token in ("/help", "/thread", "/channels", "/join", "/agents", "/chat", "/status", "/approve"):
        assert token in help_text
    assert "/group" not in help_text
    assert "/meeting" not in help_text


async def test_cmd_help_reply_matches_cmd_start() -> None:
    start_update = _authorized_update()
    help_update = _authorized_update()
    await cmd_start(start_update, _context())
    await cmd_help(help_update, _context())
    assert start_update.message.replies == help_update.message.replies
    assert TELEGRAM_START_COMMAND_HELP in start_update.message.replies[0]


def test_settings_and_handlers_use_thread_not_group_or_meeting() -> None:
    settings_js = (_REPO_ROOT / "ui/static/js/settings/settings-telegram.js").read_text(encoding="utf-8")
    source = (_REPO_ROOT / "integrations/telegram/bot.py").read_text(encoding="utf-8")
    assert "/thread" in settings_js
    assert "/help" in settings_js
    assert "/join" in settings_js
    assert "/group" not in settings_js
    assert "/meeting" not in settings_js
    assert 'CommandHandler("help", cmd_help)' in source
    assert 'CommandHandler("thread", cmd_thread)' in source
    assert 'CommandHandler("join", cmd_join)' in source
    assert 'CommandHandler("group"' not in source
    assert 'CommandHandler("meeting"' not in source


async def test_cmd_thread_opens_channel_not_meeting_session() -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    bob = db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    update = _authorized_update()

    await cmd_thread(update, _context())

    channels = db.list_channels()
    assert len(channels) == 1
    channel = channels[0]
    assert channel.name == "Ada, Bob"
    assert not channel.name.startswith("Telegram:")
    member_ids = {row["id"] for row in db.list_channel_member_details(channel.id)}
    assert member_ids == {ada.id, bob.id}
    assert channel.cli_auto_approve is False

    session = get_session(111)
    assert session is not None
    assert session.session_type == "group"
    assert session.target_channel_id == channel.id

    meeting_rows = db.query("SELECT id FROM meeting_sessions")
    assert meeting_rows == []
    assert update.message.replies[-1] == f"Thread: {channel.name}\nType anything to message them."


async def test_cmd_thread_rejoins_existing_roster_without_a_duplicate() -> None:
    db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    update = _authorized_update()

    await cmd_thread(update, _context())
    first_id = db.list_channels()[0].id
    clear_session(111)

    await cmd_thread(update, _context())

    channels = db.list_channels()
    assert [channel.id for channel in channels] == [first_id]
    session = get_session(111)
    assert session is not None
    assert session.target_channel_id == first_id


async def test_telegram_thread_appears_on_bossmod_channel_list() -> None:
    db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    update = _authorized_update()
    broadcast = _Broadcast()

    await cmd_thread(update, _context(broadcast=broadcast))

    stored = db.list_channels()
    assert len(stored) == 1
    listed = _api_client().get("/api/channels", headers=_headers())
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["id"] for row in rows] == [stored[0].id]
    assert rows[0]["name"] == stored[0].name
    assert rows[0]["status"] == "active"
    assert [row["id"] for row in broadcast.channel_updates] == [stored[0].id]
    assert broadcast.channel_updates[0]["status"] == "active"
    assert broadcast.channel_updates[0]["member_count"] == 2


async def test_channels_stamps_ordinals_and_join_rejoins_that_row() -> None:
    ada = db.create_agent("Ada", role="Eng")
    bob = db.create_agent("Bob", role="Eng")
    cara = db.create_agent("Cara", role="Eng")
    older = db.create_channel(name="Older", member_agent_ids=[ada.id])
    newer = db.create_channel(name="Newer", member_agent_ids=[bob.id, cara.id])
    base = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    _stamp(older.id, base)
    _stamp(newer.id, base + timedelta(minutes=5))
    update = _authorized_update()

    await cmd_channels(update, _context())
    listing = update.message.replies[-1]
    assert "1\\. *Newer*" in listing
    assert "2\\. *Older*" in listing

    await cmd_join(update, _context("2"))

    session = get_session(111)
    assert session is not None
    assert session.target_channel_id == older.id
    assert len(db.list_channels()) == 2
    assert "Thread: Older" in update.message.replies[-1]


async def test_join_fails_closed_when_the_list_is_stale() -> None:
    ada = db.create_agent("Ada", role="Eng")
    bob = db.create_agent("Bob", role="Eng")
    first = db.create_channel(name="First", member_agent_ids=[ada.id])
    second = db.create_channel(name="Second", member_agent_ids=[bob.id])
    base = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    _stamp(first.id, base)
    _stamp(second.id, base + timedelta(minutes=1))
    update = _authorized_update()
    await cmd_channels(update, _context())

    intruder = db.create_channel(name="Intruder", member_agent_ids=[ada.id, bob.id])
    _stamp(intruder.id, base + timedelta(minutes=2))
    await cmd_join(update, _context("1"))

    assert get_session(111) is None
    assert "stale" in update.message.replies[-1].lower()
    assert [channel.id for channel in db.list_channels()] == [intruder.id, second.id, first.id]
    assert "Thread:" not in update.message.replies[-1]

    await cmd_channels(update, _context())
    await cmd_join(update, _context("1"))
    session = get_session(111)
    assert session is not None
    assert session.target_channel_id == intruder.id


async def test_join_fails_closed_without_a_list_or_for_an_unknown_row() -> None:
    ada = db.create_agent("Ada", role="Eng")
    only = db.create_channel(name="Only", member_agent_ids=[ada.id])
    update = _authorized_update()

    await cmd_join(update, _context("1"))
    assert get_session(111) is None
    assert "No thread list yet" in update.message.replies[-1]
    assert len(db.list_channels()) == 1

    await cmd_channels(update, _context())
    await cmd_join(update, _context("2"))
    assert get_session(111) is None
    assert "not on the list" in update.message.replies[-1]
    assert db.list_channels()[0].id == only.id

    await cmd_join(update, _context("0"))
    await cmd_join(update, _context())
    assert get_session(111) is None
    assert len(db.list_channels()) == 1
