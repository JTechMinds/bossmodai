"""index.html hosts the shell: no dock markup, auth first, Settings intact."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "ui" / "templates" / "index.html"

# Every structural remnant of the dock layout. The dock is retired, not hidden.
DOCK_MARKUP = (
    "dock-slot", "dock-pane", "dock-pane-pool", "dock-slot-tabbar", "dock-slot-body",
    'data-slot="', "data-slot-tabs", "data-slot-body", 'id="slot-left"', 'id="slot-center"',
    'id="slot-right"', "center-mode-toggle", "company-subtab-dropdown", "company-subtab-item",
    'id="agent-toolbar"', 'id="agent-chips"', 'id="agent-info-bar"', "agent-subview-btn",
    'id="mobile-sheet"', "mobile-tab-btn", "Swipe up to expand", 'id="panel-left"',
    'id="panel-map"', 'id="panel-activity"', "pane-open-mark",
)

# Modules the shell replaced. The files stay on disk until Phases 2-4 delete
# them; what must go is the script tag that loads them into the running app.
RETIRED_SCRIPTS = (
    "js/dock-manager.js", "js/app.js", "js/company-view.js", "js/company-dashboard.js",
    "js/agent-context.js", "js/channels-view.js", "js/channel-thread-dom.js",
    "js/activity.js", "js/canvas.js", "js/diagnostics.js",
    # The dock-era company panes. Phase 2B had to name these individually
    # rather than use the prefix "js/company-", because the desk browser was
    # still loading the real company-file-viewer.js. Phase 3B moved that file to
    # places/files/file-viewer.js and DELETED all six dock-era modules, so the
    # prefix is true again — and it is now the stronger assertion, because it
    # also catches a company-* module nobody thought to name here.
    "js/company-",
)

# Deleted outright in Phase 3B, not merely unloaded. A script tag is not the
# only way one of these could come back: an unlisted file left on disk is how
# `app.js` sat there for two phases looking like it still mattered.
DELETED_MODULES = (
    "company-files.js", "company-file-ops.js", "company-file-viewer.js",
    "company-metrics.js", "activity.js", "diagnostics.js",
)

SETTINGS_SCRIPTS = [
    "js/cli-policy-simulator.js",
    "js/cli-policy-section.js",
    "js/settings-shared.js",
    "js/settings-connections.js",
    "js/settings-personalities.js",
    "js/settings-system.js",
    "js/settings-prompt-template.js",
    "js/settings-advanced.js",
    "js/settings-runtime-contracts.js",
    "js/settings-telegram.js",
    "js/settings-view.js",
]


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _scripts() -> list[str]:
    return re.findall(r"static_url\('([^']+\.js)'\)", _html())


def test_no_dock_markup_or_dock_scripts_remain() -> None:
    html = _html()
    for needle in DOCK_MARKUP:
        assert needle not in html, f"dock markup survived: {needle}"
    for needle in RETIRED_SCRIPTS:
        assert needle not in html, f"retired module still loaded: {needle}"


def test_the_dock_era_panes_phase_3b_replaced_are_gone_from_disk() -> None:
    """Files, Metrics, Activity and Diagnostics are places now.

    Their dock-era modules are deleted, not just unloaded: ~2,850 lines of
    markup-from-strings with private copies of shared helpers is exactly what
    stays working, stays wrong, and gets copied from.
    """
    for name in DELETED_MODULES:
        assert not (ROOT / "ui" / "static" / "js" / name).exists(), (
            f"{name} is still on disk"
        )


def test_api_auth_is_the_first_script() -> None:
    """It patches window.fetch and window.WebSocket; anything before it is unauthenticated."""
    scripts = _scripts()
    assert scripts, "index.html loads no scripts"
    assert scripts[0] == "js/api-auth.js", f"first script is {scripts[0]}"
    assert scripts.index("js/api-auth.js") < scripts.index("js/api-client.js")
    assert scripts[-1] == "js/shell/shell.js", (
        "the shell boots last, after every module it wires"
    )


def test_settings_takeover_and_banners_survive() -> None:
    html = _html()
    assert 'id="settings-layout"' in html
    assert 'id="settings-nav"' in html
    assert 'id="settings-content"' in html
    assert 'id="main-layout"' in html, "SettingsView.open() toggles #main-layout"

    assert 'id="runtime-pause-banner"' in html
    assert 'id="no-model-banner"' in html
    assert "Connect a model in Settings" in html
    assert 'id="no-model-banner-settings"' in html

    scripts = _scripts()
    positions = [scripts.index(name) for name in SETTINGS_SCRIPTS]
    assert positions == sorted(positions), (
        "settings and CLI-policy scripts must keep their relative order"
    )


# Cross-module globals that are not named BossMod*. Each is a real module
# object another loaded script calls into.
# CompanyFileViewer left this list in Phase 3B: the shared viewer is
# BossModFileViewer now, which the BossMod* pattern already covers.
NON_PREFIXED_GLOBALS = ("SettingsView", "AgentPanel")

MODULE_DEF = re.compile(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\(", re.M)
MODULE_USE = re.compile(
    r"(?<![\w$.])(BossMod[A-Za-z0-9_$]*|" + "|".join(NON_PREFIXED_GLOBALS) + r")\s*\."
)


def _loaded_app_scripts() -> list[str]:
    """The non-vendor scripts index.html loads, in load order."""
    return [name for name in _scripts() if "vendor/" not in name]


def test_every_module_global_a_loaded_script_calls_is_actually_loaded() -> None:
    """A script tag that is missing must fail a test, not just break the app.

    Phase 2B shipped shell.js calling BossModNeeds.createNeedsStore() before
    index.html loaded needs-store.js. The suite stayed green and the app was
    dead on boot, because nothing here read the two facts together. This does:
    it walks the real load order, records what each file defines, and requires
    every unguarded cross-module call to resolve to something defined by an
    earlier (or the same) script.

    References written as `typeof X !== 'undefined'` are excluded on purpose:
    that is the documented optional-capability pattern, and such a reference
    cannot throw. Two dock-era ones survive in settings-connections.js
    (BossModApp, BossModApi); Phase 4's cleanup owns them.
    """
    scripts = _loaded_app_scripts()
    assert scripts, "index.html loads no application scripts"

    sources: list[tuple[str, str]] = []
    for name in scripts:
        path = ROOT / "ui" / "static" / name
        assert path.exists(), f"index.html loads {name}, which does not exist"
        sources.append((name, path.read_text(encoding="utf-8")))

    defined_at: dict[str, int] = {}
    for index, (_, text) in enumerate(sources):
        for symbol in MODULE_DEF.findall(text):
            defined_at.setdefault(symbol, index)

    problems: list[str] = []
    for index, (name, text) in enumerate(sources):
        for symbol in sorted(set(MODULE_USE.findall(text))):
            if f"typeof {symbol}" in text:
                continue
            if symbol not in defined_at:
                problems.append(f"{name} calls {symbol}.*, which no loaded script defines")
            elif defined_at[symbol] > index:
                problems.append(
                    f"{name} calls {symbol}.*, but {scripts[defined_at[symbol]]} defines it later"
                )
    assert not problems, "\n".join(problems)


def test_shell_stylesheets_are_linked() -> None:
    html = _html()
    for sheet in ("css/tokens.css", "css/base.css", "css/shell.css"):
        assert f"static_url('{sheet}')" in html, f"{sheet} is not linked"
    assert "css/style.css" not in html, "the dock-era stylesheet is retired"
