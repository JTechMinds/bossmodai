/**
 * Node harness: Add agent — the picker's four states, the two steps, and the
 * quick layout a template gets — inside the Agents dialog, whose other tab is
 * the Marketplace.
 *
 * Invoked by tests/test_add_agent_modal.py. Not a browser bundle.
 *
 * The DIALOG is real (context/agents-dialog.js, its tabs, the one-form slot)
 * and so is the Add agent pane. The Marketplace pane is STUBBED: it is
 * tests/js_marketplace_harness.cjs's subject, and what this one needs from it
 * is the seam — the two callbacks the dialog hands it, captured so a test can
 * press "Add agent from this" or report a library change, and how often the
 * dialog asked it to start reading.
 *
 * The roster row's two-door menu is in here too: it is the other half of "add
 * an agent", it hangs off core/overlays.js's menu the way the dialog hangs off
 * the modal, and both doors open this same dialog.
 *
 * RECENT and SAVE AS TEMPLATE are here for the same reason: what the PANE does
 * with a snapshot pick — which arguments reach the form build, what the footer
 * offers, what reaches the server — rather than what the form renders from it,
 * which is the real form's and is driven in tests/js_context_harness.cjs.
 *
 * The FORM is stubbed and the dialog is real. context/agent-form.js builds its
 * markup as a string and the shared fake parses no HTML, so this hands
 * buildFormHTML a hand-built form with the same SHAPE the real one has — the
 * name field, then #role-contract-card, then the connection matrix, then the
 * Advanced disclosure, nested the way the quick layout has to move them. What
 * the real markup contains is tests/js_agent_form_harness.cjs's subject; what
 * this one is about is what the two steps do to it.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModFormat", "BossModAgentStatus",
    "BossModOverlayFocus", "BossModOverlays", "BossModMenuSelect", "BossModGates",
    "BossModAgentApi", "BossModAgentTemplatesApi", "BossModAgentFields",
    "BossModCommunication",
    // The two the fake form leans on rather than reimplementing: the shape
    // vocabulary the refusal reads back, and the connection guard itself.
    "BossModAgentFormConnections", "BossModAgentFormBindings",
    "BossModAgentFormHydrate", "BossModAgentRecovery", "BossModAgentFormSave",
    // The picker draws the local library with the marketplace's own card and
    // rail builders, so its dependencies load ahead of it — and filters it
    // with the app's toolbar search.
    "BossModAvatar", "BossModMarketplaceItems", "BossModPackCard", "BossModFilterRail",
    "BossModSearchField", "BossModTabs",
    // Save as template asks its one question with the strip the marketplace
    // detail builds, so that view and the two modules it leans on load too.
    "BossModMarketplaceWithheld", "BossModMarketplaceSections", "BossModMarketplaceDetail",
    "BossModAgentTemplatePicker",
    "BossModAgentFormTemplate", "BossModAgentSaveTemplate", "BossModAgentDialogFooter",
    "BossModAgentAddPane", "BossModAgentDialogSlot",
    "BossModAgentEdit", "BossModAgentsDialog", "BossModAddAgentMenu",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const h = global.BossModDom.h;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

const PIN = "aa11bb2ccccccccccccccccccccccccccccccccc";

// `sections` is a COMPUTED field on the real AgentTemplate model — the server
// derives it from `description` and `what_done_looks_like` through
// describe_pack on every read, so no row the API can return is ever without
// one. The fixture carried none, which made it a shape the API cannot produce;
// marketplace-items.js's projection refuses such a row by design (a card drawn
// from a broken payload is worse than one that says it could not be read), so
// the omission surfaced the moment the picker started using that projection.
// These are the all-`None` sections describe_pack returns for text carrying no
// recognised heading, which is what this prose is.
function sectionsFor(row) {
    return {
        description: {
            preamble: row.description, mission: null, in_scope: null,
            out_of_scope: null, handoff: null,
        },
        done: { preamble: row.what_done_looks_like, fail_examples: null },
    };
}

function template(overrides) {
    const row = Object.assign({
        id: "t1", source: "catalog", pack_id: "code-auditor", source_url: null,
        category: "engineering", title: "Code Auditor",
        specialty: "Reviews claims",
        description: "Reads a diff and reports what is not true. Cites files.",
        what_done_looks_like: "A checkable allow/deny exists.",
        personality_hint: "Software Engineer", tools_hint: ["work"],
        author_name: "JTech Minds", author_url: "https://github.com/JTechMinds",
        commit_sha: PIN, content_hash: "hash-1",
        installed_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
    }, overrides);
    // Derived AFTER the overrides, so a fixture that changes the description
    // cannot leave the parsed halves describing the old one.
    return Object.assign(row, { sections: sectionsFor(row) }, overrides.sections
        ? { sections: overrides.sections } : {});
}

/**
 * One agent snapshot, as GET /api/agent-snapshots answers. No api_key, no
 * api_base_url and no extra_body — the table has no column for them — so a
 * fixture that carried one would be a shape the route cannot produce.
 */
function snapshot(overrides) {
    return Object.assign({
        id: "s1", agent_id: "a1", name: "Ada", role: "Code Auditor",
        description: "Reads a diff and reports what is not true.",
        done_fail_bar: "A checkable allow/deny exists.",
        communication: {
            tone: "direct", density: "compact", jargon: "light", audience: "operator",
        },
        prompt_template: "You are terse.", color: "#1d4ed8",
        model_social: null, model_work: "llama3.1:8b", model_reasoning: null,
        model_extraction: null, model_self_queue: null,
        desk_x: 3, desk_y: 4,
        prompt_history_policy: {
            last_n_histories: 7, max_allowed_history_tokens: 900,
            earliest_ts_allowed: null, include_notifications: false,
        },
        captured_at: "2026-09-20T09:00:00Z",
        deleted_at: "2026-09-21T10:30:00Z",
    }, overrides);
}

const SNAPSHOTS = [
    snapshot({}),
    // Still on the roster: saved, not deleted.
    snapshot({
        id: "s2", agent_id: "a2", name: "Bo", role: "Writer",
        description: "Drafts release notes.", deleted_at: null,
        model_work: "gpt-4o-mini", prompt_template: null,
    }),
];

const TEMPLATES = [
    template({}),
    template({
        id: "t2", pack_id: "feature-planner", category: "product-design",
        title: "Feature Planner", specialty: "Plans features",
        description: "Breaks a goal into steps.", personality_hint: null,
        author_name: null, author_url: null,
    }),
];

// ─── The form the dialog builds ───

function select(name, id, options) {
    const el = h("select", { name, id });
    options.forEach(([value, label]) => el.append(h("option", { value }, label)));
    el.value = "";
    return el;
}

/**
 * The matrix, in whichever of its two shapes agent-form-connections.js would
 * have built: the "Set All" select over the grid, or — with nothing configured
 * — a link to Settings and no select at all. The second one is the shape that
 * used to produce an agent with five null connections.
 */
function buildConnections() {
    if (connectionMode === "none") {
        return h("section", { class: "form-section" },
            h("h3", { class: "form-section-title" }, "AI Connections"),
            h("p", { class: "field-hint" }, "No connections configured. ",
                h("button", { type: "button", id: "btn-goto-connections" },
                    "Add one in Settings")));
    }
    const allSelect = select("model_all", "agent-connection-model_all",
        [["", "— Set all connections —"], ["c1", "Local (llama)"]]);
    // ALL FIVE, as agent-form-connections.js builds them. One stood in for the
    // grid while nothing here read more than one of them; the connection guard
    // reads every one — "is any of them answered" is not a question a single
    // select can be asked — and a stub that offered fewer would be a form the
    // guard is entitled to refuse.
    const matrix = h("div", { class: "connection-grid" },
        ...global.BossModAgentFields.MODEL_TYPES.map((type) => select(
            type.key, `agent-connection-${type.key}`,
            [["", "None"], ["c1", "Local (llama)"]])));
    return h("section", { class: "form-section" },
        h("h3", { class: "form-section-title" }, "AI Connections"),
        h("div", { class: "field" },
            h("label", { for: "agent-connection-model_all" }, "Set All"),
            allSelect),
        h("hr", { class: "form-rule" }),
        matrix);
}

