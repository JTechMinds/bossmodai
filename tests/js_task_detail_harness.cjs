/**
 * Node harness: the task detail, built from the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * Opens real tasks in the real modal and reads what it shows: a status line
 * measured the way the watchdog measures, the facts as pairs, the callout for
 * each kind of state, the instruction clamp, the subtask checklist, the
 * pencil that enters Edit mode (open tasks only), the role contract, and the
 * activity as sentences. Then it drives the real operator actions: Edit mode
 * edits the same panel in place — ✓ sends only what changed, a refused
 * reassign is resent with the confirmation, a save repaints the layer it came
 * from and re-reads Activity, ✕, Esc and an outside click never lose a draft
 * by accident, and one Deliverables section holds the task's own rows and its
 * subtasks'. The status row's Resume, Mark complete… and Cancel task… appear
 * only in Edit mode, wait for an unsaved draft, and repaint the detail in
 * place; and the completer will not post without a summary.
 *
 * BossModMarkdown is stubbed to hand the source back as one text node and to
 * record what it was asked to render. The real renderer is covered by its own
 * harness (js_markdown_harness.cjs), and this fake DOM has no DOMParser of its
 * own; what matters here is that the instructions go THROUGH the renderer.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

installDom();
installIconsStub();
global.lucide = null;

const renders = [];
global.BossModMarkdown = {
    render: (text) => {
        renders.push(text);
        return [document.createTextNode(text)];
    },
};

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModFormat", "BossModSpecialty", "BossModGates",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuSelect",
    "BossModAutoGrow",
    "BossModFileListing",
    "BossModFactList",
    // The instructions' clamp is the shared component the desk's description
    // uses too; it renders through the stubbed BossModMarkdown above.
    "BossModClampedMarkdown",
    "BossModTasksColumns", "BossModTasksData", "BossModTaskDeliverables",
    "BossModTaskEvents", "BossModTaskDetailSections",
    "BossModAssignOutcomes", "BossModAssignForm",
    "BossModTaskFilePicker", "BossModTaskEditFiles", "BossModTaskEditMode", "BossModTaskDetail",
    "BossModTasksCancel", "BossModTasksComplete", "BossModTaskActions",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const NOW = Date.now();
const ago = (ms) => new Date(NOW - ms).toISOString();
const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;

const BLOCKED = {
    id: "t-blocked",
    title: "Fix the login bug",
    status: "blocked",
    status_note: "no progress, @Debra",
    description: "Make the login work again.\nThen tell Debra.",
    assigned_to: "a1",
    assigned_to_name: "Jim",
    assigned_to_role: "Implementation engineer",
    requester_id: "__human__",
    owner_id: "a1",
    parent_task_id: null,
    // 2h 5m and half a minute: formatDuration floors to whole minutes.
    last_progress_at: ago(2 * HOUR + 5 * MINUTE + 30 * 1000),
    last_activity: ago(MINUTE),
    created_at: ago(24 * HOUR),
    closed_at: null,
    work_contract: { deliverables: [{ type: "file", path: "/me/out/report.md", description: "The report" }] },
    operator_can_complete: true,
    operator_can_resume: true,
};
const CHILD_DONE = {
    id: "c-done", title: "Write the regression test", status: "complete",
    parent_task_id: "t-blocked", assigned_to: "a1", assigned_to_name: "Jim",
    last_activity: ago(2 * HOUR), created_at: ago(20 * HOUR), closed_at: ago(2 * HOUR),
};
const CHILD_OPEN = {
    id: "c-open", title: "Patch the session check", status: "active",
    parent_task_id: "t-blocked", assigned_to: "a2", assigned_to_name: "Debra",
    last_activity: ago(HOUR), created_at: ago(20 * HOUR), closed_at: null,
    work_contract: { deliverables: [{ type: "file", path: "/me/patch.diff" }] },
};
const DONE = {
    id: "t-done", title: "Ship the dashboard", status: "complete",
    completion_summary: "Shipped behind the flag.",
    assigned_to: "a1", assigned_to_name: "Jim", requester_id: "a2", requester_name: "Debra",
    parent_task_id: null,
    last_activity: ago(3 * HOUR), created_at: ago(48 * HOUR), closed_at: ago(3 * HOUR),
};
// Not yet accepted: the state machine has no pending → complete, so the
// server says it cannot be marked complete.
const PENDING = {
    id: "t-pending", title: "Draft the rollout note", status: "pending",
    assigned_to: "a2", assigned_to_name: "Debra", requester_id: "__human__", parent_task_id: null,
    last_activity: ago(HOUR), created_at: ago(HOUR), closed_at: null,
    operator_can_complete: false,
    operator_can_resume: false,
};
const TASKS = [BLOCKED, CHILD_DONE, CHILD_OPEN, DONE, PENDING];

const EVENTS = [
    { id: "e1", event_type: "status_update", author_name: "Jim", author_agent_id: "a1",
        content: "Status active → blocked: no progress, @Debra", created_at: ago(2 * HOUR) },
    { id: "e2", event_type: "status_update", author_name: "Jim", author_agent_id: "a1",
        content: "Status pending → accepted.", created_at: ago(3 * HOUR) },
    { id: "e3", event_type: "system", author_name: "BossMod", author_agent_id: null,
        content: "Reused the existing open task chosen by the operator.", created_at: ago(4 * HOUR) },
    { id: "e4", event_type: "status_update", author_name: "Debra", author_agent_id: "a2",
        content: "Moved it along by hand", created_at: ago(5 * HOUR) },
];

/** How many times any detail has read its Activity. */
let eventReads = 0;

