/**
 * Node harness: the context column, the desk modal, and their teardown.
 *
 * Invoked by tests/test_ui_context.py. Not a browser bundle.
 *
 * It mounts the real Chat place against the real modules, because the property
 * that matters — the column does not outlive a navigation away from Chat — is
 * a property of how those two are wired together, and a stub for either half
 * would prove nothing about it. The desk is the real desk dialog
 * (context/desk-dialog.js), injected as `ctx.openDesk` the way the shell
 * injects it, so every door this drives opens the modal the app opens.
 */
const fs = require("fs");
const { mock } = require("node:test");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// core/markdown.js reads `marked`, `hljs` and `DOMParser`; Node has none
// of them, and this harness is not what proves the sanitiser correct.
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

// Exercise debounce deadlines without repeating seconds of wall-clock waits
// in every test that runs this scenario. Promise turns and calendar dates stay
// real; only setTimeout/clearTimeout use the clock we advance below.
mock.timers.enable({ apis: ["setTimeout"] });
global.window.setTimeout = global.setTimeout;
global.window.clearTimeout = global.clearTimeout;

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
    "BossModApi", "BossModDom", "BossModMarkdown", "BossModClampedMarkdown", "BossModFactList", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModOperatorInvalidate", "BossModFormat", "BossModAgentStatus", "BossModSpecialty", "BossModCommunication", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuSelect",
    "BossModFileListing",
    "BossModEmptyState", "BossModTranscript", "BossModTranscriptCache", "BossModMessage",
    "BossModEventCards", "BossModTitleRename", "BossModChromeMenu", "BossModConversationChrome", "BossModDesktopClipboard", "BossModComposerAttachments", "BossModComposer",
    "BossModSystemReceipts", "BossModNeedShape", "BossModNeedCoalesce", "BossModNeeds", "BossModNeedsBar",
    "BossModThreadArchive", "BossModThreadSeat", "BossModThreadRequests", "BossModThreadSource", "BossModAgentSource",
    "BossModConversationFocus", "BossModConversation", "BossModPlaces",
    "BossModFileContent", "BossModFileForm", "BossModFileOps", "BossModFileViewer",
    "BossModMiniOffice",
    "BossModDeskOpener", "BossModDeskFiles", "BossModDeskNotes", "BossModDeskTasks", "BossModDeskActions",
    "BossModAgentApi", "BossModAgentTemplatesApi",
    "BossModAgentFields", "BossModAgentFormFields",
    "BossModAgentFormAdvanced", "BossModAgentFormConnections",
    "BossModAgentFormBindings", "BossModAgentFormHydrate",
    "BossModAgentForm",
    "BossModAgentSubmit", "BossModAgentRecovery", "BossModAgentFormSave",
    // The picker draws the local library with the marketplace's own card and
    // rail builders, so its dependencies load ahead of it — and filters it
    // with the app's toolbar search. The Agents dialog that hosts it puts its
    // two tabs up with core/tabs.js.
    "BossModMarketplaceItems", "BossModPackCard", "BossModFilterRail",
    "BossModSearchField", "BossModTabs",
    "BossModAgentTemplatePicker",
    "BossModAgentFormTemplate", "BossModAgentDialogFooter",
    "BossModAgentAddPane", "BossModAgentDialogSlot",
    "BossModFloorScope",
    "BossModAgentEdit", "BossModAgentsDialog",
    // The desk's Schedules section: the recurrence editor, its layer, the section.
    "BossModScheduleApi", "BossModScheduleView",
    "BossModScheduleFields", "BossModSchedulePreview", "BossModScheduleLayer", "BossModDeskSchedules",
    "BossModDeskPanel",
    // The desk's task rows wear the Tasks place's status labels and open the
    // task as a layer over the desk through the Tasks place's own loader,
    // detail, task actions and layer controller; its Chat tool is the one
    // conversation route. All are read at call time.
    "BossModTasksColumns", "BossModTasksData", "BossModTaskDeliverables", "BossModTaskEvents",
    "BossModTaskDetailSections", "BossModTaskDetail", "BossModTasksCancel",
    "BossModAssignOutcomes", "BossModAssignForm",
    "BossModTaskFilePicker", "BossModTaskEditFiles", "BossModTaskEditMode", "BossModTasksComplete",
    "BossModTaskActions", "BossModTaskLayers",
    "BossModDeskTaskOpener", "BossModAgentRoutes", "BossModDeskDialog",
    // The chat place hands agent conversations the Browser Vision screen.
    "BossModExtensionsApi", "BossModBrowserVisionStatus", "BossModExtensionsLive",
    // The desk panel's Extensions section (read at call time, so after it).
    "BossModDeskExtensions",
    "BossModChatPlace",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
