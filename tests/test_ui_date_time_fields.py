"""The Edit-mode field widgets: the time field, the date field and autogrow.

The schedule editor's native `type=time` / `type=date` inputs render in the
desktop webview (WebKitGTK) as grey segmented boxes with no picker, and its
instructions textarea clipped at four rows because nothing grew it. These are
the shared replacements (core/time-field.js, core/date-field.js,
core/autogrow.js), proved through tests/js_date_time_fields_harness.cjs: the
pure parse/format/grid functions case by case, and the DOM halves driven as
an operator drives them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_date_time_fields_harness.cjs"

# The order tests/js_date_time_fields_harness.cjs evaluates its modules in.
MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "menu.js",
    JS / "core" / "autogrow.js",
    JS / "core" / "time-field.js",
    JS / "core" / "date-field.js",
]


def _payload() -> dict:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_date_time_fields_and_autogrow_harness() -> None:
    assert _payload() == {
        "ok": True,
        "parseTimeReadsTheListedForms": True,
        "formatTimeOfDaySpellsTwelveHour": True,
        "monthGridIsMondayFirstAndRollsOver": True,
        "formatDateLabelSpellsTheDay": True,
        "timeFieldIsATextFieldWithAClock": True,
        "blurFormatsTheTime": True,
        "anInvalidTimeIsMarkedAndSaid": True,
        "enterSettles": True,
        "anOffGridTimeIsListed": True,
        "aSuggestionPickFiresOnChange": True,
        "aBadStoredTimeThrows": True,
        "dateTriggerNamesTheDay": True,
        "theDateMenuOpensOnTheDay": True,
        "arrowKeysMoveTheDay": True,
        "pageDownMovesAMonth": True,
        "enterPicksTheDay": True,
        "escReturnsFocusToTheTrigger": True,
        "aMonthStepClampsTheDay": True,
        "autogrowSkipsANodeWithNoLayout": True,
        "autogrowBindsTextareasOnly": True,
    }
