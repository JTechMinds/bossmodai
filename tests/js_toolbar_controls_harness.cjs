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
    "BossModDom", "BossModAvatar", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu",
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

    // ── Menu select: the two trigger looks ─────────────────────────────
    // The default is the toolbar's button with the full label and no chip,
    // exactly as every existing caller builds it.
    const classes = (el) => String(el.getAttribute("class")).split(" ").filter(Boolean).sort().join(" ");
    const defaultTrigger = global.BossModMenuSelect.create({
        label: "Filter by assignee",
        options: [{ value: "a1", label: "Jim — Engineer", short: "Jim", avatar: { name: "Jim" } }],
        onChange() {},
    });
    const buttonTrigger = defaultTrigger.element.querySelector(".menu-select-trigger");
    const defaultVariantIsTheButton = classes(buttonTrigger) === "btn btn-sm menu-select-trigger"
        && defaultTrigger.element.getAttribute("class") === "menu-select"
        && buttonTrigger.textContent.includes("Jim — Engineer")
        && !buttonTrigger.querySelector(".avatar");
    if (!defaultVariantIsTheButton) fail(`the default trigger changed: "${buttonTrigger.getAttribute("class")}"`);

    // 'field' reads as an edit field: no .btn, the short name beside the
    // chosen person's chip, and the full label still in the accessible name.
    const fieldSelect = global.BossModMenuSelect.create({
        label: "Assignee",
        variant: "field",
        options: [
            { value: "", label: "Unassigned backlog", short: "Unassigned" },
            { value: "a1", label: "Charles — Build Engineer (matches)", short: "Charles",
                avatar: { name: "Charles", color: "#d97706" } },
        ],
        value: "a1",
        onChange() {},
    });
    const fieldTrigger = fieldSelect.element.querySelector(".menu-select-trigger");
    const fieldValue = fieldTrigger.querySelector(".menu-select-value");
    const chipBeforeText = fieldTrigger.children.indexOf(fieldTrigger.querySelector(".avatar"))
        < fieldTrigger.children.indexOf(fieldValue);
    const fieldShownAtFirst = classes(fieldTrigger) === "menu-select-field menu-select-trigger"
        && fieldSelect.element.getAttribute("class") === "menu-select menu-select--field"
        && fieldValue.textContent === "Charles"
        && fieldTrigger.querySelectorAll(".avatar").length === 1 && chipBeforeText
        && fieldTrigger.getAttribute("aria-label") === "Assignee: Charles — Build Engineer (matches)";
    // The trigger follows the choice: no chip for an option without one, and
    // one chip — never two — when a person is chosen again.
    fieldSelect.setOptions([
        { value: "", label: "Unassigned backlog", short: "Unassigned" },
        { value: "a1", label: "Charles — Build Engineer (matches)", short: "Charles", avatar: { name: "Charles" } },
    ], "");
    const fieldFollowsTheChoice = fieldValue.textContent === "Unassigned"
        && !fieldTrigger.querySelector(".avatar")
        && fieldTrigger.getAttribute("aria-label") === "Assignee: Unassigned backlog";
    await fieldTrigger.dispatchClick();
    const fieldRows = fieldSelect.element.querySelector(".menu").querySelectorAll(".menu-select-label")
        .map((node) => node.textContent);
    await fieldSelect.element.querySelector(".menu").querySelectorAll(".menu-select-option")[1].dispatchClick();
    const fieldVariantReadsAsAField = fieldShownAtFirst && fieldFollowsTheChoice
        && fieldRows.join("|") === "Unassigned backlog|Charles — Build Engineer (matches)"
        && fieldValue.textContent === "Charles" && fieldTrigger.querySelectorAll(".avatar").length === 1;
    if (!fieldVariantReadsAsAField) {
        fail(`the field trigger: first ${fieldShownAtFirst}, follows ${fieldFollowsTheChoice}, rows [${fieldRows}]`);
    }

    const badVariantAndShortThrow =
        threw(() => global.BossModMenuSelect.create({
            label: "x", variant: "pill", options: [{ value: "", label: "All" }], onChange() {},
        }))
        && threw(() => global.BossModMenuSelect.create({
            label: "x", options: [{ value: "", label: "All", short: "" }], onChange() {},
        }))
        && threw(() => global.BossModMenuSelect.create({
            label: "x", options: [{ value: "", label: "All", short: 3 }], onChange() {},
        }));
    if (!badVariantAndShortThrow) fail("an unknown variant or a bad short label was accepted");

    // ── Menu select: the form contract ─────────────────────────────────
    // With `name`, the control owns a hidden input carrying its value, so a
    // form reads it like any field; a pick fires one bubbling `change` on it
    // after it is written, and a write from code fires nothing.
    const TIERS = [
        { value: "never_allowed", label: "Never Allowed" },
        { value: "always_allowed", label: "Always Allowed" },
        { value: "approval_required", label: "Approval Required" },
    ];
    const log = [];
    const named = global.BossModMenuSelect.create({
        label: "Tier", name: "tier", id: "cli-rule-tier", variant: "field",
        options: TIERS, value: "always_allowed",
        onChange: (value) => { log.push(`onChange:${value}`); },
    });
    host.append(named.element);
    const namedTrigger = named.element.querySelector(".menu-select-trigger");
    const formValue = named.element.querySelector('input[name="tier"]');
    const changeEvents = [];
    formValue.addEventListener("change", (event) => {
        changeEvents.push(event);
        log.push(`change:${formValue.value}`);
    });
    const namedCarriesItsValue = Boolean(formValue)
        && formValue.getAttribute("type") === "hidden"
        && formValue.value === "always_allowed"
        && named.element.querySelectorAll("input").length === 1;
    if (!namedCarriesItsValue) fail("a named control does not carry its value in one hidden input");

    const idLandsOnTheTrigger = namedTrigger.id === "cli-rule-tier"
        && namedTrigger.getAttribute("aria-label") === "Tier: Always Allowed"
        && formValue.id !== "cli-rule-tier";
    if (!idLandsOnTheTrigger) fail("deps.id did not land on the trigger alone");

    // An operator's pick: open, click the row.
    const pickRow = async (control, label) => {
        await control.element.querySelector(".menu-select-trigger").dispatchClick();
        const row = control.element.querySelector(".menu").querySelectorAll(".menu-select-option")
            .find((node) => node.querySelector(".menu-select-label").textContent === label);
        await row.dispatchClick();
    };
    await pickRow(named, "Approval Required");
    const aPickWritesThenFiresOneBubblingChange = formValue.value === "approval_required"
        && named.getValue() === "approval_required"
        && changeEvents.length === 1
        && changeEvents[0].type === "change"
        && changeEvents[0].bubbles === true
        // Written before the event, and the event before onChange.
        && log.join("|") === "change:approval_required|onChange:approval_required";
    if (!aPickWritesThenFiresOneBubblingChange) fail(`a pick reported [${log}] with ${changeEvents.length} events`);
    await pickRow(named, "Approval Required");
    const aSamePickFiresNothing = changeEvents.length === 1 && log.length === 2;
    if (!aSamePickFiresNothing) fail("picking the current choice again fired a change");

    named.setValue("never_allowed");
    const setValueFollowsSilently = named.getValue() === "never_allowed"
        && formValue.value === "never_allowed"
        && namedTrigger.getAttribute("aria-label") === "Tier: Never Allowed"
        && namedTrigger.querySelector(".menu-select-value").textContent === "Never Allowed"
        && changeEvents.length === 1 && log.length === 2;
    if (!setValueFollowsSilently) fail("setValue did not move the input and trigger, or reported it");
    const setValueRefusesAStranger = threw(() => named.setValue("ghost"))
        && named.getValue() === "never_allowed" && formValue.value === "never_allowed";
    if (!setValueRefusesAStranger) fail("setValue accepted a value no option holds, or moved on refusing it");

    named.setOptions([{ value: "glob", label: "Glob" }, { value: "exact", label: "Exact" }], "exact");
    const setOptionsMovesTheInput = formValue.value === "exact"
        && named.getLabel() === "Exact" && changeEvents.length === 1 && log.length === 2;
    if (!setOptionsMovesTheInput) fail("setOptions did not move the hidden input, or reported it");

    const labelIsTheCurrentChoice = named.getLabel() === "Exact"
        && (named.setValue("glob"), named.getLabel() === "Glob");
    if (!labelIsTheCurrentChoice) fail("getLabel does not name the current choice");

    const copy = named.getOptions();
    copy[0].label = "Changed";
    copy.push({ value: "x", label: "X" });
    const optionsAreACopy = JSON.stringify(named.getOptions())
        === JSON.stringify([{ value: "glob", label: "Glob" }, { value: "exact", label: "Exact" }]);
    if (!optionsAreACopy) fail("getOptions handed out the control's own list");

    // onChange is optional only with a name: without either, a pick goes nowhere.
    const onChangeIsOptionalOnlyWithAName = !threw(() => global.BossModMenuSelect.create({
        label: "Desk", name: "desk", options: [{ value: "", label: "Unassigned" }],
    }))
        && threw(() => global.BossModMenuSelect.create({ label: "Desk", options: [{ value: "", label: "Unassigned" }] }))
        && threw(() => global.BossModMenuSelect.create({
            label: "Desk", name: "", options: [{ value: "", label: "Unassigned" }],
        }));
    if (!onChangeIsOptionalOnlyWithAName) fail("onChange is not optional exactly when a name is given");

    // Without a name there is no input, and nothing named is reported.
    const unnamedHasNoInput = select.element.querySelectorAll("input").length === 0;
    if (!unnamedHasNoInput) fail("a control without a name grew a form value");

    // A module holding only a node reaches the control through any of its three.
    const stray = document.createElement("div");
    const instanceForResolvesAndRefuses = global.BossModMenuSelect.instanceFor(named.element) === named
        && global.BossModMenuSelect.instanceFor(namedTrigger) === named
        && global.BossModMenuSelect.instanceFor(formValue) === named
        && global.BossModMenuSelect.instanceFor(select.element) === select
        && threw(() => global.BossModMenuSelect.instanceFor(stray))
        && threw(() => global.BossModMenuSelect.instanceFor(null));
    if (!instanceForResolvesAndRefuses) fail("instanceFor did not resolve a control's nodes, or resolved a stranger");

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
        defaultVariantIsTheButton,
        fieldVariantReadsAsAField,
        badVariantAndShortThrow,
        namedCarriesItsValue,
        idLandsOnTheTrigger,
        aPickWritesThenFiresOneBubblingChange,
        aSamePickFiresNothing,
        setValueFollowsSilently,
        setValueRefusesAStranger,
        setOptionsMovesTheInput,
        labelIsTheCurrentChoice,
        optionsAreACopy,
        onChangeIsOptionalOnlyWithAName,
        unnamedHasNoInput,
        instanceForResolvesAndRefuses,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
