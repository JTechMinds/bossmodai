"""UI A6 — WS reconnect backoff + single canvas resize path."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_ws_reconnect_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    remainder = source[start:]
    end = re.search(r"\n    function |\n    async function |\n    return \{", remainder[1:])
    if not end:
        return remainder
    return remainder[: end.start() + 1]


def test_beforeunload_is_registered_outside_websocket_connect() -> None:
    source = _read("app.js")
    connect = _function_body(source, "initWebSocket")
    assert "beforeunload" not in connect
    assert "function initUnloadGuard(" in source
    assert source.count("addEventListener('beforeunload'") == 1
    assert "initUnloadGuard();" in _function_body(source, "init")


def test_websocket_reconnect_uses_exponential_backoff() -> None:
    source = _read("app.js")
    assert "WS_RECONNECT_DELAY" not in source
    assert "function nextWsReconnectDelay(" in source
    assert "function scheduleWsReconnect(" in source
    assert "WS_RECONNECT_MIN_MS = 1000" in source
    assert "WS_RECONNECT_MAX_MS = 30000" in source
    assert "wsReconnectAttempt = 0" in _function_body(source, "initWebSocket")
    assert "if (wsUnloadClose) return;" in _function_body(source, "initWebSocket")


def test_canvas_resize_uses_panel_resize_only() -> None:
    canvas = _read("canvas.js")
    app = _read("app.js")
    assert "addEventListener('panel-resize'" in canvas
    assert "addEventListener('resize'" not in canvas
    assert "addEventListener(\"resize\"" not in canvas
    assert "function initResize(" in app
    assert "addEventListener('resize'" in _function_body(app, "initResize")
    assert "dispatchEvent(new Event('panel-resize'))" in _function_body(app, "initResize")


def test_ws_reconnect_harness_backs_off_and_registers_unload_once() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "app.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "sockets": 5,
        "beforeunload": 1,
        "delays": [1000, 2000, 4000, 1000],
    }
