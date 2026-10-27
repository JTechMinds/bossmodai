/**
 * Node harness: the Extensions dialog, the live-view status reader, the
 * single-agent live viewer, the Configure agents layer, and the per-agent
 * surfaces — the data table's wrapper contract (over an injected table
 * factory: Tabulator needs real layout, so its rendering is checked in the
 * real app), the desk's Extensions section and the per-agent settings dialog.
 *
 * Invoked by tests/test_extensions_ui.py with the module paths in load order.
 * The modal frame, the confirm strip, the clock and the network are stubbed;
 * the bus, the dialog, the status module, the viewer, the agents layer, the
 * switch, the avatar and the DOM helpers are the real modules. Prints one
 * JSON object of named verdicts.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModBus", "BossModFormat", "BossModSwitch", "BossModAvatar", "BossModExtensionsApi",
    "BossModLiveStatus", "BossModExtensionsLive", "BossModExtensionPanel", "BossModExtensionsDialog",
    "BossModGates", "BossModSecretField", "BossModDataTable", "BossModConfigForm", "BossModAgentConfigDialog",
    "BossModExtensionSettingsDialog", "BossModExtensionAgentsLayer", "BossModDeskExtensions",
    "BossModTabs", "BossModFactList", "BossModAgentViewDialog", "BossModExternalOpen", "BossModDeskOpener",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
eval(`${fs.readFileSync(paths[0], "utf8")}\n;global.BossModDom = BossModDom;\n`);
const { h } = global.BossModDom;

// ── stubs ──
global.BossModMarketplaceDetail = {
    confirmStrip: (c) => h("div", { class: "market-confirm", role: "alert" },
        h("p", {}, c.text),
        h("button", { id: c.id, onclick: c.onConfirm }, c.label),
        h("button", { class: "cancel", onclick: c.onCancel }, "Cancel")),
};
const layers = [];
global.BossModOverlays = {
    createModal: (options) => {
        const actionsEl = h("div", { class: "actions" });
        const layer = { options, body: h("div", { class: "layer" }, options.body, actionsEl), actions: [] };
        const setActions = (actions) => {
            layer.actions = actions || [];
            actionsEl.replaceChildren();
            layer.actions.forEach((action) => {
                const button = h("button", {
                    type: action.form ? "submit" : "button", form: action.form || null, id: action.id || null,
                    onclick: action.form ? null : () => {
                        if (action.onSelect) action.onSelect();
                        if (!action.keepOpen) closeLayer(layer);
                    },
                }, action.label);
                actionsEl.append(button);
            });
        };
        setActions(options.actions);
        layers.push(layer);
        document.body.append(layer.body);
        return {
            close: () => closeLayer(layer),
            element: layer.body,
            setActions,
            setTitle: (title) => { layer.options.title = title; },
        };
    },
};
function closeLayer(layer) {
    if (!layers.includes(layer)) return;
    layers.splice(layers.indexOf(layer), 1);
    layer.body.remove();
    if (layer.options.onClose) layer.options.onClose();
}
// A fake clock: every timer (one-shot or repeating) is recorded, and
// advance() fires whatever falls due, so a hidden poller would show up as
// extra /live reads.
const timers = new Map();
let nextTimer = 1;
let now = 0;
const addTimer = (fn, ms, every) => { const id = nextTimer++; timers.set(id, { fn, at: now + (ms || 0), every }); return id; };
global.setTimeout = (fn, ms) => addTimer(fn, ms, 0);
global.setInterval = (fn, ms) => addTimer(fn, ms, ms || 1);
global.clearTimeout = (id) => { timers.delete(id); };
global.clearInterval = (id) => { timers.delete(id); };
async function advance(ms) {
    const until = now + ms;
    for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, t] = due;
        now = t.at;
        if (t.every) t.at += t.every; else timers.delete(id);
        await t.fn();
        await drain();
    }
    now = until;
}
// A fake Tabulator for the view dialog: records its options and, like the
// real one, asks for page 1 as soon as it is built.
const tabulators = [];
global.Tabulator = class {
    constructor(el, opts) {
        this.el = el;
        this.opts = opts;
        this.handlers = {};
        this.destroyed = false;
        tabulators.push(this);
        this.firstPage = opts.ajaxRequestFunc(opts.ajaxURL, {}, { page: 1, size: opts.paginationSize });
    }
    on(name, fn) { this.handlers[name] = fn; }
    setData() { return this.opts.ajaxRequestFunc(this.opts.ajaxURL, {}, { page: 1, size: this.opts.paginationSize }); }
    destroy() { this.destroyed = true; }
};
global.BossModIcons = { paint: () => {} };
const revoked = [];
global.URL.revokeObjectURL = (url) => { revoked.push(url); };
const blobFetches = [];
global.apiFetchBlobUrl = async (url) => { blobFetches.push(url); return `blob:${url}`; };
const errors = [];
console.error = (...args) => { errors.push(args.map(String).join(" ")); };
// Only a failed folder open reads the error text, and these tests open it
// successfully; file-ops.js would drag in the whole file-form stack.
global.BossModFileOps = { readApiError: async () => "" };

const bv = {
    id: "browser-vision", name: "Browser Vision", version: "1.0.0", description: "d",
    command: { name: "bv", summary: "s" }, enabled: true, valid: true, invalid_reason: null,
    requires_image_model: true, live_view: { label: "Browser view", icon: "globe" }, setup: { state: "ready", detail: null },
    setup_label: "Download browser (~120 MB)", excluded_agents: [], agent_config: null,
};
// A second live-view extension, with per-agent settings: the desktop has no
// URL, only the active window's title.
const vd = {
    ...bv, id: "virtual-desktop", name: "Virtual Desktop", command: { name: "vm", summary: "s" },
    live_view: { label: "Desktop view", icon: "monitor" }, setup_label: "Build the desktop",
    agent_config: { label: "Virtual desktop" },
};
const desktopItem = (takenAt, title) => ({
    agent_id: "a1", agent_name: "Iris", taken_at: takenAt, command: "vm view", url: null, title,
    facts: [{ label: "screen", value: "1280x800" }, { label: "pointer", value: "(412, 488) px" }],
    image_url: `/api/extensions/virtual-desktop/live/a1/image?t=${Date.parse(takenAt)}`,
});
const liveItem = (takenAt) => ({
    agent_id: "a1", agent_name: "Iris", taken_at: takenAt, command: "bv open example.com",
    url: "https://example.com", title: "Example Domain",
    facts: [
        { label: "resolution", value: "1280x800" }, { label: null, value: "full page" },
        { label: "marks", value: "3" },
    ],
    image_url: `/api/extensions/browser-vision/live/a1/image?t=${Date.parse(takenAt)}`,
});
// Browser Vision's /live reply; any other extension's is in otherLive.
let liveReply = { kind: "ok", body: { items: [] } };
const otherLive = {};
let deskReply = [];
const configCalls = [];
const storedConfig = {
    label: "Microsoft 365 mailbox", help: "Step one.\n\nStep two.", configured: true, updated_at: "2026-09-29T09:00:00Z",
    fields: [
        { key: "tenant_id", label: "Tenant ID", kind: "text", required: true, value: "t-1" },
        { key: "client_secret", label: "Client secret", kind: "secret", required: true, set: true },
        { key: "mailbox", label: "Mailbox address", kind: "email", required: true, value: "old@contoso.com" },
        { key: "check_interval_seconds", label: "Check for new mail every (seconds)", kind: "number", required: false,
          min: 15, max: 3600, default: "90", value: "90" },
    ],
};
let putReply = () => ({
    ok: false, status: 422,
    json: async () => ({ detail: { error: "CONFIG_VERIFY_FAILED", message: "The client secret is wrong or has expired. Create a new secret and try again." } }),
});
// Every /live read, and per extension; every GET /api/extensions.
let liveCalls = 0;
const liveCallsBy = {};
let listCalls = 0;
const folderOpens = [];
let listReply = [bv];
let chatgptReply = { connected: false, pending: false, message: 'Sign in to add the Chat GPT connection.', error: null,
    actions: [{ key: 'login', label: 'Continue with ChatGPT', values: { account_id: '' } }], choices: [] };
const chatgptCalls = [];
let demoConnected = false;
// The Local Network Access stand-in: a command-less service with a panel and
// settings. Each reply is a function so a case can swap behaviour.
const networkCalls = [];
const networkStatus = (code) => ({
    message: 'Other devices can open BossMod at the address below. Use this only on a network you trust: the connection isn’t encrypted.',
    error: null,
    facts: [
        { label: 'Address', value: 'http://192.168.1.20:38472', copyable: true, secret: false },
        { label: 'Access code', value: code, copyable: true, secret: true },
    ],
    actions: [{ key: 'new_code', label: 'Generate new code', values: {} }],
});
let networkCode = 'ABCD2345';
let networkPanel = () => ({ ok: true, body: networkStatus(networkCode) });
let networkEnabled = (enabled) => ({ ok: true, body: { ...networkItem(), enabled } });
let networkSettingsPut = () => ({ ok: true, body: { label: 'Network access', help: 'h', configured: true, verified: null, fields: [] } });
const networkSettings = {
    label: 'Network access', help: 'Choose the port other devices use.\n\nPick another if it is taken.', configured: false,
    fields: [{ key: 'port', label: 'Port', kind: 'number', required: false, min: 1024, max: 65535, default: '38472', value: '38472' }],
};
const networkItem = () => ({
    ...bv, id: 'network', name: 'Local Network Access', command: null, live_view: null, requires_image_model: false,
    setup: { state: 'not_required', detail: null }, setup_label: null, enabled: true, panel: true,
    settings: { label: 'Network access' }, service_error: null, description: 'Lets other devices open BossMod.',
});
global.BossModMenuSelect = {
    create: ({label, options, value, onChange}) => ({ element: h('button', { 'aria-label': label,
        onclick: () => onChange(options[options.length - 1].value) }, label + ': ' + value) }),
};
let deskReads = 0;
const viewCalls = [];
const mailItem = {
    ...bv, id: "mail", name: "Microsoft 365 Mailbox", command: { name: "mail", summary: "s" }, live_view: null,
    requires_image_model: false, setup: { state: "not_required", detail: null }, setup_label: null,
    agent_config: { label: "Microsoft 365 mailbox" },
    agent_view: { label: "Open inbox", views: [{ key: "inbox", label: "Inbox" }, { key: "sent", label: "Sent" }] },
};
const viewPage = (view) => ({
    columns: [{ key: "subject", label: "Subject" }, { key: view === "sent" ? "to" : "from", label: view === "sent" ? "To" : "From" }],
    rows: [{ id: `${view}-1`, cells: { subject: `${view} subject` }, emphasis: false }],
    has_more: false,
    caption: view === "sent" ? "Sent from reports@contoso.com" : "Inbox of reports@contoso.com",
});
// The Configure agents layer's server: who has each extension on, and the
// replies its switch gets. `agentsReply` is "ok", "broken", or a promise the
// read waits on; `configReply` is null (stored) or a refusal's body.
const layerAgents = {
    "virtual-desktop": {
        label: "Virtual desktop", help: "Gives this agent its own Linux desktop.\n\nMemory and CPUs are upper limits.",
        quick_enable: true, requires_image_model: true,
        agents: [
            { id: "a1", name: "Iris", role: "Researcher", color: "#3b82f6", configured: true, summary: null, image_capable: true },
            { id: "a2", name: "Jim", role: "Engineer", color: "#f59e0b", configured: false, summary: null, image_capable: true },
            { id: "a3", name: "Ada", role: null, color: "#10b981", configured: false, summary: null, image_capable: false },
            { id: "a4", name: "Laura", role: null, color: null, configured: true, summary: null, image_capable: false },
        ],
    },
    mail: {
        label: "Microsoft 365 mailbox", help: "Step one.", quick_enable: false, requires_image_model: false,
        agents: [
            { id: "a1", name: "Iris", role: null, color: "#3b82f6", configured: false, summary: null, image_capable: true },
            { id: "a2", name: "Jim", role: null, color: "#f59e0b", configured: true, summary: "jim@contoso.com", image_capable: false },
        ],
    },
};
let agentsReply = "ok";
let agentsReads = 0;
let configReply = null;
const layerCalls = [];
global.apiFetch = async (url, init) => {
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (url === "/api/extensions") { listCalls += 1; return ok(listReply); }
    if (url.startsWith('/api/extensions/demo-provider/provider')) {
        if (init && init.method === 'POST') demoConnected = JSON.parse(init.body).action === 'start-demo';
        return ok({ connected: demoConnected, pending: false, message: demoConnected ? 'Demo ready' : 'Connect demo provider',
            actions: [{ key: 'start-demo', label: 'Start demo', values: {} }], choices: [] });
    }
    const agentsMatch = url.match(/^\/api\/extensions\/([\w-]+)\/agents$/);
    if (agentsMatch) {
        agentsReads += 1;
        if (agentsReply === "broken") {
            return { ok: false, status: 500, json: async () => ({ detail: { error: "BOOM", message: "The agents could not be read." } }) };
        }
        if (agentsReply !== "ok") await agentsReply;
        return ok(JSON.parse(JSON.stringify(layerAgents[agentsMatch[1]])));
    }
    const pairMatch = url.match(/^\/api\/extensions\/virtual-desktop\/agents\/(\w+)\/config$/);
    if (pairMatch) {
        const method = (init && init.method) || "GET";
        layerCalls.push({ method, agent: pairMatch[1], body: init && init.body ? JSON.parse(init.body) : null });
        const agent = layerAgents["virtual-desktop"].agents.find((row) => row.id === pairMatch[1]);
        if (method === "GET") {
            return ok({ label: "Virtual desktop", help: "h", configured: agent.configured, updated_at: null, fields: [] });
        }
        if (configReply) return { ok: false, status: 422, json: async () => ({ detail: configReply }) };
        agent.configured = method === "PUT";
        return method === "PUT" ? ok({ verified: "Ready." }) : { ok: true, status: 204, json: async () => ({}) };
    }
    if (url.startsWith('/api/extensions/chatgpt/')) {
        const method = (init && init.method) || 'GET';
        if (url.endsWith('/enabled')) return ok({ ...listReply[0], enabled: JSON.parse(init.body).enabled });
        const body = init && init.body ? JSON.parse(init.body) : null;
        chatgptCalls.push({ url, method, body });
        if (body && body.action === 'login') chatgptReply = { ...chatgptReply, pending: true,
            message: 'Finish signing in in your browser…', actions: [{ key: 'cancel', label: 'Cancel sign-in', values: {} }] };
        return ok(chatgptReply);
    }
    const viewMatch = url.match(/^\/api\/extensions\/mail\/agents\/a1\/view\?view=(\w+)&skip=(\d+)&top=(\d+)$/);
    if (viewMatch) {
        viewCalls.push({ view: viewMatch[1], skip: Number(viewMatch[2]), top: Number(viewMatch[3]) });
        return ok(viewPage(viewMatch[1]));
    }
    const itemMatch = url.match(/^\/api\/extensions\/mail\/agents\/a1\/view\/([\w-]+)\?view=(\w+)$/);
    if (itemMatch) {
        viewCalls.push({ item: itemMatch[1], view: itemMatch[2] });
        return ok({ title: "Opened", facts: [["To", "jordan@contoso.com"]], body_text: "Body" });
    }
    if (url.startsWith('/api/extensions/network/')) {
        const method = (init && init.method) || 'GET';
        const body = init && init.body ? JSON.parse(init.body) : null;
        networkCalls.push({ url, method, body });
        const reply = (r) => (r.ok
            ? ok(r.body)
            : { ok: false, status: r.status, json: async () => ({ detail: { error: r.code, message: r.message } }) });
        if (url.endsWith('/enabled')) return reply(networkEnabled(body.enabled));
        if (url.endsWith('/panel') || url.endsWith('/panel/actions')) return reply(networkPanel(body));
        if (url.endsWith('/settings')) return method === 'GET' ? ok(JSON.parse(JSON.stringify(networkSettings))) : reply(networkSettingsPut(body));
    }
    if (url.endsWith("/open-folder")) {
        folderOpens.push({ url, method: init && init.method });
        return ok({ status: "ok", path: "/tmp/x" });
    }
    const liveMatch = url.match(/^\/api\/extensions\/([\w-]+)\/live$/);
    if (liveMatch) {
        const id = liveMatch[1];
        liveCalls += 1;
        liveCallsBy[id] = (liveCallsBy[id] || 0) + 1;
        const reply = id === "browser-vision" ? liveReply : otherLive[id];
        if (!reply) throw new Error(`unexpected live read of ${id}`);
        if (reply.kind === "disabled") {
            return { ok: false, status: 409, json: async () => ({ detail: { error: "EXTENSION_DISABLED", message: "It is off." } }) };
        }
        if (reply.kind === "broken") {
            return { ok: false, status: 500, json: async () => ({ detail: "boom" }) };
        }
        return ok(reply.body);
    }
    if (url.endsWith("/enabled")) return ok({ ...bv, enabled: JSON.parse(init.body).enabled });
    if (url === "/api/agents/a1/extensions") { deskReads += 1; return ok(deskReply); }
    if (url === "/api/extensions/mail/agents/a1/config") {
        configCalls.push({ method: (init && init.method) || "GET", body: init && init.body ? JSON.parse(init.body) : null });
        if (!init || !init.method) return ok(storedConfig);
        return putReply();
    }
    throw new Error(`unexpected fetch ${url}`);
};

NAMES.slice(1).forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index + 1], "utf8")}\n;global.${name} = ${name};\n`);
});
const Status = global.BossModLiveStatus;
const bus = global.BossModBus.createBus(global.BossModBus.KNOWN_TOPICS);
Status.attach({ bus });
const push = (extensionId, agentId) => bus.publish("extension_live", { extension_id: extensionId, agent_id: agentId });

const drain = async () => { for (let i = 0; i < 8; i += 1) await new Promise((r) => setImmediate(r)); };
const text = () => document.body.textContent;
const byId = (id) => document.body.querySelector(`#${id}`);
const throwsError = (fn) => { try { fn(); return false; } catch (_err) { return true; } };
const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const verdict = {};

(async () => {
    // 1. The card has no Watch button, even enabled with a live view.
    global.BossModExtensionsDialog.open();
    await drain();
    verdict.cardHasNoWatchButton = Boolean(byId("ext-switch-browser-vision"))
        && !text().includes("Watch") && !byId("ext-watch-browser-vision");
    closeLayer(layers[layers.length - 1]);

    // 2. Nothing is read while no one is subscribed, whatever arrives.
    await drain();
    let listBefore = listCalls;
    push("browser-vision", "a1");
    push(null, null);
    bus.publish("resync", { downtimeMs: 0 });
    await drain();
    verdict.noFetchesWithoutSubscribers = liveCalls === 0 && listCalls === listBefore && timers.size === 0;

    // 3. The first subscriber reads everything once — the list, then each
    //    listed view; then time alone reads nothing.
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:00:00Z")] } };
    let notified = 0;
    const off = Status.subscribe(() => { notified += 1; });
    await drain();
    verdict.subscribingReadsAtOnce = listCalls === listBefore + 1 && liveCalls === 1 && notified === 1
        && sameJson(Status.views("a1"), [{ extensionId: "browser-vision", label: "Browser view", icon: "globe" }])
        && sameJson(Status.views("a2"), [])
        && Status.latest("browser-vision", "a1").title === "Example Domain"
        && Status.label("browser-vision") === "Browser view" && timers.size === 0;
    await advance(60000);
    verdict.noPollingOverSixtySeconds = liveCalls === 1 && timers.size === 0;

    // 4. Pushes: a listed extension's reads its /live once; null, an unlisted
    //    id (it may have just been turned on) and a resync read everything once.
    let before = liveCalls;
    listBefore = listCalls;
    push("browser-vision", "a1");
    await drain();
    verdict.anEventReadsExactlyOnce = liveCalls === before + 1 && listCalls === listBefore;
    before = liveCalls;
    listBefore = listCalls;
    push(null, null);
    await drain();
    verdict.aNullEventReadsEverythingOnce = liveCalls === before + 1 && listCalls === listBefore + 1;
    before = liveCalls;
    listBefore = listCalls;
    push("some-other-extension", "a1");
    await drain();
    verdict.anUnlistedEventReadsEverythingOnce = liveCalls === before + 1 && listCalls === listBefore + 1
        && !liveCallsBy["some-other-extension"];
    before = liveCalls;
    listBefore = listCalls;
    for (let i = 0; i < 5; i += 1) push("browser-vision", "a1");
    await drain();
    verdict.aBurstOfFiveReadsAtMostTwice = liveCalls - before >= 1 && liveCalls - before <= 2 && listCalls === listBefore;
    before = liveCalls;
    listBefore = listCalls;
    bus.publish("resync", { downtimeMs: 1200 });
    await drain();
    verdict.resyncReadsEverythingOnce = liveCalls === before + 1 && listCalls === listBefore + 1;

    // 5. Another failure keeps the last known state and logs; disabled empties it.
    liveReply = { kind: "broken" };
    push("browser-vision", "a1");
    await drain();
    verdict.failureKeepsLastStateAndLogs = Status.views("a1").length === 1
        && errors.some((line) => line.includes("[live-status]"));
    liveReply = { kind: "disabled" };
    push("browser-vision", null);
    await drain();
    verdict.disabledIsAnEmptySet = sameJson(Status.views("a1"), []) && Status.latest("browser-vision", "a1") === null;

    off();
    before = liveCalls;
    listBefore = listCalls;
    push("browser-vision", "a1");
    push(null, null);
    bus.publish("resync", { downtimeMs: 0 });
    await drain();
    await advance(60000);
    verdict.noSubscribersMeansNoFetches = liveCalls === before && listCalls === listBefore && timers.size === 0;

    // 6. A toggle in the dialog asks the status to re-read everything at once.
    const again = Status.subscribe(() => {});
    await drain();
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:00:00Z")] } };
    global.BossModExtensionsDialog.open();
    await drain();
    before = liveCalls;
    listBefore = listCalls;
    byId("ext-switch-browser-vision").click();  // turn off
    await drain();
    verdict.toggleRefreshesTheStatus = liveCalls === before + 1 && listCalls === listBefore + 1;
    closeLayer(layers[layers.length - 1]);
    again();

    // 6b. Two live-view extensions: both are read, in list order; an event
    //     re-reads only its own; a burst across both coalesces; a disabled
    //     or view-less extension is never read; turning one off drops it.
    const dormant = { ...vd, id: "dormant-view", name: "Dormant", enabled: false, live_view: { label: "Dormant view", icon: "monitor" } };
    listReply = [bv, vd, mailItem, dormant];
    otherLive["virtual-desktop"] = { kind: "ok", body: { items: [
        desktopItem("2026-09-28T10:00:00Z", "Mozilla Firefox"),
        { ...desktopItem("2026-09-28T10:00:00Z", "Terminal"), agent_id: "a2", agent_name: "Jim" },
    ] } };
    const callsOf = (id) => liveCallsBy[id] || 0;
    let bvBefore = callsOf("browser-vision");
    let vdBefore = callsOf("virtual-desktop");
    listBefore = listCalls;
    const both = Status.subscribe(() => {});
    await drain();
    verdict.twoViewsAreBothRead = listCalls === listBefore + 1
        && callsOf("browser-vision") === bvBefore + 1 && callsOf("virtual-desktop") === vdBefore + 1
        && !liveCallsBy.mail && !liveCallsBy["dormant-view"]
        && sameJson(Status.views("a1"), [
            { extensionId: "browser-vision", label: "Browser view", icon: "globe" },
            { extensionId: "virtual-desktop", label: "Desktop view", icon: "monitor" },
        ])
        && sameJson(Status.views("a2"), [{ extensionId: "virtual-desktop", label: "Desktop view", icon: "monitor" }])
        && Status.label("virtual-desktop") === "Desktop view"
        && Status.latest("virtual-desktop", "a1").title === "Mozilla Firefox"
        && Status.latest("browser-vision", "a2") === null;
    bvBefore = callsOf("browser-vision");
    vdBefore = callsOf("virtual-desktop");
    listBefore = listCalls;
    push("virtual-desktop", "a1");
    await drain();
    verdict.eachEventReadsItsOwnView = callsOf("virtual-desktop") === vdBefore + 1
        && callsOf("browser-vision") === bvBefore && listCalls === listBefore;
    bvBefore = callsOf("browser-vision");
    vdBefore = callsOf("virtual-desktop");
    for (let i = 0; i < 3; i += 1) {
        push("browser-vision", "a1");
        push("virtual-desktop", "a1");
    }
    await drain();
    const bvBurst = callsOf("browser-vision") - bvBefore;
    const vdBurst = callsOf("virtual-desktop") - vdBefore;
    verdict.aBurstAcrossTwoViewsCoalesces = bvBurst >= 1 && bvBurst <= 2 && vdBurst >= 1 && vdBurst <= 2
        && listCalls === listBefore;
    verdict.anUnlistedLabelThrows = throwsError(() => Status.label("mail"))
        && throwsError(() => Status.label("dormant-view"));
    listReply = [bv, { ...vd, enabled: false }, mailItem];
    push(null, null);
    await drain();
    verdict.aViewTurnedOffLeavesTheList = sameJson(Status.views("a1"), [{ extensionId: "browser-vision", label: "Browser view", icon: "globe" }])
        && sameJson(Status.views("a2"), []) && Status.latest("virtual-desktop", "a1") === null;

    // 6c. An icon outside the UI's fixed vocabulary is a server/UI contract
    //     break: logged naming the extension, and that view is left out (its
    //     /live is never read) rather than handed to the icon painter, which
    //     would throw while painting the chat header.
    listReply = [bv, { ...vd, live_view: { label: "Desktop view", icon: "rocket" } }];
    const errorsBefore = errors.length;
    vdBefore = callsOf("virtual-desktop");
    push(null, null);
    await drain();
    verdict.anUnknownIconIsLeftOutAndLogged = sameJson(Status.views("a1"), [{ extensionId: "browser-vision", label: "Browser view", icon: "globe" }])
        && sameJson(Status.views("a2"), [])
        && callsOf("virtual-desktop") === vdBefore
        && throwsError(() => Status.label("virtual-desktop"))
        && errors.slice(errorsBefore).some((line) => line.includes("[live-status]")
            && line.includes("virtual-desktop") && line.includes('"rocket"'));
    both();

    // 7. The single-agent viewer: real alt, the stat band, and it follows the status.
    listReply = [bv];
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:00:00Z")] } };
    const blobsBefore = blobFetches.length;
    global.BossModExtensionsLive.openForAgent("browser-vision", "a1", "Iris");
    await drain();
    const viewer = layers[layers.length - 1];
    const img = () => viewer.body.querySelector("#ext-live-image");
    verdict.viewerTitleAndHeadFocus = viewer.options.title === "Iris — Browser view"
        && viewer.options.focusBody === false && viewer.options.size === "panel";
    verdict.viewerImageHasRealAlt = Boolean(img())
        && img().getAttribute("alt") === "Latest screenshot from Iris: Example Domain"
        && img().getAttribute("src") === liveItem("2026-09-28T10:00:00Z").image_url.replace(/^/, "blob:");
    const band = viewer.body.querySelector(".ext-live-band");
    const scroller = viewer.body.querySelector(".ext-scroll");
    // Text a reader hears: the aria-hidden separators are left out.
    const spoken = (node) => (node.getAttribute && node.getAttribute("aria-hidden") === "true"
        ? "" : node.nodeType === 3 ? node.textContent : (node.childNodes || []).map(spoken).join(""));
    const identity = band && band.querySelector(".ext-live-page");
    const lines = band ? band.querySelectorAll(".ext-live-line") : [];
    const facts = band && band.querySelector(".ext-live-facts");
    const separators = facts ? facts.querySelectorAll(".ext-live-sep") : [];
    verdict.viewerShowsTheQuietBand = Boolean(band) && Boolean(scroller)
        && band.parentNode === scroller.parentNode
        && band.parentNode.children.indexOf(band) < band.parentNode.children.indexOf(scroller)
        && scroller.contains(img())
        && identity.textContent === "Example Domain (https://example.com)"
        && identity.querySelector(".ext-live-url").tagName === "A"
        && identity.querySelector(".ext-live-url").getAttribute("href") === "https://example.com"
        && band.querySelector("time").getAttribute("datetime") === "2026-09-28T10:00:00Z"
        && band.querySelector("time").textContent === global.BossModFormat.formatRelativeTime("2026-09-28T10:00:00Z")
        && lines[0].textContent === "command: bv open example.com"
        && JSON.stringify(facts.querySelectorAll(".ext-live-fact").map(spoken))
            === JSON.stringify(["resolution: 1280x800", "full page", "marks: 3"])
        && spoken(facts) === "resolution: 1280x800full pagemarks: 3"
        && separators.length === 2
        && separators.every((sep) => sep.getAttribute("aria-hidden") === "true" && sep.textContent === "|")
        && !band.querySelector("dl");

    // Same taken_at on the next read: no refetch.
    push("browser-vision", "a1");
    await drain();
    verdict.viewerDoesNotRefetchAnUnchangedShot = blobFetches.length === blobsBefore + 1;

    // A newer shot: refetched, and the old blob is revoked.
    const oldSrc = img().getAttribute("src");
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:05:00Z")] } };
    push("browser-vision", "a1");
    await drain();
    verdict.viewerUpdatesOnANewShotAndRevokesTheOld = blobFetches.length === blobsBefore + 2
        && img().getAttribute("src") !== oldSrc && revoked.includes(oldSrc);

    // The session ended while open (bv close, restart): the item disappears.
    liveReply = { kind: "ok", body: { items: [] } };
    push("browser-vision", "a1");
    await drain();
    verdict.viewerSaysSessionEnded = text().includes("Iris’s session ended.") && !img();

    closeLayer(viewer);
    before = liveCalls;
    push("browser-vision", "a1");
    await drain();
    verdict.viewerStopsReadingOnClose = liveCalls === before && timers.size === 0;

    // A blank title: no title element, no placeholder, an alt without a colon.
    const blankItem = { ...liveItem("2026-09-28T10:10:00Z"), title: "" };
    liveReply = { kind: "ok", body: { items: [blankItem] } };
    global.BossModExtensionsLive.openForAgent("browser-vision", "a1", "Iris");
    await drain();
    const blankViewer = layers[layers.length - 1];
    const blankBand = blankViewer.body.querySelector(".ext-live-band");
    verdict.viewerInventsNothingForABlankTitle = Boolean(blankBand)
        && blankBand.querySelector(".ext-live-page").textContent === blankItem.url
        && blankViewer.body.querySelector("#ext-live-image").getAttribute("alt") === "Latest screenshot from Iris"
        && spoken(blankBand) === [
            blankItem.url, global.BossModFormat.formatRelativeTime(blankItem.taken_at),
            "command: ", blankItem.command, "resolution: 1280x800", "full page", "marks: 3",
        ].join("");
    closeLayer(blankViewer);

    // A local file page: the URL is a link whose click opens its folder, once, and never navigates.
    const fileItem = { ...liveItem("2026-09-28T10:15:00Z"), url: "file:///tmp/x/y.png" };
    liveReply = { kind: "ok", body: { items: [fileItem] } };
    global.BossModExtensionsLive.openForAgent("browser-vision", "a1", "Iris");
    await drain();
    const fileViewer = layers[layers.length - 1];
    const fileUrl = fileViewer.body.querySelector(".ext-live-url");
    const opensBefore = folderOpens.length;
    const fileClick = { type: "click", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    if (fileUrl) fileUrl.dispatchEvent(fileClick);
    await drain();
    verdict.viewerFileUrlOpensItsFolder = Boolean(fileUrl) && fileUrl.tagName === "A"
        && fileUrl.getAttribute("href") === "file:///tmp/x/y.png" && fileUrl.textContent === "file:///tmp/x/y.png"
        && fileViewer.body.querySelectorAll(".ext-live-url").length === 1
        && !fileViewer.body.querySelector(".ext-live-band").querySelector("button")
        && fileClick.defaultPrevented === true
        && JSON.stringify(folderOpens.slice(opensBefore)) === JSON.stringify([
            { url: "/api/extensions/browser-vision/live/a1/open-folder", method: "POST" },
        ])
        && !fileViewer.body.querySelector(".market-failed");
    closeLayer(fileViewer);

    // Any other scheme is plain text: nothing to click.
    const blankPage = { ...liveItem("2026-09-28T10:20:00Z"), url: "about:blank" };
    liveReply = { kind: "ok", body: { items: [blankPage] } };
    global.BossModExtensionsLive.openForAgent("browser-vision", "a1", "Iris");
    await drain();
    const inertViewer = layers[layers.length - 1];
    const inertBand = inertViewer.body.querySelector(".ext-live-band");
    verdict.viewerOtherSchemesAreInert = Boolean(inertBand)
        && Boolean(inertBand.querySelector(".ext-live-url"))
        && inertBand.querySelector(".ext-live-url").tagName === "SPAN"
        && inertBand.querySelector(".ext-live-url").textContent === "about:blank"
        && !inertBand.querySelector("a") && !inertBand.querySelector("button");
    closeLayer(inertViewer);

    // 7b. A desktop's view: titled with that extension's own label; with no
    //     URL the page line is the window title alone, and with neither a
    //     title nor a URL there is no page line at all — nothing is invented.
    //     An extension the status never listed opens nothing.
    listReply = [bv, vd];
    const deskShot = desktopItem("2026-09-28T11:00:00Z", "Mozilla Firefox");
    otherLive["virtual-desktop"] = { kind: "ok", body: { items: [deskShot] } };
    const headerListener = Status.subscribe(() => {});
    await drain();
    global.BossModExtensionsLive.openForAgent("virtual-desktop", "a1", "Iris");
    await drain();
    const deskViewer = layers[layers.length - 1];
    const deskBand = deskViewer.body.querySelector(".ext-live-band");
    const deskImage = deskViewer.body.querySelector("#ext-live-image");
    verdict.viewerTitleFromTheLabel = deskViewer.options.title === "Iris — Desktop view";
    verdict.viewerDesktopBandShowsTheTitleAlone = Boolean(deskBand) && Boolean(deskImage)
        && deskBand.querySelector(".ext-live-page").textContent === "Mozilla Firefox"
        && !deskBand.querySelector(".ext-live-url")
        && deskBand.querySelector("time").getAttribute("datetime") === deskShot.taken_at
        && deskBand.querySelectorAll(".ext-live-line")[0].textContent === "command: vm view"
        && deskImage.getAttribute("alt") === "Latest screenshot from Iris: Mozilla Firefox"
        && deskImage.getAttribute("src") === `blob:${deskShot.image_url}`;
    closeLayer(deskViewer);
    const bareShot = desktopItem("2026-09-28T11:05:00Z", "");
    otherLive["virtual-desktop"] = { kind: "ok", body: { items: [bareShot] } };
    push("virtual-desktop", "a1");
    await drain();
    global.BossModExtensionsLive.openForAgent("virtual-desktop", "a1", "Iris");
    await drain();
    const bareViewer = layers[layers.length - 1];
    const bareBand = bareViewer.body.querySelector(".ext-live-band");
    verdict.viewerNoPageLineWithNeitherTitleNorUrl = Boolean(bareBand)
        && !bareBand.querySelector(".ext-live-page") && !bareBand.querySelector(".ext-live-url")
        && Boolean(bareBand.querySelector("time"))
        && bareViewer.body.querySelector("#ext-live-image").getAttribute("alt") === "Latest screenshot from Iris"
        && spoken(bareBand) === [
            global.BossModFormat.formatRelativeTime(bareShot.taken_at),
            "command: ", bareShot.command, "screen: 1280x800", "pointer: (412, 488) px",
        ].join("");
    closeLayer(bareViewer);
    const layersBeforeUnlisted = layers.length;
    verdict.viewerRefusesAnUnlistedExtension = throwsError(() => global.BossModExtensionsLive.openForAgent("mail", "a1", "Iris"))
        && layers.length === layersBeforeUnlisted;
    headerListener();

    // 8. The data table wrapper: argument checks, the Tabulator options it
    //    passes, loadPage paging, text-only cells, the activator and states.
    const DataTable = global.BossModDataTable;
    const throws = (fn) => { try { fn(); return false; } catch (_err) { return true; } };
    const baseOptions = {
        caption: "Inbox of reports@contoso.com",
        columns: [{ key: "subject", label: "Subject" }, { key: "from", label: "From" }],
        rowLabel: (row) => `Open ${row.cells.subject}`,
        onActivate: () => {},
        loadPage: async () => ({ rows: [], has_more: false }),
        pageSize: 25,
        emptyText: "Nothing here yet.",
    };
    verdict.dataTableRejectsBadOptions = ["caption", "columns", "rowLabel", "onActivate", "loadPage", "pageSize", "emptyText"]
        .every((key) => throws(() => DataTable.create({ ...baseOptions, [key]: undefined, tableFactory: () => ({ on() {} }) })))
        && throws(() => DataTable.create({ ...baseOptions, pageSize: 0, tableFactory: () => ({ on() {} }) }));

    let made = null;
    const handlers = {};
    let destroyedTable = false;
    let setDataCalls = 0;
    const activated = [];
    const pageCalls = [];
    let pageReply = { rows: [{ id: "m1", cells: { subject: "<b>Hi</b>", from: "Alice" }, emphasis: true }], has_more: true };
    const table = DataTable.create({
        ...baseOptions,
        onActivate: (row) => activated.push(row.id),
        loadPage: async (args) => {
            pageCalls.push(args);
            if (pageReply instanceof Error) throw pageReply;
            return pageReply;
        },
        tableFactory: (el, opts) => {
            made = { el, opts };
            return {
                on: (name, fn) => { handlers[name] = fn; },
                setData: () => { setDataCalls += 1; return Promise.resolve(); },
                destroy: () => { destroyedTable = true; },
            };
        },
    });
    document.body.append(table.element);
    const opts = made.opts;
    verdict.dataTablePassesRemotePaging = opts.pagination === true && opts.paginationMode === "remote"
        && opts.paginationSize === 25 && opts.layout === "fitColumns" && opts.placeholder === "Nothing here yet."
        && typeof opts.ajaxRequestFunc === "function" && Boolean(opts.ajaxURL) && opts.dataLoader === false
        && opts.columns.every((column) => column.headerSort === false);
    verdict.dataTableNamesTheGridByCaption = made.el.getAttribute("aria-label") === "Inbox of reports@contoso.com";

    const pending = opts.ajaxRequestFunc("x", {}, { page: 3, size: 25 });
    verdict.dataTableShowsLoading = table.element.textContent.includes("Loading…");
    const answer = await pending;
    verdict.dataTableMapsPagesToSkipTop = pageCalls[0].skip === 50 && pageCalls[0].top === 25
        && answer.last_page === 4 && answer.data.length === 1 && !table.element.textContent.includes("Loading…");
    pageReply = { rows: [], has_more: false };
    verdict.dataTableLastPageWithoutMore = (await opts.ajaxRequestFunc("x", {}, { page: 1, size: 25 })).last_page === 1;

    const rowData = { id: "m1", cells: { subject: "<b>Hi</b>", from: "<i>Alice</i>" }, emphasis: true };
    const fakeCell = { getRow: () => ({ getData: () => rowData }) };
    const activator = opts.columns[0].formatter(fakeCell);
    const plain = opts.columns[1].formatter(fakeCell);
    verdict.dataTableFirstCellIsAButton = activator.tagName === "BUTTON" && activator.getAttribute("type") === "button"
        && activator.getAttribute("aria-label") === "Unread: Open <b>Hi</b>"
        && Boolean(activator.querySelector(".data-table-dot"));
    verdict.dataTableCellsAreText = plain.nodeType === 3 && plain.textContent === "<i>Alice</i>"
        && opts.columns[0].titleFormatter().nodeType === 3;
    handlers.rowClick({}, { getData: () => rowData });
    verdict.dataTableRowClickActivates = activated.join(",") === "m1";

    pageReply = new Error("MAILBOX_ACCESS_DENIED: Access is denied.");
    let rejected = false;
    await opts.ajaxRequestFunc("x", {}, { page: 1, size: 25 }).catch(() => { rejected = true; });
    const alertEl = table.element.querySelector(".data-table-error");
    verdict.dataTableShowsTheError = rejected && alertEl && !alertEl.hidden
        && alertEl.textContent.includes("MAILBOX_ACCESS_DENIED: Access is denied.");
    alertEl.querySelector("button").click();
    await drain();
    table.destroy();
    verdict.dataTableRetryReloadsAndDestroyTearsDown = setDataCalls === 1 && destroyedTable;

    // 9. The desk section: hidden with no per-agent extensions, a row when one is enabled.
    let changes = 0;
    deskReply = [];
    const emptyDesk = global.BossModDeskExtensions.createDeskExtensions({ agentId: "a1", agentName: () => "Iris", onChange: () => { changes += 1; } });
    const emptyWhileLoading = emptyDesk.isEmpty();
    await drain();
    verdict.deskSectionHiddenWithNone = emptyWhileLoading && emptyDesk.isEmpty() && changes >= 2
        && emptyDesk.element.textContent === "";
    emptyDesk.destroy();
    deskReply = [{ id: "mail", name: "Microsoft 365 Mailbox", config_label: "Microsoft 365 mailbox", view_label: "Open inbox", configured: false, summary: null, wakes: true, wake: null }];
    const desk = global.BossModDeskExtensions.createDeskExtensions({ agentId: "a1", agentName: () => "Iris", onChange: () => {} });
    await drain();
    verdict.deskSectionShowsNotSetUp = !desk.isEmpty() && desk.element.textContent.includes("Not set up")
        && Boolean(desk.element.querySelector("#desk-ext-config-mail")) && !desk.element.querySelector("#desk-ext-view-mail")
        && !desk.element.querySelector(".desk-ext-wake");

    // 10. The settings dialog: a blank secret is sent as "", the server's error is shown.
    desk.element.querySelector("#desk-ext-config-mail").click();
    await drain();
    const dialog = layers[layers.length - 1];
    const secretInput = dialog.body.querySelector("#ext-config-client_secret");
    const numberInput = dialog.body.querySelector("#ext-config-check_interval_seconds");
    verdict.configDialogRendersTheNumberField = Boolean(numberInput) && numberInput.getAttribute("type") === "number"
        && numberInput.getAttribute("min") === "15" && numberInput.getAttribute("max") === "3600"
        && numberInput.getAttribute("placeholder") === "90" && numberInput.value === "90"
        && dialog.body.querySelector('label[for="ext-config-check_interval_seconds"]').textContent === "Check for new mail every (seconds)";
    verdict.configDialogMasksTheSecret = dialog.options.title === "Settings for Iris" && Boolean(secretInput)
        && secretInput.value === "" && secretInput.getAttribute("placeholder") === "Leave blank to keep the current secret"
        && dialog.body.querySelector("#ext-config-mailbox").getAttribute("type") === "email"
        && dialog.body.textContent.includes("Step one.") && dialog.body.textContent.includes("Step two.");
    dialog.body.querySelector("#ext-config-mailbox").value = "reports@contoso.com";
    await dialog.body.querySelector("#ext-config-save").dispatchClick();
    await drain();
    const put = configCalls.find((call) => call.method === "PUT");
    const alertBox = dialog.body.querySelector('[role="alert"]');
    verdict.configDialogSendsABlankSecret = Boolean(put) && put.body.values.client_secret === ""
        && put.body.values.mailbox === "reports@contoso.com" && put.body.values.tenant_id === "t-1";
    verdict.configDialogShowsTheServerError = Boolean(alertBox) && !alertBox.hidden
        && alertBox.textContent.includes("The client secret is wrong or has expired. Create a new secret and try again.")
        && layers.includes(dialog) && !dialog.body.querySelector("#ext-config-save").disabled;
    putReply = () => ({ ok: true, status: 200, json: async () => ({ ...storedConfig, verified: "Connected to reports@contoso.com" }) });
    await dialog.body.querySelector("#ext-config-save").dispatchClick();
    await drain();
    verdict.configDialogShowsVerified = dialog.body.textContent.includes("Connected to reports@contoso.com")
        && Boolean(dialog.body.querySelector("#ext-config-done"));
    const sentInterval = configCalls.filter((call) => call.method === "PUT").pop().body.values.check_interval_seconds;
    verdict.configDialogSendsTheNumber = sentInterval === "90";
    const readsBeforeClose = deskReads;
    closeLayer(dialog);
    await drain();
    verdict.deskRereadsWhenTheDialogCloses = deskReads === readsBeforeClose + 1;
    desk.element.querySelector("#desk-ext-config-mail").click();
    await drain();
    const reopened = layers[layers.length - 1];
    desk.destroy();
    verdict.deskDestroyClosesItsDialogs = !layers.includes(reopened);

    // 11. The desk's wake line: waiting, last checked (+ new), and can't check.
    // Local 12-hour time, the operator's clock everywhere on screen.
    const hhmm = (iso) => { const d = new Date(iso); const hour = d.getHours(); return `${hour % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")} ${hour < 12 ? "AM" : "PM"}`; };
    const configured = { id: "mail", name: "Microsoft 365 Mailbox", config_label: "Microsoft 365 mailbox", view_label: "Open inbox", configured: true, summary: "reports@contoso.com", wakes: true };
    const deskWith = async (wake) => {
        deskReply = [{ ...configured, wake }];
        const d = global.BossModDeskExtensions.createDeskExtensions({ agentId: "a1", agentName: () => "Iris", onChange: () => {} });
        await drain();
        const line = d.element.querySelector(".desk-ext-wake");
        const out = { text: line ? line.textContent : null, tone: line ? line.getAttribute("data-tone") : null, role: line ? line.getAttribute("role") : null, all: d.element.textContent };
        d.destroy();
        return out;
    };
    const waiting = await deskWith(null);
    verdict.deskWakeWaiting = waiting.text === "Waiting for first check" && waiting.role === "status" && waiting.all.includes("reports@contoso.com");
    const checked = await deskWith({ checked_at: "2026-09-29T13:42:10+00:00", ok: true, error: null, last_new_at: "2026-09-29T13:41:00+00:00", last_new_count: 2 });
    verdict.deskWakeLastChecked = checked.text === `Last checked ${hhmm("2026-09-29T13:42:10+00:00")} · 2 new at ${hhmm("2026-09-29T13:41:00+00:00")}`
        && checked.tone === null;
    const quiet = await deskWith({ checked_at: "2026-09-29T13:42:10+00:00", ok: true, error: null, last_new_at: null, last_new_count: null });
    verdict.deskWakeLastCheckedNothingNew = quiet.text === `Last checked ${hhmm("2026-09-29T13:42:10+00:00")}`;
    const failed = await deskWith({ checked_at: "2026-09-29T13:42:10+00:00", ok: false, error: "The client secret is wrong or has expired. Create a new secret and try again.", last_new_at: null, last_new_count: null });
    verdict.deskWakeCantCheck = failed.text === "Can’t check Microsoft 365 mailbox: The client secret is wrong or has expired. Create a new secret and try again."
        && failed.tone === "alert" && failed.role === "status";
    deskReply = [{ ...configured, wakes: false, wake: null }];
    const nonWake = global.BossModDeskExtensions.createDeskExtensions({ agentId: "a1", agentName: () => "Iris", onChange: () => {} });
    await drain();
    verdict.deskNoWakeLineForNonWake = !nonWake.element.querySelector(".desk-ext-wake");
    nonWake.destroy();

    // 12. The view dialog: Inbox | Sent tabs, one table per tab, built when first shown.
    listReply = [bv, mailItem];
    global.BossModAgentViewDialog.open({ extensionId: "mail", agentId: "a1", title: "Iris — Open inbox" });
    await drain();
    const viewer2 = layers[layers.length - 1];
    const tabInbox = viewer2.body.querySelector("#agent-view-tab-inbox");
    const tabSent = viewer2.body.querySelector("#agent-view-tab-sent");
    const panelInbox = viewer2.body.querySelector("#agent-view-panel-inbox");
    const panelSent = viewer2.body.querySelector("#agent-view-panel-sent");
    verdict.viewTabsRendered = Boolean(tabInbox && tabSent) && tabInbox.getAttribute("role") === "tab"
        && tabInbox.getAttribute("aria-selected") === "true" && tabSent.getAttribute("aria-selected") === "false"
        && tabInbox.getAttribute("aria-controls") === "agent-view-panel-inbox"
        && panelInbox.getAttribute("role") === "tabpanel" && panelInbox.getAttribute("aria-labelledby") === "agent-view-tab-inbox"
        && !panelInbox.hidden && panelSent.hidden;
    verdict.viewOnlyTheShownTabIsRead = viewCalls.length === 1 && viewCalls[0].view === "inbox" && tabulators.length === 1
        && tabulators[0].el.getAttribute("aria-label") === "Inbox of reports@contoso.com";
    tabSent.click();
    await drain();
    verdict.viewSentTabBuildsItsOwnTable = !panelSent.hidden && panelInbox.hidden && tabSent.getAttribute("aria-selected") === "true"
        && viewCalls.filter((call) => call.view === "sent" && call.skip === 0).length === 1 && tabulators.length === 2
        && tabulators[1].el.getAttribute("aria-label") === "Sent from reports@contoso.com";
    tabInbox.click();
    await drain();
    verdict.viewSwitchingBackReadsNothingNew = tabulators.length === 2 && viewCalls.filter((call) => call.view === "inbox" && !call.item).length === 1;
    tabSent.click();
    await drain();
    tabulators[1].handlers.rowClick({}, { getData: () => ({ id: "sent-1", cells: { subject: "sent subject" } }) });
    await drain();
    verdict.viewItemOpensWithItsView = viewCalls.some((call) => call.item === "sent-1" && call.view === "sent")
        && layers[layers.length - 1].body.textContent.includes("jordan@contoso.com");
    closeLayer(layers[layers.length - 1]);
    closeLayer(viewer2);
    verdict.viewCloseDestroysEveryTable = tabulators.every((t) => t.destroyed);

    listReply = [bv, { ...mailItem, agent_view: { label: "Open inbox", views: [{ key: "inbox", label: "Inbox" }] } }];
    global.BossModAgentViewDialog.open({ extensionId: "mail", agentId: "a1", title: "Iris — Open inbox" });
    await drain();
    const single = layers[layers.length - 1];
    verdict.viewOneListHasNoTabRow = !single.body.querySelector('[role="tablist"]') && !single.body.querySelector('[role="tabpanel"]')
        && tabulators.length === 3;
    closeLayer(single);

    // 13. Configure agents: a button only on an enabled card with per-agent
    //     settings, right under its switch; the "set up per agent" note it
    //     replaced is gone.
    listReply = [bv, vd, mailItem, { ...mailItem, id: "mail-off", name: "Mail off", enabled: false }];
    global.BossModExtensionsDialog.open();
    await drain();
    const extDialog = layers[layers.length - 1];
    const agentsButton = byId("ext-agents-virtual-desktop");
    verdict.configureAgentsOnlyWhenOnWithAgentConfig = Boolean(agentsButton) && Boolean(byId("ext-agents-mail"))
        && !byId("ext-agents-browser-vision") && !byId("ext-agents-mail-off")
        && agentsButton.textContent === "Configure agents"
        && agentsButton.getAttribute("class") === "btn btn-sm ext-card-agents"
        && agentsButton.parentNode.children.indexOf(agentsButton)
            === agentsButton.parentNode.children.indexOf(byId("ext-switch-virtual-desktop")) + 1
        && !extDialog.body.textContent.includes("Set up per agent");

    // 14. The agents layer: loading, a failed read with Try again, then the table.
    agentsReply = "broken";
    agentsButton.click();
    const agentsLayer = layers[layers.length - 1];
    const statusLine = () => agentsLayer.body.querySelector('[role="status"]');
    verdict.agentsLayerShowsLoading = agentsLayer.options.title === "Virtual Desktop — agents"
        && agentsLayer.options.size === "panel" && Boolean(statusLine()) && statusLine().textContent === "Loading agents…";
    await drain();
    const failedLine = agentsLayer.body.querySelector('[role="alert"]');
    verdict.agentsLayerFailedOffersRetry = Boolean(failedLine) && failedLine.textContent === "The agents could not be read."
        && Boolean(agentsLayer.body.querySelector("#ext-agents-retry")) && !agentsLayer.body.querySelector("table");
    agentsReply = "ok";
    const readsBeforeRetry = agentsReads;
    agentsLayer.body.querySelector("#ext-agents-retry").click();
    const reloading = Boolean(statusLine()) && statusLine().textContent === "Loading agents…";
    await drain();
    verdict.agentsLayerRetryLoadsTheTable = reloading && agentsReads === readsBeforeRetry + 1
        && Boolean(agentsLayer.body.querySelector("table")) && !agentsLayer.body.querySelector("#ext-agents-retry");

    // 15. The table: a caption, column heads, one row per agent headed by its
    //     face and name; the switch is named for the extension and the agent.
    const rowOf = (layer, agentId) => layer.body.querySelector(`#ext-agents-name-${agentId}`).closest("tr");
    const switchOf = (layer, agentId) => rowOf(layer, agentId).querySelector('[role="switch"]');
    const settingsOf = (layer, agentId) => rowOf(layer, agentId).querySelector(".btn");
    const statusOf = (layer, agentId) => rowOf(layer, agentId).children[1].textContent;
    const tableEl = agentsLayer.body.querySelector("table");
    const caption = tableEl.querySelector("caption");
    const heads = tableEl.querySelectorAll("th").filter((th) => th.getAttribute("scope") === "col");
    const ids = ["a1", "a2", "a3", "a4"];
    verdict.agentsTableIsSemantic = Boolean(caption) && caption.getAttribute("class") === "visually-hidden"
        && caption.textContent === "Agents and whether Virtual Desktop is on for each"
        && sameJson(heads.map((th) => th.textContent), ["Agent", "Status", "On", "Settings"])
        && heads[3].children[0].getAttribute("class") === "visually-hidden"
        && ids.every((id) => rowOf(agentsLayer, id).children[0].tagName === "TH"
            && rowOf(agentsLayer, id).children[0].getAttribute("scope") === "row")
        && sameJson(agentsLayer.body.querySelectorAll(".field-hint").map((para) => para.textContent),
            ["Gives this agent its own Linux desktop.", "Memory and CPUs are upper limits."]);
    const jimLabel = switchOf(agentsLayer, "a2").querySelector(".switch-label");
    verdict.agentsRowsShowEachAgent = statusOf(agentsLayer, "a1") === "On" && statusOf(agentsLayer, "a2") === "Off"
        && statusOf(agentsLayer, "a4") === "On"
        && Boolean(rowOf(agentsLayer, "a1").querySelector(".avatar"))
        && agentsLayer.body.querySelector("#ext-agents-name-a1").textContent === "Iris"
        && settingsOf(agentsLayer, "a1").textContent === "Settings"
        && settingsOf(agentsLayer, "a1").getAttribute("aria-describedby") === "ext-agents-name-a1"
        && Boolean(settingsOf(agentsLayer, "a4")) && !settingsOf(agentsLayer, "a2") && !settingsOf(agentsLayer, "a3")
        && switchOf(agentsLayer, "a1").getAttribute("aria-checked") === "true"
        && switchOf(agentsLayer, "a2").getAttribute("aria-checked") === "false"
        && jimLabel.textContent === "Virtual Desktop for Jim" && jimLabel.classList.contains("visually-hidden")
        && !switchOf(agentsLayer, "a2").disabled;
    verdict.agentsImageIncapableOffRowIsDisabled = switchOf(agentsLayer, "a3").disabled
        && switchOf(agentsLayer, "a3").getAttribute("aria-describedby") === "ext-agents-note-a3"
        && agentsLayer.body.querySelector("#ext-agents-note-a3").textContent === "Its model can’t see images"
        && statusOf(agentsLayer, "a3") === "Off · Its model can’t see images"
        // Already on: still switchable, so it can be turned off.
        && !switchOf(agentsLayer, "a4").disabled && !agentsLayer.body.querySelector("#ext-agents-note-a4");

    // 16. Quick enable: the switch saves `{}` (the defaults) at once, holding
    //     "on" and disabled while it runs; the table is re-read and focus is
    //     back on that row's switch.
    const callsBeforeOn = layerCalls.length;
    const readsBeforeOn = agentsReads;
    switchOf(agentsLayer, "a2").click();
    const pendingSwitch = switchOf(agentsLayer, "a2");
    const heldWhilePending = pendingSwitch.getAttribute("aria-checked") === "true" && pendingSwitch.disabled
        && document.activeElement === rowOf(agentsLayer, "a2").children[0];
    await drain();
    verdict.agentsQuickEnableSavesDefaults = heldWhilePending
        && sameJson(layerCalls.slice(callsBeforeOn), [{ method: "PUT", agent: "a2", body: { values: {} } }])
        && agentsReads === readsBeforeOn + 1
        && statusOf(agentsLayer, "a2") === "On" && switchOf(agentsLayer, "a2").getAttribute("aria-checked") === "true"
        && Boolean(settingsOf(agentsLayer, "a2"))
        && document.activeElement === switchOf(agentsLayer, "a2");

    // 17. Turning off asks first; Cancel keeps it; Turn off removes the settings.
    const callsBeforeOff = layerCalls.length;
    switchOf(agentsLayer, "a1").click();
    const confirmOff = layers[layers.length - 1];
    verdict.agentsTurnOffAsksFirst = confirmOff !== agentsLayer
        && confirmOff.options.title === "Turn off Virtual Desktop for Iris?"
        && confirmOff.options.body === "Their settings for it are removed."
        && sameJson(confirmOff.options.actions.map((action) => [action.label, action.tone]),
            [["Turn off", "danger"], ["Cancel", "quiet"]])
        && switchOf(agentsLayer, "a1").getAttribute("aria-checked") === "true"
        && layerCalls.length === callsBeforeOff;
    confirmOff.body.querySelectorAll("button")[1].click();
    await drain();
    verdict.agentsTurnOffCancelKeepsIt = !layers.includes(confirmOff) && layerCalls.length === callsBeforeOff
        && switchOf(agentsLayer, "a1").getAttribute("aria-checked") === "true";
    switchOf(agentsLayer, "a1").click();
    const confirmAgain = layers[layers.length - 1];
    confirmAgain.body.querySelector("#ext-agents-off-confirm").click();
    await drain();
    verdict.agentsTurnOffRemovesTheSettings = !layers.includes(confirmAgain)
        && sameJson(layerCalls.slice(callsBeforeOff), [{ method: "DELETE", agent: "a1", body: null }])
        && statusOf(agentsLayer, "a1") === "Off" && switchOf(agentsLayer, "a1").getAttribute("aria-checked") === "false"
        && !settingsOf(agentsLayer, "a1")
        && document.activeElement === switchOf(agentsLayer, "a1");

    // 18. A refusal is shown under its row and the switch goes back; nothing
    //     changed, so nothing is re-read. The next try clears it.
    configReply = { error: "CONFIG_VERIFY_FAILED", message: "Docker isn’t running or BossMod can’t reach it." };
    const readsBeforeRefusal = agentsReads;
    switchOf(agentsLayer, "a1").click();
    await drain();
    const errorRows = agentsLayer.body.querySelectorAll(".ext-agents-error-row");
    const irisRow = rowOf(agentsLayer, "a1");
    const refusal = errorRows.length ? errorRows[0].querySelector('[role="alert"]') : null;
    verdict.agentsRowErrorRevertsTheSwitch = errorRows.length === 1
        && irisRow.parentNode.children.indexOf(errorRows[0]) === irisRow.parentNode.children.indexOf(irisRow) + 1
        && errorRows[0].children[0].getAttribute("colspan") === "4"
        && Boolean(refusal) && refusal.textContent === "Docker isn’t running or BossMod can’t reach it."
        && switchOf(agentsLayer, "a1").getAttribute("aria-checked") === "false" && !switchOf(agentsLayer, "a1").disabled
        && statusOf(agentsLayer, "a1") === "Off" && agentsReads === readsBeforeRefusal
        && document.activeElement === switchOf(agentsLayer, "a1");
    configReply = null;
    switchOf(agentsLayer, "a1").click();
    await drain();
    verdict.agentsRowErrorClearsOnTheNextTry = agentsLayer.body.querySelectorAll(".ext-agents-error-row").length === 0
        && statusOf(agentsLayer, "a1") === "On";

    // 19. Settings opens the settings dialog as a layer; closing it re-reads
    //     the table and puts focus back on that row's Settings.
    const readsBeforeSettings = agentsReads;
    settingsOf(agentsLayer, "a4").click();
    await drain();
    const settingsDialog = layers[layers.length - 1];
    verdict.agentsSettingsOpensTheDialog = settingsDialog.options.title === "Settings for Laura"
        && layerCalls.some((call) => call.method === "GET" && call.agent === "a4");
    closeLayer(settingsDialog);
    await drain();
    verdict.agentsSettingsCloseRereadsAndRefocuses = agentsReads === readsBeforeSettings + 1
        && document.activeElement === settingsOf(agentsLayer, "a4");
    closeLayer(agentsLayer);

    // 20. No agents: the help, then a plain line saying so.
    const everyone = layerAgents["virtual-desktop"].agents;
    layerAgents["virtual-desktop"].agents = [];
    byId("ext-agents-virtual-desktop").click();
    await drain();
    const emptyLayer = layers[layers.length - 1];
    verdict.agentsEmptySaysSo = !emptyLayer.body.querySelector("table")
        && emptyLayer.body.querySelector('[role="status"]').textContent === "No agents yet."
        && emptyLayer.body.textContent.includes("Gives this agent its own Linux desktop.");
    closeLayer(emptyLayer);
    layerAgents["virtual-desktop"].agents = everyone;

    // 21. An extension with a required field: the switch opens the settings
    //     dialog instead of saving, and stays off under it; closing re-reads
    //     and focus returns to the switch. A stored summary shows in Status.
    byId("ext-agents-mail").click();
    await drain();
    const mailLayer = layers[layers.length - 1];
    verdict.agentsSummaryShowsWhenOn = statusOf(mailLayer, "a2") === "On · jim@contoso.com"
        && !switchOf(mailLayer, "a1").disabled;
    const configCallsBefore = configCalls.length;
    const mailReadsBefore = agentsReads;
    switchOf(mailLayer, "a1").click();
    await drain();
    const mailDialog = layers[layers.length - 1];
    verdict.agentsSwitchOpensSettingsWhenAFieldIsRequired = mailDialog !== mailLayer
        && mailDialog.options.title === "Settings for Iris"
        && sameJson(configCalls.slice(configCallsBefore).map((call) => call.method), ["GET"])
        && switchOf(mailLayer, "a1").getAttribute("aria-checked") === "false";
    closeLayer(mailDialog);
    await drain();
    verdict.agentsDialogCloseRereadsAndRefocusesTheSwitch = agentsReads === mailReadsBefore + 1
        && document.activeElement === switchOf(mailLayer, "a1");
    closeLayer(mailLayer);
    closeLayer(extDialog);

    listReply = [{ ...bv, id: 'chatgpt', name: 'ChatGPT', enabled: true, model_provider: { connection_name: 'Chat GPT' },
        requires_image_model: false, setup: { state: 'not_required' }, description: 'Commercial use requires permission from JtechMinds LLC.' }];
    global.BossModExtensionsDialog.open();
    await drain();
    const chatLayer = layers[layers.length - 1];
    const buttonWith = (label) => [...chatLayer.body.querySelectorAll('button')].find(b => b.textContent === label);
    verdict.chatgptSignInInExtension = Boolean(buttonWith('Continue with ChatGPT')) && text().includes('Commercial use requires permission');
    buttonWith('Continue with ChatGPT').click();
    await drain();
    verdict.chatgptSignInStartsAndPolls = chatgptCalls.some(call => call.url.endsWith('/provider/actions') && call.body.action === 'login')
        && text().includes('Finish signing in in your browser') && Boolean(buttonWith('Cancel sign-in'));
    chatgptReply = { connected: true, pending: false, error: null,
        message: 'Connected as new@example.com. Choose Chat GPT when creating or editing an agent.',
        actions: [{ key: 'logout', label: 'Sign out', values: {} }],
        choices: [{ label: 'ChatGPT model', value: 'model', options: [{ value: 'model', label: 'Model' }], action: 'model', parameter: 'model' }] };
    await advance(2000);
    verdict.chatgptCompletionShowsConnection = text().includes('Choose Chat GPT when creating or editing an agent.')
        && Boolean(buttonWith('Sign out')) && [...chatLayer.body.querySelectorAll('button')].some(b => b.getAttribute('aria-label') === 'ChatGPT model');
    const chatgptReadsBeforeClose = chatgptCalls.length;
    closeLayer(chatLayer);
    await advance(10000);
    verdict.chatgptCloseStopsPolling = chatgptCalls.length === chatgptReadsBeforeClose;

    listReply = [{ ...bv, id: 'demo-provider', name: 'Demo provider', enabled: true,
        requires_image_model: false, setup: { state: 'not_required' }, model_provider: { connection_name: 'Demo' } }];
    global.BossModExtensionsDialog.open();
    await drain();
    const demoLayer = layers[layers.length - 1];
    const startDemo = [...demoLayer.body.querySelectorAll('button')].find(b => b.textContent === 'Start demo');
    startDemo.click();
    await drain();
    verdict.providerControlsHandleUnrelatedExtension = demoConnected && demoLayer.body.textContent.includes('Demo ready');
    closeLayer(demoLayer);

    // 7. A panel extension's card: status, facts, actions and the Settings button.
    const clipboardWrites = [];
    Object.defineProperty(global, 'navigator', {
        configurable: true, value: { clipboard: { writeText: async (value) => { clipboardWrites.push(value); } } },
    });
    listReply = [networkItem()];
    global.BossModExtensionsDialog.open();
    await drain();
    let netLayer = layers[layers.length - 1];
    const netButton = (label) => [...netLayer.body.querySelectorAll('button')].find(b => b.textContent === label);
    const factInput = (label) => netLayer.body.querySelector(`#ext-panel-network-fact-${label}`);
    const codeField = factInput(1);
    verdict.panelShowsMessageFactsAndAction = text().includes('Use this only on a network you trust')
        && factInput(0).textContent === 'http://192.168.1.20:38472' && codeField.value === 'ABCD2345'
        && !factInput(0).classList.contains('bm-secret-masked') && codeField.classList.contains('bm-secret-masked')
        && codeField.getAttribute('readonly') !== null && factInput(0).getAttribute('role') === 'textbox'
        && netLayer.body.querySelector('label[for="ext-panel-network-fact-1"]').textContent === 'Access code'
        && Boolean(netButton('Generate new code')) && [...netLayer.body.querySelectorAll('button')].filter(b => b.classList.contains('btn')).length >= 5;
    verdict.panelHasNoCommandLineAndShowsTheServiceSwitch = !netLayer.body.querySelector('.market-card-specialty')
        && Boolean(byId('ext-switch-network')) && Boolean(byId('ext-card-settings-network'));
    verdict.panelMasksTheSecretUntilShown = netButton('Show').getAttribute('aria-pressed') === 'false';
    netButton('Show').click();
    verdict.panelShowRevealsTheSecret = !codeField.classList.contains('bm-secret-masked')
        && Boolean(netButton('Hide')) && netButton('Hide').getAttribute('aria-pressed') === 'true';
    const copyButtons = [...netLayer.body.querySelectorAll('button')].filter(b => b.textContent === 'Copy');
    copyButtons[1].click();
    await drain();
    verdict.panelCopyWritesTheValue = copyButtons.length === 2 && clipboardWrites.join('|') === 'ABCD2345'
        && copyButtons[1].textContent === 'Copied' && copyButtons[1].getAttribute('aria-label') === 'Copy access code';
    await advance(2000);
    verdict.panelCopyFeedbackFadesBack = copyButtons[1].textContent === 'Copy';

    // An action posts, then the refreshed panel replaces the old one.
    networkCode = 'ZZZZ9999';
    networkPanel = (body) => ({ ok: true, body: networkStatus(networkCode) });
    netButton('Generate new code').click();
    await drain();
    const post = networkCalls.filter(call => call.url.endsWith('/panel/actions')).pop();
    verdict.panelActionPostsAndRerenders = Boolean(post) && post.method === 'POST' && sameJson(post.body, { action: 'new_code', values: {} })
        && factInput(1).value === 'ZZZZ9999' && !netButton('Generate new code').disabled;
    // A refusal is an alert on the card and leaves the panel in place.
    networkPanel = () => ({ ok: false, status: 409, code: 'PANEL_FAILED', message: 'Couldn’t save the new code: disk full.' });
    netButton('Generate new code').click();
    await drain();
    const alerts = [...netLayer.body.querySelectorAll('[role="alert"]')].map(el => el.textContent);
    verdict.panelActionRefusalIsAnAlert = alerts.includes('Couldn’t save the new code: disk full.')
        && factInput(1).value === 'ZZZZ9999';
    closeLayer(netLayer);

    // The status's own error is an alert; a service that could not start is too.
    networkPanel = () => ({ ok: true, body: { ...networkStatus(networkCode), error: 'Something is wrong.' } });
    listReply = [{ ...networkItem(), service_error: 'Port 38472 is already in use.' }];
    global.BossModExtensionsDialog.open();
    await drain();
    netLayer = layers[layers.length - 1];
    const netAlerts = [...netLayer.body.querySelectorAll('.market-failed')].filter(el => el.getAttribute('role') === 'alert').map(el => el.textContent);
    verdict.panelErrorAndServiceErrorAreAlerts = netAlerts.includes('Something is wrong.')
        && netAlerts.includes('Port 38472 is already in use.');
    closeLayer(netLayer);

    // A panel that cannot be read says why and offers a retry.
    networkPanel = () => ({ ok: false, status: 409, code: 'PANEL_FAILED', message: 'The panel could not be read.' });
    listReply = [networkItem()];
    global.BossModExtensionsDialog.open();
    await drain();
    netLayer = layers[layers.length - 1];
    verdict.panelLoadFailureShowsTheReasonAndRetry = text().includes('The panel could not be read.')
        && Boolean(netButton('Retry status')) && !factInput(0);
    networkPanel = () => ({ ok: true, body: networkStatus(networkCode) });
    netButton('Retry status').click();
    await drain();
    verdict.panelRetryRereads = Boolean(factInput(0));
    closeLayer(netLayer);

    // An off extension shows Settings but no panel, and no panel read happens.
    networkCalls.length = 0;
    listReply = [{ ...networkItem(), enabled: false }];
    global.BossModExtensionsDialog.open();
    await drain();
    netLayer = layers[layers.length - 1];
    verdict.settingsButtonShowsWhileOffWithoutAPanel = Boolean(netButton('Settings')) && !netLayer.body.querySelector('.ext-fact')
        && !networkCalls.some(call => call.url.endsWith('/panel'));

    // The settings dialog: the shared form on the extension-wide endpoints.
    const listBeforeSettings = listCalls;
    netButton('Settings').click();
    await drain();
    const settingsLayer = layers[layers.length - 1];
    const portInput = settingsLayer.body.querySelector('#ext-settings-port');
    verdict.settingsDialogRendersTheSharedForm = settingsLayer !== netLayer && settingsLayer.options.title === 'Local Network Access settings'
        && Boolean(portInput) && portInput.getAttribute('type') === 'number' && portInput.getAttribute('min') === '1024'
        && portInput.getAttribute('max') === '65535' && portInput.value === '38472' && portInput.getAttribute('placeholder') === '38472'
        && settingsLayer.body.textContent.includes('Pick another if it is taken.')
        && networkCalls.some(call => call.url === '/api/extensions/network/settings' && call.method === 'GET');
    portInput.value = '41000';
    await settingsLayer.body.querySelector('#ext-settings-save').dispatchClick();
    await drain();
    const settingsPut = networkCalls.filter(call => call.method === 'PUT' && call.url.endsWith('/settings')).pop();
    verdict.settingsDialogSavesAndSaysSaved = Boolean(settingsPut) && sameJson(settingsPut.body, { values: { port: '41000' } })
        && settingsLayer.body.textContent.includes('Saved.') && Boolean(settingsLayer.body.querySelector('#ext-settings-done'))
        && listCalls === listBeforeSettings + 1;
    closeLayer(settingsLayer);

    // A verified save shows the extension's own words; a refusal keeps the typing.
    networkSettingsPut = () => ({ ok: false, status: 422, code: 'CONFIG_VERIFY_FAILED', message: 'Port 2000 is already in use.' });
    netButton('Settings').click();
    await drain();
    let again2 = layers[layers.length - 1];
    again2.body.querySelector('#ext-settings-port').value = '2000';
    await again2.body.querySelector('#ext-settings-save').dispatchClick();
    await drain();
    verdict.settingsRefusalKeepsTheTyping = again2.body.textContent.includes('Port 2000 is already in use.')
        && again2.body.querySelector('#ext-settings-port').value === '2000' && !again2.body.querySelector('#ext-settings-save').disabled;
    // Values stored but not applied: the card is told to re-read, the reason is shown.
    const listBeforeApply = listCalls;
    networkSettingsPut = () => ({ ok: false, status: 409, code: 'SERVICE_START_FAILED', message: 'Port 2000 is already in use.' });
    await again2.body.querySelector('#ext-settings-save').dispatchClick();
    await drain();
    verdict.settingsApplyFailureRereadsTheCard = listCalls === listBeforeApply + 1
        && again2.body.textContent.includes('Port 2000 is already in use.');
    closeLayer(again2);
    networkSettingsPut = () => ({ ok: true, body: { label: 'Network access', help: 'h', configured: true, verified: 'Port 41001 is available.', fields: [] } });
    netButton('Settings').click();
    await drain();
    again2 = layers[layers.length - 1];
    await again2.body.querySelector('#ext-settings-save').dispatchClick();
    await drain();
    verdict.settingsVerifiedTextIsShown = again2.body.textContent.includes('Port 41001 is available.');
    closeLayer(again2);
    closeLayer(netLayer);

    // Turning a service on that cannot start: the switch stays off and says why.
    networkEnabled = () => ({ ok: false, status: 409, code: 'SERVICE_START_FAILED', message: 'Port 38472 is already in use.' });
    global.BossModExtensionsDialog.open();
    await drain();
    netLayer = layers[layers.length - 1];
    byId('ext-switch-network').click();
    await drain();
    verdict.failedServiceStartRevertsTheSwitchWithTheReason = text().includes('Port 38472 is already in use.')
        && byId('ext-switch-network').getAttribute('aria-checked') === 'false';
    networkEnabled = (enabled) => ({ ok: true, body: { ...networkItem(), enabled } });
    byId('ext-switch-network').click();
    await drain();
    verdict.serviceSwitchOnLoadsThePanel = Boolean(factInput(0)) && byId('ext-switch-network').getAttribute('aria-checked') === 'true';
    closeLayer(netLayer);

    // Provider controls render through the shared message / error / action helpers: same markup as before.
    listReply = [{ ...bv, id: 'demo-provider', name: 'Demo provider', enabled: true, requires_image_model: false,
        setup: { state: 'not_required' }, model_provider: { connection_name: 'Demo' } }];
    global.BossModExtensionsDialog.open();
    await drain();
    const demoCard = layers[layers.length - 1];
    const note = [...demoCard.body.querySelectorAll('.market-card-note')].find(el => el.getAttribute('aria-live') === 'polite');
    const startButton = [...demoCard.body.querySelectorAll('button')].find(b => b.textContent === 'Start demo');
    verdict.providerControlsKeepTheirMarkup = note.textContent === 'Demo ready'
        && startButton.classList.contains('btn') && startButton.classList.contains('btn-sm')
        && startButton.getAttribute('type') === 'button'
        && note.parentNode.children.indexOf(startButton) === note.parentNode.children.indexOf(note) + 1;
    closeLayer(demoCard);

    console.log(JSON.stringify(verdict));
})().catch((err) => { process.stderr.write(String(err && err.stack ? err.stack : err)); process.exit(1); });
