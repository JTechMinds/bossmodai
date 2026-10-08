"""The ``memory`` CLI command, driven through the real parser and runtime."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.runtime_core import MEMORY_SOFT_TARGET_CHARS
from core.agent_loop.standing_prefs import line_max_chars, list_memories, read_memories, standing_prefs_file
from core.bm_cli import filesystem
from core.bm_cli.command_registry import MEMORY_FORMS
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


def _split_chrome(result) -> tuple[dict, dict]:
    """The result's data without its ``origin_chrome``, and that chrome."""
    data = dict(result.data)
    return data, data.pop("origin_chrome")


def _assert_dm_note(chrome: dict, agent, content: str, memory_id: int | None) -> None:
    """The declared chrome is the persisted DM note: the agent's, system-authored, with its number."""
    assert chrome["agent_id"] == agent.id
    assert chrome["content"] == content
    assert chrome["from_type"] == "system"
    assert chrome["notification_kind"] == "memory"
    assert chrome["memory_id"] == memory_id
    notes = db.list_notifications(agent_id=agent.id, limit=20)
    assert any(note.id == chrome["message_id"] and note.content == content for note in notes)


def _assert_lists_forms(result) -> None:
    assert result.ok is False
    for form in MEMORY_FORMS:
        assert form in result.prompt_content


def test_add_saves_the_body_and_names_the_assigned_number(ada) -> None:
    result = _run(ada, "memory add", content="  The boss wants plain English, not jargon.  \n")
    assert result.ok is True
    assert result.kind == "memory"
    assert result.detail == "Ada saved memory #1"
    data, chrome = _split_chrome(result)
    assert data == {
        "action": "add",
        "memory": {"id": 1, "text": "The boss wants plain English, not jargon."},
    }
    _assert_dm_note(chrome, ada[0], "Ada saved a memory", 1)
    assert "The boss can see it." in result.prompt_content
    assert "MEMORY:" in result.prompt_content
    assert "1 — The boss wants plain English, not jargon." in result.prompt_content
    second = _run(ada, "memory add", content="Acme's contact is Dana Lee.")
    assert second.detail == "Ada saved memory #2"
    assert [(item.id, item.text) for item in read_memories(ada[0].storage_key)] == [
        (1, "The boss wants plain English, not jargon."),
        (2, "Acme's contact is Dana Lee."),
    ]


def test_replace_keeps_the_number_and_list_shows_every_memory_in_full(ada) -> None:
    long_text = "w" * line_max_chars()
    assert _run(ada, "memory add", content="Short sentences.").ok
    assert _run(ada, "memory add", content=long_text).ok
    replaced = _run(ada, "memory replace 1", content="Plain words.")
    assert replaced.ok is True
    assert replaced.detail == "Ada saved memory #1"
    data, chrome = _split_chrome(replaced)
    assert data == {"action": "replace", "memory": {"id": 1, "text": "Plain words."}}
    _assert_dm_note(chrome, ada[0], "Ada updated a memory", 1)
    listed = _run(ada, "memory list")
    assert listed.ok is True
    assert listed.kind == "memory"
    assert "MEMORIES:" in listed.prompt_content
    assert "1 — Plain words." in listed.prompt_content
    # Full text, not the clipped warm line.
    assert f"2 — {long_text}" in listed.prompt_content
    assert listed.data == {
        "action": "list",
        "memories": [{"id": 1, "text": "Plain words."}, {"id": 2, "text": long_text}],
    }


def test_remove_then_list_says_no_memories_saved_and_the_number_is_not_reused(ada) -> None:
    assert _run(ada, "memory add", content="Short sentences.").ok
    removed = _run(ada, "memory remove 1")
    assert removed.ok is True
    data, chrome = _split_chrome(removed)
    assert data == {"action": "remove", "memory": {"id": 1, "text": "Short sentences."}}
    _assert_dm_note(chrome, ada[0], "Ada removed a memory: “Short sentences.”", None)
    assert "The boss can see it." in removed.prompt_content
    assert "removed 1 — Short sentences." in removed.prompt_content
    listed = _run(ada, "memory list")
    assert listed.ok is True
    assert "no memories saved" in listed.prompt_content
    assert listed.data == {"action": "list", "memories": []}
    assert _run(ada, "memory add", content="Next.").detail == "Ada saved memory #2"


@pytest.mark.parametrize("command", ["memory remove 9", "memory replace 9"])
def test_a_missing_number_is_the_store_error(ada, command: str) -> None:
    result = _run(ada, command, content="New text." if "replace" in command else None)
    assert result.ok is False
    assert result.data == {"error": "no memory #9"}


