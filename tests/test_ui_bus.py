"""core/bus.js fans WebSocket topics out and rejects unknown ones loudly."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
# Broadcasts live in BOTH files: websocket.py has the manager methods,
# routes/ws.py sends the initial unified_feed on connect. Scanning only one
# would miss a new message type added to the other.
WS_SOURCES = (ROOT / "api" / "websocket.py", ROOT / "api" / "routes" / "ws.py")
HARNESS = Path(__file__).resolve().parent / "js_bus_harness.cjs"


def test_bus_semantics() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "bus.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "fansOut": True,
        "isolatesTopics": True,
        "rejectsUnknownTopics": True,
        "isolatesThrowingSubscriber": True,
    }


def test_known_topics_cover_every_server_broadcast() -> None:
    """If the server adds a message type, KNOWN_TOPICS must learn about it."""
    server_types: set[str] = set()
    for source in WS_SOURCES:
        server_types |= set(
            re.findall(r'"type":\s*"([a-z_]+)"', source.read_text(encoding="utf-8"))
        )
    assert "unified_feed" in server_types, (
        "expected unified_feed from api/routes/ws.py — did the broadcast move?"
    )
    assert len(server_types) >= 13, f"only found {len(server_types)} broadcast types"

    bus = (JS / "core" / "bus.js").read_text(encoding="utf-8")
    missing = [t for t in sorted(server_types) if f"'{t}'" not in bus and f'"{t}"' not in bus]
    assert not missing, f"core/bus.js KNOWN_TOPICS is missing: {missing}"
