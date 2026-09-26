"""The ``pref`` CLI command, driven through the real parser and runtime."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.standing_prefs import (
    ID_MAX_CHARS,
    PREF_KINDS,
    SOURCE_MAX_CHARS,
    SOURCES_MAX,
    STORE_TEXT_BYTE_CAP,
    TEXT_MAX_CHARS,
    read_standing_prefs,
)
from core.bm_cli import filesystem
from core.bm_cli.command_registry import PREF_FORMS
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.runtime import execute_bm_cli


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
    monkeypatch.setattr(filesystem, "_SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(tmp_path / "company"))
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    yield
    db.close_connection()


@pytest.fixture
def ada():
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _run(ada, command: str, content: str | None = None):
    agent, state = ada
    return execute_bm_cli(agent, state, command, content=content)


def _assert_lists_forms(result) -> None:
    assert result.ok is False
    for form in PREF_FORMS:
        assert form in result.prompt_content


def test_set_with_body_and_a_quoted_source_with_spaces(ada) -> None:
    result = _run(
        ada,
        'pref set uv-envs tool_bias operator-2026-09-22 "operator chat 9/22"',
        content="  Use uv for Python envs and installs, not pip.  \n",
    )
    assert result.ok is True
    assert result.kind == "pref"
    assert (
        "tool_bias uv-envs — Use uv for Python envs and installs, not pip. "
        "sources: operator-2026-09-22, operator chat 9/22"
    ) in result.prompt_content
    prefs = read_standing_prefs(ada[0].storage_key)
    assert [(p.id, p.kind, p.text, p.sources) for p in prefs] == [
        ("uv-envs", "tool_bias", "Use uv for Python envs and installs, not pip.", ["operator-2026-09-22", "operator chat 9/22"])
    ]


def test_set_reusing_the_id_replaces_and_list_shows_every_pref_in_full(ada) -> None:
    long_text = "w" * TEXT_MAX_CHARS
    assert _run(ada, "pref set tone style operator", content="Short sentences.").ok
    assert _run(ada, "pref set long preference operator", content=long_text).ok
    assert _run(ada, "pref set tone style thread-9", content="Plain words.").ok
    listed = _run(ada, "pref list")
    assert listed.ok is True
    assert listed.kind == "pref"
    assert "STANDING PREFS:" in listed.prompt_content
    assert "style tone — Plain words. sources: thread-9" in listed.prompt_content
    # Full text, not the clipped warm line.
    assert f"preference long — {long_text} sources: operator" in listed.prompt_content
    assert [item["id"] for item in listed.data["prefs"]] == ["tone", "long"]


def test_remove_then_list_says_no_standing_prefs(ada) -> None:
    assert _run(ada, "pref set tone style operator", content="Short sentences.").ok
    removed = _run(ada, "pref remove tone")
    assert removed.ok is True
    assert "removed tone" in removed.prompt_content
    listed = _run(ada, "pref list")
    assert listed.ok is True
    assert "no standing prefs" in listed.prompt_content


def test_remove_of_a_missing_id_is_the_store_error(ada) -> None:
    result = _run(ada, "pref remove ghost")
    assert result.ok is False
    assert result.data == {"error": 'no pref with id "ghost"'}


def test_store_validation_sentence_reaches_the_agent_unchanged(ada) -> None:
    too_long = _run(ada, "pref set tone style operator", content="x" * 212)
    assert too_long.ok is False
    assert too_long.data == {"error": f"pref text is 212 characters; the limit is {TEXT_MAX_CHARS} on one line"}
    bad_kind = _run(ada, "pref set tone rule operator", content="Short sentences.")
    assert bad_kind.data == {"error": 'kind "rule" is not one of: preference, constraint, style, tool_bias'}
    too_many = _run(ada, "pref set tone style a b c d e", content="Short sentences.")
    assert too_many.data == {"error": f"pref needs 1 to {SOURCES_MAX} sources; got 5"}
    multi_line = _run(ada, "pref set tone style operator", content="one\ntwo")
    assert multi_line.data == {
        "error": f"pref text has a line break; the limit is one line up to {TEXT_MAX_CHARS} characters"
    }
    assert read_standing_prefs(ada[0].storage_key) == []


@pytest.mark.parametrize(
    ("command", "content", "reason"),
    [
        ("pref set tone style operator", None, "pref set needs the rule as one line in the body."),
        ("pref set tone style operator", "   ", "pref set needs the rule as one line in the body."),
        ("pref set tone style", "Short sentences.", "pref set needs <id> <kind> and at least one <source> after <kind>."),
        ("pref", None, '"pref" needs a subcommand: set, remove, or list.'),
        ("pref add tone", None, 'Unknown pref subcommand "add".'),
        ("pref remove", None, "pref remove needs exactly one <id>."),
        ("pref remove tone", "body", "pref remove takes no body."),
        ("pref list everything", None, "pref list takes no arguments."),
    ],
)
def test_malformed_calls_explain_and_list_the_forms(ada, command: str, content: str | None, reason: str) -> None:
    result = _run(ada, command, content=content)
    _assert_lists_forms(result)
    assert result.data["error"].startswith(reason)


def test_learn_pref_shows_the_limits_from_the_constants(ada) -> None:
    result = _run(ada, "learn pref")
    assert result.ok is True
    text = result.prompt_content
    assert "Usage:     pref <set|remove|list> [args]" in text
    for form in PREF_FORMS:
        assert form in text
    assert f"Kinds: {', '.join(PREF_KINDS)}" in text
    assert f"1 to {ID_MAX_CHARS} letters" in text
    assert f"one line, up to {TEXT_MAX_CHARS} characters" in text
    assert f"1 to {SOURCES_MAX}, each up to {SOURCE_MAX_CHARS} characters" in text
    assert f"up to {STORE_TEXT_BYTE_CAP} bytes of text" in text


def test_write_help_no_longer_mentions_standing_prefs(ada) -> None:
    result = _run(ada, "learn write")
    assert result.ok is True
    assert "standing_prefs" not in result.prompt_content
    assert "Standing prefs" not in result.prompt_content


def test_writing_me_standing_prefs_json_is_an_ordinary_file_the_system_ignores(ada) -> None:
    raw = '{"schema_version": 1, "prefs": [{"id": "tone", "kind": "style", "text": "x", "sources": ["o"]}]}'
    written = _run(ada, "write /me/standing_prefs.json", content=raw)
    assert written.ok is True
    assert written.kind == "write"
    on_disk = agent_artifact_dir(ada[0].storage_key) / "standing_prefs.json"
    assert on_disk.read_text(encoding="utf-8") == raw + "\n"
    appended = _run(ada, "append /me/standing_prefs.json", content="more")
    assert appended.ok is True
    assert read_standing_prefs(ada[0].storage_key) == []
