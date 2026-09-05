/**
 * Node harness: dock layout clamp / normalize.
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

const clamped = DockManager.clampRect({ x: -0.4, y: 2, w: 0.05, h: 3 });
const normalized = DockManager.normalizeLayout({
    files: { open: true, x: 0.1, y: 0.1, w: 0.5, h: 0.5, z: 4 },
    junk: { open: true },
});

const payload = {
    ok: true,
    dockIds: DockManager.DOCK_IDS,
    clampedInBounds: clamped.x >= 0 && clamped.y >= 0 && clamped.x + clamped.w <= 1.0001 && clamped.y + clamped.h <= 1.0001,
    clampedMinSize: clamped.w >= 0.28 && clamped.h >= 0.28,
    filesOpen: normalized.files.open === true,
    tasksClosed: normalized.tasks.open === false,
    junkIgnored: normalized.junk === undefined,
    hasOpen: typeof DockManager.hasOpen === "function",
};

process.stdout.write(`${JSON.stringify(payload)}\n`);
