"""Channel consent cards, thinking chrome, and hire in-flight helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.agent_loop.dispatcher import _channel_id_for_presence

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
PRESENCE_HARNESS = Path(__file__).resolve().parent / "js_channel_presence_harness.cjs"
GATE_HARNESS = Path(__file__).resolve().parent / "js_inflight_gate_harness.cjs"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_utils_exports_channel_presence_and_consent_card() -> None:
    source = _read("utils.js")
    assert "function createChannelPresenceController(" in source
    assert "createChannelPresenceController," in source
    assert "function createInFlightGate(" in source
    assert "function renderHostPathConsentCard(" in source
    assert "Allow once" in source
    assert "Always allow" in source
    assert "Deny" in source


def test_channels_view_renders_consent_card_and_member_thinking() -> None:
    source = _read("channels-view.js")
    assert "BossModUtils.createChannelPresenceController()" in source
    assert "BossModUtils.isHostPathConsentMessage(message)" in source
    assert "BossModUtils.renderHostPathConsentCard(" in source
    assert "host-path-consent-card" in source
    assert "function renderChannelThinking(" in source
    assert "is thinking..." in source
    assert "handleChannelPresence" in source
    assert "presence.start(" in source
    assert "presence.stop(" in source
    app = _read("app.js")
    assert "case 'channel_presence':" in app
    assert "AgentContext.handleChannelPresence(msg.data)" in app
    context = _read("agent-context.js")
    assert "handleChannelPresence," in context
    assert "ChannelsView.handleChannelPresence(data)" in context


def test_channel_presence_helper_only_tracks_channel_turns() -> None:
    assert _channel_id_for_presence({
        "type": "channel_message",
        "channel_id": "ch-1",
    }) == "ch-1"
    assert _channel_id_for_presence({
        "type": "channel_response",
        "channel_id": "  ch-2  ",
    }) == "ch-2"
    assert _channel_id_for_presence({"type": "human_chat", "channel_id": "ch-1"}) is None
    assert _channel_id_for_presence({"type": "channel_message"}) is None


def test_channel_presence_harness_tracks_members_per_channel() -> None:
    result = subprocess.run(
        ["node", str(PRESENCE_HARNESS), str(JS / "utils.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "tracksMembers": True,
        "isolatesChannels": True,
        "stopsOne": True,
        "stopsAll": True,
    }


def test_inflight_gate_harness_blocks_nested_submit() -> None:
    result = subprocess.run(
        ["node", str(GATE_HARNESS), str(JS / "utils.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "blocksNested": True,
        "clearsAfter": True,
        "allowsLater": True,
    }
