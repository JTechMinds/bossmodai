/**
 * Node harness: the task detail, built from the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * Opens real tasks in the real modal and reads what it shows: a status line
 * measured the way the watchdog measures, the facts as pairs, the callout for
 * each kind of state, the instruction clamp, the subtask checklist, the `⋯`
 * that holds Edit, Mark complete (only where the server allows it) and
 * Cancel, the role contract, and the activity as sentences. Then it drives the
 * real operator actions behind that `⋯`: the edit form sends only what
 * changed and resends a refused reassign with the confirmation, and the
 * completer will not post without a summary.
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
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlays", "BossModMenu", "BossModMenuSelect",
    "BossModFactList",
    // The instructions' clamp is the shared component the desk's description
    // uses too; it renders through the stubbed BossModMarkdown above.
    "BossModClampedMarkdown",
    "BossModTasksColumns", "BossModTasksData", "BossModTaskDeliverables",
    "BossModTaskEvents", "BossModTaskDetailSections", "BossModTaskDetail",
    "BossModAssignOutcomes", "BossModAssignForm", "BossModTasksCancel",
    "BossModTasksComplete", "BossModTaskEditForm", "BossModTaskActions",
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

function api(url) {
    if (String(url).includes("/events")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(EVENTS) });
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
            edit: (t) => calls.edited.push(t),
            completeOne: (t) => calls.completed.push(t),
            cancelOne: (t) => calls.cancelled.push(t),
        },
        onOpenChat: (t) => calls.chats.push(t),
    });
}

async function main() {
    const SECTIONS = global.BossModTaskDetailSections;
    const calls = { navigated: [], cancelled: [], completed: [], edited: [], chats: [] };
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

    // ── optionsHoldCancel, optionsOfferEditAndComplete ──────────────────
    await click(panel.querySelector("#task-options"), "#task-options");
    const itemIds = panel.querySelectorAll(".menu-action").map((item) => item.getAttribute("id"));
    const offersAll = itemIds.join(",") === "ct-edit-task-btn,ct-complete-task-btn,ct-cancel-task-btn";
    await click(panel.querySelector("#ct-edit-task-btn"), "#ct-edit-task-btn");
    await click(panel.querySelector("#task-options"), "#task-options");
    await click(panel.querySelector("#ct-complete-task-btn"), "#ct-complete-task-btn");
    await click(panel.querySelector("#task-options"), "#task-options");
    const cancelItem = panel.querySelector("#ct-cancel-task-btn");
    await click(cancelItem, "#ct-cancel-task-btn");
    const cancelledThis = calls.cancelled.length === 1 && calls.cancelled[0] === BLOCKED
        && !panel.querySelector("#ct-cancel-task-btn");
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
    const optionsHoldCancel = cancelledThis && !donePanel.querySelector("#task-options");
    if (!optionsHoldCancel) fail("the ⋯ did not hold Cancel, or a finished task still has one");
    done.close();
    await drain();

    const pending = open(PENDING, calls);
    await drain();
    const pendingPanel = document.body.querySelectorAll(".modal-panel").pop();
    await click(pendingPanel.querySelector("#task-options"), "the pending task's ⋯");
    const pendingIds = pendingPanel.querySelectorAll(".menu-action").map((item) => item.getAttribute("id"));
    const optionsOfferEditAndComplete = offersAll
        && calls.edited.length === 1 && calls.edited[0] === BLOCKED
        && calls.completed.length === 1 && calls.completed[0] === BLOCKED
        && pendingIds.join(",") === "ct-edit-task-btn,ct-cancel-task-btn";
    if (!optionsOfferEditAndComplete) {
        fail(`the ⋯ offered [${itemIds}] and, for a pending task, [${pendingIds}]`);
    }
    pending.close();
    await drain();
    if (document.body.querySelectorAll(".modal-panel").length !== 0) fail("a detail outlived its close");

    const { editSendsOnlyChanges, mismatchRetryConfirms, completeNeedsSummary } = await operatorActions();

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
        optionsHoldCancel,
        optionsOfferEditAndComplete,
        editSendsOnlyChanges,
        mismatchRetryConfirms,
        completeNeedsSummary,
        contractIsCollapsible,
        activityReadsAsSentences,
        deliverablesCounted,
    }));
}

/**
 * Drive the real BossModTaskActions against a recording api: the edit form
 * and the completer, as the `⋯` opens them.
 */