// api-client.js publishes on `window` (an IIFE, not a top-level const), and
// the fake `window` is not `global`, so its global is read from there.
const WINDOW_PUBLISHED = new Set(["BossModApi"]);
NAMES.forEach((name, index) => {
    const source = WINDOW_PUBLISHED.has(name) ? `window.${name}` : name;
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${source};\n`);
});

// WHAT THE FORM RENDERED, as a string. The builders write their markup into
// the host and the fake creates one node per `id=`/`name=` from it (see
// stubControls above), so the VALUES — a filled field, a checked radio, a
// selected option — exist only in the markup. This keeps the last one written,
// and it wraps the real builder rather than replacing it.
let formMarkup = "";
const realBuildFormHTML = global.BossModAgentForm.buildFormHTML;
global.BossModAgentForm.buildFormHTML = async (container, agent, prefill) => {
    await realBuildFormHTML(container, agent, prefill);
    formMarkup = container.innerHTML;
};

// Read off `global` rather than destructured into module-scope consts: a
// `const` here would be in its temporal dead zone while the evals above run,
// and chat-place.js calls BossModPlaces.register() at load time.
const { BossModStore, BossModBus, BossModNeeds } = global;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

/** Advance timeout deadlines, then let their asynchronous work settle. */
async function advance(ms) {
    mock.timers.tick(ms);
    await drain();
}

// context/agent-api.js and the form's bindings call the GLOBAL request helper
// (api-auth.js patches window.fetch and every module reads it by name), so the
// dialog only runs for real if the harness provides it. Pointed at the same
// scripted API the column is injected with, so both see one world.
global.apiFetch = (...args) => api(...args);

// The Agents dialog builds its Marketplace pane beside the Add agent pane.
// The marketplace is tests/js_marketplace_harness.cjs's subject and nothing
// here opens that tab, so it stands in with the shape the dialog places.
global.BossModMarketplace = {
    createPane() {
        // The pane's title-row chevron, hidden as the real one is while no
        // pack is open.
        const lead = global.BossModDom.h("span", { class: "market-lead-stub" });
        lead.hidden = true;
        return {
            element: global.BossModDom.h("div", { class: "market-host" }),
            lead,
            activate() {},
            deactivate() {},
        };
    },
};

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
    // See the note in tests/js_add_agent_harness.cjs: `sections` is a computed
    // field the server derives on every read, so a row without one is a shape
    // the API cannot return. All-null is what describe_pack answers for prose
    // carrying no recognised heading, which is what this is.
    sections: {
        description: {
            preamble: "Reads a diff and reports what is not true.", mission: null,
            in_scope: null, out_of_scope: null, handoff: null,
        },
        done: { preamble: "A checkable allow/deny exists.", fail_examples: null },
    },
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
// What /api/personalities answers when it answers at all. Empty for most of
// this file — the form then renders its link to Settings — and filled by the
// recreate section, which is about matching a stored prompt against them.
let personalities = [];
// The agent snapshots Add agent's Recent lists. Filled by the recreate
// section; empty everywhere else, so no other section grows a rail row.
let snapshots = [];
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
// What GET /api/tasks/board answers for Jim: one task, so the desk's Tasks
// section has a row to click. Everyone else carries nothing.
const JIM_TASK = { id: "t1", title: "Write TDD specs", status: "complete" };
// What GET /api/tasks answers: the whole list a task layer resolves its
// parent and subtasks from. Jim's task, and an open subtask of it — open, so
// its Edit mode offers Cancel.
const TASK_LIST = [
    { ...JIM_TASK, assigned_to: "a1" },
    { id: "t2", title: "Draft M5.3 list", status: "in_progress", parent_task_id: "t1", assigned_to: "a1" },
];
// Set by the section that proves a failed list read is said on the desk.
let taskListFails = false;
// How many board reads the desk's Tasks section has made: a cancel re-reads.
let boardReads = 0;
// Every POST /api/tasks/{id}/cancel URL, in order.
const cancels = [];
// What GET /api/agents/{id} answers, shaped as the server's `Agent`: the desk
// footer reads the workspace and model off it, and Remove reads the NAME its
// warning is about.
const AGENT_DETAIL = { id: "a1", name: "Jim", storage_key: "jim-workspace", model_work: "gpt-test" };
// Set by the section that clicks Remove before that read has landed: while it
// holds a promise, every GET /api/agents/{id} waits on it.
let heldAgentDetail = null;

// What GET /api/agents/{id}/schedules answers, per agent: Jim has one weekday
// schedule whose last run was missed; everyone else has none. A POST appends.
const SCHEDULE_ONE = {
    id: "s1", agent_id: "a1", title: "Status check", instructions: "Read the status page.",
    recurrence: {
        frequency: "weekly", interval: 1, times: ["06:00", "12:00"],
        every_minutes: null, window_start: null, window_end: null, weekdays: [0, 1, 2, 3, 4],
        month_day: null, start_date: "2026-09-01",
    },
    notification_policy: "completion_blocked", enabled: true,
    created_by: "__human__", agent_can_change: false, created_by_name: null,
    last_occurrence_at: "2026-09-29T10:00:00Z", last_outcome: "missed", last_outcome_detail: null,
    last_task_id: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
    summary: "Every weekday at 06:00, 12:00", next_run_at: "2026-09-30T10:00:00Z", last_task_status: null,
};
const SCHEDULES = { a1: [SCHEDULE_ONE], a2: [], a3: [] };
// Set by the section that proves a failed read is said, with a retry.
let schedulesFail = false;
// How many schedule list reads, and every schedule write, in order.
let scheduleReads = 0;
const scheduleWrites = [];
// Every POST /api/schedules/preview body, and what Run now answers: 'ok',
// 'open' (a 409 refusal), or a held promise while a test watches the button.
const previewRequests = [];
let runMode = "ok";
let heldRun = null;

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
        return jsonResponse(personalities);
    }
    if (String(url).startsWith("/api/agent-snapshots")) {
        return jsonResponse(snapshots);
    }
    const personality = String(url).match(/^\/api\/personalities\/([^/]+)$/);
    if (personality) {
        const row = personalities.find((item) => item.id === personality[1]);
        return row ? jsonResponse(row) : jsonResponse({ detail: "no such personality" }, 404);
    }
    if (String(url) === "/api/agents" && init && init.method === "POST") {
        creates.push(JSON.parse(init.body));
        if (!createSucceeds) return jsonResponse({ detail: "Name already taken" }, 409);
        return jsonResponse({ id: `new-${creates.length}` });
    }
    if (String(url) === "/api/tasks") {
        if (taskListFails) return jsonResponse({ detail: "boom" }, 500);
        return jsonResponse(TASK_LIST);
    }
    const cancelOne = String(url).match(/^\/api\/tasks\/([^/]+)\/cancel$/);
    if (cancelOne && init && init.method === "POST") {
        cancels.push(String(url));
        // The single cancel answers the list-row shape, so the detail repaints.
        const listed = TASK_LIST.find((item) => item.id === cancelOne[1]);
        return jsonResponse({ ...listed, status: "cancelled" });
    }
    if (String(url).startsWith("/api/tasks/board")) {
        boardReads += 1;
        const scoped = /agent_id=a1&scope=self/.test(String(url));
        return jsonResponse({ sections: { closed: scoped ? [JIM_TASK] : [] } });
    }
    const agentSchedules = String(url).match(/^\/api\/agents\/([^/]+)\/schedules$/);
    if (agentSchedules) {
        const agentId = agentSchedules[1];
        if (init && init.method === "POST") {
            const body = JSON.parse(init.body);
            scheduleWrites.push({ method: "POST", url: String(url), body });
            const row = {
                ...SCHEDULE_ONE, ...body, id: `s${scheduleWrites.length + 1}`, agent_id: agentId,
                enabled: true, last_outcome: null, last_occurrence_at: null, summary: "Every week on Mon at 06:00",
            };
            SCHEDULES[agentId].push(row);
            return jsonResponse(row, 201);
        }
        scheduleReads += 1;
        if (schedulesFail) return jsonResponse({ detail: "boom" }, 500);
        return jsonResponse(SCHEDULES[agentId] || []);
    }
    if (String(url) === "/api/schedules/preview" && init && init.method === "POST") {
        const body = JSON.parse(init.body);
        previewRequests.push(body);
        const runs = Array.from({ length: body.count }, (_, index) => `2026-10-0${index + 2}T10:00:00Z`);
        return jsonResponse({ summary: "Every weekday at 06:00, 12:00", next_runs: runs });
    }
    const runOne = String(url).match(/^\/api\/schedules\/([^/]+)\/run$/);
    if (runOne && init && init.method === "POST") {
        scheduleWrites.push({ method: "POST", url: String(url), body: null });
        if (heldRun) return heldRun.promise;
        if (runMode === "open") {
            return jsonResponse({ detail: { reason: "open", detail: "The last run is still open", task_id: "t9" } }, 409);
        }
        const stored = Object.values(SCHEDULES).flat().find((row) => row.id === runOne[1]);
        Object.assign(stored, {
            last_outcome: "fired", last_occurrence_at: "2026-10-01T09:00:00Z", last_task_id: "t9", last_task_status: "pending",
        });
        return jsonResponse({ schedule: { ...stored }, task: { id: "t9", title: stored.title } }, 201);
    }
    const oneSchedule = String(url).match(/^\/api\/schedules\/([^/]+)$/);
    if (oneSchedule && init && (init.method === "PATCH" || init.method === "DELETE")) {
        const body = init.body ? JSON.parse(init.body) : null;
        scheduleWrites.push({ method: init.method, url: String(url), body });
        if (init.method === "DELETE") return Promise.resolve({ ok: true, status: 204, json: () => Promise.reject(new Error("no body")) });
        const stored = Object.values(SCHEDULES).flat().find((row) => row.id === oneSchedule[1]);
        Object.assign(stored, body);
        return jsonResponse({ ...stored });
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
        if (heldAgentDetail) return heldAgentDetail.promise;
        return jsonResponse(AGENT_DETAIL);
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
            // Answered as what the notes listing SAYS it is: a folder row is a
            // directory, not a file. Answering it as a file opened a viewer on
            // a folder, unseen while the viewer was a slide-over and counted
            // now that it is a modal.
            const listed = (Array.isArray(NOTES[agentId]) ? NOTES[agentId] : [])
                .find((entry) => entry.path === path);
            if (listed && listed.is_dir) {
                return jsonResponse({
                    kind: "directory", path, name: listed.name, breadcrumbs: [], entries: [],
                });
            }
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
    const navigate = (placeId, params) => navigated.push({ placeId, params });
    // The shell builds ONE desk dialog and hands its `open` to every place as
    // ctx.openDesk; so does this.
    const desk = global.BossModDeskDialog.createDeskDialog({ store, bus, api, navigate });
    const ctx = {
        store,
        bus,
        api,
        needs,
        contextEl,
        openDesk: desk.open,
        navigate,
    };

    const modals = () => documentStub.body.querySelectorAll(".modal-panel");
    /** The open desk modal, or undefined. Marked for its stylesheet. */
    const deskModal = () => modals().find((node) => node.getAttribute("data-dialog") === "desk");
    /** A node inside the desk modal. */
    const inDesk = (selector) => deskModal().querySelector(selector);
    const deskText = (selector) => deskModal().querySelectorAll(selector)
        .map((node) => node.textContent).join(" ");

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
    // along with the rooms, which is the worse of the two failures. The summary
    // reads the map once per build, so Chat is remounted to build a new one.

    mapFails = true;
    chat.unmount();
    chat.mount(placeEl, ctx);
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
    chat.unmount();
    chat.mount(placeEl, ctx);
    await drain();

    // ─── 2. A seat opens that agent's desk, as a modal ───

    const ada = seats().filter((seat) => seat.getAttribute("data-agent-id") === "a3")[0];
    if (!ada) throw new Error("the off-map agent lost their seat on the rebuilt panel");
    await ada.dispatchClick();
    await drain();
    const seatOpensDesk = Boolean(deskModal())
        && deskModal().getAttribute("data-size") === "panel"
        && deskModal().getAttribute("aria-label") === "Ada"
        && documentStub.body.children.indexOf(deskModal()) !== -1;
    if (!seatOpensDesk) {
        throw new Error("clicking a seat must open that agent's desk as a panel modal");
    }

    // ─── 3. The column holds the office summary and nothing else ───
    //
    // The desk used to be the column's second view. It is a modal over the
    // app now, so opening one leaves the summary where it is.

    const columnHoldsOnlyTheOffice = contextEl.querySelectorAll(".mini-office").length === 1
        && contextEl.querySelectorAll(".desk").length === 0
        && contextEl.querySelectorAll(".modal-panel").length === 0;
    if (!columnHoldsOnlyTheOffice) {
        throw new Error("the context column must hold only the office summary");
    }
    // One desk at a time: opening another closes the first rather than
    // stacking a second modal.
    desk.open("a1");
    await drain();
    const deskModals = () => modals().filter((node) => node.getAttribute("data-dialog") === "desk");
    const oneDeskAtATime = deskModals().length === 1
        && deskModal().getAttribute("aria-label") === "Jim";
    if (!oneDeskAtATime) {
        throw new Error(`a second desk must replace the first, got ${deskModals().length}`);
    }
    // Opening and closing desks must not accumulate subscriptions. Measured
    // with no desk open at both ends.
    desk.close();
    await drain();
    const closedSubscribers = store.subscriberCount();
    const closedBusSubscribers = bus.subscriberCount();
    desk.open("a1");
    desk.open("a2");
    await drain();
    desk.close();
    await drain();
    const deskDrainsOnClose = modals().length === 0
        && store.subscriberCount() === closedSubscribers
        && bus.subscriberCount() === closedBusSubscribers;
    if (!deskDrainsOnClose) {
        throw new Error(`the desk leaks subscriptions: store ${closedSubscribers} -> `
            + `${store.subscriberCount()}, bus ${closedBusSubscribers} -> ${bus.subscriberCount()}`);
    }

    // ─── 3a. The desk lost nothing in the move ───
    //
    // "Nothing was deleted" is a claim about what RENDERS, so it is read off
    // the built modal — a source check would pass while a block sat in a
    // branch that never runs.

    desk.open("a1");
    await drain();
    const sectionLabels = deskModal().querySelectorAll(".desk-section-head")
        .map((node) => node.children[0].textContent);
    const aboutPill = inDesk(".desk-about-block").querySelector(".status-pill");
    const deskFields = {
        // The name is the modal's title now, and the face its lead.
        name: deskModal().querySelector(".modal-title").textContent === "Jim"
            && Boolean(deskModal().querySelector(".modal-head").querySelector(".desk-lead")
                .querySelector(".avatar")),
        role: deskText(".desk-role").includes("Engineer"),
        about: deskText(".desk-about").includes("Keeps the build green."),
        status: Boolean(aboutPill) && aboutPill.textContent.trim().length > 0,
        // The contract copy AND the value, inside the disclosure that holds
        // them, in the shared warn callout.
        contract: deskModal().querySelectorAll(".desk-contract").length === 1
            && inDesk(".desk-contract").querySelector(".callout").getAttribute("data-tone") === "warn"
            && deskText(".desk-bar").includes("What done looks like for this agent:")
            && deskText(".desk-bar").includes("Good: tests pass."),
        tasks: sectionLabels.includes("Tasks")
            && deskModal().querySelectorAll(".desk-tasks").length === 1,
        files: sectionLabels.includes("Files")
            && deskModal().querySelectorAll(".desk-files").length === 1,
        // The desk's own facts, on the shared fact list: the workspace it was
        // given and the model it runs.
        desk: sectionLabels.includes("Details")
            && deskText(".fact-list").includes("Workspace")
            && deskText(".fact-list").includes("jim-workspace")
            && deskText(".fact-list").includes("Model"),
        notes: sectionLabels.includes("Notes")
            && deskModal().querySelectorAll(".desk-notes").length === 1,
    };
    const lost = Object.keys(deskFields).filter((field) => !deskFields[field]);
    if (lost.length) {
        throw new Error(`the desk lost ${lost.join(", ")}; sections `
            + `${sectionLabels.join("/")} details "${deskText(".fact-list")}"`);
    }
    // "See all" belongs to the Tasks header, and "New" to the Schedules one.
    const sectionActions = deskModal().querySelectorAll(".desk-section").map((node) => {
        const action = node.children[0].querySelector(".desk-section-action");
        return `${node.children[0].children[0].textContent}:${action ? action.textContent : ""}`;
    });
    if (!sectionActions.includes("Tasks:See all") || !sectionActions.includes("Schedules:New")
        || sectionActions.filter((entry) => !entry.endsWith(":")).length !== 2) {
        throw new Error(`Tasks owes its header a "See all" and Schedules a "New", got ${sectionActions.join("|")}`);
    }
    // The actions on the agent are the HEAD's: Chat and Edit role as tools,
    // and the other three behind the `⋯`, the destructive two marked.
    const head = deskModal().querySelector(".modal-head");
    const headTools = head.querySelector(".modal-tools").querySelectorAll("button")
        .map((node) => node.getAttribute("aria-label"));
    const toolsAreInTheHead = headTools.join("|") === "Open chat|Edit role|Desk options"
        && deskModal().querySelector(".modal-body").querySelectorAll(".desk-action").length === 0;
    if (!toolsAreInTheHead) {
        throw new Error(`the desk's head must carry its tools, got ${headTools.join("|")}`);
    }
    await inDesk("#desk-options").dispatchClick();
    await drain();
    const menuRows = head.querySelectorAll(".menu-action");
    const optionsMenuHoldsTheRest = menuRows.map((node) => node.textContent).join("|")
            === "Diagnostics|Reset runtime|Remove agent"
        && menuRows.map((node) => node.getAttribute("data-tone") || "").join("|") === "|danger|danger"
        && inDesk("#desk-options").getAttribute("aria-expanded") === "true";
    if (!optionsMenuHoldsTheRest) {
        throw new Error(`the ⋯ must hold Diagnostics, Reset runtime and Remove agent, got `
            + menuRows.map((node) => node.textContent).join("|"));
    }
    // The contract is CLOSED until the operator asks for it.
    if (inDesk(".desk-contract").hasAttribute("open")) {
        throw new Error("the contract must be a disclosure, not a standing alert");
    }

    // Diagnostics opens the Log filtered to this agent — and the desk closes
    // first, because the operator asked to go somewhere else. The Log reads
    // `agentId` (as Metrics sends it); `agentFilter` is the Tasks param.
    const navigatedBefore = navigated.length;
    await head.querySelector("#desk-diagnostics").dispatchClick();
    await drain();
    const diagnosticsNav = navigated.slice(navigatedBefore);
    const diagnosticsFiltersTheLog = diagnosticsNav.length === 1
        && diagnosticsNav[0].placeId === "log"
        && JSON.stringify(diagnosticsNav[0].params) === JSON.stringify({ agentId: "a1" })
        && modals().length === 0;
    if (!diagnosticsFiltersTheLog) {
        throw new Error(`Diagnostics must close the desk and open the Log with { agentId }, got `
            + `${JSON.stringify(diagnosticsNav)} with ${modals().length} dialogs`);
    }

    // A task row opens the task as a LAYER over the desk — the head's trail
    // reads `Jim › Write TDD specs` and ‹ comes back to the desk. It used to
    // leave for the Tasks place, closing the desk with no way back to it.
    // "See all" is a place, not a detail, and still leaves.
    desk.open("a1");
    await drain();
    const taskRow = inDesk(".desk-task");
    const taskRowIsAButtonWithTheSharedPill = Boolean(taskRow)
        && taskRow.tagName === "BUTTON"
        && taskRow.getAttribute("data-task-id") === "t1"
        && taskRow.querySelector(".status-pill").getAttribute("data-status") === "complete"
        && taskRow.querySelector(".status-pill").textContent === "Complete";
    if (!taskRowIsAButtonWithTheSharedPill) {
        throw new Error("a desk task must be a button wearing the shared status pill");
    }
    /** The top task layer, known by what its body holds. */
    const taskLayers = () => modals().filter((panel) => panel.querySelectorAll(".task-detail").length === 1);
    const topTaskLayer = () => taskLayers()[taskLayers().length - 1];
    const trailOf = (panel) => panel.querySelector(".modal-trail").querySelectorAll("li")
        .map((node) => node.textContent).join(" › ");
    const navigatedBeforeTask = navigated.length;
    await taskRow.dispatchClick();
    await drain();
    const taskRowOpensTheTask = taskLayers().length === 1
        && navigated.length === navigatedBeforeTask
        && Boolean(deskModal()) && deskModal().hidden === true
        && topTaskLayer().getAttribute("aria-label") === "Write TDD specs"
        && trailOf(topTaskLayer()) === "Jim › Write TDD specs"
        && topTaskLayer().querySelector(".modal-back").getAttribute("aria-label") === "Back to Jim";
    // A subtask link is one step deeper: three crumbs, ‹ back to its parent.
    await topTaskLayer().querySelector(".task-detail-subtask").dispatchClick();
    await drain();
    const aSubtaskPushesAThirdCrumb = taskLayers().length === 2
        && trailOf(topTaskLayer()) === "Jim › Write TDD specs › Draft M5.3 list"
        && topTaskLayer().querySelector(".modal-back").getAttribute("aria-label")
            === "Back to Write TDD specs";
    // Cancel from the open subtask's Edit mode: asked, posted, the layer
    // repainted in place as cancelled, and the desk's rows re-read. ‹ twice
    // then walks back through both task layers to the desk.
    const boardReadsBeforeCancel = boardReads;
    const cancelLayer = topTaskLayer();
    await cancelLayer.querySelector("#ct-edit-mode-btn").dispatchClick();
    await drain();
    await cancelLayer.querySelector("#ct-cancel-task-btn").dispatchClick();
    await drain();
    const cancelConfirm = modals().find((panel) => panel.textContent.includes("Cancel this task?"));
    await cancelConfirm.querySelectorAll(".modal-actions")[0].querySelectorAll("button")
        .find((btn) => btn.textContent === "Cancel task").dispatchClick();
    await drain();
    const repaintedInPlace = cancels.length === 1 && cancels[0] === "/api/tasks/t2/cancel"
        && taskLayers().length === 2 && topTaskLayer() === cancelLayer
        && cancelLayer.querySelector(".status-pill").getAttribute("data-status") === "cancelled"
        && boardReads > boardReadsBeforeCancel;
    await topTaskLayer().querySelector(".modal-back").dispatchClick();
    await drain();
    await topTaskLayer().querySelector(".modal-back").dispatchClick();
    await drain();
    const aCancelRefreshesTheDeskRows = repaintedInPlace
        && taskLayers().length === 0 && modals().length === 1
        && deskModal().hidden === false
        && Boolean(inDesk(".desk-task"));
    // ‹ from a task opened over the desk comes back to the desk.
    await inDesk(".desk-task").dispatchClick();
    await drain();
    await topTaskLayer().querySelector(".modal-back").dispatchClick();
    await drain();
    const taskBackReturnsToTheDesk = taskLayers().length === 0 && modals().length === 1
        && deskModal().hidden === false;
    // A list that cannot be read is said above the rows, which stay.
    taskListFails = true;
    await inDesk(".desk-task").dispatchClick();
    await drain();
    taskListFails = false;
    const tasksSectionErrors = inDesk(".desk-tasks").querySelectorAll(".context-error")
        .map((node) => node.textContent);
    const aFailedTaskListIsSaidOnTheDesk = tasksSectionErrors.join("|") === "Could not open that task."
        && taskLayers().length === 0 && modals().length === 1
        && inDesk(".desk-tasks").querySelectorAll(".desk-task").length === 1;
    if (!taskRowOpensTheTask || !aSubtaskPushesAThirdCrumb || !aCancelRefreshesTheDeskRows
        || !taskBackReturnsToTheDesk || !aFailedTaskListIsSaidOnTheDesk) {
        throw new Error(`a task row must open the task over the desk: opens ${taskRowOpensTheTask}, `
            + `subtask ${aSubtaskPushesAThirdCrumb}, cancel ${aCancelRefreshesTheDeskRows}, `
            + `back ${taskBackReturnsToTheDesk}, failed read ${aFailedTaskListIsSaidOnTheDesk}`);
    }
    // Leaving closes the desk AND everything stacked on it: a desk opened over
    // an open task layer takes the task with it, and an agent removed while a
    // task is open over their desk leaves nothing orphaned.
    await inDesk(".desk-task").dispatchClick();
    await drain();
    desk.open("a2");
    await drain();
    const anotherDeskClosesTheWholeStack = modals().length === 1 && taskLayers().length === 0
        && deskModal().getAttribute("aria-label") === "Laura";
    desk.open("a1");
    await drain();
    await inDesk(".desk-task").dispatchClick();
    await drain();
    store.setState({ roster: ROSTER.filter((row) => row.id !== "a1") });
    await drain();
    const aRemovalClosesTheWholeStack = modals().length === 0;
    store.setState({ roster: ROSTER });
    await drain();
    if (!anotherDeskClosesTheWholeStack || !aRemovalClosesTheWholeStack) {
        throw new Error(`leaving must close the desk and every layer on it: another desk `
            + `${anotherDeskClosesTheWholeStack}, removal ${aRemovalClosesTheWholeStack}`);
    }
    desk.open("a1");
    await drain();
    await inDesk(".desk-section-action").dispatchClick();
    await drain();
    const allNav = navigated[navigated.length - 1];
    const seeAllOpensTheAgentsTasks = allNav.placeId === "tasks"
        && JSON.stringify(allNav.params) === JSON.stringify({ agentFilter: "a1" })
        && modals().length === 0;
    if (!seeAllOpensTheAgentsTasks) {
        throw new Error(`See all must close the desk and open Tasks, got ${JSON.stringify(allNav)}`);
    }

    // ─── Remove says what a delete destroys, and whose ───
    //
    // The old copy promised "artifacts, and diagnostics are preserved" after
    // the delete had started removing both. What the operator reads is the
    // dialog, so the dialog is what is read here: the agent by name, the
    // tasks it cancels, and the back-up instruction — and never the promise.
    // It is a LAYER over the desk, so the desk is still there under it.
    const openRemove = async () => {
        await inDesk("#desk-options").dispatchClick();
        await drain();
        await deskModal().querySelector("#desk-remove").dispatchClick();
        await drain();
    };
    desk.open("a1");
    await drain();
    await openRemove();
    const removeDialog = modals()
        .find((panel) => panel.textContent.includes("Remove this agent?"));
    const removeText = removeDialog ? removeDialog.textContent : "";
    const removeWarnsWhatIsDeleted = modals().length === 2
        && Boolean(deskModal())
        && removeText.includes("Deleting Jim permanently deletes their files")
        && removeText.includes("cancels their open tasks")
        && removeText.includes("back up anything you need")
        && !removeText.includes("preserved");
    if (!removeWarnsWhatIsDeleted) {
        throw new Error(`Remove must warn what it deletes, got "${removeText}"`);
    }
    await removeDialog.querySelectorAll(".modal-actions")[0].querySelectorAll("button")
        .find((btn) => btn.textContent === "Cancel").dispatchClick();
    await drain();
    if (modals().length !== 1 || !deskModal()) {
        throw new Error("Cancel must close the Remove dialog and leave the desk");
    }

    // Before the detail read lands there is no name, so there is no dialog:
    // the Details section says why and asks again, and once the name is in,
    // Remove opens the warning that names them.
    let releaseAgentDetail = null;
    heldAgentDetail = { promise: new Promise((resolve) => { releaseAgentDetail = resolve; }) };
    desk.open("a1");
    await drain();
    await openRemove();
    const detailErrors = () => inDesk(".desk-details-block")
        .querySelectorAll(".context-error").map((node) => node.textContent).join(" ");
    const removeWaitsForTheName = modals().length === 1
        && detailErrors().includes("haven’t loaded yet");
    if (!removeWaitsForTheName) {
        throw new Error(`Remove before the name loads must not open a dialog, got `
            + `${modals().length} dialogs, details "${detailErrors()}"`);
    }
    heldAgentDetail = null;
    releaseAgentDetail(jsonResponse(AGENT_DETAIL));
    await drain();
    await openRemove();
    const removeOpensOnceTheNameIsIn = detailErrors() === ""
        && modals().length === 2
        && modals()[1].textContent.includes("Deleting Jim permanently deletes");
    if (!removeOpensOnceTheNameIsIn) {
        throw new Error(`Remove must open once the name loads, got ${modals().length} `
            + `dialogs, details "${detailErrors()}"`);
    }
    // Confirmed, the DELETE lands and the desk of someone deleted closes.
    await modals()[1].querySelectorAll(".modal-actions")[0].querySelectorAll("button")
        .find((btn) => btn.textContent === "Remove agent").dispatchClick();
    await drain();
    const removeClosesTheDesk = modals().length === 0;
    if (!removeClosesTheDesk) {
        throw new Error(`a removed agent's desk must close, got ${modals().length} dialogs`);
    }

    // ─── The roster is the desk's source of truth while it is open ───
    //
    // A rename retitles the modal; an agent deleted elsewhere closes it rather
    // than leaving "Loading…" on screen forever.
    desk.open("a1");
    await drain();
    store.setState({ roster: ROSTER.map((row) => (row.id === "a1" ? { ...row, name: "James" } : row)) });
    await drain();
    const aRenameRetitlesTheDesk = deskModal().querySelector(".modal-title").textContent === "James"
        && deskModal().getAttribute("aria-label") === "James";
    store.setState({ roster: ROSTER.filter((row) => row.id !== "a1") });
    await drain();
    const anAgentGoneFromTheRosterClosesTheDesk = modals().length === 0;
    store.setState({ roster: ROSTER });
    await drain();
    if (!aRenameRetitlesTheDesk || !anAgentGoneFromTheRosterClosesTheDesk) {
        throw new Error(`the desk must follow the roster: renamed ${aRenameRetitlesTheDesk}, `
            + `closed on removal ${anAgentGoneFromTheRosterClosesTheDesk}`);
    }

    // ─── Chat, from the head ───
    desk.open("a1");
    await drain();
    await inDesk("#desk-chat").dispatchClick();
    await drain();
    const chatToolOpensTheConversation = modals().length === 0
        && store.getState().conversationId === "a1"
        && store.getState().conversationKind === "agent";
    if (!chatToolOpensTheConversation) {
        throw new Error("the desk's Chat tool must close the desk and open the conversation");
    }

    // ─── 3a². Schedules: the rows, their states, the live repaint, the layer ───

    const scheduleRows = () => inDesk(".desk-schedules").querySelectorAll(".desk-schedule");
    const topLayer = () => modals()[modals().length - 1];
    // Every row time goes through the shared 24-hour formatter: spied on for
    // this first paint, so the row's text must be built from what it returned.
    const realClockTime = global.BossModFormat.formatClockTime;
    const clockCalls = [];
    global.BossModFormat.formatClockTime = (iso) => {
        const text = realClockTime(iso);
        clockCalls.push({ iso, text });
        return text;
    };
    desk.open("a1");
    await drain();
    global.BossModFormat.formatClockTime = realClockTime;
    const row0 = scheduleRows()[0];
    const clockOf = (iso) => (clockCalls.find((call) => call.iso === iso) || {}).text;
    const WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const timesUseTheSharedClock = Boolean(clockOf(SCHEDULE_ONE.next_run_at))
        && Boolean(clockOf(SCHEDULE_ONE.last_occurrence_at))
        && row0.querySelector(".desk-schedule-next").textContent
            === `Next: ${WEEKDAY_NAMES[new Date(SCHEDULE_ONE.next_run_at).getDay()]} ${clockOf(SCHEDULE_ONE.next_run_at)}`
        && row0.querySelector(".desk-schedule-tone").textContent
            === `Missed ${clockOf(SCHEDULE_ONE.last_occurrence_at)}, computer was asleep`;
    const scheduleSectionRendersRows = scheduleRows().length === 1
        && row0.getAttribute("data-schedule-id") === "s1"
        && row0.querySelector(".desk-schedule-title").textContent === "Status check"
        && row0.querySelector(".desk-schedule-meta").textContent === "Every weekday at 06:00, 12:00"
        && row0.querySelector(".desk-schedule-next").textContent.startsWith("Next: ")
        && row0.querySelector(".desk-schedule-tone").textContent.startsWith("Missed ")
        && row0.querySelector(".desk-schedule-tone").textContent.endsWith(", computer was asleep");
    desk.open("a2");
    await drain();
    const scheduleSectionSaysEmpty = inDesk(".desk-schedules").textContent === "No schedules yet.";
    schedulesFail = true;
    desk.open("a3");
    await drain();
    schedulesFail = false;
    const scheduleSectionSaysError = inDesk(".desk-schedules").querySelectorAll(".context-error")
        .map((node) => node.textContent).join("|") === "Could not load schedules.";
    await inDesk("#desk-schedules-retry").dispatchClick();
    await drain();
    const scheduleRetryRecovers = inDesk(".desk-schedules").textContent === "No schedules yet.";
    // The transport's refusals read as the server's own sentence: a string
    // detail and a 422's messages through BossModApi.formatError, and Run
    // now's structured 409 with its reason and task riding on the Error.
    const refusalOf = async (body, status) => {
        try {
            await BossModScheduleApi.update(() => jsonResponse(body, status), "s1", { title: "x" });
            return null;
        } catch (err) {
            return err;
        }
    };
    const stringRefusal = await refusalOf({ detail: "Schedule not found" }, 404);
    const validationRefusal = await refusalOf(
        { detail: [{ msg: "Value error, times must not repeat" }, { msg: "Field required" }] }, 422);
    const structuredRefusal = await refusalOf(
        { detail: { reason: "open", detail: "The last run is still open", task_id: "t9" } }, 409);
    const scheduleRefusalsSayWhy = stringRefusal.message === "Schedule not found"
        && validationRefusal.message === "Value error, times must not repeat; Field required"
        && structuredRefusal.message === "The last run is still open"
        && structuredRefusal.reason === "open" && structuredRefusal.taskId === "t9";
    if (!scheduleSectionRendersRows || !scheduleSectionSaysEmpty || !scheduleSectionSaysError
        || !scheduleRetryRecovers || !timesUseTheSharedClock || !scheduleRefusalsSayWhy) {
        throw new Error(`the Schedules section must render its states: rows ${scheduleSectionRendersRows}, `
            + `empty ${scheduleSectionSaysEmpty}, error ${scheduleSectionSaysError}, retry ${scheduleRetryRecovers}, `
            + `clock ${timesUseTheSharedClock}, refusals ${scheduleRefusalsSayWhy}`);
    }

    // A run of THIS agent's schedule repaints the rows; another agent's, or
    // an unrelated event, does not.
    desk.open("a1");
    await drain();
    const readsBefore = scheduleReads;
    bus.publish("activity", { event: "schedule_ran", detail: "x", agent_id: "a2", schedule_id: "z" });
    bus.publish("activity", { event: "task_created", detail: "x", agent_id: "a1" });
    await advance(650);
    const otherActivityIsIgnored = scheduleReads === readsBefore;
    bus.publish("activity", { event: "schedule_ran", detail: "x", agent_id: "a1", schedule_id: "s1", outcome: "fired" });
    bus.publish("activity", { event: "schedule_changed", detail: "x", agent_id: "a1", schedule_id: "s1" });
    await advance(499);
    if (scheduleReads !== readsBefore) {
        throw new Error("schedule events must wait for the 500ms debounce before refreshing");
    }
    await advance(1);
    const aScheduleRunRefreshesTheRows = scheduleReads === readsBefore + 1;
    if (!otherActivityIsIgnored || !aScheduleRunRefreshesTheRows) {
        throw new Error(`the Schedules section must repaint on its own runs only: ignored `
            + `${otherActivityIsIgnored}, refreshed ${aScheduleRunRefreshesTheRows} (${scheduleReads - readsBefore})`);
    }

    // A row opens the schedule as a layer; Edit mode PATCHes only what changed.
    await scheduleRows()[0].dispatchClick();
    await drain();
    const viewLayer = topLayer();
    const aRowOpensTheScheduleLayer = modals().length === 2 && deskModal().hidden === true
        && viewLayer.getAttribute("aria-label") === "Status check"
        && viewLayer.querySelector(".fact-list").textContent.includes("Every weekday at 06:00, 12:00")
        && viewLayer.querySelector(".switch-row").getAttribute("aria-checked") === "true";
    await viewLayer.querySelector("#schedule-edit").dispatchClick();
    await drain();
    viewLayer.querySelector(".task-detail-title-input").value = "Status check (prod)";
    const writesBeforeEdit = scheduleWrites.length;
    await viewLayer.querySelector("#schedule-save").dispatchClick();
    await drain();
    const editWrite = scheduleWrites[writesBeforeEdit];
    const anEditPatchesOnlyWhatChanged = scheduleWrites.length === writesBeforeEdit + 1
        && editWrite.method === "PATCH" && editWrite.url === "/api/schedules/s1"
        && JSON.stringify(editWrite.body) === JSON.stringify({ title: "Status check (prod)" })
        && viewLayer.querySelector("#schedule-edit").hidden === false;
    await viewLayer.querySelector(".modal-back").dispatchClick();
    await drain();

    // New opens the layer in edit mode. Weekly with no weekday is said on the
    // spot and nothing is sent; with one, the POST carries the exact rule.
    await deskModal().querySelectorAll(".desk-section-action")
        .find((node) => node.textContent === "New").dispatchClick();
    await drain();
    const newLayer = topLayer();
    const newOpensInEditMode = newLayer.getAttribute("aria-label") === "New schedule"
        && newLayer.querySelector("#schedule-edit").hidden === true
        && newLayer.querySelector("#schedule-save").hidden === false;
    newLayer.querySelector(".task-detail-title-input").value = "Weekly report";
    newLayer.querySelector(".task-detail-description-input").value = "Summarise the week.";
    await newLayer.querySelector(".schedule-fields").querySelector(".menu-select-trigger").dispatchClick();
    await drain();
    await newLayer.querySelectorAll(".menu-select-option")
        .find((node) => node.textContent === "Weekly").dispatchClick();
    await drain();
    newLayer.querySelector(".schedule-time").value = "06:00";
    const writesBeforeCreate = scheduleWrites.length;
    await newLayer.querySelector("#schedule-save").dispatchClick();
    await drain();
    const weeklyNeedsAWeekday = scheduleWrites.length === writesBeforeCreate
        && newLayer.querySelector(".schedule-layer-error").textContent.includes("Pick at least one weekday.");
    await newLayer.querySelector("[data-weekday=\"0\"]").dispatchClick();
    await newLayer.querySelector("#schedule-save").dispatchClick();
    await drain();
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const createWrite = scheduleWrites[writesBeforeCreate];
    const aCreatePostsTheExactRule = scheduleWrites.length === writesBeforeCreate + 1
        && createWrite.method === "POST" && createWrite.url === "/api/agents/a1/schedules"
        && JSON.stringify(createWrite.body) === JSON.stringify({
            title: "Weekly report",
            instructions: "Summarise the week.",
            recurrence: {
                frequency: "weekly", interval: 1, times: ["06:00"],
                every_minutes: null, window_start: null, window_end: null,
                weekdays: [0], month_day: null, start_date: today,
            },
            notification_policy: "completion_blocked",
            enabled: true,
            agent_can_change: false,
        })
        // Saved, it is that schedule's view now, titled by it.
        && newLayer.querySelector(".modal-title").textContent === "Weekly report"
        && newLayer.querySelector("#schedule-edit").hidden === false;
    // "Repeating": an overnight window is said and nothing is sent; a valid
    // one POSTs the repeat with `times: []` (the At time typed first is
    // dropped by the switch), hours stored as minutes x 60. Editing it
    // again shows hours, because 120 divides by 60.
    await newLayer.querySelector(".modal-back").dispatchClick();
    await drain();
    await deskModal().querySelectorAll(".desk-section-action")
        .find((node) => node.textContent === "New").dispatchClick();
    await drain();
    const repeatLayer = topLayer();
    repeatLayer.querySelector(".task-detail-title-input").value = "Uptime ping";
    repeatLayer.querySelector(".task-detail-description-input").value = "Check the status page.";
    repeatLayer.querySelector(".schedule-time").value = "06:00";
    await repeatLayer.querySelector("[data-time-mode=\"every\"]").dispatchClick();
    const repeatRow = repeatLayer.querySelector(".schedule-repeat-row");
    const repeatModeShowsOnlyItsControls = repeatRow.hidden === false
        && repeatLayer.querySelector(".schedule-at").hidden === true
        && repeatLayer.querySelector("[data-time-mode=\"every\"]").getAttribute("aria-pressed") === "true";
    repeatRow.querySelector(".schedule-repeat-every").value = "2";
    await repeatRow.querySelector(".menu-select-trigger").dispatchClick();
    await drain();
    await repeatLayer.querySelectorAll(".menu-select-option")
        .find((node) => node.textContent === "hours").dispatchClick();
    await drain();
    repeatRow.querySelector(".schedule-window-start").value = "20:00";
    repeatRow.querySelector(".schedule-window-end").value = "08:00";
    const writesBeforeRepeat = scheduleWrites.length;
    await repeatLayer.querySelector("#schedule-save").dispatchClick();
    await drain();
    const anOvernightWindowIsRefusedHere = scheduleWrites.length === writesBeforeRepeat
        && repeatLayer.querySelector(".schedule-layer-error").textContent.includes("overnight windows are not supported");
    repeatRow.querySelector(".schedule-window-start").value = "08:00";
    repeatRow.querySelector(".schedule-window-end").value = "20:00";
    await repeatLayer.querySelector("#schedule-save").dispatchClick();
    await drain();
    const repeatWrite = scheduleWrites[writesBeforeRepeat];
    const aRepeatPostsMinutesAndAWindow = scheduleWrites.length === writesBeforeRepeat + 1
        && repeatWrite.method === "POST"
        && JSON.stringify(repeatWrite.body.recurrence) === JSON.stringify({
            frequency: "daily", interval: 1, times: [], every_minutes: 120, window_start: "08:00",
            window_end: "20:00", weekdays: [], month_day: null, start_date: today,
        });
    await repeatLayer.querySelector("#schedule-edit").dispatchClick();
    await drain();
    const editedRow = repeatLayer.querySelector(".schedule-repeat-row");
    const aStoredRepeatEditsInHours = editedRow.hidden === false
        && editedRow.querySelector(".schedule-repeat-every").value === "2"
        && editedRow.querySelector(".menu-select-value").textContent === "hours";
    await repeatLayer.querySelector("#schedule-discard").dispatchClick();
    await drain();
    if (!repeatModeShowsOnlyItsControls || !anOvernightWindowIsRefusedHere || !aRepeatPostsMinutesAndAWindow
        || !aStoredRepeatEditsInHours) {
        throw new Error(`Repeating: mode ${repeatModeShowsOnlyItsControls}, overnight ${anOvernightWindowIsRefusedHere}, `
            + `post ${aRepeatPostsMinutesAndAWindow} ${JSON.stringify(repeatWrite)}, hours ${aStoredRepeatEditsInHours}`);
    }

    // Run now: the button is disabled and says so while the request is in
    // flight; success says "Run started" with Open task and repaints the
    // facts; a 409 for an open last run says why, with Open task.
    await repeatLayer.querySelector(".modal-back").dispatchClick();
    await drain();
    await scheduleRows().find((node) => node.getAttribute("data-schedule-id") === "s1").dispatchClick();
    await drain();
    const runLayer = topLayer();
    let releaseRun = null;
    heldRun = { promise: new Promise((resolve) => { releaseRun = resolve; }) };
    const runButton = runLayer.querySelector("#schedule-run-now");
    const writesBeforeRun = scheduleWrites.length;
    void runButton.dispatchClick();
    await drain();
    const runNowIsDisabledWhileRunning = runButton.disabled === true && runButton.textContent === "Starting…";
    heldRun = null;
    const stored = SCHEDULES.a1.find((row) => row.id === "s1");
    Object.assign(stored, {
        last_outcome: "fired", last_occurrence_at: "2026-10-01T09:00:00Z", last_task_id: "t9", last_task_status: "pending",
    });
    releaseRun(jsonResponse({ schedule: { ...stored }, task: { id: "t9", title: stored.title } }, 201));
    await drain();
    const runResult = () => runLayer.querySelector(".schedule-run-result");
    const runNowStartsARealRun = scheduleWrites.length === writesBeforeRun + 1
        && scheduleWrites[writesBeforeRun].url === "/api/schedules/s1/run"
        && runButton.disabled === false && runButton.textContent === "Run now"
        && runResult().textContent.includes("Run started")
        && Boolean(runResult().querySelector(".schedule-run-open-task"))
        && runLayer.querySelector(".fact-list").textContent.includes("Ran ");
    runMode = "open";
    await runButton.dispatchClick();
    await drain();
    runMode = "ok";
    const anOpenRunRefusalSaysWhy = runResult().textContent.includes("The last run is still open")
        && Boolean(runResult().querySelector(".schedule-run-open-task"));

    // Edit mode previews the draft's next runs after the debounce; an
    // invalid draft says why and sends nothing.
    await runLayer.querySelector("#schedule-edit").dispatchClick();
    const previewsBefore = previewRequests.length;
    await advance(299);
    if (previewRequests.length !== previewsBefore) {
        throw new Error("a draft must wait for the 300ms debounce before requesting its preview");
    }
    await advance(1);
    const previewBody = previewRequests[previewRequests.length - 1];
    const theDraftIsPreviewed = previewRequests.length === previewsBefore + 1
        && previewBody.count === 5
        && JSON.stringify(previewBody.recurrence.times) === JSON.stringify(["06:00", "12:00"])
        && runLayer.querySelector(".schedule-preview-list").children.length === 5;
    const firstTime = runLayer.querySelector(".schedule-time");
    firstTime.value = "";
    firstTime.dispatchEvent({ type: "input" });
    await advance(400);
    const anInvalidDraftIsSaidNotSent = previewRequests.length === previewsBefore + 1
        && runLayer.querySelector(".schedule-preview-error").textContent === "Fill in or remove the empty time.";
    await runLayer.querySelector("#schedule-discard").dispatchClick();
    await runLayer.querySelector(".modal-back").dispatchClick();
    await drain();

    // Create mode shows Enabled (on); switched off, the POST says so.
    await deskModal().querySelectorAll(".desk-section-action")
        .find((node) => node.textContent === "New").dispatchClick();
    await drain();
    const offLayer = topLayer();
    offLayer.querySelector(".task-detail-title-input").value = "Dry run";
    offLayer.querySelector(".task-detail-description-input").value = "Try it once.";
    offLayer.querySelector(".schedule-time").value = "07:00";
    await offLayer.querySelector(".switch-row").dispatchClick();
    const writesBeforeOff = scheduleWrites.length;
    await offLayer.querySelector("#schedule-save").dispatchClick();
    await drain();
    const aNewScheduleCanBeSavedOff = scheduleWrites.length === writesBeforeOff + 1
        && scheduleWrites[writesBeforeOff].body.enabled === false;
    if (!runNowIsDisabledWhileRunning || !runNowStartsARealRun || !anOpenRunRefusalSaysWhy
        || !theDraftIsPreviewed || !anInvalidDraftIsSaidNotSent || !aNewScheduleCanBeSavedOff) {
        throw new Error(`Run now / preview: disabled ${runNowIsDisabledWhileRunning}, run ${runNowStartsARealRun}, `
            + `open ${anOpenRunRefusalSaysWhy}, preview ${theDraftIsPreviewed}, invalid ${anInvalidDraftIsSaidNotSent}, `
            + `off ${aNewScheduleCanBeSavedOff}`);
    }

    // A run left open makes every later run skip: the row says the schedule
    // is paused, and the layer's Last run offers the run that holds it up.
    Object.assign(SCHEDULES.a1.find((row) => row.id === "s1"), {
        last_outcome: "skipped_open", last_occurrence_at: "2026-10-01T09:05:00Z",
        last_task_id: "t9", last_task_status: "waiting",
    });
    desk.open("a1");
    await drain();
    const pausedRow = scheduleRows().find((node) => node.getAttribute("data-schedule-id") === "s1");
    const anOpenRunShowsThePausedTone = pausedRow.querySelector(".desk-schedule-tone").textContent
        === "Paused: last run still open";
    await pausedRow.dispatchClick();
    await drain();
    const skippedLastRun = topLayer().querySelector(".schedule-last-run");
    const aSkipOffersTheOpenRun = skippedLastRun.textContent.startsWith("Skipped ")
        && Boolean(skippedLastRun.querySelector(".schedule-open-task"));
    if (!anOpenRunShowsThePausedTone || !aSkipOffersTheOpenRun) {
        throw new Error(`a paused schedule must say so: tone ${anOpenRunShowsThePausedTone}, `
            + `open task ${aSkipOffersTheOpenRun}`);
    }

    // The lock: an operator schedule shows the lock glyph; the switch beside
    // Enabled PATCHes `agent_can_change` on the spot. An agent's own schedule
    // reads "by <agent>" on the row and "Set up by" in the facts. Create
    // sends the switch (default off).
    SCHEDULES.a1.push({
        ...SCHEDULE_ONE, id: "s9", title: "Agent's own", created_by: "a1", created_by_name: "Jim",
        agent_can_change: true, last_outcome: null, last_task_id: null, last_task_status: null,
    });
    desk.open("a1");
    await drain();
    const rowOf = (id) => scheduleRows().find((node) => node.getAttribute("data-schedule-id") === id);
    const theLockAndTheAuthorShow = Boolean(rowOf("s1").querySelector(".desk-schedule-lock"))
        && rowOf("s1").querySelector(".desk-schedule-lock").getAttribute("aria-label") === "Agent cannot manage this task"
        && !rowOf("s9").querySelector(".desk-schedule-lock")
        && rowOf("s9").querySelector(".desk-schedule-meta").textContent.endsWith(" · by Jim");
    await rowOf("s9").dispatchClick();
    await drain();
    const ownLayer = topLayer();
    const setUpByShows = ownLayer.querySelector(".fact-list").textContent.includes("Set up by")
        && ownLayer.querySelector(".fact-list").textContent.includes("Jim");
    await ownLayer.querySelector(".modal-back").dispatchClick();
    await drain();
    await rowOf("s1").dispatchClick();
    await drain();
    const lockLayer = topLayer();
    const lockSwitch = lockLayer.querySelectorAll(".switch-row")
        .find((node) => node.textContent === "Agent can manage this task");
    const writesBeforeLock = scheduleWrites.length;
    await lockSwitch.dispatchClick();
    await drain();
    const theLockSwitchPatches = scheduleWrites.length === writesBeforeLock + 1
        && scheduleWrites[writesBeforeLock].method === "PATCH"
        && JSON.stringify(scheduleWrites[writesBeforeLock].body) === JSON.stringify({ agent_can_change: true });
    await lockLayer.querySelector(".modal-back").dispatchClick();
    await drain();
    await deskModal().querySelectorAll(".desk-section-action")
        .find((node) => node.textContent === "New").dispatchClick();
    await drain();
    const lockCreate = topLayer();
    lockCreate.querySelector(".task-detail-title-input").value = "Opened";
    lockCreate.querySelector(".task-detail-description-input").value = "Yours to manage.";
    lockCreate.querySelector(".schedule-time").value = "08:00";
    await lockCreate.querySelectorAll(".switch-row")
        .find((node) => node.textContent === "Agent can manage this task").dispatchClick();
    const writesBeforeOpenCreate = scheduleWrites.length;
    await lockCreate.querySelector("#schedule-save").dispatchClick();
    await drain();
    const createSendsTheLock = scheduleWrites.length === writesBeforeOpenCreate + 1
        && scheduleWrites[writesBeforeOpenCreate].body.agent_can_change === true
        && scheduleWrites[writesBeforeOpenCreate].body.enabled === true;
    if (!theLockAndTheAuthorShow || !setUpByShows || !theLockSwitchPatches || !createSendsTheLock) {
        throw new Error(`the lock: row ${theLockAndTheAuthorShow}, set up by ${setUpByShows}, `
            + `switch ${theLockSwitchPatches}, create ${createSendsTheLock}`);
    }

    // Leaving the desk takes an open schedule layer with it.
    desk.open("a2");
    await drain();
    const leavingTheDeskClosesTheScheduleLayer = modals().length === 1
        && deskModal().getAttribute("aria-label") === "Laura";
    desk.close();
    await drain();
    if (!aRowOpensTheScheduleLayer || !anEditPatchesOnlyWhatChanged || !newOpensInEditMode
        || !weeklyNeedsAWeekday || !aCreatePostsTheExactRule || !leavingTheDeskClosesTheScheduleLayer) {
        throw new Error(`the schedule layer: opens ${aRowOpensTheScheduleLayer}, patch `
            + `${anEditPatchesOnlyWhatChanged} ${JSON.stringify(editWrite)}, new ${newOpensInEditMode}, `
            + `weekday ${weeklyNeedsAWeekday}, create ${aCreatePostsTheExactRule} ${JSON.stringify(createWrite)}, `
            + `leaving ${leavingTheDeskClosesTheScheduleLayer}`);
    }

    // ─── 3b. Notes read the agent's workspace, not a column ───

    const notes = () => deskModal().querySelectorAll(".desk-note");
    const noteTitles = () => deskModal().querySelectorAll(".desk-note-title")
        .map((node) => node.textContent);

    desk.open("a1");
    await drain();
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

    // A note opens the ONE viewer — as a layer over the desk. No second
    // implementation, and the desk browser stays where the operator left it.
    await notes()[0].dispatchClick();
    await drain();
    // The viewer is known by what it holds: the one panel whose body is the
    // file view.
    const opensSharedViewer = modals()
        .filter((panel) => panel.querySelectorAll(".file-view").length === 1).length === 1
        && Boolean(deskModal());
    if (!opensSharedViewer) {
        throw new Error("clicking a note must open the shared file viewer over the desk");
    }
    // Closing the desk closes what is stacked on it too (the modal's
    // closeFrom): a viewer left over the screen would be an orphan.
    desk.close();
    await drain();
    const closingTheDeskClosesTheViewerOverIt = modals().length === 0;
    if (!closingTheDeskClosesTheViewerOverIt) {
        throw new Error(`closing the desk must close the viewer over it, got ${modals().length} dialogs`);
    }
    desk.open("a1");
    await drain();

    // A subfolder is handed to the desk browser, which is the thing that
    // navigates. Listing it and doing nothing would be a dead control.
    await notes()[1].dispatchClick();
    await drain();
    const folderRow = requestLog.filter((entry) => entry.path === "/me/notes/archive");
    if (!folderRow.length) throw new Error("a notes subfolder must open in the desk browser");

    // A desk opened on a path — a deliverable, a note's desk path — puts its
    // file browser there rather than at the root.
    const before = requestLog.length;
    desk.open("a1", "/me/reports");
    await drain();
    const opensOnThePathItWasGiven = requestLog.slice(before)
        .some((entry) => entry.agentId === "a1" && entry.path === "/me/reports");
    if (!opensOnThePathItWasGiven) {
        throw new Error("a desk opened on a path must browse that path");
    }

    // ─── An agent who has written nothing gets the empty state, not an error ───

    desk.open("a3");
    await drain();
    // Scoped to the Notes section: Tasks and Files have empty states of their
    // own, and a cross-section query would let one stand in for another.
    const notesSection = () => inDesk(".desk-notes");
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

    desk.open("a2");
    await drain();
    const errorNodes = notesText(".context-error");
    const failureSurfaces = errorNodes.some((text) => text.includes("Notes could not be loaded."))
        && notesSection().querySelectorAll("#desk-notes-retry-btn").length === 1
        && notesText(".context-empty").length === 0;
    if (!failureSurfaces) {
        throw new Error(`a 500 must surface an error with a retry, got ${JSON.stringify(errorNodes)}`);
    }
    desk.close();
    await drain();

    // ─── 3c. Hire and Edit are the same centred dialog ───
    //
    // Driven from the desk's own head tool, because "what the operator
    // clicks" is the claim.
    desk.open("a1");
    await drain();

    /** The Edit role layer, known by its name: the desk is a panel too. */
    const editModal = () => modals().find((node) => node.getAttribute("aria-label") === "Edit role");
    // Creating is the Agents dialog's Add agent tab now: a takeover, marked
    // for its stylesheet, and the only modal that carries that mark.
    const agentsModal = () => modals().filter(
        (node) => node.getAttribute("data-dialog") === "agents")[0];
    const openAddAgent = () => global.BossModAgentsDialog.open({ store, tab: "add" });
    // The frame's ✕ — the only exit on step one, whose footer row is empty.
    const closeByX = (dialog) => dialog.querySelector(".modal-close").dispatchClick();
    if (modals().length !== 1) throw new Error("only the desk should be open yet");

    const editAction = inDesk("#desk-edit");
    if (!editAction) throw new Error("the desk head must offer Edit role");
    await editAction.dispatchClick();
    await drain();

    const opened = editModal();
    const editOpensThePanelModal = Boolean(opened)
        && opened.getAttribute("role") === "dialog"
        && opened.getAttribute("data-size") === "panel"
        && Boolean(opened.querySelector("#agent-form"))
        // The edit flow keeps its remove path.
        && Boolean(opened.querySelector("#btn-delete-agent"));
    if (!editOpensThePanelModal) {
        throw new Error(`Edit role must open the panel dialog with the form in it: `
            + `${opened && opened.getAttribute("data-size")} `
            + `form ${Boolean(opened && opened.querySelector("#agent-form"))}`);
    }
    // It floats over the app as a LAYER over the desk, not inside the column
    // — and the desk it was opened from is still mounted underneath, hidden.
    const modalIsAttachedToTheBodyNotTheColumn =
        documentStub.body.children.indexOf(opened) !== -1
        && contextEl.querySelectorAll(".modal-panel").length === 0
        && Boolean(deskModal()) && deskModal().hidden === true
        && opened.querySelector(".modal-back").hidden === false
        && opened.querySelector(".modal-back").getAttribute("aria-label") === "Back to Jim";
    if (!modalIsAttachedToTheBodyNotTheColumn) {
        throw new Error("the dialog must be a layer over the desk, leaving the desk mounted");
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
    // sat pinned and obvious. Cancel first, primary last — and `Save as
    // template` leads the row, at the other end of it: it is about the form
    // rather than about finishing the dialog.
    const pinnedNamesIn = (dialog) => dialog.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").map((btn) => btn.textContent);
    /** One footer button, by the words on it. */
    const pinnedAction = (dialog, label) => dialog.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((btn) => btn.textContent === label);
    const editPinnedActions = pinnedNamesIn(opened);
    if (editPinnedActions.join("|") !== "Save as template|Cancel|Save Changes") {
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
    // The form's Delete asks with the same warning the desk's Remove does.
    // The fake builds one node per `id=` and copies no attributes, so the
    // markup's `type="button"` is carried over by hand; without it the fake
    // takes the button for the form's default submit and saves instead.
    if (!formMarkup.includes('<button type="button" id="btn-delete-agent"')) {
        throw new Error("the form's Delete must be a type=\"button\" button");
    }
    const formDelete = opened.querySelector("#btn-delete-agent");
    formDelete.setAttribute("type", "button");
    await formDelete.dispatchClick();
    await drain();
    const deleteDialog = modals().find((panel) => panel.textContent.includes("Delete this agent?"));
    const deleteText = deleteDialog ? deleteDialog.textContent : "";
    const deleteWarnsWhatIsDeleted = deleteText.includes("Deleting Jim permanently deletes their files")
        && deleteText.includes("cancels their open tasks")
        && deleteText.includes("back up anything you need");
    if (!deleteWarnsWhatIsDeleted) {
        throw new Error(`Delete must warn what it deletes, got "${deleteText}"`);
    }
    await deleteDialog.querySelectorAll(".modal-actions")[0].querySelectorAll("button")
        .find((btn) => btn.textContent === "Keep it").dispatchClick();
    await drain();
    if (modals().length !== 2 || editModal() !== opened) {
        throw new Error(`Keep it must leave the edit dialog over the desk, got ${modals().length}`);
    }

    // Dismissing puts the desk back in front and repaints it. BY NAME: the
    // first action in the row is Save as template, which opens a layer.
    await pinnedAction(opened, "Cancel").dispatchClick();
    await drain();
    const closingTheModalRestoresTheDesk = modals().length === 1
        && Boolean(deskModal()) && deskModal().hidden === false
        && deskText(".desk-role").includes("Engineer");
    if (!closingTheModalRestoresTheDesk) {
        throw new Error(`closing must leave the desk showing, got `
            + `${modals().length} dialogs`);
    }

    // One at a time. Two stacked agent dialogs would fight over Escape and the
    // focus trap, and two live `#agent-form`s would let one primary submit the
    // other's draft. The Hire door is the Agents dialog now; it hands back the
    // Edit dialog that holds the one-form slot and builds nothing of its own.
    await editAction.dispatchClick();
    await drain();
    const first = editModal();
    const handedBack = openAddAgent();
    await drain();
    const onlyOneDialogAtATime = modals().length === 2 && editModal() === first
        && agentsModal() === undefined && typeof handedBack.close === "function";
    if (!onlyOneDialogAtATime) {
        throw new Error(`a second dialog must not stack, got ${modals().length}`);
    }
    await pinnedAction(first, "Cancel").dispatchClick();
    await drain();
    desk.close();
    await drain();

    // Creating is the Agents dialog's Add agent tab: the same centred modal
    // floating over the app, a takeover now so the Marketplace tab beside it
    // has room, minus the remove path — there is nothing to remove yet — and
    // it opens on the PICKER: step one has no form, so it can have no Create
    // Agent either.
    openAddAgent();
    await drain();
    const hire = agentsModal();
    const hireOpensTheAgentsDialog = Boolean(hire)
        && hire.getAttribute("aria-label") === "Agents"
        && hire.getAttribute("data-size") === "takeover"
        && hire.getAttribute("role") === "dialog"
        && documentStub.body.children.indexOf(hire) !== -1
        && contextEl.querySelectorAll(".modal-panel").length === 0
        && hire.querySelector("#agents-tab-add").getAttribute("aria-selected") === "true"
        && hire.querySelector("#agent-form") === null
        && Boolean(hire.querySelector("#agent-pick-blank"))
        && hire.querySelector("#btn-delete-agent") === null;
    if (!hireOpensTheAgentsDialog) {
        throw new Error(`Add agent must open the Agents dialog on the picker: `
            + `${hire && hire.getAttribute("aria-label")} `
            + `form ${Boolean(hire && hire.querySelector("#agent-form"))}`);
    }
    // Step one pins NOTHING. The marketplace door that used to lead this row
    // is the Marketplace tab in the dialog's head, and the exit is the frame's
    // ✕ on every tab — a Cancel here would be a second control for it.
    const stepOnePinned = pinnedNamesIn(hire);
    if (stepOnePinned.join("|") !== "") {
        throw new Error(`step one pins nothing, got ${stepOnePinned.join("|")}`);
    }
    if (hire.querySelector("#agent-add-browse")) {
        throw new Error("the footer's marketplace door is gone; the tab replaced it");
    }

    // Picking Blank builds the form in the same body and swaps the footer.
    await hire.querySelector("#agent-pick-blank").dispatchClick();
    await drain();
    const stepTwoPinned = pinnedNamesIn(hire);
    if (stepTwoPinned.join("|") !== "Save as template|Cancel|Create Agent") {
        throw new Error(`step two pins Cancel and the primary, got `
            + stepTwoPinned.join("|"));
    }
    // Step two is a crumb on the frame's trail, and the frame's ‹ goes back.
    const hireBack = () => hire.querySelector(".modal-head").querySelector(".modal-back");
    const hireTrail = hire.querySelector(".modal-trail").querySelectorAll("li")
        .map((node) => node.textContent).join(" › ");
    if (!hireBack() || hireBack().hidden || hireTrail !== "Agents › New agent") {
        throw new Error(`step two lost the way back above its form, trail "${hireTrail}"`);
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
    // By ID, not by position: the row was [Back, Cancel, primary] and Back has
    // since moved into the step's body, so an index here silently pointed at
    // `undefined` rather than failing on what it meant to check.
    const primary = hire.querySelectorAll(".modal-actions")[0]
        .querySelector("#agent-form-submit");
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
    // Back is the frame's ‹ now, not a footer button — so this clicks the
    // control the operator actually sees at the top-left of step two.
    await hireBack().dispatchClick();
    await drain();
    if (modals().length !== 1) throw new Error("Back must not close the dialog");
    // Returning to step one is proven by the row emptying again AND by the
    // picker being on screen.
    if (pinnedNamesIn(hire).join("|") !== "") {
        throw new Error("Back must return to step one");
    }
    if (!hire.querySelector("#agent-pick-blank")) {
        throw new Error("Back must put the picker back on screen");
    }
    // Step one's row is empty, so the dismissal is the frame's ✕.
    await closeByX(hire);
    await drain();
    if (modals().length !== 0) throw new Error("the hire dialog must close");

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
    openAddAgent();
    await drain();
    const create = agentsModal();
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
    // A create opens the new agent's CONVERSATION and nothing else: a desk
    // springing open over the chat the operator just landed in would block
    // it, and the desk is one click away on the conversation's lamp.
    const createOpensTheConversationOnly = store.getState().conversationId === "new-1"
        && store.getState().conversationKind === "agent"
        && deskModal() === undefined;
    if (!createOpensTheConversationOnly) {
        throw new Error(`a create must open the conversation and no desk, got `
            + `${store.getState().conversationId} with ${modals().length} dialogs`);
    }

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
    desk.open("a1");
    await drain();
    await inDesk("#desk-edit").dispatchClick();
    await drain();
    const degradedForm = editModal();
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
    desk.close();
    await drain();

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
    openAddAgent();
    await drain();
    const sibling = agentsModal();
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
    openAddAgent();
    await drain();
    const quickDialog = agentsModal();
    const card = quickDialog.querySelectorAll(".picker-card")[0];
    if (!card) throw new Error("the picker must offer the installed template");
    await card.dispatchClick();
    await drain();
    const quickForm = quickDialog.querySelector("#agent-form");
    const templateSetAll = quickForm.querySelector('select[name="model_all"]');
    const advanced = quickForm.querySelector("#advanced-content");
    const advancedToggle = quickForm.querySelector("#advanced-toggle");
    if (!templateSetAll || !advanced || !advancedToggle) {
        throw new Error("picking a template must build the full form");
    }
    // The only disclosure left is Advanced, and it is the same shape of trap:
    // a panel the operator opens to READ, which must not be mistaken for an
    // answer. The fake dispatches nothing on its own, so the click is spelled
    // out and whatever is listening for it is run.
    const toggle = async () => {
        for (const fn of [...(advancedToggle.listeners.click || [])]) await fn({ target: advancedToggle });
    };
    const answer = async (control, value) => {
        control.value = value;
        for (const fn of [...(control.listeners.change || [])]) await fn({ target: control });
    };

    // The done bar lives in Advanced, and was auto-grown while Advanced was
    // hidden and measured 0. Opening the panel must size it again, or a long
    // done bar opens clipped with no scrollbar. The fake has no layout, so the
    // measurement is stubbed: content taller than the CSS ceiling allows.
    // stubControls keeps ids and names only, so the two markup facts this
    // rests on are read from the real markup and copied onto the fake nodes.
    const doneBar = quickForm.querySelector('textarea[name="done_fail_bar"]');
    if (!doneBar
        || !/<div id="advanced-content" class="hidden\b/.test(formMarkup)
        || !/<textarea name="done_fail_bar"[^>]*\bdata-autogrow\b/.test(formMarkup)) {
        throw new Error("the done bar must start inside the closed Advanced panel");
    }
    advanced.classList.add("hidden");
    doneBar.setAttribute("data-autogrow", "");
    doneBar.scrollHeight = 480;
    doneBar.clientHeight = 300;
    await toggle();
    const openingAdvancedSizesTheDoneBar = !advanced.classList.contains("hidden")
        && doneBar.style.height === "480px"
        && doneBar.style.overflowY === "auto";
    await toggle();
    if (!openingAdvancedSizesTheDoneBar || !advanced.classList.contains("hidden")) {
        throw new Error("opening Advanced must re-measure the done bar it hid");
    }

    const theTemplateFormAsksForAConnection = templateSetAll.hasAttribute("required")
        && Boolean(quickDialog.querySelector(".template-chip-text"))
        && quickForm.querySelector('input[name="role"]').value === "Reviews claims";
    if (!theTemplateFormAsksForAConnection) {
        throw new Error("a picked template must leave a required AI question in front");
    }

    // THE REDESIGN'S OWN PROPERTY, and the reason the layout above is gone:
    // the five selects and the colour swatches are on screen, not swept behind
    // a disclosure. `.quick-disclosure` used to hold both, so a template's
    // connection matrix and its colour were the two things the operator could
    // not see at the moment they created the agent.
    const nothingIsHiddenFromATemplate =
        MODEL_KEYS.every((key) => {
            const select = quickForm.querySelector(`select[name="${key}"]`);
            return Boolean(select) && advanced.querySelector(`select[name="${key}"]`) === null;
        })
        && quickForm.querySelectorAll('input[name="agent-color"]').length > 0
        && advanced.querySelectorAll('input[name="agent-color"]').length === 0
        && quickForm.querySelector(".quick-disclosure") === null;
    if (!nothingIsHiddenFromATemplate) {
        throw new Error("a template must not hide the matrix or the colour");
    }

    // Opened to read what is behind it, then closed again. Nothing else
    // touched — this is the invited interaction, and it used to be accepted as
    // the operator's answer to a question they were never asked.
    await toggle();
    await toggle();
    const readingTheDisclosureKeepsTheGuard = templateSetAll.hasAttribute("required");

    // An answer in the matrix is what releases it — any one of the five, so
    // an operator setting them by hand is not blocked by the select above.
    await answer(quickForm.querySelector('select[name="model_reasoning"]'), "c1");
    const answeringTheMatrixReleasesTheGuard = !templateSetAll.hasAttribute("required");
    // ...and clearing them all back to None re-arms it. Live in both
    // directions, which a one-way flip could never be.
    await answer(quickForm.querySelector('select[name="model_reasoning"]'), "");
    const clearingTheMatrixRearmsTheGuard = templateSetAll.hasAttribute("required");

    // The fan-out path: answering "Set All" itself writes the five FROM
    // SCRIPT, which fires no change event of their own — so this is also the
    // proof that the guard sees a programmatic answer, and that it is bound
    // after the fan-out rather than before it.
    await answer(templateSetAll, "c2");
    const fannedOutInQuick = MODEL_KEYS.map(
        (key) => quickForm.querySelector(`select[name="${key}"]`).value);
    const theQuickFanOutReleasesTheGuard = fannedOutInQuick.every((value) => value === "c2")
        && !templateSetAll.hasAttribute("required");
    if (!theQuickFanOutReleasesTheGuard) {
        throw new Error(`"Set All" must reach all five, got `
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

    // ─── 3g. The fifth route: refused at the create, not at a control ───
    //
    // Answer "Set All" — the fan-out fills the five and the guard releases —
    // then put all five back to None by hand. The guard re-arms, and it
    // changes nothing: the control it sits on was ANSWERED and still holds
    // that answer, so `required` is satisfied, native validation passes, and
    // buildSubmitData writes five nulls over "Saved successfully". That is the
    // fifth UI route to a connectionless agent; each of the four before it was
    // fixed at the control that exposed it and a new one appeared. So the
    // invariant moved to what would actually be SENT (spec 8.3), and this
    // drives the route end to end against it: real builder, real publish, real
    // buildSubmitData.
    //
    // The route is unchanged by the redesign, and that is the point of keeping
    // it here: the guard is a cheaper gate on a control that CAN be left
    // holding a stale answer, so the check on what is sent is what actually
    // closes the hole. Only the step that used to open a disclosure is gone —
    // the five are on screen now, so clearing them needs no panel opened.
    //
    // The POST is left SUCCEEDING for this section on purpose. A refusal proven
    // against an endpoint that refuses anyway proves nothing.
    createSucceeds = true;
    creates.length = 0;
    openAddAgent();
    await drain();
    const refusal = agentsModal();
    await refusal.querySelectorAll(".picker-card")[0].dispatchClick();
    await drain();
    const refusedForm = refusal.querySelector("#agent-form");
    const refusedSetAll = refusedForm.querySelector('select[name="model_all"]');
    if (!refusedSetAll) {
        throw new Error("the template pick must produce the full form again");
    }
    await answer(refusedSetAll, "c2");
    for (const key of MODEL_KEYS) {
        await answer(refusedForm.querySelector(`select[name="${key}"]`), "");
    }
    // The hole, spelled out before it is closed: the guard is armed again and
    // the submit still passes validation, because `required` asks the Set All
    // select for A VALUE and it has one.
    const theRearmedGuardIsAlreadySatisfied = refusedSetAll.hasAttribute("required")
        && refusedSetAll.value === "c2";
    if (!theRearmedGuardIsAlreadySatisfied) {
        throw new Error(`the route under test needs an armed-but-satisfied guard, got `
            + `required ${refusedSetAll.hasAttribute("required")} value ${refusedSetAll.value}`);
    }
    refusedForm.querySelector('input[name="name"]').value = "Connectionless";
    refusedForm.querySelector('input[name="role"]').value = "Edited by hand";
    const refusedPrimary = documentStub.querySelector("#agent-form-submit");
    await refusedPrimary.dispatchClick();
    await drain();
    const refusalLine = refusal.querySelector("#agent-save-feedback");
    const theFiveNullCreateIsRefused = creates.length === 0
        && modals().length === 1
        // Told what is missing, and where to answer it ON THIS PATH. The
        // control named is the one the operator can actually see and reach —
        // the AI Connections section, which is on screen in both create paths
        // now. A refusal naming a control the operator cannot see is a dead
        // end wearing the clothes of a gate, which is why the sentence is
        // chosen from the form's own recorded shape rather than written once.
        && refusalLine.textContent.includes("no AI connection")
        && refusalLine.textContent.includes("Choose one under AI Connections")
        // The other shape's sentence must NOT appear: this operator has two
        // connections configured, so "add a connection in Settings" is not
        // their next action — it is the tail of the same sentence, offered
        // only as the alternative, and never the whole instruction.
        && !refusalLine.textContent.includes("Add a connection in Settings;")
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
    await answer(refusedSetAll, "c2");
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
    openAddAgent();
    await drain();
    const blank = agentsModal();
    await blank.querySelector("#agent-pick-blank").dispatchClick();
    await drain();
    const blankForm = blank.querySelector("#agent-form");
    // The matrix in place, and every one of the five the save reads on screen
    // — not a class that no longer exists, which any form would satisfy.
    if (!blankForm.querySelector('select[name="model_all"]')
        || !MODEL_KEYS.every((key) => Boolean(blankForm.querySelector(`select[name="${key}"]`)))
        || blankForm.querySelector("#advanced-content")
            .querySelector('select[name="model_work"]')) {
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

    // ...and the OTHER shape: a template picked with NOTHING configured. The
    // matrix renders its link to Settings and no select at all, so there is no
    // control a `required` could sit on — the guard arms nothing, and it says
    // so rather than inventing a stand-in. A stand-in is what used to be here:
    // an unanswerable `required` select that wrote to nothing, whose only job
    // was to make native validation refuse. The refusal is now made where it
    // can be EXPLAINED — the dialog withholds its primary before the operator
    // can reach it, and the reason is in the live region the button points at.
    // An empty list is a HEALTHY read, so this is not CONNECTIONS_FAILED.
    connectionsEmpty = true;
    creates.length = 0;
    openAddAgent();
    await drain();
    const bare = agentsModal();
    await bare.querySelectorAll(".picker-card")[0].dispatchClick();
    await drain();
    const bareForm = bare.querySelector("#agent-form");
    if (bareForm.querySelector('select[name="model_all"]')) {
        throw new Error("with nothing configured the matrix must offer no select");
    }
    if (!bareForm.querySelector("#btn-goto-connections")) {
        throw new Error("the unconfigured matrix must link to Settings");
    }
    const barePrimary = documentStub.querySelector("#agent-form-submit");
    const bareLine = bare.querySelector("#agent-save-feedback");
    const theUnconfiguredCreateIsWithheldNotOffered = creates.length === 0
        // Refused BEFORE the click, which the unanswerable select could never
        // do: it let the operator fill the whole form and press Create first.
        && barePrimary.disabled === true
        // ...and never silently. The withheld button names the line that says
        // why, which is the contract the failed-read path already uses.
        && barePrimary.getAttribute("aria-describedby") === "agent-save-feedback"
        && bareLine.textContent.includes("No AI connection is configured")
        && bareLine.textContent.includes("Add one in Settings")
        // The reachable control is named, and it is the one on screen.
        && bareLine.textContent.includes("AI Connections section links there")
        // NOT the failed-read sentence: this read landed, and telling the
        // operator to reopen the dialog would send them round a loop that
        // cannot fix it.
        && !bareLine.textContent.includes("Couldn’t read");
    if (!theUnconfiguredCreateIsWithheldNotOffered) {
        throw new Error(`an unconfigured create must be withheld with a reason, got `
            + `${creates.length} creates, disabled ${barePrimary.disabled}, `
            + `line "${bareLine.textContent}"`);
    }
    // And the save path refuses it too, if it is ever reached another way —
    // `requestSubmit()` does not consult a disabled button. Same live region,
    // and the sentence chosen for THIS shape: Settings, not a matrix that is
    // not there.
    bareForm.querySelector('input[name="name"]').value = "Nothing to answer with";
    for (const fn of [...(bareForm.listeners.submit || [])]) {
        await fn({ preventDefault() {}, target: bareForm });
    }
    await drain();
    const theUnconfiguredRefusalSendsThemToSettings = creates.length === 0
        && modals().length === 1
        && bareLine.textContent.includes("no AI connection")
        && bareLine.textContent.includes("Add a connection in Settings")
        && bareLine.textContent.includes("AI Connections section links there")
        // Not the matrix shape's sentence: there is no matrix to choose in.
        && !bareLine.textContent.includes("Choose one under");
    if (!theUnconfiguredRefusalSendsThemToSettings) {
        throw new Error(`an unconfigured refusal must send them to Settings, got `
            + `${creates.length} creates, line "${bareLine.textContent}"`);
    }
    await bare.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((b) => b.textContent === "Cancel").dispatchClick();
    await drain();
    connectionsEmpty = false;
    createSucceeds = false;

    // ─── 3h. The refusal is CREATE-only; an edit with five nulls still saves ───
    //
    // An agent with no connection is a real row: the roster predates the
    // invariant, and a connection can be deleted out from under one. Refusing
    // that save would trap the operator in a dialog they cannot leave without
    // losing every other edit they came to make — so EDIT stays permissive and
    // the guarantee sits only where the agent is brought into being.
    updates.length = 0;
    desk.open("a1");
    await drain();
    await inDesk("#desk-edit").dispatchClick();
    await drain();
    const editing = editModal();
    const editingForm = editing.querySelector("#agent-form");
    for (const key of MODEL_KEYS) {
        editingForm.querySelector(`select[name="${key}"]`).value = "";
    }
    editingForm.querySelector('input[name="name"]').value = "Jim";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    // The save closes the edit layer and leaves the desk it was opened from.
    const anEditWithNoConnectionStillSaves = updates.length === 1
        && updates[0].id === "a1"
        && MODEL_KEYS.every((key) => updates[0].body[key] === null)
        && editModal() === undefined
        && modals().length === 1 && Boolean(deskModal());
    if (!anEditWithNoConnectionStillSaves) {
        throw new Error(`an edit must never be refused for having no connection, got `
            + `${updates.length} updates ${JSON.stringify(updates[0] || {})} `
            + `${modals().length} modals`);
    }
    desk.close();
    await drain();

    // ─── 3i. Recreating a RECENT agent, through the real form ───
    //
    // A snapshot is the setup of an agent made here, one that may since have
    // been deleted, and the form it fills CREATES. So it reaches the builders
    // as `prefill` — values — and everything the old single `agent` argument
    // also decided (Delete, the recovery tools, the policy read by id, the
    // duplicate-name self-exclusion, whether the save creates) keeps reading
    // the one that means IDENTITY. This drives the real builders and reads
    // the markup they wrote, because that is where a filled field lives.
    personalities = [
        { id: "p1", name: "Terse", prompt_template: "Be terse." },
    ];
    snapshots = [{
        id: "s1", agent_id: "gone-1", name: "Ada", role: "Code Auditor",
        description: "Reads a diff and reports what is not true.",
        done_fail_bar: "A checkable allow/deny exists.",
        communication: {
            tone: "direct", density: "compact", jargon: "light", audience: "operator",
        },
        // Matches no personality: the dropdown gets the kept option.
        prompt_template: "You are terse, and you cite files.",
        color: "#1d4ed8",
        model_social: null, model_work: "llama3.1:8b",
        // No connection offers this one any more.
        model_reasoning: "qwen3.8-27b",
        model_extraction: null, model_self_queue: null,
        desk_x: 3, desk_y: 4,
        prompt_history_policy: {
            last_n_histories: 7, max_allowed_history_tokens: 900,
            earliest_ts_allowed: null, include_notifications: false,
        },
        captured_at: "2026-09-20T09:00:00Z", deleted_at: "2026-09-21T10:30:00Z",
    }];
    createSucceeds = true;
    creates.length = 0;
    updates.length = 0;
    openAddAgent();
    await drain();
    const recreate = agentsModal();
    const recentRow = recreate.querySelectorAll(".market-rail-item")
        .find((node) => node.querySelector(".market-rail-label").textContent === "Recent");
    await recentRow.dispatchClick();
    await drain();
    await recreate.querySelector("#picker-recent-0").dispatchClick();
    await drain();

    // Every field the snapshot carries, filled — and the three it never
    // carries (the connection secrets) are not in the markup to fill.
    const filled = [
        'name="name"', 'value="Ada"',
        'value="Code Auditor"',
        "Reads a diff and reports what is not true.",
        // The done bar is a textarea now, so its value is the element body.
        '>A checkable allow/deny exists.</textarea>',
        'value="#1d4ed8"',
    ].every((fragment) => formMarkup.includes(fragment));
    const recreateFillsTheFormFromTheSnapshot = filled
        // The colour it had comes back CHECKED, so a save does not recolour it.
        && /value="#1d4ed8"[^>]*\s+checked/.test(formMarkup)
        // The communication block, and the desk it sat at.
        && /<option value="direct"\s+selected>/.test(formMarkup)
        && /<option value="compact"\s+selected>/.test(formMarkup)
        && /<option value="3,4"\s+selected>/.test(formMarkup)
        // The connection it can still be linked to, by model name.
        && /<option value="c1"[^>]*selected>/.test(formMarkup)
        && !formMarkup.includes("api_key")
        && !formMarkup.includes("api_base_url");
    if (!recreateFillsTheFormFromTheSnapshot) {
        throw new Error("a recreate must fill every field the snapshot carries");
    }

    // A create, not an edit: no Delete, no recovery tools, no runtime pill.
    const recreateIsACreateNotAnEdit = recreate.querySelector("#btn-delete-agent") === null
        && recreate.querySelector("#btn-clear-chat-history") === null
        && recreate.querySelector("#btn-reset-runtime") === null
        && recreate.querySelector("#agent-runtime-status-pill") === null;
    if (!recreateIsACreateNotAnEdit) {
        throw new Error("a recreate must render no Delete and no recovery tools");
    }

    // The model no connection offers any more is NAMED, under the matrix it
    // is missing from — an empty select would lose the choice in silence.
    const theMissingModelIsNamed = formMarkup.includes('id="agent-connection-missing"')
        && formMarkup.includes("Reasoning: qwen3.8-27b — no connection offers this model now")
        // Only the missing one.
        && !formMarkup.includes("Work: llama3.1:8b");
    if (!theMissingModelIsNamed) {
        throw new Error(`the missing model must be named under the matrix`);
    }

    // The prompt no personality carries any more rides in on its own option,
    // with the text itself in a hidden input for the save to send.
    const keptOptionCarriesThePrompt = formMarkup.includes('value="__kept__" selected>Kept from Ada<')
        && formMarkup.includes('name="prompt_template_kept" '
            + 'value="You are terse, and you cite files."');
    if (!keptOptionCarriesThePrompt) {
        throw new Error("a prompt no personality matches must be kept on the form");
    }

    // ...and the save sends THAT text, as a create.
    const recreateForm = recreate.querySelector("#agent-form");
    recreateForm.querySelector('select[name="personality_id"]').value = "__kept__";
    recreateForm.querySelector('[name="prompt_template_kept"]').value =
        "You are terse, and you cite files.";
    recreateForm.querySelector('select[name="model_work"]').value = "c1";
    recreateForm.querySelector('input[name="name"]').value = "Ada II";
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const recreateSavesAsACreateWithTheKeptPrompt = creates.length === 1
        && updates.length === 0
        && creates[0].name === "Ada II"
        && creates[0].prompt_template === "You are terse, and you cite files."
        && creates[0].model_work === "llama3.1:8b";
    if (!recreateSavesAsACreateWithTheKeptPrompt) {
        throw new Error(`a recreate must POST a new agent carrying the kept prompt, got `
            + `${creates.length} creates ${JSON.stringify(creates[0] || {})}`);
    }
    if (modals().length !== 0) throw new Error("a successful create must close the dialog");

    // A prompt a personality DOES still carry is that personality, not a kept
    // option: the dropdown matches by text, exactly as it does for an edit.
    snapshots = [{ ...snapshots[0], id: "s2", prompt_template: "Be terse." }];
    openAddAgent();
    await drain();
    const matched = agentsModal();
    await matched.querySelectorAll(".market-rail-item")
        .find((node) => node.querySelector(".market-rail-label").textContent === "Recent")
        .dispatchClick();
    await drain();
    await matched.querySelector("#picker-recent-0").dispatchClick();
    await drain();
    const aMatchedPromptIsJustThatPersonality = !formMarkup.includes("__kept__")
        && !formMarkup.includes("prompt_template_kept")
        && /<option value="p1"\s+selected>/.test(formMarkup);
    if (!aMatchedPromptIsJustThatPersonality) {
        throw new Error("a prompt a personality still carries must select that personality");
    }
    await closeByX(matched);
    await drain();
    createSucceeds = false;
    personalities = [];
    snapshots = [];

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
        recreateFillsTheFormFromTheSnapshot,
        recreateIsACreateNotAnEdit,
        theMissingModelIsNamed,
        keptOptionCarriesThePrompt,
        recreateSavesAsACreateWithTheKeptPrompt,
        aMatchedPromptIsJustThatPersonality,
        columnHoldsOnlyTheOffice,
        oneDeskAtATime,
        deskDrainsOnClose,
        toolsAreInTheHead,
        optionsMenuHoldsTheRest,
        taskRowIsAButtonWithTheSharedPill,
        taskRowOpensTheTask,
        aSubtaskPushesAThirdCrumb,
        aCancelRefreshesTheDeskRows,
        taskBackReturnsToTheDesk,
        aFailedTaskListIsSaidOnTheDesk,
        anotherDeskClosesTheWholeStack,
        aRemovalClosesTheWholeStack,
        closingTheDeskClosesTheViewerOverIt,
        seeAllOpensTheAgentsTasks,
        removeClosesTheDesk,
        aRenameRetitlesTheDesk,
        anAgentGoneFromTheRosterClosesTheDesk,
        chatToolOpensTheConversation,
        scheduleSectionRendersRows,
        scheduleSectionSaysEmpty,
        scheduleSectionSaysError,
        scheduleRetryRecovers,
        timesUseTheSharedClock,
        scheduleRefusalsSayWhy,
        otherActivityIsIgnored,
        aScheduleRunRefreshesTheRows,
        aRowOpensTheScheduleLayer,
        anEditPatchesOnlyWhatChanged,
        newOpensInEditMode,
        weeklyNeedsAWeekday,
        aCreatePostsTheExactRule,
        leavingTheDeskClosesTheScheduleLayer,
        repeatModeShowsOnlyItsControls,
        anOvernightWindowIsRefusedHere,
        aRepeatPostsMinutesAndAWindow,
        aStoredRepeatEditsInHours,
        runNowIsDisabledWhileRunning,
        runNowStartsARealRun,
        anOpenRunRefusalSaysWhy,
        theDraftIsPreviewed,
        anInvalidDraftIsSaidNotSent,
        aNewScheduleCanBeSavedOff,
        anOpenRunShowsThePausedTone,
        aSkipOffersTheOpenRun,
        theLockAndTheAuthorShow,
        setUpByShows,
        theLockSwitchPatches,
        createSendsTheLock,
        opensOnThePathItWasGiven,
        createOpensTheConversationOnly,
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
        editOpensThePanelModal,
        hireOpensTheAgentsDialog,
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
        diagnosticsFiltersTheLog,
        removeWarnsWhatIsDeleted,
        removeWaitsForTheName,
        removeOpensOnceTheNameIsIn,
        deleteWarnsWhatIsDeleted,
        pinnedPrimarySubmitsTheForm,
        pinnedPrimaryDoesNotCloseTheDialog,
        setAllFansOutAfterPublish,
        theFanOutIsWhatIsSaved,
        aFailedConnectionsReadStillRendersTheForm,
        theBlockedPrimaryHandsOverTheKeyboard,
        theTemplateFormAsksForAConnection,
        nothingIsHiddenFromATemplate,
        readingTheDisclosureKeepsTheGuard,
        answeringTheMatrixReleasesTheGuard,
        clearingTheMatrixRearmsTheGuard,
        theQuickFanOutReleasesTheGuard,
        theQuickCreateSavesTheConnection,
        theRearmedGuardIsAlreadySatisfied,
        theFiveNullCreateIsRefused,
        theBlankRefusalNamesTheMatrix,
        theUnconfiguredCreateIsWithheldNotOffered,
        theUnconfiguredRefusalSendsThemToSettings,
        theCorrectedCreateGoesThrough,
        aRejectedSiblingKeepsTheOtherReads,
        aDegradedFormStillSaves,
        anEditWithNoConnectionStillSaves,
    }));
    // Cosmetic cleanup timers must not hold the harness open after its verdict.
    mock.timers.reset();
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
