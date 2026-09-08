"""shell/socket.js re-syncs after an outage instead of silently losing messages."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_resync_harness.cjs"


def test_resync_fires_only_on_reconnect() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "bus.js"), str(JS / "shell" / "socket.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "noResyncOnFirstConnect": True,
        "resyncOnEveryReconnect": True,
        "backoffCapped": True,
        # Re-pointed from js_ws_reconnect_harness.cjs when Phase 4 deleted
        # app.js: the retry delay climbs while connects fail and restarts at
        # 1s after a good one, and a deliberate close leaves no timer behind.
        "backoffResetsAfterConnect": True,
        "unloadStopsReconnect": True,
        "routesMessages": True,
    }


def test_socket_does_no_routing_decisions() -> None:
    """socket.js publishes onto topics; it must not know about other modules."""
    source = (JS / "shell" / "socket.js").read_text(encoding="utf-8")
    for forbidden in ("AgentContext", "ActivityLog", "OfficeCanvas", "CompanyTasks"):
        assert forbidden not in source, (
            f"socket.js must not reference {forbidden} — that is what the bus is for"
        )
