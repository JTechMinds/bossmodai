/**
 * Node harness: the task detail, built from the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * Opens real tasks in the real modal and reads what it shows: a status line
 * measured the way the watchdog measures, the facts as pairs, the callout for
 * each kind of state, the instruction clamp, the subtask checklist, the
 * assignee's own checklist as written (read-only, in Edit mode too), the
 * pencil that enters Edit mode (open tasks only), the role contract, and the
 * activity as sentences. Then it drives the real operator actions: Edit mode
 * edits the same panel in place — ✓ sends only what changed, a refused
 * reassign is resent with the confirmation, a save repaints the layer it came
 * from and re-reads Activity, ✕, Esc and an outside click never lose a draft
 * by accident, and one Deliverables section holds the task's own rows and its
 * subtasks'. The status row's Resume, Mark complete… and Cancel task… appear
 * only in Edit mode, wait for an unsaved draft, and repaint the detail in
 * place; and the completer will not post without a summary. A held task's
 * Waiting on lists what it waits on and opens it; Hold… asks why in its own
 * layer, and Release starts a task at once unless something it waits on is
 * unfinished, when it confirms first.
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
    "BossModDom", "BossModAvatar", "BossModFormat", "BossModSpecialty", "BossModGates", "BossModBus",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuSelect",
    "BossModAutoGrow",
    "BossModFileListing",
    "BossModFactList",
    // The instructions' clamp is the shared component the desk's description
    // uses too; it renders through the stubbed BossModMarkdown above.
    "BossModClampedMarkdown",
    "BossModTasksColumns", "BossModTasksData", "BossModTaskChecklist", "BossModTaskDeliverables",
    "BossModTaskEvents", "BossModTaskSteps", "BossModTaskStepsHistory", "BossModTaskDetailSections",
    "BossModAssignOutcomes", "BossModAssignForm",
    "BossModTaskFilePicker", "BossModTaskEditFiles", "BossModTaskEditMode", "BossModTaskDetail",
    "BossModTasksCancel", "BossModTasksComplete", "BossModTasksHold", "BossModTasksRelease",
    "BossModTaskActions",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

/** The one bus every detail here follows `task_step` on. */
const bus = global.BossModBus.createBus(global.BossModBus.KNOWN_TOPICS);

const NOW = Date.now();
const ago = (ms) => new Date(NOW - ms).toISOString();
const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;

const BLOCKED = {
    id: "t-blocked",
    title: "Fix the login bug",
    status: "blocked",
    severity: "P1",
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
    // The assignee's plan, one item in each state, as the server sends it.
    checklist: [
        { text: "Reproduce the failure", status: "done",
            note: "Fails on every second login — steps in /projects/app/login.md" },
        { text: "Check the mobile client", status: "skipped", note: "The mobile app has no login of its own." },
        { text: "Patch the session check", status: "open", note: null },
    ],
    operator_can_complete: true,
    operator_can_resume: true,
};
const CHILD_DONE = {
    id: "c-done", title: "Write the regression test", status: "complete", severity: "P3",
    parent_task_id: "t-blocked", assigned_to: "a1", assigned_to_name: "Jim",
    last_activity: ago(2 * HOUR), created_at: ago(20 * HOUR), closed_at: ago(2 * HOUR),
};
const CHILD_OPEN = {
    id: "c-open", title: "Patch the session check", status: "active", severity: "P3",
    parent_task_id: "t-blocked", assigned_to: "a2", assigned_to_name: "Debra",
    last_activity: ago(HOUR), created_at: ago(20 * HOUR), closed_at: null,
    work_contract: { deliverables: [{ type: "file", path: "/me/patch.diff" }] },
};
const DONE = {
    id: "t-done", title: "Ship the dashboard", status: "complete", severity: "P3",
    completion_summary: "Shipped behind the flag.",
    assigned_to: "a1", assigned_to_name: "Jim", requester_id: "a2", requester_name: "Debra",
    parent_task_id: null,
    last_activity: ago(3 * HOUR), created_at: ago(48 * HOUR), closed_at: ago(3 * HOUR),
};
// Not yet accepted: the state machine has no pending → complete, so the
// server says it cannot be marked complete. Assigned and unstarted, so it can
// be held.
const PENDING = {
    id: "t-pending", title: "Draft the rollout note", status: "pending", severity: "P3",
    assigned_to: "a2", assigned_to_name: "Debra", requester_id: "__human__", parent_task_id: null,
    last_activity: ago(HOUR), created_at: ago(HOUR), closed_at: null,
    operator_can_complete: false,
    operator_can_resume: false,
    operator_can_hold: true,
};
// What a held task waits on, as GET /api/tasks lists both ends: one
// prerequisite finished, one still being worked.
const RESEARCH = {
    id: "p-research", title: "Research", status: "complete", severity: "P3",
    assigned_to: "a3", assigned_to_name: "Taylor", parent_task_id: null,
    last_activity: ago(2 * HOUR), created_at: ago(30 * HOUR), closed_at: ago(2 * HOUR),
};
const OUTLINE = {
    id: "p-outline", title: "Outline", status: "active", severity: "P3",
    assigned_to: "a4", assigned_to_name: "Jo", parent_task_id: null,
    last_activity: ago(HOUR), created_at: ago(30 * HOUR), closed_at: null,
};
const prerequisiteRow = (task) => ({
    id: task.id, title: task.title, status: task.status, assigned_to_name: task.assigned_to_name,
});
const HELD = {
    id: "t-held", title: "Draft the post", status: "held", severity: "P3",
    assigned_to: "a2", assigned_to_name: "Debra", requester_id: "a1", requester_name: "Jim",
    parent_task_id: null, last_activity: ago(HOUR), created_at: ago(HOUR), closed_at: null,
    operator_can_complete: false, operator_can_resume: false,
    operator_can_hold: false, operator_can_release: true,
    prerequisite_rows: [prerequisiteRow(RESEARCH), prerequisiteRow(OUTLINE)],
};
// Held with nothing left unfinished — what a settle fault leaves for the boss
// to release. Its one prerequisite is not in the list the detail was given,
// so its row says so by not being a button.
const HELD_READY = {
    ...HELD, id: "t-held-ready", title: "Publish the post",
    prerequisite_rows: [{ id: "p-elsewhere", title: "Legal review", status: "complete", assigned_to_name: null }],
};
const TASKS = [BLOCKED, CHILD_DONE, CHILD_OPEN, DONE, PENDING, RESEARCH, OUTLINE, HELD, HELD_READY];

