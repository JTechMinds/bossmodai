/**
 * Node harness: slot assign, pane independence, close/re-add, persist, resize.
 * Invoked by tests/test_ui_docks.py. Not a browser bundle.
 */
const fs = require("fs");

const documentStub = {
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
};

const dispatched = [];

global.document = documentStub;
global.window = {
    document: documentStub,
    addEventListener() {},
    dispatchEvent(event) {
        dispatched.push(event && event.type ? event.type : "event");
        return true;
    },
};
global.CustomEvent = class CustomEvent {
    constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
    }
};
global.Event = class Event {
    constructor(type) {
        this.type = type;
    }
};
global.requestAnimationFrame = (cb) => {
    cb();
    return 1;
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
const focusClosed = DockManager.closePane(defaults, "focus");
const focusReopened = DockManager.openPane(focusClosed, "focus");
const directoryOpened = DockManager.openPane(defaults, "directory");
const channelsMoved = DockManager.assignPane(directoryOpened, "channels", "right");
const independent = DockManager.closePane(channelsMoved, "focus");
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
const restoredClosed = DockManager.normalizeLayout(independent);
const restoredTabs = DockManager.normalizeLayout({
    version: 2,
    slots: {
        left: { panes: ["directory", "channels"], active: "channels" },
        center: { panes: ["map", "files"], active: "files" },
        right: { panes: ["activity"], active: "activity" },
    },
});
const toggledClosed = DockManager.togglePane(defaults, "activity");
const toggledOpen = DockManager.togglePane(toggledClosed, "activity");

const leftThree = DockManager.openPane(DockManager.openPane(defaults, "directory"), "channels");
const soloed = DockManager.maximizePane(leftThree, "directory");
const restored = DockManager.maximizePane(soloed, "directory");
const singleMax = DockManager.maximizePane(defaults, "focus");
const soloedPersist = DockManager.normalizeLayout(soloed);
const reordered = DockManager.placePane(leftThree, "channels", "left", 0);
const inserted = DockManager.placePane(defaults, "activity", "left", 0);
const sameSlotAppend = DockManager.assignPane(leftThree, "focus", "left");
const dropRects = [{ left: 0, width: 40 }, { left: 40, width: 40 }, { left: 80, width: 40 }];
const dropIndexes = [
    DockManager.dropIndexFromRects(dropRects, 10),
    DockManager.dropIndexFromRects(dropRects, 50),
    DockManager.dropIndexFromRects(dropRects, 90),
    DockManager.dropIndexFromRects(dropRects, 120),
    DockManager.dropIndexFromRects([], 0),
];

dispatched.length = 0;
DockManager.scheduleShownResize();
const resizeEvents = dispatched.filter((type) => type === "panel-resize");

const ALL_PANES = [
    "focus", "map", "activity", "directory", "channels", "files", "tasks", "metrics", "org",
];
const closeReadd = {};
ALL_PANES.forEach((id) => {
    const afterClose = DockManager.closePane(defaults, id);
    const afterOpen = DockManager.openPane(afterClose, id);
    closeReadd[id] = DockManager.slotOf(afterClose, id) === null
        && Boolean(DockManager.slotOf(afterOpen, id))
        && afterOpen.slots[DockManager.slotOf(afterOpen, id)].active === id;
});

const payload = {
    ok: true,
    slotIds: DockManager.SLOT_IDS,
    paneIds: DockManager.PANE_IDS,
    defaultLeft: defaults.slots.left,
    defaultCenter: defaults.slots.center,
    defaultRight: defaults.slots.right,
    directoryDefaultClosed: DockManager.slotOf(defaults, "directory") === null,
    channelsDefaultClosed: DockManager.slotOf(defaults, "channels") === null,
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
    coreCanClose: DockManager.slotOf(focusClosed, "focus") === null
        && focusClosed.slots.left.panes.length === 0
        && DockManager.slotOf(focusReopened, "focus") === "left",
    directoryIndependent: DockManager.slotOf(directoryOpened, "directory") === "left"
        && directoryOpened.slots.left.panes.indexOf("focus") !== -1
        && directoryOpened.slots.left.panes.indexOf("directory") !== -1
        && directoryOpened.slots.left.active === "directory",
    channelsOwnPane: DockManager.slotOf(channelsMoved, "channels") === "right"
        && DockManager.slotOf(channelsMoved, "focus") === "left"
        && DockManager.slotOf(independent, "focus") === null
        && DockManager.slotOf(independent, "directory") === "left"
        && DockManager.slotOf(independent, "channels") === "right",
    closeReadd,
    allPanesCloseable: ALL_PANES.every((id) => closeReadd[id]),
    persistRoundTrip: DockManager.slotOf(restoredClosed, "focus") === null
        && DockManager.slotOf(restoredClosed, "directory") === "left"
        && DockManager.slotOf(restoredClosed, "channels") === "right"
        && restoredClosed.slots.left.active === "directory"
        && restoredTabs.slots.left.panes.join(",") === "directory,channels"
        && restoredTabs.slots.left.active === "channels"
        && restoredTabs.slots.center.active === "files"
        && DockManager.slotOf(restoredTabs, "focus") === null,
    toggleWorks: DockManager.slotOf(toggledClosed, "activity") === null
        && DockManager.slotOf(toggledOpen, "activity") === "right",
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
    mapResizeTrigger: resizeEvents.length >= 2,
    maximizeSolos: soloed.slots.left.solo === "directory"
        && soloed.slots.left.active === "directory"
        && soloed.slots.left.panes.join(",") === "focus,directory,channels"
        && DockManager.canMaximizePane(leftThree, "directory") === true
        && DockManager.canMaximizePane(defaults, "focus") === false,
    maximizeRestores: restored.slots.left.solo === undefined
        && restored.slots.left.active === "directory"
        && restored.slots.left.panes.join(",") === "focus,directory,channels",
    maximizeSingleNoSolo: singleMax.slots.left.solo === undefined
        && singleMax.slots.left.panes.join(",") === "focus"
        && singleMax.slots.left.active === "focus",
    soloPersists: soloedPersist.slots.left.solo === "directory"
        && soloedPersist.slots.left.active === "directory",
    reorderWithinSlot: reordered.slots.left.panes.join(",") === "channels,focus,directory"
        && reordered.slots.left.active === "channels"
        && reordered.slots.left.solo === undefined,
    insertAtIndex: inserted.slots.left.panes.join(",") === "activity,focus"
        && inserted.slots.left.active === "activity"
        && DockManager.slotOf(inserted, "activity") === "left"
        && inserted.slots.right.panes.length === 0,
    assignStillAppends: sameSlotAppend.slots.left.panes.join(",") === "directory,channels,focus"
        && sameSlotAppend.slots.left.active === "focus",
    dropIndexFromRects: dropIndexes.join(",") === "0,1,2,3,0",
};

process.stdout.write(`${JSON.stringify(payload)}\n`);
