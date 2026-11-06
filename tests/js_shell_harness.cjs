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
eval(`${fs.readFileSync(process.argv[7], "utf8")}\n;global.BossModAgentMenu = BossModAgentMenu;\n`);
eval(`${fs.readFileSync(process.argv[8], "utf8")}\n;global.BossModAgentRoutes = BossModAgentRoutes;\n`);

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
BossModPlaces.register("tasks", {
    label: "Tasks",
    mount(el) {
        order.push("mount:tasks");
        const heading = makeEl("h1");
        heading.setAttribute("tabindex", "-1");
        el.append(heading);
    },
    unmount() { order.push("unmount:tasks"); },
});

// The desk modal every place reaches as ctx.openDesk; required, so a place
// can never render a desk door that fails only at the click.
const openDesk = () => {};
// The roster drawer, the same way: Chat's empty state offers it as a button.
const openRoster = () => {};
// The page's agent `⋯` (a DM header's rows, a memory line's Memory), built
// from the real module as the shell builds it, with the desk as its door.
const api = () => {};
const agentMenu = BossModAgentMenu.create({ api, store, navigate: () => {}, openDesk });
// The Settings takeover, injected so the navigator never names it. `open` is
// what isOpen() reports; close() counts, so a case can tell it was left.
const takeover = {
    open: false,
    closes: 0,
    isOpen() { return this.open; },
    close() { this.open = false; this.closes += 1; },
};
let refusedWithoutDesk = false;
try {
    BossModNavigator.createNavigator({ store, bus, container, api, agentMenu, openRoster, takeover });
} catch (err) {
    refusedWithoutDesk = /openDesk is required/.test(String(err && err.message));
}
if (!refusedWithoutDesk) throw new Error("createNavigator must refuse a missing openDesk");
let refusedWithoutRoster = false;
try {
    BossModNavigator.createNavigator({ store, bus, container, api, openDesk, agentMenu, takeover });
} catch (err) {
    refusedWithoutRoster = /openRoster is required/.test(String(err && err.message));
}
if (!refusedWithoutRoster) throw new Error("createNavigator must refuse a missing openRoster");
let refusedWithoutAgentMenu = false;
try {
    BossModNavigator.createNavigator({ store, bus, container, api, openDesk, openRoster, takeover });
} catch (err) {
    refusedWithoutAgentMenu = /agentMenu is required/.test(String(err && err.message));
}
if (!refusedWithoutAgentMenu) throw new Error("createNavigator must refuse a missing agentMenu");
let refusedWithoutTakeover = false;
try {
    BossModNavigator.createNavigator({ store, bus, container, api, openDesk, agentMenu, openRoster });
} catch (err) {
    refusedWithoutTakeover = /takeover is required/.test(String(err && err.message));
}
if (!refusedWithoutTakeover) throw new Error("createNavigator must refuse a missing takeover");
const shell = BossModNavigator.createNavigator({
    store, bus, container, api, openDesk, agentMenu, openRoster, takeover,
});
if (shell.getCtx().openDesk !== openDesk) throw new Error("ctx.openDesk must be the injected opener");
if (shell.getCtx().openRoster !== openRoster) throw new Error("ctx.openRoster must be the injected opener");
if (shell.getCtx().agentMenu !== agentMenu) throw new Error("ctx.agentMenu must be the injected menu");

shell.navigate("chat");
shell.navigate("tasks");

// Unmount MUST precede the next mount — two places must never hold state at once.
const seq = order.join(",");
if (seq !== "mount:chat,unmount:chat,mount:tasks") {
    throw new Error(`bad lifecycle order: ${seq}`);
}

// Focus lands on the new place's heading.
if (!activeElement || activeElement.tagName !== "H1") {
    throw new Error("navigate must focus the new place's h1");
}

// Store reflects the active place.
if (store.getState().place !== "tasks") throw new Error("store.place not updated");

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
    shell.navigate("tasks");
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
shell.navigate("tasks");
if (store.getState().place !== "tasks") throw new Error("nav wedged after an error");

