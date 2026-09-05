"""SEC-P0-01 — Telegram allowlist is fail-closed."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import db
from core import config
from integrations.telegram.auth import (
    is_telegram_user_allowed,
    parse_allowed_user_ids,
    telegram_start_block_reason,
)
from integrations.telegram.bot import _check_auth


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


def _update(user_id: int | None) -> SimpleNamespace:
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(effective_user=user)


def test_parse_allowed_user_ids_skips_invalid_tokens() -> None:
    assert parse_allowed_user_ids("") == set()
    assert parse_allowed_user_ids("  ") == set()
    assert parse_allowed_user_ids("123, 456") == {123, 456}
    assert parse_allowed_user_ids("123,not-a-number,789") == {123, 789}


def test_empty_allowlist_denies_all_users() -> None:
    db.set_setting("telegram_allowed_user_ids", "", "telegram")
    config.reload()

    assert is_telegram_user_allowed(111) is False
    assert _check_auth(_update(111)) is False
    assert _check_auth(_update(None)) is False


def test_listed_user_is_allowed_and_others_are_not() -> None:
    db.set_setting("telegram_allowed_user_ids", "111, 222", "telegram")
    config.reload()

    assert is_telegram_user_allowed(111) is True
    assert is_telegram_user_allowed(222) is True
    assert is_telegram_user_allowed(999) is False
    assert _check_auth(_update(111)) is True
    assert _check_auth(_update(999)) is False


def test_telegram_start_refuses_empty_allowlist_when_enabled() -> None:
    db.set_setting("telegram_enabled", "true", "telegram")
    db.set_setting("telegram_bot_token", "123456:TESTTOKEN", "telegram")
    db.set_setting("telegram_allowed_user_ids", "", "telegram")
    config.reload()

    reason = telegram_start_block_reason()
    assert reason is not None
    assert "empty" in reason.lower() or "fail-closed" in reason.lower()


def test_telegram_start_allows_configured_allowlist() -> None:
    db.set_setting("telegram_enabled", "true", "telegram")
    db.set_setting("telegram_bot_token", "123456:TESTTOKEN", "telegram")
    db.set_setting("telegram_allowed_user_ids", "111", "telegram")
    config.reload()

    assert telegram_start_block_reason() is None
