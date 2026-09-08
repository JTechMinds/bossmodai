/**
 * Node harness: roster status-line precedence and search behaviour.
 *
 * Phase 2B split the Threads half into shell/roster-threads.js and the visual
 * parity pass split the People half into shell/roster-people.js; the rail
 * constructs both. All three are evaluated here so the same properties are
 * proven against the assembled rail.
 *
 * Invoked by tests/test_ui_roster.py. Not a browser bundle.
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
        parentNode: null,
        value: "",
        selectionStart: 0,
        checked: false,
        disabled: false,
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        removeAttribute(k) { delete this.attributes[k]; },
        hasAttribute(k) { return k in this.attributes; },
        append(...kids) {
            kids.forEach((raw) => {
                const k = (raw && raw.nodeType) ? raw : { nodeType: 3, textContent: String(raw) };
                if (k.nodeType === 1) k.parentNode = this;
                this.children.push(k);
            });
        },
        remove() {
            if (!this.parentNode) return;
            const i = this.parentNode.children.indexOf(this);
            if (i !== -1) this.parentNode.children.splice(i, 1);
            this.parentNode = null;
        },
        replaceChildren() { this.children = []; },
        addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
        removeEventListener(n, fn) {
            const l = this.listeners[n] || [];
            const i = l.indexOf(fn);
            if (i !== -1) l.splice(i, 1);
        },
        focus() { activeElement = this; },
        contains(other) {
            if (other === this) return true;
            return this.children.some((c) => c && c.contains && c.contains(other));
        },
    };
}

const body = makeEl("body");
global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
    body,
    getElementById() { return null; },
    listeners: {},
    addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
    removeEventListener() {},
    get activeElement() { return activeElement; },
};
global.window = { document: global.document };
global.lucide = { createIcons() {} };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModAvatar = BossModAvatar;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModStore = BossModStore;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModBus = BossModBus;\n`);
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModAgentStatus = BossModAgentStatus;\n`);
eval(`${fs.readFileSync(process.argv[7], "utf8")}\n;global.BossModRosterPeople = BossModRosterPeople;\n`);
eval(`${fs.readFileSync(process.argv[8], "utf8")}\n;global.BossModRosterThreads = BossModRosterThreads;\n`);
eval(`${fs.readFileSync(process.argv[9], "utf8")}\n;global.BossModRoster = BossModRoster;\n`);

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

const WORLD = [
    { id: "a1", name: "Jim", role: "Engineer", color: "#3b82f6", status: "work_active", currentActivityKind: "work", x: 1, y: 1 },
    { id: "a2", name: "Laura", role: "Writer", color: "#f59e0b", status: "idle", currentActivityKind: null, x: 2, y: 2 },
];
const CHANNELS = [
    { id: "c1", name: "Launch plan", kind: "shared", status: "active", member_count: 2, members: [], latest_message: null },
];

const apiCalls = [];
function apiFetch(url, init) {
    apiCalls.push({ url, init: init || null });
    if (url.startsWith("/api/world")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(WORLD) });
    }
    if (url.startsWith("/api/channels") && (!init || !init.method || init.method === "GET")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(CHANNELS) });
    }
    if (url === "/api/channels" && init && init.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ id: "c2", name: "New thread" }) });
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
}

function rowFor(el, name) {
    return find(el, hasClass("roster-person"), []).filter((b) => text(b).includes(name))[0];
}

(async () => {
    const store = BossModStore.createStore({
        place: "chat",
        conversationId: null,
        conversationKind: null,
        rosterQuery: "",
        roster: [],
        threads: [],
        needs: [{ id: "n1", kind: "consent", agentId: "a1" }],
        runtimePaused: true,
    });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const storeBaseline = store.subscriberCount();
    const busBaseline = bus.subscriberCount();
    const el = makeEl("aside");
    const navigated = [];
    let hires = 0;

    const dispose = BossModRoster.mount(el, {
        store,
        bus,
        apiFetch,
        navigate: (id, params) => navigated.push({ id, params: params || null }),
        onHire: () => { hires += 1; },
    });
    if (typeof dispose !== "function") throw new Error("mount must return a disposer");
    await drain();

    // ── Status precedence: Paused beats a need beats the status label ──
    const jim = () => rowFor(el, "Jim");
    if (!jim()) throw new Error(`Jim's row missing; roster rendered: ${text(el)}`);
    if (!text(jim()).includes("Paused")) {
        throw new Error(`paused runtime must win the status line, got "${text(jim())}"`);
    }
    store.setState({ runtimePaused: false });
    if (!text(jim()).includes("Needs you")) {
        throw new Error(`an open need must win over the status label, got "${text(jim())}"`);
    }
    if (text(jim()).includes("Paused")) throw new Error("Paused must clear when the runtime resumes");
    store.setState({ needs: [] });
    // BossModAgentStatus.getStatusLabel('work_active', 'work') === 'working'; the raw
    // status would read 'work_active', so this pins the shared helper.
    if (!text(jim()).includes("working")) {
        throw new Error(`status label must come from BossModAgentStatus.getStatusLabel, got "${text(jim())}"`);
    }

    // ── Search filters on name and role, and keeps the caret ──
    const input = find(el, hasClass("roster-search"), [])[0];
    if (!input) throw new Error("roster must render a search input");
    const fireInput = (value) => {
        input.value = value;
        (input.listeners.input || []).forEach((fn) => fn({ target: input }));
    };

    fireInput("engineer");
    if (!rowFor(el, "Jim")) throw new Error("search must match on role");
    if (rowFor(el, "Laura")) throw new Error("search must exclude non-matching roles");

    fireInput("laura");
    if (!rowFor(el, "Laura")) throw new Error("search must match on name");
    if (rowFor(el, "Jim")) throw new Error("search must exclude non-matching names");

    fireInput("");
    if (!rowFor(el, "Jim") || !rowFor(el, "Laura")) throw new Error("clearing the search must restore every row");

    // The caret survives a re-render driven by live data.
    fireInput("la");
    input.selectionStart = 2;
    bus.publish("world_update", WORLD);
    await drain();
    const inputAfter = find(el, hasClass("roster-search"), [])[0];
    if (inputAfter !== input) throw new Error("the search input must not be re-created on re-render");
    if (input.selectionStart !== 2) {
        throw new Error(`caret moved on re-render: ${input.selectionStart}`);
    }
    if (input.value !== "la") throw new Error("the search text must survive a re-render");
    fireInput("");

    // ── Threads and Hire ──
    if (!text(el).includes("Launch plan")) throw new Error("threads must render from GET /api/channels");
    const hire = find(el, hasClass("roster-hire"), [])[0];
    if (!hire) throw new Error("roster must pin a Hire row to the bottom");
    (hire.listeners.click || []).forEach((fn) => fn({ preventDefault() {} }));
    if (hires !== 1) throw new Error("the Hire row must start the hire flow");

    // ── Select mode: the checkboxes are revealed on demand ──
    //
    // Absent from the DOM rather than hidden by CSS. A hidden checkbox is
    // still a tab stop and still carries a stale checked state, which is the
    // exact way a "cleared" selection quietly builds the wrong thread.
    const boxesNow = () => find(el, hasClass("roster-select"), []);
    const click = (node) => (node.listeners.click || []).forEach((fn) => fn({ preventDefault() {} }));

    if (boxesNow().length !== 0) {
        throw new Error(`people rows must be clean until select mode, got ${boxesNow().length} boxes`);
    }
    const createBtn = find(el, hasClass("roster-create-thread"), [])[0];
    if (!createBtn) throw new Error("roster must render a thread creation button");
    if (!text(createBtn).includes("New thread")) {
        throw new Error(`out of select mode the button invites, got "${text(createBtn)}"`);
    }
    if (find(el, hasClass("roster-thread-cancel"), []).length !== 0) {
        throw new Error("Cancel must not exist outside select mode");
    }
    const rowsAreCleanUntilSelectMode = true;

    // Entering is a click on a real <button>, so Enter and Space reach it too.
    click(createBtn);
    if (boxesNow().length !== 2) {
        throw new Error(`select mode must reveal one box per person, got ${boxesNow().length}`);
    }
    const cancelBtn = find(el, hasClass("roster-thread-cancel"), [])[0];
    if (!cancelBtn) throw new Error("select mode must offer a way out");
    if (createBtn.disabled !== true) {
        throw new Error("with nobody selected there is nothing to create");
    }

    // ── Leaving clears the selection ──
    //
    // A stale selection would silently build the wrong thread the next time.
    boxesNow()[0].checked = true;
    (boxesNow()[0].listeners.change || []).forEach((fn) => fn({ target: boxesNow()[0] }));
    if (createBtn.disabled !== false) throw new Error("a selection must enable creation");
    if (!text(createBtn).includes("Create with 1")) {
        throw new Error(`the button must count the selection, got "${text(createBtn)}"`);
    }
    click(cancelBtn);
    if (boxesNow().length !== 0) throw new Error("Cancel must take the checkboxes away");
    if (find(el, hasClass("roster-thread-cancel"), []).length !== 0) {
        throw new Error("Cancel must remove itself with the mode it leaves");
    }
    click(createBtn);
    if (boxesNow().some((box) => box.checked)) {
        throw new Error("re-entering select mode must start from an empty selection");
    }
    if (createBtn.disabled !== true) {
        throw new Error("Cancel must clear the selection, not just hide it");
    }
    const cancelClearsTheSelection = true;

    // ── Creating a thread consumes the People selection ──
    // The selection lives with People and the create button with Threads, so
    // the reset crosses a module boundary and is easy to lose in a refactor.
    boxesNow()[0].checked = true;
    (boxesNow()[0].listeners.change || []).forEach((fn) => fn({ target: boxesNow()[0] }));
    if (createBtn.disabled !== false) throw new Error("a selection must enable creation");
    click(createBtn);
    await drain();
    if (boxesNow().length !== 0) {
        throw new Error("creating a thread must leave select mode, not merely clear it");
    }
    if (!text(createBtn).includes("New thread")) {
        throw new Error(`after creating, the button invites again, got "${text(createBtn)}"`);
    }
    if (find(el, hasClass("roster-thread-cancel"), []).length !== 0) {
        throw new Error("creating must take Cancel away with the mode");
    }
    // ...and the selection really is empty, not merely out of sight.
    click(createBtn);
    if (createBtn.disabled !== true) {
        throw new Error("creating a thread must clear the People selection");
    }
    click(find(el, hasClass("roster-thread-cancel"), [])[0]);
    const selectionClearsAfterCreate = true;

    // A live channel_updated re-fetches the thread list.
    const channelFetches = () => apiCalls.filter((c) => c.url.startsWith("/api/channels") && (!c.init || !c.init.method)).length;
    const before = channelFetches();
    bus.publish("channel_updated", { id: "c1" });
    await drain();
    if (channelFetches() !== before + 1) throw new Error("channel_updated must refresh the thread list");

    // ── Disposers drain ──
    dispose();
    if (store.subscriberCount() !== storeBaseline) {
        throw new Error(`store leak: baseline ${storeBaseline}, now ${store.subscriberCount()}`);
    }
    if (bus.subscriberCount() !== busBaseline) {
        throw new Error(`bus leak: baseline ${busBaseline}, now ${bus.subscriberCount()}`);
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        pausedBeatsNeedBeatsStatus: true,
        usesSharedStatusLabel: true,
        searchMatchesNameAndRole: true,
        caretSurvivesRerender: true,
        selectionClearsAfterCreate,
        rowsAreCleanUntilSelectMode,
        cancelClearsTheSelection,
        disposersDrain: true,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
