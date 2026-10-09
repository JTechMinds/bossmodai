"""The Metrics period row: local calendar days in, half-open UTC instants out.

places/metrics/metrics-range.js turns a preset or a custom From/To into the
`[start, end)` pair the dashboard endpoint takes. Local midnight is the
browser's to know, so tests/js_metrics_range_harness.cjs runs under
TZ=America/New_York and checks a 23-hour DST day, Monday weeks and month
ends against a pinned today, then drives the row: Custom range reveals From
and To, and a To before From is said inline and never sent.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_metrics_range_harness.cjs"

# The order tests/js_metrics_range_harness.cjs evaluates its modules in.
MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "menu.js",
    JS / "core" / "menu-select.js",
    JS / "core" / "date-field.js",
    JS / "places" / "metrics" / "metrics-range.js",
]


def _payload() -> dict:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    env = dict(os.environ, TZ="America/New_York")
    result = subprocess.run(args, check=False, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_metrics_range_harness() -> None:
    assert _payload() == {
        "ok": True,
        "presetDaysCountsFromToday": True,
        "toInstantsUsesLocalMidnights": True,
        "labelNamesThePeriod": True,
        "factoryRefusesMissingDeps": True,
        "theRowIsOneToolbar": True,
        "customRevealsFromAndTo": True,
        "backwardsIsSaidNotSent": True,
        "fixingItReports": True,
        "aPresetHidesTheDays": True,
        "aCustomInitialSeedsTheDays": True,
    }
