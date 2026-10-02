/**
 * Node harness: the Edit-mode field widgets — core/time-field.js,
 * core/date-field.js and core/autogrow.js — and the formatter they share
 * (BossModFormat.formatTimeOfDay).
 *
 * Invoked by tests/test_ui_date_time_fields.py. Not a browser bundle.
 *
 * The pure halves (parseTime, monthGrid, formatDateLabel, formatTimeOfDay)
 * are checked case by case; the DOM halves are built for real against the
 * shared fake DOM and driven the way an operator drives them — typing and
 * blurring, opening the panel, pressing keys in the grid. core/menu.js binds
 * its Esc and press-outside listeners on `document`, which the shared fake
 * answers with a no-op, so this harness records them to press Esc for real.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

const documentStub = installDom();
installIconsStub();
const documentListeners = {};
documentStub.addEventListener = (type, fn) => { (documentListeners[type] ||= []).push(fn); };
documentStub.removeEventListener = (type, fn) => {
    documentListeners[type] = (documentListeners[type] || []).filter((item) => item !== fn);
};

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModFormat", "BossModOverlayFocus", "BossModMenu",
    "BossModAutoGrow", "BossModTimeField", "BossModDateField",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});
// Read off `global` under other names: a module-scope `const BossModDom`
// would be in its temporal dead zone while the evals above run.
const { BossModDom: Dom, BossModFormat: Format, BossModAutoGrow: AutoGrow,
    BossModTimeField: TimeField, BossModDateField: DateField } = global;

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

function threw(build) {
    try {
        build();
        return false;
    } catch (err) {
        return true;
    }
}

const key = (name) => ({ type: "keydown", key: name, preventDefault() {}, stopPropagation() {} });
const pressEscape = () => (documentListeners.keydown || []).slice()
    .forEach((fn) => fn({ key: "Escape", preventDefault() {}, stopPropagation() {} }));
const active = () => documentStub.activeElement;

// ── parseTime ──────────────────────────────────────────────────────────
const ACCEPTED = {
    "7": "07:00", "730": "07:30", "7:30": "07:30", "7.30": "07:30", "7:30p": "19:30",
    "7:30 pm": "19:30", "7:30 PM": "19:30", "19:30": "19:30", "0730": "07:30", "12am": "00:00",
    "12 pm": "12:00", " 9:05 am ": "09:05", "0": "00:00", "23:59": "23:59", "12:30a": "00:30",
};
const REJECTED = ["", "24", "7:60", "13pm", "0am", "7:3", "noon", "7:30:00", "7pmx", "-1", "19:30pm"];
const badParses = Object.entries(ACCEPTED)
    .filter(([text, want]) => TimeField.parseTime(text) !== want)
    .concat(REJECTED.filter((text) => TimeField.parseTime(text) !== null).map((text) => [text, null]));
const parseTimeReadsTheListedForms = badParses.length === 0;
if (!parseTimeReadsTheListedForms) fail(`parseTime misread: ${JSON.stringify(badParses)}`);

// ── formatTimeOfDay ────────────────────────────────────────────────────
const formatTimeOfDaySpellsTwelveHour = Format.formatTimeOfDay("07:30") === "7:30 AM"
    && Format.formatTimeOfDay("00:05") === "12:05 AM"
    && Format.formatTimeOfDay("12:00") === "12:00 PM"
    && Format.formatTimeOfDay("19:30") === "7:30 PM"
    && ["7:30", "24:00", "", "07:30:00"].every((bad) => threw(() => Format.formatTimeOfDay(bad)));
if (!formatTimeOfDaySpellsTwelveHour) fail("formatTimeOfDay must spell HH:MM as 12-hour time and throw on anything else");

// ── monthGrid ──────────────────────────────────────────────────────────
const localDay = (iso) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d);
};
const october = DateField.monthGrid(2026, 10);
const december = DateField.monthGrid(2026, 12);
const january = DateField.monthGrid(2027, 1);
const leapFebruary = DateField.monthGrid(2028, 2);
const plainFebruary = DateField.monthGrid(2026, 2);
const mondayFirst = (cells) => cells.length === 42
    && cells.every((cell, index) => localDay(cell.iso).getDay() === (index % 7 === 6 ? 0 : (index % 7) + 1));
const monthGridIsMondayFirstAndRollsOver = [october, december, january, leapFebruary, plainFebruary].every(mondayFirst)
    // Oct 1, 2026 is a Thursday: three September days lead the grid.
    && october[0].iso === "2026-09-28" && october[0].inMonth === false
    && october[3].iso === "2026-10-01" && october[3].day === 1 && october[3].inMonth === true
    && october[41].iso === "2026-11-08" && october.filter((cell) => cell.inMonth).length === 31
    && december[41].iso.startsWith("2027-01-") && december[41].inMonth === false
    && january[0].iso.startsWith("2026-12-") && january.some((cell) => cell.iso === "2027-01-31" && cell.inMonth)
    && leapFebruary.some((cell) => cell.iso === "2028-02-29" && cell.inMonth)
    && !plainFebruary.some((cell) => cell.iso === "2026-02-29")
    && plainFebruary.filter((cell) => cell.inMonth).length === 28
    && threw(() => DateField.monthGrid(2026, 13)) && threw(() => DateField.monthGrid(2026, 0));
if (!monthGridIsMondayFirstAndRollsOver) fail("monthGrid must be 42 Monday-first cells across month and year ends");

// ── formatDateLabel ────────────────────────────────────────────────────
const formatDateLabelSpellsTheDay = DateField.formatDateLabel("2026-10-01") === "Thu, Oct 1, 2026"
    && DateField.formatDateLabel("2028-02-29") === "Tue, Feb 29, 2028"
    && ["2026-02-30", "10/01/2026", "", "2026-13-01"].every((bad) => threw(() => DateField.formatDateLabel(bad)))
    && /^\d{4}-\d{2}-\d{2}$/.test(DateField.today());
if (!formatDateLabelSpellsTheDay) fail("formatDateLabel must read 'Thu, Oct 1, 2026' and throw on a non-day");

// ── The time field ─────────────────────────────────────────────────────
let timeChanges = 0;
const time = TimeField.create({ label: "Time of day", value: "", onChange: () => { timeChanges += 1; } });
documentStub.body.append(time.element);
const timeInput = time.element.querySelector(".time-field-input");
const clock = time.element.querySelector(".time-field-toggle");
const timeFieldIsATextFieldWithAClock = timeInput.tagName === "INPUT" && timeInput.getAttribute("type") === "text"
    && timeInput.getAttribute("aria-label") === "Time of day"
    && timeInput.classList.contains("edit-field")
    && clock.getAttribute("aria-label") === "Choose a time" && clock.getAttribute("aria-haspopup") === "dialog"
    && JSON.stringify(time.read()) === JSON.stringify({ ok: false, empty: true });
if (!timeFieldIsATextFieldWithAClock) fail("the time field must be a labelled text input with a clock button");

const typeInto = (input, text) => {
    input.value = text;
    input.dispatchEvent({ type: "input" });
    input.dispatchEvent({ type: "blur" });
};
typeInto(timeInput, "730p");
const blurFormatsTheTime = timeInput.value === "7:30 PM" && timeInput.getAttribute("aria-invalid") === null
    && JSON.stringify(time.read()) === JSON.stringify({ ok: true, value: "19:30" }) && timeChanges === 1;
if (!blurFormatsTheTime) fail(`a typed time must read back and show formatted: ${timeInput.value}`);
typeInto(timeInput, "half past");
const anInvalidTimeIsMarkedAndSaid = timeInput.value === "half past"
    && timeInput.getAttribute("aria-invalid") === "true"
    && time.read().ok === false && time.read().error === "“half past” is not a time (try 7:30 PM).";
if (!anInvalidTimeIsMarkedAndSaid) fail("unreadable text must be kept, marked aria-invalid and explained by read()");
const enterSettles = (() => {
    timeInput.value = "9";
    let prevented = false;
    timeInput.dispatchEvent({ type: "keydown", key: "Enter", preventDefault() { prevented = true; } });
    return prevented && timeInput.value === "9:00 AM" && timeInput.getAttribute("aria-invalid") === null;
})();
if (!enterSettles) fail("Enter must settle the typed time without submitting anything");

// A stored off-grid time is listed in order and marked; the list opens on it.
let offGridChanges = 0;
const offGrid = TimeField.create({ label: "Repeat from", value: "07:20", onChange: () => { offGridChanges += 1; } });
documentStub.body.append(offGrid.element);
const offGridToggle = offGrid.element.querySelector(".time-field-toggle");

(async () => {
    await offGridToggle.dispatchClick();
    const panel = offGrid.element.querySelector(".menu");
    const rows = panel ? panel.querySelectorAll(".time-field-option").map((row) => row.getAttribute("data-time")) : [];
    const pressed = panel ? panel.querySelectorAll(".time-field-option")
        .filter((row) => row.getAttribute("aria-pressed") === "true").map((row) => row.getAttribute("data-time")) : [];
    const anOffGridTimeIsListed = Boolean(panel) && panel.getAttribute("data-menu") === "time"
        && offGrid.element.querySelector(".time-field-input").value === "7:20 AM"
        && rows.length === 49 && rows.indexOf("07:20") === rows.indexOf("07:00") + 1
        && rows.indexOf("07:30") === rows.indexOf("07:20") + 1
        && JSON.stringify(pressed) === JSON.stringify(["07:20"])
        && active() && active().getAttribute("data-time") === "07:20"
        && offGridToggle.getAttribute("aria-expanded") === "true";
    if (!anOffGridTimeIsListed) fail(`an off-grid stored time must be listed, marked and focused: ${JSON.stringify(rows.slice(12, 18))}`);
    await panel.querySelectorAll(".time-field-option").find((row) => row.getAttribute("data-time") === "09:00").dispatchClick();
    const aSuggestionPickFiresOnChange = offGridChanges === 1
        && offGrid.element.querySelector(".time-field-input").value === "9:00 AM"
        && !offGrid.element.querySelector(".menu") && active() === offGridToggle
        && offGridToggle.getAttribute("aria-expanded") === "false"
        && JSON.stringify(offGrid.read()) === JSON.stringify({ ok: true, value: "09:00" });
    if (!aSuggestionPickFiresOnChange) fail("picking a suggestion must set the time, close the list and report it");
    const aBadStoredTimeThrows = threw(() => TimeField.create({ label: "x", value: "7:30", onChange() {} }))
        && threw(() => TimeField.create({ label: "x", value: "", onChange: null }));
    if (!aBadStoredTimeThrows) fail("a stored time the field cannot read must throw");

    // ── The date field ─────────────────────────────────────────────────
    let dateChanges = 0;
    const date = DateField.create({ label: "Starting", value: "2026-10-01", onChange: () => { dateChanges += 1; } });
    documentStub.body.append(date.element);
    const trigger = date.element.querySelector(".date-field-trigger");
    const dateTriggerNamesTheDay = trigger.textContent === "Thu, Oct 1, 2026"
        && trigger.getAttribute("aria-label") === "Starting: Thu, Oct 1, 2026"
        && trigger.getAttribute("aria-haspopup") === "dialog" && trigger.classList.contains("edit-field")
        && JSON.stringify(date.read()) === JSON.stringify({ ok: true, value: "2026-10-01" });
    if (!dateTriggerNamesTheDay) fail(`the date trigger must name the day: ${trigger.textContent}`);

    await trigger.dispatchClick();
    const dateMenu = date.element.querySelector(".menu");
    const grid = dateMenu && dateMenu.querySelector(".date-field-grid");
    const tabStops = () => grid.querySelectorAll(".date-field-day").filter((day) => day.getAttribute("tabindex") === "0");
    const focusedDay = () => (active() ? active().getAttribute("data-date") : null);
    const monthShown = () => dateMenu.querySelector(".date-field-month").textContent;
    const theDateMenuOpensOnTheDay = Boolean(dateMenu) && dateMenu.getAttribute("data-menu") === "date"
        && grid.getAttribute("role") === "grid" && monthShown() === "October 2026"
        && grid.querySelectorAll(".date-field-day").length === 42
        && focusedDay() === "2026-10-01" && tabStops().length === 1
        && grid.querySelectorAll("[aria-selected=\"true\"]").length === 1
        && trigger.getAttribute("aria-expanded") === "true";
    if (!theDateMenuOpensOnTheDay) fail("the date panel must open on the chosen day's month with one tab stop");

    const moves = [];
    for (const name of ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "End", "Home"]) {
        grid.dispatchEvent(key(name));
        moves.push(focusedDay());
    }
    const arrowKeysMoveTheDay = JSON.stringify(moves) === JSON.stringify([
        "2026-10-02", "2026-10-09", "2026-10-08", "2026-10-01", "2026-10-04", "2026-09-28",
    ]) && monthShown() === "September 2026" && tabStops().length === 1;
    if (!arrowKeysMoveTheDay) fail(`arrows, Home and End must move the day and cross months: ${JSON.stringify(moves)}`);

    grid.dispatchEvent(key("PageDown"));
    const pageDownMovesAMonth = focusedDay() === "2026-10-28" && monthShown() === "October 2026";
    grid.dispatchEvent(key("PageDown"));
    if (!pageDownMovesAMonth || focusedDay() !== "2026-11-28") fail(`PageDown must move a month: ${focusedDay()}`);
    grid.dispatchEvent(key("Enter"));
    const enterPicksTheDay = dateChanges === 1 && date.read().value === "2026-11-28"
        && trigger.textContent === "Sat, Nov 28, 2026" && !date.element.querySelector(".menu")
        && active() === trigger && trigger.getAttribute("aria-expanded") === "false";
    if (!enterPicksTheDay) fail("Enter must pick the focused day, relabel the trigger and close");

    await trigger.dispatchClick();
    const reopened = date.element.querySelector(".menu");
    reopened.querySelector(".date-field-grid").dispatchEvent(key("ArrowRight"));
    pressEscape();
    const escReturnsFocusToTheTrigger = !date.element.querySelector(".menu") && active() === trigger
        && dateChanges === 1 && date.read().value === "2026-11-28";
    if (!escReturnsFocusToTheTrigger) fail("Esc must close the date panel unchanged and return focus to the trigger");

    // The month clamps: Jan 31 a month on is Feb 28.
    const clamp = DateField.create({ label: "Starting", value: "2026-01-31", onChange() {} });
    documentStub.body.append(clamp.element);
    await clamp.element.querySelector(".date-field-trigger").dispatchClick();
    clamp.element.querySelector(".date-field-grid").dispatchEvent(key("PageDown"));
    const aMonthStepClampsTheDay = focusedDay() === "2026-02-28";
    clamp.destroy();
    if (!aMonthStepClampsTheDay || clamp.element.querySelector(".menu")) {
        fail(`a month step must clamp to the month's last day: ${focusedDay()}`);
    }

    // ── Autogrow ──────────────────────────────────────────────────────
    const plain = { style: {} };
    AutoGrow.fit(plain);
    const detached = Dom.h("textarea", {});
    AutoGrow.fit(detached);
    const autogrowSkipsANodeWithNoLayout = Object.keys(plain.style).length === 0 && !detached.style.height;
    const measured = Dom.h("textarea", {});
    Object.assign(measured, { scrollHeight: 480, clientHeight: 300, offsetHeight: 302 });
    const bound = AutoGrow.bind(measured);
    const grewToItsText = measured.style.height === "482px" && measured.style.overflowY === "auto";
    Object.assign(measured, { scrollHeight: 120, clientHeight: 120, offsetHeight: 122 });
    measured.dispatchEvent({ type: "input" });
    // Fitting clears the scrollbar override rather than hiding overflow, so a
    // later reflow can never clip the text with no way to scroll.
    const shrankOnInput = measured.style.height === "122px" && measured.style.overflowY === "";
    bound.destroy();
    Object.assign(measured, { scrollHeight: 200 });
    measured.dispatchEvent({ type: "input" });
    const autogrowBindsTextareasOnly = grewToItsText && shrankOnInput && measured.style.height === "122px"
        && threw(() => AutoGrow.bind(Dom.h("div", {})))
        && threw(() => AutoGrow.bind(null));
    if (!autogrowSkipsANodeWithNoLayout || !autogrowBindsTextareasOnly) {
        fail(`autogrow: skip ${autogrowSkipsANodeWithNoLayout}, bind ${autogrowBindsTextareasOnly}`);
    }

    // A width change re-wraps the text, so it re-fits — on the next frame,
    // never inside the notification; a height-only change (fit's own) must
    // not, or fitting would loop.
    const frames = [];
    global.requestAnimationFrame = (fn) => frames.push(fn);
    global.cancelAnimationFrame = (id) => { frames[id - 1] = null; };
    const flushFrames = () => frames.splice(0).forEach((fn) => fn && fn());
    const watched = Dom.h("textarea", {});
    Object.assign(watched, { isConnected: true, scrollHeight: 100, clientHeight: 100, offsetHeight: 102 });
    const watching = AutoGrow.bind(watched);
    window._resize(watched, 300, 102);
    Object.assign(watched, { scrollHeight: 160, clientHeight: 160, offsetHeight: 162 });
    window._resize(watched, 200, 102);
    const notInsideTheNotification = watched.style.height === "102px";
    flushFrames();
    const aWidthChangeRefits = notInsideTheNotification && watched.style.height === "162px";
    Object.assign(watched, { scrollHeight: 240 });
    window._resize(watched, 200, 162);
    flushFrames();
    const aHeightOnlyChangeDoesNot = watched.style.height === "162px";
    watching.destroy();
    const destroyDisconnects = window._observing(watched) === 0;
    // Removed without destroy (a section re-rendered through innerHTML): the
    // next notification disconnects it, so the observer never outlives it.
    const orphan = Dom.h("textarea", {});
    Object.assign(orphan, { isConnected: true, scrollHeight: 100, clientHeight: 100, offsetHeight: 102 });
    AutoGrow.bind(orphan);
    const orphanWasObserved = window._observing(orphan) === 1;
    orphan.isConnected = false;
    window._resize(orphan, 300, 102);
    const aDetachedTargetDisconnects = orphanWasObserved && window._observing(orphan) === 0;
    const autogrowRefitsOnWidthOnly = aWidthChangeRefits && aHeightOnlyChangeDoesNot
        && destroyDisconnects && aDetachedTargetDisconnects;
    if (!autogrowRefitsOnWidthOnly) {
        fail(`autogrow observer: width ${aWidthChangeRefits}, height-only ${aHeightOnlyChangeDoesNot}, `
            + `destroy ${destroyDisconnects}, detached ${aDetachedTargetDisconnects}`);
    }

    process.stdout.write(`${JSON.stringify({
        ok: true,
        parseTimeReadsTheListedForms,
        formatTimeOfDaySpellsTwelveHour,
        monthGridIsMondayFirstAndRollsOver,
        formatDateLabelSpellsTheDay,
        timeFieldIsATextFieldWithAClock,
        blurFormatsTheTime,
        anInvalidTimeIsMarkedAndSaid,
        enterSettles,
        anOffGridTimeIsListed,
        aSuggestionPickFiresOnChange,
        aBadStoredTimeThrows,
        dateTriggerNamesTheDay,
        theDateMenuOpensOnTheDay,
        arrowKeysMoveTheDay,
        pageDownMovesAMonth,
        enterPicksTheDay,
        escReturnsFocusToTheTrigger,
        aMonthStepClampsTheDay,
        autogrowSkipsANodeWithNoLayout,
        autogrowBindsTextareasOnly,
        autogrowRefitsOnWidthOnly,
    })}\n`);
})().catch((err) => fail(err && err.stack ? err.stack : String(err)));
