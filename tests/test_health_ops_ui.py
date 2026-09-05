"""HA-OPS-P1-01 / image-preview auth / HA-PROD-P2-01 / HA-OPS-P2-01 source checks."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"


def test_no_model_banner_and_send_disabled_in_ui() -> None:
    html = HTML.read_text(encoding="utf-8")
    app = (JS / "app.js").read_text(encoding="utf-8")
    chat = (JS / "agent-context.js").read_text(encoding="utf-8")

    assert 'id="no-model-banner"' in html
    assert "Connect a model in Settings" in html
    assert "refreshModelAvailability" in app
    assert "apiFetch('/api/connections'" in app
    assert "applyNoModelBanner" in app
    assert "function applyChatSendState(" in chat
    assert "sendBtn.disabled = !allowed" in chat
    assert "input.disabled = !allowed" in chat
    assert "BossModApp.hasUsableModel()" in chat


def test_company_image_preview_uses_authenticated_blob_url() -> None:
    viewer = (JS / "company-file-viewer.js").read_text(encoding="utf-8")
    client = (JS / "api-client.js").read_text(encoding="utf-8")

    assert "function apiFetchBlobUrl(" in client
    assert "URL.createObjectURL(blob)" in client
    assert 'src="/api/company/files/raw' not in viewer
    assert "apiFetchBlobUrl" in viewer or "fetchBlobUrl" in viewer
    assert "revokeObjectURL" in viewer
    assert "loadAuthenticatedImage" in viewer


def test_walk_receipts_stay_visible_when_system_toggle_off() -> None:
    chat = (JS / "agent-context.js").read_text(encoding="utf-8")
    assert "function isWalkReceipt(" in chat
    assert "notification_kind === 'receipt'" in chat
    assert "|| isWalkReceipt(msg)" in chat


def test_pyproject_drops_unused_duckdb_and_twilio() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "duckdb" not in text
    assert "twilio" not in text
    assert "[project.optional-dependencies]" not in text


def test_desktop_uses_recorded_pid_not_pkill() -> None:
    rust = (ROOT / "desktop" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "pkill" not in rust
    assert "fn stop_recorded_backend(" in rust
    assert ".bossmod-backend.pid" in rust
    assert "is_recorded_backend" in rust


def test_no_bare_company_raw_img_src_in_app_js() -> None:
    pattern = re.compile(r"""<img[^>]+src=["']/api/""")
    for path in JS.rglob("*.js"):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), path.name
