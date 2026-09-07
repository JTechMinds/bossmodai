"""The place registry defines exactly six places; Chat is the only one with a context column."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_places_harness.cjs"

EXPECTED_ORDER = ["chat", "office", "board", "files", "metrics", "log"]


def _source() -> str:
    return (JS / "shell" / "places.js").read_text(encoding="utf-8")


def test_six_places_in_nav_order() -> None:
    source = _source()
    match = re.search(r"PLACE_IDS\s*=\s*Object\.freeze\(\[(.*?)\]\)", source, re.S)
    assert match, "PLACE_IDS must be a frozen array"
    ids = re.findall(r"'([a-z]+)'", match.group(1))
    assert ids == EXPECTED_ORDER, f"expected {EXPECTED_ORDER}, got {ids}"


def test_only_chat_has_a_context_column() -> None:
    """The 280px context column exists on Chat alone (spec 3.1)."""
    source = _source()
    assert source.count("hasContext: true") == 1, (
        "exactly one place may declare hasContext"
    )
    chat_block = source.split("chat:", 1)[1].split("office:", 1)[0]
    assert "hasContext: true" in chat_block, "Chat must be the place with the context column"


def test_every_place_has_mount_unmount_and_a_heading() -> None:
    """navigate() focuses each place's h1 — a place without one breaks keyboard nav.

    Asserted behaviourally rather than by counting source strings: the six
    stubs share one factory, so the contract has to be checked by mounting
    each place, not by how many times "mount(" is spelled.
    """
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "dom.js"), str(JS / "shell" / "places.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "placeCount": 6,
        "everyPlaceMounts": True,
        "everyPlaceUnmounts": True,
        "everyPlaceRendersOneFocusableHeading": True,
    }


def test_register_allows_phases_to_replace_stubs() -> None:
    source = _source()
    assert "function register(" in source, (
        "Phases 2-3 replace stubs via register(); without it they would edit this file"
    )
