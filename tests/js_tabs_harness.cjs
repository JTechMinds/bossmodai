/**
 * Node harness: core/tabs.js — the quiet tab group the Office header and the
 * Agents dialog share.
 *
 * Invoked by tests/test_ui_tabs.py. Not a browser bundle.
 *
 * Its own file rather than a section of another harness: every existing one
 * pins an exact module list and an exact verdict, and a shared control has no
 * business changing either of those for a surface it is not part of. The DOM
 * it runs against is the shared fake (tests/js_fake_dom.cjs), not a copy.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();

const paths = process.argv.slice(2);
const NAMES = ["BossModDom", "BossModTabs"];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const TABS = [
    { id: "one", label: "One", panelId: "panel-one" },
    { id: "two", label: "Two", panelId: "panel-two" },
    { id: "three", label: "Three", panelId: "panel-three" },
];

function threw(build, fragment) {
    try {
        build();
        return false;
    } catch (err) {
        return String((err && err.message) || err).includes(fragment);
    }
}

/** Fire a keydown the way a browser would, and report whether it was claimed. */
async function key(el, name) {
    let prevented = false;
    const event = { key: name, target: el, preventDefault() { prevented = true; } };
    for (const fn of [...(el.listeners.keydown || [])]) await fn(event);
    return prevented;
}

/** Selected id, and the tab that is the group's one tab stop, as the DOM says. */
function state(group) {
    const buttons = group.element.querySelectorAll(".tab");
    const selected = buttons.filter((b) => b.getAttribute("aria-selected") === "true");
    const stops = buttons.filter((b) => b.getAttribute("tabindex") === "0");
    return {
        selected: selected.map((b) => b.id).join("|"),
        stop: stops.map((b) => b.id).join("|"),
    };
}

