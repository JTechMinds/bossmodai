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
    "BossModAgentFormHydrate", "BossModAgentRecovery", "BossModAgentFormSave",
    "BossModAgentTemplatePicker", "BossModAgentQuickConnection",
    "BossModAgentFormQuick", "BossModAgentDialogFooter",
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

function template(overrides) {
    return Object.assign({
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
        return h("div", {},
            h("label", { class: "block" }, "AI Connections"),
            h("p", { class: "text-xs" }, "No connections configured. ",
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
    return h("div", {},
        h("label", { class: "block" }, "AI Connections"),
        h("div", {},
            h("label", { for: "agent-connection-model_all" }, "Set All"),
            allSelect),
        h("hr", {}),
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
    const advanced = h("div", {},
        h("button", { type: "button", id: "advanced-toggle" }, "Advanced"),
        h("div", { id: "advanced-content", class: "hidden" }, done, personality));

    return h("form", { id: "agent-form" },
        h("div", {}, h("label", {}, "Name"), name),
        card, connections, advanced);
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
        return jsonResponse(CONNECTIONS);
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

/** The fake has no <details> behaviour, so opening one is spelled out. */
async function expand(details) {
    details.setAttribute("open", "");
    for (const fn of [...(details.listeners.toggle || [])]) await fn({ target: details });
}

/** ...and closing it again, which is half of "I opened it to read it". */
async function collapse(details) {
    details.removeAttribute("open");
    for (const fn of [...(details.listeners.toggle || [])]) await fn({ target: details });
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
    const node = find(".quick-provenance-text");
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
    // The primary belongs to a form, and step one has none.
    verdict.noCreateOnStepOne = footerNames().join("|") === "Browse marketplace|Cancel"
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

    // ─── 4. Category grouping, and a filter that drops what it empties ───
    verdict.categories = dialog().querySelectorAll(".picker-category-title")
        .map((node) => node.textContent).join("|") === "Engineering|Product Design";
    await type(find("#agent-template-find"), "diff");
    verdict.filterHidesEmptyCategory = cards().length === 1
        && cards()[0].getAttribute("data-template-id") === "t1"
        && dialog().querySelectorAll(".picker-category-title").length === 1;
    await type(find("#agent-template-find"), "zzz");
    verdict.noMatchCopy = dialog().textContent.includes("No template matches “zzz”.")
        && cards().length === 0;
    await type(find("#agent-template-find"), "");

    // ─── 5. Picking a template: two visible fields, everything else behind one ───
    await cards()[0].dispatchClick();
    await drain();
    verdict.stepTwoFooter = footerNames().join("|") === "Back|Cancel|Create Agent";
    verdict.provenanceChip = find(".quick-provenance-text").textContent
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

    // The connection question is beside Name, and answering it is required
    // while the matrix it writes to is out of sight.
    const all = field('select[name="model_all"]');
    verdict.connectionLifted = form.children[2].className === "quick-ai"
        && form.children[2].querySelector('select[name="model_all"]') === all
        && form.querySelector(".quick-ai-label").textContent === "AI"
        && all.hasAttribute("required")
        && form.querySelector("hr") === null;
    const disclosure = form.querySelector(".quick-disclosure");
    verdict.reviewHidesWhatTheTemplateAnswered = Boolean(disclosure)
        && disclosure.querySelector("#role-contract-card") !== null
        && disclosure.querySelector("#advanced-content") !== null
        && disclosure.querySelector(".connection-grid") !== null;
    // The tool hints the pack declares are part of the decision being made
    // here, so the line above the disclosure carries them.
    verdict.summaryNamesTheTools = form.querySelector(".quick-summary").textContent
        === "Reviews claims · Reads a diff and reports what is not true · Tools: work · Blue";
    verdict.focusLandsOnName = documentStub.activeElement === field('input[name="name"]');

    // ─── 6. The connection guard tracks the ANSWER, not the disclosure ───
    //
    // Opening this panel used to drop `required` from the lifted select, one
    // way and for good — and the panel is where the template's specialty,
    // description and what-done live, so opening it to READ them disarmed the
    // guard and Create wrote five null connections over "Saved successfully".
    // What relaxes it now is an answer in the matrix, and only that.
    await expand(disclosure);
    await collapse(disclosure);
    verdict.expandingAloneKeepsTheGuard = all.hasAttribute("required");
    // Any ONE of the five is an answer: the operator setting the matrix by
    // hand must not be blocked by the convenience select above it.
    const perType = global.BossModAgentFields.MODEL_TYPES
        .map((type) => form.querySelector(`select[name="${type.key}"]`));
    await choose(perType[1], "c1");
    verdict.answeringOneTypeReleasesTheGuard = !all.hasAttribute("required");
    // ...and clearing them all back to None re-arms it. A live check in both
    // directions, which is what a one-way flip could never be.
    await choose(perType[1], "");
    verdict.clearingThemAllRearmsTheGuard = all.hasAttribute("required")
        && perType.every((sel) => sel.value === "");

    // ─── 7. Back keeps the draft, and returns focus to the Find box ───
    await type(field('input[name="name"]'), "Mine");
    await footer().querySelectorAll("button")[0].dispatchClick();
    await drain();
    const backedOut = dialogs().length === 1
        && footerNames().join("|") === "Browse marketplace|Cancel"
        && documentStub.activeElement === find("#agent-template-find");
    await cards()[0].dispatchClick();
    await drain();
    verdict.backKeepsTheDraft = backedOut
        && find("#agent-form").querySelector('input[name="name"]').value === "Mine"
        && dialog().querySelectorAll(".quick-provenance").length === 1;

    // ─── 8. Dismissing the chip drops to blank without touching the draft ───
    await find("#quick-provenance-clear").dispatchClick();
    await drain();
    const after = find("#agent-form");
    verdict.chipClearsTemplateOnly = after.querySelector('input[name="role"]').value === ""
        && after.querySelector('textarea[name="description"]').value === ""
        && after.querySelector('input[name="done_fail_bar"]').value === ""
        && after.querySelector('input[name="name"]').value === "Mine"
        && after.querySelector('input[name="name"]').getAttribute("placeholder") === "e.g. PM Agent"
        && after.querySelector(".quick-provenance") === null
        && after.querySelector(".quick-summary") === null
        && after.querySelector(".quick-disclosure").hasAttribute("open")
        && !after.querySelector('select[name="model_all"]').hasAttribute("required");
    // Every field the template wrote, including the one applyHireFields only
    // ever set: a personality left selected under a cleared form is a template
    // that Remove template did not remove.
    verdict.chipClearsThePersonalityToo =
        after.querySelector('select[name="personality_id"]').value === "";

    // ─── 9. Picking Blank builds the plain form, and only then ───
    await footer().querySelectorAll("button")[0].dispatchClick();
    await drain();
    await find("#agent-pick-blank").dispatchClick();
    await drain();
    const blank = find("#agent-form");
    verdict.blankIsThePlainForm = blank.querySelector(".quick-provenance") === null
        && blank.querySelector(".quick-disclosure") === null
        && blank.querySelector("#role-contract-card") !== null
        && blank.querySelector('select[name="model_all"]').hasAttribute("required") === false
        && footerNames().join("|") === "Back|Cancel|Create Agent";
    await close();
    verdict.closes = dialogs().length === 0;

    // ─── 10. No AI connection configured: said in front, and refused ───
    connectionMode = "none";
    await open();
    await cards()[0].dispatchClick();
    await drain();
    const bare = find("#agent-form");
    const aiField = bare.querySelector(".quick-ai");
    const standIn = bare.querySelector('select[name="model_all"]');
    const options = standIn ? standIn.querySelectorAll("option") : [];
    verdict.noConnectionsIsToldInFront = Boolean(aiField) && Boolean(standIn)
        // The matrix's link to Settings comes UP, not behind the disclosure.
        && aiField.querySelector("#btn-goto-connections") !== null
        && bare.querySelector(".quick-disclosure")
            .querySelector("#btn-goto-connections") === null
        // And what it explains is joined to the control it explains.
        && standIn.getAttribute("aria-describedby") === "quick-ai-none"
        && aiField.querySelector("#quick-ai-none") !== null;
    // Native constraint validation, the same mechanism the populated case
    // uses: a required select with nothing to select refuses the submit.
    verdict.noConnectionsBlocksCreate = Boolean(standIn)
        && standIn.hasAttribute("required")
        && !standIn.hasAttribute("disabled")
        && options.length === 1
        && options[0].getAttribute("value") === "";
    // Expanding is not an answer anywhere, and here there is nothing that
    // could answer: no five selects, so the block stays through both the
    // disclosure and the chip's dismissal.
    await expand(bare.querySelector(".quick-disclosure"));
    await find("#quick-provenance-clear").dispatchClick();
    await drain();
    const stillBlocked = find('select[name="model_all"]');
    verdict.noConnectionsStaysBlockedAfterTheChipGoes = Boolean(stillBlocked)
        && stillBlocked.hasAttribute("required");
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
        && footerNames().join("|") === "Back|Cancel"
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
        && footerNames().join("|") === "Back|Cancel|Create Agent";
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
    await footer().querySelectorAll("button")[0].dispatchClick();
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
    await footer().querySelectorAll("button")[0].dispatchClick();
    await drain();
    await cards()[1].dispatchClick();
    await drain();
    const showedTheSecond = chipText().startsWith("Feature Planner");
    releaseFirst();
    await drain();
    verdict.aSupersededBuildNeverLands = showedTheSecond
        && chipText().startsWith("Feature Planner")
        && dialog().querySelectorAll(".quick-provenance").length === 1
        && find('input[name="role"]').value === "Plans features";
    // What is recorded as built is what is on screen, so re-picking that cell
    // is the no-op it claims to be...
    await footer().querySelectorAll("button")[0].dispatchClick();
    await drain();
    await cards()[1].dispatchClick();
    await drain();
    verdict.theRecordedBuildIsTheOneOnScreen = chipText().startsWith("Feature Planner")
        && find('input[name="role"]').value === "Plans features";
    // ...and the pick whose build lost rebuilds rather than reading as built.
    await footer().querySelectorAll("button")[0].dispatchClick();
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
    await footer().querySelectorAll("button")[0].dispatchClick();
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
    await footer().querySelectorAll("button")[0].dispatchClick();
    await drain();
    formMode = "fail";
    releaseDoomed();
    await drain();
    formMode = "ready";
    verdict.aBuildFailingAfterBackLeavesThePickerAlone =
        footerNames().join("|") === "Browse marketplace|Cancel"
        && !dialog().textContent.includes("The agent editor failed to load.")
        && cards().length === 2
        && documentStub.activeElement === find("#agent-template-find");
    // ...and nothing stayed recorded as built, so that cell is a retry.
    await cards()[0].dispatchClick();
    await drain();
    verdict.pickingAgainAfterABuriedFailureRetries = Boolean(find("#agent-form"))
        && footerNames().join("|") === "Back|Cancel|Create Agent";
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
    await footer().querySelectorAll("button")[0].dispatchClick();
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
