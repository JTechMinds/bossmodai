"""Chat http(s) clicks open in the system browser, not the Tauri webview."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
DESKTOP = ROOT / "desktop"
HTML = ROOT / "ui" / "templates" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js_external_open_harness.cjs"
EXTERNAL_OPEN = DESKTOP / "src" / "external_open.rs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_http_click_harness() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "external-open.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "transcriptInvokesDesktop": True,
        "composerInvokesDesktop": True,
        "sameCommand": True,
        "preventDefault": True,
        "javascriptRefused": True,
        "fileRefused": True,
        "mailtoLeftAlone": True,
        "relativeLeftAlone": True,
        "browserUsesWindowOpen": True,
        "webviewLocationUntouched": True,
    }


def test_external_open_rust_unit(tmp_path: Path) -> None:
    """GTK-free: the allowlist is a standalone file with no Tauri imports."""
    target = tmp_path / "external_open_test"
    compile = subprocess.run(
        ["rustc", "--test", "-o", str(target), str(EXTERNAL_OPEN)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile.returncode == 0, compile.stderr or compile.stdout
    run = subprocess.run([str(target)], check=False, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr or run.stdout
    assert "test result: ok" in run.stdout


def test_one_shared_interceptor_and_one_desktop_command() -> None:
    opener = _read(JS / "core" / "external-open.js")
    rust = _read(EXTERNAL_OPEN)
    main = _read(DESKTOP / "src" / "main.rs")
    html = _read(HTML)
    shell = _read(JS / "shell" / "shell.js")
    message = _read(JS / "conversation" / "message.js")
    composer = _read(JS / "conversation" / "composer.js")

    assert "const COMMAND = 'open_external_url'" in opener
    assert "document.addEventListener('click', handleClick, true)" in opener
    assert "location.href" not in opener
    assert "location.assign" not in opener
    assert "window.open(url, '_blank', 'noopener,noreferrer')" in opener

    assert "pub const COMMAND" not in rust
    assert "open_external_url" in rust
    assert "fn allowed_http_url" in rust
    assert "xdg-open" in rust
    assert "javascript:" in rust
    assert "file://" in rust

    assert "mod external_open;" in main
    assert "external_open::open_http_url" in main
    handler = main.split("tauri::generate_handler!", 1)[1].split("]", 1)[0]
    assert "open_external_url" in handler
    assert "on_navigation" in main
    assert "external_open::allow_webview_navigation" in main

    assert "static_url('js/core/external-open.js')" in html
    assert html.index("js/core/external-open.js") < html.index("js/conversation/message.js")
    assert html.index("js/core/external-open.js") < html.index("js/conversation/composer.js")
    assert html.index("js/core/external-open.js") < html.index("js/shell/shell.js")
    assert "BossModExternalOpen.install()" in shell

    # Surfaces share the interceptor rather than growing their own.
    assert "open_external_url" not in message
    assert "open_external_url" not in composer
    assert "addEventListener('click'" not in message
    assert "__TAURI__" not in message
    assert "__TAURI__" not in composer


def test_markdown_still_hardens_http_links_without_navigating_in_app() -> None:
    """The sanitiser keeps http(s) anchors; the interceptor is what opens them."""
    markdown = _read(JS / "core" / "markdown.js")
    assert "el.setAttribute('target', '_blank')" in markdown
    assert "ALLOWED_SCHEMES = Object.freeze(['http:', 'https:', 'mailto:'])" in markdown
    css = _read(ROOT / "ui" / "static" / "css" / "conversation.css")
    assert ".composer-input a" in css
