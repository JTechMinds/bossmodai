"""The connection form's "Examples of common formats" panel.

Driven through tests/js_thinking_examples_harness.cjs against the real form,
examples and switch modules. The examples are reference text only: they copy
to the clipboard and never reach the five thinking-level inputs.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_thinking_examples_harness.cjs"

LEVELS = [
    ("off", "Off"),
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("xhigh", "Extra high"),
]

DISCLAIMER = (
    "Formats vary by server, model and chat template. These are starting "
    "points, not guarantees — check your provider's docs."
)


@lru_cache(maxsize=1)
def _run() -> dict:
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(JS / "core" / "format.js"),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "switch.js"),
            str(JS / "settings" / "settings-thinking-examples.js"),
            str(JS / "settings" / "settings-connections-form.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_panel_renders_collapsed_with_the_disclaimer_first() -> None:
    initial = _run()["initial"]
    assert initial["expanded"] == "false"
    assert initial["hidden"] is True
    assert initial["toggleType"] == "button"
    assert initial["controls"] == "connection-thinking-examples-content"
    assert initial["title"] == "Examples of common formats"
    assert initial["insideThinkingFieldset"] is True
    assert initial["firstLine"] == DISCLAIMER
    assert initial["statusRole"] == "status"
    assert initial["statusLive"] == "polite"
    assert initial["liveRegions"] == 1
    assert {"isToggle": True, "context": "settings-thinking-examples"} in initial["painted"]


def test_examples_constant_holds_the_three_families() -> None:
    payload = _run()
    assert payload["frozen"] is True
    examples = payload["examples"]
    assert [family["title"] for family in examples] == [
        "Z.ai GLM",
        "OpenAI-style reasoning_effort",
        "Chat-template kwargs",
    ]
    glm, openai, kwargs = (family["snippets"] for family in examples)
    assert glm["off"] == '{"thinking":{"type":"disabled"}}'
    assert openai["off"] == '{"reasoning_effort":"none"}'
    assert kwargs["off"] == '{"chat_template_kwargs":{"enable_thinking":false}}'
    for key, _label in LEVELS[1:]:
        assert glm[key] == f'{{"thinking":{{"type":"enabled"}},"reasoning_effort":"{key}"}}'
        assert openai[key] == f'{{"reasoning_effort":"{key}"}}'
        assert kwargs[key] == (
            f'{{"chat_template_kwargs":{{"enable_thinking":true,"reasoning_effort":"{key}"}}}}'
        )
    for family in examples:
        assert sorted(family["snippets"]) == sorted(key for key, _ in LEVELS)
        for text in family["snippets"].values():
            assert isinstance(json.loads(text), dict)


def test_each_family_renders_five_rows_matching_the_constant() -> None:
    payload = _run()
    families = payload["families"]
    assert len(families) == 3
    for rendered, family in zip(families, payload["examples"], strict=True):
        assert rendered["title"] == family["title"]
        assert rendered["usedBy"] == family["usedBy"]
        assert [row["label"] for row in rendered["rows"]] == [label for _, label in LEVELS]
        assert [row["code"] for row in rendered["rows"]] == [
            family["snippets"][key] for key, _ in LEVELS
        ]
        for row, (_key, label) in zip(rendered["rows"], LEVELS, strict=True):
            assert row["buttonType"] == "button"
            assert row["ariaLabel"] == f"Copy {family['title']} {label} example"


def test_toggle_flips_aria_expanded_and_hidden() -> None:
    payload = _run()
    assert payload["opened"] == {"expanded": "true", "hidden": False}
    assert payload["closed"] == {"expanded": "false", "hidden": True}
    assert payload["reopened"] == {"expanded": "true", "hidden": False}


def test_copy_passes_exactly_its_snippet_and_reports() -> None:
    payload = _run()
    expected = payload["examples"][2]["snippets"]["xhigh"]
    assert payload["copied"]["ariaLabel"] == "Copy Chat-template kwargs Extra high example"
    assert payload["copied"]["writes"] == [expected]
    assert payload["copied"]["status"] == "Copied"
    # A refused clipboard writes nothing more and says so.
    assert payload["failed"]["writes"] == [expected]
    assert payload["failed"]["status"] == "Copy failed"


def test_examples_never_write_into_the_level_inputs() -> None:
    payload = _run()
    blank = {key: "" for key, _ in LEVELS}
    assert payload["inputsBeforeCopy"] == blank
    assert payload["inputsAfterCopy"] == blank


def test_submit_after_expanding_sends_levels_from_the_inputs_only() -> None:
    payload = _run()
    assert payload["reopened"]["expanded"] == "true"
    assert len(payload["saves"]) == 1
    save = payload["saves"][0]
    assert save["url"] == "/api/connections"
    assert save["method"] == "POST"
    assert save["body"]["thinking_levels"] == {
        "off": {"thinking": {"type": "disabled"}},
        "high": {"reasoning_effort": "high"},
    }
    assert payload["done"] == 1
