"""The composer field's own undo history.

The composer is a contenteditable field that code rewrites (a paste, an
@mention pick), which orphans the browser's native undo. So the field owns
its history: composer-history.js holds the pure steps and mention-draft.js
binds them to the element.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CONVERSATION = JS / "conversation"
HTML = ROOT / "ui" / "templates" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js_composer_history_harness.cjs"

# The order tests/js_composer_history_harness.cjs evaluates its modules in.
HISTORY_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    CONVERSATION / "mentions.js",
    CONVERSATION / "mention-pill.js",
    CONVERSATION / "composer-history.js",
    CONVERSATION / "mention-draft.js",
]


def _scripts() -> list[str]:
    return re.findall(r"static_url\('([^']+\.js)'\)", HTML.read_text(encoding="utf-8"))


def test_history_loads_before_the_field_that_binds_it() -> None:
    scripts = _scripts()
    assert scripts.index("js/conversation/composer-history.js") < scripts.index(
        "js/conversation/mention-draft.js"
    )
    assert scripts.index("js/conversation/mention-draft.js") < scripts.index(
        "js/conversation/composer.js"
    )


def test_composer_history_harness() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)] + [str(path) for path in HISTORY_MODULES],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        # Typing merges per word: undo takes "world", then "hello"; the third
        # undo has nothing left, and redo walks back up.
        "wordSteps": [
            {"text": "hello", "caret": 5},
            {"text": "", "caret": 0},
            None,
            {"text": "hello", "caret": 5},
            {"text": "hello world", "caret": 11},
        ],
        "backspaceRunIsOneStep": [{"text": "hello", "caret": 5}, None],
        # An overwrite never merges, so the accident is one Ctrl+Z.
        "selectAllThenTypeIsOneStep": [{"text": "important draft", "caret": 15}, None],
        # A paste or pick between two typing runs is a step of its own.
        "discreteStepMakesThreeSteps": ["abX", "ab", "", None],
        "newRecordClearsRedo": None,
        # Typing after an undo starts a step; it never joins the restored one.
        "typingAfterUndoDoesNotMerge": ["hello", ""],
        # "hel|lo" + "l": the diff is anchored at the caret, not the prefix.
        "repeatedLetterDiffAtCaret": {
            "undo": {"text": "hello", "caret": 3},
            "redo": {"text": "helllo", "caret": 4},
        },
        "resetClearsBoth": {"undo": None, "redo": None},
        # settle adopts the re-cased text without adding a step, and the next
        # step is diffed against what it adopted.
        "settleAddsNoStep": None,
        "settleChangesPresent": {"text": "hi @Joe", "caret": 7},
        "caretRefused": "[composer-history] record needs an integer caret",
        # The actions carry InputEvent.inputType's names, the same ones a
        # cancelled beforeinput hands the field.
        "shortcuts": {
            "ctrlZ": "historyUndo",
            "cmdZ": "historyUndo",
            "ctrlShiftZ": "historyRedo",
            "ctrlY": "historyRedo",
            "cmdY": None,
            # AltGr types characters on many layouts; it is never a shortcut.
            "ctrlAltZ": None,
            "plainZ": None,
        },
        "mergeable": {
            "insertText": True,
            "insertLineBreak": True,
            "insertParagraph": True,
            "deleteContentBackward": True,
            "deleteContentForward": True,
            "insertFromPaste": False,
            "deleteContent": False,
            "deleteByCut": False,
            "insertFromDrop": False,
            "insertReplacementText": False,
            "deleteWordBackward": False,
            "missing": False,
        },
        # The bound field: Ctrl+Z is handled (not left to the browser) and the
        # restore announces itself with one `input`, as native undo does.
        "typingThenCtrlZ": {
            "before": "draft more",
            "after": "draft ",
            "prevented": True,
            "inputs": 1,
        },
        "editValueIsOneStep": {
            "edited": "say hello",
            "editInputs": 1,
            "caret": 9,
            "undone": "say ",
            "undoneCaret": 4,
            "redone": "say hello",
            "redoneCaret": 9,
        },
        # Assigning `.value` replaces the text and starts a fresh history.
        "valueCutsHistory": {"value": "fresh", "prevented": True},
        # The Edit menu's undo arrives as beforeinput; it is cancelled and run here.
        "beforeinputHistoryUndo": {
            "undoPrevented": True,
            "afterUndo": "",
            "redoPrevented": True,
            "afterRedo": "abc",
        },
        # Only the IME's commit is a step; its draft text and keys are its own.
        "composition": {
            "composingKeyLeftToIme": True,
            "afterOneUndo": "",
            "afterRedo": "日本",
        },
        # No model or a sealed thread: the shortcut is swallowed, nothing restored.
        "disabledFieldDoesNotRestore": {"value": "abc", "prevented": True},
        "undoRepaintsPill": {
            "hadPill": True,
            "cleared": "",
            "value": "ping @Joe ",
            "pillEditable": "false",
        },
        # The history holds what the field reads back, so `@joe` round-trips as `@Joe`.
        "editValueReadsBack": {
            "edited": "@Joe ",
            "undone": "",
            "redone": "@Joe ",
            "undoneAgain": "",
        },
        # A bad caret is refused before the field is repainted, so the field
        # and its history never disagree.
        "editValueRefusesBadCaret": {
            "error": "[mention-draft] editValue needs an integer caret",
            "value": "keep",
            "afterUndo": "keep",
        },
    }