/** The one folder Edit mode's file picker lists here: where it opens first. */
const PROJECTS = {
    kind: "directory", path: "/projects", name: "projects",
    breadcrumbs: [{ label: "/", path: "/" }, { label: "projects", path: "/projects" }],
    entries: [{ name: "plan.md", path: "/projects/plan.md", is_dir: false, size_bytes: 64, updated_at: ago(HOUR) }],
};
/** Every desk listing the file picker asked for: `{agent, path}`. */
const deskReads = [];

function api(url) {
    if (String(url).includes("/events")) {
        eventReads += 1;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(EVENTS) });
    }
    const desk = /^\/api\/agents\/([^/]+)\/desk\?path=(.*)$/.exec(String(url));
    if (desk) {
        const path = decodeURIComponent(desk[2]);
        deskReads.push({ agent: decodeURIComponent(desk[1]), path });
        if (path !== PROJECTS.path) throw new Error(`[task-detail-harness] no listing for ${path}`);
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(PROJECTS) });
    }
    throw new Error(`[task-detail-harness] unexpected request ${url}`);
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

async function click(el, what) {
    if (!el) fail(`nothing to click: ${what}`);
    await el.dispatchClick();
    await drain();
}

/** The one element matching `selector` whose attribute has this value. */
function withAttr(root, selector, attr, value) {
    return root.querySelectorAll(selector).find((el) => el.getAttribute(attr) === value) || null;
}

/** The section whose head's first span reads `label`. */
function sectionHeaded(root, label) {
    const head = root.querySelectorAll(".task-detail-section-head")
        .find((node) => node.children[0] && node.children[0].textContent === label);
    return head ? head.parentNode : null;
}

function open(task, calls) {
    return global.BossModTaskDetail.openTaskDetail({
        api,
        taskId: task.id,
        tasks: TASKS,
        colorOf: (agentId) => (agentId === "a1" ? "#3b82f6" : undefined),
        onNavigate: (id) => calls.navigated.push(id),
        actions: {
            update: () => { throw new Error("[task-detail-harness] this detail saves nothing"); },
            roster: () => [],
            complete: () => { throw new Error("[task-detail-harness] this detail completes nothing"); },
            cancel: () => { throw new Error("[task-detail-harness] this detail cancels nothing"); },
            resume: () => { throw new Error("[task-detail-harness] this detail resumes nothing"); },
        },
        onOpenChat: (t) => calls.chats.push(t),
    });
}

