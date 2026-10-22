"""The shipped Lucide subset carries every icon the UI names, and only those.

index.html no longer loads the full vendored bundle (vendor/lucide.min.js,
1,700+ icons, ~358 KB). It loads vendor/lucide.subset.js, which
scripts/build_lucide_subset.cjs generates from it. These tests are the drift
guard that replaced "everything is always there": a module that starts using a
new icon without re-running the generator fails here, not as a blank glyph.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import get_args

from core.extensions.manifest import LiveViewIcon

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
TEMPLATES = ROOT / "ui" / "templates"
SUBSET = JS / "vendor" / "lucide.subset.js"
FULL = JS / "vendor" / "lucide.min.js"
GENERATOR = ROOT / "scripts" / "build_lucide_subset.cjs"
HTML = TEMPLATES / "index.html"
LIVE_STATUS = JS / "extensions" / "live-status.js"
# live-status.js `ICONS`, as written: a frozen array of quoted literals.
LIVE_VIEW_ICONS = re.compile(r"const ICONS = Object\.freeze\(\[([^\]]*)\]\);")

# Written independently of the generator's patterns on purpose: a blind spot
# shared by the two would let a name through both.
#
# A `data-lucide` value in markup (`data-lucide="x"`), never an interpolation.
MARKUP_NAME = re.compile(r'data-lucide="([^"$]+)"')
# A `data-lucide` value in an h() attribute object, up to the next `,` or `}`:
# a literal, or a ternary whose branches are literals.
OBJECT_VALUE = re.compile(r"""['"]data-lucide['"]\s*:\s*([^,}\n]+)""")
# The data maps: `icon: 'x'` or `icon: cond ? 'a' : 'b'`.
ICON_PROPERTY = re.compile(r"\bicon\s*:\s*([^,}\n]+)")
QUOTED = re.compile(r"""'([^']*)'|"([^"]*)\"""")

# Every way a name could be BUILT rather than written: a literal joined to
# something, or a template literal with an interpolation beside other text.
BUILT_NAME = (
    re.compile(r'data-lucide="[^"$]+\$\{'),                 # data-lucide="circle-${x}"
    re.compile(r'data-lucide="\$\{[^}]*\}[^"]+"'),            # data-lucide="${x}-off"
    re.compile(r"""['"]data-lucide['"]\s*:\s*`"""),           # 'data-lucide': `…`
    re.compile(r"""['"]data-lucide['"]\s*:[^,}\n]*\+"""),     # 'data-lucide': 'a-' + b
    re.compile(r"\bicon\s*:\s*`"),                             # icon: `…`
    re.compile(r"\bicon\s*:[^,}\n]*['\"]\s*\+|\bicon\s*:[^,}\n]*\+\s*['\"]"),  # icon: 'a-' + b
)


def _icon_key(name: str) -> str:
    """The vendor's kebab-to-Pascal rule (core/icons.js `iconKey`)."""
    return re.sub(
        r"(\w)(\w*)(_|-|\s*)",
        lambda match: match.group(1).upper() + match.group(2).lower(),
        name,
    )


def _sources() -> dict[str, str]:
    """Every app module and template, keyed by repo-relative path."""
    files = [p for p in sorted(JS.rglob("*.js")) if "vendor" not in p.relative_to(JS).parts]
    files += sorted(TEMPLATES.rglob("*.html"))
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in files}


def _subset_keys() -> set[str]:
    """The icon keys the shipped subset defines, read off the generated file."""
    return set(re.findall(r'^"([A-Za-z0-9]+)":\[', SUBSET.read_text(encoding="utf-8"), re.M))


def _declared_names() -> dict[str, list[str]]:
    """name -> the places it is declared as an icon."""
    found: dict[str, list[str]] = {}

    def add(name: str, where: str) -> None:
        found.setdefault(name, []).append(where)

    for relative, text in _sources().items():
        for number, line in enumerate(text.splitlines(), 1):
            where = f"{relative}:{number}"
            for match in MARKUP_NAME.finditer(line):
                add(match.group(1), where)
            for pattern in (OBJECT_VALUE, ICON_PROPERTY):
                for match in pattern.finditer(line):
                    for single, double in QUOTED.findall(match.group(1)):
                        add(single or double, where)
    return found


