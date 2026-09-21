"""Glanceable Needs: taskbar/dock badge + tray, same truth as the bell."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
DESKTOP = ROOT / "desktop"
HARNESS = Path(__file__).resolve().parent / "js_needs_attention_harness.cjs"
NEEDS_MAP = DESKTOP / "src" / "needs_map.rs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_needs_to_badge_harness() -> None:
    args = [
        "node",
        str(HARNESS),
        str(JS / "core" / "store.js"),
        str(JS / "needs" / "needs-attention.js"),
    ]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "mirrorsStoreCount": True,
        "clearsAtZero": True,
        "copyIsCountOnly": True,
        "osToastParked": True,
        "trayFocusOpensNeeds": True,
        "browserIsNoop": True,
        "disposerDrainsSubscriptions": True,
    }


def test_needs_map_rust_unit(tmp_path: Path) -> None:
    """GTK-free: the mapping is a standalone file with no Tauri imports."""
    target = tmp_path / "needs_map_test"
    compile = subprocess.run(
        ["rustc", "--test", "-o", str(target), str(NEEDS_MAP)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile.returncode == 0, compile.stderr or compile.stdout
    run = subprocess.run([str(target)], check=False, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr or run.stdout
    assert "test result: ok" in run.stdout


def test_shared_store_not_a_parallel_counter() -> None:
    """Badge and tray subscribe to store.needs — the same slice as the bell."""
    attention = _read(JS / "needs" / "needs-attention.js")
    header = _read(JS / "shell" / "header.js")
    rust = _read(DESKTOP / "src" / "needs_attention.rs")
    mapping = _read(NEEDS_MAP)

    assert "store.subscribe((s) => s.needs, apply)" in attention
    assert "store.subscribe((s) => s.needs, applyNeeds)" in header
    assert "createHost({ store })" in _read(JS / "shell" / "shell.js")
    assert "function countOf(needs)" in attention
    assert "Array.isArray(needs) ? needs.length" in attention

    # The desktop command takes a count, not a need. A title has nowhere to go.
    assert "pub fn sync_needs_attention(app: AppHandle, count: u32)" in rust
    assert "need.title" not in attention
    assert "need.sub" not in attention
    assert "fn tray_tooltip(needs_len: u32)" in mapping
    assert "fn badge_count(needs_len: u32)" in mapping


def test_os_toast_parked_and_copy_has_no_secret_surface() -> None:
    attention = _read(JS / "needs" / "needs-attention.js")
    rust = _read(DESKTOP / "src" / "needs_attention.rs")
    mapping = _read(NEEDS_MAP)

    assert "const OS_TOAST_ENABLED = false" in attention
    assert "pub const OS_TOAST_ENABLED: bool = false" in mapping
    assert "new Notification" not in attention
    assert "Notification(" not in attention
    assert "showNotification" not in rust
    assert "plugin-notification" not in rust
    assert "tauri-plugin-notification" not in _read(DESKTOP / "Cargo.toml")
    assert "if needs_map::OS_TOAST_ENABLED" in rust

    assert '"BossMod AI".to_string()' in mapping
    assert "1 thing needs you" in mapping
    assert "{n} things need you" in mapping
    tooltip_fn = mapping.split("pub fn tray_tooltip(needs_len: u32)", 1)[1].split(
        "pub fn tray_title", 1
    )[0]
    assert "needs_len" in tooltip_fn
    for leaked in ("need.title", "need.sub", "token", "secret", "command"):
        assert leaked not in tooltip_fn.lower()


def test_windows_overlay_macos_badge_linux_best_effort() -> None:
    rust = _read(DESKTOP / "src" / "needs_attention.rs")
    cargo = _read(DESKTOP / "Cargo.toml")
    config = json.loads(_read(DESKTOP / "tauri.conf.json"))

    assert 'features = ["tray-icon"]' in cargo
    assert config["app"]["withGlobalTauri"] is True
    assert config["app"]["windows"][0]["label"] == "main"
    assert "#[cfg(windows)]" in rust
    assert "set_overlay_icon" in rust
    assert "#[cfg(not(windows))]" in rust
    assert "set_badge_count" in rust
    assert '#[cfg(target_os = "linux")]' in rust
    assert "set_title" in rust
    assert "TrayIconBuilder::with_id" in rust
    assert "focus_needs" in rust
    assert "needs-focus" in rust
    # No custom star: the overlay is a red marker plus digits.
    assert "no custom" in _read(NEEDS_MAP).lower() or "red marker" in _read(NEEDS_MAP).lower()


def test_tray_click_focuses_needs_not_a_toggle() -> None:
    rust = _read(DESKTOP / "src" / "needs_attention.rs")
    header = _read(JS / "shell" / "header.js")
    attention = _read(JS / "needs" / "needs-attention.js")
    html = _read(ROOT / "ui" / "templates" / "index.html")

    assert "MouseButton::Left" in rust
    assert "unminimize" in rust
    assert "set_focus" in rust
    assert "app.emit(FOCUS_EVENT" in rust
    assert "attention.setShowNeeds(showNeeds)" in header
    assert "if (popover) return;" in header.split("function showNeeds()", 1)[1].split("function openNeeds()", 1)[0]
    assert "FOCUS_EVENT" in attention
    assert "js/needs/needs-attention.js" in html
    assert html.index("js/needs/needs-attention.js") < html.index("js/shell/shell.js")
    assert html.index("js/needs/needs-store.js") < html.index("js/needs/needs-attention.js")