async function main() {
    const SECTIONS = global.BossModTaskDetailSections;
    const calls = { navigated: [], chats: [] };
    const blocked = open(BLOCKED, calls);
    await drain();
    const panel = document.body.querySelector(".modal-panel");
    const detail = panel.querySelector(".task-detail");

    // ── noDuplicateTitle ────────────────────────────────────────────────
    const noDuplicateTitle = !detail.textContent.includes(BLOCKED.title)
        && !detail.querySelector(".task-detail-title")
        && panel.getAttribute("aria-label") === BLOCKED.title;
    if (!noDuplicateTitle) fail("the body repeats the title the head already shows");

    // ── statusLineReadsProgress ─────────────────────────────────────────
    const since = detail.querySelector(".task-detail-since");
    const statusLineReadsProgress = Boolean(since) && since.textContent === "2h 5m without progress"
        && detail.querySelector(".task-detail-status").querySelector(".status-pill")
            .textContent === "Blocked";
    if (!statusLineReadsProgress) fail(`status line read "${since && since.textContent}"`);

    // ── factsArePairs, humanRequesterIsYou ──────────────────────────────
    const facts = withAttr(detail, ".fact-list", "data-pairs", "2");
    const labels = facts ? facts.querySelectorAll(".fact-label").map((dt) => dt.textContent) : [];
    const factsArePairs = labels.slice(0, 4).join(",") === "Assignee,Requester,Created,Updated"
        // Owner is the assignee here, so it is not said twice.
        && !labels.includes("Owner");
    if (!factsArePairs) fail(`facts were [${labels}]`);
    const values = facts.querySelectorAll(".fact-value");
    const humanRequesterIsYou = values[labels.indexOf("Requester")].textContent === "You";
    if (!humanRequesterIsYou) fail(`requester read "${values[labels.indexOf("Requester")].textContent}"`);

    // ── alertCalloutOffersChat ──────────────────────────────────────────
    const alert = withAttr(detail, ".callout", "data-tone", "alert");
    const alertText = alert ? alert.textContent : "";
    const chat = alert && alert.querySelector(".callout-actions").querySelector("button");
    await click(chat, "the callout's Open chat");
    const alertCalloutOffersChat = alertText.includes("Blocked")
        && alertText.includes(BLOCKED.status_note)
        && calls.chats.length === 1 && calls.chats[0] === BLOCKED;
    if (!alertCalloutOffersChat) fail(`alert callout read "${alertText}", chats ${calls.chats.length}`);

    // ── instructionsUseMarkdown ─────────────────────────────────────────
    const body = detail.querySelector(".task-detail-instructions");
    const instructionsUseMarkdown = renders.includes(BLOCKED.description)
        && Boolean(body) && body.classList.contains("md");
    if (!instructionsUseMarkdown) fail("the instructions did not go through BossModMarkdown");

    // ── clampToggleFollowsOverflow ──────────────────────────────────────
    // The clamp is core/clamped-markdown.js's now (the desk's description is
    // its second user): instructions() hands back the section and its
    // measure, and the toggle keeps its label and its behaviour exactly.
    const tall = SECTIONS.instructions(BLOCKED);
    const tallBody = tall.element.querySelector(".task-detail-instructions");
    const tallMore = tall.element.querySelector(".clamped-more");
    const hiddenBeforeMeasure = tallMore.hidden === true && tallBody.classList.contains("is-clamped")
        && tallMore.textContent === "Show full instruction";
    tallBody.scrollHeight = 200;
    tallBody.clientHeight = 100;
    tall.measure();
    const revealed = tallMore.hidden === false && tallBody.classList.contains("is-clamped");
    await click(tallMore, "Show full instruction");
    const expanded = !tallBody.classList.contains("is-clamped")
        && !tall.element.querySelector(".clamped-more");
    const short = SECTIONS.instructions(BLOCKED);
    const shortBody = short.element.querySelector(".task-detail-instructions");
    shortBody.scrollHeight = 100;
    shortBody.clientHeight = 100;
    short.measure();
    const fits = !short.element.querySelector(".clamped-more") && !shortBody.classList.contains("is-clamped");
    const clampToggleFollowsOverflow = hiddenBeforeMeasure && revealed && expanded && fits;
    if (!clampToggleFollowsOverflow) {
        fail(`clamp: hidden ${hiddenBeforeMeasure}, revealed ${revealed}, expanded ${expanded}, fits ${fits}`);
    }

    // ── subtasksCountDone ───────────────────────────────────────────────
    const subtasks = sectionHeaded(detail, "Subtasks");
    const subCount = subtasks && subtasks.querySelector(".task-detail-count").textContent;
    const rows = subtasks ? subtasks.querySelectorAll(".task-detail-subtask") : [];
    const doneRows = rows.filter((row) => row.getAttribute("data-state") === "done");
    const openRow = rows.find((row) => row.getAttribute("data-state") === "open");
    await click(openRow, "the open subtask");
    const subtasksCountDone = subCount === "1 of 2" && doneRows.length === 1
        && calls.navigated.join(",") === "c-open";
    if (!subtasksCountDone) fail(`subtasks read "${subCount}", navigated [${calls.navigated}]`);

    // ── deliverablesCounted ─────────────────────────────────────────────
    const deliverables = sectionHeaded(detail, "Deliverables");
    const deliverablesCounted = Boolean(deliverables)
        && deliverables.querySelector(".task-detail-count").textContent === "2"
        && deliverables.querySelectorAll(".task-detail-file").length === 2
        && deliverables.textContent.includes(CHILD_OPEN.title);
    if (!deliverablesCounted) fail("the Deliverables head did not count own and child files");

    // ── contractIsCollapsible ───────────────────────────────────────────
    const contract = detail.querySelector(".task-detail-contract");
    const contractSummary = contract && contract.querySelector("summary").textContent;
    const contractIsCollapsible = contract && contract.tagName === "DETAILS"
        && contractSummary === "What counts as done for an Implementation engineer"
        && Boolean(withAttr(contract, ".callout", "data-tone", "warn"));
    if (!contractIsCollapsible) fail(`contract summary read "${contractSummary}"`);

    // ── activityReadsAsSentences ────────────────────────────────────────
    const texts = detail.querySelectorAll(".task-detail-event-text").map((p) => p.textContent);
    const times = detail.querySelectorAll(".task-detail-event-time").map((span) => span.textContent);
    const described = global.BossModTaskEvents.describeEvent(EVENTS[0]);
    const activityReadsAsSentences = texts.length === 4
        && texts[0].includes("Jim marked it blocked · no progress, @Debra")
        && texts[1].includes("Jim accepted it") && !texts[1].includes(" · ")
        && texts[2].includes("BossMod · Reused the existing open task")
        && texts[3].includes("Debra · Moved it along by hand")
        && times[0] === "2h ago"
        && JSON.stringify(described)
            === JSON.stringify({ actor: "Jim", verb: "marked it blocked", detail: "no progress, @Debra" });
    if (!activityReadsAsSentences) fail(`activity read ${JSON.stringify(texts)} at ${JSON.stringify(times)}`);

    // ── pencilIsTheHeadsOnlyTool ────────────────────────────────────────
    // At rest an open task's head holds the pencil and nothing else; no
    // status action shows until Edit mode.
    const pencil = panel.querySelector("#ct-edit-mode-btn");
    const noStatusAtRest = ["#ct-resume-task-btn", "#ct-complete-task-btn", "#ct-cancel-task-btn"]
        .every((id) => !panel.querySelector(id));
    const pencilAtRest = Boolean(pencil) && !pencil.hidden
        && pencil.getAttribute("aria-label") === "Edit mode" && pencil.getAttribute("data-tooltip") === "Edit mode"
        && panel.querySelector("#ct-edit-save").hidden && panel.querySelector("#ct-edit-discard").hidden
        && !panel.querySelector("#task-options") && !panel.querySelector(".menu-action") && noStatusAtRest;
    blocked.close();
    await drain();

    // ── completeCalloutShowsSummary ─────────────────────────────────────
    const done = open(DONE, calls);
    await drain();
    const donePanel = document.body.querySelectorAll(".modal-panel").pop();
    const ok = withAttr(donePanel, ".callout", "data-tone", "ok");
    const completeCalloutShowsSummary = Boolean(ok)
        && ok.querySelector(".callout-title").textContent === "Done claim"
        && ok.textContent.includes(DONE.completion_summary);
    if (!completeCalloutShowsSummary) fail("a complete task shows no Done claim with its summary");
    const pencilIsTheHeadsOnlyTool = pencilAtRest && !donePanel.querySelector("#ct-edit-mode-btn")
        && !donePanel.querySelector("#ct-edit-save");
    if (!pencilIsTheHeadsOnlyTool) fail("the head is not [✎] at rest, or a finished task still has tools");
    done.close();
    await drain();
    if (document.body.querySelectorAll(".modal-panel").length !== 0) fail("a detail outlived its close");

    const edit = await editMode();
    const status = await statusActions();
    const { completeNeedsSummary } = await operatorActions();

    process.stdout.write(JSON.stringify({
        ok: true,
        noDuplicateTitle,
        statusLineReadsProgress,
        factsArePairs,
        humanRequesterIsYou,
        alertCalloutOffersChat,
        completeCalloutShowsSummary,
        instructionsUseMarkdown,
        clampToggleFollowsOverflow,
        subtasksCountDone,
        pencilIsTheHeadsOnlyTool,
        ...edit,
        ...status,
        completeNeedsSummary,
        contractIsCollapsible,
        activityReadsAsSentences,
        deliverablesCounted,
    }));
}

