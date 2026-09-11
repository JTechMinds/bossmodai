/**
 * Node harness: Add agent — the picker's four states, the two steps, and the
 * quick layout a template gets.
 *
 * Invoked by tests/test_add_agent_modal.py. Not a browser bundle.
 *
 * The roster row's two-door menu is in here too: it is the other half of "add
 * an agent", it hangs off core/overlays.js's menu the way the dialog hangs off
 * the modal, and both stubs it needs — BossModMarketplace and the store — are
 * already standing for the dialog's own Browse door.
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
    "BossModOverlayFocus", "BossModOverlays", "BossModGates",
    "BossModAgentApi", "BossModAgentTemplatesApi", "BossModAgentFields",
    // The two the fake form leans on rather than reimplementing: the shape
    // vocabulary the refusal reads back, and the connection guard itself.
    "BossModAgentFormConnections", "BossModAgentFormBindings",
    "BossModAgentFormHydrate", "BossModAgentRecovery", "BossModAgentFormSave",
    // The picker draws the local library with the marketplace's own card and
    // rail builders, so its dependencies load ahead of it.
    "BossModAvatar", "BossModMarketplaceItems", "BossModPackCard", "BossModFilterRail",
    "BossModAgentTemplatePicker",
    "BossModAgentFormTemplate", "BossModAgentDialogFooter",
    "BossModAgentEdit", "BossModAddAgentMenu",
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
let connectionMode = "ready";
// How GET /api/connections answers the read the SUBMIT path resolves against:
// "reject" is the offline shape, "error" the 500 whose body is an object and
// not a list. Both used to end as `connections = []`, which is the same thing
// the form says when the operator has none configured.
let connectionsApiMode = "ready";
let formMode = "ready";
let marketOpens = 0;
let marketOnClosed = null;
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
global.BossModAgentForm = {
    buildFormHTML: async (container) => {
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
    open(options) {
        marketOpens += 1;
        marketOnClosed = (options && options.onClosed) || null;
        return { close() {} };
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

async function open() {
    global.BossModAgentEdit.openAgentModal({ store });
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
    // The primary belongs to a form, and step one has none. The footer is
    // dismissal alone now — `Browse marketplace` is a control in the picker's
    // the LEAD of the row, pushed to its left edge by a rule of its own, with
    // the dismissal at the other end — side by side they read as two ways to
    // leave, and beside the filter box they read as part of the filter.
    // BACK IS ON THE TITLE ROW — `‹ Add agent` — and it is not drawn on step
    // one, which has nowhere to go back to. It spent a round as a bordered
    // square floating in the band under the title, aligned to nothing, and a
    // round before that as a footer action beside Cancel.
    verdict.backLeadsTheTitleRowAndNotStepOne =
        Boolean(dialog().querySelector(".modal-head"))
        && Boolean(find("#agent-add-back"))
        && find("#agent-add-back").hidden === true
        // In the head, never in the body or the row: the body is emptied by a
        // failed build and the row is rebuilt on every step swap.
        && Boolean(dialog().querySelector(".modal-head").querySelector("#agent-add-back"))
        && footer().querySelector("#agent-add-back") === null;
    verdict.noCreateOnStepOne = footerNames().join("|") === "Browse marketplace|Cancel"
        && Boolean(find("#agent-add-browse"))
        && documentStub.querySelector("#agent-form-submit") === null
        && find("#agent-form") === null;

    // ─── 2. Browse closes this dialog, and its return reopens it, refreshed ───
    await find("#agent-template-browse").dispatchClick();
    await drain();
    const closedForMarketplace = dialogs().length === 0 && marketOpens === 1;
    libraryMode = "ready";
    marketOnClosed();
    await drain();
    verdict.browseClosesAndReopens = closedForMarketplace
        && dialogs().length === 1
        && footerNames().join("|") === "Browse marketplace|Cancel"
        && Boolean(find("#agent-add-browse"))
        && cards().length === 2;
    await close();

    // ─── 3. A library that could not be READ says so, and retries ───
    libraryMode = "fail";
    await open();
    const failedCopy = dialog().textContent.includes("Couldn’t read your template library.")
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
    verdict.categories = railLabels() === "All|Engineering|Product Design"
        && dialog().querySelectorAll(".market-rail-title")
            .map((node) => node.textContent).join("|") === "Show|Categories"
        // Counted, so the rail says how much is behind each row.
        && dialog().querySelectorAll(".market-rail-count")
            .map((node) => node.textContent).join("|") === "2|1|1";

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
        && railLabels() === "All|Engineering|Product Design";
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
    verdict.stepTwoFooter = footerNames().join("|") === "Cancel|Create Agent"
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
        && footerNames().join("|") === "Browse marketplace|Cancel"
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
        && footerNames().join("|") === "Cancel|Create Agent"
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
        && footerNames().join("|") === "Cancel|Create Agent"
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
        footerNames().join("|") === "Browse marketplace|Cancel"
        // The door out of an empty library is still there — it leads step
        // one's row, and a failure that rewrote step one would take it with
        // the rest of the picker.
        && Boolean(find("#agent-add-browse"))
        && !dialog().textContent.includes("The agent editor failed to load.")
        && cards().length === 2
        && documentStub.activeElement === find("#agent-template-find");
    // ...and nothing stayed recorded as built, so that cell is a retry.
    await cards()[0].dispatchClick();
    await drain();
    verdict.pickingAgainAfterABuriedFailureRetries = Boolean(find("#agent-form"))
        && footerNames().join("|") === "Cancel|Create Agent"
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
    // And the door still opens what it names, with the panel away first.
    const openedBefore = marketOpens;
    await doors[0].dispatchClick();
    await drain();
    verdict.theMarketplaceDoorStillOpensTheTakeover = marketOpens === openedBefore + 1
        && railHost.querySelectorAll(".add-agent-choice").length === 0
        && hireRow.getAttribute("aria-expanded") === "false";

    process.stdout.write(JSON.stringify(Object.assign({ ok: true }, verdict)));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
