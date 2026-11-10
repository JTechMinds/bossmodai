/**
 * Node harness: who is mid-turn outlives the conversation that showed it.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 *
 * Presence is page-lifetime state now: core/presence.js keeps the store's
 * `presence` slice from the server's connect snapshot (`presence_snapshot`)
 * and its deltas (`agent_presence`). A source only SELECTS its rows
 * (`presenceFor`), and the transcript paints them on a minute clock. So the
 * row survives what used to lose it: leaving Chat and coming back (the
 * conversation is destroyed and rebuilt), opening a thread after its turn
 * started, and a turn ending while another conversation is open.
 *
 * Every module is the real one. The transcript's clock is the only timer in
 * this stack, so `setTimeout` and `Date.now` are faked for the whole run and
 * the clock is advanced by hand.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// core/markdown.js reads `marked`, `hljs` and `DOMParser`; Node has none
// of them, and this harness is not what proves the sanitiser correct.
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
// The chrome paints its action glyphs after every apply.
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

// ─── Fake timers ───

let now = Date.parse("2026-11-10T12:00:00.000Z");
Date.now = () => now;
let nextTimerId = 1;
const timers = new Map();
global.setTimeout = (fn, ms) => {
    const id = nextTimerId;
    nextTimerId += 1;
    timers.set(id, { at: now + ms, fn });
    return id;
};
global.clearTimeout = (id) => { timers.delete(id); };
/** Move the clock on, firing every timer that falls due, in order. */
function advance(ms) {
    const until = now + ms;
    for (;;) {
        const due = Array.from(timers.entries())
            .filter(([, timer]) => timer.at <= until)
            .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
    }
    now = until;
}
const ago = (ms) => new Date(now - ms).toISOString();

// ─── Modules ───

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModPresence",
    "BossModOperatorInvalidate", "BossModFormat", "BossModGates", "BossModConsentCard",
    "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModYoloMode", "BossModMenu", "BossModMenuButton",
    "BossModEmptyState", "BossModNoteGroup", "BossModTranscript", "BossModTranscriptCache", "BossModMessage", "BossModEventCards",
    "BossModTitleRename", "BossModChromeMenu", "BossModChromeSwitch", "BossModConversationChrome",
    "BossModDesktopClipboard", "BossModComposerAttachments", "BossModComposerHistory", "BossModMentionDraft", "BossModComposer", "BossModSystemReceipts",
    "BossModNeedShape", "BossModNeedsBar",
    "BossModThreadArchive", "BossModThreadSeat", "BossModThreadRequests", "BossModApprovalControls", "BossModThreadMembers", "BossModThreadSource",
    "BossModAgentRequests", "BossModAgentSource",
    "BossModConversationFocus", "BossModConversationWindow", "BossModChatRewindDialog", "BossModChatRewind", "BossModConsentActivity", "BossModConversation",
    "BossModAgentApi", "BossModAgentRoutes", "BossModDeskMemory", "BossModAgentMenu",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModPresence, BossModConversation } = global;

// ─── A scripted API ───

const THREADS = {
    t1: { id: "t1", name: "Launch", status: "active", members: [{ id: "a", name: "Ada" }, { id: "b", name: "Bo" }] },
    t2: { id: "t2", name: "Design", status: "active", members: [{ id: "a", name: "Ada" }] },
    old: { id: "old", name: "Old room", status: "archived", members: [{ id: "a", name: "Ada" }] },
};
const api = async (url) => {
    const text = String(url);
    if (/^\/api\/conversations\/[^/]+\/[^/]+\/window$/.test(text)) {
        return { ok: true, async json() { return { notes: null, visible_from_message_id: null, ceiling_hit: false }; } };
    }
    if (/^\/api\/agents\/[^/]+\/messages/.test(text)) return { ok: true, async json() { return []; } };
    const record = text.match(/^\/api\/agents\/([^/?]+)$/);
    if (record) return { ok: true, async json() { return { id: record[1] }; } };
    const thread = text.match(/^\/api\/channels\/([^/?]+)$/);
    if (thread && THREADS[thread[1]]) {
        return { ok: true, async json() { return { channel: { ...THREADS[thread[1]] }, messages: [] }; } };
    }
    throw new Error(`unhandled ${text}`);
};
global.apiFetch = (...args) => api(...args);

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
    for (let i = 0; i < 8; i += 1) await tick();
}

