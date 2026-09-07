"""shell.js swaps places safely: unmount first, focus moved, errors contained."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_shell_harness.cjs"

MODULES = [
    ("core", "dom.js"), ("core", "store.js"), ("core", "bus.js"),
    ("shell", "places.js"), ("shell", "shell.js"),
]


def test_navigate_lifecycle_and_leak_guard() -> None:
    args = ["node", str(HARNESS)] + [str(JS / d / f) for d, f in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "unmountsBeforeMount": True,
        "focusesHeading": True,
        "noBusLeakAfter20Swaps": True,
        "containsMountErrors": True,
    }


def test_shell_passes_ctx_and_never_reaches_for_globals() -> None:
    """Places receive ctx; the shell must not wire modules by global name."""
    source = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "mount(" in source and "ctx" in source
    for forbidden in ("AgentContext", "CompanyTasks", "ActivityLog", "DockManager", "BossModApp"):
        assert forbidden not in source, f"shell.js must not reference {forbidden}"


def test_shell_does_not_reintroduce_dock_concepts() -> None:
    source = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    for forbidden in ("slot", "dock", "centerMode", "companyTab"):
        assert forbidden.lower() not in source.lower(), (
            f"the dock layout is retired; '{forbidden}' must not appear"
        )