async function operatorActions() {
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
    const errors = [];
    const actions = global.BossModTaskActions.create({
        api: actionApi, store, onChanged: (ids) => changed.push(...ids), onError: (m) => errors.push(m),
    });
    const top = () => document.body.querySelectorAll(".modal-panel").pop() || null;

    // ── editSendsOnlyChanges ────────────────────────────────────────────
    actions.edit(BLOCKED);
    await drain();
    const editPanel = top();
    editPanel.querySelector("#ct-edit-title").value = "Fix the login bug today";
    replies.push({ status: 200, body: { ...BLOCKED, title: "Fix the login bug today" } });
    await click(editPanel.querySelector("#ct-edit-submit"), "#ct-edit-submit");
    const first = requests[0];
    const editSendsOnlyChanges = Boolean(first) && first.method === "PATCH"
        && first.url === "/api/tasks/t-blocked"
        && JSON.stringify(first.body) === JSON.stringify({ title: "Fix the login bug today" })
        && changed.join(",") === "t-blocked" && top() === null;
    if (!editSendsOnlyChanges) fail(`the edit sent ${JSON.stringify(first)}; changed [${changed}]`);

    // ── mismatchRetryConfirms ───────────────────────────────────────────
    actions.edit(BLOCKED);
    await drain();
    const retryPanel = top();
    await click(retryPanel.querySelector(".menu-select-trigger"), "the assignee trigger");
    const debra = retryPanel.querySelectorAll(".menu-select-option")
        .find((row) => row.textContent.includes("Debra"));
    await click(debra, "Debra in the assignee menu");
    replies.push({
        status: 409,
        body: { outcome: "specialty_mismatch", reason: 'Debra is "Designer".', suggested_assignees: [] },
    });
    await click(retryPanel.querySelector("#ct-edit-submit"), "#ct-edit-submit");
    const warned = Boolean(retryPanel.querySelector(".callout"))
        && retryPanel.querySelector(".callout").getAttribute("data-tone") === "warn" && top() === retryPanel;
    replies.push({ status: 200, body: { ...BLOCKED, assigned_to: "a2" } });
    await click(retryPanel.querySelector("#ct-edit-reassign-anyway"), "Reassign anyway");
    const mismatchRetryConfirms = warned
        && JSON.stringify(requests[1].body) === JSON.stringify({ assigned_to: "a2" })
        && JSON.stringify(requests[2].body)
            === JSON.stringify({ assigned_to: "a2", confirm_specialty_mismatch: true })
        && top() === null;
    if (!mismatchRetryConfirms) fail(`the reassign sent ${JSON.stringify(requests.slice(1))}`);

    // ── completeNeedsSummary ────────────────────────────────────────────
    actions.completeOne(PENDING);
    await drain();
    const pendingOpenedNothing = top() === null;
    actions.completeOne(BLOCKED);
    await drain();
    const completePanel = top();
    await click(completePanel.querySelector("#ct-complete-submit"), "#ct-complete-submit");
    const refusedBlank = requests.length === 3
        && completePanel.querySelector(".callout").textContent.includes("A summary is required");
    completePanel.querySelector("#ct-complete-summary").value = "Shipped behind the flag.";
    replies.push({ status: 200, body: { ...BLOCKED, status: "complete" } });
    await click(completePanel.querySelector("#ct-complete-submit"), "#ct-complete-submit");
    const posted = requests[3];
    const completeNeedsSummary = pendingOpenedNothing && refusedBlank
        && posted.method === "POST" && posted.url === "/api/tasks/t-blocked/complete"
        && JSON.stringify(posted.body) === JSON.stringify({ summary: "Shipped behind the flag." })
        && changed.length === 3 && errors.length === 0 && top() === null;
    if (!completeNeedsSummary) fail(`the completion sent ${JSON.stringify(posted)}; errors [${errors}]`);

    // Open subtasks refuse a completion; the reason reaches onError.
    actions.completeOne(BLOCKED);
    await drain();
    top().querySelector("#ct-complete-summary").value = "Done enough.";
    replies.push({ status: 409, body: { reason: "Resolve first", task_ids: ["c-open"] } });
    await click(top().querySelector("#ct-complete-submit"), "#ct-complete-submit");
    if (!errors.length || !errors[0].includes(global.BossModTasksComplete.SUBTASKS_COPY)) {
        fail(`an open-subtask refusal said [${errors}]`);
    }
    return { editSendsOnlyChanges, mismatchRetryConfirms, completeNeedsSummary };
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