def test_store_validation_sentences_reach_the_agent_unchanged(ada) -> None:
    too_long = _run(ada, "memory add", content="x" * 401)
    assert too_long.ok is False
    assert too_long.data == {
        "error": "memory is 401 characters; the limit is 400. Keep it to 1–2 short sentences."
    }
    multi_line = _run(ada, "memory add", content="one\ntwo")
    assert multi_line.data == {"error": "memory text has a line break; keep it to one line"}
    db.set_setting("standing_prefs_section_max_chars", "10", "context")
    config.reload()
    over_cap = _run(ada, "memory add", content="eleven char")
    assert over_cap.data == {
        "error": "memory would grow to 11 characters; the limit is 10. Replace or remove a memory first."
    }
    assert read_memories(ada[0].storage_key) == []


def test_a_corrupt_store_is_the_store_error_and_is_kept(ada) -> None:
    path = standing_prefs_file(ada[0].storage_key)
    path.write_text("{not json", encoding="utf-8")
    added = _run(ada, "memory add", content="Short.")
    assert added.data == {"error": "memory store is unreadable; refusing to overwrite"}
    listed = _run(ada, "memory list")
    assert listed.ok is False
    assert listed.data["error"].startswith("memory store is unreadable: ")
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    ("command", "content", "reason"),
    [
        ("memory add", None, "memory add needs the memory as one line in the body."),
        ("memory add", "   ", "memory add needs the memory as one line in the body."),
        ("memory add tone", "Short.", "memory add takes no arguments; the system assigns the number."),
        ("memory replace", "Short.", "memory replace needs exactly one <n>, the memory's number."),
        ("memory replace 1 2", "Short.", "memory replace needs exactly one <n>, the memory's number."),
        ("memory replace 1", None, "memory replace needs the new sentence as one line in the body."),
        ("memory replace tone", "Short.", 'memory number "tone" must be a positive whole number.'),
        ("memory replace 0", "Short.", 'memory number "0" must be a positive whole number.'),
        ("memory remove", None, "memory remove needs exactly one <n>, the memory's number."),
        ("memory remove -1", None, 'memory number "-1" must be a positive whole number.'),
        ("memory remove 1", "body", "memory remove takes no body."),
        ("memory list everything", None, "memory list takes no arguments."),
        ("memory list", "body", "memory list takes no body."),
        ("memory", None, '"memory" needs a subcommand: add, replace, remove, or list.'),
        ("memory set 1", None, 'Unknown memory subcommand "set".'),
    ],
)
def test_malformed_calls_explain_and_list_the_forms(ada, command: str, content: str | None, reason: str) -> None:
    result = _run(ada, command, content=content)
    _assert_lists_forms(result)
    assert result.data["error"].startswith(reason)
    assert list_memories(ada[0].storage_key) == []


def test_pref_is_no_longer_a_command(ada) -> None:
    for command in ("pref list", "pref set tone style operator"):
        result = _run(ada, command, content="Short." if "set" in command else None)
        assert result.ok is False
        assert '"pref" is not a built-in command' in result.data["error"]
    learned = _run(ada, "learn pref")
    assert learned.detail == 'Command "pref" not found'


def test_learn_memory_shows_the_soft_target_the_forms_and_no_limit_number(ada) -> None:
    result = _run(ada, "learn memory")
    assert result.ok is True
    text = result.prompt_content
    assert "Usage:     memory <add|replace|remove|list> [n]" in text
    assert "Small guidance thoughts shown to you every turn" in text
    assert f"Aim for 1–2 short\nsentences (about {MEMORY_SOFT_TARGET_CHARS} characters)." in text
    assert "The system numbers each memory." in text
    for form in MEMORY_FORMS:
        assert form in text
    assert "body = the memory, 1–2 short sentences; the number is assigned for you" in text
    # The hard limits are operator settings; the static help names where they live, not a number.
    limit_rows = [line for line in text.splitlines() if line.lstrip().startswith(("text ", "store "))]
    assert limit_rows == [
        "  text   one line; the longest is set in Settings → System → Context Window",
        "  store  total text across all memories is capped (same place)",
    ]
    assert str(line_max_chars()) not in text
    assert 'memory add         — with body: "The boss wants plain English, not jargon."' in text
    assert "memory remove 3" in text


def test_memory_is_listed_in_the_agent_category(ada) -> None:
    result = _run(ada, "categories agent")
    assert result.ok is True
    assert "memory" in result.prompt_content
    assert "pref" not in result.prompt_content.split()
