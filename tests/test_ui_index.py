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
    # The two CLI-policy monoliths Phase 3C broke up. Their replacements live
    # under js/settings/cli-policy/, so neither prefix can match one of those.
    "js/cli-policy-section.js", "js/cli-policy-simulator.js",
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
    # Phase 3C: 1,426 lines of CLI policy, now nine files under
    # settings/cli-policy/. Left on disk they would be the next thing copied
    # from, the way app.js was.
    "cli-policy-section.js", "cli-policy-simulator.js",
    # Phase 4: the last of the dock era. app.js in particular sat unloaded for
    # three phases carrying `typeof X !== 'undefined'` guards for modules that
    # no longer existed — working-looking code that could not run.
    "app.js", "dock-manager.js", "company-view.js", "company-dashboard.js",
    # Phase 4 also split agent-panel.js into context/agent-*.js.
    "agent-panel.js", "utils.js",
)

# Deleted stylesheets. style.css was the dock era's, redistributed into the
# seven files linked from index.html.
DELETED_STYLESHEETS = ("style.css",)

SETTINGS_SCRIPTS = [
    "js/settings/cli-policy/shared.js",
    "js/settings/cli-policy/rules-table.js",
    "js/settings/cli-policy/rules-tab.js",
    "js/settings/cli-policy/rule-form.js",
    "js/settings/cli-policy/policy-settings.js",
    "js/settings/cli-policy/virtual-commands.js",
    "js/settings/cli-policy/simulator-output.js",
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


# What utils.js exported, minus the three overlay helpers Phase 4 retired.
# Every one of these must now have exactly one owner under core/.
UTILS_EXPORTS = {
    "core/format.js": (
        "escapeHtml", "formatRelativeTime", "formatNumber", "formatDuration",
        "formatTokenCount", "formatFileSize",
    ),
    "core/agent-status.js": (
        "normalizeAgent", "AGENT_COLOR_PALETTE", "nextUnusedAgentColor",
        "mergeRosterFromWorld", "getStatusColor", "getStatusClasses",
        "getStatusDot", "getStatusLabel",
    ),
    "core/specialty.js": (
        "inferWorkFamily", "specialtyFamily", "suggestFinishLine",
        "specialtyMatch", "specialtyRank", "specialtyWarningMessage",
        "doneClaimGuidance", "formatDoneClaim",
    ),
}


def _core_sources() -> dict[str, str]:
    core = ROOT / "ui" / "static" / "js" / "core"
    return {
        path.relative_to(ROOT / "ui" / "static" / "js").as_posix():
            path.read_text(encoding="utf-8")
        for path in sorted(core.rglob("*.js"))
    }


def test_utils_is_gone_and_its_exports_have_owners() -> None:
    """418 lines, 25 names, four unrelated concerns, and no owner.

    Splitting it is only half the job: the half that can rot silently is a name
    that ends up defined in two of the three modules, or defined in none
    because the split dropped it. So every surviving export is checked to be
    declared exactly once across all of core/, and in the module the split
    assigned it to.
    """
    js = ROOT / "ui" / "static" / "js"
    assert not (js / "utils.js").exists(), "utils.js is still on disk"
    assert "js/utils.js" not in _scripts(), "utils.js is still loaded"

    sources = _core_sources()
    for owner, names in UTILS_EXPORTS.items():
        assert owner in sources, f"{owner} does not exist"
        for name in names:
            declarations = [
                path for path, text in sources.items()
                if re.search(rf"^\s*(?:function|const)\s+{re.escape(name)}\b", text, re.M)
            ]
            assert declarations == [owner], (
                f"{name} should be declared once, in {owner}; found {declarations}"
            )
            # Declared is not exported. A name that survived the move but
            # never made it into the return block is unreachable.
            assert f"{name}," in sources[owner].rsplit("return {", 1)[-1], (
                f"{owner} declares {name} but does not export it"
            )

    # 22 names, three owners, no overlap.
    all_names = [name for names in UTILS_EXPORTS.values() for name in names]
    assert len(all_names) == 22
    assert len(set(all_names)) == 22


def test_no_module_uses_the_retired_overlay_helpers() -> None:
    """createModal / openOverlay / closeOverlay were the pre-redesign overlays.

    They had no focus trap and no Esc handling. core/overlays.js is the real
    one and has both, and by Phase 4 nothing called the old three. Deleting
    them is only safe while nothing calls them again, which is what this
    asserts — a module reaching for `BossModUtils` would be reaching for a
    global that no longer exists, and would fail at runtime rather than here.
    """
    js = ROOT / "ui" / "static" / "js"
    offenders = []
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(js).as_posix()
        if "BossModUtils" in text:
            offenders.append(f"{relative} names BossModUtils")
        for helper in ("openOverlay", "closeOverlay"):
            if helper in text:
                offenders.append(f"{relative} uses {helper}")
    assert offenders == [], "\n".join(offenders)

    # The surviving createModal is the focus-trapped one, and it is the only
    # one: a second implementation is how the old pair stayed alive.
    overlays = (js / "core" / "overlays.js").read_text(encoding="utf-8")
    assert "function createModal(" in overlays
    assert "trapKeydown" in overlays
    modals = [
        path.relative_to(js).as_posix() for path in sorted(js.rglob("*.js"))
        if "vendor" not in path.parts and "function createModal(" in path.read_text(encoding="utf-8")
    ]
    assert modals == ["core/overlays.js"], modals


def test_the_dock_era_is_gone_from_disk() -> None:
    """Unloaded is not deleted, and unloaded is where the bugs hide.

    These five were dropped from index.html in Phase 1b and left on disk for
    three phases. In that time app.js and company-view.js kept calling
    `BossModApp.refreshModelAvailability()` and guarding on globals that had
    stopped existing, and `test_health_ops_ui.py` had to carve them out of a
    tree-wide rule to stay green. A file nobody loads is a file nobody fixes
    and the first thing the next change copies from.
    """
    js = ROOT / "ui" / "static" / "js"
    css = ROOT / "ui" / "static" / "css"
    for name in ("app.js", "dock-manager.js", "company-view.js", "company-dashboard.js"):
        assert not (js / name).exists(), f"{name} is still on disk"
    for name in DELETED_STYLESHEETS:
        assert not (css / name).exists(), f"{name} is still on disk"

    # Only three top-level modules survive: the two the shell cannot start
    # without and the Tailwind mirror. Everything else lives in a directory
    # that says what it is for.
    top_level = sorted(path.name for path in js.glob("*.js"))
    assert top_level == ["api-auth.js", "api-client.js", "tailwind-config.js"], top_level

    # The globals they defined must not survive them anywhere in the tree.
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for symbol in ("BossModApp.", "DockManager.", "CompanyView.", "CompanyDashboard."):
            assert symbol not in text, f"{path.name} calls {symbol}"

    # No stylesheet may reference the deleted one either.
    for sheet in sorted(css.glob("*.css")):
        text = sheet.read_text(encoding="utf-8")
        for name in DELETED_STYLESHEETS:
            assert name not in text, f"{sheet.name} imports {name}"


# Where markup may be built from strings instead of BossModDom.h.
#
# `settings/` is the operator's decision, recorded in spec 6.7: the h() rule
# exists for escaping safety and its priority follows the DATA. Task titles,
# file names and agent output flow through places/, conversation/, context/,
# needs/ and shell/; settings render operator-entered configuration, and
# rewriting ~2,000 lines of the most feature-dense area of the app for a
# lower-risk data class is scope creep with real regression risk.
#
# The two `context/agent-form-*` modules are Phase 4's decision on the same
# question, made explicitly rather than left ambiguous (Task 4 Step 2). Every
# value they interpolate is operator-entered configuration — an agent name, a
# specialty, a connection or a personality created in Settings — and each one
# already goes through BossModFormat.escapeHtml. They are named INDIVIDUALLY,
# not by a `context/agent-*` prefix: agent-api.js, agent-edit.js,
# agent-fields.js, agent-form-bindings.js, agent-recovery.js and
# agent-submit.js are covered by the rule like everything else, and adding a
# seventh agent module must not silently inherit the exemption.
MARKUP_EXEMPT = (
    "context/agent-form-fields.js",
    "context/agent-form-advanced.js",
    "context/agent-form.js",
)

# Reading `div.innerHTML` back off a text node IS the escape. Banning the
# getter would ban the mechanism the rule exists to enforce.
MARKUP_ESCAPE_READER = "core/format.js"


def test_the_markup_exemption_stays_bounded() -> None:
    """innerHTML lives under settings/ and in three named files. Nowhere else.

    An exemption nobody wrote down is an exemption that spreads. This is the
    written form: everything outside the list builds nodes with BossModDom.h,
    where a task title or a file name cannot become markup no matter what an
    agent puts in it.

    The subject is the WRITE — `innerHTML =` and insertAdjacentHTML — because
    that is what turns a string into nodes. core/format.js reads
    `div.innerHTML` to escape, which is the opposite of a violation and is
    allowed by name.
    """
    js = ROOT / "ui" / "static" / "js"
    offenders = []
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        relative = path.relative_to(js).as_posix()
        if relative.startswith("settings/") or relative in MARKUP_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if "insertAdjacentHTML" in text:
            offenders.append(f"{relative} uses insertAdjacentHTML")
        if re.search(r"\.innerHTML\s*=", text):
            offenders.append(f"{relative} assigns innerHTML")
        if ".innerHTML" in text and relative != MARKUP_ESCAPE_READER:
            offenders.append(f"{relative} reads innerHTML")
    assert offenders == [], "\n".join(offenders)

    # The exemption is real: each named file must actually be building markup.
    # A stale entry is how a list of exceptions outlives the exception.
    for relative in MARKUP_EXEMPT:
        text = (js / relative).read_text(encoding="utf-8")
        assert re.search(r"innerHTML\s*=", text) or "return `" in text, (
            f"{relative} is exempt but builds no markup — drop it from the list"
        )
        assert "MARKUP EXEMPTION" in text, (
            f"{relative} is exempt but does not say so in its own docstring"
        )

    # And it is bounded: the other six agent modules are NOT exempt, which is
    # what stops "context/agent-*" from becoming the rule by accident.
    for path in sorted((js / "context").glob("agent-*.js")):
        relative = path.relative_to(js).as_posix()
        if relative in MARKUP_EXEMPT:
            continue
        assert "innerHTML" not in path.read_text(encoding="utf-8"), relative


# An interpolation that opens with `="${` lands inside a double-quoted
# attribute value. `esc` is the `const esc = BossModFormat.escapeHtml` alias
# the settings/cli-policy modules use — the same function, the same bug.
ATTRIBUTE_INTERPOLATION = re.compile(r'="\$\{[^}]*\b(?:escapeHtml|esc)\(')


def test_no_attribute_interpolation_uses_the_text_escaper() -> None:
    """Inside `attr="…"` the quote itself has to be escaped, and escapeHtml does not.

    escapeHtml sets `textContent` and reads `innerHTML` back, which is how a
    text node serialises: `&`, `<` and `>` become entities and `"` is left
    alone, because between tags a quote is just a quote. Inside a
    double-quoted attribute the quote is the TERMINATOR, so an operator- or
    agent-supplied `Bob" autofocus onfocus=alert(1) x="` closes the attribute
    early and the rest of the value is parsed as markup and an event handler
    — an XSS the text escaper cannot see.

    BossModFormat.escapeAttribute is escapeHtml plus `"` -> `&quot;`, and it
    is what every attribute site must call. escapeHtml stays correct for text
    BETWEEN tags, which is why this pattern is anchored on `="` and not on
    the call alone.
    """
    js = ROOT / "ui" / "static" / "js"
    offenders = []
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        relative = path.relative_to(js).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ATTRIBUTE_INTERPOLATION.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == [], (
        "escapeHtml in a double-quoted attribute — use escapeAttribute:\n"
        + "\n".join(offenders)
    )


# ─── The other half of the same rule: an attribute with NO escaper at all ───
#
# The pattern above catches the WRONG escaper. It cannot catch a missing one:
# `<option value="${c.id}">` matches nothing, and stayed that way in two agent
# form modules because the ids happen to be uuids. Nothing enforced that they
# would stay uuids.
#
# So this reads every `${…}` that lands inside a double-quoted attribute value
# — including one in the MIDDLE of a value, `id="reject-note-${req.id}"`, which
# an `="${` anchor never sees — and requires the expression to be provably
# unable to emit a `"`. Provable means one of exactly three things:
#
#   1. it calls escapeAttribute (or the `escAttr` alias), or
#   2. every value that can REACH the output is a literal, which is what makes
#      `${selected ? 'checked' : ''}` safe and `${value || ''}` not — the first
#      can only ever produce one of two authored strings, the second returns
#      whatever `value` holds, or
#   3. it is a constant declared in the same file whose initialiser is itself
#      provably literal, which is what `class="${SELECT_CLASS}"` is.
#
# Rule 2 is decided by reduction, not by a pattern: escaper calls and literals
# collapse to LITERAL, then `LITERAL + LITERAL`, `(LITERAL)` and
# `cond ? LITERAL : LITERAL` collapse again until nothing changes. An
# expression is safe when what is left is exactly LITERAL. Widening the regex
# instead would have made `${value || ''}` pass on the strength of its `''`.
#
# Rule 3 demands the file's ONLY binding of that name. settings/cli-policy/
# shared.js has two `const cls`, one of them a literal ternary and one of them
# `map[status] || '…'`, and a name-only rule let the safe one vouch for the
# other.
#
# NOT markup, and deliberately skipped: `[data-setting-card="${key}"]` inside a
# querySelector template, recognised by the `[` that opens it. Escaping there
# would be wrong — the DOM parser decodes entities, so a selector must carry
# the RAW value. Those sites have a real fragility of their own (a selector
# built from data breaks on a quote or a `]`), but it is a different bug with a
# different fix and this rule would only hide it.

LITERAL = "\x00"
ATTRIBUTE_OPEN = re.compile(r'([A-Za-z_:][-\w:.]*)\s*=\s*"')
ESCAPER_CALL = re.compile(r"\b(?:BossModFormat\.)?(?:escapeAttribute|escAttr)\s*\(")
LITERAL_VALUE = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`$]*`")
LITERAL_SCALAR = re.compile(r"\b(?:\d+(?:\.\d+)?|true|false)\b")
BINDING = r"\b(?:const|let|var)\s+{name}\b"


def _balanced(text: str, start: int, opener: str, closer: str) -> int:
    """Index one past the `closer` that matches the `opener` already consumed."""
    depth, index = 1, start
    while index < len(text) and depth:
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
        index += 1
    return index


def _attribute_interpolations(text: str) -> list[tuple[int, str]]:
    """Every (line, expression) `${…}` that renders inside `attr="…"`.

    A double-quoted JS string cannot interpolate, so a region opened by a plain
    `x = "…"` in code can never yield a hit; only template literals can, which
    is why this does not need to know which kind of quote it is inside.
    """
    found: list[tuple[int, str]] = []
    index, size = 0, len(text)
    while index < size:
        opener = ATTRIBUTE_OPEN.search(text, index)
        if not opener:
            return found
        if opener.start() and text[opener.start() - 1] == "[":
            index = opener.end()  # A CSS attribute selector, not markup.
            continue
        cursor = opener.end()
        while cursor < size:
            if text.startswith("${", cursor):
                end = _balanced(text, cursor + 2, "{", "}")
                found.append((text.count("\n", 0, cursor) + 1, text[cursor + 2:end - 1]))
                cursor = end
                continue
            if text[cursor] == "\\":
                cursor += 2
                continue
            if text[cursor] == '"':
                break
            cursor += 1
        index = cursor + 1
    return found


def _reduce(expression: str, constants: set[str]) -> str:
    """Collapse an expression to LITERAL when nothing data-bearing can escape it."""
    text = expression.replace("?.", ".")  # So `a?.b ? x : y` still reads as one ternary.
    while True:
        call = ESCAPER_CALL.search(text)
        if not call:
            break
        text = text[:call.start()] + LITERAL + text[_balanced(text, call.end(), "(", ")"):]
    text = LITERAL_VALUE.sub(LITERAL, text)
    text = LITERAL_SCALAR.sub(LITERAL, text)
    for name in constants:
        text = re.sub(rf"\b{re.escape(name)}\b", LITERAL, text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(rf"\(\s*{LITERAL}\s*\)", LITERAL, text)
        text = re.sub(rf"{LITERAL}(?:\s*(?:\+|\|\||\?\?|&&)\s*{LITERAL})+", LITERAL, text)
        text = re.sub(rf"[^?:]*\?\s*{LITERAL}\s*:\s*{LITERAL}", LITERAL, text)
        text = text.strip()
    return text


def _literal_constants(source: str) -> set[str]:
    """Names bound exactly once in this file to a provably literal expression."""
    safe = set()
    for match in re.finditer(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*", source):
        name = match.group(1)
        if len(re.findall(BINDING.format(name=re.escape(name)), source)) != 1:
            continue
        depth, end = 0, match.end()
        while end < len(source):
            character = source[end]
            if character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
            elif character == ";" and depth <= 0:
                break
            end += 1
        if _reduce(source[match.end():end], set()) == LITERAL:
            safe.add(name)
    return safe


def test_no_attribute_interpolation_is_left_unescaped() -> None:
    """Every value interpolated into an attribute must be unable to close it.

    The sibling test above proves nobody reached for escapeHtml here. This one
    proves nobody reached for nothing at all, which is the failure it could not
    see: `<option value="${c.id}">` in agent-form-connections.js and the same
    line in agent-form-advanced.js were writing a database id straight into an
    attribute with no escaper on the path, and the only thing keeping that
    honest was a `VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()` that no
    frontend rule mentioned.
    """
    js = ROOT / "ui" / "static" / "js"
    offenders = []
    checked = 0
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        constants = _literal_constants(source)
        for number, expression in _attribute_interpolations(source):
            checked += 1
            if _reduce(expression, constants) != LITERAL:
                relative = path.relative_to(js).as_posix()
                offenders.append(f"{relative}:{number}: ${{{' '.join(expression.split())}}}")
    # A scanner that stops finding attributes proves nothing by being green.
    assert checked > 50, f"only {checked} attribute interpolations found — scanner broke"
    assert offenders == [], (
        "interpolated into an attribute with no escaper — use escapeAttribute, "
        "or make every branch a literal:\n" + "\n".join(offenders)
    )


def test_every_module_stays_under_the_line_cap() -> None:
    """One assertion over the whole tree, not five over five directories.

    places/, conversation/, context/, needs/ and settings/ each had their own
    copy of this rule, so the modules that belonged to none of them — the top
    level, core/, shell/ — were never checked. shell.js was 321 lines when this
    was written and nothing said so.
    """
    js = ROOT / "ui" / "static" / "js"
    oversized = {}
    for path in sorted(js.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines >= 300:
            oversized[path.relative_to(js).as_posix()] = lines
    assert oversized == {}, f"over the 300-line cap: {oversized}"

    # The rule is worth nothing if it covers three files. Every directory the
    # spec names must actually be on disk and carry modules.
    directories = {path.relative_to(js).parts[0] for path in js.rglob("*.js")
                   if "vendor" not in path.parts and len(path.relative_to(js).parts) > 1}
    assert directories == {
        "core", "shell", "conversation", "context", "needs", "places", "settings",
    }, directories


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
# CliPolicySection and CliPolicySimulator joined in Phase 3C. Both were always
# cross-module calls — settings-view.js into the section, the section into the
# simulator — but neither matched the BossMod* pattern, so the load-order guard
# below could not see them. Now that all nine CLI-policy modules load from a
# different directory than their callers, an unseen ordering bug is exactly the
# failure this phase could introduce.
NON_PREFIXED_GLOBALS = (
    "SettingsView", "AgentPanel", "CliPolicySection", "CliPolicySimulator",
)

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
    cannot throw. Two dock-era ones survive in the Connections section, which
    Phase 3C split in two: BossModApp in both halves and BossModApi in the
    form. Phase 4's cleanup owns them.
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
