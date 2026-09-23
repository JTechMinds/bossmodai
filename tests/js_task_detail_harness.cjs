/**
 * Node harness: the task detail, built from the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * Opens real tasks in the real modal and reads what it shows: a status line
 * measured the way the watchdog measures, the facts as pairs, the callout for
 * each kind of state, the instruction clamp, the subtask checklist, the `⋯`
 * that holds Cancel, the role contract, and the activity as sentences.
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
    "BossModOverlayFocus", "BossModOverlays", "BossModMenu", "BossModFactList",
    "BossModTasksColumns", "BossModTasksData", "BossModTaskDeliverables",
    "BossModTaskEvents", "BossModTaskDetailSections", "BossModTaskDetail",
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
const TASKS = [BLOCKED, CHILD_DONE, CHILD_OPEN, DONE];

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
        onCancel: (t) => calls.cancelled.push(t),
        onOpenChat: (t) => calls.chats.push(t),
    });
}

async function main() {
    const SECTIONS = global.BossModTaskDetailSections;
    const calls = { navigated: [], cancelled: [], chats: [] };
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
    const tall = SECTIONS.instructions(BLOCKED);
    const tallBody = tall.querySelector(".task-detail-instructions");
    const tallMore = tall.querySelector(".task-detail-more");
    const hiddenBeforeMeasure = tallMore.hidden === true && tallBody.classList.contains("is-clamped");
    tallBody.scrollHeight = 200;
    tallBody.clientHeight = 100;
    SECTIONS.measureClamp(tall);
    const revealed = tallMore.hidden === false && tallBody.classList.contains("is-clamped");
    await click(tallMore, "Show full instruction");
    const expanded = !tallBody.classList.contains("is-clamped") && !tall.querySelector(".task-detail-more");
    const short = SECTIONS.instructions(BLOCKED);
    const shortBody = short.querySelector(".task-detail-instructions");
    shortBody.scrollHeight = 100;
    shortBody.clientHeight = 100;
    SECTIONS.measureClamp(short);
    const fits = !short.querySelector(".task-detail-more") && !shortBody.classList.contains("is-clamped");
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

    // ── optionsHoldCancel ───────────────────────────────────────────────
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
    if (document.body.querySelectorAll(".modal-panel").length !== 0) fail("a detail outlived its close");

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
        contractIsCollapsible,
        activityReadsAsSentences,
        deliverablesCounted,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
