"""HA-OPS-P1-02 — UI chrome is vendored, CSP matches."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_index_does_not_load_cdn_chrome() -> None:
    html = (ROOT / "ui" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in html
    assert "unpkg.com" not in html
    assert "jsdelivr.net" not in html
    assert "static_url('js/vendor/tailwindcss.js')" in html
    assert "static_url('js/vendor/lucide.min.js')" in html
    assert "static_url('js/vendor/split.min.js')" in html


def test_vendor_chrome_assets_exist() -> None:
    vendor = ROOT / "ui" / "static" / "js" / "vendor"
    for name in ("tailwindcss.js", "lucide.min.js", "split.min.js"):
        path = vendor / name
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 1000


def test_tauri_csp_is_self_only() -> None:
    raw = (ROOT / "desktop" / "tauri.conf.json").read_text(encoding="utf-8")
    config = json.loads(raw)
    csp = config["app"]["security"]["csp"]
    assert "cdn.tailwindcss.com" not in csp
    assert "unpkg.com" not in csp
    assert "script-src 'self' 'unsafe-eval'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
