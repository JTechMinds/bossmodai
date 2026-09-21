/**
 * Node harness: the shared search field and the menu-select dropdown.
 *
 * Invoked by tests/test_ui_toolbar_controls.py. Not a browser bundle.
 *
 * Both are claims about built nodes — a glyph inside the box, a dropdown that
 * is the shared menu panel, a trigger that names what is chosen — so this
 * builds the real controls and reads the shape and the behaviour back off
 * them. Esc and the press-outside dismiss belong to createMenu and are proved
 * in tests/js_overlays_harness.cjs; they are not re-proved here.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

installDom();
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModOverlayFocus", "BossModOverlays",
    "BossModSearchField", "BossModMenuSelect",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

function threw(build) {
    try {
        build();
        return false;
    } catch (err) {
        return true;
    }
}

function fire(el, type) {
    (el.listeners[type] || []).forEach((fn) => fn({ target: el }));
}

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

async function main() {
    // ── Search field ────────────────────────────────────────────────────
    let typed = 0;
    const field = global.BossModSearchField.create({
        placeholder: "Search all tasks",
        label: "Search tasks by title",
        className: "tasks-search",
        onInput: () => { typed += 1; },
    });
    const box = field.element;
    const glyph = box.children[0];
    // The glyph and the input share ONE box — the box is what carries the
    // border and the focus ring — and a click on the glyph reaches the input
    // because the box is a <label>.
    const searchIsOneBox = box.tagName === "LABEL"
        && box.getAttribute("class") === "search-field tasks-search"
        && box.children.length === 2
        && glyph.tagName === "I"
        && glyph.getAttribute("data-lucide") === "search"
        && glyph.getAttribute("aria-hidden") === "true"
        && box.children[1] === field.input;
    if (!searchIsOneBox) fail("the search field is not a labelled box holding a glyph and its input");

    const searchInputIsNamed = field.input.getAttribute("type") === "search"
        && field.input.getAttribute("aria-label") === "Search tasks by title"
        && field.input.getAttribute("placeholder") === "Search all tasks";
    if (!searchInputIsNamed) fail("the search input lost its type, name or placeholder");

    fire(field.input, "input");
    const searchReportsInput = typed === 1;
    if (!searchReportsInput) fail("typing did not reach onInput");

    const searchNeedsItsParts =
        threw(() => global.BossModSearchField.create({ label: "x", onInput() {} }))
        && threw(() => global.BossModSearchField.create({ placeholder: "x", onInput() {} }))
        && threw(() => global.BossModSearchField.create({ placeholder: "x", label: "x" }));

    // ── Menu select ─────────────────────────────────────────────────────
    const host = document.createElement("div");
    document.body.append(host);
    const changes = [];
    const select = global.BossModMenuSelect.create({
        label: "Filter by assignee",
        options: [
            { value: "", label: "Everyone" },
            { value: "a1", label: "Jim", avatar: { name: "Jim", color: "#d97706" } },
        ],
        onChange: (value) => { changes.push(value); },
    });
    host.append(select.element);
    const trigger = select.element.querySelector(".menu-select-trigger");
    const panelOf = () => select.element.querySelector(".menu");

    const triggerNamesTheChoice = Boolean(trigger)
        && trigger.getAttribute("aria-label") === "Filter by assignee: Everyone"
        && trigger.textContent.includes("Everyone")
        && trigger.getAttribute("aria-haspopup") === "dialog"
        && trigger.getAttribute("aria-expanded") === "false"
        && select.getValue() === "";
    if (!triggerNamesTheChoice) fail("the trigger does not name the current choice");

    await trigger.dispatchClick();
    const panel = panelOf();
    const rows = panel ? panel.querySelectorAll(".menu-select-option") : [];
    // The same panel and rows the chat's agent-name menu uses.
    const opensTheSharedMenu = Boolean(panel)
        && panel.getAttribute("data-menu") === "select"
        && rows.length === 2
        && rows.every((row) => row.getAttribute("class").split(" ").includes("menu-action"))
        && trigger.getAttribute("aria-expanded") === "true";
    if (!opensTheSharedMenu) fail(`the dropdown did not open the shared menu: ${rows.length} rows`);

    const opensOnTheChoice = document.activeElement === rows[0]
        && rows[0].getAttribute("aria-pressed") === "true"
        && rows[1].getAttribute("aria-pressed") === "false"
        && Boolean(rows[0].querySelector('[data-lucide="check"]'))
        && !rows[1].querySelector('[data-lucide="check"]');
    if (!opensOnTheChoice) fail("the open list does not start on, and mark, the current choice");

    const rowsCarryAvatars = Boolean(rows[1].querySelector(".avatar"))
        && !rows[0].querySelector(".avatar");
    if (!rowsCarryAvatars) fail("a person's row lost its avatar, or a plain row grew one");

    await rows[1].dispatchClick();
    const pickReportsAndCloses = changes.join(",") === "a1"
        && select.getValue() === "a1"
        && trigger.getAttribute("aria-label") === "Filter by assignee: Jim"
        && !panelOf()
        && trigger.getAttribute("aria-expanded") === "false"
        && document.activeElement === trigger;
    if (!pickReportsAndCloses) fail(`picking Jim reported [${changes}] and left value "${select.getValue()}"`);

    await trigger.dispatchClick();
    const reopened = panelOf().querySelectorAll(".menu-select-option");
    // Jim is now the SECOND row. createMenu focuses the first stop on its own,
    // so only a chosen row that is not first proves the list opens on it.
    const reopensOnTheChoice = document.activeElement === reopened[1]
        && reopened[1].getAttribute("aria-pressed") === "true"
        && reopened[0].getAttribute("aria-pressed") === "false";
    if (!reopensOnTheChoice) fail("reopening did not start on the current choice");
    await reopened[1].dispatchClick();
    const samePickIsSilent = changes.length === 1 && !panelOf();
    if (!samePickIsSilent) fail("picking the current choice again reported a change");

    await trigger.dispatchClick();
    await trigger.dispatchClick();
    const triggerToggles = !panelOf() && trigger.getAttribute("aria-expanded") === "false";
    if (!triggerToggles) fail("a second click on the trigger did not close the list");

    select.setOptions([
        { value: "", label: "Everyone" }, { value: "a1", label: "Jim" }, { value: "a2", label: "Debra" },
    ], "a2");
    const setOptionsMovesTheChoice = select.getValue() === "a2"
        && trigger.getAttribute("aria-label") === "Filter by assignee: Debra"
        && changes.length === 1;
    if (!setOptionsMovesTheChoice) fail("setOptions did not move the choice, or reported it as a pick");

    // A choice the options do not hold is refused, and refusing it changes
    // nothing: a trigger naming one filter while another is applied is the
    // failure this control exists to prevent.
    const unknownChoiceIsRefused =
        threw(() => select.setOptions([{ value: "", label: "Everyone" }], "ghost"))
        && select.getValue() === "a2"
        && trigger.getAttribute("aria-label") === "Filter by assignee: Debra"
        && threw(() => global.BossModMenuSelect.create({
            label: "x", options: [{ value: "", label: "All" }], value: "nope", onChange() {},
        }));
    if (!unknownChoiceIsRefused) fail("an unknown choice was accepted, or refusing it changed the control");

    const badOptionsThrow =
        threw(() => global.BossModMenuSelect.create({ label: "x", options: [], onChange() {} }))
        && threw(() => global.BossModMenuSelect.create({
            label: "x", options: [{ value: "a", label: "A" }, { value: "a", label: "B" }], onChange() {},
        }))
        && threw(() => global.BossModMenuSelect.create({
            label: "x", options: [{ value: "a" }], onChange() {},
        }))
        && threw(() => global.BossModMenuSelect.create({ options: [{ value: "", label: "All" }], onChange() {} }))
        && threw(() => global.BossModMenuSelect.create({ label: "x", options: [{ value: "", label: "All" }] }));

    process.stdout.write(JSON.stringify({
        ok: true,
        searchIsOneBox,
        searchInputIsNamed,
        searchReportsInput,
        searchNeedsItsParts,
        triggerNamesTheChoice,
        opensTheSharedMenu,
        opensOnTheChoice,
        rowsCarryAvatars,
        pickReportsAndCloses,
        reopensOnTheChoice,
        samePickIsSilent,
        triggerToggles,
        setOptionsMovesTheChoice,
        unknownChoiceIsRefused,
        badOptionsThrow,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
