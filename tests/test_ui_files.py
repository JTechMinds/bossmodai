"""The Files place: one heading, a keyboard-reachable menu, and no soft edges.

Spec 6.4. Files carries more small capabilities than any other place — a
context menu, a host-roots consent panel, a folder-opener prompt, global search,
named-path open, authenticated image blobs — so the assertions that matter most
here are the ones that stop any of them being softened on the way across.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
FILES = JS / "places" / "files"

# The consent copy, byte-identical. This panel grants agents read and write
# access to directories outside the company workspace, so what it promises and
# what it refuses is the operator's only statement of that boundary.
HOST_ROOTS_COPY = (
    "Optional extra directories a named absolute path may open, read, or "
    "edit. Writes the same allowlist as Settings → CLI Policy → Host workspace roots. "
    "This is not a full host mount. /, /etc, /proc, /sys, /dev, and /root are rejected."
)


def _read(name: str) -> str:
    return (FILES / name).read_text(encoding="utf-8")


def _modules() -> list[Path]:
    return sorted(FILES.glob("*.js"))


def test_files_registers_itself_and_renders_one_h1() -> None:
    """The place contract, and the four states behind it.

    navigate() focuses the new place's single h1; two of them, or none, and
    keyboard users land somewhere arbitrary.
    """
    place = _read("files-place.js")
    assert "BossModPlaces.register('files', BossModFilesPlace)" in place
    assert "resync()" in place
    assert "disposers.push(ctx.bus.subscribe('resync', () => BossModFilesPlace.resync()))" in place
    # The heading lives in the frame the grid builds, and there is exactly one.
    grid = _read("file-grid.js")
    assert grid.count("h('h1'") == 1
    assert "h('h1', { tabindex: '-1' }, 'Files')" in grid
    assert place.count("h('h1'") == 0

    # Four states (spec 8.3): a skeleton shaped like the list, an empty state
    # that says what would be here, an error state that offers a retry, and the
    # listing itself.
    assert "function renderSkeleton()" in grid
    assert "function renderError(" in grid
    assert "'Try again'" in grid
    assert "class: 'place-empty'" in grid
    assert "No files in this directory." in grid
    assert "function renderList(" in grid

    # Unmount drains everything that could outlive the place.
    unmount = place.split("unmount() {", 1)[1]
    assert "disposers.splice(0).forEach((off) => off());" in unmount
    assert "load.next();" in unmount
    assert "rowMenu.close();" in unmount
    assert "toolbar.destroy();" in unmount
    assert "BossModFileViewer.close();" in unmount


def test_context_menu_is_keyboard_reachable_and_escapable() -> None:
    """SC 2.1.1: right-click is the second way in, never the only one.

    Delete is behind this menu. Every row carries a real button that opens it,
    arrow keys move within it, Escape closes it, and focus returns to the
    button that opened it rather than being dropped on <body>.
    """
    grid = _read("file-grid.js")
    # A named button per row, not a right-click-only target.
    assert "class: 'file-entry-menu'" in grid
    assert "'aria-haspopup': 'menu'" in grid
    assert "'aria-label': `Actions for ${name}`" in grid
    assert "onMenu(entry, menu, null)" in grid, "the ⋯ button must open the same menu"
    # Right-click still works, and is additive.
    assert "row.addEventListener('contextmenu'" in grid

    actions = _read("file-actions.js")
    assert "role: 'menu'" in actions
    assert "role: 'menuitem'" in actions
    assert "event.key === 'Escape'" in actions
    assert "event.key === 'ArrowDown'" in actions
    assert "event.key === 'ArrowUp'" in actions
    assert "items[0].focus();" in actions
    close = actions.split("function close() {", 1)[1].split("\n        }", 1)[0]
    assert "document.removeEventListener('keydown', onKeydown);" in close
    assert "document.removeEventListener('click', onDocumentClick);" in close
    assert "anchor.focus();" in close, "closing must return focus to the trigger"
    assert "anchor.setAttribute('aria-expanded', 'false');" in close


def test_destructive_file_actions_confirm_through_a_modal() -> None:
    """No native dialog anywhere under places/files/, and no silent failures.

    window.confirm cannot be styled, cannot be tested, and blocks the event
    loop. Delete is a question with no input, so it is a createModal with
    Cancel focused; Rename is a form the operator types into, so it is
    file-form.js's slide-over — a modal action always closes, and a rename that
    closed on failure would throw away the name they typed. Both trap focus,
    answer Esc, and restore focus to their opener.
    """
    for path in _modules():
        source = path.read_text(encoding="utf-8")
        for banned in ("window.confirm", "window.alert", "window.prompt"):
            assert banned not in source, f"{path.name} uses {banned}"
        assert not re.search(r"(?<![\w.])confirm\s*\(", source), f"{path.name} calls confirm()"
        assert not re.search(r"(?<![\w.])alert\s*\(", source), f"{path.name} calls alert()"

    ops = _read("file-ops.js")
    delete_body = ops.split("function showDeleteDialog(", 1)[1]
    assert "BossModOverlays.createModal({" in delete_body
    assert "title: 'Delete'," in delete_body
    assert "tone: 'danger'" in delete_body
    assert "'This cannot be undone.'" in delete_body
    # Cancel is last, which is the action createModal focuses on open.
    assert delete_body.index("label: 'Delete'") < delete_body.index("label: 'Cancel'")
    # A failed delete is reported, never left in the console.
    assert "onError(`Could not delete ${name}: `" in delete_body

    rename = ops.split("function showRenameDialog(", 1)[1].split(
        "function showDeleteDialog(", 1
    )[0]
    assert "FORM.openFormPanel({" in rename
    form = _read("file-form.js")
    assert "BossModOverlays.slideOver({ title, body: form })" in form
    # The panel stays open on failure with the operator's text intact.
    assert "error((err && err.message) || 'The request failed.');" in form

    # The host-roots panel is consent-bearing and goes through the same overlay.
    assert "FORM.openFormPanel({" in _read("host-roots.js")
    # The folder-opener prompt is a modal, shared with the desk rather than
    # copied: two settings-writing prompts is the duplication this phase deletes.
    assert "BossModDeskOpener.reveal({" in _read("folder-opener.js")
    assert "BossModOverlays.createModal(" in (
        JS / "context" / "desk-opener.js"
    ).read_text(encoding="utf-8")


def test_host_roots_copy_is_unchanged() -> None:
    """Load-bearing consent copy, carried across byte for byte."""
    source = _read("host-roots.js")
    joined = " ".join(
        re.findall(r"'([^']*)'", source.split("const DESCRIPTION =", 1)[1].split(";", 1)[0])
    ).replace("  ", " ")
    assert joined == HOST_ROOTS_COPY
    assert "const TITLE = 'Host folders';" in source
    assert "const FIELD_LABEL = 'Allowlisted host folders';" in source
    assert "const PLACEHOLDER = '/home/you/src';" in source
    assert "const ADD_LABEL = 'Add host folder';" in source
    assert "const MANAGE_LABEL = 'Manage host folders';" in source
    # The bare `catch {}` that hid a failed read of the saved value did not
    # survive: saving over an allowlist you could not read revokes access by
    # accident, so the failure is on screen.
    assert "/* keep the list already shown" not in source
    assert "console.error('[host-roots] could not read the saved allowlist', err);" in source
    assert "panel.error(STALE_COPY);" in source


def test_files_modules_stay_focused() -> None:
    """Modular, DRY, no embedded markup — asserted, not trusted.

    File names and agent output flow through these views. h() escapes by
    construction; a template literal does not, so nothing here may build markup
    from a string. The one place HTML text becomes nodes is markdown, which is
    parsed (inertly) rather than assigned, and it is named here so a second one
    cannot appear quietly.
    """
    for path in _modules():
        source = path.read_text(encoding="utf-8")
        lines = len(source.splitlines())
        assert lines < 300, f"{path.name} is {lines} lines"
        assert "innerHTML" not in source, f"{path.name} builds markup from a string"
        assert "insertAdjacentHTML" not in source, f"{path.name} injects markup"
        assert "outerHTML" not in source, f"{path.name} injects markup"
        # Dependencies arrive by injection. A `typeof X !== 'undefined'` probe
        # is how app.js silently no-opped when a module failed to load, and it
        # is banned outright; `typeof value === 'string'` on a payload field is
        # a different thing and stays.
        assert "'undefined'" not in source, f"{path.name} probes for a global"
        assert "window.BossModDom" not in source
        assert "window.BossModOverlays" not in source

    parsers = [p.name for p in _modules() if "DOMParser" in p.read_text(encoding="utf-8")]
    assert parsers == ["file-content.js"], f"HTML is parsed in more than one place: {parsers}"

    # Formatters come from the shared module; no private copies.
    for name in ("formatFileSize", "formatRelativeTime"):
        assert f"function {name}(" not in _read("file-grid.js")
    assert "BossModFormat.formatFileSize(" in _read("file-grid.js")
    assert "BossModFormat.formatFileSize(" in _read("file-viewer.js")