def test_the_committed_subset_is_what_the_generator_writes() -> None:
    """Covers every statically written name, positional arguments included.

    The generator takes every quoted literal that is a lucide icon, so this is
    what catches `menuRow('desk-edit', 'pencil', …)` — a name no declared-position
    pattern can see.
    """
    result = subprocess.run(
        ["node", str(GENERATOR), "--check"],
        check=False, capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_every_declared_icon_name_is_in_the_subset() -> None:
    names = _declared_names()
    # A scanner that stops finding names proves nothing by being green.
    assert len(names) > 60, f"only {len(names)} declared icon names found — the scan broke"
    keys = _subset_keys()
    assert keys, "no icon keys read from the subset"
    missing = {
        name: places for name, places in names.items() if _icon_key(name) not in keys
    }
    assert missing == {}, (
        "icons the UI names that the shipped subset lacks — re-run "
        f"node scripts/build_lucide_subset.cjs, or fix the name: {missing}"
    )


def test_no_icon_name_is_built_at_runtime() -> None:
    """A name assembled from pieces is invisible to the generator.

    Write each variant out as a literal in a map instead, the way Phase 2 did
    for Tailwind classes.
    """
    offenders = []
    for relative, text in _sources().items():
        for number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in BUILT_NAME):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def _icon_entries(node: object, path: tuple[str, ...] = ()) -> list[tuple[str, object]]:
    """Every `(dotted key path, value)` in a manifest whose key mentions an icon."""
    found: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = (*path, key)
            if "icon" in key.lower():
                found.append((".".join(here), value))
            found.extend(_icon_entries(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_icon_entries(value, (*path, str(index))))
    return found


def test_extension_manifests_name_icons_only_from_the_host_vocabulary() -> None:
    """The generator does not read manifests, and does not have to.

    The one icon a manifest names is its live view's header glyph, and the
    schema holds it to the host's fixed vocabulary (core/extensions/manifest.py
    `LiveViewIcon`), whose names live-status.js writes out as literals — so
    they are in the subset whatever a manifest picks. An icon anywhere else, or
    a name outside the vocabulary, would reach the UI unscanned: this fails
    first so that decision is made on purpose.
    """
    vocabulary = set(get_args(LiveViewIcon))
    manifests = sorted((ROOT / "extensions").glob("*/manifest.json"))
    assert manifests, "no extension manifests found — the glob broke"
    named = 0
    for manifest in manifests:
        for where, value in _icon_entries(json.loads(manifest.read_text(encoding="utf-8"))):
            assert where == "live_view.icon", (manifest, where)
            assert value in vocabulary, (manifest, value)
            named += 1
    # A walk that stops finding the icons proves nothing by being green.
    assert named >= 2, f"only {named} live-view icons found in the manifests — the walk broke"


def test_the_live_view_icon_vocabulary_is_one_list_in_python_and_the_ui() -> None:
    """What the server lets a manifest name is exactly what the UI accepts.

    `LiveViewIcon` validates manifests; live-status.js `ICONS` is what the
    header will paint (anything else is logged and left out), written as
    literals so the generator keeps each in the subset. Adding an icon to one
    list and not the other either refuses a valid manifest's view in the UI
    or lets a manifest name a glyph the subset lacks.
    """
    match = LIVE_VIEW_ICONS.search(LIVE_STATUS.read_text(encoding="utf-8"))
    assert match, "ICONS is no longer a frozen literal list in live-status.js"
    ui_icons = tuple(single or double for single, double in QUOTED.findall(match.group(1)))
    assert ui_icons == get_args(LiveViewIcon)
    keys = _subset_keys()
    assert [name for name in ui_icons if _icon_key(name) not in keys] == []


def test_index_ships_the_subset_and_not_the_full_bundle() -> None:
    html = HTML.read_text(encoding="utf-8")
    subset_at = html.index("static_url('js/vendor/lucide.subset.js')")
    assert subset_at < html.index("static_url('js/core/icons.js')"), "the painter loads before its icons"
    assert "static_url('js/vendor/lucide.min.js')" not in html
    # The point of the exercise, held: the subset is a small fraction of the
    # bundle it is cut from.
    assert SUBSET.stat().st_size * 5 < FULL.stat().st_size
    assert len(_subset_keys()) * 5 < len(re.findall(r"\ba\.[A-Z]\w*=", FULL.read_text(encoding="utf-8")))