// ─── The Settings takeover ───
// Choosing a place while it covers the centre column leaves it. The place
// already mounted under it is uncovered, not remounted: a remount would drop
// Chat's draft, transcript cache and caret.

// Covered, another place: the takeover closes once, the swap is the usual one.
order.length = 0;
takeover.open = true;
takeover.closes = 0;
shell.navigate("chat");
const navigationLeavesTheTakeover = takeover.closes === 1 && takeover.open === false
    && order.join(",") === "unmount:tasks,mount:chat"
    && store.getState().place === "chat";

// Covered, the mounted place with no params (the header passes none, a Chat
// need target passes {}): uncovered only, focus on its heading, params kept.
shell.navigate("tasks", { taskId: "keep" });
const keptParams = store.getState().placeParams;
order.length = 0;
takeover.closes = 0;
const reveals = [undefined, {}].map((params) => {
    activeElement = null;
    takeover.open = true;
    shell.navigate("tasks", params);
    return takeover.open === false
        && order.length === 0
        && activeElement !== null && activeElement === container.querySelector("h1")
        && store.getState().placeParams === keptParams
        && store.getState().place === "tasks";
});
const revealsTheCoveredPlaceWithoutRemount = reveals.every((ok) => ok) && takeover.closes === 2;

// Covered, the same place WITH params: they are something to show, so it remounts.
order.length = 0;
takeover.open = true;
takeover.closes = 0;
shell.navigate("tasks", { taskId: "x" });
const paramsStillRemountUnderTheTakeover = takeover.closes === 1
    && order.join(",") === "unmount:tasks,mount:tasks"
    && store.getState().placeParams.taskId === "x";

// Not covered, the same place: today's behaviour, a remount that resets it.
order.length = 0;
takeover.closes = 0;
shell.navigate("tasks");
const uncoveredRenavigationUnchanged = takeover.closes === 0
    && order.join(",") === "unmount:tasks,mount:tasks"
    && Object.keys(store.getState().placeParams).length === 0;

// Covered, an unknown id: it throws before the takeover is touched.
takeover.open = true;
takeover.closes = 0;
let unknownThrew = false;
try { shell.navigate("nowhere"); } catch (err) { unknownThrew = /unknown place/.test(String(err && err.message)); }
const unknownPlaceLeavesTheTakeoverUp = unknownThrew && takeover.open === true && takeover.closes === 0
    && store.getState().place === "tasks";
takeover.open = false;

// ─── openConversation: a Chat under the takeover is not on screen ───
const routesStore = BossModStore.createStore({
    place: "chat", settingsOpen: false, conversationId: null, conversationKind: null,
});
const routedTo = [];
const routes = { store: routesStore, navigate: (placeId) => routedTo.push(placeId) };
BossModAgentRoutes.openConversation(routes, "a1", "agent");
const stayedInChat = routedTo.length === 0 && routesStore.getState().conversationId === "a1";
routesStore.setState({ settingsOpen: true });
BossModAgentRoutes.openConversation(routes, "a2", "agent");
const leftSettings = routedTo.join(",") === "chat" && routesStore.getState().conversationId === "a2";
routesStore.setState({ place: "office", settingsOpen: false });
BossModAgentRoutes.openConversation(routes, "t1", "thread");
const cameFromOffice = routedTo.join(",") === "chat,chat"
    && routesStore.getState().conversationKind === "thread";
const openConversationLeavesTheTakeover = stayedInChat && leftSettings && cameFromOffice;

process.stdout.write(JSON.stringify({
    ok: true,
    unmountsBeforeMount: true,
    focusesHeading: true,
    noBusLeakAfter20Swaps: true,
    containsMountErrors: true,
    handsEveryPlaceTheDeskOpener: true,
    handsEveryPlaceTheRosterOpener: refusedWithoutRoster,
    handsEveryPlaceTheAgentMenu: refusedWithoutAgentMenu,
    navigationLeavesTheTakeover,
    revealsTheCoveredPlaceWithoutRemount,
    paramsStillRemountUnderTheTakeover,
    uncoveredRenavigationUnchanged,
    unknownPlaceLeavesTheTakeoverUp,
    openConversationLeavesTheTakeover,
}));
