"""core/icons.js — one painter, one scope, one pass.

Lucide's `createIcons` has no `nodes` option (the string does not appear in
the vendored 0.469 bundle) and scans `document`, and the SVG it builds keeps
`data-lucide`, so every painted icon matches the next scan. Nine call sites
passed `{ nodes: [el] }` and got a document-wide rebuild of every icon on
screen instead of a scoped paint. These tests hold the replacement in place:
the behaviour, in the harness, and the call shape, in the source.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js_icons_harness.cjs"

# Every module that paints, and the call it makes. `paint` is scoped to a root
# the module owns; `paintDocument` is the shell's sweep, kept where placeholders
# are built by modules that do not paint their own (conversation/composer.js
# builds two and paints none).
CALL_SITES = {
    "settings/settings-personalities.js": "BossModIcons.paint(container, 'settings-personalities')",
    "settings/settings-view.js": "BossModIcons.paint(nav, 'settings-view.renderNav')",
    "settings/settings-connections.js": "BossModIcons.paint(container, 'settings-connections')",
    "settings/settings-telegram.js": "BossModIcons.paint(el, 'settings-telegram')",
    "settings/cli-policy/shared.js": "BossModIcons.paint(root, 'cli-policy')",
    "core/consent-card.js": "BossModIcons.paint(container, 'consent-card')",
    "shell/responsive.js": "BossModIcons.paint(headerEl, 'responsive')",
    "shell/add-agent-menu.js": "BossModIcons.paint(menu.element, 'add-agent-menu')",
    "context/agent-form.js": "BossModIcons.paint(advancedToggle, 'agent-form.advanced')",
    "conversation/chrome.js": "BossModIcons.paintDocument('conversation-chrome')",
    "shell/roster.js": "BossModIcons.paintDocument('roster')",
    "shell/header.js": "BossModIcons.paintDocument('header')",
    "shell/thread-create.js": "BossModIcons.paintDocument('thread-create')",
    "shell/roster-people.js": "BossModIcons.paintDocument('roster-people')",
}


def _app_sources() -> dict[str, str]:
    """Every module in the tree except the vendored bundles."""
    return {
        path.relative_to(JS).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(JS.rglob("*.js"))
        if "vendor" not in path.parts
    }


def test_icons_module_paints_one_scope_once() -> None:
    """The behaviour, against the real vendored bundle and the shared fake DOM."""
    result = subprocess.run(
        [
            "node", str(HARNESS),
            str(JS / "core" / "icons.js"),
            str(JS / "vendor" / "lucide.min.js"),
            str(JS),
        ],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        # Painting one root leaves the icons outside it alone.
        "scopeIsReal": True,
        # The painted node survives the next pass, identity included.
        "secondPaintKeepsNodes": True,
        # #advanced-chevron keeps its id, its classes and its rotation.
        "advancedChevronSurvives": True,
        # An unknown name throws, names itself and the call site, and leaves
        # the tree unpainted rather than half-painted.
        "unknownIconThrows": True,
        "paintsTheRootItself": True,
        "detachedRootThrows": True,
        # The sweep still covers the document, and settles after one pass.
        "paintDocumentSweepsAndSettles": True,
        # A missing bundle is a broken build, and says so.
        "missingVendorThrows": True,
        # Every data-lucide name the app ships resolves in the icon set —
        # which is what makes throwing on an unknown one safe.
        "everyShippedNameResolves": True,
        "nameConversionMatchesIconSet": True,
        "unresolvable": [],
    }


def test_nothing_calls_lucide_createicons_any_more() -> None:
    """The phantom option, and the sweep it hid, are gone from the whole tree.

    `nodes` was never read. A call that passes it looks scoped, reads as
    scoped in review, and repaints every icon in the document — which is why
    the ban is on the call itself and not just on the option: the next person
    reaching for `createIcons` would reach for the same trap.
    """
    offenders = []
    for relative, text in _app_sources().items():
        if re.search(r"createIcons\s*\(", text):
            offenders.append(f"{relative} calls createIcons()")
        if re.search(r"createIcons\s*\(\s*\{[^}]*\bnodes\b", text):
            offenders.append(f"{relative} passes the phantom nodes option")
    assert offenders == [], "\n".join(offenders)


def test_the_vendor_bundle_is_read_in_one_place() -> None:
    """`window.lucide` is core/icons.js's business and nobody else's.

    The old call sites each guarded on `if (window.lucide)` and skipped
    painting when it was falsy — a silent fallback that leaves bare `<i>`
    placeholders on screen and reads as a CSS bug. The painter throws instead,
    and it is the only module that looks.
    """
    readers = [
        relative for relative, text in _app_sources().items()
        if "window.lucide" in text
    ]
    assert readers == ["core/icons.js"], readers
    icons = (JS / "core" / "icons.js").read_text(encoding="utf-8")
    assert "if (window.lucide)" not in icons
    assert "throw new Error(" in icons


def test_every_painter_names_itself_at_the_call_site() -> None:
    """A context string per call, because the error message is the whole point.

    "unknown lucide icon" with no call site is a bug report nobody can act on.
    """
    sources = _app_sources()
    for relative, call in CALL_SITES.items():
        assert relative in sources, f"{relative} is gone; update this list"
        assert call in sources[relative], f"{relative} no longer makes: {call}"

    # And nobody paints without saying who they are.
    for relative, text in sources.items():
        for match in re.finditer(r"BossModIcons\.paint(?:Document)?\(([^)]*)\)", text):
            assert "'" in match.group(1), f"{relative} paints with no context: {match.group(0)}"


def test_the_advanced_chevron_is_looked_up_after_it_is_painted() -> None:
    """Painting replaces the node, so a reference taken first goes stale.

    The Advanced disclosure rotates `#advanced-chevron` on every click. The
    placeholder it rotates is an `<i data-lucide="chevron-right">` that the
    paint swaps for an SVG — a different object — so the form has to look the
    chevron up on the far side of the paint or spend the rest of the session
    styling an element that left the document.

    Held here rather than in the form's own harness because that harness works
    on markup strings and never parses a form; the DOM half of the promise —
    that the painted SVG carries the id and takes the style write — is
    js_icons_harness.cjs's `advancedChevronSurvives`.
    """
    source = (JS / "context" / "agent-form.js").read_text(encoding="utf-8")
    painted_at = source.index("BossModIcons.paint(advancedToggle, 'agent-form.advanced')")
    looked_up_at = source.index("form.querySelector('#advanced-chevron')")
    assert painted_at < looked_up_at, (
        "#advanced-chevron is looked up before the paint that replaces it — "
        "the disclosure would rotate a detached node"
    )


def test_index_loads_the_painter_before_anything_paints() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "static_url('js/core/icons.js')" in html, "the painter is not loaded"
    painter_at = html.index("static_url('js/core/icons.js')")
    for relative in CALL_SITES:
        needle = f"static_url('js/{relative}')"
        assert needle in html, f"{relative} paints but is not loaded by index.html"
        assert painter_at < html.index(needle), f"{relative} loads before the painter"
