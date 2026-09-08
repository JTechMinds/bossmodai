"""HA-OPS-P1-01 / image-preview auth / HA-PROD-P2-01 / HA-OPS-P2-01 source checks."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"


def test_no_model_banner_and_send_disabled_in_ui() -> None:
    """Re-pointed in Phase 2A: app.js is on disk but no longer loaded, so the
    old assertions were a false green. The banner is owned by shell/banners.js
    and Send disablement by conversation/composer.js."""
    html = HTML.read_text(encoding="utf-8")
    banners = (JS / "shell" / "banners.js").read_text(encoding="utf-8")
    composer = (JS / "conversation" / "composer.js").read_text(encoding="utf-8")

    assert 'id="no-model-banner"' in html
    assert "Connect a model in Settings" in html
    assert "refreshModelAvailability" in banners
    assert "apiFetch('/api/connections'" in banners
    assert "hasUsableModel" in banners
    assert "function applyModel(" in banners
    assert "function applyState(" in composer
    assert "sendBtn.disabled" in composer
    assert "input.disabled" in composer
    assert "hasUsableModel" in composer


def test_company_image_preview_uses_authenticated_blob_url() -> None:
    """Re-pointed in Phase 3B: the shared viewer is places/files/file-viewer.js.

    The property is unchanged and is a security one, not a style one. An image
    element pointed straight at an /api path cannot carry the X-BossMod-Token
    header, so the preview is fetched with the authenticated helper and
    rendered from an object URL; the token never lands in an attribute, and the
    URL is revoked when the panel closes.
    """
    viewer = (JS / "places" / "files" / "file-viewer.js").read_text(encoding="utf-8")
    client = (JS / "api-client.js").read_text(encoding="utf-8")

    assert "function apiFetchBlobUrl(" in client
    assert "URL.createObjectURL(blob)" in client
    assert 'src="/api/company/files/raw' not in viewer
    assert "apiFetchBlobUrl" in viewer or "fetchBlobUrl" in viewer
    assert "revokeObjectURL" in viewer
    assert "loadAuthenticatedImage" in viewer


def test_walk_receipts_stay_visible_when_system_toggle_off() -> None:
    """The dock-era chat sub-view is gone; the property is the source's now.

    Phase 2A added the agent-source half of this test while agent-context.js
    was still on disk. Phase 2B deleted that file, so its half is finished here
    rather than dropped: a walk receipt and a consent ask are system messages
    that the system-notifications toggle can never hide.
    """
    consent = (JS / "core" / "consent-card.js").read_text(encoding="utf-8")
    assert "function renderHostPathConsentCard(" in consent
    assert "Allow once" in consent
    assert "Always allow (for all agents)" in consent
    assert "Always allowed (for all agents)" in consent
    assert "Deny" in consent

    cards = (JS / "conversation" / "event-cards.js").read_text(encoding="utf-8")
    assert "host-path-consent-card" in cards

    source = (JS / "conversation" / "sources" / "agent-source.js").read_text(encoding="utf-8")
    assert "notification_kind === 'receipt'" in source
    assert "BossModConsentCard.isHostPathConsentMessage(raw)" in source
    assert "systemReceipt: isSystem && !isWalkReceipt && !consent" in source
    conversation = (JS / "conversation" / "conversation.js").read_text(encoding="utf-8")
    # Phase 2B split the persisted preference out of the controller; the key is
    # unchanged, so an operator's existing choice still applies.
    receipts = (JS / "conversation" / "system-receipts.js").read_text(encoding="utf-8")
    assert "bossmod.chat.showSystemReceipts" in receipts
    assert "message.systemReceipt" in conversation
    assert "systemReceipts.isEnabled()" in conversation
    # Only messages the source marked as a plain system receipt are filtered;
    # nothing here can reach a walk receipt or a consent ask.
    assert "messages.filter((message) => !message.systemReceipt)" in conversation


def test_connection_changes_refresh_the_no_model_banner() -> None:
    """Adding or deleting a connection must update the banner that gates Send.

    Both call sites used to read
    `if (typeof BossModApp !== 'undefined' && ...) BossModApp.refresh...()`.
    BossModApp died with the dock shell in Phase 1b, so the guard silently
    swallowed the call: connect your first model and the "No AI model is
    connected" banner stayed up, with chat Send disabled, until something else
    happened to refresh it. A typeof guard around a module that no longer
    exists is not defensive — it is a bug with the alarm switched off.
    """
    js = ROOT / "ui" / "static" / "js"
    for name in ("settings/settings-connections.js",
                 "settings/settings-connections-form.js"):
        source = (js / name).read_text(encoding="utf-8")
        assert "BossModBanners.refreshModelAvailability()" in source, name
        assert "BossModApp.refreshModelAvailability" not in source, name

    # The whole tree, not just these two: no module may guard its way past a
    # dependency that is supposed to be there.
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        # The skip list is gone with the files it named: Phase 4 deleted
        # app.js, dock-manager.js, company-view.js, company-dashboard.js and
        # agent-panel.js from disk, so the rule now covers the whole tree with
        # no exceptions. That is the stronger form of the same assertion.
        assert "typeof BossModApp" not in text, path.name


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
