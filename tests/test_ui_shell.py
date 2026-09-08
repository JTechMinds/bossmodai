"""shell.js swaps places safely: unmount first, focus moved, errors contained."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_shell_harness.cjs"

# Phase 4 split shell.js: booting the app and swapping a place are separate
# jobs on separate clocks, and the tree-wide 300-line cap made the seam
# mandatory. The navigator is what this harness drives.
MODULES = [
    ("core", "dom.js"), ("core", "store.js"), ("core", "bus.js"),
    ("shell", "places.js"), ("shell", "navigator.js"),
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
    """Places receive ctx; the shell must not wire modules by global name.

    Asserted over BOTH halves of the split. The navigator is what mounts a
    place, and boot is what builds the ctx it mounts with; a global name
    reached for in either one is the silent no-op app.js used to be.
    """
    navigator = (JS / "shell" / "navigator.js").read_text(encoding="utf-8")
    boot = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "mount(" in navigator and "ctx" in navigator
    assert "BossModNavigator.createNavigator(" in boot
    for name, source in (("navigator.js", navigator), ("shell.js", boot)):
        for forbidden in ("AgentContext", "CompanyTasks", "ActivityLog",
                          "DockManager", "BossModApp"):
            assert forbidden not in source, f"{name} must not reference {forbidden}"


def test_shell_does_not_reintroduce_dock_concepts() -> None:
    for name in ("shell.js", "navigator.js"):
        source = (JS / "shell" / name).read_text(encoding="utf-8")
        for forbidden in ("slot", "dock", "centerMode", "companyTab"):
            assert forbidden.lower() not in source.lower(), (
                f"the dock layout is retired; '{forbidden}' must not appear in {name}"
            )
