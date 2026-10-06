"""HA-OPS-P1-02 — UI chrome is vendored, CSP matches."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ui" / "static"

# Dropped in Phase 4. Its only caller was app.js, deleted in the same phase;
# the Board uses a modal rather than the table/detail divider Split.js was
# retained for (spec 3.1, superseded), so it was a vendored library with zero
# consumers. Asserted absent from BOTH the manifest and the disk, so it cannot
# creep back in as a script tag or as an unreferenced file.
#
# The eleven highlight.js language packs are the second kind of dead weight,
# and the kind the rule below could not see: they WERE referenced, so
# "nothing on disk is unreferenced" passed while they did nothing at all.
# highlight.min.js is the `common` build and already registered every one of
# them at the same 11.11.1 version — measured, loading all eleven took the
# language count from 36 to 36 and changed no highlighting output on any of
# the eleven grammars, under both explicit and auto-detected highlighting.
# 68KB and eleven requests. Named here so re-adding one is a test failure
# rather than a judgement call.
#
# tailwindcss.js, the Tailwind Play compiler, is the third: a 407KB runtime
# whose MutationObserver re-scanned the whole DOM on every class or childList
# change. Its output is now pre-built into css/tailwind.generated.css by
# scripts/build_tailwind.sh, so the compiler must not return.
RETIRED_VENDOR = (
    "split.min.js",
    "tailwindcss.js",
    "hljs-lang-bash.min.js",
    "hljs-lang-css.min.js",
    "hljs-lang-ini.min.js",
    "hljs-lang-javascript.min.js",
    "hljs-lang-json.min.js",
    "hljs-lang-markdown.min.js",
    "hljs-lang-python.min.js",
    "hljs-lang-sql.min.js",
    "hljs-lang-typescript.min.js",
    "hljs-lang-xml.min.js",
    "hljs-lang-yaml.min.js",
)

# The three the app cannot render without. Split.js was the fourth; the
# Tailwind Play compiler left too, replaced by the pre-built
# css/tailwind.generated.css, which is app CSS rather than a vendored asset.
# Lucide ships as the generated subset (scripts/build_lucide_subset.cjs);
# highlight.js is loaded on first use but is still chrome the app needs.
CHROME_ASSETS = ("lucide.subset.js", "marked.min.js", "highlight.min.js")

# On disk in vendor/ on purpose and loaded by nothing: the full Lucide bundle
# is the INPUT scripts/build_lucide_subset.cjs reads to generate
# lucide.subset.js. It is the one exception to "vendored but nothing loads it",
# and it must never be loaded again — that was 358 KB parsed per launch.
GENERATOR_INPUTS = ("lucide.min.js",)

# Vendored scripts that are not script tags: core/lazy-script.js loads each one
# on first use from a `bossmod-lazy-script` meta carrying its URL.
LAZY_SCRIPTS = {"highlight": "highlight.min.js", "tabulator": "tabulator.min.js"}


def _html() -> str:
    return (ROOT / "ui" / "templates" / "index.html").read_text(encoding="utf-8")


def _vendor_references() -> list[str]:
    """Every vendored asset index.html asks for, css and js alike."""
    return [
        ref for ref in re.findall(r"static_url\('([^']+)'\)", _html())
        if "vendor/" in ref
    ]


def test_index_does_not_load_cdn_chrome() -> None:
    html = _html()
    for cdn in ("cdn.tailwindcss.com", "unpkg.com", "jsdelivr.net",
                "cdnjs.cloudflare.com", "fonts.googleapis.com"):
        assert cdn not in html, f"index.html reaches for {cdn}"
    assert "static_url('css/tailwind.generated.css')" in html
    assert "static_url('js/vendor/lucide.subset.js')" in html
    for name in GENERATOR_INPUTS:
        assert f"static_url('js/vendor/{name}')" not in html, (
            f"{name} is a generator input and must not be loaded"
        )
    for name in RETIRED_VENDOR:
        assert name not in html, f"{name} is loaded again"


def test_vendor_chrome_assets_exist() -> None:
    """The offline guarantee: every vendored asset is referenced AND present.

    Widened in Phase 4 from a hand-written list of three. Dropping Split.js
    from a list of names would have left the remaining vendored assets — marked,
    highlight.js, the hljs stylesheet — covered by nothing at all, which is how
    this test could have gone quiet while still passing. Both directions are
    asserted: nothing is referenced that is not on disk (a blank UI in the
    packaged app), and nothing is on disk that is not referenced (dead weight
    nobody notices, which is exactly what Split.js became).

    Neither direction catches an asset that is referenced AND loads AND does
    nothing, which is what the eleven language packs were. That one needs a
    human to measure, and the answer is recorded in RETIRED_VENDOR.
    """
    references = _vendor_references()
    # Six: the Lucide subset, marked, highlight.js (lazy), the hljs stylesheet,
    # and Tabulator's script (lazy) and base stylesheet (core/data-table.js).
    # Tailwind was the seventh until its runtime compiler was replaced by a
    # pre-built sheet. An exact count rather than a floor, because a floor is
    # what let eleven redundant language packs sit here inflating it.
    assert len(references) == 6, f"{len(references)} vendored references: {references}"

    for ref in references:
        path = STATIC / ref
        assert path.is_file(), f"index.html references {ref}, which is not on disk"
        # A stub file would satisfy "is_file" and break the app at runtime.
        # The chrome keeps the 1KB floor it always had; the hljs stylesheet is
        # the one remaining asset legitimately under it (1,315 bytes).
        floor = 1000 if path.name in CHROME_ASSETS else 200
        assert path.stat().st_size > floor, f"{ref} is suspiciously small"

    referenced = {(STATIC / ref).resolve() for ref in references}
    for directory in (STATIC / "js" / "vendor", STATIC / "css" / "vendor"):
        for path in sorted(directory.iterdir()):
            if path.suffix not in {".js", ".css"}:
                continue  # VENDOR_SOURCES.md records provenance, not code
            if path.name in GENERATOR_INPUTS:
                assert path.resolve() not in referenced, f"{path.name} is loaded again"
                continue
            assert path.resolve() in referenced, (
                f"{path.name} is vendored but nothing loads it"
            )

    for name in RETIRED_VENDOR:
        assert not (STATIC / "js" / "vendor" / name).exists(), f"{name} is back on disk"


def test_tauri_csp_is_self_only() -> None:
    raw = (ROOT / "desktop" / "tauri.conf.json").read_text(encoding="utf-8")
    config = json.loads(raw)
    csp = config["app"]["security"]["csp"]
    assert "cdn.tailwindcss.com" not in csp
    assert "unpkg.com" not in csp
    # 'unsafe-eval' was there for the Tailwind Play compiler, which is gone
    # (css/tailwind.generated.css is pre-built). Nothing left evaluates code
    # from strings, so the webview must not allow it.
    assert "'unsafe-eval'" not in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_lazy_vendor_scripts_are_metas_not_script_tags() -> None:
    """highlight.js and Tabulator are parsed only when something needs them.

    Each is named once, by a `bossmod-lazy-script` meta that core/lazy-script.js
    reads its URL from, and by no script tag — a script tag would put the
    575 KB the two weigh back on every launch.
    """
    html = _html()
    script_tags = re.findall(r"<script\b[^>]*\bsrc=\"\{\{ static_url\('([^']+)'\) \}\}\"", html)
    for library, name in LAZY_SCRIPTS.items():
        meta = (
            f'<meta name="bossmod-lazy-script" data-library="{library}" '
            f"content=\"{{{{ static_url('js/vendor/{name}') }}}}\">"
        )
        assert html.count(meta) == 1, f"{name} is not declared lazily exactly once"
        assert f"js/vendor/{name}" not in script_tags, f"{name} is a script tag again"
        assert html.count(f"js/vendor/{name}") == 1, f"{name} is referenced more than once"
