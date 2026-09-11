/**
 * Node harness: Log Reply transcript on expand, collapsed preview, and
 * activity rows that reach the same reply.
 *
 * Invoked by tests/test_ui_log.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();
global.navigator = { clipboard: { writeText: async () => {} } };

const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
const [domPath, shapePath, detailPath] = process.argv.slice(2);
if (!domPath || !shapePath || !detailPath) {
    throw new Error("expected dom, log-shape, and diagnostic-detail paths");
}
load(domPath, "BossModDom");
load(shapePath, "BossModLogShape");
load(detailPath, "BossModDiagnosticDetail");

const { BossModLogShape: SHAPE, BossModDiagnosticDetail: DETAIL } = global;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

const MSG = "The real transcript\nwith a second line";
const TRIGGER = "the trigger that must not win the preview";

function sectionLabels(root) {
    const out = [];
    const visit = (node) => {
        if (!node || node.nodeType !== 1) return;
        if (node.tagName === "SECTION") {
            const buttons = [];
            const pres = [];
            const collect = (child) => {
                if (!child || child.nodeType !== 1) return;
                if (child.tagName === "BUTTON") buttons.push(child);
                if (child.tagName === "PRE") pres.push(child);
                (child.children || []).forEach(collect);
            };
            collect(node);
            out.push({
                label: buttons[0] ? buttons[0].textContent : "",
                body: pres[0] ? pres[0].textContent : "",
            });
        }
        (node.children || []).forEach(visit);
    };
    visit(root);
    return out;
}

function diagnosticSummary(overrides) {
    return {
        id: "diag-1",
        agent_id: "a1",
        agent_name: "Ada",
        trigger_type: "human_chat",
        trigger_data: JSON.stringify({ content: TRIGGER }),
        status: "success",
        mode: "decision",
        model: "test-model",
        action_name: "answer(none)",
        reply: MSG,
        duration_ms: 12,
        total_tokens: 4,
        created_at: "2026-09-11T12:00:01.000Z",
        error: null,
        ...overrides,
    };
}

// Reply section shows the actual msg, with real newlines, ahead of the JSON dig.
const sections = DETAIL.detailSections({
    trigger_data: { content: TRIGGER },
    raw_response: JSON.stringify({ act: "reply", intent: "info", msg: MSG }),
    parsed_action: { decision: "answer", reply: MSG },
});
const root = global.BossModDom.h("div", null, ...sections);
const listed = sectionLabels(root);
if (listed.length < 2) throw new Error(`expected Reply plus JSON sections, got ${listed.length}`);
if (listed[0].label !== "Reply") throw new Error(`top section must be Reply, got ${listed[0].label}`);
if (listed[0].body !== MSG) {
    throw new Error(`Reply must be the msg with newlines; got ${JSON.stringify(listed[0].body)}`);
}
const parsed = listed.find((entry) => entry.label === "Parsed Action");
if (!parsed) throw new Error("Parsed Action must remain available under Reply");

// Collapsed preview is the first ~120 chars of the reply, not the trigger.
const longReply = `${"word ".repeat(40)}end`;
const previewRow = SHAPE.fromDiagnostic(diagnosticSummary({ reply: longReply }));
if (previewRow.text.includes(TRIGGER)) {
    throw new Error("collapsed preview must not prefer the trigger over the reply");
}
if (previewRow.text.length > SHAPE.PREVIEW_CHARS) {
    throw new Error(`preview longer than ${SHAPE.PREVIEW_CHARS}: ${previewRow.text.length}`);
}
const expected = longReply.replace(/\s+/g, " ").trim();
const clipped = expected.length > SHAPE.PREVIEW_CHARS
    ? `${expected.slice(0, SHAPE.PREVIEW_CHARS - 3)}...`
    : expected;
if (previewRow.text !== clipped) {
    throw new Error(`preview mismatch: ${JSON.stringify(previewRow.text)}`);
}

// A short reply is shown in full on the collapsed row.
const shortRow = SHAPE.fromDiagnostic(diagnosticSummary());
if (shortRow.text !== "The real transcript with a second line") {
    throw new Error(`short preview should flatten newlines, got ${JSON.stringify(shortRow.text)}`);
}

// Activity "answered the request" expands through the matching diagnostic Reply.
const activity = SHAPE.fromActivity({
    id: "act-1",
    source: "activity_log",
    category: "agent",
    event: "decision_applied",
    title: "Ada answered the request",
    detail: null,
    agent_name: "Ada",
    task_id: null,
    metadata: { event: "decision_applied" },
    timestamp: "2026-09-11T12:00:00.000Z",
    is_active: false,
});
if (!SHAPE.isCannedReplyActivity(activity)) {
    throw new Error("canned activity title must be recognised");
}
const diagnostic = SHAPE.fromDiagnostic(diagnosticSummary());
SHAPE.linkActivityRows([activity], [diagnostic]);
if (activity.diagnosticId !== "diag-1") {
    throw new Error(`activity must link to the matching turn, got ${activity.diagnosticId}`);
}

async function proveActivityReachesReply() {
    let fetched = "";
    const api = async (url) => {
        fetched = url;
        return {
            ok: true,
            async json() {
                return {
                    id: "diag-1",
                    raw_response: JSON.stringify({ act: "reply", msg: MSG }),
                    parsed_action: { decision: "answer", reply: MSG },
                };
            },
        };
    };
    const detail = DETAIL.createDetail({ api });
    const expansion = detail.render(activity);
    await drain();
    if (!fetched.includes("/api/diagnostics/diag-1")) {
        throw new Error(`activity expand must fetch the linked turn, got ${fetched}`);
    }
    const expanded = sectionLabels(expansion);
    const reply = expanded.find((entry) => entry.label === "Reply");
    if (!reply) throw new Error("activity expand must surface Reply");
    if (reply.body !== MSG) {
        throw new Error(`activity Reply must be the msg; got ${JSON.stringify(reply.body)}`);
    }
    return true;
}

proveActivityReachesReply().then((activityReachesReply) => {
    process.stdout.write(JSON.stringify({
        ok: true,
        replyShowsMsg: listed[0].body === MSG,
        previewTruncates: previewRow.text.length <= SHAPE.PREVIEW_CHARS,
        activityReachesReply,
    }));
}).catch((err) => {
    console.error(err);
    process.exit(1);
});
