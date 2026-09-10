/**
 * Node harness: the context column's mode switch and its teardown.
 *
 * Invoked by tests/test_ui_context.py. Not a browser bundle.
 *
 * It mounts the real Chat place against the real modules, because the property
 * that matters — the column does not outlive a navigation away from Chat — is
 * a property of how those two are wired together, and a stub for either half
 * would prove nothing about it.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// core/markdown.js reads `marked`, `hljs` and `DOMParser`; Node has none
// of them, and this harness is not what proves the sanitiser correct.
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

// ─── Timers scheduled past the verdict ───
//
// context/agent-form-save.js hides its "Saved successfully" line three seconds
// after a save lands. That is correct in the product and is left alone — but
// this harness performs real saves now, and a pending timer keeps Node's event
// loop alive: main() finished in ~60ms and the process sat there until 3060ms,
// once per run, across every test body that spawns this file.
//
// So every timer is registered as it is scheduled and the run drops whatever
// is still pending once the verdict has been written. It ends when its work is
// done rather than when the product's last cosmetic timeout fires. Nothing is
// silenced while the run is in progress: a timer that comes due before then
// still fires, and any assertion that needed one still gets it.
const pending = new Set();
const nodeSetTimeout = global.setTimeout;
global.setTimeout = (fn, ms, ...args) => {
    const timer = nodeSetTimeout((...called) => {
        pending.delete(timer);
        return fn(...called);
    }, ms, ...args);
    pending.add(timer);
    return timer;
};
global.window.setTimeout = global.setTimeout;

function dropPendingTimers() {
    pending.forEach((timer) => clearTimeout(timer));
    pending.clear();
}

// ─── Enough of innerHTML for the agent form to be wired ───
//
// context/agent-form*.js build their markup as a string — the named markup
// exemption — and assign it. The shared fake parses no HTML, so this harness
// gives its elements a setter that creates one node per `id=` and per `name=`
// the markup declares. That is enough for the form's own wiring to find every
// control it binds, and it is honest about being a fake: it reproduces the
// form's CONTROL INVENTORY, flat, not its layout. What each control CONTAINS
// is tests/js_agent_form_harness.cjs's subject, and the nesting is nobody's.
//
// ONE relation is not layout and is reproduced: the `<form>` CONTAINS them.
// That is the difference between the host a build is staged on and the node
// that survives being published out of it, and a fake that dropped it let a
// binding scoped to the form look identical to one scoped to the emptied host
// — which is exactly the shape of the defect that reached the operator as five
// null connections. Everything declared after the form goes inside it; nothing
// deeper is claimed, because nothing deeper is under test here.
//
// The getter still escapes textContent, because BossModFormat.escapeHtml round
// trips through it and the whole form's copy depends on that.
const TAGGED = /<([a-z][a-z0-9]*)\b([^>]*)>/gi;

function stubControls(host, html) {
    TAGGED.lastIndex = 0;
    let parent = host;
    let match = TAGGED.exec(html);
    while (match) {
        const [, tag, attrs] = match;
        const id = /\bid="([^"]+)"/.exec(attrs);
        const name = /\bname="([^"]+)"/.exec(attrs);
        if (id || name) {
            const el = documentStub.createElement(tag);
            if (id) el.setAttribute("id", id[1]);
            if (name) el.setAttribute("name", name[1]);
            parent.append(el);
            if (tag.toLowerCase() === "form") parent = el;
        }
        match = TAGGED.exec(html);
    }
}

const baseCreateElement = documentStub.createElement.bind(documentStub);
documentStub.createElement = (tag) => {
    const el = baseCreateElement(tag);
    let assigned = "";
    Object.defineProperty(el, "innerHTML", {
        get() {
            if (assigned) return assigned;
            return String(this.textContent)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");
        },
        set(value) {
            assigned = String(value);
            this.replaceChildren();
            stubControls(this, assigned);
        },
        configurable: true,
    });
    return el;
};

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModFormat", "BossModAgentStatus", "BossModSpecialty", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModOverlays",
    "BossModEmptyState", "BossModTranscript", "BossModTranscriptCache", "BossModMessage",
    "BossModEventCards", "BossModTitleRename", "BossModConversationChrome", "BossModComposer",
    "BossModSystemReceipts", "BossModNeedShape", "BossModNeeds", "BossModNeedsBar",
    "BossModThreadArchive", "BossModThreadSource", "BossModAgentSource",
    "BossModConversation", "BossModPlaces",
    "BossModFileContent", "BossModFileForm", "BossModFileOps", "BossModFileViewer",
    "BossModMiniOffice",
    "BossModDeskOpener", "BossModDeskFiles", "BossModDeskNotes", "BossModDeskTasks", "BossModDeskActions",
    "BossModAgentApi", "BossModAgentTemplatesApi",
    "BossModAgentFields", "BossModAgentFormFields",
    "BossModAgentFormAdvanced", "BossModAgentFormConnections",
    "BossModAgentFormBindings", "BossModAgentFormHydrate",
    "BossModAgentForm",
    "BossModAgentSubmit", "BossModAgentRecovery", "BossModAgentFormSave",
    "BossModAgentTemplatePicker", "BossModAgentQuickConnection",
    "BossModAgentFormQuick", "BossModAgentDialogFooter",
    "BossModAgentEdit", "BossModDeskPanel",
    "BossModContextColumn", "BossModChatPlace",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

// Read off `global` rather than destructured into module-scope consts: a
// `const` here would be in its temporal dead zone while the evals above run,
// and chat-place.js calls BossModPlaces.register() at load time.
const { BossModStore, BossModBus, BossModNeeds } = global;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

// context/agent-api.js and the form's bindings call the GLOBAL request helper
// (api-auth.js patches window.fetch and every module reads it by name), so the
// dialog only runs for real if the harness provides it. Pointed at the same
// scripted API the column is injected with, so both see one world.
global.apiFetch = (...args) => api(...args);

// The floor plan GET /api/map answers with, trimmed to what the summary reads.
// The names are core/world/tilemap.py's DEFAULT_ROOMS, because the join between
// the map and the roster is BY NAME — db.get_world_state() derives
// `agent.location` from get_room_at(), which returns these same strings.
const MAP_ROOMS = [
    { id: "workspace_main", name: "Main Workspace", bounds: [1, 1, 12, 8] },
    { id: "meeting_room", name: "Meeting Room", bounds: [16, 1, 23, 8] },
    { id: "break_room", name: "Break Room", bounds: [16, 12, 23, 18] },
    { id: "hallway_main", name: "Hallway", bounds: [13, 1, 15, 18] },
    { id: "workspace_south", name: "South Workspace", bounds: [1, 12, 12, 18] },
];
// Flipped part-way through, so a later mini-office is built against a floor
// plan that will not load and the degraded path is exercised for real.
let mapFails = false;

// Jim and Laura are in one real room, which is the shape that made the old
// panel look broken: grouping by location drew ONE box for a five-room floor.
// Ada is off-map — db.get_world_state() gives her no room name — and must
// still get a seat, or the operator loses sight of her entirely.
const ROSTER = [
    { id: "a1", name: "Jim", role: "Engineer", color: "#3b82f6", status: "idle",
      description: "Keeps the build green.", done_fail_bar: "Good: tests pass. Fail: no evidence.",
      currentActivityKind: null, location: "Main Workspace" },
    { id: "a2", name: "Laura", role: "Writer", color: "#f59e0b", status: "idle",
      currentActivityKind: null, location: "Main Workspace" },
    { id: "a3", name: "Ada", role: "Analyst", color: "#10b981", status: "idle",
      currentActivityKind: null, location: null },
];

// Every desk request the harness saw, so the Notes section can be shown to ask
// for the workspace folder rather than a column that does not exist.
const requestLog = [];
// What GET /api/agents/{id}/desk?path=/me/notes answers, per agent. `null`
// means the folder is not there yet, which is a brand new agent and a 404.
const NOTES = {
    a1: [
        { name: "old.md", path: "/me/notes/old.md", is_dir: false, category: "note",
          artifact: { title: "Older thought" }, updated_at: "2026-09-01T09:00:00" },
        { name: "todo.md", path: "/me/notes/todo.md", is_dir: false, category: "note",
          artifact: null, updated_at: "2026-09-06T09:00:00" },
        { name: "archive", path: "/me/notes/archive", is_dir: true, category: "folder",
          artifact: null, updated_at: "2026-09-04T09:00:00" },
    ],
    a2: "boom",
    a3: null,
};

// The installed template library GET /api/agent-templates answers with. ONE
// row: what the QUICK layout does to the form is the subject here, not the
// picker's grouping or its filter, which tests/js_add_agent_harness.cjs owns.
// No personality hint — this harness answers /api/personalities with an empty
// list, and a hint no personality matches is a note about the fixture rather
// than anything under test.
const TEMPLATES = [{
    id: "t1", source: "catalog", pack_id: "code-auditor", source_url: null,
    category: "engineering", title: "Code Auditor",
    specialty: "Reviews claims",
    description: "Reads a diff and reports what is not true.",
    what_done_looks_like: "A checkable allow/deny exists.",
    personality_hint: null, tools_hint: ["work"],
    author_name: "JTech Minds", author_url: "https://github.com/JTechMinds",
    commit_sha: "aa11bb2ccccccccccccccccccccccccccccccccc", content_hash: "hash-1",
    installed_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
}];

// What GET /api/connections answers the agent form with. TWO, so "Set All"
// has something to fan out and what it writes is distinguishable from both the
// default and the other choice.
const CONNECTIONS = [
    { id: "c1", name: "Local", model: "llama3.1:8b", api_base_url: "http://local/v1" },
    { id: "c2", name: "Cloud", model: "gpt-4o-mini", api_base_url: "http://cloud/v1" },
];
// Flipped by the section that opens the editor while that read answers a 500
// whose body is an OBJECT and not a list — the shape that used to be assigned
// straight through and then iterated by the matrix.
let connectionsFail = false;
// Flipped by the section that opens the editor while a SIBLING read REJECTS.
// A rejection, not a 500: `Promise.all` answered one of those by rejecting the
// whole batch, which erased three healthy reads — a 500 did not, because the
// assignment it broke happened after the ones before it had already landed.
let personalitiesFail = false;
// ...and by the section that reaches the refusal with NOTHING configured,
// which is a healthy read of an empty list and not a failure at all.
let connectionsEmpty = false;
// POST /api/agents refuses by default: most of this file's dialog work clicks
// the primary to prove the click reaches the form, and a save that succeeded
// would close the dialog out from under the next assertion. Section 3d, whose
// subject is what a save WRITES, turns it on.
let createSucceeds = false;
// Every agent POST /api/agents was asked to create, in order.
const creates = [];
// Every PATCH /api/agents/{id}, in order. The create refusal is scoped to
// CREATE, and the only way to prove that scoping is to watch an edit that
// would have tripped it reach the server anyway.
const updates = [];

// The one browser API the real submit path needs that the shared fake does not
// carry. Node HAS a FormData and its constructor REFUSES an argument, so
// `new FormData(form)` threw, the save's own catch reported it as a failed
// save, and no harness could see what a save would have WRITTEN. This reads
// the fake form the way a browser reads a real one: named controls only, and
// only the checked half of a radio or checkbox group. The stub markup carries
// no `value=` attributes, so a control's value IS its property — the one
// exception is a checked checkbox, which a browser submits as "on".
global.FormData = class {
    constructor(form) {
        this.values = new Map();
        for (const el of form.querySelectorAll("input, select, textarea")) {
            const name = el.getAttribute("name");
            if (!name || el.disabled) continue;
            const type = String(el.getAttribute("type") || "").toLowerCase();
            if ((type === "checkbox" || type === "radio") && !el.checked) continue;
            const value = type === "checkbox" && !el.value ? "on" : el.value;
            this.values.set(name, String(value == null ? "" : value));
        }
    }

    get(key) {
        return this.values.has(key) ? this.values.get(key) : null;
    }
};

function jsonResponse(body, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(""),
    });
}

function api(url, init) {
    if (String(url).startsWith("/api/agent-templates")) {
        return jsonResponse(TEMPLATES);
    }
    if (String(url) === "/api/connections") {
        if (connectionsFail) return jsonResponse({ detail: "boom" }, 500);
        return jsonResponse(connectionsEmpty ? [] : CONNECTIONS);
    }
    if (String(url) === "/api/personalities") {
        if (personalitiesFail) return Promise.reject(new Error("network is down"));
        return jsonResponse([]);
    }
    if (String(url) === "/api/agents" && init && init.method === "POST") {
        creates.push(JSON.parse(init.body));
        if (!createSucceeds) return jsonResponse({ detail: "Name already taken" }, 409);
        return jsonResponse({ id: `new-${creates.length}` });
    }
    if (url.startsWith("/api/needs")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    if (url.startsWith("/api/map")) {
        if (mapFails) return jsonResponse({ detail: "unavailable" }, 503);
        return jsonResponse({ width: 28, height: 20, tiles: [], rooms: MAP_ROOMS, desks: [] });
    }
    const oneAgent = String(url).match(/^\/api\/agents\/([^/]+)$/);
    if (oneAgent) {
        if (init && init.method === "PATCH") {
            updates.push({ id: oneAgent[1], body: JSON.parse(init.body) });
            return jsonResponse({ id: oneAgent[1] });
        }
        return jsonResponse({ id: "a1", storage_key: "jim-workspace", model_work: "gpt-test" });
    }
    const desk = String(url).match(/^\/api\/agents\/([^/]+)\/desk\?path=(.*)$/);
    if (desk) {
        const agentId = desk[1];
        const path = decodeURIComponent(desk[2]);
        requestLog.push({ agentId, path });
        if (path === "/me/notes") {
            const entries = NOTES[agentId];
            if (entries === null) return jsonResponse({ detail: "Path not found" }, 404);
            if (entries === "boom") return jsonResponse({ detail: "boom" }, 500);
            return jsonResponse({ kind: "directory", path, name: "notes", breadcrumbs: [], entries });
        }
        if (path.startsWith("/me/notes/")) {
            return jsonResponse({
                kind: "file", path, name: path.split("/").pop(), breadcrumbs: [],
                artifact: null, content: "# note", truncated: false, size_bytes: 6,
                updated_at: "2026-09-06T09:00:00", binary: false,
            });
        }
        return jsonResponse({
            kind: "directory", path, name: "Desk", breadcrumbs: [], entries: [],
        });
    }
    return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve([]),
        text: () => Promise.resolve(""),
    });
}

async function main() {
    const store = BossModStore.createStore({
        place: "chat",
        placeParams: {},
        conversationId: null,
        conversationKind: null,
        contextMode: "office",
        deskAgentId: null,
        roster: [],
        threads: [],
        rosterQuery: "",
        runtimePaused: false,
        hasUsableModel: true,
        needs: [],
        needsBarEnabled: true,
        needsBarDismissed: false,
    });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const needs = BossModNeeds.createNeedsStore({ store, bus, api });
    await drain();

    // Baselines are taken with the needs store already built: it lives for the
    // life of the app, so what must come back to zero is the place's share.
    const storeBaseline = store.subscriberCount();
    const busBaseline = bus.subscriberCount();

    const placeEl = documentStub.createElement("div");
    const contextEl = documentStub.createElement("aside");
    documentStub.body.append(placeEl);
    documentStub.body.append(contextEl);

    const navigated = [];
    const ctx = {
        store,
        bus,
        api,
        needs,
        contextEl,
        navigate: (placeId) => navigated.push(placeId),
    };

    const chat = global.BossModPlaces.get("chat");
    chat.mount(placeEl, ctx);
    store.setState({ roster: ROSTER });
    await drain();

    // ─── 1. Every agent gets a seat, including the unplaced one ───

    const seats = () => contextEl.querySelectorAll(".mini-office-seat");
    const roomNames = () => contextEl.querySelectorAll(".mini-office-room-name")
        .map((node) => node.textContent);

    if (seats().length !== 3) {
        throw new Error(`every agent needs a seat, got ${seats().length}`);
    }
    if (!roomNames().includes("Unknown")) {
        throw new Error(`an off-map agent needs a real room, got rooms ${roomNames().join(", ")}`);
    }
    // ...and "Unknown" sorts last, so a real room is never buried under it.
    if (roomNames()[roomNames().length - 1] !== "Unknown") {
        throw new Error(`Unknown must sort last, got ${roomNames().join(", ")}`);
    }
    if (!seats().filter((seat) => seat.getAttribute("data-agent-id") === "a3")[0]) {
        throw new Error("the off-map agent has no seat");
    }
    const rendersUnknownRoom = true;

    // ─── 1a. The WHOLE floor draws, not only the rooms with somebody in them ───
    //
    // Two of the three agents share one room and the third is off-map, so a
    // panel that derived its rooms from occupancy would draw two boxes for a
    // five-room floor. That is what the operator saw.

    const MAPPED = MAP_ROOMS.map((room) => room.name);
    const drawsEveryMappedRoom = roomNames().join("|") === MAPPED.concat(["Unknown"]).join("|");
    if (!drawsEveryMappedRoom) {
        throw new Error(`the floor plan draws every room, in map order: ${roomNames().join(", ")}`);
    }
    // Four of the five mapped rooms hold nobody, and each says so rather than
    // rendering as a box that looks like it failed.
    const emptyLabels = () => contextEl.querySelectorAll(".mini-office-room-empty")
        .map((node) => node.textContent);
    const emptyRoomsSaySo = emptyLabels().length === 4
        && emptyLabels().every((text) => text === "Empty");
    if (!emptyRoomsSaySo) {
        throw new Error(`an empty room must say so, got ${JSON.stringify(emptyLabels())}`);
    }

    // A need on an agent pings their seat, silently.
    store.setState({ needs: [{ id: "n1", kind: "blocked", agentId: "a1", conversationId: "a1" }] });
    const jim = seats().filter((seat) => seat.getAttribute("data-agent-id") === "a1")[0];
    if (!jim.querySelector(".mini-office-ping")) {
        throw new Error("an agent with an open need must be pinged in the office summary");
    }
    if (!String(jim.getAttribute("aria-label")).includes("needs you")) {
        throw new Error("the ping is decorative; the accessible name must state the fact");
    }
    store.setState({ needs: [] });

    // ─── 1b. A floor plan that will not load degrades, and says so ───
    //
    // The roster still answers "who is around", so the panel falls back to the
    // occupied-rooms view it had before. Blanking it would lose the people
    // along with the rooms, which is the worse of the two failures.

    mapFails = true;
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    store.setState({ contextMode: "office" });
    await drain();
    const degraded = roomNames();
    const degradedError = contextEl.querySelectorAll(".context-error")
        .map((node) => node.textContent).join(" ");
    const mapFailureDegradesRatherThanBlanks = degraded.join("|") === "Main Workspace|Unknown"
        && seats().length === 3
        && degradedError.includes("Could not load the floor plan");
    if (!mapFailureDegradesRatherThanBlanks) {
        throw new Error(`a failed map must degrade, not blank: rooms ${degraded.join(", ")}`
            + ` seats ${seats().length} error "${degradedError}"`);
    }
    // Back to a working floor plan for everything below.
    mapFails = false;
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    store.setState({ contextMode: "office" });
    await drain();

    // ─── 2. A seat opens that agent's desk ───

    const ada = seats().filter((seat) => seat.getAttribute("data-agent-id") === "a3")[0];
    if (!ada) throw new Error("the off-map agent lost their seat on the rebuilt panel");
    await ada.dispatchClick();
    if (store.getState().contextMode !== "desk" || store.getState().deskAgentId !== "a3") {
        throw new Error("clicking a seat must open that agent's desk");
    }
    const seatOpensDesk = true;

    // ─── 3. The column switches modes, one view at a time ───

    if (contextEl.querySelectorAll(".mini-office").length !== 0) {
        throw new Error("the outgoing view must be unmounted before the incoming one mounts");
    }
    if (contextEl.querySelectorAll(".desk-panel").length !== 1) {
        throw new Error("desk mode must mount exactly one desk view");
    }
    store.setState({ contextMode: "office" });
    if (contextEl.querySelectorAll(".desk-panel").length !== 0) {
        throw new Error("switching back must unmount the desk view");
    }
    if (contextEl.querySelectorAll(".mini-office").length !== 1) {
        throw new Error("office mode must mount exactly one summary");
    }

    // Switching back and forth must not accumulate subscriptions. Measured in
    // the same mode at both ends: the two views hold different numbers of
    // subscriptions, so a cross-mode comparison would prove nothing.
    const officeSubscribers = store.subscriberCount();
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    store.setState({ contextMode: "office" });
    store.setState({ contextMode: "desk", deskAgentId: "a2" });
    store.setState({ contextMode: "office" });
    if (store.subscriberCount() !== officeSubscribers) {
        throw new Error(
            `mode switching leaks subscriptions: ${officeSubscribers} -> ${store.subscriberCount()}`
        );
    }
    // Switching between two agents' desks must swap the view, not stack it.
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    store.setState({ contextMode: "desk", deskAgentId: "a2" });
    if (contextEl.querySelectorAll(".desk-panel").length !== 1) {
        throw new Error("a desk-to-desk switch must leave exactly one view mounted");
    }
    store.setState({ contextMode: "office" });
    const switchesModes = true;

    // ─── 3b. Notes read the agent's workspace, not a column ───

    const notes = () => contextEl.querySelectorAll(".desk-note");
    const noteTitles = () => contextEl.querySelectorAll(".desk-note-title")
        .map((node) => node.textContent);

    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    await drain();

    // ─── 3a. The reorganised desk lost nothing ───
    //
    // Task 7 moved eight blocks: identity into one group closed by a rule, the
    // sections under labelled headers with their actions on those headers, the
    // amber contract into a disclosure, and the folder buttons and the footer
    // actions down to quiet links. "Nothing was deleted" is a claim about what
    // RENDERS, so it is read off the built panel — a source check would pass
    // while a block sat in a branch that never runs.
    const deskText = (selector) => contextEl.querySelectorAll(selector)
        .map((node) => node.textContent).join(" ");
    const sectionLabels = contextEl.querySelectorAll(".desk-section-title")
        .map((node) => node.textContent);
    const deskFields = {
        name: deskText(".desk-name").includes("Jim"),
        role: deskText(".desk-role").includes("Engineer"),
        about: deskText(".desk-about").includes("Keeps the build green."),
        status: deskText(".desk-state-pill").trim().length > 0,
        // The contract copy AND the value, inside the disclosure that now
        // holds them rather than above the task list.
        contract: contextEl.querySelectorAll(".desk-contract").length === 1
            && deskText(".desk-bar").includes("What done looks like for this agent:")
            && deskText(".desk-bar").includes("Good: tests pass."),
        tasks: sectionLabels.includes("Tasks")
            && contextEl.querySelectorAll(".desk-tasks").length === 1,
        files: sectionLabels.includes("Files")
            && contextEl.querySelectorAll(".desk-files").length === 1,
        // The desk's own facts: the workspace it was given and the model it runs.
        desk: deskText(".desk-kv").includes("Workspace")
            && deskText(".desk-kv").includes("jim-workspace")
            && deskText(".desk-kv").includes("Model"),
        notes: sectionLabels.includes("Notes")
            && contextEl.querySelectorAll(".desk-notes").length === 1,
    };
    const lost = Object.keys(deskFields).filter((field) => !deskFields[field]);
    if (lost.length) {
        throw new Error(`the desk lost ${lost.join(", ")}; sections `
            + `${sectionLabels.join("/")} footer "${deskText(".desk-kv")}"`);
    }
    // Every action that was a bordered button is still a control, just a quiet
    // one — and "See all" now belongs to the Tasks header rather than floating
    // under the list.
    const seeAll = contextEl.querySelectorAll(".desk-section-action");
    if (seeAll.length !== 1 || seeAll[0].textContent !== "See all") {
        throw new Error(`Tasks owes its header a "See all", got ${seeAll.length}`);
    }
    const footerActions = contextEl.querySelectorAll(".desk-action")
        .map((node) => node.textContent);
    if (footerActions.join("|") !== "Edit role|Diagnostics|Reset runtime|Remove") {
        throw new Error(`the footer lost an action: ${footerActions.join("|")}`);
    }
    // The contract is CLOSED until the operator asks for it.
    if (contextEl.querySelector(".desk-contract").hasAttribute("open")) {
        throw new Error("the contract must be a disclosure, not a standing alert");
    }

    const askedFor = requestLog.filter((entry) => entry.agentId === "a1" && entry.path === "/me/notes");
    const readsTheWorkspace = askedFor.length > 0;
    if (!readsTheWorkspace) {
        throw new Error(`Notes must read /me/notes, saw ${JSON.stringify(requestLog)}`);
    }
    // Newest first, the artifact title winning over the file name, and the
    // subfolder still listed rather than quietly dropped.
    if (noteTitles().join("|") !== "todo.md|archive|Older thought") {
        throw new Error(`notes must sort newest first, got ${noteTitles().join("|")}`);
    }
    const listsNewestFirst = true;

    // A note opens the ONE viewer. No second implementation, and the desk
    // browser stays where the operator left it.
    await notes()[0].dispatchClick();
    await drain();
    const opensSharedViewer = documentStub.body.querySelectorAll(".file-view-panel").length === 1;
    if (!opensSharedViewer) {
        throw new Error("clicking a note must open the shared file viewer");
    }
    BossModFileViewer.close();

    // A subfolder is handed to the desk browser, which is the thing that
    // navigates. Listing it and doing nothing would be a dead control.
    await notes()[1].dispatchClick();
    await drain();
    const folderRow = requestLog.filter((entry) => entry.path === "/me/notes/archive");
    if (!folderRow.length) throw new Error("a notes subfolder must open in the desk browser");

    // ─── An agent who has written nothing gets the empty state, not an error ───

    store.setState({ contextMode: "office" });
    store.setState({ contextMode: "desk", deskAgentId: "a3" });
    await drain();
    // Scoped to the Notes section: Tasks and Files have empty states of their
    // own, and a cross-section query would let one stand in for another.
    const notesSection = () => contextEl.querySelector(".desk-notes");
    const notesText = (selector) => notesSection().querySelectorAll(selector)
        .map((n) => n.textContent);
    const emptyCopy = notesText(".context-empty");
    const absentIsEmptyNotError = emptyCopy.includes("No notes yet")
        && notesText(".context-error").length === 0
        && notes().length === 0;
    if (!absentIsEmptyNotError) {
        throw new Error(`a 404 must render the empty state, got ${JSON.stringify(emptyCopy)}`
            + ` errors ${JSON.stringify(notesText(".context-error"))} rows ${notes().length}`);
    }
    // ...and the empty state says where notes come from, not just that there
    // are none.
    const hint = notesText(".context-hint").join(" ");
    if (!hint.includes("/me/notes")) {
        throw new Error("the empty state must name where agents write notes");
    }

    // ─── A real failure is still a real failure ───

    store.setState({ contextMode: "office" });
    store.setState({ contextMode: "desk", deskAgentId: "a2" });
    await drain();
    const errorNodes = notesText(".context-error");
    const failureSurfaces = errorNodes.some((text) => text.includes("Notes could not be loaded."))
        && notesSection().querySelectorAll("#desk-notes-retry-btn").length === 1
        && notesText(".context-empty").length === 0;
    if (!failureSurfaces) {
        throw new Error(`a 500 must surface an error with a retry, got ${JSON.stringify(errorNodes)}`);
    }
    store.setState({ contextMode: "office" });

    // ─── 3c. Hire and Edit are the same centred dialog ───
    //
    // The column hosted the form for one of the two flows and the desk swapped
    // itself out for the other, so a source check would not notice if only one
    // of them had moved. Driven from the desk's own footer control, because
    // "what the operator clicks" is the claim.
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    await drain();

    const modals = () => documentStub.body.querySelectorAll(".modal-panel");
    const wideModal = () => modals().filter(
        (node) => node.getAttribute("data-size") === "wide")[0];
    if (modals().length !== 0) throw new Error("nothing should be open yet");

    const editAction = contextEl.querySelectorAll(".desk-action")
        .filter((node) => node.textContent === "Edit role")[0];
    if (!editAction) throw new Error("the desk footer must offer Edit role");
    await editAction.dispatchClick();
    await drain();

    const opened = wideModal();
    const editOpensTheWideModal = Boolean(opened)
        && opened.getAttribute("role") === "dialog"
        && opened.getAttribute("aria-label") === "Edit role"
        && Boolean(opened.querySelector("#agent-form"))
        // The edit flow keeps its remove path.
        && Boolean(opened.querySelector("#btn-delete-agent"));
    if (!editOpensTheWideModal) {
        throw new Error(`Edit role must open the wide dialog with the form in it: `
            + `${opened && opened.getAttribute("data-size")} `
            + `form ${Boolean(opened && opened.querySelector("#agent-form"))}`);
    }
    // It floats over the app, not inside the 320px column that used to host it.
    const modalIsAttachedToTheBodyNotTheColumn =
        documentStub.body.children.indexOf(opened) !== -1
        && contextEl.querySelectorAll(".modal-panel").length === 0
        // ...and the desk it was opened from is still mounted underneath.
        && contextEl.querySelectorAll(".desk-panel").length === 1;
    if (!modalIsAttachedToTheBodyNotTheColumn) {
        throw new Error("the dialog must float over the app, leaving the desk mounted");
    }
    // The form is inside the SCROLLING body, and the dismissal outside it.
    const modalBody = opened.querySelectorAll(".modal-body")[0];
    const modalActions = opened.querySelectorAll(".modal-actions")[0];
    if (!modalBody.querySelector("#agent-form")) {
        throw new Error("the form must sit in the part that scrolls");
    }
    if (modalBody.querySelectorAll(".modal-action").length !== 0) {
        throw new Error("the dialog's own action must not scroll away with the form");
    }

    // Round four pinned the PRIMARY beside the dismissal. The operator could
    // not find `Save Changes` at the bottom of a scrolling form while Cancel
    // sat pinned and obvious. Cancel first, primary last.
    const pinnedNamesIn = (dialog) => dialog.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").map((btn) => btn.textContent);
    const editPinnedActions = pinnedNamesIn(opened);
    if (editPinnedActions.join("|") !== "Cancel|Save Changes") {
        throw new Error(`the edit dialog pins Cancel then the primary, got `
            + editPinnedActions.join("|"));
    }
    // Delete is destructive and stays in the form body, away from the primary.
    const deleteIsNotPinned = Boolean(opened.querySelector("#btn-delete-agent"))
        && modalActions.querySelector("#btn-delete-agent") === null
        && Boolean(modalBody.querySelector("#btn-delete-agent"));
    if (!deleteIsNotPinned) {
        throw new Error("Delete must stay in the form body, not beside the primary");
    }

    // Dismissing puts the desk back in front and repaints it.
    await modalActions.querySelectorAll(".modal-action")[0].dispatchClick();
    await drain();
    const closingTheModalRestoresTheDesk = modals().length === 0
        && contextEl.querySelectorAll(".desk-panel").length === 1
        && contextEl.querySelectorAll(".desk-name")
            .map((n) => n.textContent).join("").includes("Jim");
    if (!closingTheModalRestoresTheDesk) {
        throw new Error(`closing must leave the desk showing, got `
            + `${modals().length} dialogs`);
    }

    // One at a time. The rail's Hire row is reachable while a desk's Edit
    // dialog is up, and the form's identity is per render — but two stacked
    // wide modals would still fight over Escape and the focus trap.
    await editAction.dispatchClick();
    await drain();
    const first = wideModal();
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const onlyOneDialogAtATime = modals().length === 1 && wideModal() === first;
    if (!onlyOneDialogAtATime) {
        throw new Error(`a second dialog must not stack, got ${modals().length}`);
    }
    await first.querySelectorAll(".modal-action")[0].dispatchClick();
    await drain();

    // Creating is the SAME dialog, minus the remove path — there is nothing to
    // remove yet — and it opens on the PICKER: step one has no form, so it can
    // have no Create Agent either.
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const hire = wideModal();
    const hireOpensTheWideModal = Boolean(hire)
        && hire.getAttribute("aria-label") === "Add agent"
        && hire.querySelector("#agent-form") === null
        && Boolean(hire.querySelector("#agent-pick-blank"))
        && hire.querySelector("#btn-delete-agent") === null;
    if (!hireOpensTheWideModal) {
        throw new Error(`Add agent must open on the picker: `
            + `${hire && hire.getAttribute("aria-label")} `
            + `form ${Boolean(hire && hire.querySelector("#agent-form"))}`);
    }
    const stepOnePinned = pinnedNamesIn(hire);
    if (stepOnePinned.join("|") !== "Browse marketplace|Cancel") {
        throw new Error(`step one offers the marketplace and Cancel, got `
            + stepOnePinned.join("|"));
    }

    // Picking Blank builds the form in the same body and swaps the footer.
    await hire.querySelector("#agent-pick-blank").dispatchClick();
    await drain();
    const stepTwoPinned = pinnedNamesIn(hire);
    if (stepTwoPinned.join("|") !== "Back|Cancel|Create Agent") {
        throw new Error(`step two pins Back, Cancel and the primary, got `
            + stepTwoPinned.join("|"));
    }
    if (!hire.querySelector("#agent-form")) {
        throw new Error("picking a cell must build the form in the same dialog");
    }

    // ── The pinned primary submits a form it is not inside ──
    //
    // A submit button moved out of its form stops submitting it, which is why
    // round three left this one at the bottom of the scroll. `form="agent-form"`
    // is the standard answer, and this proves the CLICK rather than the markup:
    // the button is outside the form and the form's own handler still runs.
    const primary = hire.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button")[2];
    const primaryCarriesFormAttribute = primary.getAttribute("form");
    const primaryIsSubmitType = primary.getAttribute("type") === "submit";
    if (!primaryIsSubmitType || primaryCarriesFormAttribute !== "agent-form") {
        throw new Error(`the pinned primary must be the form's submit button, got `
            + `type ${primary.getAttribute("type")} form ${primaryCarriesFormAttribute}`);
    }
    // The id the suite pins moved with the button; two elements cannot share
    // it, and the one that owns it must be THIS button — context/agent-edit.js
    // looks it up by id from the document to drive the busy label, and would
    // silently find nothing if the id had stayed behind in the form.
    const submitIdCount = documentStub.querySelectorAll("#agent-form-submit").length;
    if (submitIdCount !== 1) {
        throw new Error(`exactly one element owns #agent-form-submit, got ${submitIdCount}`);
    }
    if (documentStub.querySelector("#agent-form-submit") !== primary) {
        throw new Error("#agent-form-submit must be the pinned primary itself");
    }

    let formSubmits = 0;
    hire.querySelector("#agent-form").addEventListener("submit", () => { formSubmits += 1; });
    await primary.dispatchClick();
    await drain();
    const pinnedPrimarySubmitsTheForm = formSubmits === 1;
    // ...and the click alone does not dismiss. The form's handler owns the
    // outcome — including the seed-legibility clamp, which REFUSES a save — so
    // a dialog that closed here would discard a draft the form just rejected.
    const pinnedPrimaryDoesNotCloseTheDialog = modals().length === 1;
    if (!pinnedPrimarySubmitsTheForm) {
        throw new Error(`the pinned button must submit the form it names, got `
            + `${formSubmits} submits`);
    }
    if (!pinnedPrimaryDoesNotCloseTheDialog) {
        throw new Error("the dialog must outlive the click; the form decides");
    }

    // Cancel is the SECOND action on step two: the first is Back, which must
    // leave the dialog open with the draft in it.
    const stepTwoButtons = hire.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button");
    await stepTwoButtons[0].dispatchClick();
    await drain();
    if (modals().length !== 1) throw new Error("Back must not close the dialog");
    if (pinnedNamesIn(hire).join("|") !== "Browse marketplace|Cancel") {
        throw new Error("Back must return to step one");
    }
    await hire.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button")[1].dispatchClick();
    await drain();
    if (modals().length !== 0) throw new Error("the hire dialog must close");
    store.setState({ contextMode: "office" });

    // ─── 3d. "Set All" reaches the five it writes to, AFTER publication ───
    //
    // The fan-out is bound while the form is on a DETACHED stage, and it runs
    // after renderInline has published — which MOVES the <form> out of that
    // stage and empties it. A listener that re-queries the host it was handed
    // then searches an emptied node for the rest of its life: every select
    // came back null, `if (sel)` swallowed it, and the quick create path —
    // where model_all is promoted to the ONE required AI question and the five
    // are swept behind a collapsed disclosure — wrote five nulls, no
    // connection_id and no api_base_url, and said "Saved successfully".
    //
    // Driven through the real builder, the real publish AND the real submit
    // path: every stub in that chain is a place this hid behind once already.
    createSucceeds = true;
    creates.length = 0;
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const create = wideModal();
    await create.querySelector("#agent-pick-blank").dispatchClick();
    await drain();
    const setAll = create.querySelector('select[name="model_all"]');
    if (!setAll) throw new Error("the matrix must offer Set All when a connection exists");
    setAll.value = "c2";
    for (const fn of [...(setAll.listeners.change || [])]) await fn({ target: setAll });
    const MODEL_KEYS = global.BossModAgentFields.MODEL_TYPES.map((type) => type.key);
    const fannedOut = MODEL_KEYS.map((key) => {
        const sel = create.querySelector(`select[name="${key}"]`);
        return sel ? sel.value : null;
    });
    const setAllFansOutAfterPublish = fannedOut.every((value) => value === "c2");
    if (!setAllFansOutAfterPublish) {
        throw new Error(`Set All must reach all five selects, got ${JSON.stringify(fannedOut)}`);
    }
    // ...and what the operator answered is what the server is told. The five
    // nulls were only ever visible here.
    create.querySelector('input[name="name"]').value = "Fanned";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const written = creates[creates.length - 1] || {};
    const theFanOutIsWhatIsSaved = creates.length === 1
        && MODEL_KEYS.every((key) => written[key] === "gpt-4o-mini")
        && written.connection_id === "c2"
        && written.api_base_url === "http://cloud/v1";
    if (!theFanOutIsWhatIsSaved) {
        throw new Error(`the save must carry the fanned-out connection, got `
            + `${creates.length} creates ${JSON.stringify(written)}`);
    }
    createSucceeds = false;
    if (modals().length !== 0) throw new Error("a successful create must close the dialog");
    store.setState({ contextMode: "office", deskAgentId: null });
    await drain();

    // ─── 3e. A connections read that answers an ERROR still renders a form ───
    //
    // loadFormData is documented to degrade to empty lists so the form still
    // renders with its "no connections configured" link to Settings. It had no
    // `res.ok` and no Array.isArray, so a 500's `{detail}` object was assigned
    // straight through and the matrix — which iterates what it is handed —
    // threw `connections.map is not a function` from inside the renderer. The
    // operator was then told the whole editor had failed to load, which is a
    // different and much worse story than "you have none configured".
    connectionsFail = true;
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    await drain();
    contextEl.querySelectorAll(".desk-action")
        .filter((node) => node.textContent === "Edit role")[0].dispatchClick();
    await drain();
    const degradedForm = wideModal();
    const feedbackLine = degradedForm.querySelector("#agent-save-feedback");
    const degradedPrimary = documentStub.querySelector("#agent-form-submit");
    const aFailedConnectionsReadStillRendersTheForm =
        Boolean(degradedForm.querySelector("#agent-form"))
        && Boolean(degradedForm.querySelector("#btn-goto-connections"))
        && !degradedForm.textContent.includes("The agent editor failed to load.");
    if (!aFailedConnectionsReadStillRendersTheForm) {
        throw new Error("a 500 from /api/connections must degrade the form, not replace it");
    }
    // The SAVE is a different decision and is still refused, with the reason
    // named as the button's description — and the keyboard handed to it,
    // because the button that had it is now disabled and cannot take it back.
    const theBlockedPrimaryHandsOverTheKeyboard = degradedPrimary.disabled === true
        && degradedPrimary.getAttribute("aria-describedby") === "agent-save-feedback"
        && feedbackLine.getAttribute("role") === "status"
        && feedbackLine.getAttribute("aria-live") === "polite"
        && documentStub.activeElement === feedbackLine;
    if (!theBlockedPrimaryHandsOverTheKeyboard) {
        throw new Error(`a withheld primary must say why and hand over focus: `
            + `disabled ${degradedPrimary.disabled} `
            + `describedby ${degradedPrimary.getAttribute("aria-describedby")} `
            + `focus ${documentStub.activeElement === feedbackLine}`);
    }
    await degradedForm.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((b) => b.textContent === "Cancel").dispatchClick();
    await drain();
    connectionsFail = false;
    store.setState({ contextMode: "office" });

    // ─── 3e². A read that REJECTS costs its OWN list and nothing else ───
    //
    // The four dependency reads shared one `Promise.all` and one catch, so a
    // rejected request — a network error, an abort — took the three that had
    // answered down with it. `/api/personalities` failing therefore emptied
    // `connections`, and an operator with two configured was shown "No
    // connections configured. Add one in Settings", no matrix to choose from,
    // and a LIVE primary: `readConnections` is a separate, healthy call, so
    // nothing blocked the save and nothing said anything was wrong. The only
    // way out was closing the dialog and opening it again, which nothing said
    // either. A 500 never produced this — the assignment happens before the
    // throw — so the read here rejects.
    //
    // Real builder, real publish, real submit: what the operator is told has
    // to be read off the form they were actually given.
    personalitiesFail = true;
    createSucceeds = true;
    creates.length = 0;
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const sibling = wideModal();
    await sibling.querySelector("#agent-pick-blank").dispatchClick();
    await drain();
    const siblingForm = sibling.querySelector("#agent-form");
    const siblingPrimary = documentStub.querySelector("#agent-form-submit");
    const siblingNotice = sibling.querySelector("#agent-form-degraded");
    const aRejectedSiblingKeepsTheOtherReads =
        // The connections read landed, so the matrix it feeds is on screen and
        // the "you have none" empty state is not.
        Boolean(siblingForm.querySelector('select[name="model_all"]'))
        && sibling.querySelector("#btn-goto-connections") === null
        && siblingPrimary.disabled === false
        // ...and the read that DID fail is named, so the empty personality
        // list is not left standing as a statement about their configuration.
        && Boolean(siblingNotice)
        && siblingNotice.textContent.includes("your personalities")
        && siblingNotice.textContent.includes("not because you have none")
        && !siblingNotice.textContent.includes("your AI connections");
    if (!aRejectedSiblingKeepsTheOtherReads) {
        throw new Error(`a rejected sibling read must cost only its own list: `
            + `matrix ${Boolean(siblingForm.querySelector('select[name="model_all"]'))} `
            + `settings-link ${Boolean(sibling.querySelector("#btn-goto-connections"))} `
            + `notice "${siblingNotice ? siblingNotice.textContent : ""}"`);
    }
    // ...and the connection the matrix offers really is savable, so "close it
    // and open it again" is not the operator's only exit after all.
    const siblingSetAll = siblingForm.querySelector('select[name="model_all"]');
    siblingSetAll.value = "c1";
    for (const fn of [...(siblingSetAll.listeners.change || [])]) await fn({ target: siblingSetAll });
    siblingForm.querySelector('input[name="name"]').value = "Sibling";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const aDegradedFormStillSaves = creates.length === 1
        && creates[0].connection_id === "c1"
        && creates[0].name === "Sibling"
        && modals().length === 0;
    if (!aDegradedFormStillSaves) {
        throw new Error(`the degraded form must still save, got ${creates.length} `
            + `creates ${JSON.stringify(creates[0] || {})}`);
    }
    personalitiesFail = false;
    createSucceeds = false;
    store.setState({ contextMode: "office", deskAgentId: null });
    await drain();

    // ─── 3f. The quick layout: the guard, and the fan-out it lets through ───
    //
    // Section 3d drives Blank, which is the FULL form — `model_all` is a
    // convenience there and nothing about it is required. The layout a
    // TEMPLATE gets is the one that produced a connectionless agent twice:
    // `model_all` promoted to the ONE required AI question, the five selects
    // it writes to swept behind a collapsed disclosure, and buildSubmitData
    // reading those five and never `model_all`.
    //
    // The rule this pins is the corrected one (spec 8.3): `required` tracks
    // whether the matrix has been ANSWERED, never whether the disclosure is
    // open. The old rule dropped it the moment that panel was expanded — and
    // the panel is where the template's specialty, description and what-done
    // live, so opening it to READ them disarmed the guard, and Create then
    // wrote five nulls over "Saved successfully".
    //
    // Real builder, real publish, real quick layout, real buildSubmitData,
    // real POST: every one of those is stubbed in
    // tests/js_add_agent_harness.cjs, and each stub is a place this class of
    // defect has hidden. What the attribute BUYS — a refused submit — is
    // native constraint validation, which the shared fake does not implement;
    // the attribute itself is therefore what is read here.
    createSucceeds = true;
    creates.length = 0;
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const quickDialog = wideModal();
    const card = quickDialog.querySelectorAll(".picker-card")[0];
    if (!card) throw new Error("the picker must offer the installed template");
    await card.dispatchClick();
    await drain();
    const quickForm = quickDialog.querySelector("#agent-form");
    const lifted = quickForm.querySelector('select[name="model_all"]');
    const disclosure = quickForm.querySelector(".quick-disclosure");
    if (!lifted || !disclosure) {
        throw new Error("picking a template must rearrange the form into its quick state");
    }
    // The fake has no <details> behaviour and dispatches nothing on its own,
    // so a toggle is spelled out: the attribute, then whatever is listening
    // for it. With the old rule that listener is what disarmed the guard.
    const toggle = async (open) => {
        if (open) disclosure.setAttribute("open", "");
        else disclosure.removeAttribute("open");
        for (const fn of [...(disclosure.listeners.toggle || [])]) await fn({ target: disclosure });
    };
    const answer = async (control, value) => {
        control.value = value;
        for (const fn of [...(control.listeners.change || [])]) await fn({ target: control });
    };

    const theQuickLayoutAsksForAConnection = lifted.hasAttribute("required")
        && Boolean(quickDialog.querySelector(".quick-provenance-text"))
        // ...and the five it writes to really are behind the panel, which is
        // what makes the lifted select the only visible answer.
        && disclosure.querySelector('select[name="model_work"]') !== null
        && quickForm.querySelector('input[name="role"]').value === "Reviews claims";
    if (!theQuickLayoutAsksForAConnection) {
        throw new Error("a picked template must leave one required AI question in front");
    }

    // Opened to read what the template answered, then closed again. Nothing
    // else touched — this is the invited interaction, and it used to be
    // accepted as the operator's answer to a question they were never asked.
    await toggle(true);
    await toggle(false);
    const readingTheDisclosureKeepsTheGuard = lifted.hasAttribute("required");

    // An answer in the matrix is what releases it — any one of the five, so
    // an operator setting them by hand is not blocked by the select above.
    await answer(quickForm.querySelector('select[name="model_reasoning"]'), "c1");
    const answeringTheMatrixReleasesTheGuard = !lifted.hasAttribute("required");
    // ...and clearing them all back to None re-arms it. Live in both
    // directions, which a one-way flip could never be.
    await answer(quickForm.querySelector('select[name="model_reasoning"]'), "");
    const clearingTheMatrixRearmsTheGuard = lifted.hasAttribute("required");

    // The fan-out path: answering the lifted select itself writes the five
    // FROM SCRIPT, which fires no change event of their own — so this is also
    // the proof that the guard sees a programmatic answer.
    await answer(lifted, "c2");
    const fannedOutInQuick = MODEL_KEYS.map(
        (key) => quickForm.querySelector(`select[name="${key}"]`).value);
    const theQuickFanOutReleasesTheGuard = fannedOutInQuick.every((value) => value === "c2")
        && !lifted.hasAttribute("required");
    if (!theQuickFanOutReleasesTheGuard) {
        throw new Error(`the lifted select must reach all five, got `
            + JSON.stringify(fannedOutInQuick));
    }
    // ...and what it wrote is what the server is told. This is the assertion
    // nobody was making: the whole point of the quick layout is that these
    // five carry the connection, and they are the only thing the save reads.
    quickForm.querySelector('input[name="name"]').value = "Quick";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const quickWritten = creates[creates.length - 1] || {};
    const theQuickCreateSavesTheConnection = creates.length === 1
        && MODEL_KEYS.every((key) => quickWritten[key] === "gpt-4o-mini")
        && quickWritten.connection_id === "c2"
        && quickWritten.api_base_url === "http://cloud/v1"
        && quickWritten.name === "Quick"
        // The template's own fields travelled with it.
        && quickWritten.role === "Reviews claims";
    if (!theQuickCreateSavesTheConnection) {
        throw new Error(`the quick create must carry the fanned-out connection, got `
            + `${creates.length} creates ${JSON.stringify(quickWritten)}`);
    }
    createSucceeds = false;
    if (modals().length !== 0) throw new Error("a successful create must close the dialog");
    store.setState({ contextMode: "office", deskAgentId: null });
    await drain();

    // ─── 3g. The fifth route: refused at the create, not at a control ───
    //
    // Answer the lifted AI select — the fan-out fills the five and the guard
    // releases — then open "Review & customise" and put all five back to None.
    // The guard re-arms, and it changes nothing: the control it sits on was
    // ANSWERED and still holds that answer, so `required` is satisfied, native
    // validation passes, and buildSubmitData writes five nulls over "Saved
    // successfully". That is the fifth UI route to a connectionless agent; each
    // of the four before it was fixed at the control that exposed it and a new
    // one appeared. So the invariant moved to what would actually be SENT
    // (spec 8.3), and this drives the route end to end against it: real
    // builder, real publish, real applyQuickLayout, real buildSubmitData.
    //
    // The POST is left SUCCEEDING for this section on purpose. A refusal proven
    // against an endpoint that refuses anyway proves nothing.
    createSucceeds = true;
    creates.length = 0;
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const refusal = wideModal();
    await refusal.querySelectorAll(".picker-card")[0].dispatchClick();
    await drain();
    const refusedForm = refusal.querySelector("#agent-form");
    const refusedLifted = refusedForm.querySelector('select[name="model_all"]');
    const refusedPanel = refusedForm.querySelector(".quick-disclosure");
    if (!refusedLifted || !refusedPanel) {
        throw new Error("the template pick must produce the quick layout again");
    }
    await answer(refusedLifted, "c2");
    refusedPanel.setAttribute("open", "");
    for (const fn of [...(refusedPanel.listeners.toggle || [])]) await fn({ target: refusedPanel });
    for (const key of MODEL_KEYS) {
        await answer(refusedForm.querySelector(`select[name="${key}"]`), "");
    }
    // The hole, spelled out before it is closed: the guard is armed again and
    // the submit still passes validation, because `required` asks the lifted
    // select for A VALUE and it has one.
    const theRearmedGuardIsAlreadySatisfied = refusedLifted.hasAttribute("required")
        && refusedLifted.value === "c2";
    if (!theRearmedGuardIsAlreadySatisfied) {
        throw new Error(`the route under test needs an armed-but-satisfied guard, got `
            + `required ${refusedLifted.hasAttribute("required")} value ${refusedLifted.value}`);
    }
    refusedForm.querySelector('input[name="name"]').value = "Connectionless";
    refusedForm.querySelector('input[name="role"]').value = "Edited by hand";
    const refusedPrimary = documentStub.querySelector("#agent-form-submit");
    await refusedPrimary.dispatchClick();
    await drain();
    const refusalLine = refusal.querySelector("#agent-save-feedback");
    const theFiveNullCreateIsRefused = creates.length === 0
        && modals().length === 1
        // Told what is missing, and where to answer it ON THIS PATH. The two
        // controls named are the two the quick layout leaves reachable: the
        // lifted AI field beside Name, and the disclosure the matrix went
        // behind. "AI Connections" is NOT one of them — that heading is inside
        // the collapsed disclosure here, and with no connection configured
        // liftNoConnections removes the box that carried it outright. A
        // refusal naming a control the operator cannot see is a dead end
        // wearing the clothes of a gate, so this pins the absence too.
        && refusalLine.textContent.includes("no AI connection")
        && refusalLine.textContent.includes("Choose one in the AI field above")
        && refusalLine.textContent.includes("Review & customise")
        && !refusalLine.textContent.includes("AI Connections")
        // Settings is not this operator's next action either: they have two
        // connections configured, which is why the lifted field has a select.
        && !refusalLine.textContent.includes("Settings")
        // Announced where it changes: the one live region the editor reports
        // through, unchanged — a refusal does not get a second mechanism.
        && refusalLine.getAttribute("role") === "status"
        && refusalLine.getAttribute("aria-live") === "polite"
        // The draft is the operator's. A refusal that cleared it would cost
        // them everything they typed on the way to being refused.
        && refusedForm.querySelector('input[name="name"]').value === "Connectionless"
        && refusedForm.querySelector('input[name="role"]').value === "Edited by hand"
        // ...and the button is theirs again, or the correction cannot be made.
        && refusedPrimary.disabled === false;
    if (!theFiveNullCreateIsRefused) {
        throw new Error(`a five-null create must be refused with the draft kept, got `
            + `${creates.length} creates, ${modals().length} modals, `
            + `disabled ${refusedPrimary.disabled}, line "${refusalLine.textContent}"`);
    }
    // A gate, not a dead end: answer it and the same click goes through.
    await answer(refusedLifted, "c2");
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const theCorrectedCreateGoesThrough = creates.length === 1
        && MODEL_KEYS.every((key) => creates[0][key] === "gpt-4o-mini")
        && creates[0].name === "Connectionless"
        && modals().length === 0;
    if (!theCorrectedCreateGoesThrough) {
        throw new Error(`the corrected create must go through, got ${creates.length} `
            + `creates ${JSON.stringify(creates[0] || {})}`);
    }

    // The SAME refusal on the BLANK path, where the matrix is in place and
    // neither of the quick layout's two controls exists. Every wording this
    // message has had was one sentence, and each was true on one of these two
    // paths and pointing at something hidden or absent on the other. So the
    // form says which shape it is in and the handler picks; asserted per path,
    // because a single substring both variants satisfy would pin nothing.
    creates.length = 0;
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const blank = wideModal();
    await blank.querySelector("#agent-pick-blank").dispatchClick();
    await drain();
    const blankForm = blank.querySelector("#agent-form");
    if (!blankForm.querySelector('select[name="model_all"]')
        || blankForm.querySelector(".quick-disclosure")) {
        throw new Error("Blank must give the full form, with the matrix in place");
    }
    blankForm.querySelector('input[name="name"]').value = "Blank and connectionless";
    const blankPrimary = documentStub.querySelector("#agent-form-submit");
    await blankPrimary.dispatchClick();
    await drain();
    const blankLine = blank.querySelector("#agent-save-feedback");
    const theBlankRefusalNamesTheMatrix = creates.length === 0
        && modals().length === 1
        && blankLine.textContent.includes("no AI connection")
        // What is on screen here: the matrix's own heading, and — because a
        // blank form is also where an operator with NO connections lands — the
        // link to Settings under it.
        && blankLine.textContent.includes("Choose one under AI Connections")
        && blankLine.textContent.includes("Settings")
        // ...and neither of the quick layout's controls, which are not here.
        && !blankLine.textContent.includes("AI field")
        && !blankLine.textContent.includes("Review & customise")
        && blankForm.querySelector('input[name="name"]').value === "Blank and connectionless"
        && blankPrimary.disabled === false;
    if (!theBlankRefusalNamesTheMatrix) {
        throw new Error(`the blank path's refusal must name the matrix that is on `
            + `screen, got ${creates.length} creates, line "${blankLine.textContent}"`);
    }
    await blank.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((b) => b.textContent === "Cancel").dispatchClick();
    await drain();

    // ...and the third shape: a template picked with NOTHING configured. The
    // matrix built a link to Settings and no select, liftNoConnections brought
    // that link up beside Name and removed the box the "AI Connections"
    // heading lived in — so that heading is not in the document at all here,
    // and the two controls the template path names do not exist either. The
    // operator's only next action is Settings, and the field in front of them
    // is the one that links there. An empty list is a HEALTHY read, so the
    // save is not blocked by CONNECTIONS_FAILED and this invariant is what the
    // create meets.
    connectionsEmpty = true;
    creates.length = 0;
    global.BossModAgentEdit.openAgentModal({ store });
    await drain();
    const bare = wideModal();
    await bare.querySelectorAll(".picker-card")[0].dispatchClick();
    await drain();
    const bareForm = bare.querySelector("#agent-form");
    if (bareForm.querySelector('select[name="model_all"]').value !== ""
        || !bareForm.querySelector(".quick-disclosure")) {
        throw new Error("a template with no connections must give the unanswerable shape");
    }
    bareForm.querySelector('input[name="name"]').value = "Nothing to answer with";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const bareLine = bare.querySelector("#agent-save-feedback");
    const theUnconfiguredRefusalSendsThemToSettings = creates.length === 0
        && modals().length === 1
        && bareLine.textContent.includes("no AI connection")
        && bareLine.textContent.includes("Add a connection in Settings")
        && bareLine.textContent.includes("the AI field above links there")
        // Neither of the other two shapes' controls: the heading was removed
        // with its box, and there is nothing under the disclosure to choose.
        && !bareLine.textContent.includes("AI Connections")
        && !bareLine.textContent.includes("Review & customise")
        && !bareLine.textContent.includes("Choose one");
    if (!theUnconfiguredRefusalSendsThemToSettings) {
        throw new Error(`an unconfigured refusal must send them to Settings, got `
            + `${creates.length} creates, line "${bareLine.textContent}"`);
    }
    await bare.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((b) => b.textContent === "Cancel").dispatchClick();
    await drain();
    connectionsEmpty = false;
    createSucceeds = false;
    store.setState({ contextMode: "office", deskAgentId: null });
    await drain();

    // ─── 3h. The refusal is CREATE-only; an edit with five nulls still saves ───
    //
    // An agent with no connection is a real row: the roster predates the
    // invariant, and a connection can be deleted out from under one. Refusing
    // that save would trap the operator in a dialog they cannot leave without
    // losing every other edit they came to make — so EDIT stays permissive and
    // the guarantee sits only where the agent is brought into being.
    updates.length = 0;
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    await drain();
    contextEl.querySelectorAll(".desk-action")
        .filter((node) => node.textContent === "Edit role")[0].dispatchClick();
    await drain();
    const editing = wideModal();
    const editingForm = editing.querySelector("#agent-form");
    for (const key of MODEL_KEYS) {
        editingForm.querySelector(`select[name="${key}"]`).value = "";
    }
    editingForm.querySelector('input[name="name"]').value = "Jim";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const anEditWithNoConnectionStillSaves = updates.length === 1
        && updates[0].id === "a1"
        && MODEL_KEYS.every((key) => updates[0].body[key] === null)
        && modals().length === 0;
    if (!anEditWithNoConnectionStillSaves) {
        throw new Error(`an edit must never be refused for having no connection, got `
            + `${updates.length} updates ${JSON.stringify(updates[0] || {})} `
            + `${modals().length} modals`);
    }
    store.setState({ contextMode: "office", deskAgentId: null });
    await drain();

    // ─── 4. Navigating away from Chat takes the column with it ───

    chat.unmount();
    await drain();

    if (contextEl.children.length !== 0) {
        throw new Error("unmounting Chat must clear the context element");
    }
    if (store.subscriberCount() !== storeBaseline) {
        throw new Error(`store leak: baseline ${storeBaseline}, now ${store.subscriberCount()}`);
    }
    if (bus.subscriberCount() !== busBaseline) {
        throw new Error(`bus leak: baseline ${busBaseline}, now ${bus.subscriberCount()}`);
    }

    // A second mount/unmount cycle returns to the same baseline.
    chat.mount(placeEl, ctx);
    await drain();
    chat.unmount();
    await drain();
    if (store.subscriberCount() !== storeBaseline || bus.subscriberCount() !== busBaseline) {
        throw new Error("a second Chat cycle must return to the same baseline");
    }
    const drainsOnDestroy = true;

    process.stdout.write(JSON.stringify({
        ok: true,
        switchesModes,
        drainsOnDestroy,
        rendersUnknownRoom,
        drawsEveryMappedRoom,
        emptyRoomsSaySo,
        mapFailureDegradesRatherThanBlanks,
        seatOpensDesk,
        deskFields,
        readsTheWorkspace,
        listsNewestFirst,
        opensSharedViewer,
        absentIsEmptyNotError,
        failureSurfaces,
        editOpensTheWideModal,
        hireOpensTheWideModal,
        modalIsAttachedToTheBodyNotTheColumn,
        closingTheModalRestoresTheDesk,
        onlyOneDialogAtATime,
        stepOnePinned,
        stepTwoPinned,
        editPinnedActions,
        primaryCarriesFormAttribute,
        primaryIsSubmitType,
        submitIdCount,
        deleteIsNotPinned,
        pinnedPrimarySubmitsTheForm,
        pinnedPrimaryDoesNotCloseTheDialog,
        setAllFansOutAfterPublish,
        theFanOutIsWhatIsSaved,
        aFailedConnectionsReadStillRendersTheForm,
        theBlockedPrimaryHandsOverTheKeyboard,
        theQuickLayoutAsksForAConnection,
        readingTheDisclosureKeepsTheGuard,
        answeringTheMatrixReleasesTheGuard,
        clearingTheMatrixRearmsTheGuard,
        theQuickFanOutReleasesTheGuard,
        theQuickCreateSavesTheConnection,
        theRearmedGuardIsAlreadySatisfied,
        theFiveNullCreateIsRefused,
        theBlankRefusalNamesTheMatrix,
        theUnconfiguredRefusalSendsThemToSettings,
        theCorrectedCreateGoesThrough,
        aRejectedSiblingKeepsTheOtherReads,
        aDegradedFormStillSaves,
        anEditWithNoConnectionStillSaves,
    }));
    // The verdict is the last thing this harness has to say; a timer still
    // pending behind it is the product's own cosmetic cleanup, not work.
    dropPendingTimers();
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
