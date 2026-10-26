"""UI contract for seating a live agent into an existing thread."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_thread_seat_harness.cjs"

SEAT_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "core" / "yolo-mode.js",
    JS / "core" / "menu.js",
    JS / "core" / "menu-button.js",
    JS / "conversation" / "title-rename.js",
    JS / "conversation" / "chrome-menu.js",
    JS / "conversation" / "chrome-switch.js",
    JS / "conversation" / "chrome.js",
    JS / "shell" / "floor-scope.js",
    JS / "conversation" / "sources" / "thread-archive.js",
    JS / "conversation" / "sources" / "thread-seat.js",
    JS / "conversation" / "sources" / "thread-requests.js",
    JS / "conversation" / "approval-controls.js",
    JS / "conversation" / "sources" / "thread-members.js",
    JS / "conversation" / "sources" / "thread-source.js",
]


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_seat_picker_is_not_a_source() -> None:
    seat = _read("conversation/sources/thread-seat.js")
    assert "kind: '" not in seat
    assert "BossModDom" in seat
    thread = _read("conversation/sources/thread-source.js")
    assert "ctx.seat is required" in thread
    assert "id: 'channel-seat-btn'" in thread
    assert "onSelect: membersDialog.open," in thread
    assert "onSubtitleSelect: archived ? null : membersDialog.open" in thread
    members = _read("conversation/sources/thread-members.js")
    assert "seat.pickAndSeat(" in members
    people = _read("shell/roster-people.js")
    assert "id: 'roster-seat-agent'" in people
    assert "seat.pickAndSeat(" in people
    assert "seatGroup.remove()" in people
    conversation = _read("conversation/conversation.js")
    assert "BossModThreadSeat.createThreadSeat(" in conversation
    assert "seat: BossModThreadSeat.createThreadSeat({ api, store })" in conversation


def test_seat_harness_preserves_history_and_fail_closes() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)] + [str(path) for path in SEAT_MODULES],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "offeredOnlyHugh": True,
        "emptyPickerFailClosed": True,
        "seatedWithoutWipe": True,
        "duplicateFailClosed": True,
        "liveChromeOffersSeat": True,
        "archivedHidesSeat": True,
        "floorScopedPicker": True,
        "subtitleOpensMembers": True,
        "removeRepaints": True,
        "lastMemberLocked": True,
        "taskWarning": True,
        # Everyone plus the members read live from the channel, Hugh included.
        "mentionScope": {"everyone": True, "memberIds": ["debra", "jim", "hugh"]},
    }
