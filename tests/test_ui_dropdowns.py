"""Every dropdown in the app is BossModMenuSelect, never a native <select>.

WebKitGTK paints a native <select> as a GTK combo box: taller than the fields
beside it, with its own arrow, and no stylesheet reaches it. The app's
dropdown is core/menu-select.js — the panel the chat's agent-name menu opens —
in its 'field' look inside forms and settings and its toolbar look in rows of
buttons. A form reads it through the hidden `<input name>` the control owns
(menu-select.js's form contract), so `FormData` and `[name=…]` readers did not
change; a writer goes through the control, never the input, or the trigger
would name one choice while the form sends another.

These are static gates; the control's behaviour is proved on built nodes in
tests/js_toolbar_controls_harness.cjs.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"

NATIVE_SELECT = re.compile(r"<select\b|h\(\s*['\"]select['\"]")
# The form values the menu-selects own. A direct `.value =` on any of them
# moves the value without the trigger.
MENU_NAMES = (
    r"personality_id|desk|communication_[^\"'\]]*|tier|match_mode|agent_id"
    r"|connection_id|thinking_social|thinking_work"
)
DIRECT_WRITE = re.compile(
    r"\[name=\\?[\"'](?:" + MENU_NAMES + r")\\?[\"']\][^;\n]*?\.value\s*=(?!=)"
)


def _app_sources() -> dict[str, str]:
    return {
        path.relative_to(JS).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(JS.rglob("*.js"))
        if "vendor" not in path.parts
    }


def _in_comment(source: str, offset: int) -> bool:
    line = source[source.rfind("\n", 0, offset) + 1:offset].strip()
    return line.startswith(("*", "//", "/*"))


def test_no_module_builds_a_native_select() -> None:
    offenders = [
        f"{name}:{source.count(chr(10), 0, match.start()) + 1}"
        for name, source in _app_sources().items()
        for match in NATIVE_SELECT.finditer(source)
        if not _in_comment(source, match.start())
    ]
    assert offenders == [], f"native <select> outside comments: {offenders}"


def test_the_native_select_look_is_gone() -> None:
    for root in (JS, CSS):
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".js", ".css") or "vendor" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            assert "field-select" not in text, path.relative_to(ROOT).as_posix()
            assert "SELECT_CLS" not in text, path.relative_to(ROOT).as_posix()


def test_no_module_writes_a_menu_selects_form_value_directly() -> None:
    offenders = [
        f"{name}: {match.group(0)}"
        for name, source in _app_sources().items()
        for match in DIRECT_WRITE.finditer(source)
        if not _in_comment(source, match.start())
    ]
    assert offenders == [], f"write through BossModMenuSelect.instanceFor(...).setValue: {offenders}"


def test_the_direct_write_gate_would_catch_one() -> None:
    """The regex above matches the shapes it exists to ban."""
    for line in (
        "formRoot.querySelector('[name=\"personality_id\"]').value = '';",
        "form.querySelector(`[name=\"communication_${key}\"]`).value = comm[key];",
        "slot.querySelector('[name=\"tier\"]').value = 'glob';",
    ):
        assert DIRECT_WRITE.search(line), line
    assert not DIRECT_WRITE.search("const v = form.querySelector('[name=\"desk\"]').value;")
    assert not DIRECT_WRITE.search("if (form.querySelector('[name=\"desk\"]').value === '') {}")


def test_menu_select_exposes_the_form_contract() -> None:
    source = (JS / "core" / "menu-select.js").read_text(encoding="utf-8")
    for member in ("instanceFor", "setValue(", "getLabel(", "getOptions("):
        assert member in source, member
    assert "return { create, instanceFor };" in source
