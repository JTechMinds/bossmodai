"""HA-OPS-P1-02 — UI chrome is vendored, CSP matches."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ui" / "static"

# Dropped in Phase 4. Its only caller was app.js, deleted in the same phase;
# the Board uses a slide-over rather than the table/detail divider Split.js was
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
RETIRED_VENDOR = (
    "split.min.js",
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

# The three the app cannot render without. Split.js was the fourth.
CHROME_ASSETS = ("tailwindcss.js", "lucide.min.js", "marked.min.js",
                 "highlight.min.js")


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
    assert "static_url('js/vendor/tailwindcss.js')" in html
    assert "static_url('js/vendor/lucide.min.js')" in html
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
    # Five: Tailwind, Lucide, marked, highlight.js and the hljs stylesheet.
    # An exact count rather than a floor, because a floor is what let eleven
    # redundant language packs sit here inflating it.
    assert len(references) == 5, f"{len(references)} vendored references: {references}"

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
    assert "script-src 'self' 'unsafe-eval'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
