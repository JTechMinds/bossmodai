"""Agent memory: a system-owned store (schema v2), injected on work turns only.

Covers the typed operations, system-assigned ids that are never reused,
validation sentences, the cross-process lock, atomic writes, the warm
section, isolation from every agent path, and the one-time v1 → v2 migration.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

import db
from core import config
from core.agent_loop import standing_prefs
from core.agent_loop.standing_prefs import (
    Memory,
    MemoryNotFoundError,
    add_memory,
    line_max_chars,
    list_memories,
    migrate_standing_prefs_v1,
    read_memories,
    remove_memory,
    render_warm_section,
    replace_memory,
    section_max_chars,
    standing_prefs_file,
)
from core.bm_cli import filesystem
from core.bm_cli.filesystem import agent_artifact_dir, standing_prefs_root
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.host_roots import allowed_workspace_roots
from core.bm_cli.virtual_fs import resolve_cli_path
from core.llm import context_builder
from db.floors import LOBBY_ID

# Two prefs in the shape the live v1 stores hold (one-line texts, filler
# source "operator"); the migration must carry each text over byte for byte.
_LIVE_V1_TEXTS = (
    "Use uv for Python envs and installs, never pip into the host interpreter, for every repo.",
    "Keep status updates to the boss short: what changed, what is blocked, and what you need next.",
)


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


def _key() -> str:
    return db.create_agent("Ada", role="Writer").storage_key


def _set_limit(key: str, value: int) -> None:
    db.set_setting(key, str(value), "context")
    config.reload()


def _store_names() -> list[str]:
    return sorted(item.name for item in standing_prefs_root().iterdir())


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
        if str(message.get("content") or "").startswith("# Your memory")
    ]


# ── typed store ──────────────────────────────────────────────────────────


def test_add_assigns_1_2_3_and_a_removed_id_is_never_reused() -> None:
    key = _key()
    assert [add_memory(key, text).id for text in ("one", "two", "three")] == [1, 2, 3]
    removed = remove_memory(key, 2)
    assert (removed.id, removed.text) == (2, "two")
    assert add_memory(key, "four").id == 4
    assert [(item.id, item.text) for item in list_memories(key)] == [(1, "one"), (3, "three"), (4, "four")]
    document = json.loads(standing_prefs_file(key).read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
    assert document["next_id"] == 5


def test_removing_the_newest_still_never_reuses_its_id() -> None:
    key = _key()
    add_memory(key, "one")
    remove_memory(key, 1)
    assert list_memories(key) == []
    assert add_memory(key, "two").id == 2


def test_replace_keeps_the_id_and_the_position() -> None:
    key = _key()
    for text in ("one", "two", "three"):
        add_memory(key, text)
    replaced = replace_memory(key, 2, "  second, reworded  ")
    assert (replaced.id, replaced.text) == (2, "second, reworded")
    assert [(item.id, item.text) for item in list_memories(key)] == [
        (1, "one"),
        (2, "second, reworded"),
        (3, "three"),
    ]
    assert add_memory(key, "four").id == 4
    assert read_memories(key) == list_memories(key)


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_a_missing_id_raises_and_leaves_the_store(operation: str) -> None:
    key = _key()
    add_memory(key, "one")
    before = standing_prefs_file(key).read_text(encoding="utf-8")
    with pytest.raises(MemoryNotFoundError, match=r"^no memory #7$"):
        if operation == "replace":
            replace_memory(key, 7, "new")
        else:
            remove_memory(key, 7)
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("   ", r"^memory text is empty; give it as one line$"),
        ("first line\nsecond line", r"^memory text has a line break; keep it to one line$"),
        ("first line\rsecond line", r"^memory text has a line break; keep it to one line$"),
    ],
)
def test_empty_and_multi_line_text_is_refused(text: str, message: str) -> None:
    key = _key()
    with pytest.raises(ValueError, match=message):
        add_memory(key, text)
    assert not standing_prefs_file(key).exists()
    add_memory(key, "kept")
    with pytest.raises(ValueError, match=message):
        replace_memory(key, 1, text)
    assert list_memories(key)[0].text == "kept"


def test_text_at_the_line_setting_saves_and_one_over_is_refused() -> None:
    key = _key()
    limit = line_max_chars()
    assert limit == 400
    assert add_memory(key, "y" * limit).text == "y" * limit
    with pytest.raises(
        ValueError,
        match=r"^memory is 401 characters; the limit is 400\. Keep it to 1–2 short sentences\.$",
    ):
        add_memory(key, "x" * (limit + 1))
    with pytest.raises(ValueError, match=r"^memory is 401 characters; the limit is 400\."):
        replace_memory(key, 1, "x" * (limit + 1))
    assert [item.text for item in list_memories(key)] == ["y" * limit]


def test_the_line_setting_is_read_live_on_save() -> None:
    key = _key()
    _set_limit("standing_prefs_line_max_chars", 50)
    with pytest.raises(ValueError, match=r"^memory is 51 characters; the limit is 50\."):
        add_memory(key, "x" * 51)
    assert not standing_prefs_file(key).exists()
    assert add_memory(key, "x" * 50).text == "x" * 50


def test_store_cap_refuses_growth_and_keeps_the_store() -> None:
    _set_limit("standing_prefs_section_max_chars", 20)
    key = _key()
    add_memory(key, "short sentences")
    before = standing_prefs_file(key).read_text(encoding="utf-8")
    with pytest.raises(
        ValueError,
        match=r"^memory would grow to 36 characters; the limit is 20\. Replace or remove a memory first\.$",
    ):
        add_memory(key, "another standing rule")
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before


def _store_at_900_then_lower_section_to_500(key: str) -> None:
    # Three 300-character memories saved under the default cap, then the
    # operator lowers the section limit below the stored total.
    for letter in ("a", "b", "c"):
        add_memory(key, letter * 300)
    _set_limit("standing_prefs_section_max_chars", 500)


def test_a_lowered_section_still_saves_a_shortening_or_equal_replace() -> None:
    key = _key()
    _store_at_900_then_lower_section_to_500(key)
    replace_memory(key, 2, "b" * 200)
    replace_memory(key, 3, "z" * 300)
    assert [len(item.text) for item in list_memories(key)] == [300, 200, 300]


def test_a_lowered_section_refuses_a_new_memory_and_a_growing_replace() -> None:
    key = _key()
    _store_at_900_then_lower_section_to_500(key)
    before = standing_prefs_file(key).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=r"^memory would grow to 910 characters; the limit is 500\."):
        add_memory(key, "d" * 10)
    with pytest.raises(ValueError, match=r"^memory would grow to 1000 characters; the limit is 500\."):
        replace_memory(key, 1, "a" * 400)
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before


def test_corrupt_store_is_never_overwritten_or_injected() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    path = standing_prefs_file(agent.storage_key)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable; refusing to overwrite"):
        add_memory(agent.storage_key, "tone")
    with pytest.raises(ValueError, match="unreadable; refusing to overwrite"):
        replace_memory(agent.storage_key, 1, "tone")
    with pytest.raises(ValueError, match="unreadable; refusing to overwrite") as removed:
        remove_memory(agent.storage_key, 1)
    # A corrupt store is not "already gone": the API keeps it a 500.
    assert not isinstance(removed.value, MemoryNotFoundError)
    with pytest.raises(ValueError, match="^memory store is unreadable: "):
        list_memories(agent.storage_key)
    context = context_builder.build_context(_turn(agent, state, trigger_type="human_chat", contract_kind="decision"))
    assert _warm_sections(context) == []
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "next_id": 2, "memories": [{"id": 2, "text": "a"}]},
        {"schema_version": 2, "next_id": 3, "memories": [{"id": 1, "text": "a"}, {"id": 1, "text": "b"}]},
        {"schema_version": 2, "next_id": 3, "memories": [{"id": True, "text": "a"}]},
        {"schema_version": 2, "next_id": 3, "memories": [{"id": 1, "text": "a", "kind": "style"}]},
    ],
)
def test_an_inconsistent_document_is_rejected_strictly(document: dict) -> None:
    key = _key()
    standing_prefs_file(key).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="^memory store is unreadable: "):
        list_memories(key)
    assert read_memories(key) == []


def test_writes_are_atomic_and_leave_only_the_store_and_its_lock() -> None:
    key = _key()
    add_memory(key, "tone")
    add_memory(key, "tools")
    remove_memory(key, 1)
    assert _store_names() == [f"{key}.json", f"{key}.lock"]
    document = json.loads(standing_prefs_file(key).read_text(encoding="utf-8"))
    assert document == {"schema_version": 2, "next_id": 3, "memories": [{"id": 2, "text": "tools"}]}


def test_failed_write_keeps_the_previous_store_and_removes_the_temp_file(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _key()
    add_memory(key, "tone")
    before = standing_prefs_file(key).read_text(encoding="utf-8")

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(standing_prefs.os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        add_memory(key, "tools")
    assert standing_prefs_file(key).read_text(encoding="utf-8") == before
    assert _store_names() == [f"{key}.json", f"{key}.lock"]


def test_unreadable_store_warning_names_the_key_path_and_reason(caplog: pytest.LogCaptureFixture) -> None:
    key = _key()
    path = standing_prefs_file(key)
    path.write_text(json.dumps({"memories": []}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.standing_prefs"):
        assert read_memories(key) == []
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert key in messages[0]
    assert str(path) in messages[0]
    assert "schema_version" in messages[0]


# ── the lock ─────────────────────────────────────────────────────────────


_ADDS_PER_PROCESS = 40


def _add_many(system_root: str, key: str, start: multiprocessing.synchronize.Event) -> None:
    # A spawned child: point the store at the same root, and read the limits
    # from the parent's isolated DB (BOSSMOD_DB_PATH is inherited).
    filesystem._SYSTEM_ROOT = Path(system_root)
    config.reload()
    start.wait()
    for index in range(_ADDS_PER_PROCESS):
        add_memory(key, f"{os.getpid()}-{index}")


def test_two_processes_adding_at_once_lose_nothing() -> None:
    key = _key()
    _set_limit("standing_prefs_section_max_chars", 100_000)
    db.close_connection()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = [
        context.Process(target=_add_many, args=(str(filesystem._SYSTEM_ROOT), key, start)) for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=120)
        assert worker.exitcode == 0
    memories = list_memories(key)
    total = 2 * _ADDS_PER_PROCESS
    assert len(memories) == total
    assert sorted(item.id for item in memories) == list(range(1, total + 1))
    assert len({item.text for item in memories}) == total
    document = json.loads(standing_prefs_file(key).read_text(encoding="utf-8"))
    assert document["next_id"] == total + 1


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


def test_work_turns_inject_memory_and_do_not_read_note_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    notes = agent_artifact_dir(agent.storage_key) / "notes" / "how.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("NOTEBODY_SHOULD_NOT_BE_READ\n", encoding="utf-8")
    project_note = floor_root(LOBBY_ID) / "billing" / "how.md"
    project_note.parent.mkdir(parents=True)
    project_note.write_text("PROJECT_NOTE_BODY\n", encoding="utf-8")
    add_memory(agent.storage_key, "The boss wants plain English, not jargon.")
    add_memory(agent.storage_key, "Use uv run pytest.")

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
        assert sections == [
            "# Your memory (shown every turn; manage with memory)\n"
            "- 1 — The boss wants plain English, not jargon.\n"
            "- 2 — Use uv run pytest."
        ]
        joined = "\n".join(str(message.get("content") or "") for message in context)
        assert "NOTEBODY_SHOULD_NOT_BE_READ" not in joined
        assert "PROJECT_NOTE_BODY" not in joined
    assert all(path != notes and notes.parent not in path.parents for path in reads)
    assert all(path != project_note for path in reads)


def test_social_turn_does_not_inject_memory() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    add_memory(agent.storage_key, "tone")
    context = context_builder.build_context(_turn(agent, state, trigger_type="social", contract_kind="execution"))
    assert _warm_sections(context) == []


def _long_memories(key: str, count: int) -> list[Memory]:
    # 300-character memories: each renders as one ~306-character line, and
    # 13 of them (3900 characters of text) still fit the store cap while
    # overflowing the section.
    for index in range(count):
        add_memory(key, f"memory {index:02d} " + "w" * 290)
    return read_memories(key)


def test_many_long_memories_render_whole_within_the_section_cap_with_no_more_line() -> None:
    assert section_max_chars() == 4000
    loaded = _long_memories(_key(), 10)
    section = render_warm_section(loaded)
    assert section is not None
    assert len(section) <= section_max_chars()
    lines = section.splitlines()
    assert lines[0] == "# Your memory (shown every turn; manage with memory)"
    assert lines[1:] == [f"- {item.id} — {item.text}" for item in loaded]


def test_warm_section_soft_cap_keeps_the_store_and_points_at_memory_list() -> None:
    key = _key()
    loaded = _long_memories(key, 13)
    section = render_warm_section(loaded)
    assert section is not None
    assert len(section) <= section_max_chars()
    shown = [line for line in section.splitlines() if line.startswith("- ")]
    omitted = len(loaded) - len(shown)
    assert omitted > 0
    assert section.splitlines()[-1] == f"more: {omitted} not shown — run memory list"
    assert [item.id for item in list_memories(key)] == list(range(1, 14))


def test_a_section_at_the_minimum_renders_one_full_limit_memory_whole() -> None:
    line = line_max_chars()
    minimum = line + standing_prefs.WARM_PREFIX_MAX_CHARS + len(standing_prefs.WARM_SECTION_HEADER) + 1
    _set_limit("standing_prefs_section_max_chars", minimum)
    longest_id = 10 ** standing_prefs.MEMORY_ID_MAX_DIGITS - 1
    memory = Memory(id=longest_id, text="t" * line)
    section = render_warm_section([memory])
    assert section == f"{standing_prefs.WARM_SECTION_HEADER}\n- {longest_id} — {'t' * line}"
    assert len(section) == minimum


def test_a_lowered_limit_cuts_the_warm_line_only() -> None:
    key = _key()
    text = "Keep " + "r" * 290 + " end."
    add_memory(key, text)
    _set_limit("standing_prefs_line_max_chars", 100)
    # Parsing never checks the operator limit, so the store still loads.
    loaded = read_memories(key)
    assert [(item.id, item.text) for item in loaded] == [(1, text)]
    section = render_warm_section(loaded)
    assert section is not None
    assert section.splitlines()[1] == f"- 1 — {text[:97]}..."
    assert list_memories(key)[0].text == text


def test_render_reads_both_limits_live() -> None:
    key = _key()
    memories = [add_memory(key, "a" * 60), add_memory(key, "b" * 60)]
    before = render_warm_section(memories)
    assert before is not None
    assert "more:" not in before
    _set_limit("standing_prefs_line_max_chars", 50)
    _set_limit("standing_prefs_section_max_chars", 150)
    after = render_warm_section(memories)
    assert after is not None
    assert len(after) <= 150
    lines = after.splitlines()
    assert lines[1] == f"- 1 — {'a' * 47}..."
    assert lines[-1] == "more: 1 not shown — run memory list"


def test_memory_text_does_not_invent_board_done() -> None:
    agent = db.create_agent("Ada", role="Writer")
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = db.create_task("Ship the note", assigned_to=agent.id, project="billing")
    add_memory(agent.storage_key, "Board status is Done")
    context = context_builder.build_context(
        _turn(agent, state, trigger_type="activity_resumed", contract_kind="execution", project="billing")
    )
    sections = _warm_sections(context)
    assert sections and "Board status is Done" in sections[0]
    fresh = db.get_task(task.id)
    assert fresh is not None
    assert fresh.status == "pending"
    assert not (fresh.completion_summary or "").strip()


# ── isolation ────────────────────────────────────────────────────────────


def test_memory_root_is_outside_every_agent_path_jail_root() -> None:
    agent = db.create_agent("Ada", role="Writer")
    root = standing_prefs_root().resolve()
    jail = allowed_workspace_roots(agent.storage_key)
    assert jail
    for allowed in jail:
        allowed = Path(allowed).resolve()
        assert allowed != root
        assert allowed not in root.parents
        assert root not in allowed.parents


def test_no_virtual_path_resolves_into_the_memory_root() -> None:
    agent = db.create_agent("Ada", role="Writer")
    root = standing_prefs_root().resolve()
    for raw in (
        "/me",
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


# ── v1 → v2 migration ────────────────────────────────────────────────────


def _v1(*texts: str, **overrides) -> str:
    prefs = [
        {"id": f"pref-{index}", "kind": "preference", "text": text, "sources": ["operator"]}
        for index, text in enumerate(texts, start=1)
    ]
    return json.dumps({"schema_version": 1, "prefs": prefs, **overrides})


def test_migration_converts_the_live_v1_shape_exactly() -> None:
    path = standing_prefs_file("agent_0012")
    path.write_text(_v1(*_LIVE_V1_TEXTS), encoding="utf-8")
    migrate_standing_prefs_v1()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == {
        "schema_version": 2,
        "next_id": 3,
        "memories": [{"id": 1, "text": _LIVE_V1_TEXTS[0]}, {"id": 2, "text": _LIVE_V1_TEXTS[1]}],
    }
    assert add_memory("agent_0012", "third").id == 3


def test_migration_converts_an_empty_v1_store() -> None:
    path = standing_prefs_file("agent_0013")
    path.write_text(_v1(), encoding="utf-8")
    migrate_standing_prefs_v1()
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": 2, "next_id": 1, "memories": []}


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        json.dumps({"schema_version": 1, "prefs": [{"id": "x", "kind": "style", "sticky": "use uv"}]}),
        json.dumps({"schema_version": 1, "prefs": [{"id": "x", "kind": "style", "text": "a\nb", "sources": []}]}),
        json.dumps({"schema_version": 7, "memories": []}),
        json.dumps({"prefs": []}),
    ],
)
def test_migration_leaves_an_invalid_store_untouched_with_a_warning(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = standing_prefs_file("agent_0042")
    path.write_text(raw, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.agent_loop.standing_prefs"):
        migrate_standing_prefs_v1()
    assert path.read_text(encoding="utf-8") == raw
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "agent_0042" in warnings[0] and str(path) in warnings[0]


def test_migration_skips_v2_and_running_it_twice_is_a_no_op(caplog: pytest.LogCaptureFixture) -> None:
    add_memory("agent_0050", "already v2")
    v2_before = standing_prefs_file("agent_0050").read_text(encoding="utf-8")
    standing_prefs_file("agent_0051").write_text(_v1("old"), encoding="utf-8")
    migrate_standing_prefs_v1()
    after_first = standing_prefs_file("agent_0051").read_text(encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="core.agent_loop.standing_prefs"):
        migrate_standing_prefs_v1()
    assert caplog.records == []
    assert standing_prefs_file("agent_0050").read_text(encoding="utf-8") == v2_before
    assert standing_prefs_file("agent_0051").read_text(encoding="utf-8") == after_first
    assert [(item.id, item.text) for item in list_memories("agent_0051")] == [(1, "old")]


def test_init_db_runs_the_migration() -> None:
    standing_prefs_file("agent_0014").write_text(_v1(*_LIVE_V1_TEXTS), encoding="utf-8")
    db.init_db()
    assert [(item.id, item.text) for item in list_memories("agent_0014")] == [
        (1, _LIVE_V1_TEXTS[0]),
        (2, _LIVE_V1_TEXTS[1]),
    ]


def test_reset_database_removes_the_memory_root() -> None:
    add_memory("agent_0042", "tone")
    db.reset_database()
    assert not standing_prefs_file("agent_0042").exists()
    assert not standing_prefs_file("agent_0042").with_suffix(".lock").exists()


# ── cold import ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module",
    [
        "core.agent_loop.standing_prefs",
        "core.agent_loop.runtime_core",
        "core.bm_cli.command_registry",
        "core.bm_cli.memory_commands",
    ],
)
def test_each_memory_module_imports_first_in_a_fresh_interpreter(module: str) -> None:
    # standing_prefs → core.bm_cli (eager runtime) → command_registry →
    # standing_prefs was a cycle; only a cold first import shows it. The
    # child inherits conftest's temp DB and artifacts roots via os.environ.
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