const EVENTS = [
    { id: "e1", event_type: "status_update", author_name: "Jim", author_agent_id: "a1",
        content: "Status active → blocked: no progress, @Debra", created_at: ago(2 * HOUR) },
    { id: "e2", event_type: "status_update", author_name: "Jim", author_agent_id: "a1",
        content: "Status pending → accepted.", created_at: ago(3 * HOUR) },
    { id: "e3", event_type: "system", author_name: "BossMod", author_agent_id: null,
        content: "Reused the existing open task chosen by the boss.", created_at: ago(4 * HOUR) },
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

/** Every steps request, in order. */
const stepReads = [];
/** How GET …/steps/recent and …/steps answer; steps() swaps it per case. */
let stepsReply = (url) => Promise.resolve({
    ok: true,
    json: () => Promise.resolve(url.includes("/steps/recent")
        ? { steps: [], has_more: false, limit: 5 } : { steps: [], has_more: false }),
});

function api(url) {
    if (String(url).includes("/events")) {
        eventReads += 1;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(EVENTS) });
    }
    if (/^\/api\/tasks\/[^/]+\/steps/.test(String(url))) {
        stepReads.push(String(url));
        return stepsReply(String(url));
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
        bus,
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
            hold: () => { throw new Error("[task-detail-harness] this detail holds nothing"); },
            release: () => { throw new Error("[task-detail-harness] this detail releases nothing"); },
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
    const rows = subtasks ? subtasks.querySelectorAll(".task-detail-check") : [];
    const doneRows = rows.filter((row) => row.getAttribute("data-state") === "done");
    const openRow = rows.find((row) => row.getAttribute("data-state") === "open");
    await click(openRow, "the open subtask");
    const subtasksCountDone = subCount === "1 of 2" && doneRows.length === 1
        && calls.navigated.join(",") === "c-open";
    if (!subtasksCountDone) fail(`subtasks read "${subCount}", navigated [${calls.navigated}]`);

    // ── checklistReadsAsWritten ─────────────────────────────────────────
    // Read-only rows, in order, with the agent's text and notes verbatim.
    // Done and skipped both count as checked off.
    const checklist = sectionHeaded(detail, "Checklist");
    const checklistCount = checklist && checklist.querySelector(".task-detail-count").textContent;
    const checks = checklist ? checklist.querySelectorAll(".task-detail-check") : [];
    const readCheck = (node) => ({
        tag: node.tagName,
        state: node.getAttribute("data-state"),
        title: node.querySelector(".task-detail-check-title").textContent,
        note: node.querySelector(".task-detail-meta") ? node.querySelector(".task-detail-meta").textContent : null,
        said: node.querySelector(".visually-hidden").textContent,
    });
    const sections = detail.querySelectorAll(".task-detail-section");
    const checklistReadsAsWritten = checklistCount === "2 of 3 checked off"
        && same(checks.map(readCheck), BLOCKED.checklist.map((item, index) => ({
            tag: "DIV",
            state: item.status,
            title: item.text,
            note: item.note,
            said: [" — Done", " — Skipped", " — Open"][index],
        })))
        && checklist.querySelectorAll("button").length === 0
        // Between what the task produces and what is being done about it;
        // Recent steps follows the plan, and the subtasks come after both.
        && sections.indexOf(sectionHeaded(detail, "Deliverables")) + 1 === sections.indexOf(checklist)
        && sections.indexOf(checklist) + 1 === sections.indexOf(sectionHeaded(detail, "Recent steps"))
        && sections.indexOf(sectionHeaded(detail, "Recent steps")) + 1 === sections.indexOf(subtasks);
    if (!checklistReadsAsWritten) fail(`checklist read "${checklistCount}": ${JSON.stringify(checks.map(readCheck))}`);

    // ── checklistAbsentWithoutItems, malformedChecklistThrows ───────────
    const throws = (fn) => { try { fn(); return false; } catch (err) { return true; } };
    const checklistAbsentWithoutItems = SECTIONS.checklist({ ...BLOCKED, checklist: [] }) === null
        && SECTIONS.checklist(DONE) === null;
    if (!checklistAbsentWithoutItems) fail("a task without checklist items still drew a Checklist section");
    const malformedChecklistThrows = throws(() => SECTIONS.checklist({ id: "x", checklist: [{ text: "", status: "open" }] }))
        && throws(() => SECTIONS.checklist({ id: "x", checklist: [{ text: "A part", status: "maybe" }] }));
    if (!malformedChecklistThrows) fail("a malformed checklist item drew a row instead of throwing");

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
            === JSON.stringify({ actor: "Jim", verb: "marked it blocked", detail: "no progress, @Debra" })
        // The human's own event is "You"; its stored name is an audit snapshot.
        && global.BossModTaskEvents.describeEvent({
            event_type: "system", author_type: "human", author_name: "Human Operator", content: "Edited.",
        }).actor === "You";
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
    const { completeNeedsSummary, summaryUncapped } = await operatorActions();
    const holdRelease = await holdAndRelease();
    const stepCases = await steps();

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
        checklistReadsAsWritten,
        checklistAbsentWithoutItems,
        malformedChecklistThrows,
        pencilIsTheHeadsOnlyTool,
        ...edit,
        ...status,
        completeNeedsSummary,
        summaryUncapped,
        ...holdRelease,
        contractIsCollapsible,
        activityReadsAsSentences,
        deliverablesCounted,
        ...stepCases,
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
        bus,
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
    // The description is the agent's instructions: no length cap in markup.
    const descriptionUncapped = !panel.querySelector(".task-detail-instructions").hasAttribute("maxlength");
    if (!descriptionUncapped) fail("the task description carries a maxlength");

    // ── checklistReadOnlyInEditMode ─────────────────────────────────────
    const editChecklist = sectionHeaded(panel, "Checklist");
    const checklistReadOnlyInEditMode = Boolean(editChecklist)
        && editChecklist.querySelector(".task-detail-count").textContent === "2 of 3 checked off"
        && editChecklist.querySelectorAll(".task-detail-check").length === 3
        && editChecklist.querySelectorAll("button").length === 0
        && editChecklist.querySelectorAll("input").length === 0
        && editChecklist.querySelectorAll("textarea").length === 0;
    if (!checklistReadOnlyInEditMode) fail("Edit mode dropped the checklist or made it editable");

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

    // ── reassignSavesDirectly (Enter saves from the title) ──────────────
    // Whoever assigns decides who does the work: the menu labels no
    // specialty match, and a reassign is one PATCH with no override step.
    await enter();
    await click(panel.querySelector(".menu-select-trigger"), "the assignee trigger");
    const optionTexts = panel.querySelectorAll(".menu-select-option").map((item) => item.textContent);
    const debra = panel.querySelectorAll(".menu-select-option").find((item) => item.textContent.includes("Debra"));
    await click(debra, "Debra in the assignee menu");
    row = { ...row, assigned_to: "a2", assigned_to_name: "Debra" };
    reply(200, row);
    let prevented = false;
    titleInput().dispatchEvent({ type: "keydown", key: "Enter", preventDefault() { prevented = true; } });
    await drain();
    const unlabelled = optionTexts.length > 0
        && optionTexts.every((text) => !text.includes("(matches)") && !text.includes("(mismatch)"));
    const reassignSavesDirectly = prevented && unlabelled
        && requests.length === 2 && same(requests[1].body, { assigned_to: "a2" })
        && !errorCallout() && atRest() && top() === panel && updated.length === 2;
    if (!reassignSavesDirectly) {
        fail(`the reassign sent ${JSON.stringify(requests.slice(1))}; options ${JSON.stringify(optionTexts)}`);
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
    const replacedFiles = same(requests[3].body, { work_contract: { deliverables: files } }) && atRest();
    await enter();
    await click(fileRows()[0].querySelector(".task-detail-file-remove"), "the file's ✕");
    // No own file left: the head counts the subtask's one.
    const countedChildOnly = panel.querySelectorAll(".task-detail-count")
        .some((node) => node.parentNode.textContent.startsWith("Deliverables") && node.textContent === "1");
    row = { ...row, work_contract: null };
    reply(200, row);
    await click(save, "✓");
    const fileEditsSendTheList = replacedFiles && countedChildOnly
        && same(requests[4].body, { work_contract: null }) && atRest();
    if (!fileEditsSendTheList) fail(`the file edits sent ${JSON.stringify(requests.slice(2))}`);

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
        && titleText() === row.title && requests.length === 5;
    await click(scrim, "the backdrop");
    const backdropClosesAtRest = document.body.querySelectorAll(".modal-panel").length === 0;
    if (!backdropRefusedWhileEditing || !backdropClosesAtRest) fail("the backdrop ignored Edit mode");
    if (!escDiscardsWhileEditing) fail("Esc while editing did not discard in place");

    return {
        editModeEntersInPlace,
        descriptionUncapped,
        checklistReadOnlyInEditMode,
        assigneeReadsAsAField,
        oneDeliverablesSection,
        statusActionsWaitForTheDraft,
        saveRefreshesActivity,
        discardRestores,
        unchangedSaveSendsNothing,
        saveRepaintsInPlace,
        reassignSavesDirectly,
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
            api, bus, taskId: task.id, tasks: TASKS, colorOf: () => undefined,
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
    // A pending task is neither resumable nor completable; it can be held.
    const pending = await openLive(PENDING);
    const pendingAtRest = statusIds(pending.panel) === "";
    await click(pencilOf(pending.panel), "the pending task's pencil");
    const statusActionsFollowTheFlags = pendingAtRest
        && statusIds(pending.panel) === "ct-hold-task-btn,ct-cancel-task-btn";
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
    // The summary is posted whole and mirrored in full: no length cap in markup.
    const summaryUncapped = !completePanel.querySelector("#ct-complete-summary").hasAttribute("maxlength");
    if (!summaryUncapped) fail("the completion summary carries a maxlength");
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
    return { completeNeedsSummary, summaryUncapped };
}

/**
 * Waiting on, Hold… and Release, through the real detail and the real
 * actions: the section lists what a task waits on and opens it as a layer;
 * Hold… asks why in its own layer; Release starts a task at once when
 * nothing it waits on is unfinished, and confirms first while something is.
 */
async function holdAndRelease() {
    const { actions, requests, replies, changed } = recordingActions();
    const reply = (status, body) => replies.push({ status, body });
    const navigated = [];
    const openLive = (task) => {
        const handle = global.BossModTaskDetail.openTaskDetail({
            api, bus, taskId: task.id, tasks: TASKS, colorOf: () => undefined,
            onNavigate: (id) => navigated.push(id), actions, onOpenChat: () => {},
        });
        return drain().then(() => ({ handle, panel: top() }));
    };
    const pill = (panel) => panel.querySelector(".task-detail-status").querySelector(".status-pill")
        .getAttribute("data-status");
    const statusIds = (panel) => panel.querySelector(".task-detail-status").querySelectorAll("button")
        .map((item) => item.getAttribute("id")).join(",");
    const editingIn = (panel) => Boolean(panel.querySelector(".edit-field-title"));
    const pencilOf = (panel) => panel.querySelector("#ct-edit-mode-btn");
    const footerButton = (panel, label) => panel.querySelectorAll(".modal-actions")[0]
        .querySelectorAll("button").find((item) => item.textContent === label);
    const errorText = (panel) => {
        const slot = panel.querySelector(".task-detail-edit-error");
        const callout = slot && !slot.hidden ? slot.querySelector(".callout") : null;
        return callout ? callout.textContent : "";
    };
    const readRow = (node) => ({
        tag: node.tagName,
        title: node.querySelector(".task-detail-check-title").textContent,
        who: node.querySelector(".task-detail-meta") ? node.querySelector(".task-detail-meta").textContent : null,
        status: node.querySelector(".status-pill").getAttribute("data-status"),
        pill: node.querySelector(".status-pill").textContent,
    });

    // ── waitingOnListsPrerequisites ─────────────────────────────────────
    // Edge order, finished counted as complete only; a listed row opens its
    // task, one the list does not hold is not a button; no prerequisites,
    // no section.
    const held = await openLive(HELD);
    const section = sectionHeaded(held.panel, "Waiting on");
    const rows = section ? section.querySelectorAll(".task-detail-prerequisite") : [];
    const heldRows = rows.map(readRow);
    await click(rows[1], "the Outline row");
    const ready = await openLive(HELD_READY);
    const readySection = sectionHeaded(ready.panel, "Waiting on");
    const readyRows = readySection ? readySection.querySelectorAll(".task-detail-prerequisite").map(readRow) : [];
    const pending = await openLive(PENDING);
    const noSectionWithout = !sectionHeaded(pending.panel, "Waiting on");
    pending.handle.close();
    await drain();
    const waitingOnListsPrerequisites = Boolean(section)
        && section.querySelector(".task-detail-count").textContent === "1 of 2 finished"
        && same(heldRows, [
            { tag: "BUTTON", title: "Research", who: "Taylor", status: "complete", pill: "Complete" },
            { tag: "BUTTON", title: "Outline", who: "Jo", status: "active", pill: "Active" },
        ])
        && navigated.join(",") === "p-outline"
        && Boolean(readySection) && readySection.querySelector(".task-detail-count").textContent === "1 of 1 finished"
        && same(readyRows, [
            { tag: "DIV", title: "Legal review", who: null, status: "complete", pill: "Complete" },
        ])
        && noSectionWithout;
    if (!waitingOnListsPrerequisites) {
        fail(`Waiting on read ${JSON.stringify(heldRows)} / ${JSON.stringify(readyRows)}, navigated [${navigated}]`);
    }

    // ── releaseStartsAtOnce ─────────────────────────────────────────────
    // Nothing it waits on is unfinished: one POST, no layer.
    await click(pencilOf(ready.panel), "the ready task's pencil");
    const readyIds = statusIds(ready.panel);
    const pendingRow = (task) => ({ ...task, status: "pending", operator_can_release: false, operator_can_hold: true });
    reply(200, pendingRow(HELD_READY));
    await click(ready.panel.querySelector("#ct-release-task-btn"), "Release");
    const releaseStartsAtOnce = readyIds === "ct-release-task-btn,ct-cancel-task-btn"
        && requests.length === 1 && requests[0].method === "POST"
        && requests[0].url === "/api/tasks/t-held-ready/release" && requests[0].body === null
        && top() === ready.panel && pill(ready.panel) === "pending" && !editingIn(ready.panel)
        && changed.join(",") === "t-held-ready";
    if (!releaseStartsAtOnce) fail(`Release sent ${JSON.stringify(requests)}; pill ${pill(ready.panel)}`);
    ready.handle.close();
    await drain();

    // ── releaseConfirmsWhileWaiting ─────────────────────────────────────
    // "Outline" is still active: Release asks first, Cancel sends nothing and
    // keeps Edit mode, Start now posts and repaints in place.
    await click(pencilOf(held.panel), "the held task's pencil");
    await click(held.panel.querySelector("#ct-release-task-btn"), "Release");
    const confirm = top();
    const asked = confirm !== held.panel && confirm.getAttribute("aria-label") === "Start it now?"
        && confirm.textContent.includes('It is still waiting on "Outline".')
        && !confirm.textContent.includes('"Research"');
    await click(footerButton(confirm, "Cancel"), "Cancel");
    const keptWaiting = top() === held.panel && editingIn(held.panel) && requests.length === 1
        && !held.panel.querySelector("#ct-release-task-btn").disabled && pill(held.panel) === "held";
    await click(held.panel.querySelector("#ct-release-task-btn"), "Release");
    reply(200, pendingRow(HELD));
    await click(footerButton(top(), "Start now"), "Start now");
    const releaseConfirmsWhileWaiting = asked && keptWaiting
        && requests.length === 2 && requests[1].url === "/api/tasks/t-held/release" && requests[1].body === null
        && top() === held.panel && pill(held.panel) === "pending" && !editingIn(held.panel)
        && document.activeElement === pencilOf(held.panel) && changed.join(",") === "t-held-ready,t-held";
    if (!releaseConfirmsWhileWaiting) {
        fail(`release confirm: asked ${asked}, kept ${keptWaiting}, sent ${JSON.stringify(requests[1])}`);
    }
    held.handle.close();
    await drain();

    // ── holdAsksThenRepaints ────────────────────────────────────────────
    // Its own layer with one optional field; Cancel sends nothing, Hold
    // posts the reason and the detail repaints held.
    const holding = await openLive(PENDING);
    await click(pencilOf(holding.panel), "the pending task's pencil");
    await click(holding.panel.querySelector("#ct-hold-task-btn"), "Hold…");
    const layer = top();
    const label = layer.querySelector(".assign-field-label");
    const holdLayerReads = layer !== holding.panel && layer.getAttribute("aria-label") === "Put this on hold?"
        && Boolean(label) && label.textContent === "Why? (optional)"
        && Boolean(layer.querySelector("#ct-hold-note")) && Boolean(footerButton(layer, "Hold"));
    await click(footerButton(layer, "Cancel"), "Cancel");
    const cancelledHold = top() === holding.panel && editingIn(holding.panel) && requests.length === 2
        && !holding.panel.querySelector("#ct-hold-task-btn").disabled;
    await click(holding.panel.querySelector("#ct-hold-task-btn"), "Hold…");
    top().querySelector("#ct-hold-note").value = "  Waiting for the brand review  ";
    reply(200, { ...PENDING, status: "held", status_note: "Waiting for the brand review",
        operator_can_hold: false, operator_can_release: true });
    await click(top().querySelector("#ct-hold-submit"), "Hold");
    const holdAsksThenRepaints = holdLayerReads && cancelledHold
        && requests.length === 3 && requests[2].method === "POST" && requests[2].url === "/api/tasks/t-pending/hold"
        && same(requests[2].body, { note: "Waiting for the brand review" })
        && top() === holding.panel && pill(holding.panel) === "held" && !editingIn(holding.panel)
        && changed.join(",") === "t-held-ready,t-held,t-pending";
    if (!holdAsksThenRepaints) {
        fail(`hold: layer ${holdLayerReads}, cancel ${cancelledHold}, sent ${JSON.stringify(requests[2])}`);
    }
    holding.handle.close();
    await drain();

    // ── holdRefusalStaysInEditMode ──────────────────────────────────────
    // A blank why is sent as none; the server's refusal shows in Edit mode.
    const refused = await openLive(PENDING);
    await click(pencilOf(refused.panel), "the pending task's pencil");
    await click(refused.panel.querySelector("#ct-hold-task-btn"), "Hold…");
    reply(400, { detail: "This task just changed. Refresh and try again." });
    await click(top().querySelector("#ct-hold-submit"), "Hold");
    const holdRefusalStaysInEditMode = requests.length === 4 && same(requests[3].body, { note: null })
        && top() === refused.panel && editingIn(refused.panel) && pill(refused.panel) === "pending"
        && errorText(refused.panel).includes("Could not put on hold: This task just changed. Refresh and try again.")
        && !refused.panel.querySelector("#ct-hold-task-btn").disabled && changed.length === 3;
    if (!holdRefusalStaysInEditMode) fail(`a refused hold read "${errorText(refused.panel)}"`);
    refused.handle.close();
    await drain();
    if (top() !== null) fail("hold and release left a layer behind");

    return {
        waitingOnListsPrerequisites,
        releaseStartsAtOnce,
        releaseConfirmsWhileWaiting,
        holdAsksThenRepaints,
        holdRefusalStaysInEditMode,
    };
}

// ─── Recent steps and the step history ──────────────────────────────────

const SECOND = 1000;

/** A step row as GET …/steps and the `task_step` topic carry it. */
function stepRow(id, startedAgoMs, fields) {
    return {
        id, task_id: BLOCKED.id, agent_id: "a1", agent_name: "Jim", action: "bm_cli",
        note: null, command: null, outcome: null, result: null,
        started_at: ago(startedAgoMs), ended_at: null, ...fields,
    };
}
/** Finished `tookMs` after a start `startedAgoMs` ago. */
const endedAt = (startedAgoMs, tookMs) => ago(startedAgoMs - tookMs);
const answer = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });

