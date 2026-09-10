"""The shared visual vocabulary: one avatar, one switch, one button, one ring.

The redesign phases built the right structure but let each module author its own
look. These assertions are the written form of the vocabulary that replaced it —
a derived avatar tint that is *proven* AA rather than assumed, a single toggle
control behind three surfaces, and a sprite ring that makes an arbitrary
operator-chosen colour safe on the office floor.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HTML = ROOT / "ui" / "templates" / "index.html"
AVATAR_HARNESS = Path(__file__).resolve().parent / "js_avatar_harness.cjs"

# The floor, the desks, and the chairs the agent sprite is drawn on top of.
# Measured: every colour in every candidate palette scores 1.1-1.5:1 against
# the chair, so no palette can make the sprite visible. The ring can.
OFFICE_SURFACES = {"floor": "#e5e7eb", "desk": "#d97706", "chair": "#b45309"}


AVATAR_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "avatar.js",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(source: str) -> str:
    """The source with its comments stripped.

    A boundary assertion has to read what the module DOES. Several of these
    modules explain in prose exactly which global they are forbidden to touch,
    and a naive substring check reads that explanation as the violation.
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.M)


def _app_js() -> list[Path]:
    return [p for p in sorted(JS.rglob("*.js")) if "vendor" not in p.parts]