/**
 * The real BossModTaskActions against a recording api: each request is kept,
 * and each is answered by the next queued reply.
 */
function recordingActions() {
    const requests = [];
    const replies = [];
    const actionApi = (url, opts) => {
        const init = opts || {};
        requests.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : null });
        const reply = replies.shift();
        if (!reply) throw new Error(`[task-detail-harness] no reply queued for ${url}`);
        return Promise.resolve({
            ok: reply.status < 400, status: reply.status, json: () => Promise.resolve(reply.body),
        });
    };
    const store = {
        getState: () => ({
            roster: [
                { id: "a1", name: "Jim", role: "Implementation engineer", color: "#3b82f6", floorId: "f1" },
                { id: "a2", name: "Debra", role: "Designer", color: "#10b981", floorId: "f1" },
            ],
        }),
    };
    const changed = [];
    const updated = [];
    const errors = [];
    const actions = global.BossModTaskActions.create({
        api: actionApi,
        store,
        onChanged: (ids) => changed.push(...ids),
        onUpdated: (row) => updated.push(row),
        onError: (m) => errors.push(m),
    });
    return { actions, requests, replies, changed, updated, errors };
}

const top = () => document.body.querySelectorAll(".modal-panel").pop() || null;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * In the file picker on top: select the listed file `select`, or type a new
 * `name`; then press "Use this file".
 */
async function pickInPicker({ select, name }) {
    const picker = top();
    if (select) {
        await click(picker.querySelectorAll(".desk-entry").find((row) => row.getAttribute("data-path") === select),
            `${select} in the picker`);
    } else {
        const field = picker.querySelector("#task-file-picker-name");
        field.value = name;
        field.dispatchEvent({ type: "input" });
    }
    await click(picker.querySelector("#task-file-picker-use"), "Use this file");
}

/**
 * Edit mode, driven through the real detail and the real actions: the same
 * panel becomes editable in place, and every way out of it is checked.
 */
