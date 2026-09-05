/**
 * Node harness: slot assign + maximize→tabs.
 * Invoked by tests/test_ui_docks.py. Not a browser bundle.
 */
const fs = require("fs");

const documentStub = {
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
};

global.document = documentStub;
global.window = {
    document: documentStub,
    addEventListener() {},
    dispatchEvent() { return true; },
};
global.CustomEvent = class CustomEvent {
    constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
    }
};

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.DockManager = DockManager;\n`);

if (typeof DockManager !== "object") {
    throw new Error("DockManager missing");
}

const defaults = DockManager.defaultLayout();
const assigned = DockManager.assignPane(defaults, "activity", "left");
const mapped = DockManager.assignPane(assigned, "map", "right");
const opened = DockManager.openPane(defaults, "files");
const maximized = DockManager.maximizePane(opened, "tasks");
const closed = DockManager.closePane(maximized, "files");
const office = DockManager.closeAllPanes(closed);
const coreCloseIgnored = DockManager.closePane(defaults, "focus");
const junk = DockManager.normalizeLayout({
    version: 2,
    slots: {
        left: { panes: ["focus", "nope"], active: "nope" },
        center: { panes: ["map", "map"], active: "map" },
        right: { panes: ["activity"], active: "activity" },
    },
    files: { open: true, x: 0.1 },
});
const migrated = DockManager.normalizeLayout({
    files: { open: true, x: 0.08, y: 0.07, w: 0.7, h: 0.78, filled: true },
    tasks: { open: true, x: 0.14, y: 0.12, w: 0.7, h: 0.78 },
    junk: { open: true },
});
const empty = DockManager.normalizeLayout(null);

const payload = {
    ok: true,
    slotIds: DockManager.SLOT_IDS,
    paneIds: DockManager.PANE_IDS,
    defaultLeft: defaults.slots.left,
    defaultCenter: defaults.slots.center,
    defaultRight: defaults.slots.right,
    activityMovedLeft: DockManager.slotOf(assigned, "activity") === "left"
        && assigned.slots.left.active === "activity"
        && assigned.slots.left.panes.indexOf("focus") !== -1
        && assigned.slots.right.panes.length === 0,
    mapMovedRight: DockManager.slotOf(mapped, "map") === "right",
    filesOpenedCenter: DockManager.slotOf(opened, "files") === "center"
        && opened.slots.center.panes.indexOf("map") !== -1
        && opened.slots.center.panes.indexOf("files") !== -1
        && opened.slots.center.active === "files",
    maximizeIsTabs: maximized.slots.center.panes.indexOf("map") !== -1
        && maximized.slots.center.panes.indexOf("files") !== -1
        && maximized.slots.center.panes.indexOf("tasks") !== -1
        && maximized.slots.center.active === "tasks"
        && maximized.filled === undefined
        && !maximized.slots.center.panes.some((id) => typeof id === "object"),
    filesClosed: DockManager.slotOf(closed, "files") === null
        && closed.slots.center.panes.indexOf("tasks") !== -1,
    companyClosed: DockManager.companyOpenIds(office).length === 0
        && office.slots.center.panes.indexOf("map") !== -1,
    coreStaysOpen: coreCloseIgnored.slots.left.panes.indexOf("focus") !== -1,
    junkIgnored: junk.slots.left.panes.indexOf("nope") === -1
        && junk.slots.left.active === "focus"
        && junk.slots.center.panes.filter((id) => id === "map").length === 1
        && junk.files === undefined,
    migratedToCenterTabs: migrated.slots.center.panes.indexOf("map") !== -1
        && migrated.slots.center.panes.indexOf("files") !== -1
        && migrated.slots.center.panes.indexOf("tasks") !== -1
        && migrated.slots.center.active === "tasks"
        && migrated.files === undefined,
    emptyDefaults: empty.slots.left.active === "focus"
        && empty.slots.center.active === "map"
        && empty.slots.right.active === "activity",
};

process.stdout.write(`${JSON.stringify(payload)}\n`);
