/**
 * Node harness: the Extensions dialog, the Browser Vision status reader, the
 * single-agent browser viewer, and the per-agent surfaces — the data table's
 * wrapper contract (over an injected table factory: Tabulator needs real
 * layout, so its rendering is checked in the real app), the desk's
 * Extensions section and the per-agent settings dialog.
 *
 * Invoked by tests/test_extensions_ui.py with the module paths in load order.
 * The modal frame, the confirm strip, the clock and the network are stubbed;
 * the bus, the dialog, the status module, the viewer, the switch and the DOM
 * helpers are the real modules. Prints one JSON object of named verdicts.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

installDom();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModBus", "BossModFormat", "BossModSwitch", "BossModExtensionsApi",
    "BossModBrowserVisionStatus", "BossModExtensionsLive", "BossModExtensionsDialog",
    "BossModGates", "BossModSecretField", "BossModDataTable", "BossModAgentConfigDialog", "BossModDeskExtensions",
    "BossModTabs", "BossModFactList", "BossModAgentViewDialog",
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

const bv = {
    id: "browser-vision", name: "Browser Vision", version: "1.0.0", description: "d",
    command: { name: "bv", summary: "s" }, enabled: true, valid: true, invalid_reason: null,
    requires_image_model: true, live_view: true, setup: { state: "ready", detail: null },
    setup_label: "Download browser (~120 MB)", excluded_agents: [],
};
const liveItem = (takenAt) => ({
    agent_id: "a1", agent_name: "Iris", taken_at: takenAt, command: "bv open example.com",
    url: "https://example.com", title: "Example Domain",
    caption_lines: ["window: desktop 1280x800", "view: full page", "image 1280x800", "marks: 3"],
    image_url: `/api/extensions/browser-vision/live/a1/image?t=${Date.parse(takenAt)}`,
});
let liveReply = { kind: "ok", body: { items: [] } };
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
let liveCalls = 0;
let listReply = [bv];
let deskReads = 0;
const viewCalls = [];
const mailItem = {
    ...bv, id: "mail", name: "Microsoft 365 Mailbox", command: { name: "mail", summary: "s" }, live_view: false,
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
global.apiFetch = async (url, init) => {
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (url === "/api/extensions") return ok(listReply);
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
    if (url.endsWith("/live")) {
        liveCalls += 1;
        if (liveReply.kind === "disabled") {
            return { ok: false, status: 409, json: async () => ({ detail: { error: "EXTENSION_DISABLED", message: "Browser Vision is off." } }) };
        }
        if (liveReply.kind === "broken") {
            return { ok: false, status: 500, json: async () => ({ detail: "boom" }) };
        }
        return ok(liveReply.body);
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
const Status = global.BossModBrowserVisionStatus;
const bus = global.BossModBus.createBus(global.BossModBus.KNOWN_TOPICS);
Status.attach({ bus });
const push = (extensionId, agentId) => bus.publish("extension_live", { extension_id: extensionId, agent_id: agentId });

const drain = async () => { for (let i = 0; i < 8; i += 1) await new Promise((r) => setImmediate(r)); };
const text = () => document.body.textContent;
const byId = (id) => document.body.querySelector(`#${id}`);
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
    push("browser-vision", "a1");
    push(null, null);
    bus.publish("resync", { downtimeMs: 0 });
    await drain();
    verdict.noFetchesWithoutSubscribers = liveCalls === 0 && timers.size === 0;

    // 3. The first subscriber reads once; then time alone reads nothing.
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:00:00Z")] } };
    let notified = 0;
    const off = Status.subscribe(() => { notified += 1; });
    await drain();
    verdict.subscribingReadsAtOnce = liveCalls === 1 && notified === 1 && Status.hasView("a1")
        && Status.latest("a1").title === "Example Domain" && timers.size === 0;
    await advance(60000);
    verdict.noPollingOverSixtySeconds = liveCalls === 1 && timers.size === 0;

    // 4. Pushes: ours or null reads once each; another extension's reads nothing.
    let before = liveCalls;
    push("browser-vision", "a1");
    await drain();
    verdict.anEventReadsExactlyOnce = liveCalls === before + 1;
    before = liveCalls;
    push(null, null);
    await drain();
    verdict.aNullEventReadsExactlyOnce = liveCalls === before + 1;
    before = liveCalls;
    push("some-other-extension", "a1");
    await drain();
    verdict.anotherExtensionsEventReadsNothing = liveCalls === before;
    before = liveCalls;
    for (let i = 0; i < 5; i += 1) push("browser-vision", "a1");
    await drain();
    verdict.aBurstOfFiveReadsAtMostTwice = liveCalls - before >= 1 && liveCalls - before <= 2;
    before = liveCalls;
    bus.publish("resync", { downtimeMs: 1200 });
    await drain();
    verdict.resyncReadsOnce = liveCalls === before + 1;

    // 5. Another failure keeps the last known state and logs; disabled empties it.
    liveReply = { kind: "broken" };
    push("browser-vision", "a1");
    await drain();
    verdict.failureKeepsLastStateAndLogs = Status.hasView("a1")
        && errors.some((line) => line.includes("[browser-vision-status]"));
    liveReply = { kind: "disabled" };
    push("browser-vision", null);
    await drain();
    verdict.disabledIsAnEmptySet = !Status.hasView("a1") && Status.latest("a1") === null;

    off();
    before = liveCalls;
    push("browser-vision", "a1");
    push(null, null);
    bus.publish("resync", { downtimeMs: 0 });
    await drain();
    await advance(60000);
    verdict.noSubscribersMeansNoFetches = liveCalls === before && timers.size === 0;

    // 6. A toggle in the dialog asks the status to re-read at once.
    const again = Status.subscribe(() => {});
    await drain();
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:00:00Z")] } };
    before = liveCalls;
    global.BossModExtensionsDialog.open();
    await drain();
    byId("ext-switch-browser-vision").click();  // turn off
    await drain();
    verdict.toggleRefreshesTheStatus = liveCalls === before + 1;
    closeLayer(layers[layers.length - 1]);
    again();

    // 7. The single-agent viewer: real alt, caption lines, and it follows the status.
    liveReply = { kind: "ok", body: { items: [liveItem("2026-09-28T10:00:00Z")] } };
    const blobsBefore = blobFetches.length;
    global.BossModExtensionsLive.openForAgent("a1", "Iris");
    await drain();
    const viewer = layers[layers.length - 1];
    const img = () => viewer.body.querySelector("#ext-live-image");
    verdict.viewerTitleAndHeadFocus = viewer.options.title === "Iris — browser"
        && viewer.options.focusBody === false && viewer.options.size === "panel";
    verdict.viewerImageHasRealAlt = Boolean(img())
        && img().getAttribute("alt") === "Latest screenshot from Iris: Example Domain"
        && img().getAttribute("src") === liveItem("2026-09-28T10:00:00Z").image_url.replace(/^/, "blob:");
    verdict.viewerShowsEveryCaptionLine = ["command: bv open example.com", "url: https://example.com",
        "title: Example Domain", "window: desktop 1280x800", "view: full page", "image 1280x800", "marks: 3", "taken: "]
        .every((line) => viewer.body.textContent.includes(line));

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
    verdict.viewerSaysSessionEnded = text().includes("Iris’s browser session ended.") && !img();

    closeLayer(viewer);
    before = liveCalls;
    push("browser-vision", "a1");
    await drain();
    verdict.viewerStopsReadingOnClose = liveCalls === before && timers.size === 0;

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
    const hhmm = (iso) => { const d = new Date(iso); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
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

    console.log(JSON.stringify(verdict));
})().catch((err) => { process.stderr.write(String(err && err.stack ? err.stack : err)); process.exit(1); });