def _avatar_payload() -> dict:
    args = ["node", str(AVATAR_HARNESS)] + [str(path) for path in AVATAR_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_agent_sprites_are_ringed_against_every_surface() -> None:
    """The ring, not the palette, is what keeps an agent visible.

    Asserted on the body fill specifically: the status dot has been ringed
    since Phase 1 and would otherwise satisfy a looser grep.
    """
    source = _read(JS / "places/office/canvas-sprites.js")
    assert "// body" in source and "// status dot" in source, (
        "the body and status-dot blocks must be named, or this test cannot "
        "tell which of the two it is reading"
    )
    body = source.split("// body", 1)[1].split("// status dot", 1)[0]
    assert "ctx2d.strokeStyle = palette.pill;" in body
    assert "ctx2d.lineWidth = 2;" in body
    assert "ctx2d.stroke();" in body

    # The measurement the ring exists to answer, kept beside the assertion so a
    # future reader can re-derive it rather than trust the comment.
    assert set(OFFICE_SURFACES) == {"floor", "desk", "chair"}


def test_avatar_tints_clear_aa() -> None:
    """The derivation is proven over the colour space, not over our eight seeds.

    `agent.color` is arbitrary operator-set hex, so an avatar built from it
    can only be trusted if the pale-tint/dark-ink pair is guaranteed rather
    than spot-checked. The harness sweeps a 216-colour grid and measures every
    pair with its own copy of the WCAG formula.
    """
    payload = _avatar_payload()
    assert payload["swept"] == 216
    assert payload["meetsAA"] is True, (
        f"worst pair {payload['worstHex']} at {payload['worstRatio']}:1"
    )
    assert payload["worstRatio"] >= 4.5

    # No colour is a real state, not an error: an agent may simply have none.
    assert payload["neutralWhenNull"] is True
    assert payload["neutralWhenEmpty"] is True
    # A colour that cannot be parsed IS a data bug, and says so once.
    assert payload["malformedIsNeutral"] is True
    assert payload["malformedLogs"] is True
    assert payload["absentColourIsSilent"] is True


def test_palette_seeds_need_no_darkening() -> None:
    """What the operator picks is what the avatar renders.

    Every seed must already clear AA on its own derived tint, so tintFor()
    returns the seed unchanged. A seed that needs darkening renders as a
    different colour than the swatch that offered it.
    """
    payload = _avatar_payload()
    assert payload["paletteSize"] == 8
    assert payload["paletteDriftFree"] is True, payload["paletteDrift"]


def test_the_seed_picker_survived_the_palette_swap() -> None:
    """nextUnusedAgentColor() reads the palette by reference, not by value.

    Swapping the eight seeds must not have changed how a new hire is coloured,
    and the stored colours it compares against arrive from the database in
    whatever case they were written in.
    """
    payload = _avatar_payload()
    assert payload["nextUnusedOnEmptyRoster"] is True
    assert payload["nextUnusedSkipsTaken"] is True
    assert payload["nextUnusedWrapsWhenFull"] is True
    assert payload["nextUnusedIgnoresTheAgentBeingEdited"] is True


def test_the_palette_and_its_labels_name_the_same_eight_colours() -> None:
    """The form must not offer a swatch labelled with a raw hex.

    agent-fields.js falls back to the value when a key is missing, so a palette
    edit that forgets the labels degrades quietly instead of failing. This is
    the assertion that makes it loud.
    """
    seeds = re.findall(
        r"'(#[0-9a-f]{6})',\s*//",
        _read(JS / "core/agent-status.js").split("AGENT_COLOR_PALETTE = [", 1)[1]
        .split("];", 1)[0],
    )
    labels = dict(re.findall(
        r"'(#[0-9a-f]{6})':\s*'([A-Za-z]+)'",
        _read(JS / "context/agent-fields.js").split("AGENT_COLOR_NAMES = {", 1)[1]
        .split("};", 1)[0],
    ))
    assert len(seeds) == 8, seeds
    assert sorted(labels) == sorted(seeds), (sorted(labels), sorted(seeds))
    assert len(set(labels.values())) == 8, labels


def test_avatar_create_builds_one_node_two_ways() -> None:
    """Decorative beside a name; a real button when it is the only affordance.

    The decorative form is `aria-hidden`, because the text beside it already
    names the person and a second announcement is noise. The interactive form
    is a <button> carrying its own accessible name, so Enter and Space work
    with no key handling of its own (SC 2.1.1).
    """
    payload = _avatar_payload()

    assert payload["decorativeTag"] == "SPAN"
    assert payload["decorativeHidden"] is True
    assert payload["decorativeClass"] == "avatar avatar-chip"
    assert payload["decorativeInitial"] == "J"
    assert payload["decorativeStyle"].startswith("background:#")

    assert payload["interactiveTag"] == "BUTTON"
    assert payload["interactiveLabel"] == "Open Laura's desk"
    assert payload["interactiveClass"] == "avatar avatar-md"
    assert payload["interactiveInitial"] == "L"

    # A dead control must fail at construction, not render and do nothing.
    assert payload["unknownSizeThrows"] is True
    assert payload["unlabelledButtonThrows"] is True
    assert payload["handlerlessButtonThrows"] is True


def test_a_two_word_name_earns_two_initials_and_a_third_word_earns_none() -> None:
    """One letter is enough for a face; a category is read by its mark alone.

    `initial` answers for a person, who has their name printed beside them.
    A thing identified by its mark across a grid — the catalog's categories —
    needs the letters that tell `Data Analyst` from `Design`. The cap is the
    other half of the rule: a third word gets no third letter, because the
    circle is sized for a face and controls.css sets type for a pair and for
    nothing wider.

    A hyphen is INSIDE a word here. This module also draws people, and
    `Jean-Luc Picard` is JP.
    """
    payload = _avatar_payload()
    got = payload["initials"]
    assert got["oneWord"] == "E"
    assert got["twoWords"] == "DA"
    assert got["threeWords"] == "SD", "a third word must not earn a third letter"
    assert got["hyphenIsOneWord"] == "JP"
    assert got["padded"] == "PD", "runs of whitespace are one boundary, not several"
    # An unnamed thing gets the same `?` a nameless agent gets — never an empty
    # circle, which identifies nothing at all.
    assert got["nameless"] == "?"
    assert got["absent"] == "?"
    # The max is a parameter, and asking for no letters is a caller bug.
    assert got["explicitMaxOne"] == "D"
    assert payload["maxBelowOneThrows"] is True

    # The cap is written once and read by both halves of the rule.
    avatar = _read(JS / "core/avatar.js")
    assert "const MAX_INITIALS = 2;" in avatar
    assert "MAX_INITIALS," in avatar.rsplit("return {", 1)[-1]


def test_two_initials_get_the_smaller_type_size_in_the_same_circle() -> None:
    """`DA` at the one-letter size overruns the circle it is centred in.

    A capital on this stack covers about 0.72em, so a pair spans ~1.45em where
    a single letter spans ~0.72em, and the widest flat string a circle of
    diameter D holds is the side of its inscribed square, 0.707D. At `chip`
    that ceiling is 9.9px and an unreduced pair measures 11.6px.

    The correction is a MODIFIER in the stylesheet that owns `.avatar`, not an
    inline style: geometry is CSS's, and an inline font-size would have to be
    computed per size in JS. `create` adds the class from the glyph it was
    handed, so no caller has to remember to ask for it.
    """
    payload = _avatar_payload()
    assert payload["duoText"] == "DA"
    assert payload["duoClass"] == "avatar avatar-lg avatar-duo"
    # A single letter must NOT be shrunk: every existing caller renders one.
    assert payload["soloClass"] == "avatar avatar-lg"
    # A glyph the circle cannot hold, and one that is not there at all, both
    # render a mark nobody can read. Loud at construction, never on screen.
    assert payload["overlongTextThrows"] is True
    assert payload["blankTextThrows"] is True

    css = _read(CSS / "controls.css")
    # One rule per size, because each size declares its own font-size and a
    # single-class rule could carry only one value.
    for size, px in (("chip", 6), ("sm", 8), ("md", 9), ("lg", 12)):
        rule = f".avatar-{size}.avatar-duo"
        assert f"{rule} " in css or f"{rule}{{" in css, rule
        block = css.split(rule, 1)[1].split("}", 1)[0]
        assert f"font-size: {px}px" in block, block
        # font-size ONLY: .avatar's em-based optical lift has to keep scaling.
        assert "padding" not in block and "width" not in block, block


def test_a_derived_seed_is_stable_spread_and_measured() -> None:
    """An open-ended set has no stored colour, and must not get a lookup table.

    The catalog's categories are whatever `packs/<category>/` a contributor
    adds, so a colour per known category is a table that goes stale the first
    time someone adds one. `seedFor` derives the seed from the KEY instead and
    hands it to the same `tintFor` every stored colour goes through — one
    contrast routine in the module, not two.

    The family is swept the way the palette is: by measuring every colour it
    can produce with this file's own copy of the WCAG formula. All 360 hues
    already clear AA on their own tint, so the darkening loop never runs and
    the hue derived is the hue painted.
    """
    payload = _avatar_payload()
    assert payload["seedFamilySize"] == 360, "the whole hue circle, not a slice"
    assert payload["seedsMeetAA"] is True, (
        f"worst derived pair {payload['seedWorstHex']} at {payload['seedWorstRatio']}:1"
    )
    assert payload["seedWorstRatio"] >= 4.5
    assert payload["seedsNeverDarken"] is True, (
        "a darkened seed is a hue bent toward its neighbours"
    )
    # A derived colour is one this app would equally accept as an agent's, so
    # there is no second class of colour here.
    assert payload["seedsAreOfficeLegible"] is True

    # Deterministic, and the same category however it was typed.
    assert payload["seedIsStable"] is True
    assert payload["seedIgnoresCaseAndSpace"] is True
    assert payload["twoKeysTwoSeeds"] is True
    # No key is a real state and answers with the pair the module already keeps
    # for an agent with no colour — never an empty circle.
    assert payload["seedForNothing"] is True
    assert payload["keylessPair"] == {"bg": "var(--line)", "ink": "var(--muted)"}
    assert payload["neutralRatio"] >= 4.5, payload["neutralRatio"]
    # ...and the harness measured the tokens those two names actually resolve to.
    tokens = _read(CSS / "tokens.css")
    assert "--line: #e6e8ec;" in tokens
    assert "--muted: #565e6b;" in tokens

    # The derivation is traceable from the module, not only from this test.
    avatar = _read(JS / "core/avatar.js")
    assert "const SEED_SATURATION = 0.70;" in avatar
    assert "const SEED_LIGHTNESS = 0.24;" in avatar
    # It goes through tintFor rather than around it.
    seed = avatar.split("function seedFor(key) {", 1)[1].split("\n    }", 1)[0]
    assert "contrast(" not in seed and "AA_RATIO" not in seed


# Every surface that draws a person. Each one used to derive its own initial
# and paint its own circle; five of them disagreed about the geometry.
AVATAR_CALL_SITES = (
    # The rail's People half; split out of shell/roster.js when select mode
    # pushed the assembler past the 300-line cap.
    "shell/roster-people.js",
    "context/mini-office.js",
    "context/desk-panel.js",
    "places/office/org-view.js",
    "places/board/task-card.js",
)


def test_one_avatar_implementation() -> None:
    """One place derives an initial, and five surfaces ask it for a node.

    The subject is the DERIVATION, not the string `avatar`: five modules each
    took the first character of a name and upper-cased it, one of them
    (`shell/roster.js`) forgot to render the result at all, and no two agreed
    on the circle's size. A file offends if it takes a first character and
    upper-cases it — spelled `charAt(0)` or `[0]`, both of which were in the
    tree.
    """
    offenders = []
    for path in _app_js():
        text = _read(path)
        derives = "charAt(0)" in text or any(
            "[0]" in line and "toUpperCase()" in line for line in text.splitlines()
        )
        if derives:
            offenders.append(path.relative_to(JS).as_posix())
    assert offenders == ["core/avatar.js"], offenders

    # The positive half: each surface actually asks for the shared node.
    for caller in AVATAR_CALL_SITES:
        assert "BossModAvatar.create(" in _read(JS / caller), caller

    # task-card.js's private helper is gone, not merely unused.
    assert "function initial(" not in _read(JS / "places/board/task-card.js")


# The three surfaces that expressed one idea three ways: a bare checkbox in a
# label, an aria-pressed button that rewrote its own text, and a second bare
# checkbox. One control, three call sites.
SWITCH_CALL_SITES = (
    "conversation/system-receipts.js",
    "places/board/board-toolbar.js",
    "places/log/log-filters.js",
)

SWITCH_MODULES = [JS / "core" / "dom.js", JS / "core" / "switch.js"]
SWITCH_HARNESS = Path(__file__).resolve().parent / "js_switch_harness.cjs"


def _switch_payload() -> dict:
    args = ["node", str(SWITCH_HARNESS)] + [str(path) for path in SWITCH_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_one_toggle_control() -> None:
    """Board 'Subtasks', Log 'Follow', and the receipts toggle are one control.

    Scoped to the tree outside settings/: the Diagnostics and Telegram toggles
    are Tailwind markup inside the declared markup exemption (test_ui_index.py),
    they are not built with BossModDom.h, and converting them is neither in this
    plan's file list nor free of regression risk. The rule this asserts is that
    no NEW switch is authored by hand.
    """
    definers = sorted(
        p.relative_to(JS).as_posix() for p in _app_js()
        if not p.relative_to(JS).as_posix().startswith("settings/")
        and (
            "'role': 'switch'" in _code(_read(p))
            or 'role="switch"' in _code(_read(p))
            # The two literal spellings above are only the two this tree happens
            # to use today; `setAttribute('role', 'switch')` slips past both, as
            # a mutation confirmed. aria-checked is the attribute a switch
            # cannot work without whatever spelling builds it, so it is the
            # honest thing to key on.
            or "aria-checked" in _code(_read(p))
        )
    )
    # Read through _code(), for the reason _code() exists: core/overlays.js and
    # conversation/chrome.js both explain IN PROSE that the view-options panel
    # is a role="dialog" precisely BECAUSE it carries a role="switch", and a
    # naive substring check read those two explanations as two new switches.
    assert definers == ["core/switch.js"], definers

    for caller in SWITCH_CALL_SITES:
        assert "BossModSwitch.create(" in _read(JS / caller), caller

    # And the checkbox it replaces is gone.
    assert "type: 'checkbox'" not in _read(JS / "conversation/system-receipts.js")
    assert "type: 'checkbox'" not in _read(JS / "places/board/board-toolbar.js")


def test_the_switch_row_is_the_target_not_the_pill() -> None:
    """26x14 is under the 24x24 minimum target (SC 2.5.8), so the pill cannot
    be the target. The whole label+control row is the <button>; the pill is an
    aria-hidden span inside it.

    Asserted on the built node rather than on a comment, because "the row is
    the target" is exactly the kind of claim that survives in prose after the
    markup has stopped being true.
    """
    payload = _switch_payload()

    assert payload["rowTag"] == "BUTTON"
    assert payload["rowRole"] == "switch"
    assert payload["rowClass"] == "switch-row"
    assert payload["pillTag"] == "SPAN"
    assert payload["pillIsDecorative"] is True
    # The label is INSIDE the button, which is both what names it and what
    # makes the row wide enough to be a legal target.
    assert payload["labelIsInsideTheRow"] is True
    assert payload["accessibleName"] == "Show subtasks"

    # State is announced as checked/unchecked, and it is the row that carries it.
    assert payload["startsUnchecked"] is True
    assert payload["clickChecksAndReports"] is True
    assert payload["secondClickUnchecks"] is True
    # set() re-syncs from outside without pretending the operator clicked.
    assert payload["setUpdatesWithoutFiring"] is True
    # A switch nothing listens to, or that nothing names, is a dead control.
    assert payload["unlabelledThrows"] is True
    assert payload["handlerlessThrows"] is True

    css = _read(CSS / "controls.css")
    pill = css.split(".switch {", 1)[1].split("}", 1)[0]
    assert "width: 26px" in pill and "height: 14px" in pill
    row = css.split(".switch-row {", 1)[1].split("}", 1)[0]
    assert "min-height: 24px" in row, "the row is the target; it must clear 24px"


# ─── The seed clamp, and the form that enforces it ───

ROSTER_HARNESS = Path(__file__).resolve().parent / "js_roster_harness.cjs"

# The order tests/js_roster_harness.cjs evaluates its modules in. Kept beside
# tests/test_ui_roster.py's copy on purpose: both files drive the same rail,
# and a shared constant would hide which of them a breakage belongs to.
ROSTER_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "shell" / "roster-row-meta.js",
    JS / "shell" / "roster-people.js",
    JS / "shell" / "thread-create.js",
    JS / "shell" / "thread-view-menu.js",
    JS / "shell" / "roster-threads.js",
    JS / "shell" / "roster.js",
]


def _roster_payload() -> dict:
    args = ["node", str(ROSTER_HARNESS)] + [str(path) for path in ROSTER_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


AGENT_FORM_HARNESS = Path(__file__).resolve().parent / "js_agent_form_harness.cjs"

AGENT_FORM_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "avatar.js",
    JS / "context" / "agent-fields.js",
    JS / "context" / "agent-form-fields.js",
    JS / "context" / "agent-form-advanced.js",
    JS / "context" / "agent-form-connections.js",
    JS / "context" / "agent-submit.js",
]


def _agent_form_payload() -> dict:
    args = ["node", str(AGENT_FORM_HARNESS)] + [str(path) for path in AGENT_FORM_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_palette_seed_is_legible_on_the_lightest_floor_tile() -> None:
    """The ring was a mitigation; the bound on the seed is the guarantee.

    Task 0 shipped the sprite ring and measured it honestly: --panel scores
    5.02:1 on the chair but 1.10-1.24:1 on the pale floor and transit tiles,
    where the BODY colour is what carries the separation. So the seed itself has
    to be dark enough.

    The threshold is derived, not typed. --office-transit #f3f4f6 has relative
    luminance 0.9041, and a body fill clears 3:1 against it (SC 1.4.11) only
    while its own luminance satisfies (0.9041 + 0.05) / (L + 0.05) >= 3 — that
    is, L <= 0.2680. The harness recomputes that from the token with its own
    copy of the WCAG formula and checks the constant lands on it.
    """
    payload = _agent_form_payload()
    assert payload["boundIsDerivedFromTheTransitTile"] is True
    assert payload["boundClearsNonTextContrast"] is True, (
        "a seed sitting exactly on the bound must still measure 3:1"
    )
    assert payload["seedMaxLuminance"] == 0.268033
    assert payload["allSeedsLegible"] is True, payload["illegibleSeeds"]

    # The derivation is traceable from the module, not only from this test.
    avatar = _read(JS / "core/avatar.js")
    assert "--office-transit" in avatar
    assert "SEED_MAX_LUMINANCE," in avatar.rsplit("return {", 1)[-1]


def test_a_too_light_custom_colour_is_refused_not_silently_accepted() -> None:
    """Blocked with a reason. Never auto-corrected behind the operator's back,
    and never accepted into a sprite nobody can see.

    Proven by CALLING the save path: a guard that is written but unreachable is
    the failure mode a source grep cannot tell apart from a working one.
    """
    payload = _agent_form_payload()
    assert payload["rejectsPaleSeed"] is True          # #ffe066, luminance 0.755
    assert payload["acceptsPaletteSeed"] is True
    # A colour that cannot be measured is not a colour that passes.
    assert payload["rejectsUnparseableSeed"] is True
    assert payload["rejectsAbsentSeed"] is True

    assert payload["submitRefusesAPaleColour"] is True
    assert payload["submitSaysWhy"] is True
    assert payload["submitAcceptsAPaletteColour"] is True

    submit = _read(JS / "context/agent-submit.js")
    assert "BossModAvatar.isSeedLegible(" in submit
    assert "too light to see on the office floor" in submit
    # Refused, not repaired: an auto-darkened colour is a value changed on save.
    assert "tintFor" not in submit


def test_the_connections_matrix_actually_renders() -> None:
    """A live arity bug, caught by executing the builder rather than reading it.

    `connectionSelect(t.key, agent?.[t.key])` called a three-parameter function
    with two, so `connections` received the string 'model_social' and
    `connections.map` was not a function. It sits in the else branch of the
    no-connections guard, so it threw for every operator who had configured at
    least one connection — and the suite stayed green because it only ever read
    this file's source.
    """
    payload = _agent_form_payload()
    assert payload["matrixError"] is None, payload["matrixError"]
    assert payload["matrixCoversEveryModelType"] is True, payload["matrixSelectNames"]
    assert payload["matrixRendersConnectionOptions"] is True
    assert payload["matrixPreselectsTheStoredModel"] is True
    assert payload["emptyMatrixLinksToSettings"] is True


# ─── Header: nav placement, size, and the rail collapse ───


def test_header_nav_geometry() -> None:
    """The operator's words: "I like the icons, but their placement and
    location and size is wrong." So the icons stay and the geometry changes.

    The nav sits beside the brand rather than centred in the window, the items
    are small pills, and the active place is quiet — a filled accent pill for
    the place you are already on shouts louder than anything you might go to.
    """
    css = _read(CSS / "shell.css")

    nav = css.split(".place-nav {", 1)[1].split("}", 1)[0]
    assert "margin-inline: auto" not in nav, "the nav is beside the brand, not centred"
    assert "margin-left: 8px" in nav

    # Dropping the auto margin takes the shove that kept the actions right with
    # it, so the actions have to carry it themselves.
    actions = css.split(".header-actions {", 1)[1].split("}", 1)[0]
    assert "margin-left: auto" in actions

    item = css.split(".place-nav-item {", 1)[1].split("}", 1)[0]
    assert "font-size: 13px" in item
    assert "border-radius: 999px" in item, "a pill, not the 8px --r"

    active = css.split('.place-nav-item[aria-current="page"] {', 1)[1].split("}", 1)[0]
    assert "var(--accent-bg)" not in active
    assert "background: var(--bg)" in active
    # --ink on --bg measures 15.45:1; the quiet pill is still the strongest
    # text in the row, which is what makes it read as current.
    assert "color: var(--ink)" in active

    header = css.split(".app-header {", 1)[1].split("}", 1)[0]
    assert "height: var(--bar)" in header


def test_the_four_rules_that_cross_the_window_are_two_lines() -> None:
    """The operator's words: the borders "dont line up cleanly".

    They did not. The header, the conversation chrome and the rail's search row
    all ended near 48px but only two of them said so — the search row added up
    to about 52px out of its own padding and sat four pixels low. The composer
    band and the rail's `Add agent` row were 62px and 46px.

    So the alignment is a declared token rather than four paddings that nearly
    agree, and each surface reads it. --dock is derived from the COMPOSER,
    because the composer is the one of the pair that cannot be told a height:
    it grows with the draft, and the other three can be told to match its rest.
    """
    tokens = _read(CSS / "tokens.css")
    assert "--bar: 48px;" in tokens
    # 52, down from 62: --dock is derived from the composer's rest, and the
    # send target inside it dropped from 38px to the 32px .header-icon-btn
    # already used. The band followed the control rather than the reverse.
    assert "--dock: 52px;" in tokens

    shell = _read(CSS / "shell.css")
    conversation = _read(CSS / "conversation.css")
    # Each of the five reads the token, and none of them keeps a private copy of
    # the number beside it. Scoped to these rule bodies rather than swept over
    # the file: `.place-nav-item` is 48px too and that 48 is a touch target
    # (SC 2.5.8), not the rhythm line — a blanket scan would read them as the
    # same decision and force one of them to move when the other changed.
    for sheet, rule, prop in (
        (shell, ".app-header", "height"),
        (shell, ".roster-search-row", "height"),
        (conversation, ".conversation-chrome", "min-height"),
        (shell, ".roster-hire", "min-height"),
        (conversation, ".composer", "min-height"),
    ):
        body = sheet.split(f"{rule} {{", 1)[1].split("}", 1)[0]
        token = "--bar" if prop == "height" or rule == ".conversation-chrome" else "--dock"
        assert f"{prop}: var({token})" in body, rule
        assert "48px" not in body and "62px" not in body, rule


def test_neither_borderless_field_lost_its_focus_indicator() -> None:
    """The operator called the blue ring jarring, and it was: base.css paints a
    2px accent box around a field whose whole point is that it has none, and a
    browser fires :focus-visible on a text field for a MOUSE click too.

    It is replaced, never removed (SC 2.4.7) — an inset 2px underline, which
    costs no layout and cannot move the row the send button is centred in. Both
    fields make the same swap, spelled the same way: one of them keeping the
    box would read as the other being broken.

    A HAIRLINE, and the quietest one that is still legal — the ask was "look,
    you're typing", not "HEY YOU ARE TYPING".

    Colour is not the lever and neither is opacity. --line-control measures
    3.10:1 on --panel and 3:1 is the floor SC 1.4.11 sets for a focus
    indicator; the same grey at 60% composites to 1.87:1 and at 40% to 1.49:1,
    both well under. WEIGHT is the lever, so it is 1px rather than 2 — half the
    mark, same ratio — with a short fade so it arrives rather than appears.
    (The ≥2px rule people reach for is SC 2.4.13 Focus Appearance, which is
    AAA. AA asks that the indicator be visible and clear 3:1.)

    Both fields, spelled the same way: one of them keeping a heavier mark would
    read as the other being broken.
    """
    for css_file, rule in ((CSS / "conversation.css", ".composer-input"),
                           (CSS / "shell.css", ".roster-search")):
        css = _read(css_file)
        focus = css.split(f"{rule}:focus-visible {{", 1)[1].split("}", 1)[0]
        assert "outline: none" in focus, rule
        # Removed only because something visible takes its place.
        assert "box-shadow: inset 0 -1px 0 var(--line-control)" in focus, rule
        # Never the accent, and never below the floor: no alpha channel here.
        assert "var(--accent)" not in focus, rule
        assert "rgba" not in focus, rule
        # It arrives rather than appearing. base.css flattens this under
        # prefers-reduced-motion, so the rule needs no opt-out of its own.
        rest = css.split(f"\n{rule} {{", 1)[1].split("}", 1)[0]
        assert "transition: box-shadow" in rest, rule


def test_one_header_icon_button() -> None:
    """The bell, the gear and the rail toggle are one 32px control.

    Three copies of the same geometry is how the surfaces drifted apart in the
    first place; the rail toggle is the third, so it is the one that has to
    compose rather than add a fourth rule.
    """
    css = _read(CSS / "shell.css")
    assert ".header-icon-btn {" in css
    assert ".header-bell {" not in css, "the bell composes the icon button"
    assert ".header-gear {" not in css, "the gear composes the icon button"
    # Pause is the fourth, and the one that arrived as a FIFTH geometry — a
    # bordered alert pill carrying a label. It is a glyph in the same 32px box
    # as the three beside it now, and it declares nothing of its own.
    assert ".header-pause {" not in css, "Pause composes the icon button"

    header = _read(JS / "shell/header.js")
    for control in ("header-icon-btn header-bell",
                    "header-icon-btn header-gear",
                    "header-icon-btn header-rail-toggle",
                    "header-icon-btn header-pause"):
        assert control in header, control
    # The state is a SHAPE, not a hue (SC 1.4.1) — and it is a shape three
    # other surfaces already announce in words, which is why the control itself
    # went quiet: the pause banner, the footer's dot, and every roster row.
    assert "paused ? 'play' : 'pause'" in header
    assert "var(--alert" not in css.split(
        ".header-icon-btn {", 1)[1].split(".bell-badge", 1)[0]
    # One string, spent twice: the accessible name and the tooltip that replaces
    # the label a pointer operator lost.
    assert "pause.setAttribute('aria-label', label);" in header
    assert "pause.setAttribute('data-tooltip', label);" in header


def test_rail_collapse_is_keyboard_reachable() -> None:
    """A <button>, so Enter and Space work with no key handling (SC 2.1.1).

    It also reports its own state: a toggle that paints a collapsed rail but
    keeps announcing "expanded" is worse than one with no state at all.
    """
    header = _read(JS / "shell/header.js")
    assert "'aria-label': 'Toggle sidebar'" in header
    assert "railCollapsed" in header
    built_with = header.split("'aria-label': 'Toggle sidebar'", 1)[0].rsplit("h(", 1)[-1]
    assert built_with.startswith("'button', {"), built_with[:60]
    assert "'aria-expanded'" in header
    # And it stays in step with the store rather than only with its own clicks:
    # a session restored with the rail collapsed must not announce "expanded".
    assert "(s) => s.railCollapsed," in header

    shell = _read(JS / "shell/shell.js")
    assert "function applyRailCollapsed(" in shell
    assert "(s) => s.railCollapsed," in shell
    assert "railCollapsed: false," in shell

    css = _read(CSS / "shell.css")
    # The collapse moves the GRID TRACK, not the width of an element sitting
    # inside a track that is still 220px wide.
    assert '#main-layout[data-rail="collapsed"] {' in css
    collapsed = css.split('#main-layout[data-rail="collapsed"] {', 1)[1]
    assert "--rail: 56px;" in collapsed.split("}", 1)[0]
    # What a 56px rail cannot show is hidden rather than clipped.
    for hidden in (".roster-search-row", ".roster-section-title", ".roster-person",
                   ".roster-hire span"):
        assert f'[data-rail="collapsed"] {hidden}' in css, hidden


def test_the_collapsed_rail_is_inert_below_the_rail_breakpoint() -> None:
    """Under 768px the rail has already left the grid.

    A --rail override there would force a 56px column back onto a layout that
    is deliberately one column wide, so the narrow block must not read --rail
    at all and must not carry a rule of its own for the collapsed state.
    """
    css = _read(CSS / "shell.css")
    narrow = css.split("@media (max-width: 767px) {", 1)[1]
    assert "grid-template-columns: minmax(0, 1fr);" in narrow
    assert "var(--rail)" not in narrow, "the narrow grid must not read --rail"
    assert "data-rail" not in narrow


# ─── Roster rail: select mode, the hire row, the threads block ───


def test_people_rows_are_clean_until_select_mode() -> None:
    """Checkboxes are revealed on demand, not carried permanently.

    The checkbox is genuinely absent from the DOM when the mode is off rather
    than hidden with CSS: a hidden checkbox is still a tab stop and still holds
    a stale `checked`, which is precisely how a selection nobody can see builds
    the wrong thread. Entering and leaving are both plain <button> clicks, so
    both are keyboard-reachable (SC 2.1.1), and leaving clears the selection.
    """
    roster = _read(JS / "shell/roster-people.js")
    assert "if (selectMode)" in roster or "selectMode ?" in roster
    assert "BossModAvatar.create(" in roster
    assert "function enterSelectMode()" in roster
    assert "function exitSelectMode()" in roster
    exit_body = roster.split("function exitSelectMode() {", 1)[1].split("\n        }", 1)[0]
    assert "selectMode = false" in exit_body
    assert "selected.clear()" in exit_body, exit_body
    # Nothing hides the CONTROL instead of removing it. Matched as a whole
    # class name rather than as a substring: the polish round added
    # `.roster-select-actions` for select mode's own hint-and-buttons block —
    # round three folded that block into the section header and the class is
    # gone, but the whole-name match is what kept the ban honest while it
    # existed, so it stays that way.
    assert re.search(r"\.roster-select(?![-\w])", _read(CSS / "shell.css")) is None


def test_select_mode_is_proven_on_the_built_rail() -> None:
    """The source half above can only say the branch exists; this says it works.

    Driven through the real rail: no boxes until New thread, boxes after it,
    Cancel takes both the boxes and the selection away, and creating leaves the
    mode rather than stranding the operator in it.
    """
    payload = _roster_payload()
    assert payload["rowsAreCleanUntilSelectMode"] is True
    assert payload["cancelClearsTheSelection"] is True
    assert payload["selectionClearsAfterCreate"] is True


def test_one_button_vocabulary_and_it_projects() -> None:
    """The operator's words: the icon buttons "feel flat and noisy", and the ask
    was a reusable style with "a bit more projection".

    Flat was the whole of it — one fill inside one crisp border reads as a drawn
    rectangle, not as something that will move when pressed. Three declarations
    answer it, and they live on `.btn` so every call site gets the same one
    rather than two surfaces agreeing by hand.

    The PRESS is the half that makes it a control rather than a bevel someone
    drew: :active collapses the ramp and drops the lift, so the button goes down
    under the pointer. Without it the raise is a picture of a button.
    """
    controls = _read(CSS / "controls.css")
    tokens = _read(CSS / "tokens.css")
    for token in ("--btn-face:", "--btn-face-hover:", "--btn-shadow:"):
        assert token in tokens, token

    btn = controls.split(".btn {", 1)[1].split("}", 1)[0]
    assert "linear-gradient(180deg, var(--panel), var(--btn-face))" in btn
    assert "box-shadow: var(--btn-shadow)" in btn
    # The border still carries the identification job (SC 1.4.11) — no fill this
    # light could: --btn-face measures 1.06:1 against --panel.
    assert "border: 1px solid var(--line-control)" in btn

    press = controls.split(".btn:active:not([disabled]) {", 1)[1].split("}", 1)[0]
    assert "box-shadow: none" in press
    assert "background: var(--btn-face-hover)" in press

    # The primary ramp only ever improves the white label's contrast: --panel is
    # 4.83:1 on --accent and 6.36:1 on --accent-deep, so the worst row of the
    # gradient is the flat colour it replaced.
    assert "--accent-deep: #2559c4;" in tokens
    primary = controls.split(".btn-primary {", 1)[1].split("}", 1)[0]
    assert "linear-gradient(180deg, var(--accent), var(--accent-deep))" in primary

    # The two variants that are meant to be flat opt out of all three rather
    # than inheriting a raise they then have to fight.
    for flat in (".btn-quiet {", ".btn-link {"):
        body = controls.split(flat, 1)[1].split("}", 1)[0]
        assert "box-shadow: none" in body, flat
        assert "background: none" in body, flat

    # ICON-ONLY is one rule on the primitive, not one per surface. It moved here
    # the moment a second surface — the desk's back arrow — wanted it.
    assert ".btn[data-tooltip] { padding-inline: 6px; }" in controls
    assert "[data-tooltip] { padding-inline" not in _read(CSS / "conversation.css")


def test_the_way_out_of_a_desk_is_a_control_not_a_banner() -> None:
    """`← The office` was a full-width .btn with the arrow typed into its label.

    .desk-body is a flex column and stretches its children, so it spanned all
    280px and read as a banner across the top of the panel. It is the same
    icon-only button every other glyph control in the window is now, and the
    only thing the surface adds is that it stops stretching.
    """
    js = _read(JS / "context/desk-panel.js")
    assert "const BACK_LABEL = 'Back to the office';" in js
    assert "'aria-label': BACK_LABEL" in js
    assert "'data-tooltip': BACK_LABEL" in js
    assert "'data-lucide': 'chevron-left'" in js
    assert "class: 'btn btn-sm desk-back'" in js
    assert "The office" not in _code(js)
    assert "context-link" not in _code(js)
    # Nothing else in this column sweeps for placeholders, so the view paints
    # its own subtree — the same rule context/mini-office.js follows.
    assert "BossModIcons.paint(element, 'desk-panel')" in js

    css = _read(CSS / "context.css")
    back = css.split(".desk-back {", 1)[1].split("}", 1)[0]
    assert "align-self: flex-start" in back
    assert "color: var(--muted)" in back
    # The class its one caller left behind went with it.
    assert ".context-link" not in css


def test_hire_row_matches_the_person_rows() -> None:
    """An empty seat, aligned with the seats above it — not a stray glyph.

    And ONE colour across the row: the label is --hint, the ink its own dashed
    seat already carries. It was --accent, which made a secondary action the
    loudest thing in the rail and put a blue word inside a grey circle. --hint
    measures 4.83:1 on --panel, so it clears AA as text.

    The rule above it is gone too. A border made the row a compartment bolted
    to the foot of the rail; without one it floats at the bottom of the same
    column the names are in, which is what it is.
    """
    roster = _read(JS / "shell/roster.js")
    assert "user-plus" not in roster
    assert "avatar-empty" in roster
    css = _read(CSS / "shell.css")
    hire = css.split(".roster-hire {", 1)[1].split("}", 1)[0]
    assert "color: var(--hint)" in hire
    assert "var(--accent)" not in hire
    assert "border-top" not in hire
    # ...and the hover is the quiet pair every other frameless control uses,
    # rather than the blue-on-blue that needed its own contrast correction.
    hover = css.split(".roster-hire:hover {", 1)[1].split("}", 1)[0]
    assert "background: var(--bg)" in hover and "color: var(--ink)" in hover


def test_the_need_dot_sits_beside_the_name_not_inside_it() -> None:
    """It was rendering inside the text column, which is why it moved the
    status line. A sibling pinned right is what the concept has.

    Round three gave that right edge a second occupant — the last-activity
    time — and moved both into one shared column, shell/roster-row-meta.js, so
    these assertions follow the dot to its new owner. What they guard is
    unchanged: the dot is not inside the name button, and it is pinned right.
    Read on the new owner rather than left where it was, which would have
    passed on a file that no longer mentions the dot at all.
    """
    roster = _read(JS / "shell/roster-people.js")
    person = roster.split("class: 'roster-person',", 1)[1].split("BossModRosterRowMeta", 1)[0]
    assert "roster-need-dot" not in person, "the dot is a sibling of the button"
    meta = _read(JS / "shell/roster-row-meta.js")
    assert "roster-need-dot" in meta, "the shared column owns the dot"
    assert "'aria-hidden': 'true'" in meta, "the status line already says it"
    css = _read(CSS / "shell.css")
    column = css.split(".roster-row-meta {", 1)[1].split("}", 1)[0]
    assert "margin-left: auto" in column
    # Two rules both pushing right would be one of them doing nothing.
    dot = css.split(".roster-need-dot {", 1)[1].split("}", 1)[0]
    assert "margin-left: auto" not in dot


def test_the_threads_block_is_two_states_not_a_permanent_button() -> None:
    """`New thread` opens the mode; a confirm and a Cancel close it.

    Re-pointed at shell/thread-create.js, which the polish round split out of
    the Threads half: reading the thread list and making a new one are two
    jobs, and the second is the one this describes.

    Re-pointed again in round three, which moved the second state onto the
    section header row the first one already lived on. The two states are what
    this has always guarded; what changed is that both now wear the header's
    icon-only vocabulary instead of the mode owning a row of text buttons.
    """
    threads = _read(JS / "shell/thread-create.js")
    assert "'New thread'" in threads
    assert "'Create thread'" in threads
    assert "'Cancel'" in threads
    # All three are the header row's quiet icon control, not a filled button
    # that would outrank every person in the rail.
    assert threads.count("class: 'roster-section-action'") == 2
    assert "class: 'roster-section-action roster-confirm-thread'" in threads
    assert "btn-primary" not in threads
    assert "btn btn-quiet" not in threads

    # WHICH LIST is a third owner of the same header row — the `⋯`, its panel,
    # and the segment inside it — not a pair of pills below the header. The
    # pills cost a permanent row of a 220px rail to answer a question that is
    # `Active` almost every visit; behind the `⋯` they cost no height at all.
    shell_css = _read(CSS / "shell.css")
    assert ".roster-thread-filter" not in shell_css
    assert ".roster-thread-filters" not in shell_css

    roster_threads = _read(JS / "shell/roster-threads.js")
    view = _read(JS / "shell/thread-view-menu.js")
    assert "class: 'roster-section-action roster-thread-view'" in view
    # The `⋯` sits OUTSIDE the group thread-create.js empties on every mode
    # swap: a control that survives the swap cannot live in the cleared node.
    head = roster_threads.split("class: 'roster-section-head'", 1)[1].split(");", 1)[0]
    assert head.index("create.actions") < head.index("view.button"), head
    # The seam: the menu owns the control, the list owns the answer. A second
    # copy of "which list" is how the pills and the list drift apart.
    assert "getStatus: () => threadFilter" in roster_threads
    assert "onSelect: setThreadFilter" in roster_threads
    assert "threadFilter" not in view
    # `getContainer` is a THUNK because the row the panel hangs off cannot be
    # built until the button that goes in it exists.
    assert "getContainer: () => head" in roster_threads
    # ...so the row's right inset belongs to the ROW rather than to whichever
    # group happens to be last in it.
    assert "margin-right: 6px" not in shell_css.split(
        ".roster-section-actions {", 1)[1].split("}", 1)[0]
    assert "padding-right: 6px" in shell_css.split(
        ".roster-section-head {", 1)[1].split("}", 1)[0]

    # ONE GLYPH FOR A MENU. This control was a gear for a day and it was the
    # third mark for the same idea in one window — `⋯` on the conversation
    # header, sliders here, the application gear in the app header. The rule:
    # `⋯` opens a menu of things you can do to the thing beside it, and a gear
    # means application settings and appears exactly once.
    assert "'data-lucide': 'ellipsis'" in view
    assert "settings" not in _code(view)
    gears = [
        path.relative_to(JS).as_posix()
        for path in _app_js()
        if "'data-lucide': 'settings'" in _read(path)
    ]
    assert gears == ["shell/header.js"], gears

    # It reuses the shared panel rather than growing a popover of its own.
    assert "BossModOverlays.createMenu({" in view

    # BOTH lists on screen at once, and the one you are looking at is filled:
    # state is a shape, not a sentence. Two earlier spellings were wrong in
    # opposite directions — a `Show archived threads` switch implied the two
    # lists were additive when they are exclusive, and a row that renamed
    # itself `View archives` / `View active threads` was so quiet you had to
    # read it to find out where you were.
    assert "status: 'active', label: 'Active'" in view
    assert "status: 'archived', label: 'Archived'" in view
    assert "const SEGMENT_LABEL = 'Thread view';" in view
    assert "class: 'menu-segment-option'" in view
    assert "aria-pressed" in view
    assert "Show archived" not in _code(view)
    assert "View archives" not in _code(view)
    # The caption is a real heading the group points at, rather than an
    # aria-label repeating on screen text into the accessibility tree.
    assert "'aria-labelledby': SEGMENT_ID" in view
    assert "class: 'menu-label', id: SEGMENT_ID" in view
    overlays = _read(CSS / "overlays.css")
    pressed = overlays.split('.menu-segment-option[aria-pressed="true"] {', 1)[1].split("}", 1)[0]
    assert "var(--accent)" not in pressed
    assert "background: var(--bg)" in pressed and "color: var(--ink)" in pressed

    # A menu panel is DETACHED while it is closed, so the document sweep that
    # paints the rail can never reach a glyph inside it. Both menus paint what
    # they just attached — the bug that rendered Archive as a bare heading.
    assert "BossModIcons.paint(menu.element, 'thread-view-menu')" in view
    chrome = _read(CONVERSATION / "chrome.js")
    assert "BossModIcons.paint(menu.element, 'conversation-chrome.menu')" in chrome


# ─── Conversation: identity, glyphs, bubbles, and the empty state ───

CONVERSATION = JS / "conversation"


def test_the_chrome_descriptor_grew_rather_than_the_view_reaching_out() -> None:
    """`avatar` and `icon` are DATA the source already holds.

    The alternative — the chrome looking the agent up in the roster — is the
    one boundary the whole conversation surface is built around, and it is why
    it never subscribes to `world_update`. tests/test_ui_conversation.py's
    `test_sources_never_touch_the_dom` guards the other side of the same seam.
    """
    chrome = _read(CONVERSATION / "chrome.js")
    assert "BossModAvatar.create(" in chrome
    assert "action.icon" in chrome
    # The view reaches for neither the roster nor the socket. Read against the
    # CODE, not the prose: the docstring names both, because saying why the
    # boundary exists is the point of it.
    for forbidden in ("store.", ".roster", "bus.subscribe", "world_update"):
        assert forbidden not in _code(chrome), f"chrome.js must not reach for {forbidden}"

    agent = _read(CONVERSATION / "sources/agent-source.js")
    assert "avatar: { name:" in agent
    assert "icon: 'lamp-desk'" in agent
    # The adapter names an icon; it never builds one.
    for forbidden in ("document.", "innerHTML", "BossModDom"):
        assert forbidden not in agent

    # A thread has no one face and says so by omission, not by a placeholder
    # the adapter invents.
    thread = _read(CONVERSATION / "sources/thread-source.js")
    assert "avatar:" not in _code(thread)
    assert "avatar-group" in _read(CSS / "controls.css")


def test_the_receipts_band_is_gone_and_the_preference_is_not() -> None:
    """Only its position and its control type changed.

    The full-width strip spent a whole row of the conversation on a display
    preference. The preference, its storage key, and its blocked-storage
    fallback are all untouched — asserted here so "moved" cannot quietly
    become "dropped".
    """
    receipts = _read(CONVERSATION / "system-receipts.js")
    assert "bossmod.chat.showSystemReceipts" in receipts
    assert "'Show system notifications'" in receipts
    assert "BossModSwitch.create(" in receipts
    assert "site data is unreadable" in receipts
    # The band is gone from the markup AND from the stylesheet, so it cannot
    # come back as dead styling.
    assert "conversation-controls" not in receipts
    assert "conversation-controls" not in _read(CSS / "conversation.css")
    # It is mounted into the chrome as a SLOT rather than as a field of a
    # descriptor that is rebuilt on every conversation switch. The slot moved
    # from the action row to the `⋯` menu in the polish round — the property
    # this guards is the slot, not which row it lands in, and the behaviour
    # behind it is proven on built nodes by the conversation harness.
    controller = _read(CONVERSATION / "conversation.js")
    assert "viewOptions: [systemReceipts.element]," in controller
    assert "deps.viewOptions" in _read(CONVERSATION / "chrome.js")
    # ...and it is still not a descriptor field, which would rebuild it.
    assert "avatar?, actions}" in _read(CONVERSATION / "chrome.js")


def test_bubbles_are_tinted_and_timestamps_recede() -> None:
    """The concept's geometry, on this codebase's measured palette."""
    css = _read(CSS / "conversation.css")

    listing = css.split(".transcript-list {", 1)[1].split("}", 1)[0]
    assert "max-width: 760px" in listing

    bubble = css.split(".msg {", 1)[1].split("}", 1)[0]
    assert "max-width: 72%" in bubble
    assert "border-radius: 14px" in bubble

    human = css.split(".msg-human {", 1)[1].split("}", 1)[0]
    assert "align-self: flex-end" in human
    assert "background: var(--accent-bg)" in human
    assert "color: var(--blue-ink)" in human

    agent = css.split(".msg-agent {", 1)[1].split("}", 1)[0]
    assert "align-self: flex-start" in agent
    assert "background: var(--bg)" in agent

    # Kept — they are ours, not the concept's — but demoted.
    time = css.split(".msg-time {", 1)[1].split("}", 1)[0]
    assert "font-size: 10px" in time
    assert "color: var(--hint)" in time
    # ...except on the tint, where --hint measures 4.19:1 and fails AA.
    assert ".msg-human .msg-time { color: var(--blue-ink); }" in css


def test_the_composer_is_the_field_and_send_and_nothing_else() -> None:
    """The clipboard on the left is gone; what is left is quieter than before.

    It was a third front door to the one assign form — the Board's `+ New task`
    and the empty conversation's `Assign a task` are the other two — and the
    only one parked in front of the operator for every second they were typing.

    Send went quiet with it. It is --muted at rest and --ink under the pointer
    — the pair every other frameless glyph in the app wears — because it was
    the last accent mark in a band this round was spent quieting, and it is not
    the primary send path: Enter is, and the placeholder beside it says so.
    """
    composer = _read(CONVERSATION / "composer.js")
    row = composer.split("class: 'composer-row' }", 1)[1].split(")", 1)[0]
    assert row.strip().startswith(", input, sendBtn"), row

    css = _read(CSS / "conversation.css")
    send = css.split(".composer-send {", 1)[1].split("}", 1)[0]
    assert "color: var(--muted)" in send
    assert "var(--accent)" not in send
    assert "background: none" in send
    hover = css.split(".composer-send:hover:not(:disabled) {", 1)[1].split("}", 1)[0]
    assert "color: var(--ink)" in hover
    # 32px, the same target .header-icon-btn carries, so the window has one
    # icon-target size. Still well over the 24px floor (SC 2.5.8).
    assert "width: 32px" in send and "height: 32px" in send
    # And there is no second control in the row to share a rule with.
    assert ".composer-assign" not in css


def test_both_typing_surfaces_are_the_page_not_a_box_on_it() -> None:
    """The operator's ask: no grey fill, no border, a light placeholder that
    reads as floating on the surface it is typed onto.

    The two fields are asserted TOGETHER because they are one decision — a
    borderless composer beside a boxed rail search would read as one of them
    being broken. Each keeps the two things a field cannot give up: a
    placeholder that clears AA as text, which is what identifies a control with
    no boundary (SC 1.4.11), and base.css's focus ring, which is NOT replaced
    here and is the only thing a keyboard operator has to find it by (SC 2.4.7).
    """
    for css_file, rule in ((CSS / "conversation.css", ".composer-input"),
                           (CSS / "shell.css", ".roster-search")):
        css = _read(css_file)
        body = css.split(f"{rule} {{", 1)[1].split("}", 1)[0]
        assert "border: 0" in body, rule
        assert "background: none" in body, rule
        assert "var(--line-control)" not in body, rule
        assert "var(--bg)" not in body, rule
        assert f"{rule}::placeholder {{ color: var(--hint); }}" in css, rule
        # Neither replaces the focus indicator it inherits.
        assert "outline" not in body, rule


def test_an_empty_conversation_is_not_a_dead_end() -> None:
    """Two lines of text and a composer to think of something to type into.

    The state lives with the transcript status it replaces, not in the Chat
    place: the place does not know whether the open conversation has messages,
    and teaching it would cross the boundary the controller exists to hold.
    """
    empty = _read(CONVERSATION / "empty-state.js")
    assert "'Say hello'" in empty
    assert "'Assign a task'" in empty
    assert "size: 'lg'" in empty
    # It builds no send path and names no form of its own.
    assert "api(" not in _code(empty)
    assert "AssignForm" not in empty

    controller = _read(CONVERSATION / "conversation.js")
    assert "onGreet: (text) => composer.sendText(text)," in controller
    assert "onAssign: openAssign," in controller
    # One send path: the greeting goes through the composer's gate.
    assert "async function sendText(text)" in _read(CONVERSATION / "composer.js")

    # The no-conversation-selected state keeps its own copy.
    place = _read(JS / "places/chat/chat-place.js")
    assert "No conversation open" in place


# ─── Context column: the office as a map ───


def test_mini_office_is_a_map() -> None:
    """The concept hardcodes three rooms; ours are whatever `agent.location`
    says — any names, any count, plus the Unknown bucket.

    So the rule has to be positional rather than by name: the sort is already
    stable (alphabetical, Unknown last), the FIRST room spans both columns with
    the panel background, and every room after it takes the next tint from a
    fixed ramp. Tint follows sorted position, so a room does not change colour
    when an agent walks between rooms.
    """
    css = _read(CSS / "context.css")
    grid = css.split(".mini-office-rooms {", 1)[1].split("}", 1)[0]
    assert "display: grid" in grid
    assert "grid-template-columns: 1fr 1fr" in grid
    assert ".mini-office-room:first-child {" in css
    first = css.split(".mini-office-room:first-child {", 1)[1].split("}", 1)[0]
    assert "grid-column: 1 / -1" in first
    assert "background: var(--panel)" in first

    # blue/amber/teal/pink, not the plan's blue/ok/amber/teal: --ok on --ok-bg
    # measures 4.33:1 and there is no --ok-ink token, while all four of these
    # have measured ink pairs in tokens.css (6.20-6.41:1).
    for tone in ("blue", "amber", "teal", "pink"):
        assert f'.mini-office-room[data-tone="{tone}"]' in css, tone

    js = _read(JS / "context/mini-office.js")
    # Offset by one against the plan's `TONES[index % TONES.length]`: index 0 is
    # the wide panel room, so that formula would start the ramp at its SECOND
    # tone and never reach the first until a fifth room existed.
    assert "TONES[(index - 1) % TONES.length]" in js
    assert "'data-tone'" in js


def test_mini_office_keeps_the_one_door_that_is_only_its_own() -> None:
    """`Open` became a map; `Open metrics` went entirely.

    Metrics has been a place on the header nav, on every screen, since the nav
    was built — so the link was a second front door to the same trip, one row
    lower and worded differently. The Office link stays because the panel IS a
    summary of that place and is the only thing on screen that says so, but it
    stops spending a word on the verb: the control is a map, named for a screen
    reader and tooltipped for a pointer out of ONE string.
    """
    js = _read(JS / "context/mini-office.js")
    assert "navigate('office')" in js
    # Read against the CODE: the module explains in prose which link it dropped
    # and why, and a naive substring check reads that explanation as the link.
    code = _code(js)
    assert "navigate('metrics')" not in code
    assert "Open metrics" not in code
    assert "const OPEN_OFFICE_LABEL = 'Open the office';" in js
    assert "'aria-label': OPEN_OFFICE_LABEL" in js
    assert "'data-tooltip': OPEN_OFFICE_LABEL" in js
    assert "'data-lucide': 'map'" in js
    # A glyph nothing paints is an empty placeholder on screen. Nothing else
    # mounted in this column paints, so this view paints its own subtree.
    assert "BossModIcons.paint(element, 'mini-office')" in js
    # Quiet and icon-sized, not the accent link it was: the panel under it is
    # already a picture of the floor.
    context_css = _read(CSS / "context.css")
    head_link = context_css.split(".context-head-link {", 1)[1].split("}", 1)[0]
    assert "color: var(--muted)" in head_link
    assert "var(--accent)" not in head_link
    assert ".context-head-link svg { width: 14px; height: 14px; }" in context_css
    # The `N on the floor · N need you` line went with it. It was the THIRD
    # statement of a fact already on screen twice — the bell's badge counts what
    # needs the operator, and the seats in this panel each carry a ping — and
    # counting the roster back to someone looking at a picture of it is not
    # news. The real counts it reported are still rendered, as those pings.
    code = _code(js)
    assert "on the floor" not in code
    assert "mini-office-stat" not in code
    assert "mini-office-stat" not in context_css
    assert "mini-office-ping" in js


def test_mini_office_seats_are_chips_on_a_legal_target() -> None:
    """The seat shrinks to the shared `chip` avatar; the BUTTON does not.

    The concept's seat is 18px, which is under the 24x24 minimum (SC 2.5.8).
    The avatar inside it is 14px paint and the button around it carries the
    floor, which is the same split core/switch.js uses for its pill.
    """
    js = _read(JS / "context/mini-office.js")
    assert "size: 'chip'" in js
    assert "size: 'sm'" not in js
    # The ping and the accessible name both survive the shrink.
    assert "mini-office-ping" in js
    assert "needs you" in js

    seat = _read(CSS / "context.css").split(".mini-office-seat {", 1)[1].split("}", 1)[0]
    assert "width: 24px" in seat and "height: 24px" in seat


def test_a_legacy_colour_survives_the_edit_form() -> None:
    """The palette swap must not silently recolour agents hired before it.

    Task 0 replaced the eight seeds. An agent still holding an old one matches
    no radio, so nothing is checked; agent-submit.js then reads
    `formData.get('agent-color') || FALLBACK_COLOR` and writes the fallback the
    next time anything on the form is saved. The operator never touched the
    colour and is never told it changed — which is the silent-data-loss shape
    this codebase forbids, introduced by our own change.

    Proven on built markup rather than by reading the source: the bug is an
    absent `checked` attribute, and a grep cannot see an absence.
    """
    payload = _agent_form_payload()
    assert payload["legacyColourIsOffered"] is True
    assert payload["legacyColourAddsExactlyOneSwatch"] is True
    # The ninth swatch appears only when it is needed.
    assert payload["paletteColourAddsNoSwatch"] is True


# ─── Office: the header rhythm, the quiet tabs, the ticker ───


def test_office_canvas_is_untouched_by_the_visual_pass() -> None:
    """The guard on the one place where the concept is behind us.

    The prototype fakes a floor with tinted <div> rooms and DOM avatars; we
    render a real 2D canvas. A DOM avatar reaching into a canvas layer would be
    a port of the worse artifact, so the three canvas modules are asserted to
    name none.
    """
    for name in ("office-canvas.js", "canvas-sprites.js", "canvas-motion.js"):
        source = _read(JS / "places/office" / name)
        assert "BossModAvatar" not in source, f"{name} paints on canvas, not DOM"


def test_office_header_and_ticker_match_the_concept() -> None:
    """One 48px bar rhythm, and a ticker that is a strip rather than a panel.

    The concept's `.bar` is `min-height: 48px`, not `height` — the office
    header wraps its tabs onto a second line in a narrow window, and a fixed
    height would clip them.
    """
    css = _read(CSS / "places.css")
    header = css.split(".office-header {", 1)[1].split("}", 1)[0]
    assert "min-height: 48px" in header

    # The strip carries the type size, so an entry does not have to restate it
    # and the empty state cannot disagree with the rows it replaces.
    ticker = css.split(".ticker {", 1)[1].split("}", 1)[0]
    assert "border-top: 1px solid var(--line)" in ticker
    assert "font-size: 12px" in ticker

    # Quiet, exactly as the place nav is quiet: the tab you are on is the
    # strongest TEXT in the group, not the loudest fill.
    tab = css.split('.office-tab[aria-selected="true"] {', 1)[1].split("}", 1)[0]
    assert "var(--accent-bg)" not in tab
    assert "background: var(--bg)" in tab
    assert "color: var(--ink)" in tab


def test_the_office_state_pill_is_readable_on_its_own_tint() -> None:
    """--ok on --ok-bg measures 4.33:1, which is under the 4.5 floor.

    The pill is 11px uppercase — small text, so AA is 4.5:1 and there is no
    large-text exemption to fall back on. --ok-ink is the darkened pair
    tokens.css now carries for exactly this: ink ON a tint, as distinct from
    --ok (a mark on the page background) and --ok-mark (a dot).
    """
    tokens = _read(CSS / "tokens.css")
    assert "--ok-ink:" in tokens
    pill = _read(CSS / "places.css").split(".office-state {", 1)[1].split("}", 1)[0]
    assert "background: var(--ok-bg)" in pill
    assert "color: var(--ok-ink)" in pill


# ─── Board: cards that recede, and one that asks for you ───


def test_board_cards_match_the_concept() -> None:
    """A card that needs the operator is bordered, not filled.

    Filling it would fight the column header, which already says so in words —
    and a filled card in the Needs column would be the loudest thing on a board
    whose whole job is to make one column stand out.
    """
    css = _read(CSS / "places.css")
    card = css.split(".task-card {", 1)[1].split("}", 1)[0]
    assert "background: var(--panel)" in card
    assert "border-radius: var(--r)" in card
    assert '.task-card[data-needs="true"] { border-color: var(--alert-line); }' in css
    # All three border rules weigh the same, so source order is what decides
    # which one an operator sees on a needy card they are hovering or have
    # selected. Those two answer "what am I doing", so they come last and win.
    assert css.index('.task-card[data-needs="true"]') < css.index(".task-card:hover")
    assert css.index(".task-card:hover") < css.index(".task-card.is-selected")

    # Done recedes rather than disappearing: the text dims, the card stays.
    assert '.board-column[data-column="done"] .task-card { color: var(--muted); }' in css

    head = css.split(".board-column-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 12px" in head
    assert "color: var(--hint)" in head
    # The header that "already says so".
    assert (
        '.board-column[data-column="needs"] .board-column-title { color: var(--alert); }'
        in css
    )


def test_the_needy_card_reads_the_one_column_map() -> None:
    """`data-needs` is derived, never a second copy of which statuses need you.

    board-columns.js is the only file that knows `blocked` and `stalled` are
    the Needs column, and test_ui_board.py proves that map total against the
    engine's TaskStatus. Spelling the two statuses again in the card — or in a
    CSS selector on data-status — would be the second copy that map exists to
    prevent, and it would silently stop matching the day the engine adds a
    third blocking status.
    """
    card = _read(JS / "places/board/task-card.js")
    assert "BossModBoardColumns.columnFor(task.status) === 'needs'" in card
    assert "'data-needs':" in card
    # The attribute is absent, not "false", when the task is fine — h() drops a
    # null, so there is nothing to match and nothing to explain.
    assert "? 'true' : null" in card

    css = _read(CSS / "places.css")
    for status in ('blocked', 'stalled'):
        assert f'.task-card[data-status="{status}"]' not in css, (
            f"places.css re-states that {status} needs the operator"
        )

    # Load order: the map must be defined before the card that reads it.
    scripts = re.findall(r"static_url\('([^']+\.js)'\)", _read(HTML))
    assert scripts.index("js/places/board/board-columns.js") < scripts.index(
        "js/places/board/task-card.js"
    )


def test_new_task_is_a_link_not_a_fifth_bordered_button() -> None:
    """The toolbar already carries four bordered buttons; a fifth is noise.

    `+ New task` is the concept's `.linkish`, which in this tree is the shared
    `.btn-link` from controls.css — not a bespoke rule, and not a hand-rolled
    <a>.
    """
    toolbar = _read(JS / "places/board/board-toolbar.js")
    new_task = toolbar.split("'+ New task'", 1)[0].rsplit("h('button', {", 1)[1]
    assert "class: 'btn-link'" in new_task, new_task
    assert "onclick: onNewTask" in new_task


# ─── Metrics: the health panel, and rows that line up ───


def test_metrics_leads_with_a_health_panel() -> None:
    """The verdict was a sentence in the header; it is the first card now.

    The panel is a new CONTAINER, not new data: every number healthLine
    reported — the error percentage, the uptime, the agent count — is still
    reported, by the same formatters, in the same order.
    """
    js = _read(JS / "places/metrics/metric-cards.js")
    assert "function renderHealthPanel(data)" in js
    panel = js.split("function renderHealthPanel(data) {", 1)[1].split("\n    }", 1)[0]
    # A coloured dot needs a text equivalent (SC 1.4.1 — colour is never the
    # only carrier), so the verdict is rendered as a word, not implied by tone.
    assert "health.label" in panel
    assert "'data-health': health.tone" in panel
    assert "metric-health-light" in panel
    assert "healthDetail(data)" in panel
    # The dot is decoration beside the word that already says it.
    assert "'aria-hidden': 'true'" in panel

    # Every number the header sentence carried is still carried.
    detail = js.split("function healthDetail(data) {", 1)[1].split("\n    }", 1)[0]
    for kept in ("health.percent", "U.formatDuration(", "uptime", "agents"):
        assert kept in detail, kept
    # …and the verdict is not said twice: the panel's bold word is the label,
    # so the sentence beneath it must not repeat it.
    assert "health.label" not in detail

    css = _read(CSS / "places.css")
    for tone in ("ok", "warn", "bad"):
        assert f'.metric-health-light[data-health="{tone}"]' in css, tone

    # It leads. A verdict below the numbers it is a verdict about is not one.
    place = _read(JS / "places/metrics/metrics-place.js")
    body = place.split("function paintDashboard(data) {", 1)[1].split("\n    }", 1)[0]
    assert body.index("CARDS.renderHealthPanel(data)") < body.index(
        "CARDS.renderStatCards(data)"
    )


def test_the_two_health_indicators_do_not_share_one_class() -> None:
    """`.metric-health` is the panel; the Error Rate card's line is its own.

    They are different things — one is a card, one is a tinted sub-line inside
    a different card — and the panel's tone must not inherit the sub-line's
    rule, which colours ALL of its text by tone. A shared name is how the
    verdict and its sentence would have come out alert-red together.
    """
    js = _read(JS / "places/metrics/metric-cards.js")
    assert "'metric-card-sub metric-card-health'" in js
    css = _read(CSS / "places.css")
    assert '.metric-card-health[data-health="bad"]' in css
    assert '.metric-health[data-health=' not in css


def test_metric_rows_are_a_grid_so_the_numbers_line_up() -> None:
    """Three flex tricks became one grid.

    The concept's `70px 1fr 90px` is widened at both ends: 70px ellipses real
    agent names (they come from the roster, not from a fixture of one-word
    names), and the token row's value is `1.2M tokens · 45 calls`, which no
    90px column holds. The property that matters — a fixed label column, a
    fluid track, and values that align across rows — is the grid's.
    """
    css = _read(CSS / "places.css")
    row = css.split(".metric-row {", 1)[1].split("}", 1)[0]
    assert "display: grid" in row
    assert "grid-template-columns: 120px minmax(0, 1fr) 90px" in row
    # The wrap hacks the flex layout needed are gone with it.
    assert "flex: 0 0 120px" not in css
    assert ".metric-row .metric-bar { flex: 1 1 auto; }" not in css
    # The token row keeps its two-line shape, now as a spanning grid item.
    assert ".metric-row-stacked .metric-bar { grid-column: 1 / -1; }" in css


def test_the_metrics_panels_share_one_radius_token() -> None:
    """12px is the concept's card radius. A token, because three rules want it.

    Inlining `12px` in each would be the third magic number in this stylesheet
    and the one a future --r change would silently leave behind.
    """
    assert "--r-lg: 12px;" in _read(CSS / "tokens.css")
    css = _read(CSS / "places.css")
    shared = css.split(".metric-card,\n.metric-cell,\n.metric-panel {", 1)[1].split("}", 1)[0]
    assert "border-radius: var(--r-lg)" in shared
    assert "border-radius: 12px" not in css


# ─── Log: a monospace grid, not a stack of cards ───


def test_log_rows_are_a_monospace_grid() -> None:
    """Rows, not cards. The plan named `.log-rows`; this tree calls it
    `.log-list` — the class the renderer and the place both build — so the
    property is asserted where it actually lives.
    """
    css = _read(CSS / "places.css")
    rows = css.split(".log-list {", 1)[1].split("}", 1)[0]
    assert "font-family: ui-monospace" in rows

    row = css.split(".log-row {", 1)[1].split("}", 1)[0]
    assert "display: grid" in row
    assert "border-radius" not in row          # rows, not cards
    assert "border-bottom: 1px solid var(--line)" in row
    assert "border-left" not in row, "the tinted left edge is what the tint replaces"

    # `display: grid` on the row is the outer stack — the head-button and, when
    # it is open, the expansion. The four columns are the head's, because the
    # head is inside a <button> and `display: contents` on a button drops it out
    # of the accessibility tree in shipping browsers. So the columns are
    # asserted where they are.
    head = css.split(".log-row-head {", 1)[1].split("}", 1)[0]
    assert "display: grid" in head
    assert "grid-template-columns: 96px 120px minmax(0, 1fr) auto" in head

    # The whole error row is tinted, not one edge of it.
    assert '.log-row[data-type="error"] { color: var(--alert); }' in css
    # …which only works if the row's own children stop overriding it.
    assert ".log-row-toggle,\n.log-row-static {\n  display: block;\n  width: 100%;" in css
    toggle = css.split(".log-row-toggle,\n.log-row-static {", 1)[1].split("}", 1)[0]
    assert "color: inherit" in toggle


def test_the_log_row_keeps_everything_it_carried() -> None:
    """A restyle, not a rewrite: the type dot, the Active pill, the meta line
    and the expansion all survive the move to four columns.

    The dot moves INSIDE the time cell rather than taking a fifth column — it
    is the type's colour, and the type is what the timestamp is a timestamp of.
    The Active pill and the meta share the trailing cell, wrapped so that a row
    without a pill still lines its meta up with the rows that have one.
    """
    js = _read(JS / "places/log/log-row.js")
    assert "class: 'log-row-dot'" in js
    assert "class: 'log-row-tail'" in js
    tail = js.split("h('span', { class: 'log-row-tail' },", 1)[1].split("));", 1)[0]
    assert "log-row-active" in tail
    assert "log-row-meta" in tail
    # The dot is inside the time cell, so the head has exactly four children.
    time_cell = js.split("h('span', { class: 'log-row-time' },", 1)[1].split("),\n", 1)[0]
    assert "log-row-dot" in time_cell

    # The card border used to say "this row opens". Flattening the row must not
    # take that with it, so the affordance moves to hover on the toggle.
    assert ".log-row-toggle:hover { background: var(--bg); }" in _read(CSS / "places.css")

    # The expansion is untouched: it is a Phase 3B feature, not concept chrome.
    assert "if (expanded && detail) element.append(detail);" in js
    assert "'aria-expanded': expanded ? 'true' : 'false'" in js
    assert "if (!row.expandable)" in js

    css = _read(CSS / "places.css")
    assert ".log-row-tail {" in css
    for kept in ('.log-row[data-type="task"] .log-row-dot',
                 '.log-row[data-type="agent"] .log-row-dot',
                 '.log-row[data-type="system"] .log-row-dot'):
        assert kept in css, kept


def test_the_active_pill_is_readable_on_its_own_tint() -> None:
    """The same 4.33:1 miss the Office state pill had, in the Log.

    `.log-row-active` and `.log-step-badge` both put --ok on --ok-bg at 10px
    bold. --ok-ink is the pair that clears AA on that tint.
    """
    css = _read(CSS / "places.css")
    pill = css.split(".log-row-active {", 1)[1].split("}", 1)[0]
    assert "background: var(--ok-bg)" in pill
    assert "color: var(--ok-ink)" in pill
    badge = css.split(".log-step-badge {", 1)[1].split("}", 1)[0]
    assert "color: var(--ok-ink)" in badge


# ─── The sweep: one colour source, and two harnesses that cannot lie ───


def test_no_hex_outside_tokens() -> None:
    """One file owns colour. Every other stylesheet resolves through it.

    conversation.css has been held to this since Phase 2; the visual pass put
    the same rule over the whole tree, because a hex in places.css is exactly
    as unmaintainable as a hex in conversation.css was. The concept prototype
    leaks four (#1c5c40, #efcf95, #e0b25a, #e0a516) — one of them is now the
    --ok-ink token, and the other three had no consumer worth inventing.

    The pattern also matches an id selector made only of hex digits (`#abcdef`).
    That is the safe direction to be wrong in for a colour guard: a false alarm
    is read once, a missed hex is read never.
    """
    for path in sorted(CSS.glob("*.css")):
        if path.name == "tokens.css":
            continue
        found = re.search(r"#[0-9a-fA-F]{3,8}\b", _read(path))
        assert not found, f"{path.name}: {found.group(0) if found else ''}"


def test_the_fake_dom_refuses_a_selector_it_cannot_express() -> None:
    """A `null` from an unsupported selector reads as "the node is absent".

    js_fake_dom.cjs's matches() promised in prose that anything it could not
    understand would throw. Descendant (`.a .b`) and compound (`.a.b`)
    selectors slipped past that: both start with a `.`, so the class branch
    took them, sliced the dot, and asked whether the class list contained the
    literal string `a .b`. The answer was a silent false — a harness looking
    for a node it could not express got null and read it as an absence.

    Asserted by RUNNING the fake, not by reading it: the claim is about what
    querySelector does, and the previous version's docstring already made the
    claim the code did not keep.
    """
    probe = """
    const { FakeEl } = require(process.argv[1]);
    const outer = new FakeEl('div'); outer.className = 'a';
    const inner = new FakeEl('span'); inner.className = 'b';
    outer.append(inner);
    const root = new FakeEl('div'); root.append(outer);
    const result = {};
    for (const selector of ['.a .b', '.a.b', 'div > span', '#one #two']) {
        try { result[selector] = root.querySelector(selector) === null ? 'null' : 'node'; }
        catch (err) { result[selector] = 'threw'; }
    }
    // The forms it DOES support still work, or the throw above is just a ban.
    result.supported = [
        root.querySelector('.b') ? 'ok' : 'lost',
        root.querySelector('span') ? 'ok' : 'lost',
    ].join(',');
    process.stdout.write(JSON.stringify(result));
    """
    fake = str(Path(__file__).resolve().parent / "js_fake_dom.cjs")
    result = subprocess.run(
        ["node", "-e", probe, "--", fake], check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    for selector in (".a .b", ".a.b", "div > span", "#one #two"):
        assert payload[selector] == "threw", (selector, payload)
    assert payload["supported"] == "ok,ok", payload


def test_the_a11y_harness_counts_before_it_judges() -> None:
    """A naming check over zero controls is a vacuum, not a pass.

    The harness reported `everyShellControlIsNamed: true` over an empty walk
    while the rail was failing to paint — core/store.js catches a subscriber's
    exception, so nothing propagated and there was nothing left to name. It
    reports what it walked now, and refuses to judge a walk that is too small
    to mean anything.
    """
    harness = _read(Path(__file__).resolve().parent / "js_a11y_harness.cjs")
    assert "const CONTROL_FLOOR = { header: 8, roster: 6, footer: 0 };" in harness
    assert "controlsWalked: walked" in harness
    # The count alone cannot tell an empty people list from a short one: a rail
    # that painted nobody still renders six controls.
    assert "personRows.length < 1" in harness
    assert "personRowsWalked: personRows.length" in harness
    # The floors are checked BEFORE the naming walk, or they prove nothing.
    body = harness.split("const walked = {", 1)[1]
    assert body.index("CONTROL_FLOOR") < body.index("const unnamed = []")
    assert body.index("personRows.length < 1") < body.index("const unnamed = []")


def test_the_collapse_cannot_follow_the_rail_into_the_mobile_drawer() -> None:
    """Below 768px the rail is MOVED, not copied — and the collapse stays behind.

    responsive.js presents the real `#app-roster` node in a slide-over rather
    than building a second rail, and core/overlays.js appends that slide-over
    to <body>. So every collapse rule must be scoped under `#main-layout`: a
    bare `[data-rail="collapsed"] .roster-person` would follow the node into the
    drawer and hide the people inside it, on a screen where the drawer is the
    only way to reach them at all.
    """
    css = _read(CSS / "shell.css")
    for index, _ in enumerate(css):
        if not css.startswith('[data-rail="collapsed"]', index):
            continue
        assert css[max(0, index - 12):index].endswith("#main-layout"), (
            "a collapse rule is not rooted at #main-layout, so it would follow "
            "the rail into the mobile drawer"
        )
    assert '[data-rail="collapsed"]' in css  # …and the scan had something to scan

    js = _read(JS / "shell/responsive.js")
    assert "layoutEl.insertBefore(column, placeEl)" in js, "the node is returned, not rebuilt"
    assert "BossModOverlays.slideOver(" in js
