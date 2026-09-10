"""Agent marketplace: takeover states, card staleness, install, uninstall, trust.

The behavioural half runs in tests/js_marketplace_harness.cjs — a fake DOM, the
real modules eval'd in load order, one JSON verdict on stdout. The static half
pins the rules a harness cannot see: the API client is under the token wrap,
remote data never reaches a markup-string path, the stylesheet spends tokens
rather than hex, and index.html loads the six modules after what they call.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css" / "marketplace.css"
OVERLAYS = ROOT / "ui" / "static" / "css" / "overlays.css"
SHELL_CSS = ROOT / "ui" / "static" / "css" / "shell.css"
HTML = ROOT / "ui" / "templates" / "index.html"
HERE = Path(__file__).resolve().parent

# Load order matters twice over: the harness evals them in this order, and
# index.html must load them in an order that satisfies the same dependencies.
HARNESS_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "context" / "agent-api.js",
    JS / "context" / "agent-templates-api.js",
    JS / "marketplace" / "marketplace-withheld.js",
    JS / "marketplace" / "marketplace-items.js",
    JS / "marketplace" / "marketplace-sections.js",
    JS / "marketplace" / "marketplace-detail.js",
    JS / "marketplace" / "marketplace-view.js",
    JS / "marketplace" / "marketplace.js",
]

MARKETPLACE_MODULES = [
    JS / "context" / "agent-templates-api.js",
    JS / "marketplace" / "marketplace.js",
    JS / "marketplace" / "marketplace-withheld.js",
    JS / "marketplace" / "marketplace-items.js",
    JS / "marketplace" / "marketplace-sections.js",
    JS / "marketplace" / "marketplace-detail.js",
    JS / "marketplace" / "marketplace-view.js",
]

# The three files that put one pack's own text on screen. None of them may
# re-split a hire string, and none may render the raw `description`.
READERS = ("marketplace-view.js", "marketplace-sections.js", "marketplace-detail.js")

# Every module the takeover is built from, in index.html load order.
INDEX_MODULES = (
    "js/marketplace/marketplace-withheld.js",
    "js/marketplace/marketplace-items.js",
    "js/marketplace/marketplace-sections.js",
    "js/marketplace/marketplace-detail.js",
    "js/marketplace/marketplace-view.js",
    "js/marketplace/marketplace.js",
)


COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """The file with its comments stripped.

    Several rules below are about what the CODE does, and this module's prose
    quotes the very strings they ban — a comment explaining why a card no
    longer prints `what_done_looks_like` must not be read as printing it.
    """
    return COMMENT.sub("", _read(path))


def _harness() -> dict:
    args = ["node", str(HERE / "js_marketplace_harness.cjs")]
    args += [str(path) for path in HARNESS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _scripts() -> list[str]:
    return re.findall(r"static_url\('([^']+\.js)'\)", _read(HTML))


def test_marketplace_behaviour() -> None:
    """Every state, transition and invariant the harness drives."""
    payload = _harness()
    expected = [
        # Loading, failed and empty are three different answers.
        "loadingCopy", "loadingClears", "failedIsAlert", "retryRecovers", "emptyCopy",
        # Shape and semantics.
        "isTakeover", "railCounts", "railIsAList", "cardsAreButtons", "findHasALabel",
        # The rail's two kinds of row, told apart.
        "railGroupsScopesApartFromCategories", "railKeyboardCrossesBothGroups",
        # The decision this whole surface exists to get right.
        "cardStateFromContentHash",
        # Remote data is text, never markup.
        "remoteTitleStaysText",
        # Filtering.
        "filterNarrows", "noMatchCopy",
        # The card reads as a card: the pack's mission, and a chip only where
        # there is something to say.
        "cardBodyIsTheMission", "uninstalledCardOffersNoControl",
        # Both opening paragraphs, in the order they are read.
        "cardDrawsTheIntroAboveTheMission", "detailDrawsTheIntroAboveTheMission",
        "emptyPreambleDrawsNoIntro",
        # The card leads with what kind of pack it is, not with its own name
        # said twice.
        "cardShowsTheCategoryNotTheTitleTwice", "cardKeepsASpecialtyThatSaysSomethingElse",
        "everyCardCarriesItsCategory",
        # The detail is a view swap, and going back undoes it exactly.
        "detailReplacesTheBrowseView", "detailSectionsInOrder", "detailLeadIsTheMission",
        "detailAuthorLinkIsSafe", "backRestoresTheBrowseView", "backReturnsFocusToTheCard",
        "backIsAGlyphThatStillSaysWhereItGoes",
        # One rule on the whole read, and the sections behind a vertical tab
        # list that answers to the keyboard, the pointer and a click alike.
        "detailDrawsExactlyOneRule", "sectionsAreAVerticalTabList",
        "everySectionCarriesItsSubtitle", "oneTabStopAndOneSelection",
        "panelIsLabelledByItsTab", "arrowMovesTheSelectionAndThePanel",
        "endGoesToTheLastSection", "homeGoesBackToTheFirst", "arrowUpWrapsToTheEnd",
        "clickSelectsAndTakesTheKeyboard", "hoverWaitsForTheIntentDelay",
        "hoverSelectsOnceTheIntentIsClear", "leavingBeforeTheDelaySelectsNothing",
        "hoveringTheOpenSectionRebuildsNothing", "toolsAreATabOfTheirOwn",
        "aNewPackOpensOnItsFirstSection",
        # One exit per view, no footer, and never a state with nothing to Tab to.
        "noFooterActionRow", "detailHasOneDismissControl", "browseCarriesTheExit",
        "detailCloseIsNamed", "detailCloseDismissesTheTakeover",
        "browseCloseDismissesTheTakeover",
        "loadingCanBeTabbed", "failedCanBeTabbed", "emptyCanBeTabbed",
        # Install, at the pin that was displayed.
        "selectFocusesDetail", "detailShowsInstall", "installUsedDisplayedPin",
        "installFlipsCardState", "installAnnounces",
        # The hero's primary slot holds an action or nothing at all.
        "installedIsStatedNotOffered", "uninstallStaysQuiet", "updateKeepsThePrimary",
        # URL installs live only under Installed; the pane shows the done bar
        # and the tools the row carries.
        "extrasOnlyUnderInstalled", "detailShowsTheDoneBarAndTools",
        # Neither confirm may become a second focus trap.
        "uninstallAsksInline", "uninstallRemoved", "uninstallLeavesTheDetail",
        "uninstallKeepsACatalogCardsDetail", "backAfterUninstallLandsSomewhereReal",
        "urlRowIsInline", "urlFocused",
        "trustStripIsInline", "trustNotYetInstalled", "trustReissuesWithConfirm",
        "urlInstallLands",
        # What the API can legitimately leave out — and what it cannot.
        "nullSectionsRenderNoTab", "absentToolsRenderNoBlock",
        "thinPackKeepsItsSpecialty", "aSectionlessRowIsANamedFailure",
        # The rows that never became cards: reported, never as an error, and
        # never as one undifferentiated pile.
        "noWithheldDrawsNoNotice", "withheldNoticeNamesBothKinds",
        "withheldRowsCarryTheirCategory", "withheldIsNotAFailedRead",
        "withheldIsAnnouncedAndNotAnAlert", "withheldDoesNotStealFocus",
        "onlyTheUnreadableKindOffersARetry",
        "withheldRetryReloadsAndKeepsTheKeyboard",
        # A retry that works deletes the button that was pressed, and a read
        # that fails drops the catalog it lost.
        "gridRetryKeepsTheKeyboard", "aFailedReadDropsTheStaleCatalog",
        "aRecoveredReadPutsTheRailBack",
        # And the installed rows that lost their card while still installed.
        "aRefusedInstallIsNotAVanishedOne", "theRefusalFollowsIntoTheDetail",
        "aFailedReadDrawsNoEmptyCategoryGroup",
        "closesCleanly",
    ]
    assert payload["ok"] is True
    for key in expected:
        assert payload[key] is True, key
    # A verdict that stops covering something proves nothing by being green.
    assert set(expected) <= set(payload)


def test_the_rail_hands_focus_back_after_the_rebuild_it_causes() -> None:
    """render() claims it always puts focus back. onCategory made that false.

    It was the only handler that set no `focusRequest`, and the rail's rows
    carried no id either, so render()'s `keep` fallback had nothing to restore.
    Clicking a category destroyed the button that was clicked and focus fell to
    <body>. The rows are numbered — a category id is catalog data, and remote
    text has no business in a selector string — and the handler is handed the
    one it fired from.
    """
    payload = _harness()
    assert payload["railClickKeepsFocus"] is True
    assert payload["railReturnsToAllWithFocus"] is True
    view = _read(JS / "marketplace" / "marketplace-view.js")
    assert "id: `market-rail-${at}`" in view
    assert "handlers.onCategory(row.id, `#market-rail-${at}`)" in view
    state = _read(JS / "marketplace" / "marketplace.js")
    assert "onCategory(id, focus)" in state
    assert "state.focusRequest = focus || null;" in state


def test_the_detail_replaces_the_grid_and_back_undoes_it_exactly() -> None:
    """A 320px third column could not hold a hire contract, so it is a view now.

    Selecting a card replaces the header, the rail and the grid. `‹ Templates`
    has to put all three back the way they were — same category, same scroll
    offset, and the keyboard on the card that was opened, not at the top of a
    grid the operator had scrolled down. The card's own id travels with the
    click for the same reason the rail's does: a pack id is catalog data and
    has no business in a selector string.
    """
    payload = _harness()
    for key in (
        "detailReplacesTheBrowseView", "backRestoresTheBrowseView",
        "backReturnsFocusToTheCard", "detailCloseDismissesTheTakeover",
    ):
        assert payload[key] is True, key
    view = _read(JS / "marketplace" / "marketplace-view.js")
    assert "id: `market-card-${at}`" not in view, "the id is built once and reused"
    assert "const id = `market-card-${at}`;" in view
    assert "handlers.onSelect(item.key, `#${id}`)" in view
    # The reading position is carried on the state, not measured back out of a
    # layout that no longer exists.
    assert "state.browseScroll = leaving.scrollTop;" in view
    assert "scroller.scrollTop = state.browseScroll;" in view
    state = _read(JS / "marketplace" / "marketplace.js")
    assert "onSelect(key, focus)" in state
    assert "state.focusRequest = state.cardFocus || '#market-find';" in state
    # And it is dropped the moment the grid it points into can have changed
    # shape, so back never follows a stale index to the wrong card — or to none.
    assert payload["backAfterUninstallLandsSomewhereReal"] is True
    uninstall = state.split("async function runUninstall(", 1)[1]
    assert "state.cardFocus = null;" in uninstall.split("const handlers", 1)[0]


def test_a_failed_catalog_is_never_left_behind_the_detail_view() -> None:
    """Its message and its Try again are in the grid, and there is no other copy.

    The detail view has no retry and no room for one, so a read that failed
    while a pack was open must put the browse view back rather than strand the
    operator in a view whose only exits are `‹ Templates` and `✕`.
    """
    state = _read(JS / "marketplace" / "marketplace.js")
    failure = state.split("} catch (err) {", 1)[1].split("return;", 1)[0]
    assert "state.status = 'failed';" in failure
    assert "state.detailOpen = false;" in failure
    view = _read(JS / "marketplace" / "marketplace-view.js")
    assert (
        "const detail = Boolean(state.detailOpen && state.selected "
        "&& state.status !== 'failed');"
    ) in view


def test_the_card_chip_is_a_state_and_never_a_control() -> None:
    """`Install` on an uninstalled card invited a click that did nothing.

    The card selects; installing is the detail view's primary. So the chip is
    painted only for the two states that have something to SAY — Installed and
    Update available — and an uninstalled card carries none at all.
    """
    assert _harness()["uninstalledCardOffersNoControl"] is True
    view = _read(JS / "marketplace" / "marketplace-view.js")
    chip = view.split("const STATE_CHIP = Object.freeze({", 1)[1].split("});", 1)[0]
    assert "install:" not in chip, "an uninstalled card must have no chip"
    assert "installed: 'Installed'" in chip
    assert "update: 'Update available'" in chip
    # And it is a <span>, inside the one button the card already is.
    assert "h('span', { class: 'market-card-state' }, chip)" in view


def test_the_card_leads_with_the_category_and_never_its_own_title_twice() -> None:
    """"Code Auditor / Code Auditor" was the most valuable line on every card.

    `specialty` repeats the title on nearly every pack in the real catalog, so
    the line under the name carried nothing while holding the row the eye lands
    on first. The echo is dropped IN THE PROJECTION — one decision, so the card
    and the detail cannot disagree about whether a pack has a specialty — and
    the row goes to the category, which is real, distinct, and appeared nowhere
    on the card before. A specialty that says something else is still shown.

    The slug is title-cased by the helper the rail's own rows already use.
    Deriving it twice is how "product-design" comes to read two ways on one
    screen, so there is exactly one definition of it in the browse view.
    """
    payload = _harness()
    for key in (
        "cardShowsTheCategoryNotTheTitleTwice",
        "cardKeepsASpecialtyThatSaysSomethingElse", "everyCardCarriesItsCategory",
    ):
        assert payload[key] is True, key
    items = _read(JS / "marketplace" / "marketplace-items.js")
    assert "function distinctSpecialty(title, specialty)" in items
    assert "specialty: distinctSpecialty(card.title || card.id, card.specialty)," in items
    assert "specialty: distinctSpecialty(row.title, row.specialty)," in items
    view = _code(JS / "marketplace" / "marketplace-view.js")
    # One title-caser, spent by the rail row and the card alike.
    assert view.count("function categoryLabel(") == 1
    assert "const category = categoryLabel(item.category);" in view
    assert "h('span', { class: 'market-card-category' }, category)" in view
    # Light blue, and it is the pair this codebase has already measured: text on
    # a tint takes that tint's ink, never --accent, which fails on it.
    css = _read(CSS)
    chip = css.split(".market-card-category {", 1)[1].split("}", 1)[0]
    assert "background: var(--accent-bg);" in chip
    assert "color: var(--blue-ink);" in chip
    assert "var(--accent)" not in chip.replace("var(--accent-bg)", "")
    # A selected card IS --accent-bg, so the chip steps to the deeper tint of
    # the same family rather than vanishing into its own ground.
    assert (
        '.market-card[aria-current="true"] .market-card-category '
        "{ background: var(--blue); }"
    ) in css


def test_a_pack_wears_its_categorys_bubble_in_both_views() -> None:
    """A grid of cards with nothing to scan by but 15px of title text.

    Every card and the detail hero now lead with the same bubble the roster
    draws for a person, carrying the CATEGORY'S initials — `E` for Engineering,
    `PD` for Product Design, and two letters for a three-word category, never
    three. The hero used to build its mark from the pack's own title, which
    said what the line beside it already said.

    The bubble does NOT replace the category chip. They are different jobs on
    different bands: the bubble is a colour-coded scanning aid that lets the
    grid be grouped at a glance, and the chip is the word — the same word the
    rail filters by. A coloured `E` is not "Engineering", and Engineering and
    Education would share it; SC 1.4.1 forbids leaving colour as the only
    carrier of a fact. That word is also what lets the bubble stay
    `aria-hidden` in both views, which is why the detail's byline gained the
    category it had never named.
    """
    payload = _harness()
    for key in (
        "everyCardLeadsWithItsCategoryMark", "aOneWordCategoryIsOneLetter",
        "aTwoWordCategoryIsTwoLetters", "aThreeWordCategoryStillTakesTwoLetters",
        "anAbsentCategoryIsStatedNotBlank", "twoCategoriesAreToldApart",
        "theSameCategoryIsTheSameColourTwice", "theHeroCarriesTheCardsMark",
        "theHeroNamesTheCategoryItMarks",
        # The chip is still there, on every card.
        "everyCardCarriesItsCategory", "cardShowsTheCategoryNotTheTitleTwice",
    ):
        assert payload[key] is True, key

    # ONE builder, on the seam that already carries the two parts both views
    # spend. The browse view cannot own it — the detail view loads first and
    # calling back into it would be a cycle.
    detail = _code(JS / "marketplace" / "marketplace-detail.js")
    assert "function categoryMark(slug, size) {" in detail
    assert "categoryMark," in detail.rsplit("return {", 1)[-1]
    assert "categoryMark(item.category, 'lg')" in detail
    view = _code(JS / "marketplace" / "marketplace-view.js")
    assert "DETAIL.categoryMark(item.category, 'md')" in view
    # ...and nothing here paints a second bubble of its own.
    builders = {
        path.name: _code(path).count("BossModAvatar.create(")
        for path in sorted((JS / "marketplace").glob("*.js"))
    }
    assert sum(builders.values()) == 1, builders
    assert builders["marketplace-detail.js"] == 1, builders

    # The colour is DERIVED from the slug through the shared tint, never a
    # colour per known category: `packs/<category>/` is whatever a contributor
    # adds, so a table would go stale on the first one.
    assert "BossModAvatar.seedFor(slug)" in detail
    assert "BossModAvatar.initials(ITEMS.categoryLabel(slug))" in detail
    for path in sorted((JS / "marketplace").glob("*.js")):
        code = _code(path)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code), f"hardcoded colour in {path.name}"
        # No second contrast or tint routine out here.
        assert "luminance" not in code and "tintFor" not in code, path.name

    # One title-caser for the whole surface: the rail row, the card's chip and
    # the mark's letters all cut "product-design" the same way.
    items = _code(JS / "marketplace" / "marketplace-items.js")
    assert "function categoryLabel(slug) {" in items
    assert "categoryLabel," in items.rsplit("return {", 1)[-1]
    derivations = [
        path.name for path in sorted((JS / "marketplace").glob("*.js"))
        if "replace(/^[a-z]/" in _code(path)
    ]
    assert derivations == ["marketplace-items.js"], derivations


def test_the_cards_mark_and_name_share_one_band() -> None:
    """The bubble is a flex row's first item, not a float or a magic offset."""
    css = _read(CSS)
    head = css.split(".market-card-head {", 1)[1].split("}", 1)[0]
    assert "display: flex" in head
    assert "align-items: center" in head
    assert "gap: 10px" in head
    # A long title wraps inside the row rather than widening the grid track.
    title = css.split(".market-card-title {", 1)[1].split("}", 1)[0]
    assert "min-width: 0;" in title
    # The chip kept its own band and its own tint pair.
    assert ".market-card-category {" in css


