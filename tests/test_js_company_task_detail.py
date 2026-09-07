"""Task detail: deliverable links must open files and host folders.

Re-pointed in Phase 3A from company-task-detail.js / company-tasks.js to the
Board's modules. Every assertion below guards the same property it always did;
where a literal changed it is because the markup is now built with h() rather
than concatenated into a string, and the note on each says so.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
BOARD = JS / "places" / "board"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_deliverable_cards_keep_original_path_and_agent_id() -> None:
    """The path and the agent id it was recorded against travel together.

    The signature gained `api` because the helper takes the authenticated fetch
    by injection now instead of reaching for the global; `path` and `agentId`
    are still both parameters and are still passed through unmodified, which is
    the whole property.
    """
    source = (BOARD / "task-deliverables.js").read_text(encoding="utf-8")
    assert "function openDeliverablePath(api, path, agentId)" in source
    assert "'data-agent-id': agentId" in source
    assert "isAgentDeskPath(target)" in source
    assert "/api/agents/${encodeURIComponent(agentId)}/desk?path=" in source
    assert "/api/company/files?path=" in source
    assert "/api/company/files/open-folder" in source
    assert "payload.kind === 'file'" in source
    assert "BossModFileViewer.open" in source


def test_deliverable_open_does_not_remap_host_paths_through_me() -> None:
    source = (BOARD / "task-deliverables.js").read_text(encoding="utf-8")
    assert "'/agents/' + storageKey" not in source
    assert "virtualPath.slice(3)" not in source


def test_file_viewer_open_surfaces_load_failures() -> None:
    """Re-pointed in Phase 3B: the shared viewer is places/files/file-viewer.js.

    A deliverable card marks itself failed from this rejection, so a viewer
    that swallowed the load error would leave a card that silently does
    nothing when clicked.
    """
    source = (JS / "places" / "files" / "file-viewer.js").read_text(encoding="utf-8")
    assert "throw err;" in source


def test_cancel_task_button_and_confirm_copy() -> None:
    """The cancel button, its confirm copy, and the open/finished distinction.

    The detail half keeps the button, its label, and its id. `setCancelCallback`
    is gone and the assertion is stronger for it: the panel is handed `onCancel`
    at construction and is asserted never to name the cancel route itself, so
    there is exactly one place on the Board that can end a task.

    The table half moved to the Board's modules. `statusFilter`, its three
    chips, and `matchesBoardFilter` were the mechanism by which the old table
    let the operator see active work separately from finished work; four
    columns plus the disclosure are that mechanism now, and the assertions below
    pin the same distinction — open, done, and closed-without-completing are
    three counts, and `cancelled` is terminal.
    """
    detail = (BOARD / "task-detail.js").read_text(encoding="utf-8")
    assert "id: 'ct-cancel-task-btn'" in detail
    assert "'Cancel task'" in detail
    assert "onCancel(task)" in detail
    assert "/api/tasks/cancel" not in detail, (
        "the detail asks for a cancel; the Board performs it"
    )

    place = (BOARD / "board-place.js").read_text(encoding="utf-8")
    cancel = (BOARD / "board-cancel.js").read_text(encoding="utf-8")
    toolbar = (BOARD / "board-toolbar.js").read_text(encoding="utf-8")
    columns = (BOARD / "board-columns.js").read_text(encoding="utf-8")
    data = (BOARD / "board-data.js").read_text(encoding="utf-8")

    assert "Cancel this task?" in cancel
    assert "Cancel ${ids.length} tasks?" in cancel
    assert "Cancel selected" in toolbar
    assert "/api/tasks/cancel" in cancel

    assert "TERMINAL_STATUSES" in columns
    assert "'cancelled'" in columns
    assert "{ id: 'backlog'" in columns
    assert "{ id: 'done'" in columns
    # open / done / closed are three separate counts, so "active" and
    # "done/cancelled" are still distinguishable — without ever calling a
    # cancelled task done.
    for field in ("totals.open", "totals.done", "totals.closed"):
        assert field in data, f"the summary lost {field}"
    assert "closed without completing" in place
