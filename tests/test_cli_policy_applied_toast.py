"""Default Policy and CLI rule writes confirm with a toast, not a second Save."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css" / "overlays.css"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_applied_toast_renders_saved_applied_and_replaces_itself() -> None:
    harness = Path(__file__).resolve().parent / "js_cli_policy_applied_toast_harness.cjs"
    result = subprocess.run(
        ["node", str(harness), str(JS / "settings" / "cli-policy" / "shared.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"ok": True, "copy": "Saved / Applied", "replaced": True}


def test_default_policy_and_rule_writes_announce_without_a_second_save() -> None:
    """Autosave stays. The toast follows the write; nothing re-PUTs the key."""
    settings = _read("settings/cli-policy/policy-settings.js")
    rules = _read("settings/cli-policy/rules-tab.js")
    form = _read("settings/cli-policy/rule-form.js")
    css = CSS.read_text(encoding="utf-8")

    assert "addEventListener('change'" in settings
    assert "if (key === 'cli_default_policy') announceApplied();" in settings
    assert settings.count("announceApplied()") == 1
    # The select writes itself on change. A button that only re-PUTs the same
    # key would be a submit control or a control labelled Save.
    assert 'type="submit"' not in settings
    assert ">Save<" not in settings
    assert "Save</button>" not in settings

    for call_after in (
        "table.renderTableBody(rulesCache);\n                    announceApplied();",
        "await apiFetchOk('/api/cli-policy/rules/seed-defaults', { method: 'POST' });\n                announceApplied();",
    ):
        assert call_after in rules
    assert rules.count("announceApplied()") == 3
    assert ">Save<" not in rules

    assert "announceApplied();\n                onSaved();" in form
    assert form.count("announceApplied()") == 1

    assert ".cli-applied-toast-host" in css
    assert ".cli-applied-toast" in css
