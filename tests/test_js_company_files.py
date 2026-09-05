"""Company Files host-roots findability and path-open UX."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_company_files_exposes_same_host_roots_setting() -> None:
    source = _read("company-files.js")
    assert "cf-host-roots-btn" in source
    assert "Add host folder" in source
    assert "Manage host folders" in source
    assert "cf-host-roots-input" in source
    assert "apiFetchOk(`/api/settings/workspace_host_roots?value=${encodeURIComponent(value)}&category=cli_policy`" in source
    assert "SettingsView.open('cli-policy'" in source
    assert "workspace_host_roots" in source
    assert "This is not a full host mount" in source


def test_company_files_path_open_uses_api_kind_not_dot_heuristic() -> None:
    source = _read("company-files.js")
    assert "named.includes('.')" not in source
    assert "includes('.') && !named.endsWith('/')" not in source
    assert "async function openNamedPath(" in source
    assert "payload.kind === 'file'" in source
    assert "CompanyFileViewer.open(payload.path || named)" in source
    assert "cf-action-error" in source
    assert "function setActionError(" in source


def test_company_files_open_folder_errors_are_visible() -> None:
    source = _read("company-files.js")
    assert "setActionError(err.message || 'Failed to open folder')" in source
    assert "BossModApi.formatError" in source
    assert "apiFetchOk(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(chosen)}&category=advanced`" in source


def test_settings_can_open_cli_policy_host_roots() -> None:
    view = _read("settings-view.js")
    assert "function open(sectionId, options)" in view
    assert "CliPolicySection.render(content, pendingOptions)" in view
    section = _read("cli-policy-section.js")
    assert "async function render(el, options)" in section
    assert "options.focusKey" in section
    assert "data-setting-card=" in section
    assert "Host workspace roots can also be added from Company Files" in section


def test_company_files_named_path_harness() -> None:
    harness = Path(__file__).resolve().parent / "js_company_files_harness.cjs"
    result = subprocess.run(
        [
            "node",
            str(harness),
            str(JS / "utils.js"),
            str(JS / "company-files.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["dottedDirOpenedViewer"] is False
    assert payload["fileOpenedViewer"] is True
    assert payload["deniedPathErrorVisible"] is True
    assert "outside" in payload["deniedPathError"].lower()
