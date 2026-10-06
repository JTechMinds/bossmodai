"""highlight.js and Tabulator load on first use, from 'self', and fail out loud.

Neither is a script tag any more (see tests/test_offline_ui.py for the
template half). The behaviour runs in tests/js_lazy_script_harness.cjs: the
real core/lazy-script.js, then markdown, the file viewer and the data table
driven through the state a real launch starts in — neither library defined.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_lazy_script_harness.cjs"
MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "lazy-script.js",
    JS / "core" / "markdown.js",
    JS / "places" / "files" / "file-content.js",
    JS / "core" / "data-table.js",
]


def test_lazy_vendor_scripts() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), *(str(path) for path in MODULES)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        # The loader: one tag per library, from the template's meta, resolved
        # with the global the script defines.
        "injectsOneScriptFromTheMeta": True,
        "resolvesWithTheGlobal": True,
        "aDefinedGlobalInjectsNothing": True,
        # Failures reject with the library named; none is remembered.
        "aFailedLoadRejectsAndRemovesItsTag": True,
        "aFailedLoadIsRetried": True,
        "aScriptThatDefinesNothingRejects": True,
        # 'self' only, refused before anything is appended.
        "offOriginIsRefusedBeforeInjecting": True,
        "aMissingMetaRejectsNamingIt": True,
        # Markdown: only a fence that declares a language asks for hljs.
        "untaggedCodeLoadsNothing": True,
        "aDeclaredFenceLoadsHighlightJs": True,
        "theFenceIsHighlightedWhenItLands": True,
        "anUnknownLanguageIsDroppedOnceKnown": True,
        "aFailedHighlighterIsLogged": True,
        "aCodeFileLoadsHighlightJs": True,
        "aLoadedHighlighterIsUsedAtOnce": True,
        # The data table: loading state, then built; error state with a retry.
        "aTableLoadsTabulatorAndShowsLoading": True,
        "theTableIsBuiltWhenItLands": True,
        "aLaterTableBuildsAtOnce": True,
        "aFailedTableLoadShowsTheError": True,
        "tryAgainRetriesTheLoad": True,
        "aTableDestroyedWhileLoadingIsNeverBuilt": True,
    }


def test_nothing_else_injects_scripts() -> None:
    """core/lazy-script.js is the one place a script element is created."""
    offenders = [
        path.relative_to(JS).as_posix()
        for path in sorted(JS.rglob("*.js"))
        if "vendor" not in path.parts
        and "createElement('script')" in path.read_text(encoding="utf-8")
    ]
    assert offenders == ["core/lazy-script.js"], offenders