function buildForm() {
    const name = h("input", { name: "name", placeholder: "e.g. PM Agent", required: true });
    const role = h("input", { name: "role" });
    const description = h("textarea", { name: "description" });
    const colour = h("input", { type: "radio", name: "agent-color", value: "#1d4ed8" });
    colour.checked = true;
    const card = h("div", { id: "role-contract-card" }, role, description, colour);

    const connections = buildConnections();

    const done = h("input", { name: "done_fail_bar" });
    const personality = h("select", { name: "personality_id" });
    personality.append(h("option", { value: "" }, "No personality"),
        h("option", { value: "p1" }, "Software Engineer"));
    const advancedContent = h("div", { id: "advanced-content", class: "hidden" },
        done, personality);
    const advancedToggle = h("button", { type: "button", id: "advanced-toggle" }, "Advanced");
    // The real disclosure toggles `.hidden` on click (context/agent-form.js).
    // Reproduced here because section 6 opens and closes it to prove that a
    // panel toggle is not an answer to the connection question.
    advancedToggle.addEventListener("click", () => {
        advancedContent.classList.toggle("hidden");
    });
    const advanced = h("div", {}, advancedToggle, advancedContent);

    const form = h("form", { id: "agent-form" },
        h("div", { class: "form-section" },
            h("h3", { class: "form-section-title" }, "Identity"),
            h("div", {}, h("label", {}, "Name"), name),
            card),
        connections, advanced);

    // WHAT agent-form.js BINDS, reproduced in the same order, because the two
    // things it binds to this markup are the subject of section 6 and neither
    // lives in a module this stub could skip:
    //
    //   1. the "Set All" fan-out, which writes the five FROM SCRIPT;
    //   2. bindConnectionGuard, AFTER it, so the guard reads the five once the
    //      fan-out has written them — a script-assigned value fires no change
    //      event, so the order is load-bearing and is asserted in
    //      tests/test_add_agent_modal.py.
    //
    // The guard is the REAL module (context/agent-form-bindings.js). A stub of
    // it would be a stub of the rule under test.
    const setAll = form.querySelector('select[name="model_all"]');
    if (setAll) {
        setAll.addEventListener("change", () => {
            if (!setAll.value) return;
            global.BossModAgentFields.MODEL_TYPES.forEach((type) => {
                const control = form.querySelector(`select[name="${type.key}"]`);
                if (control) control.value = setAll.value;
            });
        });
    }
    form.setAttribute(
        global.BossModAgentFormConnections.AI_QUESTION,
        global.BossModAgentFormConnections.shapeFor(
            connectionMode === "none" ? [] : CONNECTIONS),
    );
    global.BossModAgentFormBindings.bindConnectionGuard(form, { creating: true });
    return form;
}

// ─── Stubs the dialog reaches for ───

let libraryMode = "ready";
let snapshotMode = "ready";
// How POST /api/agent-templates/local answers: "ready", "taken" (the 409 the
// Replace question is raised by, once) or "fail".
let localSaveMode = "ready";
// Every local-template body the layer posted, in order.
const localSaves = [];
let connectionMode = "ready";
// How GET /api/connections answers the read the SUBMIT path resolves against:
// "reject" is the offline shape, "error" the 500 whose body is an object and
// not a list. Both used to end as `connections = []`, which is the same thing
// the form says when the operator has none configured.
let connectionsApiMode = "ready";
let formMode = "ready";
// The Marketplace pane the dialog built last: the callbacks it was handed, and
// how many times the dialog told it that it is on screen.
let marketDeps = null;
let marketPanes = 0;
let marketActivations = 0;
// How often the dialog told the Marketplace pane the library had changed.
let marketRefreshes = 0;
// Resolved by the test that holds a save open, so the in-flight footer can be
// read before the server answers.
let holdCreate = null;
// Held so a SECOND pick can be made while the first is still building — one
// host, two renders, and a primary that finds its form by id. Claimed by the
// build that starts next, so holding one does not hold the one after it.
let nextBuildHold = null;
// Every agent POST /api/agents was asked to create, in order. WHICH draft
// reached the server is the whole subject of the pick-race tests.
const creates = [];
// Every PATCH /api/agents/{id}. A recreate must never be one: the form it
// fills creates a NEW agent, and the snapshot is values, not identity.
const updates = [];
// What GET /api/connections answers with when it answers at all. It is what
// the five stub selects offer, so the submit stub resolves a real value rather
// than a placeholder.
const CONNECTIONS = [
    { id: "c1", name: "Local (llama)", model: "llama3.1:8b", api_base_url: "http://local/v1" },
];

function jsonResponse(body, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
    });
}

global.apiFetch = (url, init) => {
    if (String(url) === "/api/agent-templates/local") {
        localSaves.push(JSON.parse(init.body));
        if (localSaveMode === "fail") {
            return jsonResponse({ detail: { code: "boom", message: "The write failed." } }, 500);
        }
        if (localSaveMode === "taken" && !JSON.parse(init.body).replace) {
            return jsonResponse({
                detail: {
                    code: "local_title_taken",
                    message: 'A local template named "Code Auditor" already exists.',
                },
            }, 409);
        }
        const body = JSON.parse(init.body);
        return jsonResponse(template({
            id: "t-local", source: "local", pack_id: null, source_url: null,
            category: body.category, title: body.title, specialty: body.specialty,
            description: body.description,
            what_done_looks_like: body.what_done_looks_like,
            personality_hint: body.personality_hint, tools_hint: [],
            author_name: null, author_url: null, commit_sha: null, content_hash: null,
        }), 201);
    }
    if (String(url).startsWith("/api/agent-snapshots")) {
        if (snapshotMode === "fail") {
            return jsonResponse({ detail: { code: "read_failed", message: "boom" } }, 500);
        }
        return jsonResponse(snapshotMode === "empty" ? [] : SNAPSHOTS);
    }
    if (String(url).startsWith("/api/agent-templates")) {
        if (libraryMode === "fail") {
            return jsonResponse({ detail: { code: "read_failed", message: "boom" } }, 500);
        }
        return jsonResponse(libraryMode === "empty" ? [] : TEMPLATES);
    }
    if (String(url) === "/api/agents" && init && init.method === "POST") {
        creates.push(JSON.parse(init.body));
        // Held, then REFUSED: the subject is the primary's disabled state on
        // the way in and on the way out, and a refusal keeps the dialog up to
        // be read instead of closing it and taking the footer with it.
        const refuse = () => jsonResponse({ detail: "Name already taken" }, 409);
        return holdCreate ? holdCreate.then(refuse) : refuse();
    }
    const agentPatch = String(url).match(/^\/api\/agents\/([^/]+)$/);
    if (agentPatch && init && init.method === "PATCH") {
        updates.push({ id: agentPatch[1], body: JSON.parse(init.body) });
        return jsonResponse({ id: agentPatch[1] });
    }
    if (String(url) === "/api/connections") {
        if (connectionsApiMode === "reject") return Promise.reject(new Error("offline"));
        if (connectionsApiMode === "error") return jsonResponse({ detail: "boom" }, 500);
        // ONE endpoint feeds both reads in the real app — the form's own
        // (loadFormData) and the submit path's (readConnections) — so
        // `connectionMode` has to move both or the fixture is a state the
        // server cannot produce: a form saying "none configured" over a list
        // that holds two.
        return jsonResponse(connectionMode === "none" ? [] : CONNECTIONS);
    }
    return jsonResponse([]);
};
// What the last build was handed. `agent` is IDENTITY and `prefill` is
// VALUES, and a recreate must pass the snapshot as the second: the real form
// decides everything else from which of the two it got.
let builtWith = null;
global.BossModAgentForm = {
    buildFormHTML: async (container, agent = null, prefill = null) => {
        builtWith = { agent, prefill };
        if (formMode === "fail") throw new Error("the form could not be built");
        const hold = nextBuildHold;
        nextBuildHold = null;
        if (hold) await hold;
        // Re-read AFTER the hold as well, so a build can be made to fail at
        // the moment it lands rather than only at the moment it starts. The
        // difference is the whole subject of section 18: what the operator did
        // in between is what the failure path has to notice.
        if (formMode === "fail") throw new Error("the form could not be built");
        container.replaceChildren(buildForm());
    },
};
// Reads the form it is handed AND the connections it is handed, so a test can
// tell WHICH draft reached the server and WHAT that draft would have written.
// Answering `{}` for every form is exactly the hole a create from the previous
// pick slipped through unnoticed; ignoring `connections` was the same hole one
// layer down, and it is why no harness could watch a save write five nulls.
global.BossModAgentSubmit = {
    buildSubmitData: async (form, connections) => {
        const byId = new Map((connections || []).map((conn) => [conn.id, conn]));
        const agentData = { name: form.querySelector('input[name="name"]').value };
        for (const type of global.BossModAgentFields.MODEL_TYPES) {
            const control = form.querySelector(`select[name="${type.key}"]`);
            const conn = control ? byId.get(control.value) : null;
            agentData[type.key] = conn ? conn.model : null;
            if (conn && !agentData.connection_id) {
                agentData.connection_id = conn.id;
                agentData.api_base_url = conn.api_base_url;
            }
        }
        return { agentData, promptHistoryPolicy: {} };
    },
};
global.BossModMarketplace = {
    createPane(deps) {
        marketPanes += 1;
        marketDeps = deps;
        // One control, the way the real browse head has its filter, so a test
        // can tell which pane holds the keyboard.
        const element = h("div", { class: "market-host" },
            h("input", { id: "market-find", type: "search" }));
        return {
            element,
            activate() { marketActivations += 1; },
            refreshLibrary() { marketRefreshes += 1; return Promise.resolve(); },
        };
    },
};

const store = { setState() {} };

// ─── Reading the dialog ───

const dialogs = () => documentStub.body.children
    .filter((node) => String(node.className).includes("modal-panel"));
const dialog = () => dialogs()[0] || null;
const footer = () => dialog().querySelectorAll(".modal-actions")[0];
const footerNames = () => footer().querySelectorAll("button").map((b) => b.textContent);
const find = (selector) => dialog().querySelector(selector);
const cards = () => dialog().querySelectorAll(".picker-card");
// The Agents dialog's two tabs, and the panel each one controls.
const tabOf = (id) => find(`#agents-tab-${id}`);
const panelOf = (id) => find(`#agents-panel-${id}`);
const selectedTab = () => ["add", "marketplace"]
    .filter((id) => tabOf(id).getAttribute("aria-selected") === "true").join("|");

/** Type into a field the way an operator does: value, then the event. */
async function type(input, value) {
    input.value = value;
    for (const fn of [...(input.listeners.input || [])]) await fn({ target: input });
}

