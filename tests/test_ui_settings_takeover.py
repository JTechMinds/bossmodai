"""The Settings takeover gets the screen, and keeps getting it.

The takeover was in the DOM, was `display: flex`, and was zero pixels tall at
every viewport. `SettingsView.open()` hid the app frame by adding Tailwind's
`.hidden` — specificity (0,1,0) — to `#main-layout`, whose `display: grid`
comes from an ID rule at (1,0,0). A class cannot outrank an id, so the frame
never went away; `#main-layout` is `flex: 1 1 auto` and the takeover is
`flex: 1 1 0%`, so in the column's negative free space the takeover shrank to
nothing and the frame kept its size.

The fix carries the view on `data-view` on <body> and lets shell.css decide,
which is why most of this file is source-level: a fake DOM has no cascade, so
the only place the winning rule can be proven to win is the stylesheet.

The first test is the regression test. It does not check that shell.css still
says what it says today — it measures every rule that sets `display` on
`#main-layout` and requires the takeover's rule to outrank all of them, which
is the invariant rather than the text. A future `#main-layout[data-anything]`
that set `display` would have to be written to lose to it, and a bare class or
attribute selector reintroducing the bug fails here.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "ui" / "static" / "css"
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js_settings_takeover_harness.cjs"

HARNESS_MODULES = [
    JS / "core" / "format.js",
    JS / "settings" / "settings-view.js",
]

# The rule the invariant rests on, spelled exactly as shell.css spells it.
TAKEOVER_RULE = 'body[data-view="settings"] #main-layout'
APP_RULE = 'body[data-view="app"] #settings-layout'

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_COMBINATOR = re.compile(r"\s*[>+~]\s*|\s+")
_ATTRIBUTE = re.compile(r"\[[^\]]*\]")
_PSEUDO_ELEMENT = re.compile(r"::[A-Za-z-]+")
_PSEUDO_CLASS = re.compile(r"(?<!:):[A-Za-z-]+(?:\([^)]*\))?")
_ID = re.compile(r"#[A-Za-z_-][\w-]*")
_CLASS = re.compile(r"\.[A-Za-z_-][\w-]*")
_TYPE = re.compile(r"[A-Za-z][\w-]*")
_DISPLAY = re.compile(r"(?<![\w-])display\s*:")


def _specificity(selector: str) -> tuple[int, int, int]:
    """(ids, classes, types) for one selector, per CSS Selectors level 4 §17.

    Each category is consumed in turn and removed from the string, so what is
    left over when the ids, classes, attributes and pseudos are gone is exactly
    the type selectors — which is the only way to count them without a regex
    that also matches the inside of an attribute value.
    """
    working = selector.strip()
    attributes = _ATTRIBUTE.findall(working)
    working = _ATTRIBUTE.sub(" ", working)
    pseudo_elements = _PSEUDO_ELEMENT.findall(working)
    working = _PSEUDO_ELEMENT.sub(" ", working)
    pseudo_classes = _PSEUDO_CLASS.findall(working)
    working = _PSEUDO_CLASS.sub(" ", working)
    ids = _ID.findall(working)
    working = _ID.sub(" ", working)
    classes = _CLASS.findall(working)
    working = _CLASS.sub(" ", working)
    types = _TYPE.findall(working)
    return (
        len(ids),
        len(classes) + len(attributes) + len(pseudo_classes),
        len(types) + len(pseudo_elements),
    )


def _rules(css: str) -> Iterator[tuple[str, str]]:
    """Every style rule as (selector list, declarations), @media flattened in.

    A media query decides WHEN a rule applies and never how specific it is, so
    a rule inside one competes with a rule outside it on exactly the terms
    `_specificity` measures. Flattening is what stops the scan below from
    missing a `display` set on `#main-layout` at one breakpoint only.
    """
    index = 0
    prelude_start = 0
    while index < len(css):
        char = css[index]
        if char == "{":
            prelude = css[prelude_start:index].strip()
            depth = 1
            cursor = index + 1
            while cursor < len(css) and depth:
                if css[cursor] == "{":
                    depth += 1
                elif css[cursor] == "}":
                    depth -= 1
                cursor += 1
            assert depth == 0, f"unterminated block after {prelude!r}"
            body = css[index + 1 : cursor - 1]
            if prelude.startswith("@"):
                yield from _rules(body)
            else:
                yield prelude, body
            index = prelude_start = cursor
        elif char == "}":
            index += 1
            prelude_start = index
        else:
            index += 1


def _shell_css() -> str:
    return _COMMENT.sub("", (CSS / "shell.css").read_text(encoding="utf-8"))


def _subject(selector: str) -> str:
    """The compound a selector actually styles — its rightmost one."""
    return _COMBINATOR.split(selector.strip())[-1]


def _selectors_setting_display_on(element: str) -> list[str]:
    """Every selector whose SUBJECT is `element` and whose rule sets display."""
    found = []
    for selector_list, declarations in _rules(_shell_css()):
        if not _DISPLAY.search(declarations):
            continue
        for selector in selector_list.split(","):
            if element in _subject(selector):
                found.append(selector.strip())
    return found


def _block(marker: str) -> str:
    """The body of one brace-matched at-rule, named by its full prelude."""
    css = _shell_css()
    opening = css.index(marker) + len(marker) - 1
    depth = 0
    for index in range(opening, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[opening + 1 : index]
    raise AssertionError(f"unterminated {marker}")


def _declarations(css: str, selector: str) -> str:
    for selector_list, declarations in _rules(css):
        if selector in [part.strip() for part in selector_list.split(",")]:
            return declarations
    raise AssertionError(f"no rule for {selector}")


def _harness() -> dict:
    result = subprocess.run(
        ["node", str(HARNESS)] + [str(path) for path in HARNESS_MODULES],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_specificity_counts_what_the_cascade_counts() -> None:
    """The measuring stick, checked against the four selectors it is used on.

    A specificity function that is wrong in the same direction as the bug would
    report the bug fixed, so it is calibrated here before anything is judged
    with it.
    """
    assert _specificity("#main-layout") == (1, 0, 0)
    assert _specificity(".hidden") == (0, 1, 0)
    assert _specificity('#main-layout[data-context="off"] .app-context') == (1, 2, 0)
    assert _specificity(TAKEOVER_RULE) == (1, 1, 1)
    # The two shapes the fix deliberately did NOT take: both lose to an id.
    assert _specificity('[data-view="settings"] #main-layout') == (1, 1, 0)
    assert _specificity("#main-layout.hidden") == (1, 1, 0)


def test_the_takeover_rule_outranks_every_display_on_the_frame() -> None:
    """The invariant, not today's text: nothing may set display on the frame
    at a specificity the takeover cannot beat.

    This is the test the bug would have failed. `.hidden` on `#main-layout` is
    (0,1,0) against an id rule's (1,0,0) and lost every time; the shape here is
    `body[…] #id` — (1,1,1) — which also survives a future
    `#main-layout[data-anything]` at (1,1,0).
    """
    competitors = _selectors_setting_display_on("#main-layout")
    assert TAKEOVER_RULE in competitors, "the takeover no longer hides the app frame"

    winner = _specificity(TAKEOVER_RULE)
    losers = [selector for selector in competitors if selector != TAKEOVER_RULE]
    assert losers, "the scan found nothing to outrank — the frame sets no display at all"
    for selector in losers:
        assert winner > _specificity(selector), (
            f"{selector!r} at {_specificity(selector)} is not outranked by "
            f"{TAKEOVER_RULE!r} at {winner}"
        )

    # And the winning rule hides, rather than merely existing.
    assert re.search(r"display\s*:\s*none", _declarations(_shell_css(), TAKEOVER_RULE))


def test_the_app_view_hides_the_takeover() -> None:
    """The other half of the same fact. One attribute, two rules that read it.

    The takeover carried `hidden` in the markup before; dropping it is what
    makes `data-view` the only switch. Without this rule the takeover would be
    on screen from the first paint.
    """
    assert re.search(r"display\s*:\s*none", _declarations(_shell_css(), APP_RULE))


def test_the_markup_states_the_initial_view_and_owns_no_second_switch() -> None:
    html = HTML.read_text(encoding="utf-8")
    body = re.search(r"<body\b[^>]*>", html)
    assert body, "index.html has no <body> tag"
    assert 'data-view="app"' in body.group(0), body.group(0)

    takeover = re.search(r'<div id="settings-layout"[^>]*>', html)
    assert takeover, 'index.html no longer declares id="settings-layout"'
    classes = re.search(r'class="([^"]*)"', takeover.group(0))
    assert classes, "the takeover carries no class attribute"
    assert "hidden" not in classes.group(1).split(), (
        "the takeover still carries `hidden`, so its visibility has two owners"
    )


def test_the_view_swap_is_one_attribute_write() -> None:
    """settings-view.js names the attribute and neither panel.

    The old swap resolved `#main-layout`, `#settings-layout` and a
    `#mobile-sheet` the redesign had already deleted, then wrote `.hidden` onto
    all three. Naming none of them is what makes a desync impossible: there is
    one write, of one fact.
    """
    source = (JS / "settings" / "settings-view.js").read_text(encoding="utf-8")
    assert "document.body.dataset.view" in source

    for banned in ("main-layout", "mainLayout", "settings-layout", "settingsLayout",
                   "mobile-sheet", "mobileSheet"):
        assert banned not in source, f"settings-view.js still reaches for {banned}"
    assert "classList" not in source, "settings-view.js writes a class onto a panel again"


def test_the_tab_bar_runway_is_reserved_once() -> None:
    """Below 768px the place nav is a fixed bottom bar, and <body> pads for it.

    This replaced a test that required BOTH frames to pad by --tabbar, on the
    premise that the frames scroll under the bar. They do not: the footer sits
    in the flow between the frames and the bar, and it padded a literal 60px
    for the bar as well. So the runway was reserved twice, and the frames' copy
    was a blank 56px band above the footer on every phone screen. That test
    enforced the bug; this one enforces the fix: one owner (<body>, the flex
    column all three sit in), sized by the token the bar itself is sized by,
    and no second copy anywhere in the block.
    """
    assert re.search(r"--tabbar:\s*[0-9.]+px", (CSS / "tokens.css").read_text(
        encoding="utf-8")), "tokens.css names no --tabbar"

    narrow = _block("@media (max-width: 767px) {")
    for selector, prop in (("body", "padding-bottom"), (".place-nav", "min-height")):
        value = re.search(rf"(?<![\w-]){prop}\s*:\s*([^;]+)",
                          _declarations(narrow, selector))
        assert value, f"{selector} sets no {prop} at this width"
        assert value.group(1).strip() == "var(--tabbar)", (
            f"{selector} {prop} is {value.group(1).strip()}, not var(--tabbar)"
        )

    # No second runway: no frame and not the footer pads for the bar again.
    for selector_list, declarations in _rules(narrow):
        selectors = [part.strip() for part in selector_list.split(",")]
        for owner in ("#main-layout", "#settings-layout", ".app-footer"):
            if any(_subject(selector).startswith(owner) for selector in selectors):
                assert not re.search(r"(?<![\w-])padding-bottom\s*:", declarations), (
                    f"{selector_list} pads for the tab bar a second time"
                )
    assert "60px" not in narrow, "the footer's literal runway is back"


def test_open_and_close_write_the_view_and_nothing_else() -> None:
    """The JS half, from tests/js_settings_takeover_harness.cjs.

    The cascade is asserted above because a fake DOM has no cascade. What it
    can answer is whether the two functions write the one attribute, whether
    `isOpen()` agrees with what they wrote, and whether the options an opener
    hands in survive exactly one render — `places/files/host-roots.js` opens
    the CLI policy section with a tab and a field to focus, and an option that
    outlived its render would re-focus that field on every later tab switch.
    """
    payload = _harness()
    assert payload["noViewBeforeOpen"] is True
    assert payload["openSetsTheView"] is True
    assert payload["rendersTheDefaultSection"] is True
    assert payload["closeSetsTheView"] is True
    assert payload["closeRefreshesModelAvailability"] is True
    assert payload["optionsReachTheSection"] is True
    assert payload["optionsAreOneShot"] is True
    assert payload["listenerSeesOpen"] is True
    assert payload["listenerSeesClose"] is True
    assert payload["disposerStopsTheListener"] is True


def test_a_phone_shows_the_list_or_one_section() -> None:
    """Below 768px the 224px section list left a section 194px, one word a
    line. The list and the section take turns at full width instead, and which
    is up is `data-pane` on the head and the body, written by settings-view.js
    alone: the gear opens on the list, a direct opener on its section, a tap
    in the list shows that section, and the ‹ goes back.
    """
    payload = _harness()
    assert payload["gearOpensOnTheList"] is True
    assert payload["directOpenLandsOnTheSection"] is True
    assert payload["switchShowsTheSection"] is True
    assert payload["backShowsTheList"] is True

    # The two columns are the takeover's own classes now, not Tailwind's
    # fixed `w-56` and `p-6`, so the stylesheet can change their shape.
    html = HTML.read_text(encoding="utf-8")
    body = re.search(r'<div id="settings-body" class="settings-body">(.*?)\n        </div>', html, re.S)
    assert body, "index.html no longer declares the takeover's body"
    assert "w-56" not in body.group(1) and "p-6" not in body.group(1), body.group(1)
    assert '<div class="settings-nav-col">' in body.group(1)
    assert '<div id="settings-content" class="settings-content"></div>' in body.group(1)
    back = re.search(r"<button[^>]*id=\"settings-back\"[^>]*>", html, re.S)
    assert back and 'aria-label="Back to all settings"' in back.group(0)

    css = _shell_css()
    assert "var(--settings-nav)" in _declarations(css, ".settings-nav-col")
    narrow = _block("@media (max-width: 767px) {")
    assert re.search(r"display\s*:\s*none", _declarations(
        narrow, '.settings-body[data-pane="nav"] .settings-content'))
    assert re.search(r"display\s*:\s*none", _declarations(
        narrow, '.settings-body[data-pane="section"] .settings-nav-col'))
    assert re.search(r"display\s*:\s*inline-flex", _declarations(
        narrow, '.settings-head[data-pane="section"] .settings-back'))
    # The CLI rules table is wider than a phone and scrolls in its own wrap.
    assert re.search(r"overflow-x\s*:\s*auto", _declarations(css, ".bm-rules-table-wrap"))

    # The ‹ is wired by the shell to the view's own door.
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "requireElement('settings-back').addEventListener('click', SettingsView.showNav);" in shell


def test_the_takeover_carries_its_own_title_and_exit() -> None:
    """A full-screen view that hides the frame says where it is and offers the
    way out in itself.

    marketplace.js states the rule for its own takeover — it "carries its exit
    in its own top-right corner" — and Settings was the one surface in the app
    obeying neither half of it. The gear and Escape both worked; both were
    invisible, which is not the same as being available.
    """
    html = HTML.read_text(encoding="utf-8")

    layout = re.search(r'<div id="settings-layout"[^>]*class="([^"]*)"', html)
    assert layout, 'index.html no longer declares id="settings-layout"'
    assert "flex-col" in layout.group(1).split(), (
        "the takeover must stack its bar above its two columns"
    )

    title = re.search(r'<h2 class="settings-title">([^<]+)</h2>', html)
    assert title, "the takeover declares no title, so it never says where you are"
    assert title.group(1).strip() == "Settings"

    dismiss = re.search(r"<button[^>]*id=\"settings-dismiss\"[^>]*>", html, re.S)
    assert dismiss, "the takeover carries no visible exit"
    assert 'aria-label="Close settings"' in dismiss.group(0), dismiss.group(0)
    assert 'type="button"' in dismiss.group(0), dismiss.group(0)


def test_the_exit_shares_the_gear_and_escape_entry_point() -> None:
    """Three doors out, one function behind them.

    shell.js already says the gear and Escape share an entry point "so they
    cannot disagree". The ✕ is the third door and goes through the same one
    rather than reaching for SettingsView.close() on its own.
    """
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    wiring = re.search(
        r"requireElement\('settings-dismiss'\)\s*\.addEventListener\('click',\s*(\w+)\)",
        shell)
    assert wiring, "shell.js never wires #settings-dismiss to anything"
    assert wiring.group(1) == "closeSettings", (
        f"the ✕ calls {wiring.group(1)} rather than the shared closeSettings"
    )


def test_navigation_and_floor_moves_leave_through_the_shared_exit() -> None:
    """Choosing a place or another floor leaves Settings by the ✕'s own door.

    The header stays on screen over the takeover, so its place nav, the
    shortcuts, the bell's Show me, a toast's Open and the floor switcher could
    all change what was underneath while Settings stayed up. They now leave it
    through one injected object whose close is the shared closeSettings, so
    leaving by navigation means exactly what the ✕ means, model refresh
    included. Only shell.js names SettingsView; the navigator, the header and
    the switcher load before it and receive the object.
    """
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    declared = re.search(r"const settingsTakeover = \{([^}]*)\};", shell)
    assert declared, "shell.js declares no shared settingsTakeover"
    assert re.search(r"\bclose:\s*closeSettings\b", declared.group(1)), declared.group(0)
    for call in ("BossModNavigator.createNavigator(", "BossModHeader.mount("):
        args = shell.split(call, 1)[1].split("});", 1)[0]
        assert "takeover: settingsTakeover" in args, f"{call} is not handed the shared exit"

    header = (JS / "shell" / "header.js").read_text(encoding="utf-8")
    assert re.search(r"BossModFloorSwitcher\.mount\(\{[^}]*\btakeover\b[^}]*\}\)", header), (
        "the header does not hand the takeover on to the floor switcher"
    )

    # Add agent opens its menu over Chat; a Chat under Settings is not on screen.
    on_hire = shell.split("onHire: () => {", 1)[1].split("},", 1)[0]
    assert "settingsOpen" in on_hire, on_hire


def test_the_banner_door_only_opens() -> None:
    """"Connect a model in Settings" lands on AI Connections, every time.

    The banner sits outside the takeover, so it stays visible while Settings
    is up, and it used to share the gear's toggle: pressed then, it closed
    Settings, the opposite of what it says. It is open-only now and names its
    section, so it also lands there on a phone and over another section. The
    gear keeps the toggle.
    """
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    wiring = re.search(
        r"requireElement\('no-model-banner-settings'\)\s*\.addEventListener\('click',\s*(\w+)\)",
        shell)
    assert wiring, "shell.js never wires #no-model-banner-settings to anything"
    assert wiring.group(1) == "openModelSettings", (
        f"the banner calls {wiring.group(1)} rather than the open-only openModelSettings"
    )
    body = shell.split("function openModelSettings() {", 1)[1].split("\n        }", 1)[0]
    assert "SettingsView.open('connections')" in body, body
    assert "close" not in body, f"the banner's door must never close Settings: {body}"

    header_args = shell.split("BossModHeader.mount(", 1)[1].split("});", 1)[0]
    assert "openSettings: toggleSettings" in header_args, "the gear lost its toggle"


def test_the_gear_reports_whether_the_takeover_is_open() -> None:
    """The rail toggle already announces its state; the gear did not.

    So a screen reader could not tell Settings was open — the same invisibility
    the ✕ fixes for sighted users. The state is subscribed rather than set on
    click because two other modules open the takeover directly.
    """
    header = (JS / "shell" / "header.js").read_text(encoding="utf-8")
    gear = re.search(r"class:\s*'header-icon-btn header-gear'.*?\}\)", header, re.S)
    assert gear, "header.js no longer builds the gear"
    assert "aria-expanded" in gear.group(0), "the gear reports no open/closed state"
    assert re.search(r"store\.subscribe\(\(s\) => s\.settingsOpen", header), (
        "the gear's state is set once rather than kept in step with the store"
    )

    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "settingsOpen: false" in shell, "the store carries no settingsOpen"
    assert "SettingsView.onViewChange(" in shell, (
        "nothing projects the takeover's state onto the store, so it will drift"
    )


def test_the_takeover_bar_sits_on_the_apps_rhythm_line() -> None:
    """--bar is the line the header, the conversation chrome and the rail's
    search row all end on. A settings bar NEAR that line rather than on it is
    exactly the drift --bar was introduced to stop.
    """
    css = _shell_css()
    assert "var(--bar)" in _declarations(css, ".settings-head"), (
        "the takeover's bar is not sized by --bar"
    )
    assert re.search(r"min-height:\s*0", _declarations(css, ".settings-body")), (
        "without min-height: 0 the columns grow past the takeover instead of scrolling"
    )
