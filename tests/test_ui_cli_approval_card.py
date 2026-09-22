"""Nest Always allow, twin collapse, and stale Dismiss on CLI cards."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_cli_approval_card_harness.cjs"


def test_cli_approval_card_always_coalesce_and_stale_dismiss() -> None:
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(JS / "core" / "consent-card.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "nestOffersAlways": True,
        "deskHidesAlways": True,
        "quietWithoutNote": True,
        "showsReviewWhy": True,
        "alwaysAllowResolves": True,
        "staleMorphsToDismiss": True,
        "dismissIsLocalOnly": True,
    }
