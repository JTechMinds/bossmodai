/**
 * Node harness: a task's severity and its reference documents, in the real modules.
 *
 * Invoked by tests/test_ui_tasks.py. Not a browser bundle.
 *
 * The card carries the severity pill; the detail shows a Severity fact and a
 * References section before Deliverables, whose cards open through the
 * reporter's desk while deliverables still open through the assignee's; and
 * Edit mode sends `severity` only when it was changed.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");
const { installIconsStub } = require("./js_icons_stub.cjs");

installDom();
installIconsStub();
global.lucide = null;
global.BossModMarkdown = { render: (text) => [document.createTextNode(text)] };
/** Every path the file viewer was asked to open. */
const viewed = [];
global.BossModFileViewer = { open: (path) => { viewed.push(path); return Promise.resolve(); } };

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModFormat", "BossModSpecialty", "BossModGates",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuSelect",
    "BossModAutoGrow",
    "BossModFileListing",
    "BossModFactList",
    "BossModClampedMarkdown",
    "BossModTasksColumns", "BossModTasksData", "BossModTaskCard", "BossModTaskDeliverables",
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

const NOW = new Date().toISOString();
const REFERENCE = "/projects/webapp/qa/issue-login-500.md";
const COMPANY_PATH = "floors/lobby/projects/webapp/qa/issue-login-500.md";

/** Filed by Debra (a2), now assigned to Jim (a1), with one file to produce. */
const FILED = {
    id: "t-filed",
    title: "Login returns 500",
    status: "accepted",
    severity: "P1",
    description: "Wrong password gives a 500.",
    assigned_to: "a1",
    assigned_to_name: "Jim",
    requester_id: "a2",
    requester_name: "Debra",
    owner_id: "a1",
    parent_task_id: null,
    floor_id: "f1",
    last_activity: NOW,
    created_at: NOW,
    closed_at: null,
    references: [{ path: REFERENCE, description: "Steps to reproduce" }],
    work_contract: { deliverables: [{ type: "file", path: "/me/fix-notes.md", description: "What changed" }] },
    operator_can_complete: false,
    operator_can_resume: false,
};
const TASKS = [FILED];

/** Every desk read: `{agent, path}`. */
const deskReads = [];
const requests = [];
const replies = [];

