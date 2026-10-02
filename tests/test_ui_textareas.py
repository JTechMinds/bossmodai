"""One textarea standard across the app.

A textarea's look and size come from one CSS class plus a size modifier, never
from a call site's `rows`, pixels or a Tailwind utility string. The audit that
introduced this found fourteen form textareas in five styles: two private
copies of the field box (a hardcoded radius, a different fill), seven Tailwind
strings on the decorative --line border (one hardcoding `bg-white`), one
utility string copied between two settings sections, and heights from 2 to 12
rows set per call site. Three classes remain:

- `field-textarea` (controls.css): a form field, short by default and
  `data-size="long"` for prompts and descriptions; `field-mono` (a font rule
  for any field) for code, JSON, keys and templates;
- `edit-field-multiline` (controls.css): prose edited in place in a record's
  Edit mode;
- `text-editor` (controls.css): a full-pane editor.

The single-line inputs and native selects in the same forms followed in a
second pass: a grey shared textarea under white Tailwind or `.assign-*` inputs
was two field looks in one form. They wear `field-input` / `field-select`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"

STANDARD = ("field-textarea", "edit-field-multiline", "text-editor")
# Not a form field: a hidden clipboard helper (places/files/file-ops.js).
NOT_A_FIELD = "file-copy-shim"

MARKUP_TEXTAREA = re.compile(r"<textarea\b([^>]*)>")
BUILT_TEXTAREA = re.compile(r"h\('textarea',\s*\{(.*?)\}\)", re.S)


def _app_sources() -> dict[str, str]:
    return {
        path.relative_to(JS).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(JS.rglob("*.js"))
        if "vendor" not in path.parts
    }


def _in_comment(source: str, offset: int) -> bool:
    line = source[source.rfind("\n", 0, offset) + 1:offset].strip()
    return line.startswith(("*", "//", "/*"))


def _textareas() -> list[tuple[str, str]]:
    """Every form textarea's attributes, as (module, attribute text)."""
    found = []
    for name, source in _app_sources().items():
        for match in MARKUP_TEXTAREA.finditer(source):
            if not _in_comment(source, match.start()):
                found.append((name, match.group(1)))
        for match in BUILT_TEXTAREA.finditer(source):
            if NOT_A_FIELD not in match.group(1):
                found.append((name, match.group(1)))
    return found


def test_every_textarea_wears_one_of_the_three_standard_classes() -> None:
    textareas = _textareas()
    # The audit's fourteen form textareas (the runtime contracts are six
    # editors in one module), so a regex that stopped matching cannot pass.
    assert len(textareas) >= 18, textareas
    offenders = [(name, attrs) for name, attrs in textareas if not any(cls in attrs for cls in STANDARD)]
    assert offenders == [], f"textareas outside the standard: {offenders}"


def test_no_textarea_sets_its_own_rows() -> None:
    offenders = [name for name, attrs in _textareas() if re.search(r"\brows\s*[=:]", attrs)]
    assert offenders == [], f"a size class sets a textarea's height, not rows: {offenders}"


def test_the_copied_editor_class_string_is_gone() -> None:
    offenders = [name for name, source in _app_sources().items() if "TEXTAREA_CLS" in source]
    assert offenders == [], offenders
    controls = (CSS / "controls.css").read_text(encoding="utf-8")
    for rule in (".field-textarea {", '.field-textarea[data-size="long"]', ".field-mono {", ".text-editor {"):
        assert rule in controls, rule
    places = (CSS / "places.css").read_text(encoding="utf-8")
    for retired in (".assign-textarea", ".file-form-textarea", ".file-view-editor"):
        assert retired not in places, f"{retired} is a private copy of the field box"


# The forms whose textareas moved to the standard, whose sibling inputs and
# selects followed (and the file picker, the last other `.assign-input`).
SIBLING_FORMS = (
    "places/tasks/assign-form.js",
    "places/tasks/task-file-picker.js",
    "settings/settings-nest-git.js",
    "settings/settings-personalities.js",
    "settings/cli-policy/rule-form.js",
    "settings/cli-policy/policy-settings.js",
    "settings/settings-connections-form.js",
)
# Quote-aware, so a `>` inside an attribute value (`<url>` in a placeholder)
# does not end the tag early.
MARKUP_CONTROL = re.compile(r"""<(input|select)\b((?:[^>"']|"[^"]*"|'[^']*')*)>""")
BUILT_CONTROL = re.compile(r"h\('(input|select)',\s*\{(.*?)\}\)", re.S)
CONTROL_TYPE = re.compile(r"""\btype['"]?\s*[=:]\s*['"](\w+)""")
CONTROL_CLASS = re.compile(r"""\bclass['"]?\s*[=:]\s*(?:"([^"]*)"|'([^']*)')""")
# Checkboxes, switches and buttons keep their own look.
NOT_TEXT_LIKE = {"checkbox", "radio", "hidden", "button", "submit", "file", "range", "color"}
DRIFTED = ("bg-white", "border-bm-border", "assign-input", "assign-select")


def _sibling_controls() -> list[tuple[str, str, str]]:
    """Every text-like input and native select in SIBLING_FORMS, as (module, tag, class)."""
    found = []
    for name in SIBLING_FORMS:
        source = (JS / name).read_text(encoding="utf-8")
        for match in list(MARKUP_CONTROL.finditer(source)) + list(BUILT_CONTROL.finditer(source)):
            tag, attrs = match.group(1), match.group(2)
            kind = CONTROL_TYPE.search(attrs)
            if tag == "input" and kind and kind.group(1) in NOT_TEXT_LIKE:
                continue
            classes = CONTROL_CLASS.search(attrs)
            found.append((name, tag, (classes.group(1) or classes.group(2)) if classes else ""))
    return found


def test_sibling_inputs_and_selects_wear_the_shared_field_classes() -> None:
    controls = _sibling_controls()
    # 26 when this landed; a scanner that stopped matching cannot pass.
    assert len(controls) >= 26, controls
    offenders = [
        control for control in controls
        if ("field-select" if control[1] == "select" else "field-input") not in control[2].split()
        or any(drift in control[2] for drift in DRIFTED)
    ]
    assert offenders == [], f"inputs and selects outside the shared field look: {offenders}"
    for name in ("places/tasks/assign-form.js", "places/tasks/task-file-picker.js"):
        source = (JS / name).read_text(encoding="utf-8")
        assert "assign-input" not in source and "assign-select" not in source, name
    places = (CSS / "places.css").read_text(encoding="utf-8")
    assert ".assign-input" not in places and ".assign-select" not in places


def test_the_mono_rule_is_one_font_rule_for_any_field() -> None:
    offenders = [name for name, source in _app_sources().items() if "field-textarea-mono" in source]
    assert offenders == [], offenders
    assert "field-textarea-mono" not in (CSS / "controls.css").read_text(encoding="utf-8")
