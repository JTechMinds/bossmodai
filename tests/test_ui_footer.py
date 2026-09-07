"""shell/footer.js + shell/banners.js — reconnect notice and the no-model banner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js_footer_harness.cjs"

MODULES = [
    ("core", "dom.js"), ("core", "store.js"), ("core", "bus.js"),
    ("shell", "footer.js"), ("shell", "banners.js"),
]


def test_resync_shows_reconnected_then_returns_to_connected() -> None:
    """A dropped socket loses broadcasts silently; the footer must say so."""
    args = ["node", str(HARNESS)] + [str(JS / d / f) for d, f in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "resyncAnnouncesThenClears": True,
        "agentCountFollowsRoster": True,
        "uptimeIntervalClearedOnTeardown": True,
        "bannersToggleFromState": True,
        "disposersDrain": True,
    }


def test_no_model_banner_keeps_its_exact_copy() -> None:
    """The markup stays in index.html; banners.js only toggles it."""
    html = HTML.read_text(encoding="utf-8")
    assert 'id="no-model-banner"' in html
    assert "Connect a model in Settings" in html
    assert 'id="runtime-pause-banner"' in html

    banners = (JS / "shell" / "banners.js").read_text(encoding="utf-8")
    assert "apiFetch('/api/connections'" in banners, (
        "banners.js takes model availability over from app.js"
    )
    assert "refreshModelAvailability" in banners