function api(url, opts) {
    const text = String(url);
    if (text.includes("/events")) return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    const desk = /^\/api\/agents\/([^/]+)\/desk\?path=(.*)$/.exec(text);
    if (desk) {
        const path = decodeURIComponent(desk[2]);
        deskReads.push({ agent: decodeURIComponent(desk[1]), path });
        if (path !== REFERENCE) throw new Error(`[task-severity-harness] no desk entry for ${path}`);
        return Promise.resolve({
            ok: true, status: 200, json: () => Promise.resolve({ kind: "file", company_path: COMPANY_PATH }),
        });
    }
    const init = opts || {};
    requests.push({ url: text, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    const reply = replies.shift();
    if (!reply) throw new Error(`[task-severity-harness] no reply queued for ${text}`);
    return Promise.resolve({ ok: reply.status < 400, status: reply.status, json: () => Promise.resolve(reply.body) });
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function fail(message) {
    process.stderr.write(`${message}\n`);
    process.exit(1);
}

async function click(el, what) {
    if (!el) fail(`nothing to click: ${what}`);
    await el.dispatchClick();
    await drain();
}

/** The section whose head's first span reads `label`. */
function sectionHeaded(root, label) {
    const head = root.querySelectorAll(".task-detail-section-head")
        .find((node) => node.children[0] && node.children[0].textContent === label);
    return head ? head.parentNode : null;
}

function cardShowsSeverity() {
    const card = global.BossModTaskCard.renderCard(
        { ...FILED, severity: "P0", status: "pending", assigned_to: null, assigned_to_name: null },
        { onOpen: () => {}, onToggleSelect: () => {}, colorOf: () => undefined },
    );
    const meta = card.querySelector(".task-card-meta");
    const pill = meta && meta.children[0];
    return Boolean(pill) && pill.classList.contains("status-pill")
        && pill.getAttribute("data-severity") === "P0"
        && pill.textContent === "Severity P0"
        && pill.querySelector(".visually-hidden").textContent === "Severity ";
}

async function main() {
    const cardShowsSeverityPill = cardShowsSeverity();
    if (!cardShowsSeverityPill) fail("the card shows no P0 severity pill first in its meta row");

    const store = {
        getState: () => ({
            roster: [
                { id: "a1", name: "Jim", role: "Engineer", color: "#3b82f6", floorId: "f1" },
                { id: "a2", name: "Debra", role: "QA", color: "#10b981", floorId: "f1" },
            ],
        }),
    };
    const actions = global.BossModTaskActions.create({
        api, store, onChanged: () => {}, onUpdated: () => {}, onError: () => {},
    });
    global.BossModTaskDetail.openTaskDetail({
        api, taskId: FILED.id, tasks: TASKS, colorOf: () => undefined,
        onNavigate: () => {}, actions, onOpenChat: () => {},
    });
    await drain();
    const panel = document.body.querySelectorAll(".modal-panel").pop();
    const column = panel.querySelector(".task-detail-column");

    // ── detailShowsSeverityFact ─────────────────────────────────────────
    const facts = column.querySelector(".fact-list");
    const labels = facts.querySelectorAll(".fact-label").map((dt) => dt.textContent);
    const severityValue = facts.querySelectorAll(".fact-value")[labels.indexOf("Severity")];
    const detailShowsSeverityFact = labels.includes("Severity") && Boolean(severityValue)
        && severityValue.querySelector(".status-pill").getAttribute("data-severity") === "P1"
        && severityValue.textContent === "P1";
    if (!detailShowsSeverityFact) fail(`facts were [${labels}]`);

    // ── referencesComeBeforeDeliverables ────────────────────────────────
    const references = sectionHeaded(column, "References");
    const deliverables = sectionHeaded(column, "Deliverables");
    const referencesComeBeforeDeliverables = Boolean(references) && Boolean(deliverables)
        && column.children.indexOf(references) < column.children.indexOf(deliverables)
        && references.querySelector(".task-detail-count").textContent === "1"
        && references.querySelector(".task-detail-file-desc").textContent === "Steps to reproduce"
        && references.querySelector(".task-detail-file-path").textContent === REFERENCE;
    if (!referencesComeBeforeDeliverables) fail("References is missing, miscounted or not before Deliverables");

    // ── referenceOpensThroughTheReporter ────────────────────────────────
    const refCard = references.querySelector(".task-detail-file");
    await click(refCard, "the reference card");
    const referenceOpensThroughTheReporter = refCard.getAttribute("data-agent-id") === "a2"
        && same(deskReads, [{ agent: "a2", path: REFERENCE }])
        && same(viewed, [COMPANY_PATH]) && !refCard.classList.contains("is-failed");
    if (!referenceOpensThroughTheReporter) fail(`the reference opened via ${JSON.stringify(deskReads)}`);

    // ── deliverableStillUsesTheAssignee ─────────────────────────────────
    const deliverableStillUsesTheAssignee = deliverables.querySelector(".task-detail-file")
        .getAttribute("data-agent-id") === "a1";
    if (!deliverableStillUsesTheAssignee) fail("a deliverable no longer resolves against the assignee");

    // ── editSendsSeverityOnlyWhenChanged ────────────────────────────────
    const pencil = panel.querySelector("#ct-edit-mode-btn");
    const save = panel.querySelector("#ct-edit-save");
    const severityTrigger = () => panel.querySelectorAll(".menu-select-trigger")
        .find((node) => String(node.getAttribute("aria-label")).startsWith("Severity:"));
    await click(pencil, "the pencil");
    const fieldInEdit = Boolean(severityTrigger())
        && severityTrigger().getAttribute("aria-label") === "Severity: P1 — High";
    const title = panel.querySelector(".edit-field-title");
    title.value = "Login returns 500 on a wrong password";
    title.dispatchEvent({ type: "input" });
    replies.push({ status: 200, body: { ...FILED, title: title.value } });
    await click(save, "✓");
    const titleOnly = requests.length === 1 && same(requests[0].body, { title: "Login returns 500 on a wrong password" });

    await click(pencil, "the pencil");
    await click(severityTrigger(), "the severity trigger");
    const options = panel.querySelectorAll(".menu-select-option").map((item) => item.textContent);
    await click(panel.querySelectorAll(".menu-select-option").find((item) => item.textContent === "P0 — Critical"),
        "P0 in the severity menu");
    replies.push({ status: 200, body: { ...FILED, severity: "P0" } });
    await click(save, "✓");
    const severityOnly = requests.length === 2 && requests[1].method === "PATCH"
        && requests[1].url === "/api/tasks/t-filed" && same(requests[1].body, { severity: "P0" });
    const editSendsSeverityOnlyWhenChanged = fieldInEdit && titleOnly && severityOnly
        && same(options, ["P0 — Critical", "P1 — High", "P2 — Medium", "P3 — Low"]);
    if (!editSendsSeverityOnlyWhenChanged) {
        fail(`edit: field ${fieldInEdit}, options ${JSON.stringify(options)}, sent ${JSON.stringify(requests)}`);
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        cardShowsSeverityPill,
        detailShowsSeverityFact,
        referencesComeBeforeDeliverables,
        referenceOpensThroughTheReporter,
        deliverableStillUsesTheAssignee,
        editSendsSeverityOnlyWhenChanged,
    }));
}

main().catch((err) => fail(err && err.stack ? err.stack : String(err)));