async function editMode() {
    const { actions, requests, replies, updated } = recordingActions();
    global.BossModTaskDetail.openTaskDetail({
        api,
        taskId: BLOCKED.id,
        tasks: TASKS,
        colorOf: () => undefined,
        onNavigate: () => {},
        actions,
        onOpenChat: () => {},
    });
    await drain();
    const panel = top();
    const save = panel.querySelector("#ct-edit-save");
    const discard = panel.querySelector("#ct-edit-discard");
    const pencil = panel.querySelector("#ct-edit-mode-btn");
    const titleText = () => panel.querySelector(".modal-title").textContent;
    const titleInput = () => panel.querySelector(".edit-field-title");
    const fileRows = () => panel.querySelectorAll("[data-file-row]");
    const errorCallout = () => {
        const slot = panel.querySelector(".task-detail-edit-error");
        return slot && !slot.hidden ? slot.querySelector(".callout") : null;
    };
    const editing = () => Boolean(titleInput()) && !save.hidden && !discard.hidden && pencil.hidden;
    const atRest = () => !titleInput() && save.hidden && discard.hidden && !pencil.hidden;
    const enter = () => click(pencil, "the pencil");
    const statusButtons = () => panel.querySelector(".task-detail-status").querySelectorAll("button");
    const hint = () => panel.querySelector(".task-detail-status-hint");
    /** The row the detail shows now: what the server last returned. */
    let row = BLOCKED;
    const reply = (status, body) => replies.push({ status, body });

    // ── editModeEntersInPlace ───────────────────────────────────────────
    const restedFirst = atRest();
    await enter();
    const input = titleInput();
    const editModeEntersInPlace = restedFirst && editing()
        && input.parentNode === panel.querySelector(".modal-title")
        && input.value === BLOCKED.title && input.getAttribute("aria-label") === "Task title"
        && document.activeElement === input && input.readOnly === false
        && panel.getAttribute("aria-label") === BLOCKED.title
        && Boolean(panel.querySelector(".edit-field-multiline"))
        && fileRows().length === 1 && document.body.querySelectorAll(".modal-panel").length === 1;
    if (!editModeEntersInPlace) fail("entering Edit mode did not make the same panel editable");

    // ── assigneeReadsAsAField ───────────────────────────────────────────
    // The fact cell's dropdown is the field look: no .btn, the short name
    // beside the chip, the full row label kept for a screen reader.
    const trigger = panel.querySelector(".menu-select-trigger");
    const assigneeReadsAsAField = trigger.classList.contains("menu-select-field")
        && !trigger.classList.contains("btn")
        && trigger.querySelector(".menu-select-value").textContent === "Jim"
        && Boolean(trigger.querySelector(".avatar"))
        && trigger.getAttribute("aria-label").startsWith("Assignee: Jim — Implementation engineer");
    if (!assigneeReadsAsAField) fail(`the assignee trigger read "${trigger.getAttribute("aria-label")}"`);

    // ── oneDeliverablesSection ──────────────────────────────────────────
    // The task's own editable row and the add-row first, then the subtask's
    // file under its title, read-only, all counted in the one head.
    const heads = panel.querySelectorAll(".task-detail-section-head")
        .filter((node) => node.children[0] && node.children[0].textContent === "Deliverables");
    const section = heads.length === 1 ? heads[0].parentNode : null;
    const childHeading = section && section.querySelectorAll(".task-detail-meta")
        .find((node) => node.textContent === CHILD_OPEN.title);
    const addRowAt = section ? section.children.indexOf(section.querySelector("#ct-edit-add-file")) : -1;
    const oneDeliverablesSection = Boolean(section) && Boolean(childHeading)
        && section.querySelector(".task-detail-count").textContent === "2"
        && section.querySelectorAll("[data-file-row]").length === 1
        && addRowAt >= 0 && section.children.indexOf(childHeading) > addRowAt;
    if (!oneDeliverablesSection) fail(`Edit mode showed ${heads.length} Deliverables sections`);

    // ── statusActionsWaitForTheDraft ────────────────────────────────────
    const idsInEdit = statusButtons().map((item) => item.getAttribute("id")).join(",");
    const cleanEnabled = statusButtons().every((item) => !item.disabled) && hint().hidden;
    input.value = "Scratch title";
    input.dispatchEvent({ type: "input" });
    const dirtyDisabled = statusButtons().every((item) => item.disabled) && !hint().hidden
        && hint().textContent === "Save or discard your changes first";
    input.value = BLOCKED.title;
    input.dispatchEvent({ type: "input" });
    const cleanAgain = statusButtons().every((item) => !item.disabled) && hint().hidden;
    const statusActionsWaitForTheDraft = idsInEdit === "ct-resume-task-btn,ct-complete-task-btn,ct-cancel-task-btn"
        && cleanEnabled && dirtyDisabled && cleanAgain
        && withAttr(panel, "button", "id", "ct-cancel-task-btn").classList.contains("btn-danger");
    if (!statusActionsWaitForTheDraft) {
        fail(`status actions [${idsInEdit}]: clean ${cleanEnabled}, dirty ${dirtyDisabled}, again ${cleanAgain}`);
    }

    // ── discardRestores ─────────────────────────────────────────────────
    input.value = "Scratch title";
    panel.querySelector(".edit-field-multiline").value = "Scratch";
    await click(panel.querySelector("#ct-edit-add-file"), "Add a required file");
    await pickInPicker({ name: "draft.md" });
    const grewARow = fileRows().length === 2 && top() === panel;
    await click(discard, "✕");
    const leftClean = atRest() && titleText() === BLOCKED.title && document.activeElement === pencil
        && !panel.querySelector(".edit-field-multiline") && requests.length === 0;
    await enter();
    const discardRestores = grewARow && leftClean && titleInput().value === BLOCKED.title
        && panel.querySelector(".edit-field-multiline").value === BLOCKED.description
        && fileRows().length === 1
        && fileRows()[0].querySelector("[data-field=\"path\"]").textContent === "/me/out/report.md";
    if (!discardRestores) fail(`✕ left grew ${grewARow}, clean ${leftClean}`);

    // ── unchangedSaveSendsNothing ───────────────────────────────────────
    await click(save, "✓");
    const unchangedSaveSendsNothing = requests.length === 0 && atRest() && updated.length === 0;
    if (!unchangedSaveSendsNothing) fail(`an unchanged save sent ${JSON.stringify(requests)}`);

    // ── saveRepaintsInPlace (title only), saveRefreshesActivity ─────────
    await enter();
    titleInput().value = "Fix the login bug today";
    row = { ...row, title: "Fix the login bug today" };
    reply(200, row);
    const readsBeforeSave = eventReads;
    await click(save, "✓");
    const saveRefreshesActivity = eventReads === readsBeforeSave + 1;
    const titleOnly = requests.length === 1 && requests[0].method === "PATCH"
        && requests[0].url === "/api/tasks/t-blocked" && same(requests[0].body, { title: row.title });
    const saveRepaintsInPlace = titleOnly && top() === panel
        && document.body.querySelectorAll(".modal-panel").length === 1
        && atRest() && titleText() === row.title && panel.getAttribute("aria-label") === row.title
        && updated.length === 1 && updated[0].title === row.title && document.activeElement === pencil;
    if (!saveRepaintsInPlace) fail(`a title save sent ${JSON.stringify(requests[0])}; head "${titleText()}"`);

    // ── mismatchOffersOverride (Enter saves from the title) ─────────────
    await enter();
    await click(panel.querySelector(".menu-select-trigger"), "the assignee trigger");
    const pickDebra = async () => {
        const debra = panel.querySelectorAll(".menu-select-option").find((item) => item.textContent.includes("Debra"));
        await click(debra, "Debra in the assignee menu");
    };
    await pickDebra();
    reply(409, {
        outcome: "specialty_mismatch",
        reason: "Debra is \"Designer\".",
        suggested_assignees: [{ id: "a1", name: "Jim", role: "Implementation engineer", match: "match" }],
    });
    let prevented = false;
    titleInput().dispatchEvent({ type: "keydown", key: "Enter", preventDefault() { prevented = true; } });
    await drain();
    const warn = errorCallout();
    const warned = prevented && Boolean(warn) && warn.getAttribute("data-tone") === "warn"
        && warn.textContent.includes("Specialty mismatch — nothing was saved")
        && warn.textContent.includes("Debra is") && editing()
        && same(requests[1].body, { assigned_to: "a2" });
    // A suggestion chooses, it does not save.
    const jim = warn.querySelectorAll("button").find((item) => item.textContent.startsWith("Jim"));
    await click(jim, "the suggested Jim");
    const suggestionChoosesOnly = requests.length === 2
        && panel.querySelector(".menu-select-trigger").getAttribute("aria-label").includes("Jim");
    await click(panel.querySelector(".menu-select-trigger"), "the assignee trigger");
    await pickDebra();
    row = { ...row, assigned_to: "a2", assigned_to_name: "Debra" };
    reply(200, row);
    await click(panel.querySelector("#ct-edit-reassign-anyway"), "Reassign anyway");
    const mismatchOffersOverride = warned && suggestionChoosesOnly
        && same(requests[2].body, { assigned_to: "a2", confirm_specialty_mismatch: true })
        && atRest() && top() === panel && updated.length === 2;
    if (!mismatchOffersOverride) {
        fail(`the reassign sent ${JSON.stringify(requests.slice(1))}; warned ${warned}, suggestion ${suggestionChoosesOnly}`);
    }

    // ── failedSaveKeepsDraft, fileEditsSendTheList, filesArePicked ──────
    // A row appears only once a path is picked, browsing as the DRAFT's
    // assignee (Debra now); the path button reopens the picker at its folder.
    await enter();
    await click(fileRows()[0].querySelector(".task-detail-file-remove"), "the file's ✕");
    const readsBeforeAdd = deskReads.length;
    await click(panel.querySelector("#ct-edit-add-file"), "Add a required file");
    const pickerOpened = top() !== panel && top().getAttribute("aria-label") === "Choose a file"
        && fileRows().length === 0 && deskReads.length === readsBeforeAdd + 1
        && same(deskReads[deskReads.length - 1], { agent: "a2", path: "/projects" });
    await pickInPicker({ name: "summary.md" });
    const added = fileRows()[0];
    const pathButton = added.querySelector("[data-field=\"path\"]");
    const rowAfterPick = top() === panel && fileRows().length === 1 && pathButton.tagName === "BUTTON"
        && pathButton.textContent === "/projects/summary.md"
        && pathButton.getAttribute("aria-label") === "Change file: /projects/summary.md"
        && added.querySelector(".task-detail-file-name").textContent === "summary.md";
    await click(pathButton, "the row's path");
    const repickOpensAtFolder = top() !== panel
        && same(deskReads[deskReads.length - 1], { agent: "a2", path: "/projects" });
    await pickInPicker({ select: "/projects/plan.md" });
    const repickChangesPath = top() === panel && pathButton.textContent === "/projects/plan.md"
        && pathButton.getAttribute("aria-label") === "Change file: /projects/plan.md"
        && added.querySelector(".task-detail-file-name").textContent === "plan.md" && fileRows().length === 1;
    const filesArePicked = pickerOpened && rowAfterPick && repickOpensAtFolder && repickChangesPath;
    if (!filesArePicked) {
        fail(`picking: opened ${pickerOpened}, row ${rowAfterPick}, repick ${repickOpensAtFolder}/${repickChangesPath}`);
    }
    const note = added.querySelector("[data-field=\"description\"]");
    note.value = "The summary";
    note.dispatchEvent({ type: "input" });
    reply(400, { detail: "Path is outside the allowed roots" });
    await click(save, "✓");
    const refused = errorCallout();
    const failedSaveKeepsDraft = Boolean(refused) && refused.getAttribute("data-tone") === "alert"
        && refused.textContent.includes("Path is outside the allowed roots")
        && editing() && fileRows().length === 1 && pathButton.textContent === "/projects/plan.md"
        && updated.length === 2;
    if (!failedSaveKeepsDraft) fail("a refused save lost the draft or left edit mode");
    const files = [{ type: "file", path: "/projects/plan.md", description: "The summary" }];
    row = { ...row, work_contract: { deliverables: files } };
    reply(200, row);
    await click(save, "✓");
    const replacedFiles = same(requests[4].body, { work_contract: { deliverables: files } }) && atRest();
    await enter();
    await click(fileRows()[0].querySelector(".task-detail-file-remove"), "the file's ✕");
    // No own file left: the head counts the subtask's one.
    const countedChildOnly = panel.querySelectorAll(".task-detail-count")
        .some((node) => node.parentNode.textContent.startsWith("Deliverables") && node.textContent === "1");
    row = { ...row, work_contract: null };
    reply(200, row);
    await click(save, "✓");
    const fileEditsSendTheList = replacedFiles && countedChildOnly
        && same(requests[5].body, { work_contract: null }) && atRest();
    if (!fileEditsSendTheList) fail(`the file edits sent ${JSON.stringify(requests.slice(3))}`);

    // ── browsingFollowsTheAssignee ──────────────────────────────────────
    // With the draft unassigned there is no file view to browse: the add-row
    // is disabled and the section says why; choosing someone re-enables it.
    await enter();
    const addButton = () => panel.querySelector("#ct-edit-add-file");
    const filesHint = () => panel.querySelector(".task-detail-files-hint");
    const chooseAssignee = async (label) => {
        await click(panel.querySelector(".menu-select-trigger"), "the assignee trigger");
        await click(panel.querySelectorAll(".menu-select-option").find((item) => item.textContent.includes(label)),
            `${label} in the assignee menu`);
    };
    const browsableAtFirst = !addButton().disabled && filesHint().hidden;
    await chooseAssignee("Unassigned backlog");
    // (A disabled button fires no click in a browser; this fake DOM would
    // still fire it, so the refusal is read off the control, not clicked.)
    const refusedUnassigned = addButton().disabled && !filesHint().hidden
        && filesHint().textContent === "Pick an assignee to browse their files."
        && panel.querySelector(".menu-select-value").textContent === "Unassigned";
    await chooseAssignee("Jim");
    const browsingFollowsTheAssignee = browsableAtFirst && refusedUnassigned
        && !addButton().disabled && filesHint().hidden && top() === panel;
    await click(discard, "✕");
    if (!browsingFollowsTheAssignee) fail(`browsing: first ${browsableAtFirst}, unassigned ${refusedUnassigned}`);

    // ── backdropRefusedWhileEditing, escDiscardsWhileEditing ────────────
    await enter();
    const scrim = document.body.querySelector(".modal-backdrop");
    await click(scrim, "the backdrop");
    const backdropRefusedWhileEditing = top() === panel && editing();
    titleInput().value = "Scratch";
    let stopped = false;
    let escPrevented = false;
    panel.dispatchEvent({
        type: "keydown", key: "Escape", target: titleInput(),
        preventDefault() { escPrevented = true; }, stopPropagation() { stopped = true; },
    });
    const escDiscardsWhileEditing = stopped && escPrevented && top() === panel && atRest()
        && titleText() === row.title && requests.length === 6;
    await click(scrim, "the backdrop");
    const backdropClosesAtRest = document.body.querySelectorAll(".modal-panel").length === 0;
    if (!backdropRefusedWhileEditing || !backdropClosesAtRest) fail("the backdrop ignored Edit mode");
    if (!escDiscardsWhileEditing) fail("Esc while editing did not discard in place");

    return {
        editModeEntersInPlace,
        assigneeReadsAsAField,
        oneDeliverablesSection,
        statusActionsWaitForTheDraft,
        saveRefreshesActivity,
        discardRestores,
        unchangedSaveSendsNothing,
        saveRepaintsInPlace,
        mismatchOffersOverride,
        failedSaveKeepsDraft,
        fileEditsSendTheList,
        filesArePicked,
        browsingFollowsTheAssignee,
        backdropRefusedWhileEditing: backdropRefusedWhileEditing && backdropClosesAtRest,
        escDiscardsWhileEditing,
    };
}