// ─── The page: one store and bus, presence attached as the shell does ───

const store = BossModStore.createStore({
    roster: [
        { id: "a", name: "Ada", role: "Writer" },
        { id: "b", name: "Bo", role: "Reviewer" },
    ],
    threads: [],
    presence: [],
    hasUsableModel: true,
    needs: [],
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
BossModOperatorInvalidate.attach({ bus });
BossModPresence.attach({ store, bus });

const needsStub = {
    refresh: () => Promise.resolve(),
    resolve: () => Promise.resolve(),
    acknowledge: () => {},
    getError: () => "",
    subscribeError: () => () => {},
    destroy: () => {},
};
const agentMenu = BossModAgentMenu.create({ api, store, navigate() {} });
// What chat-place.js keeps for the life of the page, so they outlive a remount.
const drafts = new Map();
const cache = BossModTranscriptCache.createCache();
function mountConversation() {
    const conversation = BossModConversation.createConversation({
        store, bus, api, navigate() {}, needs: needsStub, drafts, cache, agentMenu,
    });
    documentStub.body.append(conversation.element);
    return conversation;
}
const presenceRows = (conversation) => conversation.element
    .querySelectorAll(".transcript-presence-row")
    .map((row) => row.textContent);

const errors = [];
const realConsoleError = console.error;
console.error = (...args) => { errors.push(args); };

const thinking = (agentId, name, channelId, since) => ({
    agent_id: agentId, agent_name: name, phase: "thinking", channel_id: channelId, since,
});
const queued = (agentId, name, channelId, ahead) => ({
    agent_id: agentId, agent_name: name, phase: "queued", channel_id: channelId, ahead,
});
const idle = (agentId, channelId) => ({ agent_id: agentId, agent_name: "x", phase: "idle", channel_id: channelId });

(async () => {
    // ─── snapshotReplaces ───
    bus.publish("presence_snapshot", [
        { ...thinking("a", "Ada", "t1", ago(1000)), ahead: null },
        { ...queued("b", "Bo", "t2", 1), since: null },
    ]);
    const firstSnapshot = store.getState().presence;
    bus.publish("presence_snapshot", [{ ...queued("b", "Bo", null, 0), since: null }]);
    const replaced = store.getState().presence;
    const snapshotReplaces = firstSnapshot.length === 2
        && firstSnapshot[0].agentId === "a" && firstSnapshot[0].channelId === "t1"
        && firstSnapshot[0].phase === "thinking" && firstSnapshot[1].ahead === 1
        && replaced.length === 1 && replaced[0].agentId === "b" && replaced[0].channelId === null
        && replaced[0].ahead === 0;
    if (!snapshotReplaces) throw new Error(`a snapshot must replace the slice: ${JSON.stringify(replaced)}`);

    // A malformed frame is dropped at the boundary, loudly, and changes nothing.
    errors.length = 0;
    bus.publish("presence_snapshot", [{ agent_id: "", agent_name: "Nobody", phase: "thinking" }]);
    bus.publish("agent_presence", { agent_id: "a", agent_name: "Ada", phase: "typing", channel_id: "t1" });
    const malformedLeavesState = store.getState().presence === replaced
        && errors.length === 2
        && errors.every((args) => args[0] === "[presence] dropped malformed event");
    if (!malformedLeavesState) {
        throw new Error(`a malformed frame must be logged and ignored: ${errors.length} errors`);
    }
    bus.publish("presence_snapshot", []);

    // ─── deltaSetsAndRemoves ───
    let notified = 0;
    const offCount = store.subscribe((s) => s.presence, () => { notified += 1; });
    bus.publish("agent_presence", queued("a", "Ada", "t1", 2));
    bus.publish("agent_presence", queued("a", "Ada", "t1", 2));
    const afterRepeat = notified;
    bus.publish("agent_presence", thinking("a", "Ada", "t1", ago(1000)));
    const oneKey = store.getState().presence.length === 1
        && store.getState().presence[0].phase === "thinking";
    bus.publish("agent_presence", thinking("a", "Ada", "t2", ago(1000)));
    bus.publish("agent_presence", idle("a", "t1"));
    const removedOne = store.getState().presence.map((row) => row.channelId).join("|") === "t2";
    const beforeIdleRepeat = notified;
    bus.publish("agent_presence", idle("a", "t1"));
    bus.publish("agent_presence", idle("nobody", null));
    const deltaSetsAndRemoves = afterRepeat === 1 && oneKey && removedOne && notified === beforeIdleRepeat;
    if (!deltaSetsAndRemoves) {
        throw new Error(`deltas: repeat ${afterRepeat}, oneKey ${oneKey}, removedOne ${removedOne}, `
            + `idle no-op ${notified === beforeIdleRepeat}`);
    }
    offCount();
    bus.publish("presence_snapshot", []);

    // ─── survivesRemount: Chat → Log → Chat ───
    let conversation = mountConversation();
    await conversation.open("t1", "thread");
    await settle();
    bus.publish("agent_presence", thinking("a", "Ada", "t1", ago(5000)));
    const paintedBefore = presenceRows(conversation).join("|") === "Ada is thinking...";
    // Leaving Chat destroys the controller, as chat-place.js unmount() does.
    conversation.destroy();
    conversation.element.remove();
    const timerClearedOnDestroy = timers.size === 0;
    conversation = mountConversation();
    await conversation.open("t1", "thread");
    await settle();
    const survivesRemount = paintedBefore && timerClearedOnDestroy
        && presenceRows(conversation).join("|") === "Ada is thinking...";
    if (!survivesRemount) {
        throw new Error(`the row must survive a remount: before ${paintedBefore}, `
            + `timer cleared ${timerClearedOnDestroy}, after ${presenceRows(conversation).join("|")}`);
    }

    // ─── openedAfterThinking: a thread opened after its turn began ───
    bus.publish("agent_presence", thinking("b", "Bo", "t2", ago(1000)));
    const notPaintedHere = presenceRows(conversation).join("|") === "Ada is thinking...";
    await conversation.open("t2", "thread");
    await settle();
    const openedAfterThinking = notPaintedHere && presenceRows(conversation).join("|") === "Bo is thinking...";
    if (!openedAfterThinking) {
        throw new Error(`a thread opened mid-turn must show the row, got ${presenceRows(conversation).join("|")}`);
    }

    // ─── idleWhileAway: a turn that ends while another conversation is open ───
    bus.publish("agent_presence", idle("a", "t1"));
    await conversation.open("t1", "thread");
    await settle();
    const idleWhileAway = presenceRows(conversation).length === 0;
    if (!idleWhileAway) {
        throw new Error(`an idle while away must clear the row, got ${presenceRows(conversation).join("|")}`);
    }

    // ─── archivedShowsNone ───
    bus.publish("agent_presence", thinking("a", "Ada", "old", ago(1000)));
    await conversation.open("old", "thread");
    await settle();
    const openedArchivedEmpty = presenceRows(conversation).length === 0;
    // A live room sealed while open repaints its rows away.
    bus.publish("agent_presence", thinking("a", "Ada", "t1", ago(1000)));
    await conversation.open("t1", "thread");
    await settle();
    const liveBeforeSeal = presenceRows(conversation).length === 1;
    bus.publish("channel_updated", { ...THREADS.t1, status: "archived" });
    const archivedShowsNone = openedArchivedEmpty && liveBeforeSeal && presenceRows(conversation).length === 0
        && store.getState().presence.some((row) => row.channelId === "t1");
    if (!archivedShowsNone) {
        throw new Error(`archived rooms show no presence: opened ${openedArchivedEmpty}, `
            + `live ${liveBeforeSeal}, sealed ${presenceRows(conversation).join("|")}`);
    }

    // ─── dmAgentRow: what the agent is doing anywhere, thinking first ───
    bus.publish("presence_snapshot", [
        { ...queued("a", "Ada", "t2", 3), since: null },
        { ...queued("a", "Ada", "t1", 1), since: null },
        { ...queued("b", "Bo", "t1", 0), since: null },
    ]);
    await conversation.open("a", "agent");
    await settle();
    const fewestAhead = presenceRows(conversation).join("|") === "Queued (1 ahead)";
    bus.publish("agent_presence", thinking("a", "Ada", "t2", ago(1000)));
    const thinkingWins = presenceRows(conversation).join("|") === "Ada is thinking...";
    const rows = store.getState().presence;
    const dmAgentRow = fewestAhead && thinkingWins
        && BossModPresence.agentRow(rows, "a").phase === "thinking"
        && BossModPresence.agentRow(rows, "b").ahead === 0
        && BossModPresence.agentRow(rows, "nobody") === null;
    if (!dmAgentRow) {
        throw new Error(`the DM shows the agent's row: fewest ahead ${fewestAhead}, thinking ${thinkingWins}`);
    }

    // ─── clock: "is working · 2m" from since, repainted at minute boundaries ───
    bus.publish("presence_snapshot", [{ ...thinking("a", "Ada", null, ago(2 * 60 * 1000 + 10 * 1000)), ahead: null }]);
    const twoMinutes = presenceRows(conversation).join("|") === "Ada is working · 2m";
    const oneTimer = timers.size === 1;
    advance(49 * 1000);
    const heldUntilBoundary = presenceRows(conversation).join("|") === "Ada is working · 2m";
    advance(1000);
    const threeMinutes = presenceRows(conversation).join("|") === "Ada is working · 3m";
    advance(60 * 1000);
    const fourMinutes = presenceRows(conversation).join("|") === "Ada is working · 4m";
    // Under a minute the copy holds, and the first switch lands at exactly one.
    bus.publish("presence_snapshot", [{ ...thinking("a", "Ada", null, ago(30 * 1000)), ahead: null }]);
    const shortTurn = presenceRows(conversation).join("|") === "Ada is thinking..." && timers.size === 1;
    advance(30 * 1000);
    const firstSwitch = presenceRows(conversation).join("|") === "Ada is working · 1m";
    // A since that does not parse is surfaced, painted without a number, and
    // starts no clock.
    errors.length = 0;
    bus.publish("presence_snapshot", [{ ...thinking("a", "Ada", null, "not-a-time"), ahead: null }]);
    const unparseable = presenceRows(conversation).join("|") === "Ada is thinking..."
        && timers.size === 0 && errors.length === 1;
    // The turn ends: no row, no clock.
    bus.publish("presence_snapshot", [{ ...thinking("a", "Ada", null, ago(90 * 1000)), ahead: null }]);
    bus.publish("agent_presence", idle("a", null));
    const endedClears = presenceRows(conversation).length === 0 && timers.size === 0;
    const clock = twoMinutes && oneTimer && heldUntilBoundary && threeMinutes && fourMinutes
        && shortTurn && firstSwitch && unparseable && endedClears;
    if (!clock) {
        throw new Error(`clock: 2m ${twoMinutes}, one timer ${oneTimer}, held ${heldUntilBoundary}, `
            + `3m ${threeMinutes}, 4m ${fourMinutes}, short ${shortTurn}, first ${firstSwitch}, `
            + `unparseable ${unparseable}, ended ${endedClears}`);
    }

    conversation.destroy();
    console.error = realConsoleError;
    process.stdout.write(JSON.stringify({
        ok: true,
        snapshotReplaces,
        malformedLeavesState,
        deltaSetsAndRemoves,
        survivesRemount,
        openedAfterThinking,
        idleWhileAway,
        archivedShowsNone,
        dmAgentRow,
        clock,
    }));
})().catch((err) => {
    console.error = realConsoleError;
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
