"""Standing prefs: a system-owned typed store, injected on work turns only.

Covers the typed operations, validation sentences, atomic writes, the warm
section, isolation from every agent path, and the one-time legacy migration.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop import standing_prefs
from core.agent_loop.standing_prefs import (
    STORE_TEXT_BYTE_CAP,
    WARM_SECTION_CHAR_CAP,
    list_standing_prefs,
    migrate_workspace_standing_prefs,
    read_standing_prefs,
    remove_standing_pref,
    render_warm_section,
    set_standing_pref,
    standing_prefs_file,
)
from core.bm_cli import filesystem
from core.bm_cli.filesystem import agent_artifact_dir, standing_prefs_root
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.host_roots import allowed_workspace_roots
from core.bm_cli.virtual_fs import resolve_cli_path
from core.llm import context_builder
from db.floors import LOBBY_ID


def _set(key: str, pref_id: str, *, kind: str = "preference", text: str = "short sentences", sources: list[str] | None = None):
    return set_standing_pref(key, pref_id=pref_id, kind=kind, text=text, sources=sources or ["operator"])


def _legacy_document(*prefs: dict) -> str:
    return json.dumps({"schema_version": 1, "prefs": list(prefs)})


def _legacy_pref(pref_id: str, text: str = "short sentences") -> dict:
    return {"id": pref_id, "kind": "preference", "text": text, "sources": ["operator"]}


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


# ── warm inject ──────────────────────────────────────────────────────────


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
    _set(agent.storage_key, "short-sentences", sources=["/me/notes/how.md"])
    _set(agent.storage_key, "pytest", kind="tool_bias", text="use uv run pytest")

    reads: list[Path] = []
    original = Path.read_text

    def _spy(self: Path, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _spy)
    contexts = [
        context_builder.build_context(_turn(agent, state, trigger_type="human_chat", contract_kind="decision")),
        context_builder.build_context(
            _turn(agent, state, trigger_type="activity_resumed", contract_kind="execution", project="billing")
        ),
    ]
    for context in contexts:
        sections = _warm_sections(context)
        assert len(sections) == 1
        section = sections[0]
        assert section.startswith("# Standing prefs (manage with pref)")
        assert "preference short-sentences" in section
        assert "/me/notes/how.md" in section
        assert "tool_bias pytest" in section
        assert "use uv run pytest" in section
        assert len(section) <= WARM_SECTION_CHAR_CAP
        joined = "\n".join(str(message.get("content") or "") for message in context)
        assert "NOTEBODY_SHOULD_NOT_BE_READ" not in joined
        assert "PROJECT_NOTE_BODY" not in joined
    assert all(path != notes and notes.parent not in path.parents for path in reads)
    assert all(path != project_note for path in reads)


def test_social_turn_does_not_inject_standing_prefs() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    _set(agent.storage_key, "tone")
    context = context_builder.build_context(_turn(agent, state, trigger_type="social", contract_kind="execution"))
    assert _warm_sections(context) == []


def test_warm_section_soft_cap_keeps_the_store_and_points_at_pref_list() -> None:
    agent = db.create_agent("Ada", role="Writer")
    for index in range(6):
        _set(agent.storage_key, f"pref-{index}", text=f"sticky number {index} " + ("word " * 12), sources=[f"src-{index}"])
    loaded = read_standing_prefs(agent.storage_key)
    section = render_warm_section(loaded)
    assert section is not None
    assert len(section) <= WARM_SECTION_CHAR_CAP
    assert section.startswith("# Standing prefs (manage with pref)")
    omitted = len(loaded) - sum(1 for line in section.splitlines() if line.startswith("- "))
    assert omitted > 0
    assert section.splitlines()[-1] == f"more: {omitted} not shown — run pref list"
    assert "/me/standing_prefs.json" not in section
    assert [item.id for item in loaded] == [f"pref-{index}" for index in range(6)]


def test_prefs_text_does_not_invent_board_done() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Ship the note", assigned_to=agent.id, project="billing")
    _set(agent.storage_key, "fake-done", kind="constraint", text="Board status is Done")
    context = context_builder.build_context(
        _turn(agent, state, trigger_type="activity_resumed", contract_kind="execution", project="billing")
    )
    sections = _warm_sections(context)
    assert sections and "Board status is Done" in sections[0]
    fresh = db.get_task(task.id)
    assert fresh is not None
    assert fresh.status == "pending"
    assert not (fresh.completion_summary or "").strip()


# ── typed store ──────────────────────────────────────────────────────────


def test_set_adds_replaces_by_id_and_keeps_order() -> None:
    key = db.create_agent("Ada", role="Writer").storage_key
    _set(key, "tone", text="short sentences")
    _set(key, "tools", kind="tool_bias", text="use uv run pytest")
    _set(key, "last", text="third")
    replaced = _set(key, "tone", kind="style", text="plain words", sources=["thread-9", "operator chat"])
    assert replaced.text == "plain words"
    prefs = list_standing_prefs(key)
    assert [item.id for item in prefs] == ["tone", "tools", "last"]
    assert prefs[0].kind == "style"
    assert prefs[0].sources == ["thread-9", "operator chat"]
    assert read_standing_prefs(key) == prefs


def test_remove_drops_one_id_and_a_missing_id_raises() -> None:
    key = db.create_agent("Ada", role="Writer").storage_key
    _set(key, "tone")
    _set(key, "tools")
    remove_standing_pref(key, "tone")
    assert [item.id for item in list_standing_prefs(key)] == ["tools"]
    before = standing_prefs_file(key).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match='^no pref with id "tone"$'):
        remove_standing_pref(key, "tone")
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before
    remove_standing_pref(key, "tools")
    assert list_standing_prefs(key) == []


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"text": "x" * 161}, "^pref text is 161 characters; the limit is 160 on one line$"),
        ({"text": "first line\nsecond line"}, "^pref text has a line break; the limit is one line up to 160 characters$"),
        ({"text": "   "}, "^pref text is empty; give the rule as one line up to 160 characters$"),
        ({"kind": "rule"}, '^kind "rule" is not one of: preference, constraint, style, tool_bias$'),
        ({"sources": []}, "^pref needs 1 to 4 sources; got 0$"),
        ({"sources": ["a", "b", "c", "d", "e"]}, "^pref needs 1 to 4 sources; got 5$"),
        ({"sources": ["ok", "s" * 81]}, "^source 2 is 81 characters; the limit is 80$"),
        ({"pref_id": "has space"}, '^pref id "has space" must be a short token: 1 to 64 letters'),
        ({"pref_id": "x" * 65}, "must be a short token: 1 to 64 letters"),
    ],
)
def test_each_validation_error_is_one_sentence_naming_its_limit(fields: dict, message: str) -> None:
    key = db.create_agent("Ada", role="Writer").storage_key
    args = {"pref_id": "tone", "kind": "preference", "text": "short sentences", "sources": ["operator"]}
    args.update(fields)
    with pytest.raises(ValueError, match=message):
        set_standing_pref(key, **args)
    assert not standing_prefs_file(key).exists()


def test_store_cap_rejects_the_set_without_dropping_existing_prefs(monkeypatch: pytest.MonkeyPatch) -> None:
    assert STORE_TEXT_BYTE_CAP == 4096
    monkeypatch.setattr(standing_prefs, "STORE_TEXT_BYTE_CAP", 20)
    key = db.create_agent("Ada", role="Writer").storage_key
    _set(key, "tone", text="short sentences")
    before = standing_prefs_file(key).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=r"^standing prefs would hold 36 bytes of text; the limit is 20\. "):
        _set(key, "extra", text="another standing rule")
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before


def test_corrupt_store_is_never_overwritten_or_injected() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    path = standing_prefs_file(agent.storage_key)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable; refusing to overwrite"):
        _set(agent.storage_key, "tone")
    with pytest.raises(ValueError, match="unreadable; refusing to overwrite"):
        remove_standing_pref(agent.storage_key, "tone")
    with pytest.raises(ValueError, match="^standing prefs store is unreadable: "):
        list_standing_prefs(agent.storage_key)
    context = context_builder.build_context(_turn(agent, state, trigger_type="human_chat", contract_kind="decision"))
    assert _warm_sections(context) == []
    assert path.read_text(encoding="utf-8") == "{not json"


def test_writes_are_atomic_and_leave_no_temp_file() -> None:
    key = db.create_agent("Ada", role="Writer").storage_key
    _set(key, "tone")
    _set(key, "tools")
    remove_standing_pref(key, "tone")
    assert sorted(item.name for item in standing_prefs_root().iterdir()) == [f"{key}.json"]
    document = json.loads(standing_prefs_file(key).read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert [item["id"] for item in document["prefs"]] == ["tools"]


def test_failed_write_keeps_the_previous_store_and_removes_the_temp_file(monkeypatch: pytest.MonkeyPatch) -> None:
    key = db.create_agent("Ada", role="Writer").storage_key
    _set(key, "tone")
    before = standing_prefs_file(key).read_text(encoding="utf-8")

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(standing_prefs.os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        _set(key, "tools")
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before
    assert sorted(item.name for item in standing_prefs_root().iterdir()) == [f"{key}.json"]


def test_unreadable_store_warning_names_the_key_path_and_reason(caplog: pytest.LogCaptureFixture) -> None:
    key = db.create_agent("Ada", role="Writer").storage_key
    path = standing_prefs_file(key)
    path.write_text(json.dumps({"prefs": []}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.standing_prefs"):
        assert read_standing_prefs(key) == []
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert key in messages[0]
    assert str(path) in messages[0]
    assert "schema_version" in messages[0]


# ── isolation ────────────────────────────────────────────────────────────


def test_prefs_root_is_outside_every_agent_path_jail_root() -> None:
    agent = db.create_agent("Ada", role="Writer")
    root = standing_prefs_root().resolve()
    jail = allowed_workspace_roots(agent.storage_key)
    assert jail
    for allowed in jail:
        allowed = Path(allowed).resolve()
        assert allowed != root
        assert allowed not in root.parents
        assert root not in allowed.parents


def test_no_virtual_path_resolves_into_the_prefs_root() -> None:
    agent = db.create_agent("Ada", role="Writer")
    root = standing_prefs_root().resolve()
    for raw in (
        "/me",
        "/me/standing_prefs.json",
        f"/me/../system/standing_prefs/{agent.storage_key}.json",
        "/me/../../system/standing_prefs",
        "/projects",
        "/projects/../system/standing_prefs",
        "/projects/billing/../../system",
    ):
        try:
            resolved = resolve_cli_path(agent.storage_key, "/me", raw)
        except (ValueError, LookupError):
            continue
        if resolved.real_path is None:
            continue
        real = resolved.real_path.resolve()
        assert real != root and root not in real.parents, raw


# ── legacy migration ─────────────────────────────────────────────────────


def _legacy_file(key: str) -> Path:
    return agent_artifact_dir(key) / "standing_prefs.json"


def test_valid_legacy_file_moves_and_the_workspace_copy_is_deleted() -> None:
    legacy = _legacy_file("agent_0042")
    raw = _legacy_document(_legacy_pref("tone"), _legacy_pref("tools", "use uv"))
    legacy.write_text(raw, encoding="utf-8")
    migrate_workspace_standing_prefs()
    assert not legacy.exists()
    assert [item.id for item in list_standing_prefs("agent_0042")] == ["tone", "tools"]
    assert sorted(item.name for item in standing_prefs_root().iterdir()) == ["agent_0042.json"]


def test_invalid_legacy_file_stays_untouched_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    legacy = _legacy_file("agent_0042")
    raw = json.dumps({"prefs": [{"kind": "preference", "sticky": "use uv", "sources": ["operator"]}]})
    legacy.write_text(raw, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.standing_prefs"):
        migrate_workspace_standing_prefs()
    assert legacy.read_text(encoding="utf-8") == raw
    assert not standing_prefs_file("agent_0042").exists()
    assert read_standing_prefs("agent_0042") == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "agent_0042" in warnings[0] and str(legacy) in warnings[0]
    assert "schema_version" in warnings[0]


def test_when_both_exist_the_workspace_file_stays_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    _set("agent_0042", "current", text="system copy")
    system_before = standing_prefs_file("agent_0042").read_text(encoding="utf-8")
    legacy = _legacy_file("agent_0042")
    raw = _legacy_document(_legacy_pref("old"))
    legacy.write_text(raw, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.standing_prefs"):
        migrate_workspace_standing_prefs()
    assert legacy.read_text(encoding="utf-8") == raw
    assert standing_prefs_file("agent_0042").read_text(encoding="utf-8") == system_before
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "agent_0042" in warnings[0] and "already exists" in warnings[0]


def test_running_the_migration_twice_is_a_no_op() -> None:
    _legacy_file("agent_0042").write_text(_legacy_document(_legacy_pref("tone")), encoding="utf-8")
    bad = _legacy_file("agent_0043")
    bad.write_text("{not json", encoding="utf-8")
    migrate_workspace_standing_prefs()
    after_first = standing_prefs_file("agent_0042").read_text(encoding="utf-8")
    migrate_workspace_standing_prefs()
    assert standing_prefs_file("agent_0042").read_text(encoding="utf-8") == after_first
    assert not _legacy_file("agent_0042").exists()
    assert bad.read_text(encoding="utf-8") == "{not json"
    assert sorted(item.name for item in standing_prefs_root().iterdir()) == ["agent_0042.json"]


def test_init_db_runs_the_migration() -> None:
    _legacy_file("agent_0042").write_text(_legacy_document(_legacy_pref("tone")), encoding="utf-8")
    db.init_db()
    assert not _legacy_file("agent_0042").exists()
    assert [item.id for item in list_standing_prefs("agent_0042")] == ["tone"]


def test_reset_database_removes_the_prefs_root() -> None:
    _set("agent_0042", "tone")
    db.reset_database()
    assert not standing_prefs_file("agent_0042").exists()
