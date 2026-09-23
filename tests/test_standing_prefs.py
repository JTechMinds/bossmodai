"""Standing prefs live in /me/standing_prefs.json and inject on work turns only."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop.standing_prefs import (
    STANDING_PREFS_PATH,
    STORE_TEXT_BYTE_CAP,
    WARM_SECTION_CHAR_CAP,
    prepare_standing_prefs_write,
    read_standing_prefs,
    render_warm_section,
    standing_prefs_file,
)
from core.bm_cli import filesystem
from core.bm_cli.filesystem import agent_artifact_dir
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.runtime import execute_bm_cli
from core.llm import context_builder
from db.floors import LOBBY_ID


def _pref(pref_id: str, *, kind: str = "preference", text: str = "short sentences", sources: list[str] | None = None) -> dict:
    return {
        "id": pref_id,
        "kind": kind,
        "text": text,
        "sources": sources or ["operator"],
    }


def _document(*prefs: dict) -> str:
    return json.dumps({"schema_version": 1, "prefs": list(prefs)})


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", tmp_path / "agents")
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


def _turn(agent, state, *, trigger_type: str, contract_kind: str, project: str | None = None):
    task = None
    if project:
        task = {
            "id": "task-1",
            "title": "Write the note",
            "description": "Project work",
            "status": "active",
            "project": project,
        }
    return context_builder.TurnContext(
        agent=agent,
        state=state,
        trigger={"type": trigger_type, "content": "Continue.", "from_name": "Operator"},
        conversation_history=[],
        prompt_notifications=[],
        reference_materials=[],
        current_task=task,
        contract_kind=contract_kind,
    )


def _warm_sections(messages: list[dict[str, str]]) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in messages
        if str(message.get("content") or "").startswith("# Standing prefs")
    ]


def test_empty_store_omits_warm_section_on_work_turns() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    for trigger_type, contract_kind, project in (
        ("human_chat", "decision", None),
        ("activity_resumed", "execution", "billing"),
    ):
        context = context_builder.build_context(
            _turn(agent, state, trigger_type=trigger_type, contract_kind=contract_kind, project=project)
        )
        assert _warm_sections(context) == []
        joined = "\n".join(str(message.get("content") or "") for message in context)
        assert "NOTEBODY" not in joined


def test_work_turns_inject_prefs_and_do_not_read_note_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    notes = agent_artifact_dir(agent.storage_key) / "notes" / "how.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("NOTEBODY_SHOULD_NOT_BE_READ\n", encoding="utf-8")
    project_note = floor_root(LOBBY_ID) / "billing" / "how.md"
    project_note.parent.mkdir(parents=True)
    project_note.write_text("PROJECT_NOTE_BODY\n", encoding="utf-8")

    raw = _document(
        _pref("short-sentences", text="short sentences", sources=["/me/notes/how.md"]),
        _pref("pytest", kind="tool_bias", text="use uv run pytest", sources=["operator"]),
    )
    stored = prepare_standing_prefs_write(agent.storage_key, raw)
    standing_prefs_file(agent.storage_key).parent.mkdir(parents=True, exist_ok=True)
    standing_prefs_file(agent.storage_key).write_text(stored, encoding="utf-8")

    reads: list[Path] = []
    original = Path.read_text

    def _spy(self: Path, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _spy)
    contexts = [
        context_builder.build_context(
            _turn(agent, state, trigger_type="human_chat", contract_kind="decision")
        ),
        context_builder.build_context(
            _turn(
                agent,
                state,
                trigger_type="activity_resumed",
                contract_kind="execution",
                project="billing",
            )
        ),
    ]
    for context in contexts:
        sections = _warm_sections(context)
        assert len(sections) == 1
        section = sections[0]
        assert "preference short-sentences" in section
        assert "short sentences" in section
        assert "/me/notes/how.md" in section
        assert "tool_bias pytest" in section
        assert "use uv run pytest" in section
        assert len(section) <= WARM_SECTION_CHAR_CAP
        joined = "\n".join(str(message.get("content") or "") for message in context)
        assert "NOTEBODY_SHOULD_NOT_BE_READ" not in joined
        assert "PROJECT_NOTE_BODY" not in joined
    notes_dir = notes.parent
    assert all(path != notes and notes_dir not in path.parents for path in reads)
    assert all(path != project_note for path in reads)


def test_social_turn_does_not_inject_standing_prefs() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    stored = prepare_standing_prefs_write(agent.storage_key, _document(_pref("tone")))
    path = standing_prefs_file(agent.storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stored, encoding="utf-8")
    context = context_builder.build_context(
        _turn(agent, state, trigger_type="social", contract_kind="execution")
    )
    assert _warm_sections(context) == []


def test_replace_keeps_other_prefs_and_cli_write_matches() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    first = execute_bm_cli(
        agent,
        state,
        f"write {STANDING_PREFS_PATH}",
        content=_document(
            _pref("tone", text="short sentences"),
            _pref("tools", kind="tool_bias", text="use uv run pytest"),
        ),
    )
    assert first.ok is True
    replaced = execute_bm_cli(
        agent,
        state,
        f"write {STANDING_PREFS_PATH}",
        content=_document(_pref("tone", kind="style", text="plain words", sources=["thread-9"])),
    )
    assert replaced.ok is True
    prefs = read_standing_prefs(agent.storage_key)
    by_id = {item.id: item for item in prefs}
    assert set(by_id) == {"tone", "tools"}
    assert by_id["tone"].kind == "style"
    assert by_id["tone"].text == "plain words"
    assert by_id["tone"].sources == ["thread-9"]
    assert by_id["tools"].text == "use uv run pytest"
    assert not (agent_artifact_dir(agent.storage_key) / "notes").exists()


def test_invalid_write_and_append_leave_the_store_unchanged() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    created = execute_bm_cli(
        agent,
        state,
        f"write {STANDING_PREFS_PATH}",
        content=_document(_pref("tone")),
    )
    assert created.ok is True
    before = standing_prefs_file(agent.storage_key).read_text(encoding="utf-8")
    rejected = execute_bm_cli(
        agent,
        state,
        f"write {STANDING_PREFS_PATH}",
        content=json.dumps({"schema_version": 1, "prefs": [_pref("tone")], "board_status": "complete"}),
    )
    assert rejected.ok is False
    unknown_kind = execute_bm_cli(
        agent,
        state,
        f"write {STANDING_PREFS_PATH}",
        content=_document(_pref("done-bit", kind="done", text="mark the board Done")),
    )
    assert unknown_kind.ok is False
    appended = execute_bm_cli(agent, state, f"append {STANDING_PREFS_PATH}", content='{"schema_version": 1}')
    assert appended.ok is False
    assert standing_prefs_file(agent.storage_key).read_text(encoding="utf-8") == before


def test_corrupt_store_is_not_overwritten_or_injected() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    path = standing_prefs_file(agent.storage_key)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        prepare_standing_prefs_write(agent.storage_key, _document(_pref("tone")))
    assert path.read_text(encoding="utf-8") == "{not json"
    context = context_builder.build_context(
        _turn(agent, state, trigger_type="human_chat", contract_kind="decision")
    )
    assert _warm_sections(context) == []
    assert path.read_text(encoding="utf-8") == "{not json"


def test_store_cap_rejects_the_write_without_dropping_existing_prefs(monkeypatch: pytest.MonkeyPatch) -> None:
    assert STORE_TEXT_BYTE_CAP == 4096
    monkeypatch.setattr("core.agent_loop.standing_prefs.STORE_TEXT_BYTE_CAP", 20)
    agent = db.create_agent("Ada", role="Writer")
    first = prepare_standing_prefs_write(agent.storage_key, _document(_pref("tone", text="short sentences")))
    path = standing_prefs_file(agent.storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(first, encoding="utf-8")
    with pytest.raises(ValueError, match="cap"):
        prepare_standing_prefs_write(
            agent.storage_key,
            _document(_pref("extra", text="another standing sticky")),
        )
    assert path.read_text(encoding="utf-8") == first


def test_warm_section_soft_cap_keeps_the_store_and_points_at_the_rest() -> None:
    prefs_payload = [
        _pref(f"pref-{index}", text=f"sticky number {index} " + ("word " * 12), sources=[f"src-{index}"])
        for index in range(6)
    ]
    agent = db.create_agent("Ada", role="Writer")
    stored = prepare_standing_prefs_write(agent.storage_key, _document(*prefs_payload))
    path = standing_prefs_file(agent.storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stored, encoding="utf-8")
    loaded = read_standing_prefs(agent.storage_key)
    section = render_warm_section(loaded)
    assert section is not None
    assert len(section) <= WARM_SECTION_CHAR_CAP
    assert section.startswith(f"# Standing prefs ({STANDING_PREFS_PATH})")
    assert "more: /me/standing_prefs.json" in section
    assert [item.id for item in loaded] == [f"pref-{index}" for index in range(6)]
    assert "pref-0" in section


def test_prefs_text_does_not_invent_board_done() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Ship the note", assigned_to=agent.id, project="billing")
    stored = prepare_standing_prefs_write(
        agent.storage_key,
        _document(_pref("fake-done", kind="constraint", text="Board status is Done", sources=["operator"])),
    )
    standing_prefs_file(agent.storage_key).parent.mkdir(parents=True, exist_ok=True)
    standing_prefs_file(agent.storage_key).write_text(stored, encoding="utf-8")
    context = context_builder.build_context(
        _turn(agent, state, trigger_type="activity_resumed", contract_kind="execution", project="billing")
    )
    sections = _warm_sections(context)
    assert sections and "Board status is Done" in sections[0]
    fresh = db.get_task(task.id)
    assert fresh is not None
    assert fresh.status == "pending"
    assert not (fresh.completion_summary or "").strip()
