"""UI A3 — conversation/desk/tasks/files apply only the current load generation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_load_generation_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_gates_export_create_load_generation() -> None:
    source = _read("core/gates.js")
    assert "function createLoadGeneration()" in source
    assert "createLoadGeneration," in source


def test_select_and_panel_loads_use_shared_generation() -> None:
    """The conversation half of this property lives in conversation.js."""
    source = _read("conversation/conversation.js")
    assert "const generation = BossModGates.createLoadGeneration()" in source
    assert "const loadId = generation.next()" in source
    assert "if (!generation.isCurrent(loadId)) return;" in source
    # The dock-era single mutable id is what the generation replaced.
    assert "let activeChatLoadId" not in source

    body = source.split("async function open(conversationId, kind) {", 1)[1]
    assert body.index("const loadId = generation.next()") < body.index("await source.load()")
    assert body.index("await source.load()") < body.index("if (!generation.isCurrent(loadId)) return;")


def test_selecting_another_agent_invalidates_the_previous_desk_loads() -> None:
    """agent-context.js's selectGeneration, re-pointed to the context column.

    The dock-era view kept one selected agent and one generation, and bumped it
    on select, deselect, and create so a slow response for the previous agent
    could not paint over the new one. The column expresses the same guarantee
    structurally: switching agents unmounts the whole desk before mounting the
    next, and every sub-view invalidates its own generation as it goes. That is
    stronger than the predicate it replaces, not weaker — nothing survives the
    switch to be guarded in the first place.
    """
    column = _read("context/context-column.js")
    apply_body = column.split("function apply() {", 1)[1]
    assert "unmountView();" in apply_body
    assert apply_body.index("unmountView();") < apply_body.index("view = build(mode, agentId);")
    assert "if (view) view.destroy();" in column
    # Keyed on the agent, so selecting someone else really is a switch.
    assert "const key = `${mode}:${agentId || ''}`;" in column

    for name in ("context/desk-files.js", "context/desk-tasks.js", "context/desk-actions.js"):
        source = _read(name)
        destroy = source.split("destroy() {", 1)[1]
        assert "destroyed = true;" in destroy, f"{name} must stop painting on destroy"
        assert ".next();" in destroy, f"{name} must invalidate its generation on destroy"

    panel = _read("context/desk-panel.js")
    for child in ("actions.destroy();", "tasks.destroy();", "files.destroy();"):
        assert child in panel, f"the desk must tear down {child}"


def test_meeting_tasks_desk_guard_before_dom_apply() -> None:
    """The desk and tasks halves, re-pointed to context/ in Phase 2B."""
    desk = _read("context/desk-files.js")
    assert "const load = BossModGates.createLoadGeneration()" in desk
    assert "function isLive(loadId, requestedPath)" in desk
    # Both guards, as before: the generation AND the requested path.
    assert "load.isCurrent(loadId) && activePath === requestedPath" in desk
    body = desk.split("async function open(path) {", 1)[1]
    assert "const loadId = load.next()" in body
    assert body.index("const loadId = load.next()") < body.index("await api(deskUrl(")
    assert body.index("if (!isLive(loadId, requestedPath)) return;") < body.index(
        "renderDirectory(payload)"
    )

    tasks = _read("context/desk-tasks.js")
    assert "const load = BossModGates.createLoadGeneration()" in tasks
    assert "const loadId = load.next()" in tasks
    refresh = tasks.split("async function refresh() {", 1)[1]
    assert refresh.index("const loadId = load.next()") < refresh.index("await Promise.all([")
    assert refresh.index("if (destroyed || !load.isCurrent(loadId)) return;") < refresh.index(
        "tasks.slice(0, TOP_N)"
    )


def test_company_files_guards_navigate_and_search() -> None:
    source = _read("company-files.js")
    assert "const filesLoad = BossModGates.createLoadGeneration()" in source

    fetch = source.split("async function fetchAndRender() {", 1)[1].split(
        "function renderDirectory()", 1
    )[0]
    assert "const loadId = filesLoad.next()" in fetch
    assert "if (!filesLoad.isCurrent(loadId) || currentPath !== requestedPath) return;" in fetch

    search = source.split("async function performGlobalSearch(query) {", 1)[1].split(
        "function restoreSearchFocus()", 1
    )[0]
    assert "const loadId = filesLoad.next()" in search
    assert "if (!filesLoad.isCurrent(loadId)) return;" in search

    bind = source.split("function bindInteractions() {", 1)[1].split(
        "async function performGlobalSearch(", 1
    )[0]
    assert "document.addEventListener('click'" not in bind
    assert "function onDocumentClickCloseNewMenu(" in source
    assert "document.addEventListener('click', onDocumentClickCloseNewMenu)" in source


def test_load_generation_harness_drops_stale_applies() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "gates.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "lastSelectWins": True,
        "lastDeskPathWins": True,
        "invalidatedSearchDropped": True,
    }
