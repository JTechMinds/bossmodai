"""UI A6 — WS reconnect backoff + single canvas resize path.

Re-pointed in Phase 4, when the dock era was deleted from disk. Every subject
here was `app.js`; all four properties moved with the code and none of them was
dropped:

* the unload guard is registered once, by `shell/shell.js`, outside the socket's
  connect path — `shell/socket.js` owns the socket and knows nothing about the
  page lifecycle;
* the backoff constants and ramp are `shell/socket.js`'s, and the behavioural
  half (the climb, the reset after a good connect, the timer a deliberate close
  must not leave behind) is proven by `js_resync_harness.cjs`, which absorbed
  `js_ws_reconnect_harness.cjs` when app.js was deleted;
* the canvas still has exactly one resize path, and the `panel-resize` event it
  listens for is dispatched by `places/office/office-place.js`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"


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
    """The socket does not know the page can go away; the shell does.

    Registering it inside `connect` meant one more listener on every reconnect,
    and the listener is what stops a deliberate close from being read as an
    outage and retried forever.
    """
    socket = _read("shell/socket.js")
    shell = _read("shell/shell.js")
    assert "beforeunload" not in socket, "the socket must not bind the page lifecycle"
    assert shell.count("addEventListener('beforeunload'") == 1
    assert "socket.close()" in shell
    # The guard socket.js keeps in its place: a close after that must not
    # schedule anything.
    assert "unloading = true;" in socket
    assert "if (unloading) return;" in socket


def test_websocket_reconnect_uses_exponential_backoff() -> None:
    """One named ramp, both ends bounded.

    A single constant delay either hammers a dead server or leaves the operator
    staring at a stale UI; an uncapped one climbs past any useful wait.
    """
    source = _read("shell/socket.js")
    assert "WS_RECONNECT_DELAY" not in source
    assert "function nextDelay(" in source
    assert "function scheduleReconnect(" in source
    assert "RECONNECT_MIN_MS = 1000" in source
    assert "RECONNECT_MAX_MS = 30000" in source
    # The counter resets on a good open — asserted behaviourally in
    # test_ui_resync.py; this is the line that does it.
    assert "attempt = 0;" in _function_body(source, "connect")


def test_canvas_resize_uses_panel_resize_only() -> None:
    """Re-pointed to places/office/, which owns the canvas and its container.

    Same property, same assertions: one resize path. A canvas in a hidden
    container renders at zero size, and binding window.resize as a second path
    is how that bug came back last time.
    """
    canvas = _read("places/office/office-canvas.js")
    place = _read("places/office/office-place.js")
    assert "addEventListener('panel-resize'" in canvas
    assert "addEventListener('resize'" not in canvas
    assert "addEventListener(\"resize\"" not in canvas
    # Something must fire the event the canvas waits for, or the single path is
    # a path to nothing.
    assert "dispatchEvent(new Event('panel-resize'))" in place


def test_the_reconnect_harness_moved_rather_than_vanished() -> None:
    """app.js's harness is gone; its properties are not.

    js_ws_reconnect_harness.cjs drove app.js and asserted three things:
    the socket count across reconnects, one beforeunload listener, and the
    delay ramp [1000, 2000, 4000, 1000]. The ramp and the unload guard are
    js_resync_harness.cjs's now and the listener is asserted above. Deleting
    the harness without this check is exactly how a re-point becomes a
    deletion.
    """
    tests = Path(__file__).resolve().parent
    assert not (tests / "js_ws_reconnect_harness.cjs").exists()
    resync = (tests / "js_resync_harness.cjs").read_text(encoding="utf-8")
    assert "[1000, 2000, 4000, 1000]" in resync
    assert "backoffResetsAfterConnect" in resync
    assert "unloadStopsReconnect" in resync
    suite = (tests / "test_ui_resync.py").read_text(encoding="utf-8")
    assert '"backoffResetsAfterConnect": True,' in suite
    assert '"unloadStopsReconnect": True,' in suite
