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
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "function isWalkReceipt(" in chat
    assert "notification_kind === 'receipt'" in chat
    assert "function isHostPathConsent(" in chat
    assert "BossModUtils.isHostPathConsentMessage(msg)" in chat
    assert "|| isWalkReceipt(msg) || isHostPathConsent(msg)" in chat
    assert "host-path-consent-card" in chat
    assert "function renderHostPathConsentCard(" in utils
    assert "Allow once" in utils
    assert "Always allow" in utils
    assert "Deny" in utils


def test_pyproject_drops_unused_duckdb_and_twilio() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "duckdb" not in text
    assert "twilio" not in text
    assert "[project.optional-dependencies]" not in text


def test_desktop_uses_recorded_pid_not_pkill() -> None:
    rust = (ROOT / "desktop" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'Command::new("pkill")' not in rust
    assert ".arg(\"-f\")" not in rust
    assert "fn stop_recorded_backend(" in rust
    assert ".bossmod-backend.pid" in rust
    assert "is_recorded_backend" in rust
    assert "fn stop_backend_tree(" in rust
    assert "fn take_and_stop_backend(" in rust
    assert "libc::SIGTERM" in rust
    assert "process_group(0)" in rust
    assert "shutdown_runtime" in rust
    assert "handle_quit_signal" in rust
    assert "RunEvent::Exit" in rust


def test_living_docs_do_not_claim_pr2_is_open() -> None:
    """HA-OPS-P2-03: PR #2 (token + fail-closed Telegram) is on main."""
    arch = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    backlog = (ROOT / "docs" / "HEALTH_BACKLOG.md").read_text(encoding="utf-8")
    assert "cursor/sec-p0-01-p0-02-b82e`, open)" not in arch
    assert "Open branch `cursor/sec-p0-01-p0-02-b82e`" not in backlog
    assert "Those are live, not open" in backlog
    assert "Merged on `main`" in backlog
    assert "local API token" in arch
    assert "fail-closed" in arch
    assert "No auth on main" not in arch
    assert "empty allowlist = allow all" not in arch
    assert "2.3k-LOC router" not in arch
    assert "remaining open items" not in arch
    assert "every ID in this table is **shipped**" in backlog


def test_no_bare_company_raw_img_src_in_app_js() -> None:
    pattern = re.compile(r"""<img[^>]+src=["']/api/""")
    for path in JS.rglob("*.js"):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), path.name