def test_the_detail_draws_one_rule_and_reads_its_sections_in_a_tab_list() -> None:
    """Six sections, six horizontal rules, and no shape to the page at all.

    Every section used to be a labelled block fenced off from the last by a
    `border-top`, stacked down a full-width view: to read what a pack said about
    scope you scrolled past what it said about handoff, and the only visual
    rhythm was the fences. The description now sits full width on top, ONE rule
    separates it from the contract, and the contract is read a section at a time
    — a list of what the pack answers beside the answer.

    The list is a real vertical tab list, not a set of divs that repaint on
    click: `role="tablist"` with `aria-orientation`, tabs that say which is
    selected, one tab stop for the group with the arrows moving inside it, and a
    panel named by the tab that opened it. That is what makes it reachable
    without a pointer at all.
    """
    payload = _harness()
    for key in (
        "detailDrawsExactlyOneRule", "sectionsAreAVerticalTabList",
        "everySectionCarriesItsSubtitle", "oneTabStopAndOneSelection",
        "panelIsLabelledByItsTab", "arrowMovesTheSelectionAndThePanel",
        "endGoesToTheLastSection", "homeGoesBackToTheFirst", "arrowUpWrapsToTheEnd",
        "clickSelectsAndTakesTheKeyboard", "toolsAreATabOfTheirOwn",
        "aNewPackOpensOnItsFirstSection",
        "nullSectionsRenderNoTab",
    ):
        assert payload[key] is True, key
    reader = _read(JS / "marketplace" / "marketplace-sections.js")
    assert "role: 'tablist'" in reader
    assert "'aria-orientation': 'vertical'" in reader
    assert "role: 'tab'," in reader
    assert "'aria-selected': selected ? 'true' : 'false'," in reader
    assert "tabindex: selected ? '0' : '-1'," in reader
    assert "role: 'tabpanel'" in reader
    assert "'aria-labelledby': `market-section-${at}`," in reader
    # Every key the pattern owes: both arrows, both ends, and the default that
    # does nothing at all rather than swallowing the keystroke.
    assert "const STEP = Object.freeze({ ArrowDown: 1, ArrowUp: -1 });" in reader
    assert "else if (event.key === 'Home') next = 0;" in reader
    assert "else if (event.key === 'End') next = entries.length - 1;" in reader
    assert "if (next === null) return;" in reader
    # Which section is up is STATE, held where the other nine pieces of this
    # surface's state are held, and cleared with every change of pack: a section
    # id from the last pack would name one the next may not carry.
    state = _read(JS / "marketplace" / "marketplace.js")
    assert "onSection(id, focus)" in state
    assert "sectionKey: null, pendingUninstall: null, error: null, notice: ''," in state
    assert "detailOpen: true, cardFocus: null, sectionKey: null" in state
    # The rule is DRAWN ONLY when there is something under it to separate, and
    # it is the one element of its kind in the file.
    detail = _code(JS / "marketplace" / "marketplace-detail.js")
    assert "reader ? h('hr', { class: 'market-detail-rule' }) : null," in detail
    assert detail.count("h('hr'") == 1
    # And the stack of per-section fences is gone from the sheet, not merely
    # unused by the markup.
    css = _read(CSS)
    for retired in (".market-detail-section", ".market-detail-heading"):
        assert retired not in css, retired
        assert retired.lstrip(".") not in _code(
            JS / "marketplace" / "marketplace-detail.js"
        ), retired
    # The panel scrolls on its own: a long handoff must not push the list that
    # picks it off the bottom of the view.
    panel = css.split(".market-section-panel {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in panel
    assert "background: var(--bg);" in panel
    assert "border-radius: var(--r-lg);" in panel
    # Selected takes the app's selection tint, and BOTH lines are re-inked:
    # --hint measures 4.19:1 on --accent-bg and fails, which is why the subtitle
    # is --muted off the tint and --blue-ink on it.
    selected = css.split('.market-section-tab[aria-selected="true"] {', 1)[1].split("}", 1)[0]
    assert "background: var(--accent-bg);" in selected
    assert "color: var(--blue-ink);" in selected
    assert "color: var(--muted);" in css.split(".market-section-sub {", 1)[1].split("}", 1)[0]
    assert (
        '.market-section-tab[aria-selected="true"] .market-section-sub '
        "{ color: var(--blue-ink); }"
    ) in css


def test_hover_selects_a_section_only_once_the_pointer_has_rested() -> None:
    """Hover-to-read, without a list that strobes when you cross it.

    The operator asked for hover, and hover on its own is a panel that repaints
    four times while the pointer travels to the one entry it was aimed at. So it
    is an INTENT: the pointer has to rest before anything changes, leaving is
    enough to call it off, and a deliberate choice — click or arrow — cancels
    whatever the pointer had pending.

    Two rules keep it honest. It never moves focus, because a pointer must not
    take the keyboard off what the operator left it on. And it arms nothing at
    all over the entry already up: selecting that one rebuilds the very node the
    pointer is standing on, which a browser answers with a fresh mouseenter on
    the replacement — a loop, not a selection.
    """
    payload = _harness()
    for key in (
        "hoverWaitsForTheIntentDelay", "hoverSelectsOnceTheIntentIsClear",
        "leavingBeforeTheDelaySelectsNothing", "hoveringTheOpenSectionRebuildsNothing",
    ):
        assert payload[key] is True, key
    reader = _read(JS / "marketplace" / "marketplace-sections.js")
    assert "const HOVER_DELAY_MS = 120;" in reader
    assert "onmouseenter: (event) => armHover(entry, selected, event.target, handlers)," in reader
    assert "onmouseleave: () => cancelHover()," in reader
    # The loop guard, and the teardown guard: a timer must not reach past the
    # surface that armed it.
    armed = reader.split("function armHover(", 1)[1].split("\n    }", 1)[0]
    assert "if (selected) return;" in armed
    assert "if (!document.body.contains(node)) return;" in armed
    assert "handlers.onSection(entry.spec.id, null);" in armed
    # Every deliberate selection goes through one door, so a pending hover
    # cannot land on top of the choice just made.
    assert "function choose(entry, focus, handlers) {\n        cancelHover();" in reader
    # And hover is never the only way in: click and both arrows reach every
    # entry, which is what makes the list work on a touch screen.
    assert "onclick: () => choose(entry, `#${id}`, handlers)," in reader
    assert "onkeydown: (event) => move(event, at, entries, handlers)," in reader


def test_the_sections_are_the_servers_and_absence_is_reported_as_absence() -> None:
    """No string is re-split in the browser, and no section is invented.

    `GET /api/agent-packs` and `GET /api/agent-templates` both carry `sections`
    already split by the parser the pack quality gate reads. TWO absences are
    real: a single section is null when the text had no such heading, and
    `tools_hint` is absent — not `[]` — on a pack that lists none.

    The third absence this used to pin is gone, and the requirement genuinely
    changed under it. `unparsedCardRenders` and `unparsedDetailOpens` asserted
    that a card carrying no `sections` still rendered and still opened, from a
    fabricated payload no route can produce any more: validation moved to the
    source, so a pack that fails to parse is withheld and never becomes a card,
    and an installed row computes `sections` from two TEXT NOT NULL columns.
    Keeping them would have pinned a branch nothing can reach and — worse —
    required the projection to keep quietly drawing a blank card for a payload
    that is a contract break. They are replaced by
    `aSectionlessRowIsANamedFailure`, which asserts the projection now throws
    and names the module and the row.
    """
    payload = _harness()
    for key in (
        "aSectionlessRowIsANamedFailure", "nullSectionsRenderNoTab",
        "absentToolsRenderNoBlock", "detailSectionsInOrder", "cardBodyIsTheMission",
        "detailLeadIsTheMission",
    ):
        assert payload[key] is True, key
    # The dead branch is gone from both readers, not merely unused.
    items = _code(JS / "marketplace" / "marketplace-items.js")
    assert "row.sections || null" not in items
    assert "const group = sections.description;" in items
    assert "carries no parsed sections" in items
    # One reader, and it answers all three absences the same way. It moved to
    # marketplace-sections.js with the tab list that is now its only caller:
    # the detail view frames a pack, this reads one.
    sections = _read(JS / "marketplace" / "marketplace-sections.js")
    assert "function sectionText(sections, half, key)" in sections
    assert "const group = sections ? sections[half] : null;" in sections
    assert "return typeof value === 'string' && value.trim() ? value : null;" in sections
    assert "sectionText" not in _code(JS / "marketplace" / "marketplace-detail.js")
    # Nothing splits a hire string in the browser: the views read the server's
    # `sections` and never the raw `description` the cards used to print whole,
    # headings and all. It survives on the item for the FILTER alone, which is
    # why the ban is on rendering it rather than on carrying it.
    for name in READERS:
        source = _code(JS / "marketplace" / name)
        assert "item.description" not in source, name
        assert "what_done_looks_like" not in source, name
    assert "item.description" in _code(JS / "marketplace" / "marketplace-items.js")


def test_the_withheld_rows_are_reported_under_the_grid_and_never_as_an_error() -> None:
    """A pack vanishing out of the operator's own catalog is how it stays broken.

    The server already withheld the CARD — install runs the same gate the
    browse list does, so a card for a pack this app rejects is a card whose
    only outcome is an error — and reported the fact in `withheld`. Nothing in
    the browser read it: `grep -rn "withheld" ui/static/js/` returned nothing,
    so the whole point of reporting them was unmet and a maintainer merged a PR
    into their own catalog and watched the pack never appear.

    It is a NOTICE, under the grid: the packs above it loaded fine. So no
    `role="alert"` anywhere in it, `role="status"` on the summary line alone —
    announced politely, and scoped to one sentence because the browse view is
    rebuilt on every keystroke in the filter box — and no focus taken from
    wherever the takeover put it. Nothing at all is drawn when nothing was
    withheld.
    """
    payload = _harness()
    for key in (
        "withheldNoticeNamesBothKinds", "withheldRowsCarryTheirCategory",
        "withheldIsNotAFailedRead", "withheldIsAnnouncedAndNotAnAlert",
        "withheldDoesNotStealFocus", "noWithheldDrawsNoNotice",
    ):
        assert payload[key] is True, key
    source = _code(JS / "marketplace" / "marketplace-withheld.js")
    # Announced, never asserted. `role="alert"` interrupts, and this is not an
    # error — the one live region is the summary, and it is polite.
    assert "role: 'status'" in source
    assert "role: 'alert'" not in source
    # Remote pack-authored text — titles, paths, and the message, which can
    # quote pack content — reaches the document as text nodes and nothing else.
    for banned in ("innerHTML", "insertAdjacentHTML"):
        assert banned not in _read(JS / "marketplace" / "marketplace-withheld.js")
    # Under the grid, in the grid column: never in place of it.
    view = _code(JS / "marketplace" / "marketplace-view.js")
    grid = view.split("h('div', { class: 'market-grid' },", 1)[1].split("\n    }", 1)[0]
    assert grid.index("gridInner(state, handlers)") < grid.index(
        "WITHHELD.notice(state.withheld, handlers)"
    ), "the notice goes under the grid, never in place of it"
    # Quiet, and not dressed as the failure it is not: no alert token in it.
    css = _read(CSS)
    block = css.split("/* ─── Browse: the rows the grid would not show ───", 1)[1]
    block = block.split("/* ─── The detail view ─── */", 1)[0]
    assert "--alert" not in block, "a withheld row is not an error"
    assert "background: var(--amber);" in block
    assert "color: var(--amber-ink);" in block


def test_a_refused_pack_and_an_unreadable_one_are_told_as_two_facts() -> None:
    """A GitHub 502 was reported to a maintainer as "your pack is broken".

    `list_catalog` caught `AgentPackError` around the fetch AND the parse
    together, so a transport failure on one pack file came back wearing the
    same shape as a quality refusal. They are not the same fact: a refusal
    names what is wrong inside a file the maintainer has to change, and an
    unavailability knows nothing whatever about the content, so the only
    honest offer is to read the catalog again.

    The split is at the SOURCE and by which call failed — never by matching a
    message, and never by classifying `code`, which would be silently wrong the
    day a new code is raised.
    """
    payload = _harness()
    for key in (
        "withheldNoticeNamesBothKinds", "onlyTheUnreadableKindOffersARetry",
        "withheldRetryReloadsAndKeepsTheKeyboard",
    ):
        assert payload[key] is True, key
    source = _code(JS / "marketplace" / "marketplace-withheld.js")
    # One list with an explicit kind, so the browser branches on the kind and
    # never re-derives it from the code or the message.
    assert "const REFUSED = 'refused';" in source
    assert "const UNAVAILABLE = 'unavailable';" in source
    assert "row.code" not in source.split("function byKind", 1)[1].split("}\n", 1)[0]
    # An unknown kind is a contract break and is named, never sorted into a
    # pile or dropped on the floor.
    assert "unknown withheld kind" in source
    # And the retry lands the keyboard somewhere, because a reload that works
    # deletes the button that was pressed.
    state = _read(JS / "marketplace" / "marketplace.js")
    assert "onRetry(focus)" in state
    assert "handlers.onRetry('#market-find')" in source


def test_both_retries_land_the_keyboard_and_a_failed_read_drops_its_catalog() -> None:
    """Two latent faults in the same two lines, and one of them was visible.

    The grid's `Try again` took focus to <body> every time it WORKED: a
    successful read replaces the failure message and its button with cards, and
    nothing asked for the keyboard back. The withheld notice's retry already
    passed a landing spot for exactly this reason; the grid's now passes the
    same one, and the filter box is the right answer because it is in the browse
    view whatever the read returned.

    And a read that failed kept the categories of the read before it. The rail
    is drawn in the failed state — it is the browse view that comes back up —
    so it went on offering a row per category out of a catalog the app no longer
    held, each one counting packs it could not show. Cleared in the FAILURE
    branch and not at the top of `load`, because a reload still in flight has
    lost nothing yet and must not empty the rail under the operator who asked
    for it.
    """
    payload = _harness()
    for key in (
        "gridRetryKeepsTheKeyboard", "retryRecovers",
        "aFailedReadDropsTheStaleCatalog", "aRecoveredReadPutsTheRailBack",
    ):
        assert payload[key] is True, key
    view = _read(JS / "marketplace" / "marketplace-view.js")
    assert "onclick: () => handlers.onRetry('#market-find')," in view
    assert "handlers.onRetry()," not in view, "a retry with no landing spot"
    state = _read(JS / "marketplace" / "marketplace.js")
    failure = state.split("} catch (err) {", 1)[1].split("return;", 1)[0]
    assert "state.categories = [];" in failure
    # And NOT at the top of load(), where it would empty the rail on every
    # reload rather than on the reads that actually lost something.
    opening = state.split("async function load() {", 1)[1].split("try {", 1)[0]
    assert "categories" not in opening


def test_an_installed_pack_that_went_bad_is_not_one_that_left_the_repo() -> None:
    """The one place withholding hides something from someone already affected.

    `indexInstalled` calls an installed row an "extra" when no catalog card
    carries its `pack_id`. Once refused packs stopped becoming cards, a pack
    that has SINCE GONE BAD in the catalog fell into that same bucket as one
    DELETED from the repo, and the two rendered identically — with no hint that
    the catalog still lists it and this app now refuses it. The operator is
    running agents built from that pack; it is the last row that should be
    quietly reshelved.

    The withheld ids answer it, so `indexInstalled` takes the withheld list as
    a third input and keys it by pack id.
    """
    payload = _harness()
    for key in ("aRefusedInstallIsNotAVanishedOne", "theRefusalFollowsIntoTheDetail"):
        assert payload[key] is True, key
    items = _code(JS / "marketplace" / "marketplace-items.js")
    assert "function indexInstalled(templates, categories, withheld)" in items
    assert "withheld.forEach((row) => { withheldByPackId[row.id] = row; });" in items
    # Four distinct answers, and a URL install is not warned about at all.
    assert "function extraStatus(row, withheldByPackId)" in items
    assert "if (!row.pack_id) return 'url';" in items
    assert "return withheld ? withheld.kind : 'gone';" in items
    # Every call site passes it: a stale index would mislabel the very row this
    # exists to label.
    state = _read(JS / "marketplace" / "marketplace.js")
    assert state.count("state.templates, state.categories, state.withheld,") == 3
    # Said in both views, from ONE wording, so they cannot drift apart.
    withheld = _code(JS / "marketplace" / "marketplace-withheld.js")
    assert "function installedNote(catalogStatus)" in withheld
    for name in ("marketplace-view.js", "marketplace-detail.js"):
        assert "WITHHELD.installedNote(item.catalogStatus)" in _code(
            JS / "marketplace" / name
        ), name


def test_the_preamble_is_content_and_renders_above_the_mission() -> None:
    """It rendered NOWHERE, and the server had sent it.

    ``describe_pack`` splits a hire description into ``preamble`` — the prose
    before the first heading — plus one key per heading it recognises. A
    description reading "Read this first.\n\nMission: …" carries BOTH, and the
    two are not alternatives: the card body and the detail lead each read
    ``mission`` alone, so the lead-in the pack author wrote appeared on no
    screen at all while the API was returning it.

    The pairing is decided in the PROJECTION rather than in each view, so the
    card and the detail cannot disagree about which two paragraphs a pack opens
    with or which of them comes first.
    """
    payload = _harness()
    for key in (
        "cardDrawsTheIntroAboveTheMission", "detailDrawsTheIntroAboveTheMission",
        "emptyPreambleDrawsNoIntro",
    ):
        assert payload[key] is True, key
    items = _read(JS / "marketplace" / "marketplace-items.js")
    assert "function openingText(sections)" in items
    # Both bodies still come out of the SAME group, in this order — that is the
    # rule this test exists for. The `group ? … : null` guards that used to be
    # written around them are gone: `sections` can no longer be absent from
    # either route, so the guard was a dead branch that let a broken payload
    # render as a blank card instead of saying so. `parsed()` names it now.
    assert "return { intro: body(group.preamble), mission: body(group.mission) };" in items
    # Neither view re-derives either paragraph: both read what the projection
    # decided, which is what keeps them from drifting apart. The section reader
    # is in the same ban — it reads the five labelled sections and never the two
    # paragraphs above it, which are not sections and are not its to draw.
    for name in ("marketplace-view.js", "marketplace-detail.js"):
        source = _code(JS / "marketplace" / name)
        assert "item.intro" in source, name
        assert "item.mission" in source, name
    for name in READERS:
        source = _code(JS / "marketplace" / name)
        assert "'description', 'mission'" not in source, name
        assert "'description', 'preamble'" not in source, name
    # And the card's lead-in is clamped on its own lines rather than eating the
    # mission's three.
    css = _read(CSS)
    intro = css.split(".market-card-intro {", 1)[1].split("}", 1)[0]
    assert "-webkit-line-clamp: 2;" in intro
    assert "color: var(--hint);" in intro


def test_the_back_control_is_a_glyph_that_still_says_where_it_goes() -> None:
    """The word went; the name stayed. Two changes, and the second is the rule.

    `‹ Templates` first named the wrong place — most of what is behind that
    control is packs that are not installed templates — and became `‹ Back`.
    `Back` then said nothing the announced name did not already say better, and
    it was the only visible word in a bar whose other control, the `✕` beside
    it, is a glyph. The pair match now.

    The requirement this test exists for is the half that did NOT change: an
    icon-only control is still a real button with an accessible name that says
    where it goes, and the glyph stays aria-hidden so it cannot be announced as
    a punctuation mark instead. Dropping the label is a visual change; dropping
    the name would be a keyboard dead end.
    """
    assert _harness()["backIsAGlyphThatStillSaysWhereItGoes"] is True
    detail = _read(JS / "marketplace" / "marketplace-detail.js")
    assert "backLabel: 'Back to the marketplace'," in detail
    assert "'aria-label': COPY.backLabel," in detail
    # The glyph is the button's ONLY child, and it is hidden from the name.
    assert (
        "h('span', { class: 'market-detail-back-mark', 'aria-hidden': 'true' }, '‹')),"
    ) in detail
    # The retired copy is gone rather than merely unrendered — comments
    # stripped, because the prose above quotes both of the words it replaced.
    copy = _code(JS / "marketplace" / "marketplace-detail.js")
    copy = copy.split("const COPY = Object.freeze({", 1)[1].split("});", 1)[0]
    assert "back: " not in copy, "the visible label is gone, not just unused"
    # A chevron is narrower than a word, so the target is squared up (SC 2.5.8).
    css = _read(CSS)
    back = css.split(".market-detail-back {", 1)[1].split("}", 1)[0]
    assert "min-width: 32px;" in back
    assert "justify-content: center;" in back
    assert "Templates" not in copy, "the control no longer names the library"


def test_the_takeover_carries_one_dismiss_control_and_no_footer() -> None:
    """The detail had a header ✕ AND the modal's footer Close: two, for one errand.

    A full-screen takeover carries its controls in its own top-right corner, so
    the footer goes and both views build the same `✕` from one builder — browse
    included, which had no visible exit of its own until now. Removing the
    footer also removes createModal's fallback of focusing the last action
    button when the body has nothing focusable, so every state the takeover can
    open in has to hold a keyboard by itself: loading, ready, empty and failed
    all do, and the failed one keeps its `Try again` besides.
    """
    payload = _harness()
    for key in (
        "noFooterActionRow", "detailHasOneDismissControl", "browseCarriesTheExit",
        "detailCloseDismissesTheTakeover", "browseCloseDismissesTheTakeover",
        "loadingCanBeTabbed", "failedCanBeTabbed", "emptyCanBeTabbed",
    ):
        assert payload[key] is True, key
    state = _read(JS / "marketplace" / "marketplace.js")
    assert "actions: []," in state
    assert "COPY.close" not in state, "the footer's Close is gone, and so is its copy"
    # One builder, spent by both views: browse cannot end up without an exit.
    detail = _read(JS / "marketplace" / "marketplace-detail.js")
    assert "function dismissButton(handlers)" in detail
    assert "'aria-label': COPY.dismiss," in detail
    assert detail.count("id: 'market-close'") == 1
    view = _read(JS / "marketplace" / "marketplace-view.js")
    assert "DETAIL.dismissButton(handlers)" in view
    # The empty row createModal still builds leaves neither a rule nor a gap.
    css = _read(CSS)
    assert '.modal-panel[data-size="takeover"] .modal-actions:empty { display: none; }' in css
    assert '.modal-panel[data-size="takeover"] .modal-body { margin-bottom: 0; }' in css


def test_the_heros_primary_slot_only_ever_holds_an_action() -> None:
    """The biggest element on the screen was the one that could not be pressed.

    An installed, current pack rendered a static `Installed` slab in the hero's
    primary slot while `Uninstall` sat quiet beside it. `Installed` is a FACT
    about the pack — the same kind of fact as its author and its pin — so it
    reads on the byline with them and the slot stays empty. An update available
    IS actionable, so `Update` keeps the slot. `Uninstall` never inherits it: it
    is the destructive action and must not be the most prominent thing here.
    """
    payload = _harness()
    for key in ("installedIsStatedNotOffered", "uninstallStaysQuiet", "updateKeepsThePrimary"):
        assert payload[key] is True, key
    detail = _read(JS / "marketplace" / "marketplace-detail.js")
    primary = detail.split("function primary(item, state, handlers) {", 1)[1]
    primary = primary.split("\n    }", 1)[0]
    assert "if (item.state === 'installed') return null;" in primary
    assert "market-action-lead" in primary
    assert "COPY.uninstall" not in primary, "the destructive action is not the primary"
    # The state moved to the metadata line, and it is a span there, not a slab.
    assert "h('span', { class: 'market-detail-installed' }, COPY.installed)" in detail
    assert "market-detail-state" not in detail
    css = _read(CSS)
    assert ".market-detail-state" not in css
    installed = css.split(".market-detail-installed {", 1)[1].split("}", 1)[0]
    assert "background: var(--ok-bg);" in installed and "color: var(--ok-ink);" in installed


def test_staleness_is_content_hash_never_commit_sha() -> None:
    """The catalog pin is repo-wide; a SHA comparison would stale everything."""
    items = _read(JS / "marketplace" / "marketplace-items.js")
    decision = items.split("function cardState(", 1)[1].split("\n    }", 1)[0]
    assert "content_hash" in decision
    assert "commit_sha" not in decision
    assert "sha" not in decision.lower().replace("content_hash", "")
    # And what installs is what was read: the card's own pin travels as `ref`.
    assert "ref: item.commitSha" in _read(JS / "marketplace" / "marketplace.js")


def test_the_takeover_is_the_only_dialog_the_marketplace_opens() -> None:
    """Install-from-URL and both confirms are inline. No nested focus trap."""
    state = _read(JS / "marketplace" / "marketplace.js")
    assert state.count("createModal(") == 1
    assert "size: 'takeover'" in state
    for path in MARKETPLACE_MODULES:
        source = _code(path)
        assert "slideOver" not in source, path.name
        assert "createMenu" not in source, path.name


def test_the_api_client_is_under_the_token_wrap_and_keeps_the_error_code() -> None:
    """`trust_required` must survive the client, or the confirm strip cannot exist."""
    api = _read(JS / "context" / "agent-templates-api.js")
    assert "apiFetch('/api/agent-templates'" in api
    assert "apiFetch(`/api/agent-templates/${encodeURIComponent(id)}`" in api
    assert "error.code = (detail && detail.code)" in api
    # Installing never patches a live hire: the route only rejects `agent_id`
    # because it declares it, and the client strips it rather than trusting that.
    assert "delete payload.agent_id" in api
    state = _read(JS / "marketplace" / "marketplace.js")
    assert "err.code === 'trust_required'" in state
    assert "confirm: true" in state


def test_remote_data_never_reaches_a_markup_string_path() -> None:
    """Titles, missions, sections and author names are catalog data. h() or nothing."""
    for path in MARKETPLACE_MODULES:
        source = _read(path)
        assert "innerHTML" not in source, path.name
        assert "insertAdjacentHTML" not in source, path.name
    for name in ("marketplace-view.js", "marketplace-detail.js"):
        assert "BossModDom" in _read(JS / "marketplace" / name), name
    # The one remote value that reaches an href keeps its escape hatch shut.
    detail = _read(JS / "marketplace" / "marketplace-detail.js")
    assert "href: author.url," in detail
    assert "rel: 'noopener noreferrer'," in detail


def test_every_marketplace_module_stays_a_module() -> None:
    """Six files, one concern each, and none of them near the 400-line cap.

    The redesign did not fit in two. The split is at real seams rather than at
    a line count: what the app says about packs it will not offer calls only
    dom.js, the pure catalog-and-library projection calls nothing, the detail
    view owns one pack's read, the browse view owns the grid and the swap
    between the two, and the state module owns the three API calls.

    marketplace-withheld.js is the fifth and it is a seam, not a spill: the
    notice under the grid and the warning on an installed row whose catalog
    entry has gone bad are the same subject and share their wording.

    marketplace-sections.js is the sixth, and the seam is the same kind. The
    detail view owns the CHROME around a pack — the back bar, the hero, the
    byline, the install it offers — and the tab list owns the pack's own text
    and the one interaction on this whole surface that is not a click handed
    straight back to the state module. Keyboard, hover intent and a roving
    tabindex inside the file that also builds a byline would be two subjects in
    one place, and it is what pushed the detail view over its budget.
    """
    sizes = {
        path.name: len(_read(path).splitlines())
        for path in sorted((JS / "marketplace").glob("*.js"))
    }
    assert set(sizes) == {
        "marketplace.js", "marketplace-items.js", "marketplace-withheld.js",
        "marketplace-sections.js", "marketplace-detail.js", "marketplace-view.js",
    }, sizes
    assert all(size < 400 for size in sizes.values()), sizes
    # The projection is pure: no DOM, no fetch, no state mutation.
    items = _code(JS / "marketplace" / "marketplace-items.js")
    for banned in ("BossModDom", "document", "apiFetch", "await "):
        assert banned not in items, banned
    # And no cycle, in either seam: the browse view calls the detail one and the
    # detail one calls the reader, never the reverse.
    assert "BossModMarketplaceDetail" in _code(JS / "marketplace" / "marketplace-view.js")
    assert "BossModMarketplaceView" not in _code(JS / "marketplace" / "marketplace-detail.js")
    reader = _code(JS / "marketplace" / "marketplace-sections.js")
    assert "BossModMarketplaceSections" in _code(JS / "marketplace" / "marketplace-detail.js")
    assert "BossModMarketplaceDetail" not in reader
    assert "BossModMarketplaceView" not in reader


def test_the_takeover_has_a_height_not_only_a_ceiling() -> None:
    """It popped cropped and then grew: `max-height` alone is a shrink-wrap.

    While the catalog fetched, the panel was as tall as the single "Loading…"
    line inside it, then jumped to full height when the cards landed. Loading,
    ready, empty and failed must all occupy the same box, and that is CSS's
    answer to give — overlays.js says geometry is never measured in JS.
    """
    block = _read(OVERLAYS).split('.modal-panel[data-size="takeover"] {', 1)[1]
    block = block.split("}", 1)[0]
    # Anchored to the start of its own line. `height:` is a SUBSTRING of
    # `max-height:`, so a bare `in` here passed with the height deleted — the
    # exact bug this test exists to catch, sitting inside the test itself.
    assert "\n  height: calc(100vh - 48px);" in block
    assert "\n  max-height: calc(100vh - 48px);" in block
    assert "\n  width: min(1180px, calc(100vw - 48px));" in block


def test_the_marketplace_owns_its_own_stylesheet() -> None:
    """319 lines of browsing surface inside the file that owns overlay primitives.

    overlays.css defines the modal, the menu, the popover and the toast. The
    marketplace merely RIDES one of them, so it is its own sheet — linked after
    overlays.css, because every rule in it lives inside
    `.modal-panel[data-size="takeover"]` and has to be able to override what it
    sits on. The add-agent picker stays where it was: it belongs to the dialog.
    """
    overlays = _read(OVERLAYS)
    assert "─── Marketplace ───" not in overlays
    assert ".market-card" not in overlays
    assert ".market-detail" not in overlays
    # The two blocks that genuinely belong to the dialog stay put.
    assert "─── Add agent: the two steps ───" in overlays
    assert "─── Add agent: step 2, from a template ───" in overlays
    html = _read(HTML)
    assert "static_url('css/marketplace.css')" in html
    assert html.index("css/overlays.css") < html.index("css/marketplace.css")


def test_marketplace_styles_are_tokens_only() -> None:
    """No hex, no rgba, no magic colour in the sheet this surface owns."""
    css = _read(CSS)
    assert "─── Marketplace ───" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "hardcoded hex in .market-*"
    assert "rgba(" not in css
    # Text on a tint uses that tint's ink pair, never the base colour.
    assert "background: var(--ok-bg); color: var(--ok-ink);" in css
    assert "background: var(--amber); color: var(--amber-ink);" in css
    assert "color: var(--alert-ink);" in css
    assert "color: var(--blue-ink);" in css
    # Selection is the app's tint, not the heavy edge it used to paint.
    assert "box-shadow: inset" not in css
    selected = css.split('.market-card[aria-current="true"] {', 1)[1].split("}", 1)[0]
    assert "background: var(--accent-bg);" in selected
    assert "color: var(--blue-ink); }" in css.split(
        '.market-card[aria-current="true"] .market-card-author', 1)[1]
    # Two columns now, not three: the detail is a view, not a gutter.
    assert "grid-template-columns: 180px minmax(0, 1fr);" in css
    assert "320px" not in css
    assert 'data-detail="open"' not in css, "the pushed view is retired"
    # Each view owns exactly one scroller, and both stick at an exact 0.
    assert ".market-rail { position: sticky; top: 0; }" in css
    body = css.split(".market-body {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in body and "min-height: 0;" in body
    detail = css.split(".market-detail {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in detail and "min-height: 0;" in detail
    # The header is pinned by the column it sits in, not by sticky + z-index.
    head = css.split(".market-head {", 1)[1].split("}", 1)[0]
    assert "position: sticky" not in head and "z-index" not in head


def test_the_takeover_body_carries_the_apps_ink() -> None:
    """.modal-body is muted for a confirm dialog. A browsing surface is not one."""
    css = _read(OVERLAYS)
    assert (
        '.modal-panel[data-size="wide"] .modal-body,\n'
        '.modal-panel[data-size="takeover"] .modal-body { color: var(--ink); }'
    ) in css
    # And the takeover's body is the column whose CHILD scrolls, which is what
    # makes both views' `top: 0` exact rather than measured.
    assert (
        '.modal-panel[data-size="takeover"] .modal-body {\n'
        "  display: flex;\n"
        "  flex-direction: column;\n"
        "  overflow: hidden;\n"
        "}"
    ) in css
    assert "top: 58px" not in css


def test_index_loads_the_six_modules_after_what_they_call() -> None:
    """A module that loads before its dependency is an app that is dead on boot."""
    scripts = _scripts()
    order = {name: index for index, name in enumerate(scripts)}
    for name in (*INDEX_MODULES, "js/context/agent-templates-api.js"):
        assert name in order, f"index.html does not load {name}"
    # The six, in the one order that has no forward reference in it.
    assert [order[name] for name in INDEX_MODULES] == sorted(
        order[name] for name in INDEX_MODULES
    ), "the marketplace modules are out of dependency order"
    # Both views read the withheld wording at IIFE time, so it is first — and
    # the detail view binds the section reader at IIFE time for the same reason.
    for first in (
        "js/marketplace/marketplace-withheld.js", "js/marketplace/marketplace-sections.js",
    ):
        assert order[first] < order["js/marketplace/marketplace-detail.js"], first
    for dependency in (
        "js/core/dom.js", "js/core/avatar.js", "js/core/overlays.js",
        "js/context/agent-api.js", "js/context/agent-templates-api.js",
    ):
        assert order[dependency] < order["js/marketplace/marketplace-detail.js"], dependency
    # agent-edit.js opens the takeover in step 4, so it must load after it.
    assert order["js/marketplace/marketplace.js"] < order["js/context/agent-edit.js"]


def test_the_rail_groups_scopes_apart_from_the_catalogs_categories() -> None:
    """`All 5 / Installed 0 / Engineering 3 / Product 2` read as four buckets.

    Two of those rows are SCOPES — which packs are on the table — and the rest
    are the catalog's own categories. Run together in one flat list they said
    the catalog had four sections, one of which was the operator's library, and
    nothing on screen distinguished a question about ownership from a question
    about subject.

    The split is real STRUCTURE and not a gap. Each group is its own list under
    its own heading, and the list is named by that heading, so a screen reader
    announces which group a row belongs to; the treatment is the roster rail's
    `PEOPLE` / `THREADS`, down to the type, rather than a second vocabulary
    invented for this one surface. Every row stays a plain button, so the rail
    is still as many tab stops as it has rows and Tab crosses both groups.

    The rows stay numbered across the WHOLE rail rather than per group: the id
    is what render() hands focus back to after the click that destroyed the
    button, and a per-group number would name two different rows.

    `Show`, not `Scopes`: this same takeover reads a pack's `In scope` and
    `Out of scope` one view away, and a word that means two things on one
    surface means neither.
    """
    payload = _harness()
    for key in (
        "railGroupsScopesApartFromCategories", "railKeyboardCrossesBothGroups",
        "railCounts", "railIsAList",
        # A heading over no rows claims a group the catalog does not have.
        "aFailedReadDrawsNoEmptyCategoryGroup",
        # And the regrouping did not cost the rail its focus handling.
        "railClickKeepsFocus", "railReturnsToAllWithFocus",
    ):
        assert payload[key] is True, key
    view = _read(JS / "marketplace" / "marketplace-view.js")
    assert "function railGroup(id, title, rows, from, state, handlers)" in view
    assert "h('h3', { class: 'market-rail-title', id }, title)," in view
    assert "h('ul', { class: 'market-rail-list', 'aria-labelledby': id }," in view
    assert "railRow(row, from + at, state, handlers)" in view
    assert "scopeGroup: 'Show'," in view
    assert "categoryGroup: 'Categories'," in view
    # Drawn only when there is something to put under it.
    assert "categories.length" in view.split("function rail(state, handlers)", 1)[1]
    # The heading is the roster rail's, character for character on every
    # property that makes it read as one: a second small-caps treatment on one
    # screen is two conventions for one idea.
    title = _read(CSS).split(".market-rail-title {", 1)[1].split("}", 1)[0]
    roster = _read(SHELL_CSS).split(".roster-section-title {", 1)[1].split("}", 1)[0]
    for rule in (
        "font-size: 11px;", "font-weight: 600;", "letter-spacing: 0.06em;",
        "text-transform: uppercase;", "color: var(--hint);",
    ):
        assert rule in title, rule
        assert rule in roster, f"shell.css changed under this test: {rule}"


def test_the_section_panel_hugs_its_content_and_cannot_move_the_list() -> None:
    """A one-line section rendered as a full-height grey slab.

    `align-items: stretch` gave the panel the height of the grid row whatever
    was in it, so `Handoff: the operator gets a written verdict.` was one line
    of text at the top of an empty box the height of the view. It sizes to its
    section now and carries its own ceiling instead of borrowing the row's.

    The stretch was not arbitrary, though, and the reason it was there is the
    thing this test pins. The pointer chooses from the LEFT column, so nothing
    the right one does may move it:

    * `align-items: start` plus the list's own `align-self: start` puts both
      columns at the top of the row, so the panel growing or shrinking cannot
      move a tab out from under the pointer resting on it.
    * The ROW keeps `flex: 1 1 auto`, so it absorbs the detail column's slack
      rather than collapsing onto its content. `.market-detail`'s scroll height
      therefore does not change with the section either — a view scrolled to
      its end cannot be clamped upward under a resting pointer, which is the
      one way a hugging panel could still have moved something.
    * The panel's `max-height` keeps it inside that slack on a short window, so
      the row has slack to absorb.

    The floor the row used to carry goes with the stretch: a `min-height` under
    a hugging panel is the empty slab again, drawn under the box instead of
    inside it.
    """
    css = _read(CSS)
    read = css.split(".market-detail-read {", 1)[1].split("}", 1)[0]
    assert "align-items: start;" in read
    assert "align-items: stretch" not in read
    assert "flex: 1 1 auto;" in read
    assert "min-height:" not in read, "a floor under the row is the slab again"
    listing = css.split(".market-section-list {", 1)[1].split("}", 1)[0]
    assert "align-self: start;" in listing
    panel = css.split(".market-section-panel {", 1)[1].split("}", 1)[0]
    assert "max-height: 52vh;" in panel, "a ceiling, so it scrolls only when it must"
    assert "overflow-y: auto;" in panel
    assert "height: 100%" not in panel
    # Narrow stacks the list above the panel; the floor is gone there too.
    narrow = css.split("@media (max-width: 700px) {", 1)[1]
    assert "min-height" not in narrow
    assert "grid-template-columns: minmax(0, 1fr);" in narrow
    # Geometry stays CSS's: nothing in the reader measures a box to decide one.
    reader = _code(JS / "marketplace" / "marketplace-sections.js")
    for banned in ("offsetHeight", "clientHeight", "getBoundingClientRect", "style."):
        assert banned not in reader, banned


def test_the_section_panel_is_set_as_prose_and_not_as_chrome() -> None:
    """13px, the size of the list beside it, run across an 1180px takeover.

    The panel is the one surface on this screen that is READ rather than
    scanned — a handoff or a done bar runs to hundreds of words — and it was
    set like UI chrome and measured like nothing at all. Three changes, one
    idea: a step above the 13px around it and at the app's own reading size
    (conversation.css sets a message body at base.css's 14px, and that is the
    closest existing "read this" surface), a line-height loose enough for a
    paragraph rather than for a label, and a measure that ends the line before
    the eye has to hunt for the start of the next one.

    tokens.css carries no type scale, so the numbers are the app's existing
    prose rather than invented: the sheet's own colours are the only thing a
    token could have supplied here, and every one of them already is one.
    """
    css = _read(CSS)
    text = css.split(".market-detail-text {", 1)[1].split("}", 1)[0]
    assert "font-size: 14px;" in text
    assert "line-height: 1.7;" in text
    assert "max-width: 62ch;" in text
    assert "color: var(--ink);" in text
    # Larger than the UI it sits beside, which is what "body prose" means here.
    name = css.split(".market-section-name {", 1)[1].split("}", 1)[0]
    assert "font-size: 13px;" in name
    # And the app's reading size is where the number came from.
    base = _read(ROOT / "ui" / "static" / "css" / "base.css")
    assert "font-size: 14px;" in base, "base.css changed under this test"
    # The panel got the padding of a reading box rather than of a chrome pane.
    panel = css.split(".market-section-panel {", 1)[1].split("}", 1)[0]
    assert "padding: 18px 20px;" in panel


def test_the_section_list_names_stop_shouting_at_their_own_subtitles() -> None:
    """Six two-line rows at weight 600 in 7px of padding, over an 11px caption.

    The names were heavier than the pack text they pick from and the rows were
    packed tight enough to read as a block rather than as six choices. The
    hierarchy is carried by ink and size, the way the rest of this sheet
    carries it, so the weight steps down and the subtitle steps up: two lines a
    step apart in size and a step apart in ink already read as a name over a
    description.

    Every pair stays at AA, and the subtitle is the one that has to be watched
    because it sits on three different grounds. --hint clears only the panel
    (4.83, then 3.94 on --line, then 4.19 on --accent-bg), so the line is
    --muted off the tint — 6.54 / 5.33 — and re-inked to --blue-ink on the
    selected row, where it measures 6.86.
    """
    css = _read(CSS)
    name = css.split(".market-section-name {", 1)[1].split("}", 1)[0]
    assert "font-weight: 500;" in name
    assert "font-weight: 600" not in name
    sub = css.split(".market-section-sub {", 1)[1].split("}", 1)[0]
    assert "font-size: 12px;" in sub
    assert "color: var(--muted);" in sub
    assert "var(--hint)" not in sub
    tab = css.split(".market-section-tab {", 1)[1].split("}", 1)[0]
    assert "padding: 10px 12px;" in tab
    assert "gap: 3px;" in tab
    assert (
        ".market-section-list { display: flex; flex-direction: column; "
        "gap: 4px; align-self: start; }"
    ) in css
    # The selected row re-inks BOTH lines; --hint would fail on that tint and
    # --muted is not the pair for it either.
    assert (
        '.market-section-tab[aria-selected="true"] .market-section-sub '
        "{ color: var(--blue-ink); }"
    ) in css
    selected = css.split('.market-section-tab[aria-selected="true"] {', 1)[1].split("}", 1)[0]
    assert "color: var(--blue-ink);" in selected
    # And the row is still a 24x24 target with the list still one tab stop.
    assert "min-height: 24px;" in tab
