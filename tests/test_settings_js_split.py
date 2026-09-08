"""HA-STRUCT-P1-04 — settings + CLI-policy JS split keeps public IIFE names."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"

SECTION_FILES = {
    "settings/settings-connections.js": "ConnectionsSection",
    "settings/settings-personalities.js": "PersonalitiesSection",
    "settings/settings-system.js": "SystemSection",
    "settings/settings-prompt-template.js": "PromptTemplateSection",
    "settings/settings-advanced.js": "AdvancedSystemSection",
    "settings/settings-runtime-contracts.js": "RuntimeContractsSection",
    "settings/settings-telegram.js": "TelegramSection",
}

SHELL_GLOBALS = {
    "settings/settings-view.js": "SettingsView",
    "settings/cli-policy/section.js": "CliPolicySection",
    "settings/cli-policy/simulator.js": "CliPolicySimulator",
}

# The halves the two oversized sections were split into in Phase 3C. They are
# not section entry points (SettingsView never calls them), so they are kept
# out of SECTION_FILES, whose contract is "renders a settings section"; their
# load order and their IIFE names are asserted below instead.
SPLIT_HALVES = {
    "settings/settings-connections-form.js": "BossModConnectionForm",
    "settings/settings-runtime-contracts-actions.js": "BossModRuntimeContractActions",
}

REQUIRED_SCRIPTS = [
    "js/api-auth.js",
    "js/api-client.js",
    "js/settings/cli-policy/shared.js",
    "js/settings/cli-policy/rules-table.js",
    "js/settings/cli-policy/rules-tab.js",
    "js/settings/cli-policy/rule-form.js",
    "js/settings/cli-policy/policy-settings.js",
    "js/settings/cli-policy/virtual-commands.js",
    "js/settings/cli-policy/simulator-output.js",
    "js/settings/cli-policy/simulator-shell.js",
    "js/settings/cli-policy/simulator-run.js",
    "js/settings/cli-policy/simulator.js",
    "js/settings/cli-policy/approvals.js",
    "js/settings/cli-policy/section.js",
    "js/settings/settings-shared.js",
    "js/settings/settings-connections-form.js",
    "js/settings/settings-connections.js",
    "js/settings/settings-personalities.js",
    "js/settings/settings-system.js",
    "js/settings/settings-prompt-template.js",
    "js/settings/settings-advanced.js",
    "js/settings/settings-runtime-contracts-actions.js",
    "js/settings/settings-runtime-contracts.js",
    "js/settings/settings-telegram.js",
    "js/settings/settings-view.js",
    "js/shell/shell.js",
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
    shell = _read("settings/settings-view.js")
    assert "const SettingsView = (() => {" in shell
    assert "function switchSection(" in shell
    for global_name in SECTION_FILES.values():
        assert f"const {global_name}" not in shell
        assert f"{global_name}.render(content)" in shell
    assert "CliPolicySection.render(content, pendingOptions)" in shell
    assert "function initResizeHandle" not in shell


def test_shared_resize_helper_lives_outside_sections() -> None:
    shared = _read("settings/settings-shared.js")
    assert "function initResizeHandle(" in shared
    assert "const SettingsView" not in shared
    prompt = _read("settings/settings-prompt-template.js")
    contracts = _read("settings/settings-runtime-contracts.js")
    assert "initResizeHandle(" in prompt
    assert "initResizeHandle(" in contracts


def test_simulator_extracted_from_cli_policy_section() -> None:
    """Every assertion below still holds; three of them moved file.

    Phase 3C split the simulator into a shell, a runner and an output painter.
    The request and its dry-run-by-default body belong to the runner now, so
    they are asserted against simulator-run.js. Nothing is dropped: the section
    still must not carry the endpoint, and the shell still must carry the
    Execute-for-real control that is the only way to turn the dry run off.
    """
    section = _read("settings/cli-policy/section.js")
    simulator = _read("settings/cli-policy/simulator.js")
    runner = _read("settings/cli-policy/simulator-run.js")
    assert "function renderSimulatorTab" not in section
    assert "CliPolicySimulator.render(content)" in section
    assert "const CliPolicySimulator = (() => {" in simulator
    assert "dry_run = true" in runner or "body.dry_run = true" in runner
    assert "body.execute = true" in runner
    assert "Execute for real" in simulator
    assert "/api/cli-policy/simulator/execute" in runner
    assert "/api/cli-policy/simulator/execute" not in section
    assert "/api/cli-policy/simulator/execute" not in simulator
    # The shell is what decides which of the two a keypress is.
    assert "_executeSimCommand(cmd, { execute: true })" in simulator
    assert "_executeSimCommand(cmd, { execute: false })" in simulator

    # The seam itself: only the shell names the terminal's DOM. The runner and
    # the painter are handed where to write and who to write as, which is what
    # makes this a split by responsibility rather than a cut by line number.
    painter = _read("settings/cli-policy/simulator-output.js")
    # Phase 4 moved the chrome MARKUP to simulator-shell.js when the load
    # path grew a real error state. simulator.js still resolves both nodes —
    # it is the only module that touches the terminal's DOM — and the shell is
    # the only one that declares them.
    chrome = _read("settings/cli-policy/simulator-shell.js")
    assert 'id="cli-sim-output"' in chrome, "the chrome declares the output pane"
    assert 'id="cli-sim-agent"' in chrome, "the chrome declares the agent selector"
    assert "cli-sim-output" in simulator, "the controller resolves the output pane"
    assert "cli-sim-agent" in simulator, "the controller resolves the agent selector"
    # The chrome is markup only: it binds nothing and runs nothing.
    assert "addEventListener" not in chrome
    assert "apiFetch" not in chrome
    for name, source in (("simulator-run.js", runner),
                         ("simulator-output.js", painter)):
        assert "cli-sim-output" not in source, f"{name} reaches into the shell's DOM"
        assert "cli-sim-agent" not in source, f"{name} reads the shell's agent selector"


def _try_blocks(text: str):
    """Yield each brace-matched `try { ... }` body in `text`."""
    for match in re.finditer(r"\btry\s*\{", text):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    yield text[: match.start()].count("\n") + 1, text[start : index + 1]
                    break


def test_settings_loads_surface_failures() -> None:
    """apiFetch does not throw, so a `try` alone catches nothing.

    Every settings load wrapped `await res.json()` in a try around a
    NON-throwing apiFetch. A 4xx therefore reached the parser, the error body
    parsed cleanly as JSON, and the tab rendered as though the server had
    returned nothing: an empty Connections list, a Rules tab with no rules, a
    Telegram tab with every field blank. The only 4xx that ever reached the
    catch was one whose body was not JSON.

    Recorded by Phase 3C, fixed in Phase 4 (spec 12). The rule is one line:
    inside any `try` that parses a response, the response's `ok` must be
    consulted. Checking it AFTER the parse is fine and is what the save paths
    do — they need the body to build the message.
    """
    root = JS / "settings"
    offenders = []
    for path in sorted(root.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for line, body in _try_blocks(text):
            if not re.search(r"await\s+\w+\.json\(\)", body):
                continue
            if not re.search(r"\.ok\b", body):
                offenders.append(f"{path.relative_to(root)}:{line}")
    assert offenders == [], (
        "a settings load parses a response without checking res.ok: "
        + ", ".join(offenders)
    )

    # A detected failure that does nothing is the same defect. Both Edit
    # buttons used to read `if (res.ok) openForm(await res.json());` and
    # silently do nothing otherwise — the operator clicked and the app sat
    # there with no explanation.
    assert "function showRowError(" in _read("settings/settings-shared.js")
    for name in ("settings/settings-connections.js", "settings/settings-personalities.js"):
        source = _read(name)
        assert "showRowError(container," in source, name
        assert "if (res.ok) openForm" not in source, name
        assert "if (res.ok) renderForm" not in source, name

    # The badge said in its own docstring that it "stays as it is" on failure
    # and then hid itself, because `{detail: ...}.length` is undefined. Pending
    # approvals vanishing from the nav is the opposite of leaving it alone.
    section = _read("settings/cli-policy/section.js")
    badge = section.split("async function refreshApprovalBadge() {", 1)[1]
    assert badge.index("if (!res.ok) return;") < badge.index("await res.json()")

    # A failed roster read is not cached as "there are no agents": the old code
    # set agentsFetched either way, so one bad response meant raw uuids in
    # every policy tab until the page was reloaded.
    shared = _read("settings/cli-policy/shared.js")
    fetch_agents = shared.split("async function fetchAgents() {", 1)[1].split("\n    }\n", 1)[0]
    assert fetch_agents.index("agentsCache = await res.json();") < fetch_agents.index(
        "agentsFetched = true;"
    ), "a failed read must not be cached as a successful one"

    # And the simulator tells the operator the read failed rather than telling
    # them to create an agent they already have.
    simulator = _read("settings/cli-policy/simulator.js")
    assert "The simulator could not load" in simulator
    assert "No agents found" in simulator
    assert simulator.index("if (failed) {") < simulator.index("if (agentsCache.length === 0) {\n            _renderNotice(el, 'bot'")


def test_index_loads_split_scripts_in_dependency_order() -> None:
    sources = _script_sources()
    for required in REQUIRED_SCRIPTS:
        assert required in sources, required
    index = {name: sources.index(name) for name in REQUIRED_SCRIPTS}
    assert index["js/api-auth.js"] < index["js/api-client.js"]
    assert index["js/api-client.js"] < index["js/settings/cli-policy/simulator.js"]
    assert index["js/settings/cli-policy/shared.js"] < index["js/settings/cli-policy/section.js"]
    assert index["js/settings/cli-policy/rules-table.js"] < index["js/settings/cli-policy/rules-tab.js"]
    assert index["js/settings/cli-policy/rules-tab.js"] < index["js/settings/cli-policy/section.js"]
    assert index["js/settings/cli-policy/rule-form.js"] < index["js/settings/cli-policy/section.js"]
    assert index["js/settings/cli-policy/policy-settings.js"] < index["js/settings/cli-policy/section.js"]
    assert index["js/settings/cli-policy/virtual-commands.js"] < index["js/settings/cli-policy/section.js"]
    assert index["js/settings/cli-policy/simulator.js"] < index["js/settings/cli-policy/section.js"]
    # The terminal chrome must be defined before the module that paints it.
    assert (index["js/settings/cli-policy/simulator-shell.js"]
            < index["js/settings/cli-policy/simulator.js"])
    assert index["js/settings/settings-shared.js"] < index["js/settings/settings-prompt-template.js"]
    assert index["js/settings/settings-shared.js"] < index["js/settings/settings-runtime-contracts.js"]
    for section_file in SECTION_FILES:
        assert index[f"js/{section_file}"] < index["js/settings/settings-view.js"]
    assert index["js/settings/cli-policy/section.js"] < index["js/settings/settings-view.js"]
    assert index["js/settings/settings-view.js"] < index["js/shell/shell.js"]
    # A split half must load before the section that calls into it.
    assert (index["js/settings/settings-connections-form.js"]
            < index["js/settings/settings-connections.js"])
    assert (index["js/settings/settings-runtime-contracts-actions.js"]
            < index["js/settings/settings-runtime-contracts.js"])


def test_settings_and_cli_policy_use_shared_api_client() -> None:
    """HA-STRUCT-P1-08: settings/CLI policy go through apiFetch (token wrap still under it)."""
    names = [
        *SECTION_FILES,
        "settings/settings-view.js",
        "settings/cli-policy/section.js",
        "settings/cli-policy/simulator.js",
    ]
    for name in names:
        source = _read(name)
        assert "XMLHttpRequest" not in source, name
        if "/api/" in source:
            assert "apiFetch(" in source, name
            assert "fetch(" not in source, name


def test_split_modules_stay_focused() -> None:
    """The two limits here are contracts, not ceilings.

    settings-view.js is a router over the section modules and settings-shared.js
    holds one helper; both staying small is the design. The three ceilings that
    used to sit beside them — cli-policy-section.js < 1100,
    cli-policy-simulator.js < 500, and every SECTION_FILES entry < 400 —
    existed only because the two monoliths did. Phase 3C deleted them, and
    test_settings_tree_has_no_file_over_the_cap below is strictly tighter than
    all three: 300 lines, applied to every file in the tree rather than to a
    list somebody has to remember to extend.
    """
    assert _line_count("settings/settings-view.js") < 160
    assert _line_count("settings/settings-shared.js") < 80


def test_settings_tree_has_no_file_over_the_cap() -> None:
    """One rule, the same one places/ lives under."""
    oversized = {
        path.relative_to(JS).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted((JS / "settings").rglob("*.js"))
        if len(path.read_text(encoding="utf-8").splitlines()) >= 300
    }
    assert oversized == {}, f"over the 300-line cap: {oversized}"

    # The rule above is scoped by directory where the ceilings it replaced were
    # scoped by filename. That is only stronger while every named module is
    # inside the directory it scans, so that is asserted rather than assumed.
    scanned = {
        path.relative_to(JS).as_posix()
        for path in (JS / "settings").rglob("*.js")
    }
    named = {*SECTION_FILES, *SHELL_GLOBALS, *SPLIT_HALVES}
    assert named <= scanned, f"outside the capped tree: {sorted(named - scanned)}"


def test_cli_policy_section_is_a_router() -> None:
    """The section owns the shell, the tab bar and the badge — no tab bodies.

    Before Phase 3C this file was 1,012 lines because every tab rendered here.
    A tab that creeps back in would still work, which is exactly why it needs a
    test rather than a convention.
    """
    section = _read("settings/cli-policy/section.js")

    for name in ("renderRulesTab", "renderSettingsTab",
                 "renderVirtualCommandsTab", "renderApprovalsTab",
                 "showRuleForm", "renderTableBody", "renderApprovalCard"):
        assert f"function {name}" not in section, f"{name} body is back in section.js"

    # Every tab reaches its own module.
    for call in ("BossModCliPolicyRules.renderRulesTab(",
                 "BossModCliPolicyVirtual.renderVirtualCommandsTab(",
                 "BossModCliPolicySettings.renderSettingsTab(",
                 "CliPolicySimulator.render(",
                 "BossModCliPolicyApprovals.renderApprovalsTab("):
        assert call in section, f"section.js does not delegate {call}"

    # A registry, so a sixth tab is an entry rather than an edit to a switch.
    assert "const TABS = [" in section
    assert "switch (activeTab)" not in section

    # None of the tabs' own markup lives here.
    for marker in ("cli-rules-tbody", "cli-rule-form-slot", "data-setting-card",
                   "data-approve", "data-reject-confirm", "cli-sim-terminal"):
        assert marker not in section, f"tab markup survived in section.js: {marker}"


def test_cli_policy_helpers_have_exactly_one_home() -> None:
    """The shared helpers are shared, not copied.

    statusBadge, flashBorder and applySettingSaveResult were in the same
    closure as the five tabs that used them. Splitting the tabs into files is
    exactly the moment a helper gets duplicated instead of imported.

    Phase 4 removed the fourth, `tierBadge`, which had no caller anywhere (spec
    12), along with the TIER_BORDER / TIER_LABEL constants that existed only to
    feed it. It is asserted GONE rather than dropped from the list — an unused
    export is the thing the next tab copies from, and the rules table already
    has its own tier chips.
    """
    files = sorted((JS / "settings" / "cli-policy").glob("*.js"))
    assert files, "no cli-policy modules found"
    for helper in ("statusBadge", "applySettingSaveResult"):
        definers = [
            path.name for path in files
            if f"function {helper}(" in path.read_text(encoding="utf-8")
        ]
        assert definers == ["shared.js"], f"{helper} is defined in {definers}"

    # flashBorder is no longer exported: applySettingSaveResult, in the same
    # file, is its only caller and always was. A shared export invited a second
    # way to report a save — one that flashes a border and never writes the
    # status line a screen reader can read.
    shared = _read("settings/cli-policy/shared.js")
    assert "function flashBorder(" in shared
    assert "        flashBorder,\n" not in shared, "flashBorder is exported again"
    for path in files:
        if path.name == "shared.js":
            continue
        assert "flashBorder" not in path.read_text(encoding="utf-8"), path.name

    for dead in ("tierBadge", "TIER_BORDER", "TIER_LABEL"):
        offenders = [
            path.name for path in files
            if dead in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"{dead} came back in {offenders}"


def test_split_halves_define_their_own_iifes() -> None:
    """No mapping was dropped when the two oversized sections were split.

    settings-connections.js and settings-runtime-contracts.js keep their
    ConnectionsSection / RuntimeContractsSection entry points; what left them
    is a second module each, asserted here so a half cannot quietly lose its
    global and be reached only by luck of load order.
    """
    for filename, global_name in SPLIT_HALVES.items():
        source = _read(filename)
        assert f"const {global_name} = (() => {{" in source, filename
    connections = _read("settings/settings-connections.js")
    assert "BossModConnectionForm.renderForm(conn, { container, onDone: renderList })" in connections
    contracts = _read("settings/settings-runtime-contracts.js")
    assert "BossModRuntimeContractActions.bindActions({ onRefresh: () => render(el) })" in contracts


def test_all_split_files_exist_and_are_nonempty() -> None:
    for name in [*SECTION_FILES, *SHELL_GLOBALS, *SPLIT_HALVES,
                 "settings/settings-shared.js"]:
        path = JS / name
        assert path.is_file(), name
        assert path.stat().st_size > 80, name
