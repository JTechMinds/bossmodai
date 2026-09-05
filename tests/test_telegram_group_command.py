"""HA-CORR-P1-04 — Telegram /group is a channel, not a room meeting."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import db
from core import config
from integrations.telegram.bot import (
    TELEGRAM_START_COMMAND_HELP,
    cmd_group,
    cmd_meeting,
    cmd_start,
)
from integrations.telegram.sessions import get_session

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


def teardown_function() -> None:
    db.close_connection()


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


def _authorized_update(user_id: int = 111) -> SimpleNamespace:
    db.set_setting("telegram_allowed_user_ids", str(user_id), "telegram")
    config.reload()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=_FakeMessage(),
    )


def test_start_help_describes_group_channel_not_spatial_meeting() -> None:
    help_text = TELEGRAM_START_COMMAND_HELP
    assert "/group" in help_text
    assert "group channel" in help_text.lower()
    assert "not a spatial office meeting" in help_text.lower()
    assert "legacy alias" in help_text.lower()
    assert "spatial office meeting" in help_text.lower()


def test_settings_telegram_copy_matches_group_channel() -> None:
    settings_js = (_REPO_ROOT / "ui/static/js/settings-view.js").read_text(encoding="utf-8")
    assert "/group" in settings_js
    assert "not a spatial office meeting" in settings_js
    assert "Legacy alias for /group" in settings_js


def test_create_application_registers_group_and_meeting_alias() -> None:
    source = (_REPO_ROOT / "integrations/telegram/bot.py").read_text(encoding="utf-8")
    assert 'CommandHandler("group", cmd_group)' in source
    assert 'CommandHandler("meeting", cmd_meeting)' in source


async def test_cmd_group_opens_channel_not_meeting_session() -> None:
    ada = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    bob = db.create_agent("Bob", role="Eng", desk_x=2, desk_y=1)
    update = _authorized_update()

    await cmd_group(update, SimpleNamespace())

    channels = db.list_channels()
    assert len(channels) == 1
    channel = channels[0]
    assert channel.name.startswith("Telegram:")
    member_ids = {row["id"] for row in db.list_channel_member_details(channel.id)}
    assert member_ids == {ada.id, bob.id}

    session = get_session(111)
    assert session is not None
    assert session.session_type == "group"
    assert session.target_channel_id == channel.id

    meeting_rows = db.query("SELECT id FROM meeting_sessions")
    assert meeting_rows == []
    assert "Group chat:" in update.message.replies[-1]


async def test_cmd_meeting_is_legacy_alias_for_group_channel() -> None:
    db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    update = _authorized_update()

    await cmd_meeting(update, SimpleNamespace())

    channels = db.list_channels()
    assert len(channels) == 1
    meeting_rows = db.query("SELECT id FROM meeting_sessions")
    assert meeting_rows == []
    session = get_session(111)
    assert session is not None
    assert session.session_type == "group"


async def test_cmd_start_help_matches_constant() -> None:
    update = _authorized_update()
    await cmd_start(update, SimpleNamespace())
    assert update.message.replies
    assert TELEGRAM_START_COMMAND_HELP in update.message.replies[0]
