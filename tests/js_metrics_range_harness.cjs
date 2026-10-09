/**
 * Node harness: the Metrics period row — places/metrics/metrics-range.js.
 *
 * Invoked by tests/test_ui_metrics_range.py with TZ=America/New_York, so the
 * local-midnight instants cross a real DST change (Sunday, March 8, 2026, a
 * 23-hour day). Not a browser bundle.
 *
 * The pure calendar half (presetDays, toInstants, label) is checked against a
 * pinned "today"; the row is built for real against the shared fake DOM and
 * driven as the operator drives it — picking Custom range from the Period
 * dropdown, then a To day before From.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

const documentStub = installDom();
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModOverlayFocus", "BossModMenu", "BossModMenuSelect",
    "BossModDateField", "BossModMetricsRange",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});
const { BossModMetricsRange: Range } = global;

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

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

if (process.env.TZ !== "America/New_York") fail(`run with TZ=America/New_York, not ${process.env.TZ}`);

// ── presetDays ─────────────────────────────────────────────────────────
// Wed Oct 7 2026 (mid-week), Mon Oct 5 2026, Sun Nov 1 2026 (a month's first
// day, which is also a week's last).
const presetDaysCountsFromToday = same(Range.presetDays("today", "2026-10-07"), { from: "2026-10-07", to: "2026-10-07" })
    && same(Range.presetDays("yesterday", "2026-10-07"), { from: "2026-10-06", to: "2026-10-06" })
    && same(Range.presetDays("week", "2026-10-07"), { from: "2026-10-05", to: "2026-10-07" })
    && same(Range.presetDays("week", "2026-10-05"), { from: "2026-10-05", to: "2026-10-05" })
    && same(Range.presetDays("month", "2026-10-07"), { from: "2026-10-01", to: "2026-10-07" })
    && same(Range.presetDays("yesterday", "2026-11-01"), { from: "2026-10-31", to: "2026-10-31" })
    && same(Range.presetDays("week", "2026-11-01"), { from: "2026-10-26", to: "2026-11-01" })
    && same(Range.presetDays("month", "2026-11-01"), { from: "2026-11-01", to: "2026-11-01" })
    && same(Range.presetDays("yesterday", "2027-01-01"), { from: "2026-12-31", to: "2026-12-31" })
    && threw(() => Range.presetDays("custom", "2026-10-07"))
    && threw(() => Range.presetDays("fortnight", "2026-10-07"))
    && threw(() => Range.presetDays("today", "2026-02-30"));
if (!presetDaysCountsFromToday) fail("presetDays must count Monday weeks and months from the injected today");

// ── toInstants ─────────────────────────────────────────────────────────
const toInstantsUsesLocalMidnights = same(
    Range.toInstants({ from: "2026-10-01", to: "2026-10-01" }),
    { start: "2026-10-01T04:00:00.000Z", end: "2026-10-02T04:00:00.000Z" },
) && same(
    // The spring-forward day is 23 hours long: EST midnight to EDT midnight.
    Range.toInstants({ from: "2026-03-08", to: "2026-03-08" }),
    { start: "2026-03-08T05:00:00.000Z", end: "2026-03-09T04:00:00.000Z" },
) && same(
    Range.toInstants({ from: "2026-03-07", to: "2026-03-09" }),
    { start: "2026-03-07T05:00:00.000Z", end: "2026-03-10T04:00:00.000Z" },
) && threw(() => Range.toInstants({ from: "2026-10-02", to: "2026-10-01" }))
    && threw(() => Range.toInstants({ from: "2026-10-01", to: "nope" }));
if (!toInstantsUsesLocalMidnights) fail("toInstants must return local midnights as UTC instants, across DST");

// ── label ──────────────────────────────────────────────────────────────
const labelNamesThePeriod = Range.label({ preset: "week", from: "2026-10-05", to: "2026-10-07" }) === "This week"
    && Range.label({ preset: "today", from: "2026-10-07", to: "2026-10-07" }) === "Today"
    && Range.label({ preset: "custom", from: "2026-10-01", to: "2026-10-01" }) === "Thu, Oct 1, 2026"
    && Range.label({ preset: "custom", from: "2026-10-01", to: "2026-10-07" })
        === "Thu, Oct 1, 2026 → Wed, Oct 7, 2026"
    && threw(() => Range.label({ preset: "fortnight", from: "2026-10-01", to: "2026-10-01" }));
if (!labelNamesThePeriod) fail("label must name a preset, one day, or a span with →");

// ── createRangeFilter ──────────────────────────────────────────────────
const TODAY = { preset: "today", from: "2026-10-07", to: "2026-10-07" };
const factoryRefusesMissingDeps = threw(() => Range.createRangeFilter({ onChange() {} }))
    && threw(() => Range.createRangeFilter({ initial: TODAY }))
    && threw(() => Range.createRangeFilter(undefined))
    && threw(() => Range.createRangeFilter({ initial: { preset: "fortnight" }, onChange() {} }));
if (!factoryRefusesMissingDeps) fail("createRangeFilter must throw without initial or onChange");

(async () => {
    // One row: the Period dropdown, then the trailing node last.
    let changes = 0;
    const refresh = global.BossModDom.h("button", { class: "btn btn-sm", type: "button" }, "Refresh");
    const row = Range.createRangeFilter({ initial: TODAY, onChange: () => { changes += 1; }, trailing: refresh });
    documentStub.body.append(row.element);
    const triggers = () => row.element.querySelectorAll("button")
        .filter((button) => button.getAttribute("aria-haspopup") === "dialog");
    const errorEl = row.element.querySelector(".files-error");
    const opening = row.read();
    const theRowIsOneToolbar = row.element.getAttribute("class") === "place-controls"
        && row.element.children[row.element.children.length - 1] === refresh
        && triggers().length === 1
        && triggers()[0].getAttribute("class") === "btn btn-sm menu-select-trigger"
        && triggers()[0].getAttribute("aria-label") === "Period: Today"
        && errorEl.getAttribute("role") === "alert" && errorEl.hidden === true
        && opening.ok === true && opening.preset === "today" && opening.label === "Today"
        && opening.days.from === opening.days.to;
    if (!theRowIsOneToolbar) fail("the period row must be one .place-controls row: Period, then Refresh");

    // Custom range reveals From and To as toolbar buttons, seeded from today.
    await triggers()[0].dispatchClick();
    const custom = row.element.querySelectorAll(".menu-select-option")
        .find((option) => option.textContent === "Custom range");
    await custom.dispatchClick();
    const [, fromTrigger, toTrigger] = triggers();
    const today = global.BossModDateField.today();
    const customRevealsFromAndTo = triggers().length === 3
        && fromTrigger.getAttribute("class") === "btn btn-sm date-field-trigger"
        && toTrigger.getAttribute("class") === "btn btn-sm date-field-trigger"
        && fromTrigger.getAttribute("aria-label").startsWith("From: ")
        && toTrigger.getAttribute("aria-label").startsWith("To: ")
        && same(row.read().days, { from: today, to: today })
        && row.element.children[row.element.children.length - 1] === refresh
        && changes === 1;
    if (!customRevealsFromAndTo) fail(`Custom range must reveal From and To and report once: ${changes}`);

    // Back to a preset: From and To go away.
    await triggers()[0].dispatchClick();
    await row.element.querySelectorAll(".menu-select-option")
        .find((option) => option.textContent === "Yesterday").dispatchClick();
    const yesterday = row.read();
    const aPresetHidesTheDays = triggers().length === 1 && changes === 2 && yesterday.ok
        && yesterday.label === "Yesterday"
        && yesterday.days.from === global.BossModDateField.addDays(today, -1);
    row.destroy();
    if (!aPresetHidesTheDays) fail("picking a preset must remove From and To and report");

    // To before From, on pinned days: said inline, and the place is not told.
    let pinnedChanges = 0;
    const pinned = Range.createRangeFilter({
        initial: { preset: "custom", from: "2026-10-07", to: "2026-10-07" },
        onChange: () => { pinnedChanges += 1; },
    });
    documentStub.body.append(pinned.element);
    const pinnedError = pinned.element.querySelector(".files-error");
    const toButton = pinned.element.querySelectorAll(".date-field-trigger")[1];
    const pickTo = async (iso) => {
        await toButton.dispatchClick();
        await pinned.element.querySelector(`[data-date="${iso}"]`).dispatchClick();
    };
    await pickTo("2026-10-06");
    const backwards = pinned.read();
    const backwardsIsSaidNotSent = pinnedChanges === 0 && backwards.ok === false
        && backwards.error === "The end date is before the start date."
        && pinnedError.hidden === false && pinnedError.textContent === "The end date is before the start date.";
    if (!backwardsIsSaidNotSent) fail(`To before From must show the error and not call onChange: ${pinnedChanges}`);

    await pickTo("2026-10-09");
    const fixed = pinned.read();
    const fixingItReports = pinnedChanges === 1 && fixed.ok === true && pinnedError.hidden === true
        && pinnedError.textContent === "" && fixed.label === "Wed, Oct 7, 2026 → Fri, Oct 9, 2026";
    pinned.destroy();
    if (!fixingItReports) fail("a valid range again must clear the error and report");

    // A remembered custom range comes back with its days.
    const remembered = Range.createRangeFilter({
        initial: { preset: "custom", from: "2026-10-01", to: "2026-10-07" }, onChange() {},
    });
    const read = remembered.read();
    const aCustomInitialSeedsTheDays = read.ok && read.preset === "custom"
        && same(read.days, { from: "2026-10-01", to: "2026-10-07" })
        && read.label === "Thu, Oct 1, 2026 → Wed, Oct 7, 2026"
        && same(read.instants, { start: "2026-10-01T04:00:00.000Z", end: "2026-10-08T04:00:00.000Z" });
    remembered.destroy();
    if (!aCustomInitialSeedsTheDays) fail(`a custom initial must seed From and To: ${JSON.stringify(read)}`);

    process.stdout.write(`${JSON.stringify({
        ok: true,
        presetDaysCountsFromToday,
        toInstantsUsesLocalMidnights,
        labelNamesThePeriod,
        factoryRefusesMissingDeps,
        theRowIsOneToolbar,
        customRevealsFromAndTo,
        backwardsIsSaidNotSent,
        fixingItReports,
        aPresetHidesTheDays,
        aCustomInitialSeedsTheDays,
    })}\n`);
})().catch((err) => fail(err && err.stack ? err.stack : String(err)));
