"""The no-retry command list (``cli_no_retry_commands``): parsing, matching, loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.retry_policy import (
    SETTING_KEY,
    NoRetryListError,
    blocks_retry,
    load_no_retry_list,
    parse_no_retry_list,
)


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


def test_parse_skips_blank_lines_and_trims_and_lowercases() -> None:
    raw = "\n  Mail   Send  \n\n\t\nmail reply\n   \nGIT push\n"
    assert parse_no_retry_list(raw) == (("mail", "send"), ("mail", "reply"), ("git", "push"))


def test_parse_of_only_blank_lines_is_empty() -> None:
    assert parse_no_retry_list("\n  \n\t\n") == ()


def test_parse_keeps_crlf_entries_clean() -> None:
    assert parse_no_retry_list("mail send\r\nmail reply\r\n") == (("mail", "send"), ("mail", "reply"))


@pytest.mark.parametrize("command", [
    "mail send a@x.com --subject s",
    "mail send",
    "MAIL Send A@x.com --subject s",
    "mail reply m4bd23eff --all",
])
def test_a_listed_prefix_matches(command: str) -> None:
    entries = parse_no_retry_list("mail send\nmail reply")
    assert blocks_retry(parse_cli_command(command), entries) is True


@pytest.mark.parametrize("command", [
    "mail read m4bd23eff",
    "mailx send a@x.com",
    "mail sender a@x.com",
    "mail",
    "status",
])
def test_an_unlisted_command_does_not_match(command: str) -> None:
    entries = parse_no_retry_list("mail send\nmail reply")
    assert blocks_retry(parse_cli_command(command), entries) is False


def test_an_entry_longer_than_the_command_does_not_match() -> None:
    entries = parse_no_retry_list("mail send now")
    assert blocks_retry(parse_cli_command("mail send"), entries) is False
    assert blocks_retry(parse_cli_command("mail send now please"), entries) is True


def test_an_empty_list_matches_nothing() -> None:
    assert blocks_retry(parse_cli_command("mail send a@x.com"), ()) is False


def test_the_seeded_list_names_mail_send_and_mail_reply() -> None:
    assert load_no_retry_list() == (("mail", "send"), ("mail", "reply"))
    row = next(item for item in db.get_settings() if item.key == SETTING_KEY)
    assert row.category == "cli_policy"


def test_an_operator_edit_is_read_live() -> None:
    db.set_setting(SETTING_KEY, "status\nGit Push", "cli_policy")
    assert load_no_retry_list() == (("status",), ("git", "push"))


def test_a_missing_row_raises() -> None:
    db.execute("DELETE FROM settings WHERE key = $1", [SETTING_KEY])
    config.reload()
    with pytest.raises(NoRetryListError, match="cli_no_retry_commands is missing"):
        load_no_retry_list()


@pytest.mark.parametrize("value", ["", "  \n\t\n"])
def test_a_blank_value_lists_nothing(value: str) -> None:
    db.set_setting(SETTING_KEY, value, "cli_policy")
    assert load_no_retry_list() == ()


def test_seed_defaults_keeps_an_operator_edit() -> None:
    from db.settings import seed_defaults

    db.set_setting(SETTING_KEY, "git push", "cli_policy")
    seed_defaults()
    assert load_no_retry_list() == (("git", "push"),)
