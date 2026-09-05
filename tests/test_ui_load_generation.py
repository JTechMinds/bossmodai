"""UI A3 — select/desk/tasks/meeting/files apply only the current load generation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_load_generation_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_utils_exports_create_load_generation() -> None:
    source = _read("utils.js")
    assert "function createLoadGeneration()" in source
    assert "createLoadGeneration," in source


def test_select_and_panel_loads_use_shared_generation() -> None:
    source = _read("agent-context.js")
    assert "const selectGeneration = BossModUtils.createLoadGeneration()" in source
    assert "const chatLoad = BossModUtils.createLoadGeneration()" in source
    assert "const meetingLoad = BossModUtils.createLoadGeneration()" in source
    assert "const tasksLoad = BossModUtils.createLoadGeneration()" in source
    assert "const deskLoad = BossModUtils.createLoadGeneration()" in source
    assert "function isLiveAgentLoad(" in source
    assert "let activeChatLoadId" not in source
    assert "const loadId = chatLoad.next()" in source
    assert "if (!isLiveAgentLoad(chatLoad, loadId, agentId, 'chat'))" in source

    select = source.split("async function selectAgent(agentData) {", 1)[1].split(
        "function deselectAgent()", 1
    )[0]
    assert "const loadId = selectGeneration.next()" in select
    assert "if (!selectGeneration.isCurrent(loadId)) return;" in select
    assert "selectedAgent = mergeAgentSnapshot(details, agentData)" in select

    deselect = source.split("function deselectAgent() {", 1)[1].split(
        "function startCreateAgent()", 1
    )[0]
    assert "selectGeneration.next()" in deselect

    create = source.split("function startCreateAgent() {", 1)[1].split(
        "function getSelectedAgent()", 1
    )[0]
    assert "selectGeneration.next()" in create


def test_meeting_tasks_desk_guard_before_dom_apply() -> None:
    source = _read("agent-context.js")

    meeting = source.split("async function renderMeeting() {", 1)[1].split(
        "function renderMeetingEmpty(", 1
    )[0]
    assert "const loadId = meetingLoad.next()" in meeting
    assert "if (!isLiveAgentLoad(meetingLoad, loadId, agentId, 'meeting')) return;" in meeting
    assert meeting.index("const loadId = meetingLoad.next()") < meeting.index(
        "renderMeetingEmpty(container)"
    )

    tasks = source.split("async function renderTasks() {", 1)[1].split(
        "async function renderDesk(", 1
    )[0]
    assert "const loadId = tasksLoad.next()" in tasks
    assert "if (!isLiveAgentLoad(tasksLoad, loadId, agentId, 'tasks')) return;" in tasks
    assert tasks.index("if (!isLiveAgentLoad(tasksLoad, loadId, agentId, 'tasks')) return;") < tasks.index(
        "container.innerHTML"
    )

    desk = source.split("async function renderDesk(path = '/me', { forceRefresh = false } = {}) {", 1)[
        1
    ].split("function renderDeskPayload(", 1)[0]
    assert "const loadId = deskLoad.next()" in desk
    assert "if (!isLiveAgentLoad(deskLoad, loadId, agentId, 'desk') || activeDeskPath !== requestedPath) return;" in desk
    assert desk.index("setCachedDesk(agentId, requestedPath, payload)") < desk.index(
        "renderDeskPayload(container, payload)"
    )


def test_company_files_guards_navigate_and_search() -> None:
    source = _read("company-files.js")
    assert "const filesLoad = BossModUtils.createLoadGeneration()" in source

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
        ["node", str(HARNESS), str(JS / "utils.js")],
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
