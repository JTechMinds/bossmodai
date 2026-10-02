"""One textarea standard across the app.

A textarea's look and size come from one CSS class plus a size modifier, never
from a call site's `rows`, pixels or a Tailwind utility string. The audit that
introduced this found fourteen form textareas in five styles: two private
copies of the field box (a hardcoded radius, a different fill), seven Tailwind
strings on the decorative --line border (one hardcoding `bg-white`), one
utility string copied between two settings sections, and heights from 2 to 12
rows set per call site. Three classes remain:

- `field-textarea` (controls.css): a form field, short by default and
  `data-size="long"` for prompts and descriptions; `field-textarea-mono` for
  code, JSON, keys and templates;
- `edit-field-multiline` (controls.css): prose edited in place in a record's
  Edit mode;
- `text-editor` (controls.css): a full-pane editor.
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
    for rule in (".field-textarea {", '.field-textarea[data-size="long"]', ".field-textarea-mono {", ".text-editor {"):
        assert rule in controls, rule
    places = (CSS / "places.css").read_text(encoding="utf-8")
    for retired in (".assign-textarea", ".file-form-textarea", ".file-view-editor"):
        assert retired not in places, f"{retired} is a private copy of the field box"