async function main() {
    const verdict = {};
    const heard = [];
    const group = global.BossModTabs.create({
        label: "Test view",
        idPrefix: "test-tab",
        tabs: TABS,
        selected: "two",
        onSelect: (id) => { heard.push(id); },
    });
    documentStub.body.append(group.element);
    const tab = (id) => group.element.querySelector(`#test-tab-${id}`);

    // ── The ARIA contract, and one tab stop for the whole group.
    const buttons = group.element.querySelectorAll(".tab");
    verdict.theGroupIsANamedTablist = group.element.tagName === "DIV"
        && group.element.getAttribute("class") === "tabs"
        && group.element.getAttribute("role") === "tablist"
        && group.element.getAttribute("aria-label") === "Test view";
    verdict.everyTabIsAButtonThatNamesItsPanel = buttons.length === 3
        && buttons.every((b, at) => b.tagName === "BUTTON"
            && b.getAttribute("type") === "button"
            && b.getAttribute("role") === "tab"
            && b.id === `test-tab-${TABS[at].id}`
            && b.getAttribute("aria-controls") === TABS[at].panelId
            && b.textContent === TABS[at].label);
    const opened = state(group);
    verdict.oneSelectedAndOneTabStop = opened.selected === "test-tab-two"
        && opened.stop === "test-tab-two"
        && tab("one").getAttribute("aria-selected") === "false"
        && tab("one").getAttribute("tabindex") === "-1"
        && group.selected() === "two"
        // Building the group is not the operator choosing anything.
        && heard.length === 0;

    // ── A click selects and reports; a click on the tab already up does not.
    await tab("three").dispatchClick();
    verdict.aClickSelectsAndReports = state(group).selected === "test-tab-three"
        && state(group).stop === "test-tab-three"
        && group.selected() === "three"
        && heard.join("|") === "three";
    await tab("three").dispatchClick();
    verdict.clickingTheSelectedTabIsSilent = heard.join("|") === "three"
        && group.selected() === "three";

    // ── The arrows wrap at both ends, and take the selection AND the keyboard.
    heard.length = 0;
    const rightClaimed = await key(tab("three"), "ArrowRight");
    const wrappedRight = group.selected() === "one"
        && documentStub.activeElement === tab("one")
        && state(group).stop === "test-tab-one";
    const leftClaimed = await key(tab("one"), "ArrowLeft");
    const wrappedLeft = group.selected() === "three"
        && documentStub.activeElement === tab("three");
    verdict.theArrowsWrapAndMoveTheKeyboard = rightClaimed && leftClaimed
        && wrappedRight && wrappedLeft
        && heard.join("|") === "one|three";
    await key(tab("three"), "ArrowLeft");
    verdict.anArrowStepsOneTab = group.selected() === "two"
        && documentStub.activeElement === tab("two");

    // ── Home and End go to the ends.
    heard.length = 0;
    const homeClaimed = await key(tab("two"), "Home");
    const home = group.selected() === "one" && documentStub.activeElement === tab("one");
    const endClaimed = await key(tab("one"), "End");
    const end = group.selected() === "three" && documentStub.activeElement === tab("three");
    verdict.homeAndEndGoToTheEnds = homeClaimed && endClaimed && home && end
        && heard.join("|") === "one|three";

    // ── A key that is not the group's is left alone, not swallowed.
    heard.length = 0;
    const tabClaimed = await key(tab("three"), "Tab");
    verdict.otherKeysAreLeftAlone = !tabClaimed && heard.length === 0
        && group.selected() === "three";

    // ── select() moves the selection and never calls back.
    heard.length = 0;
    group.select("one");
    verdict.selectMovesTheSelectionSilently = state(group).selected === "test-tab-one"
        && state(group).stop === "test-tab-one"
        && group.selected() === "one"
        && heard.length === 0;
    tab("three").focus();
    group.focus();
    verdict.focusLandsOnTheSelectedTab = documentStub.activeElement === tab("one");

    // ── Every input the builder cannot work without is named when missing.
    const good = { label: "L", idPrefix: "p", tabs: TABS, selected: "one", onSelect() {} };
    const without = (overrides) => () => global.BossModTabs.create({ ...good, ...overrides });
    verdict.theConstructorRefusesWhatItCannotBuild =
        threw(without({ label: "" }), "deps.label")
        && threw(without({ idPrefix: "" }), "deps.idPrefix")
        && threw(without({ tabs: [] }), "deps.tabs")
        && threw(without({ tabs: undefined }), "deps.tabs")
        && threw(without({ tabs: [TABS[0], { ...TABS[0], label: "Again" }] }), "same id twice")
        && threw(without({ selected: "four" }), "deps.selected")
        && threw(without({ onSelect: "nope" }), "deps.onSelect")
        && threw(without({ tabs: [{ ...TABS[0], icon: "" }] }), "icon with no name")
        && threw(without({ tabs: [{ ...TABS[0], icon: 7 }] }), "icon with no name")
        && threw(() => global.BossModTabs.create(), "deps.label");

    // ── A tab MAY lead with an icon: an unpainted lucide placeholder (the
    //    caller paints), decorative, then the label as the tab's only text.
    //    A tab without one is the bare label it always was.
    const marked = global.BossModTabs.create({
        label: "Marked", idPrefix: "marked", selected: "a", onSelect() {},
        tabs: [
            { id: "a", label: "Add agent", panelId: "pa", icon: "plus" },
            { id: "b", label: "Plain", panelId: "pb" },
        ],
    });
    const [withIcon, withoutIcon] = marked.element.querySelectorAll(".tab");
    const mark = withIcon.children[0];
    verdict.aTabMayLeadWithAnIcon = withIcon.children.length === 2
        && mark.tagName === "I"
        && mark.getAttribute("data-lucide") === "plus"
        && mark.getAttribute("aria-hidden") === "true"
        && withIcon.children[1].tagName === "SPAN"
        && withIcon.textContent === "Add agent"
        // No mark and no wrapper: the label is the button's text, as the
        // Office's tabs always were. (The fake DOM keeps a text node in
        // `children`, so this asks for elements by name.)
        && withoutIcon.querySelector("i") === null
        && withoutIcon.querySelector("span") === null
        && withoutIcon.textContent === "Plain";
    verdict.selectRefusesAnUnknownTab = threw(() => group.select("four"), "no tab \"four\"")
        // ...and a refused select changed nothing.
        && group.selected() === "one";

    process.stdout.write(JSON.stringify(Object.assign({ ok: true }, verdict)));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
