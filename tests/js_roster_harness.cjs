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
    // The rail asks this before acting on Escape, so that Escape inside a
    // modal belongs to the modal. Nothing in this harness opens one.
    querySelector() { return null; },
    listeners: {},
    addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
    // Was a no-op, which made "the listener is dropped on destroy" unprovable
    // — the rail now owns one, so the fake has to be able to answer.
    removeEventListener(n, fn) {
        const l = this.listeners[n] || [];
        const i = l.indexOf(fn);
        if (i !== -1) l.splice(i, 1);
    },
    get activeElement() { return activeElement; },
};
global.window = { document: global.document };
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModAvatar = BossModAvatar;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModStore = BossModStore;\n`);
eval(`${fs.readFileSync(process.argv[5], "utf8")}\n;global.BossModBus = BossModBus;\n`);
// The rails render the last-activity timestamp through the shared formatter,
// so it is the real one here — a stub would prove the column exists and
// nothing about what it says.
eval(`${fs.readFileSync(process.argv[6], "utf8")}\n;global.BossModFormat = BossModFormat;\n`);
eval(`${fs.readFileSync(process.argv[7], "utf8")}\n;global.BossModAgentStatus = BossModAgentStatus;\n`);
// The Threads half hangs a menu off its `⋯`, so the real panel is loaded
// rather than stubbed: a stub would prove a button exists and nothing about
// what opening it does.
eval(`${fs.readFileSync(process.argv[8], "utf8")}\n;global.BossModOverlayFocus = BossModOverlayFocus;\n`);
eval(`${fs.readFileSync(process.argv[9], "utf8")}\n;global.BossModOverlays = BossModOverlays;\n`);
// Both halves build their right-hand column through this one builder.
eval(`${fs.readFileSync(process.argv[10], "utf8")}\n;global.BossModRosterRowMeta = BossModRosterRowMeta;\n`);
eval(`${fs.readFileSync(process.argv[11], "utf8")}\n;global.BossModRosterPeople = BossModRosterPeople;\n`);
eval(`${fs.readFileSync(process.argv[12], "utf8")}\n;global.BossModThreadCreate = BossModThreadCreate;\n`);
// Which list the rail is showing — the header row's third owner.
eval(`${fs.readFileSync(process.argv[13], "utf8")}\n;global.BossModThreadViewMenu = BossModThreadViewMenu;\n`);
eval(`${fs.readFileSync(process.argv[14], "utf8")}\n;global.BossModRosterThreads = BossModRosterThreads;\n`);
eval(`${fs.readFileSync(process.argv[15], "utf8")}\n;global.BossModRoster = BossModRoster;\n`);

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

// 10:10 this morning, LOCAL — built from the real clock rather than written
// down, so the rendered "10:10 AM" is the same string whatever day the suite
// runs on and whatever offset it runs in.
const TODAY_AT_1010 = (() => {
    const when = new Date();
    when.setHours(10, 10, 0, 0);
    return when.toISOString();
})();

const WORLD = [
    { id: "a1", name: "Jim", role: "Engineer", color: "#3b82f6", status: "work_active", currentActivityKind: "work", x: 1, y: 1, lastMessageAt: TODAY_AT_1010 },
    // Nobody has spoken to Laura: her row gets no timestamp at all.
    { id: "a2", name: "Laura", role: "Writer", color: "#f59e0b", status: "idle", currentActivityKind: null, x: 2, y: 2, lastMessageAt: null },
];
const CHANNELS = [
    { id: "c1", name: "Launch plan", kind: "shared", status: "active", member_count: 2, members: [],
      latest_message: { content: "shipped", author_name: "Jim", created_at: "2020-03-04T12:00:00+00:00" } },
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

    // ── The last-activity column ──
    //
    // Read here, with the needs fixture cleared, so a row with a timestamp and
    // a row with neither can be told apart. The dot is exercised above.
    const metaIn = (listClass) => find(
        find(el, hasClass(listClass), [])[0], hasClass("roster-row-meta"), []);
    const timeIn = (listClass) => metaIn(listClass)
        .map((meta) => text(find(meta, hasClass("roster-time"), [])[0]).trim());

    const personTimestamps = timeIn("roster-people");
    // One row has one, the other has none — so the column is absent, not empty.
    const quietRowHasNoMeta = metaIn("roster-people").length === 1;
    if (!quietRowHasNoMeta) {
        throw new Error(`a row with nothing to say must carry no column, got `
            + `${metaIn("roster-people").length}`);
    }
    if (personTimestamps.join("|") !== "10:10 AM") {
        throw new Error(`a person row must render its last message time, got `
            + `${personTimestamps.join("|")}`);
    }
    const threadTimestamps = timeIn("roster-threads");
    if (threadTimestamps.length !== 1 || !/, 2020$/.test(threadTimestamps[0])) {
        throw new Error(`a thread row must date an old post with its year, got `
            + `${threadTimestamps.join("|")}`);
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

    // A thread row carries the SHARED group avatar, at the size a person row
    // takes. Without it the two lists had different left edges and the rail
    // read as two unrelated lists rather than one rail.
    const threadsList = find(el, hasClass("roster-threads"), [])[0];
    const groupAvatars = find(threadsList, hasClass("avatar-group"), []);
    const threadRowsCarryTheGroupAvatar = groupAvatars.length === 1
        && String(groupAvatars[0].getAttribute("class")).split(/\s+/).includes("avatar-md")
        && groupAvatars[0].getAttribute("aria-hidden") === "true";
    if (!threadRowsCarryTheGroupAvatar) {
        throw new Error(`a thread row needs the md group avatar, got ${groupAvatars.length}`
            + ` "${groupAvatars[0] && groupAvatars[0].getAttribute("class")}"`);
    }

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
    const byId = (id) => find(el, (n) => n.getAttribute("id") === id, [])[0];
    const createNow = () => byId("roster-create-thread");
    const cancelNow = () => byId("roster-cancel-select");
    // The section header's three slots: the label, the middle slot the mode
    // speaks through, and the action group pinned right.
    const middleNow = () => find(el, hasClass("roster-section-hint"), [])[0];
    const actionGroup = () => find(el, hasClass("roster-section-actions"), [])[0];
    const headerActionNames = () => find(actionGroup(), (n) => n.tagName === "BUTTON", [])
        .map((b) => b.getAttribute("aria-label"));
    const pressEscape = () => (document.listeners.keydown || [])
        .forEach((fn) => fn({ key: "Escape", preventDefault() {} }));

    if (boxesNow().length !== 0) {
        throw new Error(`people rows must be clean until select mode, got ${boxesNow().length} boxes`);
    }
    // `New thread` is the `+` on the THREADS header row: icon-only, so its
    // whole accessible name is the label it carries.
    const newThreadBtn = byId("roster-new-thread");
    if (!newThreadBtn) throw new Error("the THREADS header must carry a New thread action");
    if (newThreadBtn.getAttribute("aria-label") !== "New thread") {
        throw new Error(`an icon-only control needs its own name, got `
            + `"${newThreadBtn.getAttribute("aria-label")}"`);
    }
    if (newThreadBtn.getAttribute("aria-expanded") !== "false") {
        throw new Error("the New thread action must report the mode it opens as closed");
    }
    if (createNow()) throw new Error("Create must not exist outside select mode");
    if (cancelNow()) throw new Error("Cancel must not exist outside select mode");
    const rowsAreCleanUntilSelectMode = true;

    // Idle: one control, and an EMPTY middle slot. It used to invite —
    // "Select teammates and start a shared thread." — which truncated to
    // "Select teammat..." at rail width, read as broken, and explained a mode
    // nobody was in. The slot speaks only while there is something to say.
    const idleHeaderActions = headerActionNames();
    const idleMiddleSlot = text(middleNow()).trim();
    if (idleHeaderActions.join("|") !== "New thread") {
        throw new Error(`idle offers one header control, got ${idleHeaderActions.join("|")}`);
    }
    if (idleMiddleSlot !== "") {
        throw new Error(`the idle middle slot must say nothing, got "${idleMiddleSlot}"`);
    }

    // ── Out of select mode a row click still opens the conversation ──
    click(rowFor(el, "Jim"));
    const rowClickOpensConversationNormally = store.getState().conversationId === "a1"
        && store.getState().conversationKind === "agent";
    if (!rowClickOpensConversationNormally) {
        throw new Error(`a row click must open the conversation when not selecting, got `
            + `${store.getState().conversationId}`);
    }
    store.setState({ conversationId: null, conversationKind: null });

    // Neither state adds a row: everything the mode says and every control it
    // offers lives on the one header row that was already there.
    const standaloneRows = find(el, hasClass("roster-select-actions"), [])
        .concat(find(el, hasClass("roster-thread-hint"), []));
    if (standaloneRows.length !== 0) {
        throw new Error(`select mode must cost no extra row, got ${standaloneRows.length}`);
    }
    const hasStandaloneCreateRow = standaloneRows.length > 0;

    // Entering is a click on a real <button>, so Enter and Space reach it too.
    click(newThreadBtn);
    if (boxesNow().length !== 2) {
        throw new Error(`select mode must reveal one box per person, got ${boxesNow().length}`);
    }
    if (newThreadBtn.getAttribute("aria-expanded") !== "true") {
        throw new Error("the New thread action must report the mode it opened as open");
    }
    const createBtn = createNow();
    const cancelBtn = cancelNow();
    if (!createBtn) throw new Error("select mode must offer a way to create");
    if (!cancelBtn) throw new Error("select mode must offer a way out");
    // Cancel first, confirm last: the confirm takes the `+`'s old position at
    // the end of the row, so nothing moved further than one slot.
    const selectingHeaderActions = headerActionNames();
    if (selectingHeaderActions.join("|") !== "Cancel|Create thread") {
        throw new Error(`select mode's controls are wrong: ${selectingHeaderActions.join("|")}`);
    }
    // The `+` is out of the document while the mode it opened is open.
    if (find(el, (n) => n.getAttribute("id") === "roster-new-thread", []).length !== 0) {
        throw new Error("the `+` must leave the row it started, not sit dead in it");
    }
    const confirmDisabledAtZero = createBtn.disabled === true;
    if (!confirmDisabledAtZero) {
        throw new Error("with nobody selected there is nothing to create");
    }
    const selectingMiddleSlotAtZero = text(middleNow()).trim();
    if (selectingMiddleSlotAtZero !== "0 selected") {
        throw new Error(`the middle slot must count, got "${selectingMiddleSlotAtZero}"`);
    }

    // ── In select mode the ROW selects ──
    //
    // The 16px checkbox was the only target; clicking the name opened the
    // conversation, which is the opposite of what the mode is for. The box
    // stays as the state indicator and stops being the thing you have to hit.
    const checkboxStillRendersInSelectMode = boxesNow().length === 2;
    if (!checkboxStillRendersInSelectMode) {
        throw new Error("the checkbox is the mode's only visible state and must survive");
    }

    const jimName = rowFor(el, "Jim");
    const jimRow = jimName.parentNode;
    const jimBox = boxesNow()[0];
    click(jimName);
    const rowClickSelectsInSelectMode = jimBox.checked === true
        && text(middleNow()).trim() === "1 selected"
        && store.getState().conversationId === null
        && jimRow.getAttribute("data-selected") === "true";
    if (!rowClickSelectsInSelectMode) {
        throw new Error(`the name must toggle while selecting: checked ${jimBox.checked}`
            + ` slot "${text(middleNow()).trim()}" conversation ${store.getState().conversationId}`
            + ` row ${jimRow.getAttribute("data-selected")}`);
    }
    // One teammate is a real thread, and POST /api/channels accepts it — the
    // confirm must not wait for a second pick.
    const confirmEnabledAtOne = createBtn.disabled === false;
    if (!confirmEnabledAtOne) throw new Error("a thread of one is a thread");

    // The slot COUNTS rather than saying one fixed thing: with both rows
    // picked it reads two, and a hard-coded string would fail here.
    click(rowFor(el, "Laura"));
    if (text(middleNow()).trim() !== "2 selected") {
        throw new Error(`the count must track the selection, got "${text(middleNow()).trim()}"`);
    }
    click(rowFor(el, "Laura"));

    // The desk affordance is re-bound too, so it does not fire in this mode.
    const personAvatars = find(el, hasClass("avatar-md"), []).filter((n) => n.tagName === "BUTTON");
    if (personAvatars.length !== 2) {
        throw new Error(`each person row owns one avatar button, got ${personAvatars.length}`);
    }
    click(personAvatars[0]);
    const avatarDoesNotOpenDeskWhileSelecting = store.getState().contextMode === undefined
        && store.getState().deskAgentId === undefined
        && jimBox.checked === false
        && jimRow.getAttribute("data-selected") === "false";
    if (!avatarDoesNotOpenDeskWhileSelecting) {
        throw new Error(`the avatar must toggle rather than open the desk: mode `
            + `${store.getState().contextMode} checked ${jimBox.checked}`);
    }

    // A nested interactive control is invalid HTML and unreachable by
    // keyboard, so re-binding is the only way to make the row the target.
    const nested = (root) => find(root, (n) => n.tagName === "BUTTON", [])
        .filter((btn) => find(btn, (n) => n.tagName === "BUTTON", []).length > 0);
    if (nested(el).length !== 0) {
        throw new Error(`${nested(el).length} nested buttons while selecting`);
    }

    // ── Leaving clears the selection ──
    //
    // A stale selection would silently build the wrong thread the next time.
    boxesNow()[0].checked = true;
    (boxesNow()[0].listeners.change || []).forEach((fn) => fn({ target: boxesNow()[0] }));
    if (createBtn.disabled !== false) throw new Error("a selection must enable creation");
    const selectingMiddleSlotAtOne = text(middleNow()).trim();
    if (selectingMiddleSlotAtOne !== "1 selected") {
        throw new Error(`the middle slot must count the selection, got "${selectingMiddleSlotAtOne}"`);
    }
    click(cancelBtn);
    if (boxesNow().length !== 0) throw new Error("Cancel must take the checkboxes away");
    if (cancelNow()) throw new Error("Cancel must remove itself with the mode it leaves");
    if (headerActionNames().join("|") !== "New thread") {
        throw new Error("leaving must restore the New thread control, got "
            + headerActionNames().join("|"));
    }
    if (text(middleNow()).trim() !== "") {
        throw new Error(`leaving must empty the middle slot, got "${text(middleNow()).trim()}"`);
    }
    click(newThreadBtn);
    if (boxesNow().some((box) => box.checked)) {
        throw new Error("re-entering select mode must start from an empty selection");
    }
    if (createBtn.disabled !== true) {
        throw new Error("Cancel must clear the selection, not just hide it");
    }
    const cancelClearsTheSelection = true;

    // ── Escape leaves the mode too ──
    //
    // Every other dismissible state in the shell answers to it, and a mode
    // opened from the header should not need the mouse to close. The mode is
    // already open here, from the re-entry the section above ends on.
    boxesNow()[0].checked = true;
    (boxesNow()[0].listeners.change || []).forEach((fn) => fn({ target: boxesNow()[0] }));
    pressEscape();
    const escapeCancelsSelectMode = boxesNow().length === 0
        && headerActionNames().join("|") === "New thread";
    if (!escapeCancelsSelectMode) {
        throw new Error(`Escape must leave select mode, got ${boxesNow().length} boxes`
            + ` and ${headerActionNames().join("|")}`);
    }
    // Out of the mode the rail must not swallow Escape: it belongs to whatever
    // surface is open.
    pressEscape();
    if (headerActionNames().join("|") !== "New thread") {
        throw new Error("Escape outside select mode must change nothing");
    }
    // ...and it forgot who was picked, exactly as Cancel does. This re-entry
    // is what the creation section below runs in.
    click(newThreadBtn);
    if (createNow().disabled !== true) {
        throw new Error("Escape must clear the selection, not just hide it");
    }

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
    if (createNow() || cancelNow()) {
        throw new Error("creating must take the mode's controls away with the mode");
    }
    if (newThreadBtn.getAttribute("aria-expanded") !== "false") {
        throw new Error("after creating, the `+` must report the mode as closed again");
    }
    // ...and the selection really is empty, not merely out of sight.
    click(newThreadBtn);
    if (createNow().disabled !== true) {
        throw new Error("creating a thread must clear the People selection");
    }
    click(cancelNow());
    const selectionClearsAfterCreate = true;

    // ...and out of the mode too, where the avatar and the name go back to
    // being two separate controls with two separate jobs.
    const noNestedButtons = nested(el).length === 0;
    if (!noNestedButtons) throw new Error(`${nested(el).length} nested buttons in the rail`);

    // A live channel_updated re-fetches the thread list.
    const channelFetches = () => apiCalls.filter((c) => c.url.startsWith("/api/channels") && (!c.init || !c.init.method)).length;
    const before = channelFetches();
    bus.publish("channel_updated", { id: "c1" });
    await drain();
    if (channelFetches() !== before + 1) throw new Error("channel_updated must refresh the thread list");

    // ── Disposers drain ──
    dispose();
    const escapeListenerDrains = (document.listeners.keydown || []).length === 0;
    if (!escapeListenerDrains) {
        throw new Error(`the rail left ${(document.listeners.keydown || []).length} keydown listeners`);
    }
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
        threadRowsCarryTheGroupAvatar,
        personTimestamps,
        threadTimestamps,
        quietRowHasNoMeta,
        idleHeaderActions,
        idleMiddleSlot,
        selectingHeaderActions,
        selectingMiddleSlotAtZero,
        selectingMiddleSlotAtOne,
        hasStandaloneCreateRow,
        confirmDisabledAtZero,
        confirmEnabledAtOne,
        escapeCancelsSelectMode,
        escapeListenerDrains,
        rowClickSelectsInSelectMode,
        rowClickOpensConversationNormally,
        avatarDoesNotOpenDeskWhileSelecting,
        checkboxStillRendersInSelectMode,
        noNestedButtons,
        disposersDrain: true,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