const RUNNING = stepRow("s5", 5 * SECOND, {
    note: "Run the workspace tests", command: "cargo +1.67.1 test --workspace",
});
const DONE_STEP = stepRow("s4", 2 * MINUTE, {
    note: "Check the clone", command: "git status", outcome: "ok",
    result: "command completed (exit 0, 10ms)", ended_at: endedAt(2 * MINUTE, 3 * SECOND),
});
const FAILED_STEP = stepRow("s3", 4 * MINUTE, {
    note: "Read the host log", command: "cat /var/log/app.log", outcome: "failed",
    result: "BossMod CLI error: Blocked — host path is outside the locked clone",
    ended_at: endedAt(4 * MINUTE, SECOND),
});
const APPROVAL_STEP = stepRow("s2", 6 * MINUTE, {
    note: "Start the database", command: "docker run postgres", outcome: "approval",
    result: "Jim requests approval: docker run postgres", ended_at: endedAt(6 * MINUTE, SECOND),
});
const CONSENT_STEP = stepRow("s1", 8 * MINUTE, {
    note: "Read the deploy folder", command: "ls /srv/app", outcome: "consent",
    result: "Jim asks to use /srv/app", ended_at: endedAt(8 * MINUTE, SECOND),
});

/** Actions for a detail these cases never edit. */
const NO_ACTIONS = {
    update: () => { throw new Error("[task-detail-harness] no save here"); },
    roster: () => [],
    complete: () => { throw new Error("[task-detail-harness] no completion here"); },
    cancel: () => { throw new Error("[task-detail-harness] no cancel here"); },
    resume: () => { throw new Error("[task-detail-harness] no resume here"); },
    hold: () => { throw new Error("[task-detail-harness] no hold here"); },
    release: () => { throw new Error("[task-detail-harness] no release here"); },
};