/** Answer one connection select the way an operator does. */
async function choose(control, value) {
    control.value = value;
    for (const fn of [...(control.listeners.change || [])]) await fn({ target: control });
}

/**
 * Answer the AI question on the form currently in the dialog.
 *
 * Every CREATE has to get past it now: agent-form-save.js refuses one whose
 * five `model_*` keys are all null (spec 8.3), so a section whose subject is
 * the footer, the claim or the keyboard has to answer it before it can watch
 * a save happen at all. One of the FIVE, directly — this harness stubs
 * `BossModAgentForm`, so nothing here fans `model_all` out to them.
 */
async function answerAi() {
    await choose(find('select[name="model_work"]'), "c1");
}

async function open(tab = "add") {
    const handle = global.BossModAgentsDialog.open({ store, tab });
    await drain();
    return handle;
}

/** Operator clicks a tab of the Agents dialog. */
async function clickTab(id) {
    await tabOf(id).dispatchClick();
    await drain();
}

/** The frame's ✕: the exit on every tab, and the only one on step one. */
async function closeByX() {
    await dialog().querySelector(".modal-close").dispatchClick();
    await drain();
}

/**
 * Dismiss the dialog.
 *
 * By NAME, not by position. The last action on step two is the primary, and
 * the primary is `<button type="submit" form="agent-form">` — clicking it
 * saved the draft instead of dismissing it, which is only invisible while
 * every stubbed save succeeds. What a save does to the footer is section 12's
 * subject; this one is the dismissal.
 */
async function close() {
    const cancel = footer().querySelectorAll("button")
        .find((button) => button.textContent === "Cancel");
    if (!cancel) throw new Error("the dialog's footer has no Cancel");
    await cancel.dispatchClick();
    await drain();
}

/** The chip's line, or "" while no template is on screen. */
function chipText() {
    const node = find(".template-chip-text");
    return node ? node.textContent : "";
}

/** The Recent cards on screen. They are `.picker-card`s like the templates —
 *  one grid — and `.picker-recent` is what tells the two apart. */
const recentCards = () => dialog().querySelectorAll(".picker-recent");

/** One rail row, by its visible label. */
function railRow(label) {
    return dialog().querySelectorAll(".market-rail-item")
        .find((node) => node.querySelector(".market-rail-label").textContent === label) || null;
}

/** The layer on top: Save as template opens one over the dialog beneath it. */
const layer = () => dialogs()[dialogs().length - 1] || null;
const layerButton = (label) => layer().querySelectorAll("button")
    .find((button) => button.textContent === label) || null;

/** The footer action that opens the Save as template layer. */
const saveTemplateAction = () => footer().querySelector("#agent-save-template");

/**
 * Open a blank create form while the connections read is failing, and report
 * what the operator is told and what reaches the server.
 *
 * @param {"reject"|"error"} mode
 * @returns {Promise<{told: boolean, saved: number}>}
 */
async function connectionsFailure(mode) {
    connectionsApiMode = mode;
    creates.length = 0;
    await open();
    await find("#agent-pick-blank").dispatchClick();
    await drain();
    const primary = documentStub.querySelector("#agent-form-submit");
    const line = find("#agent-save-feedback");
    const told = primary.disabled === true
        && Boolean(line)
        && line.textContent.includes("Couldn’t read your AI connections")
        // Withheld WITH a reason: the line that carries it is named as this
        // button's description rather than left as loose text near it.
        && primary.getAttribute("aria-describedby") === "agent-save-feedback";
    await type(find('input[name="name"]'), "CONNECTIONLESS");
    await primary.dispatchClick();
    // And the handler refuses on its own account: implicit submission never
    // goes through the primary, so a disabled button is not the only gate.
    const form = find("#agent-form");
    for (const fn of [...(form.listeners.submit || [])]) await fn({ preventDefault() {} });
    await drain();
    const saved = creates.length;
    await close();
    connectionsApiMode = "ready";
    return { told, saved };
}

