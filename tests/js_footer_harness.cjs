/**
 * Node harness: footer status transitions across a reconnect, the uptime
 * interval's teardown, and the two banners toggling from store state.
 *
 * Invoked by tests/test_ui_footer.py. Not a browser bundle.
 */
const fs = require("fs");

function makeEl(tag) {
    const el = {
        tagName: String(tag).toUpperCase(),
        nodeType: 1,
        attributes: {},
        classes: new Set(),
        children: [],
        listeners: {},
        parentNode: null,
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        removeAttribute(k) { delete this.attributes[k]; },
        append(...kids) {
            kids.forEach((raw) => {
                const k = (raw && raw.nodeType) ? raw : { nodeType: 3, textContent: String(raw) };
                if (k.nodeType === 1) k.parentNode = this;
                this.children.push(k);
            });
        },
        replaceChildren() { this.children = []; },
        addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
        removeEventListener() {},
        focus() {},
    };
    el.classList = {
        add: (c) => el.classes.add(c),
        remove: (c) => el.classes.delete(c),
        contains: (c) => el.classes.has(c),
        toggle: (c, on) => { if (on) el.classes.add(c); else el.classes.delete(c); },
    };
    return el;
}

global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
    body: makeEl("body"),
    getElementById() { return null; },
    addEventListener() {},
    removeEventListener() {},
};
global.window = { document: global.document };
global.lucide = { createIcons() {} };

// Spy on timers so the uptime interval's teardown is observable.
const liveTimers = new Set();
const realSetInterval = global.setInterval;
const realClearInterval = global.clearInterval;
global.setInterval = (fn, ms) => {
    const id = realSetInterval(fn, ms);
    liveTimers.add(id);
    return id;
};
global.clearInterval = (id) => {
    liveTimers.delete(id);
    realClearInterval(id);
};

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModStore = BossModStore;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModBus = BossModBus;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModFooter = BossModFooter;\n`);
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModBanners = BossModBanners;\n`);

function text(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.textContent;
    return (node.children || []).map(text).join(" ");
}

function find(node, predicate, out) {
    (node.children || []).forEach((child) => {
        if (child && child.nodeType === 1) {
            if (predicate(child)) out.push(child);
            find(child, predicate, out);
        }
    });
    return out;
}

const hasClass = (name) => (el) => String(el.getAttribute("class") || "").split(/\s+/).includes(name);
const settled = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 6; i += 1) await settled(); };

const apiCalls = [];
function apiFetch(url) {
    apiCalls.push(url);
    if (url.startsWith("/api/connections")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([{ id: "c1", provider: "openai" }]) });
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
}

(async () => {
    const store = BossModStore.createStore({
        connection: "connected",
        runtimePaused: false,
        hasUsableModel: false,
        roster: [{ id: "a1" }, { id: "a2" }, { id: "a3" }],
    });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const storeBaseline = store.subscriberCount();
    const busBaseline = bus.subscriberCount();

    // ── Footer ──
    const el = makeEl("footer");
    const disposeFooter = BossModFooter.mount(el, { store, bus });
    if (typeof disposeFooter !== "function") throw new Error("footer mount must return a disposer");

    const label = () => text(find(el, hasClass("footer-status-label"), [])[0]);
    const dotState = () => find(el, hasClass("footer-status-dot"), [])[0].getAttribute("data-state");

    if (label() !== "Connected") throw new Error(`expected Connected, got "${label()}"`);
    if (!text(el).includes("3 agents")) throw new Error(`agent count missing: ${text(el)}`);

    // A reconnect says so, and keeps saying so until the refetches settle.
    bus.publish("resync", { downtimeMs: 4200 });
    if (label() !== "Reconnected — refreshing") {
        throw new Error(`resync must announce itself, got "${label()}"`);
    }
    if (dotState() !== "resyncing") throw new Error(`dot state should be resyncing, got ${dotState()}`);
    store.setState({ connection: "resyncing" });
    if (label() !== "Reconnected — refreshing") {
        throw new Error(`notice must persist while refetching, got "${label()}"`);
    }
    store.setState({ connection: "connected" });
    if (label() !== "Connected") throw new Error(`expected Connected after resync, got "${label()}"`);

    // The live agent count follows the store roster.
    store.setState({ roster: [{ id: "a1" }] });
    if (!text(el).includes("1 agent ")) {
        if (!/\b1 agent\b/.test(text(el))) throw new Error(`agent count must be singular: ${text(el)}`);
    }

    if (liveTimers.size !== 1) throw new Error(`footer must run exactly one uptime interval, got ${liveTimers.size}`);

    // ── Banners ──
    const pauseBanner = makeEl("div");
    pauseBanner.classList.add("hidden");
    const noModelBanner = makeEl("div");
    noModelBanner.classList.add("hidden");
    const disposeBanners = BossModBanners.mount({ store, bus, apiFetch, pauseBanner, noModelBanner });
    if (typeof disposeBanners !== "function") throw new Error("banners mount must return a disposer");
    await drain();

    if (!apiCalls.some((u) => u.startsWith("/api/connections"))) {
        throw new Error("banners must check model availability through /api/connections");
    }
    if (store.getState().hasUsableModel !== true) throw new Error("a live connection means a usable model");
    if (noModelBanner.classList.contains("hidden") !== true) {
        throw new Error("the no-model banner must stay hidden while a model is connected");
    }
    store.setState({ hasUsableModel: false });
    if (noModelBanner.classList.contains("hidden") !== false) {
        throw new Error("the no-model banner must show when no model is connected");
    }

    if (pauseBanner.classList.contains("hidden") !== true) throw new Error("pause banner starts hidden");
    bus.publish("runtime_state", { paused: true, started_at: new Date(Date.now() - 90000).toISOString() });
    if (store.getState().runtimePaused !== true) throw new Error("runtime_state must record the pause");
    if (pauseBanner.classList.contains("hidden") !== false) {
        throw new Error("the pause banner must show while the runtime is paused");
    }
    if (label() !== "Paused") throw new Error(`footer must read Paused, got "${label()}"`);
    if (!/\d/.test(text(find(el, hasClass("footer-uptime"), [])[0]))) {
        throw new Error("uptime must render once the runtime start time is known");
    }

    // ── Teardown ──
    disposeFooter();
    disposeBanners();
    if (liveTimers.size !== 0) throw new Error("the uptime interval must be cleared on teardown");
    if (store.subscriberCount() !== storeBaseline) {
        throw new Error(`store leak: baseline ${storeBaseline}, now ${store.subscriberCount()}`);
    }
    if (bus.subscriberCount() !== busBaseline) {
        throw new Error(`bus leak: baseline ${busBaseline}, now ${bus.subscriberCount()}`);
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        resyncAnnouncesThenClears: true,
        agentCountFollowsRoster: true,
        uptimeIntervalClearedOnTeardown: true,
        bannersToggleFromState: true,
        disposersDrain: true,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