/**
 * The status row's actions, driven through the real detail and the real
 * actions: shown in Edit mode only, and every outcome repaints in place.
 */
async function statusActions() {
    const { actions, requests, replies, changed } = recordingActions();
    const reply = (status, body) => replies.push({ status, body });
    const openLive = (task) => {
        const handle = global.BossModTaskDetail.openTaskDetail({
            api, taskId: task.id, tasks: TASKS, colorOf: () => undefined,
            onNavigate: () => {}, actions, onOpenChat: () => {},
        });
        return drain().then(() => ({ handle, panel: top() }));
    };
    const pill = (panel) => panel.querySelector(".task-detail-status").querySelector(".status-pill")
        .getAttribute("data-status");
    const statusIds = (panel) => panel.querySelector(".task-detail-status").querySelectorAll("button")
        .map((item) => item.getAttribute("id")).join(",");
    const editingIn = (panel) => Boolean(panel.querySelector(".edit-field-title"));
    const noTools = (panel) => ["#ct-edit-mode-btn", "#ct-edit-save", "#ct-edit-discard"]
        .every((id) => panel.querySelector(id).hidden);
    const footerButton = (panel, label) => panel.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((item) => item.textContent === label);
    const pencilOf = (panel) => panel.querySelector("#ct-edit-mode-btn");

    // ── statusActionsFollowTheFlags ─────────────────────────────────────
    // A pending task is neither resumable nor completable: Cancel alone.
    const pending = await openLive(PENDING);
    const pendingAtRest = statusIds(pending.panel) === "";
    await click(pencilOf(pending.panel), "the pending task's pencil");
    const statusActionsFollowTheFlags = pendingAtRest && statusIds(pending.panel) === "ct-cancel-task-btn";
    if (!statusActionsFollowTheFlags) fail(`a pending task offered [${statusIds(pending.panel)}]`);
    pending.handle.close();
    await drain();

    // ── resumeRepaintsInPlace ───────────────────────────────────────────
    const resumed = await openLive(BLOCKED);
    await click(pencilOf(resumed.panel), "the pencil");
    reply(200, { ...BLOCKED, status: "pending", operator_can_resume: false, operator_can_complete: false });
    const readsBeforeResume = eventReads;
    await click(resumed.panel.querySelector("#ct-resume-task-btn"), "Resume");
    const resumeRepaintsInPlace = requests.length === 1 && requests[0].method === "POST"
        && requests[0].url === "/api/tasks/t-blocked/resume" && requests[0].body === null
        && top() === resumed.panel && pill(resumed.panel) === "pending" && !editingIn(resumed.panel)
        && statusIds(resumed.panel) === "" && !pencilOf(resumed.panel).hidden
        && document.activeElement === pencilOf(resumed.panel)
        && eventReads === readsBeforeResume + 1 && changed.join(",") === "t-blocked";
    if (!resumeRepaintsInPlace) fail(`Resume sent ${JSON.stringify(requests[0])}; pill ${pill(resumed.panel)}`);
    resumed.handle.close();
    await drain();

    // ── rejectedActionKeepsEditMode ─────────────────────────────────────
    const refused = await openLive(BLOCKED);
    await click(pencilOf(refused.panel), "the pencil");
    reply(400, { detail: "Only a blocked or stalled task with an assignee can be resumed" });
    await click(refused.panel.querySelector("#ct-resume-task-btn"), "Resume");
    const slot = refused.panel.querySelector(".task-detail-edit-error");
    const alertCallout = slot && !slot.hidden ? slot.querySelector(".callout") : null;
    const rejectedActionKeepsEditMode = requests.length === 2 && Boolean(alertCallout)
        && alertCallout.getAttribute("data-tone") === "alert"
        && alertCallout.textContent.includes("Only a blocked or stalled task with an assignee can be resumed")
        && editingIn(refused.panel) && pill(refused.panel) === "blocked"
        && refused.panel.querySelector(".edit-field-title").value === BLOCKED.title
        && !refused.panel.querySelector("#ct-resume-task-btn").disabled && changed.length === 1;
    if (!rejectedActionKeepsEditMode) fail("a refused status action left Edit mode or said nothing");
    refused.handle.close();
    await drain();

    // ── cancelConfirmsThenRepaints ──────────────────────────────────────
    const cancelled = await openLive(BLOCKED);
    await click(pencilOf(cancelled.panel), "the pencil");
    await click(cancelled.panel.querySelector("#ct-cancel-task-btn"), "Cancel task…");
    const confirm = top();
    const asked = confirm !== cancelled.panel && confirm.textContent.includes("Cancel this task?");
    await click(footerButton(confirm, "Keep it"), "Keep it");
    const keptEditing = top() === cancelled.panel && editingIn(cancelled.panel) && requests.length === 2
        && !cancelled.panel.querySelector("#ct-cancel-task-btn").disabled;
    await click(cancelled.panel.querySelector("#ct-cancel-task-btn"), "Cancel task…");
    reply(200, { ...BLOCKED, status: "cancelled", closed_at: ago(0), operator_can_resume: false,
        operator_can_complete: false });
    await click(footerButton(top(), "Cancel task"), "Cancel task");
    const cancelConfirmsThenRepaints = asked && keptEditing
        && requests[2].method === "POST" && requests[2].url === "/api/tasks/t-blocked/cancel"
        && top() === cancelled.panel && pill(cancelled.panel) === "cancelled"
        && !editingIn(cancelled.panel) && noTools(cancelled.panel)
        && document.activeElement === cancelled.panel.querySelector(".modal-close");
    if (!cancelConfirmsThenRepaints) fail(`cancel: asked ${asked}, kept ${keptEditing}, pill ${pill(cancelled.panel)}`);
    cancelled.handle.close();
    await drain();

    // ── completeRepaintsFinished ────────────────────────────────────────
    const completed = await openLive(BLOCKED);
    await click(pencilOf(completed.panel), "the pencil");
    await click(completed.panel.querySelector("#ct-complete-task-btn"), "Mark complete…");
    top().querySelector("#ct-complete-summary").value = "Shipped behind the flag.";
    reply(200, { ...BLOCKED, status: "complete", closed_at: ago(0), completion_summary: "Shipped behind the flag.",
        operator_can_resume: false, operator_can_complete: false });
    await click(top().querySelector("#ct-complete-submit"), "#ct-complete-submit");
    const doneClaim = withAttr(completed.panel, ".callout", "data-tone", "ok");
    const completeRepaintsFinished = requests[3].url === "/api/tasks/t-blocked/complete"
        && top() === completed.panel && pill(completed.panel) === "complete"
        && Boolean(doneClaim) && doneClaim.textContent.includes("Shipped behind the flag.")
        && !editingIn(completed.panel) && noTools(completed.panel)
        && document.activeElement === completed.panel.querySelector(".modal-close");
    if (!completeRepaintsFinished) fail(`complete repainted as ${pill(completed.panel)}`);
    completed.handle.close();
    await drain();
    if (top() !== null) fail("a status action left a layer behind");

    return {
        statusActionsFollowTheFlags,
        resumeRepaintsInPlace,
        rejectedActionKeepsEditMode,
        cancelConfirmsThenRepaints,
        completeRepaintsFinished,
    };
}

