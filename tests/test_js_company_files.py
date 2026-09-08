"""Files: host-roots findability, path-open UX, and visible open-folder errors.

Re-pointed in Phase 3B from company-files.js to places/files/. Every assertion
below guards the property it guarded before; where the spelling had to change,
the replacement is named in a comment beside it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
FILES = JS / "places" / "files"

# The order the Files harness evaluates its modules in.
FILES_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "gates.js",
    JS / "core" / "overlays.js",
    JS / "shell" / "places.js",
    FILES / "file-content.js",
    FILES / "file-form.js",
    FILES / "file-ops.js",
    FILES / "file-viewer.js",
    FILES / "files-data.js",
    FILES / "host-roots.js",
    # The Files place's folder opener delegates the 409 handler prompt to the
    # desk's, so the desk module has to be loaded before it.
    JS / "context" / "desk-opener.js",
    FILES / "folder-opener.js",
    FILES / "file-grid.js",
    FILES / "file-actions.js",
    FILES / "files-toolbar.js",
    FILES / "files-place.js",
]


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_company_files_exposes_same_host_roots_setting() -> None:
    """The allowlist is reachable from Files and writes the same setting.

    The dock-era ids (cf-host-roots-btn / cf-host-roots-input) are gone with the
    markup strings that carried them; the controls they named are asserted by
    what they say and what they write instead, which is what the operator and
    the server actually see.
    """
    roots = _read("places/files/host-roots.js")
    assert "Add host folder" in roots
    assert "Manage host folders" in roots
    assert "id: 'host-roots-input'" in roots
    assert "workspace_host_roots" in roots
    assert "This is not a full host mount" in roots
    assert (
        "`/api/settings/workspace_host_roots?value=${encodeURIComponent(value)}&category=cli_policy`"
        in roots
    )
    # apiFetchOk is not injectable, so the guard it provided is written out:
    # the response is checked before the panel closes and before onSaved runs,
    # which is the property "a save UI cannot flash success" stands for.
    submit = roots.split("onSubmit: async () => {", 1)[1].split("},", 1)[0]
    assert "if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));" in submit
    assert submit.index("if (!res.ok)") < submit.index("panel.close()")
    assert submit.index("if (!res.ok)") < submit.index("onSaved()")
    assert "SettingsView.open('cli-policy'" in roots

    # Two ways in, as before: the toolbar button and the workspace-note bar.
    assert "'Host folders'" in _read("places/files/files-toolbar.js")
    assert "BossModHostRoots.buttonLabel(roots)" in _read("places/files/file-grid.js")


def test_company_files_path_open_uses_api_kind_not_dot_heuristic() -> None:
    place = _read("places/files/files-place.js")
    data = _read("places/files/files-data.js")
    assert "named.includes('.')" not in place
    assert "includes('.') && !named.endsWith('/')" not in place
    assert "async function openNamedPath(" in place
    assert "payload.kind === 'file'" in place
    assert "openViewer(payload.path || named)" in place
    # The server answers what a path IS; the client never guesses from the name.
    assert "whose `kind` says whether this" in data
    # A refused path is shown, not logged: the banner and the setter that fills it.
    assert "class: 'files-error'" in _read("places/files/file-grid.js")
    assert "function setError(" in place
    assert "setError((err && err.message) || 'Could not open that path')" in place


def test_company_files_open_folder_errors_are_visible() -> None:
    """An open that failed must say so, and say what the host said.

    company-files.js logged the host's own refusal and showed one generic
    sentence; the port keeps the server's message, which is where "outside the
    allowed workspace roots" comes from.
    """
    opener = _read("places/files/folder-opener.js")
    assert "onError" in opener
    assert "describeFailure: async (res) => (await BossModFileOps.readApiError(res))" in opener
    assert "FAILURE_COPY = 'Failed to open folder'" in opener
    # The place hands the banner in as onError, so nothing is console-only.
    place = _read("places/files/files-place.js")
    assert "BossModFolderOpener.openFolder({" in place
    assert "onError: setError" in place

    # The one error formatter, reached rather than reimplemented.
    ops = _read("places/files/file-ops.js")
    assert "window.BossModApi.formatError(payload, res.status)" in ops

    # The handler prompt and its settings write are the desk opener's, shared
    # rather than copied — the duplication Phase 3B was asked to resolve.
    desk = _read("context/desk-opener.js")
    assert (
        "'/api/settings/desktop_open_folder_handler'\n"
        "                        + `?value=${encodeURIComponent(chosen)}&category=advanced`"
        in desk
    )
    assert "if (!saved.ok) {" in desk
    assert "onError('Could not save that folder opener.');" in desk
    assert "BossModDeskOpener.reveal({" in opener


def test_settings_can_open_cli_policy_host_roots() -> None:
    view = _read("settings/settings-view.js")
    assert "function open(sectionId, options)" in view
    assert "CliPolicySection.render(content, pendingOptions)" in view
    section = _read("settings/cli-policy/section.js")
    assert "async function render(el, options)" in section
    assert "options.focusKey" in section
    # Phase 3C split the Settings tab out of the section. The deep link now has
    # one more hop — section -> policy-settings.js — so the hop itself is
    # asserted; without it the split could drop the focus key and every
    # remaining assertion here would still pass.
    assert "BossModCliPolicySettings.renderSettingsTab(content, { takeFocusKey })" in section
    settings_tab = _read("settings/cli-policy/policy-settings.js")
    assert "renderSettingsTab(el, { takeFocusKey })" in settings_tab
    assert "const focusKey = takeFocusKey();" in settings_tab
    assert '`[data-setting-card="${focusKey}"]`' in settings_tab
    assert "data-setting-card=" in settings_tab
    assert "Host workspace roots can also be added from Company Files" in settings_tab


def test_company_files_named_path_harness() -> None:
    harness = Path(__file__).resolve().parent / "js_company_files_harness.cjs"
    result = subprocess.run(
        ["node", str(harness)] + [str(path) for path in FILES_MODULES],
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
