/**
 * Node harness: the Extensions dialog, the Browser Vision status reader and
 * the single-agent browser viewer.
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
        const layer = { options, body: h("div", { class: "layer" }, options.body) };
        layers.push(layer);
        document.body.append(layer.body);
        return { close: () => closeLayer(layer) };
    },
};
function closeLayer(layer) {
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
let liveCalls = 0;
global.apiFetch = async (url, init) => {
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (url === "/api/extensions") return ok([bv]);
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

    console.log(JSON.stringify(verdict));
})().catch((err) => { process.stderr.write(String(err && err.stack ? err.stack : err)); process.exit(1); });
