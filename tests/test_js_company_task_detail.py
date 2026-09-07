"""Company task detail: deliverable links must open files and host folders."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_deliverable_cards_keep_original_path_and_agent_id() -> None:
    source = _read("company-task-detail.js")
    assert "function openDeliverablePath(path, agentId)" in source
    assert "data-agent-id=" in source
    assert "isAgentDeskPath(target)" in source
    assert "/api/agents/${encodeURIComponent(agentId)}/desk?path=" in source
    assert "/api/company/files?path=" in source
    assert "/api/company/files/open-folder" in source
    assert "payload.kind === 'file'" in source
    assert "CompanyFileViewer.open" in source


def test_deliverable_open_does_not_remap_host_paths_through_me() -> None:
    source = _read("company-task-detail.js")
    assert "'/agents/' + storageKey" not in source
    assert "virtualPath.slice(3)" not in source


def test_file_viewer_open_surfaces_load_failures() -> None:
    source = _read("company-file-viewer.js")
    assert "throw err;" in source
