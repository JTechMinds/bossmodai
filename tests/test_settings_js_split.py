"""HA-STRUCT-P1-04 — settings + CLI-policy JS split keeps public IIFE names."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"

SECTION_FILES = {
    "settings-connections.js": "ConnectionsSection",
    "settings-personalities.js": "PersonalitiesSection",
    "settings-system.js": "SystemSection",
    "settings-prompt-template.js": "PromptTemplateSection",
    "settings-advanced.js": "AdvancedSystemSection",
    "settings-runtime-contracts.js": "RuntimeContractsSection",
    "settings-telegram.js": "TelegramSection",
}

SHELL_GLOBALS = {
    "settings-view.js": "SettingsView",
    "cli-policy-section.js": "CliPolicySection",
    "cli-policy-simulator.js": "CliPolicySimulator",
}

REQUIRED_SCRIPTS = [
    "js/api-auth.js",
    "js/api-client.js",
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
    "js/app.js",
]


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _line_count(name: str) -> int:
    return _read(name).count("\n")


def _script_sources() -> list[str]:
    html = HTML.read_text(encoding="utf-8")
    return re.findall(r"static_url\('([^']+)'\)", html)


def test_section_modules_export_expected_iifes() -> None:
    for filename, global_name in {**SECTION_FILES, **SHELL_GLOBALS}.items():
        source = _read(filename)
        assert f"const {global_name} = (() => {{" in source, filename
        assert "return { render" in source or "return { open, close" in source, filename


def test_settings_shell_no_longer_owns_section_iifes() -> None:
    shell = _read("settings-view.js")
    assert "const SettingsView = (() => {" in shell
    assert "function switchSection(" in shell
    for global_name in SECTION_FILES.values():
        assert f"const {global_name}" not in shell
        assert f"{global_name}.render(content)" in shell
    assert "CliPolicySection.render(content)" in shell
    assert "function initResizeHandle" not in shell


def test_shared_resize_helper_lives_outside_sections() -> None:
    shared = _read("settings-shared.js")
    assert "function initResizeHandle(" in shared
    assert "const SettingsView" not in shared
    prompt = _read("settings-prompt-template.js")
    contracts = _read("settings-runtime-contracts.js")
    assert "initResizeHandle(" in prompt
    assert "initResizeHandle(" in contracts


def test_simulator_extracted_from_cli_policy_section() -> None:
    section = _read("cli-policy-section.js")
    simulator = _read("cli-policy-simulator.js")
    assert "function renderSimulatorTab" not in section
    assert "CliPolicySimulator.render(content)" in section
    assert "const CliPolicySimulator = (() => {" in simulator
    assert "dry_run = true" in simulator or "body.dry_run = true" in simulator
    assert "body.execute = true" in simulator
    assert "Execute for real" in simulator
    assert "/api/cli-policy/simulator/execute" in simulator
    assert "/api/cli-policy/simulator/execute" not in section


def test_index_loads_split_scripts_in_dependency_order() -> None:
    sources = _script_sources()
    for required in REQUIRED_SCRIPTS:
        assert required in sources, required
    index = {name: sources.index(name) for name in REQUIRED_SCRIPTS}
    assert index["js/api-auth.js"] < index["js/api-client.js"]
    assert index["js/api-client.js"] < index["js/cli-policy-simulator.js"]
    assert index["js/cli-policy-simulator.js"] < index["js/cli-policy-section.js"]
    assert index["js/settings-shared.js"] < index["js/settings-prompt-template.js"]
    assert index["js/settings-shared.js"] < index["js/settings-runtime-contracts.js"]
    for section_file in SECTION_FILES:
        assert index[f"js/{section_file}"] < index["js/settings-view.js"]
    assert index["js/cli-policy-section.js"] < index["js/settings-view.js"]
    assert index["js/settings-view.js"] < index["js/app.js"]


def test_settings_and_cli_policy_use_shared_api_client() -> None:
    """HA-STRUCT-P1-08: settings/CLI policy go through apiFetch (token wrap still under it)."""
    names = [
        *SECTION_FILES,
        "settings-view.js",
        "cli-policy-section.js",
        "cli-policy-simulator.js",
    ]
    for name in names:
        source = _read(name)
        assert "XMLHttpRequest" not in source, name
        if "/api/" in source:
            assert "apiFetch(" in source, name
            assert "fetch(" not in source, name


def test_split_modules_stay_focused() -> None:
    assert _line_count("settings-view.js") < 160
    assert _line_count("settings-shared.js") < 80
    assert _line_count("cli-policy-simulator.js") < 500
    # Rules / settings / virtual / approvals still share one IIFE on purpose.
    assert _line_count("cli-policy-section.js") < 1100
    for filename in SECTION_FILES:
        assert _line_count(filename) < 400, filename


def test_all_split_files_exist_and_are_nonempty() -> None:
    for name in [*SECTION_FILES, *SHELL_GLOBALS, "settings-shared.js"]:
        path = JS / name
        assert path.is_file(), name
        assert path.stat().st_size > 80, name