async function main() {
    const verdict = {};

    // ─── 1. An empty library still offers Blank, and a way to fill itself ───
    libraryMode = "empty";
    await open();
    verdict.emptyState = dialog().textContent.includes("No templates installed yet.")
        && Boolean(find("#agent-pick-blank"))
        && Boolean(find("#agent-template-browse"));
    // BACK IS ON THE TITLE ROW — the dialog's `lead` — and it is not drawn on
    // step one, which has nowhere to go back to. It spent a round as a
    // bordered square floating in the band under the title, aligned to
    // nothing, and a round before that as a footer action beside Cancel.
    verdict.backLeadsTheTitleRowAndNotStepOne =
        Boolean(dialog().querySelector(".modal-head"))
        && Boolean(find("#agent-add-back"))
        && find("#agent-add-back").hidden === true
        // In the head, never in the body or the row: the body is emptied by a
        // failed build and the row is rebuilt on every step swap.
        && Boolean(dialog().querySelector(".modal-head").querySelector("#agent-add-back"))
        && footer().querySelector("#agent-add-back") === null;
    // Step one's row is EMPTY now: no primary (there is no form for it to
    // submit), no `Browse marketplace` (the Marketplace is the tab beside this
    // one), and no Cancel (the frame's ✕ is the exit on every tab).
    verdict.noCreateOnStepOne = footerNames().join("|") === ""
        && documentStub.querySelector("#agent-add-browse") === null
        && documentStub.querySelector("#agent-form-submit") === null
        && find("#agent-form") === null;

    // ONE takeover, titled for both errands, with the two tabs in its head —
    // the Office header's quiet group — and a panel per tab, named by it. The
    // Marketplace pane was BUILT, hidden, and not yet told to read anything.
    const tools = dialog().querySelector(".modal-tools");
    const tablist = tools ? tools.querySelector(".tabs") : null;
    verdict.theDialogIsOneTakeoverWithTwoTabs = dialogs().length === 1
        && dialog().getAttribute("data-size") === "takeover"
        && dialog().getAttribute("aria-label") === "Agents"
        && dialog().getAttribute("data-dialog") === "agents"
        && Boolean(tablist)
        && tablist.getAttribute("role") === "tablist"
        && tablist.querySelectorAll(".tab").map((t) => t.textContent).join("|")
            === "Add agent|Marketplace"
        && selectedTab() === "add"
        && tabOf("add").getAttribute("aria-controls") === "agents-panel-add"
        && panelOf("add").getAttribute("role") === "tabpanel"
        && panelOf("add").getAttribute("aria-labelledby") === "agents-tab-add"
        && panelOf("marketplace").getAttribute("aria-labelledby") === "agents-tab-marketplace"
        && panelOf("add").hidden === false
        && panelOf("marketplace").hidden === true
        && marketPanes === 1
        && marketActivations === 0
        // No footer of the dialog's own: an empty action row.
        && footer().children.length === 0;

    // ─── 2. The empty library's door SWITCHES TABS; nothing closes ───
    //
    // It used to close this dialog and open the marketplace takeover, whose
    // ✕ then reopened this one behind it. The Marketplace is the other tab of
    // the same dialog now, so the door selects it: one panel still open, the
    // picker hidden rather than destroyed, the pane told it is on screen, and
    // the keyboard on the tab — the button that was pressed has just been put
    // away with its pane.
    await find("#agent-template-browse").dispatchClick();
    await drain();
    verdict.emptyLibraryBrowseSwitchesTab = dialogs().length === 1
        && selectedTab() === "marketplace"
        && panelOf("add").hidden === true
        && panelOf("marketplace").hidden === false
        && marketActivations === 1
        && documentStub.activeElement === tabOf("marketplace")
        && footerNames().join("|") === ""
        // Hidden, never destroyed.
        && Boolean(find("#agent-template-browse"));
    // An install over there re-reads the library over here, so the template
    // is in the picker when the operator switches back.
    libraryMode = "ready";
    marketDeps.onLibraryChanged();
    await drain();
    await clickTab("add");
    verdict.libraryChangedRefreshesPicker = cards().length === 2
        && selectedTab() === "add"
        && panelOf("add").hidden === false
        && panelOf("marketplace").hidden === true;
    await closeByX();

    // ─── 3. A library that could not be READ says so, and retries ───
    libraryMode = "fail";
    await open();
    const failedCopy = dialog().textContent
        .includes("Couldn’t read your templates or your recent agents.")
        && Boolean(find("#agent-template-retry"));
    // A retry that fails again rebuilds the button that was clicked, so focus
    // has to land on its replacement rather than on <body>.
    await find("#agent-template-retry").dispatchClick();
    await drain();
    const retryKeptFocus = documentStub.activeElement === find("#agent-template-retry");
    libraryMode = "ready";
    await find("#agent-template-retry").dispatchClick();
    await drain();
    verdict.failedThenRetry = failedCopy && cards().length === 2;
    verdict.retryPlacesFocus = retryKeptFocus
        && documentStub.activeElement === find("#agent-template-find");

    // ─── 4. The rail of categories, and the filter that narrows the grid ───
    //
    // The categories are RAIL ROWS now, not headings inside the card grid —
    // the same organisation the marketplace takeover uses over the same rows,
    // built by the same module. Two groups, because `All` is a scope and the
    // rest are the catalog's own buckets; a flat list of three claimed the
    // library had three categories when it has two.
    const railLabels = () => dialog().querySelectorAll(".market-rail-label")
        .map((node) => node.textContent).join("|");
    const cardTitles = () => cards()
        .map((node) => node.querySelector(".market-card-title").textContent).join("|");
    // THE AUTHOR IS A NAME, not the object it is carried in. The projection
    // puts `{name, url}` on an item, the card prints the name, and for one
    // round the unwrap lived in the marketplace's caller instead of in the
    // shared builder — so the marketplace printed "JTech Minds" and this
    // picker, handing the same item to the same builder, printed
    // "[object Object]" under every card. A shape known to the projection and
    // to the renderer must not have to be known by everything in between.
    const cardAuthors = () => cards()
        .map((node) => {
            const foot = node.querySelector(".market-card-author");
            return foot ? foot.textContent : "";
        }).join("|");
    // Both halves: the row that HAS an author prints the name, and the row
    // whose `author_name` is null prints no footer author at all rather than
    // an empty band.
    verdict.cardsPrintTheAuthorName = cardAuthors() === "JTech Minds|"
        && !cardAuthors().includes("object Object");
    // `Recent` joins `All` in the Show group — the two SCOPES — and the
    // catalog's own buckets stay under Categories.
    verdict.categories = railLabels() === "All|Recent|Engineering|Product Design"
        && dialog().querySelectorAll(".market-rail-title")
            .map((node) => node.textContent).join("|") === "Show|Categories"
        // Counted, so the rail says how much is behind each row: two
        // templates, two snapshots, and the categories the templates are in.
        && dialog().querySelectorAll(".market-rail-count")
            .map((node) => node.textContent).join("|") === "2|2|1|1";

    // A rail row narrows the grid, and the row that was clicked keeps the
    // keyboard — the rail repaints its live mark in place rather than being
    // rebuilt, which is what the marketplace needs focus-restoration code for.
    const engineering = dialog().querySelectorAll(".market-rail-item")
        .find((node) => node.querySelector(".market-rail-label").textContent === "Engineering");
    await engineering.dispatchClick();
    await drain();
    // The row SURVIVES ITS OWN CLICK — same node, still in the document, with
    // the live mark moved onto it. That is what lets focus stay where the
    // operator put it without any code to put it back: the rail repaints
    // `aria-current` in place rather than being rebuilt. (Node identity is the
    // property to assert here, not `activeElement`: the fake DOM does not move
    // focus on a dispatched click, so reading it back would be asserting the
    // fake rather than the rail.)
    verdict.railNarrowsTheGridAndKeepsFocus = cardTitles() === "Code Auditor"
        && engineering.getAttribute("aria-current") === "true"
        && dialog().querySelectorAll(".market-rail-item").includes(engineering);
    const all = dialog().querySelectorAll(".market-rail-item")
        .find((node) => node.querySelector(".market-rail-label").textContent === "All");
    await all.dispatchClick();
    await drain();
    verdict.railReturnsToAll = cards().length === 2
        && engineering.getAttribute("aria-current") === null;

    await type(find("#agent-template-find"), "diff");
    verdict.filterNarrowsTheGrid = cardTitles() === "Code Auditor"
        // The rail counts the LIBRARY, not the filter — the same rule the
        // marketplace's rail follows, so a count never moves under a keystroke.
        && railLabels() === "All|Recent|Engineering|Product Design";
    await type(find("#agent-template-find"), "zzz");
    verdict.noMatchCopy = dialog().textContent.includes("No template matches “zzz”.")
        && cards().length === 0
        // Blank is offered in EVERY state, including this one: it is the one
        // affordance that always works, so it is never filtered away.
        && Boolean(find("#agent-pick-blank"));
    await type(find("#agent-template-find"), "");

    // ─── 5. Picking a template: the SAME form, prefilled and marked ───
    await cards()[0].dispatchClick();
    await drain();
    verdict.backShowsOnStepTwo = find("#agent-add-back").hidden === false;
    verdict.stepTwoFooter = footerNames().join("|")
        === "Save as template|Cancel|Create Agent"
        // Back left the row for the top-left of the step body, which is
        // where every other back control in this app lives.
        && Boolean(find("#agent-add-back"));
    verdict.provenanceChip = find(".template-chip-text").textContent
        === "Code Auditor · JTech Minds · pinned aa11bb2";

    const form = find("#agent-form");
    const field = (selector) => form.querySelector(selector);
    verdict.hydrated = field('input[name="role"]').value === "Reviews claims"
        && field('textarea[name="description"]').value
            === "Reads a diff and reports what is not true. Cites files."
        && field('input[name="done_fail_bar"]').value === "A checkable allow/deny exists."
        && field('select[name="personality_id"]').value === "p1";
    // The three the operator owns. A template that could write these would
    // overwrite a draft, and a name it filled would be accepted by accident.
    verdict.operatorFieldsUntouched = field('input[name="name"]').value === ""
        && field('input[name="agent-color"]').checked === true
        && field('select[name="model_work"]').value === ""
        && field('input[name="name"]').getAttribute("placeholder") === "e.g. Code Auditor";

    // THE CONNECTION QUESTION IS ON SCREEN, in the matrix, and it is required
    // until it is answered. It used to be a single select LIFTED out beside
    // Name with the matrix swept behind a disclosure — so the five selects the
    // save actually reads, and the colour swatches, were the two things a
    // template hid.
    const setAll = field('select[name="model_all"]');
    verdict.connectionOnScreen = Boolean(setAll)
        && setAll.hasAttribute("required")
        && Boolean(form.querySelector(".connection-grid"))
        // Every one of the five the submit path reads, and none of them behind
        // the one disclosure that is left.
        && global.BossModAgentFields.MODEL_TYPES.every((type) => {
            const select = form.querySelector(`select[name="${type.key}"]`);
            return Boolean(select)
                && form.querySelector("#advanced-content").querySelector(
                    `select[name="${type.key}"]`) === null;
        });
    // ONE LAYOUT: nothing a template answered is hidden, and the disclosure
    // that used to hold it does not exist.
    verdict.templateHidesNothing = form.querySelector(".quick-disclosure") === null
        && form.querySelector("#role-contract-card") !== null
        && form.querySelectorAll('input[name="agent-color"]').length > 0
        && form.querySelector("#advanced-content").querySelectorAll(
            'input[name="agent-color"]').length === 0;
    // The tool hints the pack declares are part of the decision being made
    // here, and they are the one template fact with no field to live in — so
    // the chip carries them and everything else is a visible field now.
    verdict.summaryNamesTheTools = form.querySelector(".template-tools").textContent
        === "Tools: work";
    verdict.focusLandsOnName = documentStub.activeElement === field('input[name="name"]');
    // Kept for section 9, which proves Blank renders exactly these.
    const templateSections = form.querySelectorAll(".form-section-title")
        .map((node) => node.textContent).join("|");

    // ─── 6. The connection guard tracks the ANSWER, not a disclosure ───
    //
    // Opening a panel used to drop `required` from the lifted select, one way
    // and for good — and that panel was where the template's specialty,
    // description and what-done lived, so opening it to READ them disarmed the
    // guard and Create wrote five null connections over "Saved successfully".
    // What relaxes it is an answer in the matrix, and only that.
    //
    // Re-pointed at Advanced, the one disclosure that remains. The property is
    // not about any particular panel: a panel toggle is not an answer.
    const advancedToggle = form.querySelector("#advanced-toggle");
    await advancedToggle.dispatchClick();
    await advancedToggle.dispatchClick();
    verdict.expandingAloneKeepsTheGuard = setAll.hasAttribute("required");
    // Any ONE of the five is an answer: the operator setting the matrix by
    // hand must not be blocked by the convenience select above it.
    const perType = global.BossModAgentFields.MODEL_TYPES
        .map((type) => form.querySelector(`select[name="${type.key}"]`));
    await choose(perType[1], "c1");
    verdict.answeringOneTypeReleasesTheGuard = !setAll.hasAttribute("required");
    // ...and clearing them all back to None re-arms it. A live check in both
    // directions, which is what a one-way flip could never be.
    await choose(perType[1], "");
    verdict.clearingThemAllRearmsTheGuard = setAll.hasAttribute("required")
        && perType.every((sel) => sel.value === "");

    // ─── 7. Back keeps the draft, and returns focus to the Find box ───
    await type(field('input[name="name"]'), "Mine");
    await find("#agent-add-back").dispatchClick();
    await drain();
    const backedOut = dialogs().length === 1
        && footerNames().join("|") === ""
        && documentStub.activeElement === find("#agent-template-find");
    await cards()[0].dispatchClick();
    await drain();
    verdict.backKeepsTheDraft = backedOut
        && find("#agent-form").querySelector('input[name="name"]').value === "Mine"
        && dialog().querySelectorAll(".template-chip").length === 1;

    // ─── 8. Dismissing the chip drops to blank without touching the draft ───
    await find("#quick-provenance-clear").dispatchClick();
    await drain();
    const after = find("#agent-form");
    verdict.chipClearsTemplateOnly = after.querySelector('input[name="role"]').value === ""
        && after.querySelector('textarea[name="description"]').value === ""
        && after.querySelector('input[name="done_fail_bar"]').value === ""
        && after.querySelector('input[name="name"]').value === "Mine"
        && after.querySelector('input[name="name"]').getAttribute("placeholder") === "e.g. PM Agent"
        && after.querySelector(".template-chip") === null
        && after.querySelector(".template-tools") === null
        // The GUARD is untouched by Remove template, and that is the change
        // this pins: it tracks whether the matrix has been answered, and
        // dropping a template answers nothing. It used to be released here,
        // which meant "Remove template" quietly re-opened the route to a
        // connectionless agent. The five are still at None, so it stays armed.
        && after.querySelector('select[name="model_all"]').hasAttribute("required");
    // Every field the template wrote, including the one applyHireFields only
    // ever set: a personality left selected under a cleared form is a template
    // that Remove template did not remove.
    verdict.chipClearsThePersonalityToo =
        after.querySelector('select[name="personality_id"]').value === "";

    // ─── 9. Blank builds the SAME form, minus the chip ───
    //
    // This is the property the whole redesign is for: the two create paths
    // differ by a provenance chip and four prefilled values, and by nothing
    // else. It used to be the opposite — Blank got the plain form, a template
    // got a rearranged one — so the operator met two different dialogs
    // depending on which cell they clicked.
    await find("#agent-add-back").dispatchClick();
    await drain();
    await find("#agent-pick-blank").dispatchClick();
    await drain();
    const blank = find("#agent-form");
    const sectionsOf = (root) => root.querySelectorAll(".form-section-title")
        .map((node) => node.textContent).join("|");
    verdict.blankIsThePlainForm = blank.querySelector(".template-chip") === null
        && blank.querySelector(".template-tools") === null
        && blank.querySelector("#role-contract-card") !== null
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        && Boolean(find("#agent-add-back"));
    // Same sections, same order, whichever cell was picked — and the guard is
    // armed on BOTH, because an agent with no connection fails on its first
    // turn however it was created. Blank used to carry no guard at all.
    verdict.blankAndTemplateRenderTheSameSections =
        sectionsOf(blank) === "Identity|AI Connections"
        && sectionsOf(blank) === templateSections
        && blank.querySelector('select[name="model_all"]').hasAttribute("required") === true;
    await close();
    verdict.closes = dialogs().length === 0;

    // ─── 10. No AI connection configured: said in front, and refused ───
    connectionMode = "none";
    await open();
    await cards()[0].dispatchClick();
    await drain();
    const bare = find("#agent-form");
    const barePrimary = documentStub.querySelector("#agent-form-submit");
    const bareLine = dialog().querySelector("#agent-save-feedback");
    // The section renders its link to Settings and NO select — there is
    // nothing to choose, so there is no control to make required. A stand-in
    // `required` select that wrote to nothing used to stand here: it was a
    // control the operator could not answer, and it let them fill the whole
    // form before native validation stopped them.
    verdict.noConnectionsIsToldInFront =
        bare.querySelector('select[name="model_all"]') === null
        && bare.querySelector("#btn-goto-connections") !== null
        // On screen, not behind the one disclosure that is left.
        && bare.querySelector("#advanced-content")
            .querySelector("#btn-goto-connections") === null;
    // Refused BEFORE the click, and never silently: the withheld primary names
    // the live region that says why.
    verdict.noConnectionsBlocksCreate = barePrimary.disabled === true
        && barePrimary.getAttribute("aria-describedby") === "agent-save-feedback"
        && bareLine.textContent.includes("No AI connection is configured")
        && bareLine.textContent.includes("Add one in Settings");
    // Dropping the template answers nothing, so the block stays. There are no
    // five selects here that could ever answer it.
    await find("#quick-provenance-clear").dispatchClick();
    await drain();
    verdict.noConnectionsStaysBlockedAfterTheChipGoes =
        documentStub.querySelector("#agent-form-submit").disabled === true
        && find("#agent-form").querySelector('select[name="model_all"]') === null;
    await close();
    connectionMode = "ready";

    // ─── 11. A form that cannot render leaves a footer that can recover ───
    formMode = "fail";
    await open();
    await cards()[0].dispatchClick();
    await drain();
    verdict.failedRenderDropsTheDeadPrimary =
        dialog().textContent.includes("The agent editor failed to load.")
        && dialog().textContent.includes("Go back and pick again.")
        && footerNames().join("|") === "Cancel"
        // The primary submits #agent-form by id, and there is no longer one.
        && documentStub.querySelector("#agent-form-submit") === null
        && find("#agent-form") === null
        && documentStub.activeElement === find("#agent-add-back");
    formMode = "ready";
    await find("#agent-add-back").dispatchClick();
    await drain();
    await cards()[0].dispatchClick();
    await drain();
    verdict.pickingAgainAfterAFailedRenderRetries = Boolean(find("#agent-form"))
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        && Boolean(find("#agent-add-back"));
    await close();

    // ─── 12. The primary is disabled for as long as the save is running ───
    await open();
    await find("#agent-pick-blank").dispatchClick();
    await drain();
    await type(find('input[name="name"]'), "Mine");
    await answerAi();
    let releaseCreate;
    holdCreate = new Promise((resolve) => { releaseCreate = resolve; });
    // NOT awaited: the point is what the footer looks like mid-flight.
    const saving = documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    const busy = documentStub.querySelector("#agent-form-submit");
    verdict.primaryIsDisabledWhileSaving = busy.disabled === true
        && busy.textContent === "Creating…";
    releaseCreate();
    await saving;
    await drain();
    const settled = documentStub.querySelector("#agent-form-submit");
    verdict.primaryComesBackWhenTheSaveIsRefused = settled.disabled === false
        && settled.textContent === "Create Agent"
        && dialogs().length === 1;
    holdCreate = null;
    await close();

    // ─── 13. A pick that is still building cannot save the one before it ───
    //
    // `Create Agent` finds its form BY ID, and a build is four requests long.
    // Pick, draft, Back, pick another: the FIRST form was still mounted, still
    // named `agent-form` and still listening, so the primary posted the draft
    // the operator had just left — and then took them to that agent.
    creates.length = 0;
    await open();
    await cards()[0].dispatchClick();
    await drain();
    await type(find('input[name="name"]'), "DRAFT-FROM-TEMPLATE-A");
    // A's draft is answered too, or the mid-build click below is refused by
    // the connection INVARIANT rather than by the withheld primary this
    // section exists to test — and a `building()` that stopped disabling
    // would then still read as passing.
    await answerAi();
    await find("#agent-add-back").dispatchClick();
    await drain();
    let releaseBuild;
    nextBuildHold = new Promise((resolve) => { releaseBuild = resolve; });
    await cards()[1].dispatchClick();
    await drain();
    const midBuild = documentStub.querySelector("#agent-form-submit");
    await midBuild.dispatchClick();
    await drain();
    verdict.theBuildingPickCannotSaveTheLastOne = creates.length === 0;
    // Withheld, and the operator can see that it is: a control that refuses
    // without saying so is the dead primary the failure path already removed.
    verdict.theWithheldPrimarySaysWhy = midBuild.disabled === true
        && midBuild.textContent === "Loading…";
    releaseBuild();
    await drain();
    // ...and once its own form is there, the primary saves THAT one.
    await type(find('input[name="name"]'), "AGENT-FROM-TEMPLATE-B");
    await answerAi();
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    verdict.theSavedAgentIsThePickOnScreen =
        creates.map((body) => body.name).join("|") === "AGENT-FROM-TEMPLATE-B";
    await close();

    // ─── 14. A superseded build must not land on top of the one that won ───
    //
    // Back-then-pick leaves two renders over one host. The first arriving last
    // replaced the winner's form and chip with the template the operator had
    // already left — and what was recorded as built was written BEFORE the
    // build, so it named the other one and re-picking it did nothing at all.
    await open();
    let releaseFirst;
    nextBuildHold = new Promise((resolve) => { releaseFirst = resolve; });
    await cards()[0].dispatchClick();
    await drain();
    await find("#agent-add-back").dispatchClick();
    await drain();
    await cards()[1].dispatchClick();
    await drain();
    const showedTheSecond = chipText().startsWith("Feature Planner");
    releaseFirst();
    await drain();
    verdict.aSupersededBuildNeverLands = showedTheSecond
        && chipText().startsWith("Feature Planner")
        && dialog().querySelectorAll(".template-chip").length === 1
        && find('input[name="role"]').value === "Plans features";
    // What is recorded as built is what is on screen, so re-picking that cell
    // is the no-op it claims to be...
    await find("#agent-add-back").dispatchClick();
    await drain();
    await cards()[1].dispatchClick();
    await drain();
    verdict.theRecordedBuildIsTheOneOnScreen = chipText().startsWith("Feature Planner")
        && find('input[name="role"]').value === "Plans features";
    // ...and the pick whose build lost rebuilds rather than reading as built.
    await find("#agent-add-back").dispatchClick();
    await drain();
    await cards()[0].dispatchClick();
    await drain();
    verdict.theLosingPickRebuilds = chipText().startsWith("Code Auditor")
        && find('input[name="role"]').value === "Reviews claims";
    await close();

    // ─── 15. A connections read that FAILED is not "none configured" ───
    const rejected = await connectionsFailure("reject");
    const errored = await connectionsFailure("error");
    verdict.aFailedConnectionsReadIsToldAndBlocks = rejected.told && errored.told;
    verdict.aFailedConnectionsReadSavesNothing =
        rejected.saved === 0 && errored.saved === 0;

    // ─── 16. A refused save cannot re-enable a build's withheld primary ───
    //
    // Back and Cancel are live while a save runs. Fill A, click Create, press
    // Back, pick B: B's build correctly withholds the primary — and then A's
    // save is REFUSED, and its `finally` painted the button live again while
    // A's form was still the mounted `#agent-form`. One click posted
    // DRAFT-FROM-A, closed the dialog and left the operator looking at an
    // agent they had walked away from. `hireSubmit.busy()` is no help: that
    // run has finished. The withhold belongs to B's render, so B's render is
    // the only thing allowed to clear it.
    creates.length = 0;
    await open();
    await cards()[0].dispatchClick();
    await drain();
    await type(find('input[name="name"]'), "DRAFT-FROM-A");
    await answerAi();
    let releaseRefusal;
    holdCreate = new Promise((resolve) => { releaseRefusal = resolve; });
    const refusedSave = documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    await find("#agent-add-back").dispatchClick();
    await drain();
    let releaseSecond;
    nextBuildHold = new Promise((resolve) => { releaseSecond = resolve; });
    await cards()[1].dispatchClick();
    await drain();
    releaseRefusal();
    await refusedSave;
    await drain();
    holdCreate = null;
    const withheld = documentStub.querySelector("#agent-form-submit");
    verdict.aRefusedSaveCannotReviveAWithheldPrimary = withheld.disabled === true
        && withheld.textContent === "Loading…";
    await withheld.dispatchClick();
    await drain();
    verdict.aRefusedSaveCannotPostTheDraftLeftBehind =
        creates.map((body) => body.name).join("|") === "DRAFT-FROM-A";
    releaseSecond();
    await drain();
    await close();

    // ─── 17. A build cannot repaint a dialog that is not its own ───
    //
    // The primary was found with a document-wide lookup, so a create dialog
    // dismissed mid-build relabelled whatever dialog happened to be open when
    // its build finally landed. The worst shape is here on purpose: the dead
    // build's own connections read fails, so it left a healthy EDIT dialog
    // with a permanently disabled primary described by an empty hidden line,
    // and no way out but closing and reopening.
    await open();
    let releaseOrphan;
    nextBuildHold = new Promise((resolve) => { releaseOrphan = resolve; });
    await cards()[0].dispatchClick();
    await drain();
    await close();
    global.BossModAgentEdit.openAgentModal({ store, agent: { id: "a9", name: "Ada" } });
    await drain();
    const editPrimary = documentStub.querySelector("#agent-form-submit");
    const editIsHealthy = editPrimary.disabled === false
        && editPrimary.textContent === "Save Changes";
    connectionsApiMode = "reject";
    releaseOrphan();
    await drain();
    connectionsApiMode = "ready";
    verdict.anOrphanedBuildLeavesTheOpenDialogAlone = editIsHealthy
        && documentStub.querySelector("#agent-form-submit") === editPrimary
        && editPrimary.disabled === false
        && editPrimary.textContent === "Save Changes"
        && editPrimary.getAttribute("aria-describedby") === null;
    await close();

    // ─── 18. A build that fails AFTER Back leaves step one alone ───
    //
    // pickTemplate checks which step it landed on; failed() did not. Back is
    // live while a build runs, so the failure of a build the operator has left
    // rewrote step ONE's footer — `Browse marketplace`, the only door out of
    // an empty library, replaced by a second Back that led to the step already
    // on screen, and then given focus — and wrote its error paragraph into the
    // hidden form host, where nobody could read it.
    await open();
    let releaseDoomed;
    nextBuildHold = new Promise((resolve) => { releaseDoomed = resolve; });
    await cards()[0].dispatchClick();
    await drain();
    await find("#agent-add-back").dispatchClick();
    await drain();
    formMode = "fail";
    releaseDoomed();
    await drain();
    formMode = "ready";
    verdict.aBuildFailingAfterBackLeavesThePickerAlone =
        // Step one's row is empty, and a failure that rewrote step one would
        // have put the recovery row's `Cancel` into it.
        footerNames().join("|") === ""
        // The way to the marketplace is still where it lives — the dialog's
        // tab — and still not selected.
        && selectedTab() === "add"
        && !dialog().textContent.includes("The agent editor failed to load.")
        && cards().length === 2
        && documentStub.activeElement === find("#agent-template-find");
    // ...and nothing stayed recorded as built, so that cell is a retry.
    await cards()[0].dispatchClick();
    await drain();
    verdict.pickingAgainAfterABuriedFailureRetries = Boolean(find("#agent-form"))
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        && Boolean(find("#agent-add-back"));
    await close();

    // ─── 19. The keyboard, while the primary is taken away and given back ───
    //
    // Disabling the control the operator just pressed drops focus to <body>.
    // The line that now says what is happening is where it goes — it is a live
    // region and a focus target for exactly this — and the button takes the
    // keyboard back the moment it is live again.
    await open();
    await find("#agent-pick-blank").dispatchClick();
    await drain();
    await type(find('input[name="name"]'), "Focused");
    await answerAi();
    const pressed = documentStub.querySelector("#agent-form-submit");
    pressed.focus();
    let releaseFocusSave;
    holdCreate = new Promise((resolve) => { releaseFocusSave = resolve; });
    const focusSave = pressed.dispatchClick();
    await drain();
    const line = find("#agent-save-feedback");
    verdict.theBusyPrimaryHandsTheKeyboardToTheLine =
        documentStub.activeElement === line
        && line.getAttribute("role") === "status"
        && line.getAttribute("aria-live") === "polite"
        && line.getAttribute("tabindex") === "-1";
    releaseFocusSave();
    await focusSave;
    await drain();
    holdCreate = null;
    const settledPrimary = documentStub.querySelector("#agent-form-submit");
    verdict.theSettledPrimaryTakesTheKeyboardBack =
        documentStub.activeElement === settledPrimary
        && settledPrimary.disabled === false
        && settledPrimary.textContent === "Create Agent";
    await close();

    // ─── 20. A rebuilt footer does not un-withhold the primary ───
    //
    // Back and re-picking the SAME template is documented to keep the draft,
    // so it rebuilds the ROW without rebuilding the form. setActions makes a
    // fresh button at the resting label and enabled, and the state its render
    // is holding — here `blocked`, because that render could not read the
    // connections it would resolve against — went with the old button. The
    // form still refused on click, but the control said nothing about it and
    // the reason was no longer named as its description.
    connectionsApiMode = "error";
    creates.length = 0;
    await open();
    await cards()[0].dispatchClick();
    await drain();
    await find("#agent-add-back").dispatchClick();
    await drain();
    await cards()[0].dispatchClick();
    await drain();
    const rebuilt = documentStub.querySelector("#agent-form-submit");
    verdict.aRebuiltFooterKeepsTheWithhold = rebuilt.disabled === true
        && rebuilt.textContent === "Create Agent"
        && rebuilt.getAttribute("aria-describedby") === "agent-save-feedback";
    await close();
    connectionsApiMode = "ready";

    // ─── 21. The roster row's menu: an icon, then a label, on both doors ───
    //
    // `Browse Marketplace →` and `+ Add Agent` were two different ideas of
    // where a mark goes — one glyph hung off the end of a label, one typed into
    // the front of it, and neither of them the shell's own icon system. Both
    // rows are a lucide icon followed by the text that names them now.
    const hireRow = h("button", { class: "roster-hire", type: "button" }, "Add agent");
    const railHost = h("div", { class: "roster" }, hireRow);
    documentStub.body.append(railHost);
    const addAgent = global.BossModAddAgentMenu.createAddAgentMenu({
        anchor: hireRow, container: railHost, store,
    });
    addAgent.toggle();
    await drain();
    const doors = railHost.querySelectorAll(".add-agent-choice");
    const marks = doors.map((row) => row.querySelector("i"));
    verdict.bothMenuDoorsCarryALucideIcon = doors.length === 2
        && marks.every((mark) => Boolean(mark)
            // Decorative: the accessible name is the label beside it, so a
            // bundle that failed to load costs the row nothing it is named by.
            && mark.getAttribute("aria-hidden") === "true"
            && Boolean(mark.getAttribute("data-lucide")))
        // In FRONT of the label, on both, so the two labels start on one edge.
        && doors.every((row) => row.children[0] === row.querySelector("i"));
    // No trailing glyph on either row, and no literal `+` typed into a label.
    verdict.neitherMenuDoorCarriesATrailingArrow = doors
        .every((row) => !/[→+]/.test(row.textContent));
    // `blocks`, and specifically NOT `building`: shell/places.js spends that one
    // on the Office, and two doors in one shell wearing the same mark is a mark
    // that identifies neither of them.
    verdict.theMarketplaceDoorIsBlocksAndNotTheOfficesIcon =
        doors[0].textContent === "Agent Marketplace"
        && marks[0].getAttribute("data-lucide") === "blocks"
        && marks[0].getAttribute("data-lucide") !== "building"
        && doors[1].textContent === "Add Agent"
        && marks[1].getAttribute("data-lucide") === "plus";
    // And the door still opens what it names, with the panel away first: the
    // Agents takeover, on its Marketplace tab, the pane told it is on screen.
    const activatedBefore = marketActivations;
    await doors[0].dispatchClick();
    await drain();
    verdict.theMarketplaceDoorStillOpensTheTakeover = dialogs().length === 1
        && dialog().getAttribute("data-size") === "takeover"
        && selectedTab() === "marketplace"
        && panelOf("marketplace").hidden === false
        && panelOf("add").hidden === true
        && marketActivations === activatedBefore + 1
        && railHost.querySelectorAll(".add-agent-choice").length === 0
        && hireRow.getAttribute("aria-expanded") === "false";
    await closeByX();
    // The other door opens the SAME dialog, on the other tab.
    addAgent.toggle();
    await drain();
    await railHost.querySelectorAll(".add-agent-choice")[1].dispatchClick();
    await drain();
    verdict.theAddAgentDoorOpensTheSameDialogOnItsTab = dialogs().length === 1
        && dialog().getAttribute("aria-label") === "Agents"
        && selectedTab() === "add"
        && panelOf("add").hidden === false
        && Boolean(find("#agent-pick-blank"));
    // Each tab wears the mark of the door that opens it — one definition,
    // the dialog's ICONS, so a door and its tab cannot drift apart. Icon
    // first and decorative; the label is still the tab's whole text.
    const tabMark = (id) => tabOf(id).querySelector("i");
    verdict.eachTabWearsItsDoorsMark = Boolean(tabMark("add"))
        && Boolean(tabMark("marketplace"))
        && tabMark("marketplace").getAttribute("data-lucide")
            === marks[0].getAttribute("data-lucide")
        && tabMark("add").getAttribute("data-lucide") === marks[1].getAttribute("data-lucide")
        && ["add", "marketplace"].every((id) => tabMark(id).getAttribute("aria-hidden") === "true"
            && tabOf(id).children[0] === tabMark(id))
        && tabOf("add").textContent === "Add agent"
        && tabOf("marketplace").textContent === "Marketplace";
    await closeByX();

    // ─── 22. The picker filters with the app's toolbar search ───
    //
    // A magnifier inside one bordered box, the words that were a visible
    // <label> now the input's accessible name — the control Tasks and the Log
    // already use, and the one the Marketplace tab puts in the same place.
    await open();
    const pickerHead = find(".picker-head");
    const pickerBox = pickerHead.querySelector(".search-field");
    verdict.thePickerFilterIsTheToolbarSearch = Boolean(pickerBox)
        && pickerBox.querySelector("#agent-template-find") === find("#agent-template-find")
        && find("#agent-template-find").getAttribute("aria-label") === "Find a template"
        && find("#agent-template-find").getAttribute("type") === "search"
        && pickerBox.querySelector("i").getAttribute("data-lucide") === "search"
        && pickerHead.querySelectorAll(".field-label").length === 0
        && pickerHead.querySelectorAll("label")
            .every((label) => label.getAttribute("for") === null);

    // ─── 23. "Add agent from this": the Marketplace hands over a template ───
    //
    // From the Marketplace tab, the bridge switches to Add agent and starts
    // the form from that template — the same form a picker cell builds, chip
    // and all — with the keyboard on Name.
    await clickTab("marketplace");
    marketDeps.onUseTemplate(TEMPLATES[0]);
    await drain();
    verdict.useTemplateOpensTheForm = selectedTab() === "add"
        && panelOf("add").hidden === false
        && panelOf("marketplace").hidden === true
        && Boolean(find("#agent-form"))
        && chipText() === "Code Auditor · JTech Minds · pinned aa11bb2"
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        && find("#agent-add-back").hidden === false
        && documentStub.activeElement === find('input[name="name"]');
    // A form has landed, so there is a draft an outside click could lose.
    await documentStub.body.querySelector(".modal-backdrop").dispatchClick();
    await drain();
    const refusedWithADraft = dialogs().length === 1;
    await close();
    // ...and before a form lands there is nothing to lose, so it closes.
    await open();
    await documentStub.body.querySelector(".modal-backdrop").dispatchClick();
    await drain();
    verdict.draftBlocksBackdropClose = refusedWithADraft && dialogs().length === 0;

    // ─── 24. A build that lands while the Marketplace is up (spec §3.1) ───
    //
    // The row is empty while the other tab is up, so the build's `ready`
    // found no button — and a primary that recorded only what it PAINTED
    // remembered `building`, and the row that came back was `Loading…` and
    // disabled for good.
    await open();
    let releaseAway;
    nextBuildHold = new Promise((resolve) => { releaseAway = resolve; });
    await cards()[0].dispatchClick();
    await drain();
    const buildingBeforeLeaving = documentStub.querySelector("#agent-form-submit").textContent
        === "Loading…";
    await clickTab("marketplace");
    const emptiedWhileAway = footerNames().join("|") === ""
        && find("#agent-add-back").hidden === true;
    tabOf("marketplace").focus();
    releaseAway();
    await drain();
    // Landed while away: nothing in the Marketplace's footer, and the
    // keyboard where the operator left it rather than on a hidden Name field.
    const stillEmpty = footerNames().join("|") === ""
        && documentStub.activeElement === tabOf("marketplace");
    await clickTab("add");
    const live = documentStub.querySelector("#agent-form-submit");
    verdict.buildLandingWhileAwayLeavesPrimaryLive = buildingBeforeLeaving
        && emptiedWhileAway && stillEmpty
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        && live.disabled === false
        && live.textContent === "Create Agent"
        && find("#agent-add-back").hidden === false
        && chipText().startsWith("Code Auditor");
    await close();

    // ─── 25. A build that FAILS while the Marketplace is up (spec §3.2) ───
    //
    // The recovery row is owed, not written: `Cancel` must not appear in the
    // Marketplace tab's footer, and the keyboard must not be taken from it.
    // Coming back puts the owed row up without moving the keyboard either.
    await open();
    let releaseDoomedAway;
    nextBuildHold = new Promise((resolve) => { releaseDoomedAway = resolve; });
    await cards()[0].dispatchClick();
    await drain();
    await clickTab("marketplace");
    tabOf("marketplace").focus();
    formMode = "fail";
    releaseDoomedAway();
    await drain();
    formMode = "ready";
    const marketplaceLeftAlone = footerNames().join("|") === ""
        && documentStub.activeElement === tabOf("marketplace");
    tabOf("add").focus();
    await clickTab("add");
    verdict.failureWhileAwayLeavesMarketplaceAlone = marketplaceLeftAlone
        && footerNames().join("|") === "Cancel"
        && dialog().textContent.includes("The agent editor failed to load.")
        && documentStub.querySelector("#agent-form-submit") === null
        // resume() put the row back without taking the keyboard off the tab.
        && documentStub.activeElement === tabOf("add");

    // ─── 26. "Add agent from this" onto that failed form step (spec §3.3) ───
    //
    // The pane is already on the form step, whose row is `Cancel` alone, so
    // the step swap is a no-op — and a build that started there painted a
    // primary that was not in the row. Every build now starts with the row it
    // owes rebuilt.
    await clickTab("marketplace");
    marketDeps.onUseTemplate(TEMPLATES[1]);
    await drain();
    const restored = documentStub.querySelector("#agent-form-submit");
    verdict.useTemplateAfterAFailedBuildRestoresTheFormRow = selectedTab() === "add"
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        && Boolean(restored) && restored.disabled === false
        && restored.textContent === "Create Agent"
        && chipText().startsWith("Feature Planner")
        && !dialog().textContent.includes("The agent editor failed to load.");
    await close();

    // ─── 27. One agent form at a time, across both dialogs ───
    //
    // `#agent-form` is one id for the whole document and the primary submits
    // it by that id, so the Agents dialog and the Edit role dialog share one
    // slot: whichever is open is handed back, and nothing second is built.
    const agentsHandle = await open();
    const editOverAgents = global.BossModAgentEdit.openAgentModal({
        store, agent: { id: "a9", name: "Ada" },
    });
    await drain();
    const agentsBlocksEdit = editOverAgents === agentsHandle && dialogs().length === 1
        && dialog().getAttribute("aria-label") === "Agents";
    // A second open of the Agents dialog is the same dialog, switched.
    const reopened = global.BossModAgentsDialog.open({ store, tab: "marketplace" });
    await drain();
    const reopenSwitches = reopened === agentsHandle && dialogs().length === 1
        && selectedTab() === "marketplace";
    await closeByX();
    const editHandle = global.BossModAgentEdit.openAgentModal({
        store, agent: { id: "a9", name: "Ada" },
    });
    await drain();
    const agentsOverEdit = global.BossModAgentsDialog.open({ store, tab: "add" });
    await drain();
    verdict.oneAgentFormAtATime = agentsBlocksEdit && reopenSwitches
        && agentsOverEdit === editHandle
        && dialogs().length === 1
        && dialog().getAttribute("aria-label") === "Edit role"
        && dialog().querySelectorAll(".tabs").length === 0;
    await close();
    verdict.theSlotIsFreeOnceBothHaveClosed = dialogs().length === 0
        && global.BossModAgentDialogSlot.current() === null;

    // ─── 28. The Edit role dialog is edit-only ───
    //
    // One step, the panel size, no tabs, no picker — and no create path at
    // all: a call without the agent it edits is a caller that wanted the
    // Agents dialog, and it is told so instead of being handed a create form
    // with no picker in front of it.
    let refusal = "";
    try {
        global.BossModAgentEdit.openAgentModal({ store });
    } catch (err) {
        refusal = String((err && err.message) || err);
    }
    verdict.theEditDialogRefusesToCreate = refusal.includes("deps.agent is required")
        && dialogs().length === 0;
    global.BossModAgentEdit.openAgentModal({ store, agent: { id: "a9", name: "Ada" } });
    await drain();
    verdict.theEditDialogIsOneStep = dialog().getAttribute("data-size") === "panel"
        && dialog().getAttribute("aria-label") === "Edit role"
        && footerNames().join("|") === "Save as template|Cancel|Save Changes"
        && find("#agent-pick-blank") === null
        && find("#agent-add-back") === null
        && Boolean(find("#agent-form"));
    await close();

    // ─── 29. Both doors refuse what they cannot open ───
    const refuses = (build, fragment) => {
        try {
            build();
            return false;
        } catch (err) {
            return String((err && err.message) || err).includes(fragment);
        }
    };
    verdict.theAgentsDialogRefusesAnUnknownTab =
        refuses(() => global.BossModAgentsDialog.open({ store, tab: "hire" }), "unknown tab")
        && refuses(() => global.BossModAgentsDialog.open({ store }), "unknown tab")
        && refuses(() => global.BossModAgentsDialog.open({ tab: "add" }), "deps.store")
        && dialogs().length === 0;

    // ─── 30. Recent: the scope, its cards, and what a pick builds from ───
    //
    // A snapshot is the setup of an agent made here — deleted or not — and the
    // form it fills CREATES. The pane hands it to the build as `prefill`,
    // never as `agent`: the second is identity, and identity is what would
    // turn the save into a PATCH of the agent the operator is recreating.
    creates.length = 0;
    updates.length = 0;
    await open();
    await railRow("Recent").dispatchClick();
    await drain();
    const first = recentCards()[0];
    verdict.recentListsTheSnapshots = recentCards().length === 2
        && cards().length === 2
        // Newest first, as the server ordered them.
        && first.querySelector(".market-card-title").textContent === "Ada"
        // Blank is still the first cell of the grid, in this scope too.
        && Boolean(find("#agent-pick-blank"));
    // The card's anatomy: the agent's OWN avatar rather than a category
    // bubble, no category chip, its role, its description, and the one date
    // that says whether it is still on the roster.
    verdict.aRecentCardIsTheAgentNotAPack =
        first.querySelector(".market-card-category") === null
        && first.querySelector(".market-card-specialty").textContent === "Code Auditor"
        && first.querySelector(".market-card-intro").textContent
            .startsWith("Reads a diff")
        && first.querySelector(".avatar").textContent === "A"
        && first.querySelector(".market-card-state").textContent.startsWith("Deleted ")
        && recentCards()[1].querySelector(".market-card-state").textContent
            .startsWith("Saved ");
    // The filter reads a snapshot's own three fields.
    await type(find("#agent-template-find"), "release notes");
    const filteredRecent = recentCards()
        .map((node) => node.querySelector(".market-card-title").textContent).join("|");
    await type(find("#agent-template-find"), "zzz");
    verdict.theFilterSearchesRecentTooAndSaysSoWhenItMisses = filteredRecent === "Bo"
        && dialog().textContent.includes("No recent agent matches “zzz”.");
    await type(find("#agent-template-find"), "");

    await recentCards()[0].dispatchClick();
    await drain();
    verdict.aRecentPickBuildsFromValuesNotIdentity = builtWith.agent === null
        && builtWith.prefill === SNAPSHOTS[0]
        && footerNames().join("|") === "Save as template|Cancel|Create Agent"
        // No Delete and no recovery tools: they are the edit form's, and this
        // one creates. (The stub form carries neither either way; what this
        // pins is the argument that decides it.)
        && find("#btn-delete-agent") === null;
    // The chip says whose setup this is, and when they went.
    verdict.theRecentChipNamesTheAgentAndWhenItWent =
        chipText().startsWith("Recreating Ada")
        && chipText().includes("deleted ")
        && Boolean(find("#snapshot-provenance-blank"));
    // ...and it SAVES as a create. Never a PATCH of the agent it came from.
    await type(find('input[name="name"]'), "Ada II");
    await answerAi();
    await documentStub.querySelector("#agent-form-submit").dispatchClick();
    await drain();
    verdict.recreatingPostsANewAgent = creates.length === 1
        && creates[0].name === "Ada II"
        && updates.length === 0;

    // Start blank throws the recreate away for an empty form — it is a PICK,
    // not a field-by-field undo.
    await find("#snapshot-provenance-blank").dispatchClick();
    await drain();
    verdict.startBlankLeavesTheRecreate = builtWith.prefill === null
        && builtWith.agent === null
        && chipText() === ""
        && Boolean(find("#agent-form"))
        && footerNames().join("|") === "Save as template|Cancel|Create Agent";
    await close();

    // ...and with no snapshots there is no scope row to reach: a rail row that
    // filters to nothing is furniture.
    snapshotMode = "empty";
    await open();
    verdict.noRecentAgentsNoRecentRow = railRow("Recent") === null
        && railRow("All") !== null;
    await closeByX();
    snapshotMode = "ready";

    // ─── 31. Save as template: the form's role contract, into the library ───
    //
    // It opens a LAYER over the form — the dialog beneath must still be there
    // when it closes — and it reads the form at the moment it is pressed.
    localSaves.length = 0;
    await open();
    await cards()[0].dispatchClick();
    await drain();
    await type(find('input[name="role"]'), "Release Notes Writer");
    await type(find('textarea[name="description"]'), "Turns merged PRs into notes.");
    await saveTemplateAction().dispatchClick();
    await drain();
    const saveLayer = layer();
    verdict.saveAsTemplateOpensALayerAndKeepsTheForm = dialogs().length === 2
        && saveLayer.getAttribute("aria-label") === "Save as template"
        && Boolean(saveLayer.querySelector("#agent-save-template-title"))
        // The form is still underneath, hidden rather than gone.
        && Boolean(dialogs()[0].querySelector("#agent-form"))
        // Defaulted to the specialty on the form, not to the template's title.
        && saveLayer.querySelector("#agent-save-template-title").value
            === "Release Notes Writer"
        // The category is the app's dropdown, never a native <select>.
        && Boolean(saveLayer.querySelector(".menu-select"))
        && saveLayer.querySelectorAll("select").length === 0;

    // A taken title is a question, not an overwrite: Replace re-posts.
    localSaveMode = "taken";
    await layerButton("Save").dispatchClick();
    await drain();
    const asked = Boolean(layer().querySelector("#agent-save-template-replace"))
        && layer().textContent.includes("already exists")
        && localSaves.length === 1
        && localSaves[0].replace === false;
    await layer().querySelector("#agent-save-template-replace").dispatchClick();
    await drain();
    verdict.aTakenTitleAsksBeforeItReplaces = asked
        && localSaves.length === 2
        && localSaves[1].replace === true;
    // What was sent is what the form was carrying, and nothing the operator
    // owns: no name, no colour, no connection.
    const sent = localSaves[1];
    verdict.theSavedTemplateIsTheFormsContract = sent.title === "Release Notes Writer"
        && sent.specialty === "Release Notes Writer"
        && sent.description === "Turns merged PRs into notes."
        && sent.category === "engineering"
        && typeof sent.communication === "object"
        && !("name" in sent) && !("color" in sent) && !("model_work" in sent);
    // It landed: the layer says where it went and offers one way out, and the
    // dialog's other views are told to re-read.
    verdict.aSavedTemplateSaysWhereItWentAndRefreshesBoth =
        layer().textContent.includes("Saved “Release Notes Writer” to your templates")
        && layer().textContent.includes("tagged Local")
        && layerButton("Done") !== null
        && documentStub.activeElement === layerButton("Done")
        && marketRefreshes === 1;
    await layerButton("Done").dispatchClick();
    await drain();
    verdict.doneClosesOnlyTheLayer = dialogs().length === 1
        && Boolean(find("#agent-form"));
    localSaveMode = "ready";

    // A contract with nothing in it is refused before any layer opens, on the
    // form, with the keyboard on the field that is missing.
    await type(find('textarea[name="description"]'), "");
    await saveTemplateAction().dispatchClick();
    await drain();
    verdict.anEmptyContractIsRefusedOnTheForm = dialogs().length === 1
        && Boolean(find("#agent-save-template-refused"))
        && find("#agent-save-template-refused").textContent.includes("a description")
        && documentStub.activeElement === find('textarea[name="description"]');
    await close();

    // ...and while a build is still running there is no form to read, so the
    // action is withheld exactly as the primary is.
    let releaseSaveTemplate;
    await open();
    nextBuildHold = new Promise((resolve) => { releaseSaveTemplate = resolve; });
    await cards()[0].dispatchClick();
    await drain();
    const withheldWhileBuilding = saveTemplateAction().disabled === true
        && documentStub.querySelector("#agent-form-submit").disabled === true;
    releaseSaveTemplate();
    await drain();
    verdict.saveAsTemplateIsWithheldWhileAFormIsBuilding = withheldWhileBuilding
        && saveTemplateAction().disabled === false;
    await close();

    // The Edit role dialog offers it too: a contract worth keeping is as
    // likely to be one already in front of the operator.
    localSaves.length = 0;
    global.BossModAgentEdit.openAgentModal({ store, agent: { id: "a9", name: "Ada" } });
    await drain();
    await type(find('input[name="role"]'), "Code Auditor");
    await type(find('textarea[name="description"]'), "Reads a diff.");
    await saveTemplateAction().dispatchClick();
    await drain();
    await layerButton("Save").dispatchClick();
    await drain();
    verdict.theEditDialogSavesTemplatesToo = localSaves.length === 1
        && localSaves[0].specialty === "Code Auditor"
        // Nothing to re-read on this side: the library views are the Agents
        // dialog's, and one agent form is open at a time.
        && marketRefreshes === 1;
    await layerButton("Done").dispatchClick();
    await drain();
    await close();
    verdict.everythingClosed = dialogs().length === 0;

    process.stdout.write(JSON.stringify(Object.assign({ ok: true }, verdict)));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
