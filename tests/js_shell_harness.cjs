/**
 * Node harness: navigate() ordering, focus movement, error containment,
 * and no subscription leak across place swaps.
 * Invoked by tests/test_ui_shell.py. Not a browser bundle.
 */
const fs = require("fs");

let activeElement = null;
function makeEl(tag) {
    return {
        tagName: String(tag).toUpperCase(),
        nodeType: 1,
        attributes: {},
        children: [],
        listeners: {},
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        append(...kids) { kids.forEach((k) => this.children.push(k)); },
        replaceChildren() { this.children = []; },
        addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
        removeEventListener() {},
        focus() { activeElement = this; },
        querySelector(sel) {
            if (sel !== "h1") return null;
            const walk = (node) => {
                for (const c of node.children) {
                    if (c.tagName === "H1") return c;
                    if (c.children) { const f = walk(c); if (f) return f; }
                }
                return null;
            };
            return walk(this);
        },
        get textContent() {
            return this.children.map((c) => (c.nodeType === 3 ? c.textContent : (c.textContent || ""))).join("");
        },
    };
}
global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
    body: makeEl("body"),
    addEventListener() {},
    get activeElement() { return activeElement; },
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModStore = BossModStore;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModBus = BossModBus;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModPlaces = BossModPlaces;\n`);
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModNavigator = BossModNavigator;\n`);

const store = BossModStore.createStore({ place: "chat" });
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const container = makeEl("div");
const order = [];

// Two instrumented places replace stubs.
BossModPlaces.register("chat", {
    label: "Chat",
    mount(el) { order.push("mount:chat"); el.append(makeEl("h1")); },
    unmount() { order.push("unmount:chat"); },
});
BossModPlaces.register("board", {
    label: "Board",
    mount(el) {
        order.push("mount:board");
        const heading = makeEl("h1");
        heading.setAttribute("tabindex", "-1");
        el.append(heading);
    },
    unmount() { order.push("unmount:board"); },
});

const shell = BossModNavigator.createNavigator({ store, bus, container, api: () => {} });

shell.navigate("chat");
shell.navigate("board");

// Unmount MUST precede the next mount — two places must never hold state at once.
const seq = order.join(",");
if (seq !== "mount:chat,unmount:chat,mount:board") {
    throw new Error(`bad lifecycle order: ${seq}`);
}

// Focus lands on the new place's heading.
if (!activeElement || activeElement.tagName !== "H1") {
    throw new Error("navigate must focus the new place's h1");
}

// Store reflects the active place.
if (store.getState().place !== "board") throw new Error("store.place not updated");

// LEAK GUARD: a place that subscribes must not leak after unmount.
const baseline = bus.subscriberCount();
BossModPlaces.register("log", {
    label: "Log",
    mount(el, ctx) {
        this._off = ctx.bus.subscribe("activity", () => {});
        el.append(makeEl("h1"));
    },
    unmount() { if (this._off) this._off(); },
});
for (let i = 0; i < 20; i += 1) {
    shell.navigate("log");
    shell.navigate("board");
}
if (bus.subscriberCount() !== baseline) {
    throw new Error(`bus leak: baseline ${baseline}, now ${bus.subscriberCount()}`);
}

// A throwing mount shows an error, not a blank pane, and does not wedge nav.
BossModPlaces.register("files", {
    label: "Files",
    mount() { throw new Error("kaboom"); },
    unmount() {},
});
let threw = false;
try { shell.navigate("files"); } catch (e) { threw = true; }
if (threw) throw new Error("navigate must contain a place error, not propagate it");
if (!container.textContent.toLowerCase().includes("could not")) {
    throw new Error(`expected an error state, got: ${container.textContent}`);
}
// Navigation still works afterwards.
shell.navigate("board");
if (store.getState().place !== "board") throw new Error("nav wedged after an error");

process.stdout.write(JSON.stringify({
    ok: true,
    unmountsBeforeMount: true,
    focusesHeading: true,
    noBusLeakAfter20Swaps: true,
    containsMountErrors: true,
}));