/**
 * Recent steps in the real detail and the history layer it opens: loading,
 * rows and their outcome words (an open step's on its visible result line),
 * start times that keep an earlier day, live upserts and the trim, empty and
 * error states, paging, a resync reloading both, and every subscription
 * released on close.
 */
async function steps() {
    const rowsIn = (root) => root.querySelectorAll(".task-step");
    const textOf = (li, selector) => {
        const el = li.querySelector(selector);
        return el ? el.textContent : null;
    };
    /** The mark's word for a screen reader; an open step's mark has none. */
    const hiddenWord = (li) => textOf(li, ".visually-hidden");
    /** What each row says it came to: a finished step's hidden word, an open one's visible result line. */
    const words = (root) => rowsIn(root).map((li) => hiddenWord(li) || textOf(li, ".task-step-result"));
    const notes = (root) => rowsIn(root).map((li) => textOf(li, ".task-step-note"));
    // The clock time today, the date and time on an earlier day (a case near
    // midnight can start a step yesterday).
    const sameLocalDay = (iso) => new Date(iso).toDateString() === new Date().toDateString();
    const clock = (iso) => (sameLocalDay(iso)
        ? global.BossModFormat.formatActivityTime(iso) : global.BossModFormat.formatDateTime(iso));
    const base = bus.subscriberCount();
    const calls = { navigated: [], chats: [] };

    // ── detailRequiresBus ───────────────────────────────────────────────
    let busError = null;
    try {
        global.BossModTaskDetail.openTaskDetail({
            api, taskId: BLOCKED.id, tasks: TASKS, colorOf: () => undefined,
            onNavigate: () => {}, actions: NO_ACTIONS, onOpenChat: () => {},
        });
    } catch (err) {
        busError = err.message;
    }
    const detailRequiresBus = busError === "[task-detail] deps.bus is required" && top() === null;
    if (!detailRequiresBus) fail(`a detail without a bus said [${busError}]`);

    // ── recentLoadsThenShowsRows ────────────────────────────────────────
    let release = null;
    stepsReply = () => new Promise((resolve) => { release = resolve; });
    const handle = open(BLOCKED, calls);
    await drain();
    const panel = top();
    const recent = sectionHeaded(panel, "Recent steps");
    const seeAll = recent && recent.querySelector(".task-steps-more");
    const loading = Boolean(recent) && recent.textContent.includes("Loading steps…")
        && rowsIn(recent).length === 0 && seeAll.hidden === true
        && stepReads[stepReads.length - 1] === `/api/tasks/${BLOCKED.id}/steps/recent`;
    release(await answer({
        steps: [RUNNING, DONE_STEP, FAILED_STEP, APPROVAL_STEP, CONSENT_STEP], has_more: false, limit: 5,
    }));
    await drain();
    const loaded = rowsIn(recent);
    const command = loaded[0].querySelector(".task-step-command");
    const recentLoadsThenShowsRows = loading && loaded.length === 5
        && !recent.textContent.includes("Loading steps…")
        && same(notes(recent), [RUNNING, DONE_STEP, FAILED_STEP, APPROVAL_STEP, CONSENT_STEP].map((step) => step.note))
        && command.tagName === "CODE" && command.classList.contains("field-mono")
        && command.textContent === RUNNING.command
        // Open: when and who, no duration, and a result line that says there is none yet.
        && loaded[0].querySelector(".task-step-meta").textContent === `${clock(RUNNING.started_at)} · Jim`
        && textOf(loaded[0], ".task-step-result") === "No result yet"
        && loaded[1].querySelector(".task-step-meta").textContent === `${clock(DONE_STEP.started_at)} · < 1m · Jim`
        && loaded[1].querySelector(".task-step-result").textContent === DONE_STEP.result
        && loaded[2].querySelector(".task-step-result").textContent === FAILED_STEP.result;
    if (!recentLoadsThenShowsRows) {
        fail(`Recent steps loaded ${loaded.length} rows: ${JSON.stringify(notes(recent))}, loading ${loading}`);
    }

    // ── outcomeWordsFollowTheRules ──────────────────────────────────────
    const ROW = global.BossModTaskSteps.row;
    const wordOf = (step, newest) => {
        const li = ROW(step, { newest });
        return hiddenWord(li) || textOf(li, ".task-step-result");
    };
    const actionOnly = ROW(stepRow("x1", 0, { action: "complete", outcome: "ok", ended_at: ago(0) }), { newest: false });
    const commandOnly = ROW(stepRow("x2", 0, { command: "ls" }), { newest: false });
    let malformed = false;
    try {
        global.BossModTaskSteps.checkStep({ ...DONE_STEP, outcome: "maybe" });
    } catch (err) {
        malformed = err instanceof TypeError;
    }
    const outcomeWordsFollowTheRules = same(words(recent),
        ["No result yet", "Done", "Failed", "Waiting for your approval", "Waiting for your permission"])
        && same(loaded.map((li) => li.getAttribute("data-outcome")), ["open", "ok", "failed", "approval", "consent"])
        && wordOf(RUNNING, false) === "No result recorded"
        && wordOf(RUNNING, true) === "No result yet"
        && wordOf(DONE_STEP, true) === "Done"
        // The action's name stands in only when there is neither a note nor a command.
        && actionOnly.querySelector(".task-step-note").textContent === "complete"
        && !actionOnly.querySelector(".task-step-command")
        && !commandOnly.querySelector(".task-step-note")
        && ROW(DONE_STEP, { newest: false }).querySelector(".task-step-note").textContent === DONE_STEP.note
        && malformed;
    if (!outcomeWordsFollowTheRules) fail(`outcome words read ${JSON.stringify(words(recent))}`);

    // ── openStepSaysSoInWords ───────────────────────────────────────────
    // An open step says it has no result on its visible result line, and its
    // mark is the icon alone, so a screen reader reads the word once. A
    // finished step keeps its mark's hidden word next to its own result.
    const openNewest = ROW(RUNNING, { newest: true });
    const openOlder = ROW(RUNNING, { newest: false });
    const finishedRow = ROW(DONE_STEP, { newest: true });
    const iconOnly = (li) => {
        const mark = li.querySelector(".task-step-mark");
        return mark.children.length === 1 && mark.children[0].getAttribute("aria-hidden") === "true"
            && hiddenWord(li) === null;
    };
    const openStepSaysSoInWords = iconOnly(openNewest) && iconOnly(openOlder) && iconOnly(loaded[0])
        && textOf(openNewest, ".task-step-result") === "No result yet"
        && textOf(openOlder, ".task-step-result") === "No result recorded"
        && hiddenWord(finishedRow) === "Done" && textOf(finishedRow, ".task-step-result") === DONE_STEP.result
        && same(loaded.slice(1).map(hiddenWord),
            ["Done", "Failed", "Waiting for your approval", "Waiting for your permission"])
        // A finished step that left no result has no result line; nothing stands in for it.
        && textOf(ROW({ ...DONE_STEP, result: null }, { newest: true }), ".task-step-result") === null;
    if (!openStepSaysSoInWords) {
        fail(`an open step read [${textOf(openNewest, ".task-step-result")}], `
            + `hidden [${hiddenWord(openNewest)}]`);
    }

    // ── stepTimeKeepsTheDay ─────────────────────────────────────────────
    // Today: the clock time alone. An earlier day: the date and the time,
    // which formatActivityTime alone would drop.
    const timeOf = (step) => textOf(ROW(step, { newest: false }), "time");
    // Started this moment rather than at the harness's NOW, which a run
    // across midnight would put on yesterday.
    const todayStep = stepRow("d1", 0, { started_at: new Date().toISOString() });
    const earlierStep = stepRow("d2", 3 * 24 * HOUR, {});
    const stepTimeKeepsTheDay = sameLocalDay(todayStep.started_at)
        && timeOf(todayStep) === global.BossModFormat.formatActivityTime(todayStep.started_at)
        && /^\d{1,2}:\d{2} [AP]M$/.test(timeOf(todayStep))
        && timeOf(earlierStep) === global.BossModFormat.formatDateTime(earlierStep.started_at)
        && /^[A-Z][a-z]{2} \d{1,2}(, \d{4})?, \d{1,2}:\d{2} [AP]M$/.test(timeOf(earlierStep));
    if (!stepTimeKeepsTheDay) fail(`step times read [${timeOf(todayStep)}] and [${timeOf(earlierStep)}]`);

    // ── liveStepUpdatesInPlaceOrPrependsAndTrims, otherTaskIgnored ──────
    const hiddenWhileAllShown = seeAll.hidden === true;
    const finished = { ...RUNNING, outcome: "ok", result: "command completed (exit 0, 225s)", ended_at: ago(0) };
    bus.publish("task_step", finished);
    await drain();
    const inPlace = rowsIn(recent).length === 5
        && same(words(recent), ["Done", "Done", "Failed", "Waiting for your approval", "Waiting for your permission"])
        && rowsIn(recent)[0].querySelector(".task-step-result").textContent === finished.result
        && seeAll.hidden === true;
    const NEWER = stepRow("s6", 0, { note: "Open the report", command: "cat /me/report.md" });
    bus.publish("task_step", NEWER);
    await drain();
    const prependedAndTrimmed = same(notes(recent),
        ["Open the report", "Run the workspace tests", "Check the clone", "Read the host log", "Start the database"])
        && words(recent)[0] === "No result yet" && seeAll.hidden === false;
    const liveStepUpdatesInPlaceOrPrependsAndTrims = inPlace && prependedAndTrimmed;
    if (!liveStepUpdatesInPlaceOrPrependsAndTrims) {
        fail(`live steps: in place ${inPlace}, prepended ${JSON.stringify(notes(recent))}`);
    }
    const beforeOther = notes(recent).join("|");
    bus.publish("task_step", { ...NEWER, id: "elsewhere", task_id: CHILD_OPEN.id, started_at: ago(-MINUTE) });
    await drain();
    const otherTaskIgnored = notes(recent).join("|") === beforeOther;
    if (!otherTaskIgnored) fail("a step for another task reached this detail");

    // ── historyOpensAsLayer ─────────────────────────────────────────────
    const PAGE_ONE = [NEWER, finished, DONE_STEP];
    const PAGE_TWO = [FAILED_STEP, APPROVAL_STEP, CONSENT_STEP];
    stepsReply = (url) => answer(url.includes("before=")
        ? { steps: PAGE_TWO, has_more: false } : { steps: PAGE_ONE, has_more: true });
    const readsBefore = stepReads.length;
    const subscribedBefore = bus.subscriberCount();
    await click(seeAll, "See all steps");
    const layer = top();
    const trail = layer.querySelector(".modal-trail").querySelectorAll("li").map((li) => li.textContent).join(" › ");
    const historyOpensAsLayer = layer !== panel && layer.getAttribute("aria-label") === "Steps"
        && trail === `${BLOCKED.title} › Steps` && panel.hidden === true
        && stepReads[readsBefore] === `/api/tasks/${BLOCKED.id}/steps`
        && same(notes(layer), PAGE_ONE.map((step) => step.note))
        // It follows `task_step` and `resync`.
        && bus.subscriberCount() === subscribedBefore + 2;
    if (!historyOpensAsLayer) fail(`the history opened as "${trail}" with ${JSON.stringify(notes(layer))}`);

    // ── olderSendsOldestId ──────────────────────────────────────────────
    const older = layer.querySelector(".task-steps-older");
    const olderShown = Boolean(older) && older.hidden === false && older.textContent === "Load older steps";
    await click(older, "Load older steps");
    const olderSendsOldestId = olderShown
        && stepReads[stepReads.length - 1] === `/api/tasks/${BLOCKED.id}/steps?before=${DONE_STEP.id}`
        && same(notes(layer), [...PAGE_ONE, ...PAGE_TWO].map((step) => step.note))
        && older.hidden === true;
    if (!olderSendsOldestId) fail(`Load older steps read ${stepReads[stepReads.length - 1]}`);

    // ── historyLivePrepends ─────────────────────────────────────────────
    const NEWEST = stepRow("s7", -SECOND, { note: "Write the summary", command: "write /me/summary.md" });
    bus.publish("task_step", NEWEST);
    await drain();
    // The open step it displaced is no longer the newest: nothing came back for it.
    const historyLivePrepends = notes(layer).length === 7 && notes(layer)[0] === NEWEST.note
        && same(words(layer).slice(0, 2), ["No result yet", "No result recorded"]);
    if (!historyLivePrepends) fail(`a live step in the history read ${JSON.stringify(notes(layer))}`);

    // ── resyncReloadsRecentAndHistory ───────────────────────────────────
    // A reconnect lost whatever steps were sent while the socket was down:
    // Recent steps (under the layer) and the history both read page 1 again,
    // and the history forgets the older page it had loaded.
    const RESYNC_RECENT = [NEWEST, NEWER, finished];
    const RESYNC_PAGE = [NEWEST, NEWER];
    stepsReply = (url) => answer(url.includes("/steps/recent")
        ? { steps: RESYNC_RECENT, has_more: true, limit: 5 } : { steps: RESYNC_PAGE, has_more: true });
    const readsBeforeResync = stepReads.length;
    bus.publish("resync", { downtimeMs: 1200 });
    await drain();
    const resyncReads = stepReads.slice(readsBeforeResync).sort();
    const resyncReloadsRecentAndHistory = same(resyncReads,
        [`/api/tasks/${BLOCKED.id}/steps`, `/api/tasks/${BLOCKED.id}/steps/recent`])
        && same(notes(recent), RESYNC_RECENT.map((step) => step.note))
        && same(notes(layer), RESYNC_PAGE.map((step) => step.note))
        && older.hidden === false;
    if (!resyncReloadsRecentAndHistory) {
        fail(`a resync read ${JSON.stringify(resyncReads)}: recent ${JSON.stringify(notes(recent))}, `
            + `history ${JSON.stringify(notes(layer))}`);
    }

    // ── closingHistoryUnsubscribes ──────────────────────────────────────
    await click(layer.querySelector(".modal-back"), "the history's ‹");
    // Only the detail's Recent steps still answers a resync.
    const readsAtHistoryClose = stepReads.length;
    bus.publish("resync", { downtimeMs: 0 });
    await drain();
    const closingHistoryUnsubscribes = bus.subscriberCount() === subscribedBefore
        && top() === panel && panel.hidden === false
        && same(stepReads.slice(readsAtHistoryClose), [`/api/tasks/${BLOCKED.id}/steps/recent`]);
    if (!closingHistoryUnsubscribes) {
        fail(`closing the history left ${bus.subscriberCount()} subscribers; `
            + `a resync then read ${JSON.stringify(stepReads.slice(readsAtHistoryClose))}`);
    }
    handle.close();
    await drain();
    const readsAtDetailClose = stepReads.length;
    bus.publish("resync", { downtimeMs: 0 });
    await drain();
    const closingDetailUnsubscribes = bus.subscriberCount() === base && top() === null
        && stepReads.length === readsAtDetailClose;
    if (!closingDetailUnsubscribes) {
        fail(`closing the detail left ${bus.subscriberCount() - base} subscribers; `
            + `a resync then read ${stepReads.length - readsAtDetailClose} times`);
    }

    // ── liveDuringLoadIsHeld, recentEmptyAndErrorRetry ──────────────────
    stepsReply = () => new Promise((resolve) => { release = resolve; });
    const heldHandle = open(BLOCKED, calls);
    await drain();
    bus.publish("task_step", RUNNING);
    release(await answer({ steps: [], has_more: false, limit: 5 }));
    await drain();
    const heldRecent = sectionHeaded(top(), "Recent steps");
    const liveDuringLoadIsHeld = same(notes(heldRecent), [RUNNING.note])
        && !heldRecent.textContent.includes("No steps recorded yet.");
    heldHandle.close();
    await drain();
    if (!liveDuringLoadIsHeld) fail(`a step sent mid-load read ${JSON.stringify(notes(heldRecent))}`);

    stepsReply = () => answer({ steps: [], has_more: false, limit: 5 });
    const emptyHandle = open(BLOCKED, calls);
    await drain();
    const emptyRecent = sectionHeaded(top(), "Recent steps");
    const emptyShown = emptyRecent.textContent.includes("No steps recorded yet.")
        && rowsIn(emptyRecent).length === 0 && emptyRecent.querySelector(".task-steps-more").hidden === true;
    emptyHandle.close();
    await drain();

    stepsReply = () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ detail: "boom" }) });
    const errorHandle = open(BLOCKED, calls);
    await drain();
    const errorRecent = sectionHeaded(top(), "Recent steps");
    const alertLine = errorRecent.querySelector(".place-error-detail");
    const retry = errorRecent.querySelectorAll("button").find((button) => button.textContent === "Try again");
    const errorShown = Boolean(alertLine) && alertLine.textContent === "Could not load steps."
        && alertLine.getAttribute("role") === "alert" && Boolean(retry)
        && errorRecent.querySelector(".task-steps-more").hidden === true;
    // The server says there is more: See all shows from the first load.
    stepsReply = () => answer({ steps: [DONE_STEP], has_more: true, limit: 5 });
    await click(retry, "Try again");
    const retried = same(notes(errorRecent), [DONE_STEP.note]) && !errorRecent.querySelector(".place-error-detail")
        && errorRecent.querySelector(".task-steps-more").hidden === false;
    errorHandle.close();
    await drain();
    const recentEmptyAndErrorRetry = emptyShown && errorShown && retried;
    if (!recentEmptyAndErrorRetry) fail(`empty ${emptyShown}, error ${errorShown}, retried ${retried}`);

    // ── seeAllOnlyWhileMore ─────────────────────────────────────────────
    const seeAllOnlyWhileMore = hiddenWhileAllShown && prependedAndTrimmed && retried && emptyShown;
    if (bus.subscriberCount() !== base || top() !== null) fail("a steps case outlived its close");

    return {
        detailRequiresBus,
        recentLoadsThenShowsRows,
        outcomeWordsFollowTheRules,
        openStepSaysSoInWords,
        stepTimeKeepsTheDay,
        liveStepUpdatesInPlaceOrPrependsAndTrims,
        otherTaskIgnored,
        seeAllOnlyWhileMore,
        historyOpensAsLayer,
        olderSendsOldestId,
        historyLivePrepends,
        resyncReloadsRecentAndHistory,
        closingHistoryUnsubscribes,
        closingDetailUnsubscribes,
        liveDuringLoadIsHeld,
        recentEmptyAndErrorRetry,
    };
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