/** The completer, as the status row's Mark complete… opens it. */
async function operatorActions() {
    const { actions, requests, replies, changed } = recordingActions();

    // ── completeNeedsSummary ────────────────────────────────────────────
    const nothing = await actions.complete(PENDING);
    const pendingOpenedNothing = nothing === null && top() === null;
    // Keep open resolves null and posts nothing.
    const kept = actions.complete(BLOCKED);
    await drain();
    await click(top().querySelectorAll(".modal-actions")[0].querySelectorAll("button")
        .find((item) => item.textContent === "Keep open"), "Keep open");
    const keptOpen = (await kept) === null && requests.length === 0 && top() === null;
    const completing = actions.complete(BLOCKED);
    await drain();
    const completePanel = top();
    await click(completePanel.querySelector("#ct-complete-submit"), "#ct-complete-submit");
    const refusedBlank = requests.length === 0
        && completePanel.querySelector(".callout").textContent.includes("A summary is required");
    completePanel.querySelector("#ct-complete-summary").value = "Shipped behind the flag.";
    replies.push({ status: 200, body: { ...BLOCKED, status: "complete" } });
    await click(completePanel.querySelector("#ct-complete-submit"), "#ct-complete-submit");
    const row = await completing;
    const posted = requests[0];
    const completeNeedsSummary = pendingOpenedNothing && keptOpen && refusedBlank
        && posted.method === "POST" && posted.url === "/api/tasks/t-blocked/complete"
        && JSON.stringify(posted.body) === JSON.stringify({ summary: "Shipped behind the flag." })
        && row && row.status === "complete" && changed.length === 1 && top() === null;
    if (!completeNeedsSummary) fail(`the completion sent ${JSON.stringify(posted)}`);

    // Open subtasks refuse a completion; the promise rejects with the reason.
    const refusal = actions.complete(BLOCKED).then(() => null, (err) => err);
    await drain();
    top().querySelector("#ct-complete-summary").value = "Done enough.";
    replies.push({ status: 409, body: { reason: "Resolve first", task_ids: ["c-open"] } });
    await click(top().querySelector("#ct-complete-submit"), "#ct-complete-submit");
    const err = await refusal;
    if (!err || !err.message.includes(global.BossModTasksComplete.SUBTASKS_COPY) || changed.length !== 1) {
        fail(`an open-subtask refusal said [${err && err.message}]`);
    }
    return { completeNeedsSummary };
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
