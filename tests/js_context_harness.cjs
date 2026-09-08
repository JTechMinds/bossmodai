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
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModFormat", "BossModAgentStatus", "BossModSpecialty", "BossModGates",
    "BossModConsentCard", "BossModOverlays", "BossModEmptyState", "BossModTranscript", "BossModTranscriptCache", "BossModMessage",
    "BossModEventCards", "BossModConversationChrome", "BossModComposer",
    "BossModSystemReceipts", "BossModNeedShape", "BossModNeeds", "BossModNeedsBar",
    "BossModThreadArchive", "BossModThreadSource", "BossModAgentSource",
    "BossModConversation", "BossModPlaces",
    "BossModFileContent", "BossModFileForm", "BossModFileOps", "BossModFileViewer",
    "BossModMiniOffice",
    "BossModDeskOpener", "BossModDeskFiles", "BossModDeskNotes", "BossModDeskTasks", "BossModDeskActions",
    "BossModAgentApi", "BossModAgentFields", "BossModAgentFormFields",
    "BossModAgentFormAdvanced", "BossModAgentFormBindings", "BossModAgentForm",
    "BossModAgentSubmit", "BossModAgentRecovery", "BossModAgentEdit", "BossModDeskPanel",
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

// The shared file viewer renders markdown, and the vendored libraries that
// do it are not part of this harness's subject. Stubbing them keeps the
// assertion on "a note opens the ONE viewer" rather than on marked's output;
// places/files owns the rendering itself.
global.marked = { parse: (raw) => String(raw) };
global.hljs = { highlightElement() {} };
global.window.marked = global.marked;
global.window.hljs = global.hljs;
global.DOMParser = class {
    parseFromString(html) {
        const body = documentStub.createElement("body");
        body.append(documentStub.createTextNode(String(html)));
        return { body: { childNodes: body.children } };
    }
};
global.window.DOMParser = global.DOMParser;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

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

function jsonResponse(body, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(""),
    });
}

function api(url) {
    if (url.startsWith("/api/needs")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    if (url.startsWith("/api/map")) {
        if (mapFails) return jsonResponse({ detail: "unavailable" }, 503);
        return jsonResponse({ width: 28, height: 20, tiles: [], rooms: MAP_ROOMS, desks: [] });
    }
    if (/^\/api\/agents\/[^/]+$/.test(url)) {
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
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
