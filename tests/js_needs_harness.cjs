/**
 * Node harness: needs-store behaviour.
 *
 * Invoked by tests/test_ui_needs.py. Not a browser bundle.
 *
 * The properties proven here are the ones no source grep can reach: that a
 * continuously ticking world produces no store notifications, that a failed
 * resolution puts the need back, and that a failed refresh keeps the last good
 * list rather than telling the operator nothing needs them.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

// installDom's document listeners are no-ops; the popover's Esc handling lives
// on one, so record them and fire them by hand.
const docListeners = {};
documentStub.addEventListener = (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn); };
documentStub.removeEventListener = (type, fn) => {
    docListeners[type] = (docListeners[type] || []).filter((item) => item !== fn);
};
const pressKey = (key, shiftKey) => {
    [...(docListeners.keydown || [])].forEach((fn) => fn({ key, shiftKey: shiftKey === true, preventDefault() {} }));
};

const load = (path, name) => eval(`${fs.readFileSync(path, "utf8")}\n;global.${name} = ${name};\n`);
const [
    domPath, storePath, busPath, gatesPath, formatPath, nestGitPath, cardsPath, shapePath,
    needsPath, popoverPath, barPath, toastPath,
] = process.argv.slice(2);
load(domPath, "BossModDom");
load(storePath, "BossModStore");
load(busPath, "BossModBus");
// needs-store.js guards refresh() with the shared load generation.
load(gatesPath, "BossModGates");
load(formatPath, "BossModFormat");
load(nestGitPath, "BossModNestGitCard");
load(cardsPath, "BossModEventCards");
load(shapePath, "BossModNeedShape");
load(needsPath, "BossModNeeds");
load(popoverPath, "BossModNeedsPopover");
load(barPath, "BossModNeedsBar");
load(toastPath, "BossModNeedsToast");

const {
    BossModStore, BossModBus, BossModNeeds, BossModNeedsPopover,
    BossModNeedsBar, BossModNeedsToast,
} = global;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

const SNAKE_KEYS = ["agent_id", "agent_name", "created_at", "conversation_id"];

/** One pending CLI approval, exactly as api/routes/needs.py emits it. */
function approvalRow(id, conversationId) {
    return {
        id,
        kind: "approval",
        agent_id: "a1",
        agent_name: "Jim",
        title: "Jim wants to run a command",
        sub: "rm -rf build/",
        created_at: "2026-09-07T12:00:00Z",
        conversation_id: conversationId,
        card_kind: "cli_approval",
        grouped_ids: [id],
        actions: [
            { label: "Approve", method: "POST", tone: "primary",
              href: `/api/cli-policy/approvals/${id}/approve` },
            { label: "Reject", method: "POST", tone: "quiet",
              href: `/api/cli-policy/approvals/${id}/reject` },
        ],
    };
}

/** One blocked task, exactly as api/routes/needs.py emits it. */
function blockedRow(id, agentId) {
    return {
        id,
        kind: "blocked",
        agent_id: agentId,
        agent_name: "Jim",
        title: "Jim is blocked",
        sub: "Ship the release notes",
        created_at: "2026-09-07T13:00:00Z",
        conversation_id: agentId,
        actions: [
            { label: "Open task", method: "GET", tone: "primary",
              href: `/api/tasks/${id}` },
        ],
    };
}

/** One pending Nest git consent, exactly as api/routes/needs.py emits it. */
function nestGitRow(id) {
    return {
        id,
        kind: "consent",
        card_kind: "nest_git",
        agent_id: "a1",
        agent_name: "Jim",
        title: "Jim needs permission to push to GitHub",
        sub: "git push origin main",
        created_at: "2026-09-07T12:05:00Z",
        conversation_id: "a1",
        grouped_ids: [id],
        actions: [
            { label: "Use this computer’s Git login", method: "POST", tone: "primary",
              href: `/api/nest-git/${id}/enable`, body: {} },
            { label: "Add a GitHub access token or SSH key", method: "POST", tone: "default",
              href: `/api/nest-git/${id}/credentials`, body: {} },
            { label: "Use Acme", method: "POST", tone: "default",
              href: `/api/nest-git/${id}/use`, body: { credential_id: "acme" } },
        ],
    };
}

/** One pending host-path consent, exactly as api/routes/needs.py emits it. */
function consentRow(id, conversationId) {
    return {
        id,
        kind: "consent",
        agent_id: "a1",
        agent_name: "Jim",
        title: "Jim wants to read a folder",
        sub: "docs/superpowers/specs/",
        created_at: "2026-09-07T10:17:56Z",
        conversation_id: conversationId || "a1",
        card_kind: "host_path",
        grouped_ids: [id],
        actions: [
            { label: "Allow once", method: "POST", tone: "primary",
              href: `/api/host-path-consent/${id}/allow-once` },
            { label: "Deny", method: "POST", tone: "quiet",
              href: `/api/host-path-consent/${id}/deny` },
        ],
    };
}

async function main() {
    const calls = [];
    let queue = [consentRow("n1")];
    let queueFails = false;
    let resolveFails = false;
    let resolveGone = false;

    function api(url, init) {
        calls.push({ url, method: (init && init.method) || "GET", body: init && init.body });
        if (url.startsWith("/api/needs")) {
            if (queueFails) return Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve("down") });
            return Promise.resolve({ ok: true, json: () => Promise.resolve(queue) });
        }
        if (resolveGone) {
            return Promise.resolve({
                ok: false,
                status: 404,
                text: () => Promise.resolve(
                    JSON.stringify({ detail: "Approval request not found or already resolved" }),
                ),
            });
        }
        if (resolveFails) {
            return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("Consent already answered.") });
        }
        return Promise.resolve({ ok: true, text: () => Promise.resolve("") });
    }

    const store = BossModStore.createStore({ needs: [] });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const storeBaseline = store.subscriberCount();
    const busBaseline = bus.subscriberCount();

    let notifications = 0;
    store.subscribe((s) => s.needs, () => { notifications += 1; });

    const needs = BossModNeeds.createNeedsStore({ store, bus, api });
    await drain();

    // ─── 1. The wire shape is normalised exactly once ───

    const first = store.getState().needs;
    if (first.length !== 1) throw new Error(`boot refresh must publish the queue, got ${first.length}`);
    const need = first[0];
    for (const key of SNAKE_KEYS) {
        if (key in need) throw new Error(`a snake_case key reached a consumer: ${key}`);
    }
    if (need.agentId !== "a1" || need.agentName !== "Jim") {
        throw new Error(`agent_id/agent_name must become agentId/agentName, got ${JSON.stringify(need)}`);
    }
    if (need.createdAt !== "2026-09-07T10:17:56Z" || need.conversationId !== "a1") {
        throw new Error("created_at/conversation_id must become createdAt/conversationId");
    }
    if (need.actions[0].href !== "/api/host-path-consent/n1/allow-once") {
        throw new Error("actions must survive normalisation with their server hrefs");
    }
    const normalisesShape = true;

    if (!BossModNeedShape.isOpenFocusNeed({ kind: "consent", agentId: "a1" })) {
        throw new Error("pending consent is an open Focus need");
    }
    if (!BossModNeedShape.isOpenFocusNeed({ kind: "approval", agentId: "a1" })) {
        throw new Error("pending approval is an open Focus need");
    }
    if (BossModNeedShape.isOpenFocusNeed({ kind: "error", agentId: "a1" })) {
        throw new Error("an error card is not an open Focus need");
    }
    if (BossModNeedShape.isOpenFocusNeed({ kind: "blocked", agentId: "a1" })) {
        throw new Error("a task block is not an open Focus need");
    }
    if (BossModNeedShape.isOpenFocusNeed({ kind: "consent" })) {
        throw new Error("a Focus need without an agent cannot paint a People row");
    }
    if (!BossModNeedShape.belongsOnOpenFocus(
        { kind: "approval", agentId: "a1", conversationId: "th1" },
        "a1",
        "agent",
    )) {
        throw new Error("thread-originated approval belongs on the agent's open Focus");
    }
    if (BossModNeedShape.belongsOnOpenFocus(
        { kind: "approval", agentId: "a1", conversationId: "th1" },
        "th1",
        "thread",
    ) !== true) {
        throw new Error("thread-originated approval belongs on its origin thread");
    }
    if (!BossModNeedShape.coversInlineNeed(
        { id: "dup-new", groupedIds: ["dup-old", "dup-new"] },
        ["dup-old"],
    )) {
        throw new Error("a coalesced sibling inline card must cover the live need");
    }
    const openFocusNeedTableHolds = true;

    // ─── 2. A ticking world publishes nothing ───
    // The world ticks continuously. Rebuilding store.needs on each tick would
    // re-render the roster, the bell, the bar and the toast forever.

    const quietBaseline = notifications;
    for (let i = 0; i < 10; i += 1) {
        // A healthy diagnostic: no error, so no need, so no change.
        bus.publish("diagnostic", {
            id: `d-healthy-${i}`, agent_id: "a1", agent_name: "Jim",
            status: "success", error: null, created_at: "2026-09-07T10:20:00Z",
        });
        // A recognised activity: refreshes, recomputes, and finds the same queue.
        bus.publish("activity", { event: "status_changed", title: "Jim blocked" });
    }
    await drain();
    if (notifications !== quietBaseline) {
        throw new Error(`twenty no-op ticks produced ${notifications - quietBaseline} needs notifications`);
    }
    const quietOnUnchangedTick = true;

    // ─── 3. An unrecognised activity name is ignored ───

    const beforeUnknown = calls.filter((c) => c.url.startsWith("/api/needs")).length;
    bus.publish("activity", { event: "agent_moved", title: "Jim walked to desk 3" });
    bus.publish("activity", { event: "watchdog_ping", title: "ping" });
    await drain();
    const afterUnknown = calls.filter((c) => c.url.startsWith("/api/needs")).length;
    if (afterUnknown !== beforeUnknown) {
        throw new Error("an unrecognised activity name must not refresh the queue");
    }
    const ignoresUnknownActivity = true;

    // ─── 4. A diagnostic carrying an error becomes a need ───
    // The predicate is `error`, not `status`: the dispatcher's crash path
    // leaves status at its "success" default.

    bus.publish("diagnostic", {
        id: "d1", agent_id: "a2", agent_name: "Laura", status: "success",
        error: "Model call timed out after 60s", created_at: "2026-09-07T11:00:00Z",
    });
    await drain();
    const errorNeed = store.getState().needs.find((item) => item.kind === "error");
    if (!errorNeed) throw new Error("a diagnostic with a non-empty error must raise a need");
    if (errorNeed.id !== "d1" || errorNeed.agentId !== "a2") {
        throw new Error("an error need must carry the diagnostic id and agent");
    }
    if (errorNeed.title !== "Laura hit an error") {
        throw new Error(`unexpected error need title: ${errorNeed.title}`);
    }
    if (errorNeed.sub !== "Model call timed out after 60s") {
        throw new Error("the error text is the need's sub line");
    }
    if (errorNeed.conversationId !== "a2") {
        throw new Error("an error need points at the agent that hit it");
    }
    if (errorNeed.actions[0].href !== "/api/diagnostics/d1") {
        throw new Error(`unexpected diagnostics href: ${errorNeed.actions[0].href}`);
    }
    // Deduped by diagnostic id: the same broadcast twice is still one need.
    bus.publish("diagnostic", {
        id: "d1", agent_id: "a2", agent_name: "Laura", status: "success",
        error: "Model call timed out after 60s", created_at: "2026-09-07T11:00:00Z",
    });
    await drain();
    if (store.getState().needs.filter((item) => item.id === "d1").length !== 1) {
        throw new Error("error needs must be deduped by diagnostic id");
    }
    const errorNeedFromDiagnostic = true;

    // ─── 4b. Identical error cards collapse to one live card plus a count ───
    for (let i = 0; i < 3; i += 1) {
        bus.publish("diagnostic", {
            id: `timeout-${i}`, agent_id: "a2", agent_name: "Laura", status: "success",
            error: "LLM call timed out after 120s",
            created_at: `2026-09-07T11:0${i}:00Z`,
        });
    }
    await drain();
    const timeoutNeeds = store.getState().needs.filter((item) => (
        item.kind === "error" && item.sub === "LLM call timed out after 120s" && item.agentId === "a2"
    ));
    if (timeoutNeeds.length !== 1) {
        throw new Error(`identical timeouts must be one live card, got ${timeoutNeeds.length}`);
    }
    if (timeoutNeeds[0].count !== 3) {
        throw new Error(`timeout card count must be 3, got ${timeoutNeeds[0].count}`);
    }
    if (!String(timeoutNeeds[0].title).includes("×3")) {
        throw new Error(`timeout card title must carry the count, got ${timeoutNeeds[0].title}`);
    }
    bus.publish("diagnostic", {
        id: "timeout-jim", agent_id: "a1", agent_name: "Jim", status: "success",
        error: "LLM call timed out after 120s", created_at: "2026-09-07T11:10:00Z",
    });
    await drain();
    const jimTimeouts = store.getState().needs.filter((item) => (
        item.kind === "error" && item.sub === "LLM call timed out after 120s" && item.agentId === "a1"
    ));
    if (jimTimeouts.length !== 1 || jimTimeouts[0].count !== 1) {
        throw new Error("identical copy on a different agent must stay a separate card");
    }
    await needs.resolve(timeoutNeeds[0], timeoutNeeds[0].actions[0]);
    await drain();
    if (store.getState().needs.some((item) => (
        item.sub === "LLM call timed out after 120s" && item.agentId === "a2"
    ))) {
        throw new Error("acknowledging the live timeout card must clear the grouped copies");
    }
    const identicalErrorCardsCoalesce = true;

    // ─── 5. A failed resolution puts the need back ───
    // A dropped failure leaves the operator believing they approved something
    // they did not.

    resolveFails = true;
    const target = store.getState().needs.find((item) => item.id === "n1");
    let threw = false;
    try {
        await needs.resolve(target, target.actions[0]);
    } catch (err) {
        threw = true;
    }
    await drain();
    if (!threw) throw new Error("a failed resolution must reject so the caller can explain it");
    const restored = store.getState().needs.find((item) => item.id === "n1");
    if (!restored) throw new Error("a failed resolution must restore the need");
    if (!restored.error || !restored.error.includes("Consent already answered.")) {
        throw new Error(`the restored need must carry the failure text, got ${restored.error}`);
    }
    const restoresOnFailedResolve = true;

    // A successful resolution removes it optimistically and then confirms.
    resolveFails = false;
    queue = [];
    await needs.resolve(restored, restored.actions[0]);
    await drain();
    if (store.getState().needs.some((item) => item.id === "n1")) {
        throw new Error("a successful resolution must clear the need");
    }

    // ─── 5b. Already-resolved approval 404 drops the need; no restore, no throw ───
    queue = [approvalRow("stale-appr", "a1")];
    await needs.refresh();
    await drain();
    const staleNeed = store.getState().needs.find((item) => item.id === "stale-appr");
    if (!staleNeed) throw new Error("stale approval must reach the queue");
    resolveGone = true;
    queue = [];
    let staleThrew = false;
    try {
        await needs.resolve(staleNeed, staleNeed.actions[0]);
    } catch (err) {
        staleThrew = true;
    }
    await drain();
    if (staleThrew) throw new Error("a gone/already-resolved approval must not reject");
    if (store.getState().needs.some((item) => item.id === "stale-appr")) {
        throw new Error("a gone approval must leave the queue, not restore Approve/Reject");
    }
    resolveGone = false;
    const staleAlreadyResolvedDropsNeed = true;

    // ─── 6. A failed refresh keeps the last good list ───
    // Blanking the bell because one fetch failed tells the operator nothing
    // needs them, which is a lie.

    queue = [consentRow("n2"), Object.assign(consentRow("n3"), { sub: "other/path" })];
    await needs.refresh();
    await drain();
    if (store.getState().needs.filter((item) => item.kind === "consent").length !== 2) {
        throw new Error("the queue must reload after a successful refresh");
    }
    queueFails = true;
    await needs.refresh();
    await drain();
    if (store.getState().needs.filter((item) => item.kind === "consent").length !== 2) {
        throw new Error("a failed refresh must keep the last good list");
    }
    if (!needs.getError()) {
        throw new Error("a failed refresh must surface a message, not only log one");
    }
    queueFails = false;
    await needs.refresh();
    await drain();
    if (needs.getError() !== "") throw new Error("a recovered refresh must clear the error line");
    const keepsQueueOnFailedRefresh = true;

    // ─── 7. The popover shows a failed resolution instead of eating it ───
    // The store restoring the need is only half of the safety property; the
    // operator has to SEE it. Nothing else asserts that half.

    queue = [consentRow("n4", "n4-conv")];
    await needs.refresh();
    await drain();

    const store2Baseline = store.subscriberCount();
    const bell = documentStub.createElement("button");
    documentStub.body.append(bell);
    bell.focus();
    const navigatedTo = [];
    let closes = 0;

    const popover = BossModNeedsPopover.openPopover({
        store,
        needs,
        anchor: bell,
        navigate: (placeId) => navigatedTo.push(placeId),
        onClose: () => { closes += 1; },
    });
    await drain();

    const entry = () => popover.element.querySelector('[data-need-id="n4"]');
    if (!entry()) throw new Error("the popover must list the open need");
    if (popover.element.getAttribute("role") !== "dialog") {
        throw new Error("the popover must be a dialog");
    }
    if (!String(popover.element.getAttribute("aria-label")).includes("need")) {
        throw new Error(`the dialog must name the count, got "${popover.element.getAttribute("aria-label")}"`);
    }

    resolveFails = true;
    const actionBtn = entry().querySelectorAll(".popover-action")[0];
    if (!actionBtn) throw new Error("the popover must render the server-described actions");
    if (actionBtn.textContent !== "Allow once") {
        throw new Error(`the action label comes from the server, got "${actionBtn.textContent}"`);
    }
    await actionBtn.dispatchClick();
    await drain();

    const failed = entry();
    if (!failed) throw new Error("a failed resolution must leave the entry in the popover");
    const errorLine = failed.querySelector(".popover-need-error");
    if (!errorLine || !errorLine.textContent.includes("Consent already answered.")) {
        throw new Error("a failed resolution must show its reason on the entry");
    }
    if (failed.querySelectorAll(".popover-action")[0].disabled !== false) {
        throw new Error("the action must be operable again after a failure");
    }
    const popoverShowsResolutionFailure = true;

    // ─── 8. The footer toggle silences the bar, never the bell ───

    const toggle = popover.element.querySelector("#needs-bar-toggle");
    if (!toggle) throw new Error("the popover footer must offer the composer-bar toggle");
    const needsBefore = store.getState().needs.length;
    toggle.checked = false;
    [...(toggle.listeners.change || [])].forEach((fn) => fn({ target: toggle }));
    if (store.getState().needsBarEnabled !== false) {
        throw new Error("the footer toggle must write needsBarEnabled");
    }
    if (store.getState().needs.length !== needsBefore) {
        throw new Error("suppressing the bar must not remove anything from the queue the bell counts");
    }
    const barToggleNeverHidesTheBell = true;

    // ─── 9. Esc closes, returns focus to the bell, and drains ───

    documentStub._activeElement = null;
    pressKey("Escape");
    if (closes !== 1) throw new Error("Esc must notify the opener so the bell can toggle");
    if (documentStub.activeElement !== bell) {
        throw new Error("closing must return focus to the bell");
    }
    if (popover.element.parent) throw new Error("closing must remove the popover from the document");
    if (store.subscriberCount() !== store2Baseline) {
        throw new Error(`popover store leak: baseline ${store2Baseline}, now ${store.subscriberCount()}`);
    }
    const escClosesAndReturnsFocus = true;
    resolveFails = false;

    // ─── 9a. "Show me" switches the conversation without remounting Chat ───
    // Navigating to Chat while already there takes the transcript cache, the
    // composer draft and the caret with it. shell/roster.js has always known
    // this; every other way into a conversation has to know it too.

    store.setState({ place: "chat", conversationId: null, conversationKind: null });
    navigatedTo.length = 0;
    const showMe = popover.element.querySelector(".popover-show-me");
    if (!showMe) throw new Error("a need with a conversation must offer Show me");
    await showMe.dispatchClick();
    if (store.getState().conversationId !== "n4-conv") {
        throw new Error(`Show me must switch the conversation, got ${store.getState().conversationId}`);
    }
    if (navigatedTo.length !== 0) {
        throw new Error("Show me must not remount Chat when Chat is already open");
    }
    if (closes !== 1) throw new Error("Show me must close the popover");
    const showMeKeepsShell = true;

    // From anywhere else it does navigate — and only after closing, so focus
    // lands on the new place rather than being pulled back to the bell.
    const popover2 = BossModNeedsPopover.openPopover({
        store, needs, anchor: bell,
        navigate: (placeId) => navigatedTo.push(placeId),
        onClose: () => { closes += 1; },
    });
    await drain();
    store.setState({ place: "tasks" });
    await popover2.element.querySelector(".popover-show-me").dispatchClick();
    if (navigatedTo.join(",") !== "chat") {
        throw new Error(`Show me from another place must navigate, got ${navigatedTo.join(",")}`);
    }

    // ─── 9b. An inspection action decides nothing, and acknowledges an error ───
    // Two deliberate behaviours that nothing else would catch: a GET action is
    // "Open task" / "Open diagnostics", so it must NOT optimistically remove
    // the entry — that would tell the operator they had settled something they
    // had only looked at. And an error need is client-held, so resolving it is
    // the only thing that can ever clear it.

    queue = [];
    await needs.refresh();
    await drain();
    bus.publish("diagnostic", {
        id: "d2", agent_id: "a2", agent_name: "Laura", status: "success",
        error: "Tool call rejected", created_at: "2026-09-07T12:30:00Z",
    });
    await drain();
    const errorEntry = store.getState().needs.find((item) => item.id === "d2");
    if (!errorEntry) throw new Error("the error need must be in the queue");
    if (errorEntry.actions[0].method !== "GET") {
        throw new Error("Open diagnostics is an inspection, not a decision");
    }

    // A GET that FAILS must leave the entry exactly where it was.
    resolveFails = true;
    let inspectThrew = false;
    try {
        await needs.resolve(errorEntry, errorEntry.actions[0]);
    } catch (err) {
        inspectThrew = true;
    }
    await drain();
    if (!inspectThrew) throw new Error("a failed inspection must still reject");
    const stillThere = store.getState().needs.find((item) => item.id === "d2");
    if (!stillThere) throw new Error("a failed inspection must not remove the need");
    if (!stillThere.error) throw new Error("a failed inspection must be explained on the entry");
    resolveFails = false;

    // Reading the diagnostic acknowledges it; nothing else ever could.
    await needs.resolve(stillThere, stillThere.actions[0]);
    await drain();
    if (store.getState().needs.some((item) => item.id === "d2")) {
        throw new Error("resolving an error need must clear the client-held entry");
    }
    // ...and it stays gone: the server list never contained it.
    await needs.refresh();
    await drain();
    if (store.getState().needs.some((item) => item.id === "d2")) {
        throw new Error("an acknowledged error need must not come back on refresh");
    }
    const inspectionDoesNotResolve = true;

    // ─── 9c. A blocked task and an error turn lead to their place ───
    // Phase 2B performed the server-described GET and refreshed, because Tasks
    // and Log did not exist. They do now. The destination comes from ONE
    // mapping table in need-shape.js, so the bell cannot form one opinion about
    // where a blocked task lives and the bar another.

    queue = [blockedRow("t1", "a1")];
    await needs.refresh();
    await drain();
    const blocked = store.getState().needs.find((item) => item.id === "t1");
    if (!blocked) throw new Error("the blocked task must reach the queue");
    if (!blocked.target || blocked.target.place !== "tasks") {
        throw new Error(`a blocked need must lead to Tasks, got ${JSON.stringify(blocked.target)}`);
    }
    if (blocked.target.params.taskId !== "t1") {
        throw new Error("a blocked need must carry its task id to Tasks");
    }
    // The server-described action is untouched: it is still an inspecting GET.
    if (blocked.actions[0].method !== "GET" || blocked.actions[0].href !== "/api/tasks/t1") {
        throw new Error("the server-described actions must not change");
    }

    bus.publish("diagnostic", {
        id: "d9", agent_id: "a2", agent_name: "Laura", status: "success",
        error: "Tool call rejected", created_at: "2026-09-07T13:30:00Z",
    });
    await drain();
    const errored = store.getState().needs.find((item) => item.id === "d9");
    if (!errored.target || errored.target.place !== "log") {
        throw new Error(`an error need must lead to the log, got ${JSON.stringify(errored.target)}`);
    }
    if (errored.target.params.diagnosticId !== "d9") {
        throw new Error("an error need must carry its diagnostic id to the log");
    }

    // ...and the popover's "Show me" actually goes there, with the params.
    store.setState({ place: "chat" });
    navigatedTo.length = 0;
    const wentTo = [];
    const popover3 = BossModNeedsPopover.openPopover({
        store, needs, anchor: bell,
        navigate: (placeId, params) => wentTo.push({ placeId, params }),
        onClose: () => { closes += 1; },
    });
    await drain();
    const blockedEntry = popover3.element.querySelector('[data-need-id="t1"]');
    if (!blockedEntry) throw new Error("the popover must list the blocked need");
    await blockedEntry.querySelector(".popover-show-me").dispatchClick();
    if (wentTo.length !== 1 || wentTo[0].placeId !== "tasks") {
        throw new Error(`Show me on a blocked need must open Tasks, got ${JSON.stringify(wentTo)}`);
    }
    if (wentTo[0].params.taskId !== "t1") {
        throw new Error("Show me must carry the task id into placeParams");
    }
    const targetsNavigate = true;

    queue = [];
    await needs.refresh();
    await drain();
    await needs.resolve(errored, errored.actions[0]);
    await drain();

    // ─── 10. Toast and bar are mutually exclusive, per need ───
    // Spec 5.5. Enforced here rather than left to judgement: if you are
    // looking at the conversation the bar tells you quietly, and if you are
    // not the toast comes to you. Never both.

    const store2 = BossModStore.createStore({
        needs: [],
        conversationId: null,
        conversationKind: null,
        needsBarEnabled: true,
        needsBarDismissed: false,
        inlineNeedIds: [],
    });
    const bus2 = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    let queue2 = [];
    const api2 = (url) => {
        if (url.startsWith("/api/needs")) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve(queue2) });
        }
        return Promise.resolve({ ok: true, text: () => Promise.resolve("") });
    };

    // Three pending needs are already on the server when the app launches.
    queue2 = [blockedRow("q1", "a1"), blockedRow("q2", "a1"), blockedRow("q3", "b1")];
    const needs2 = BossModNeeds.createNeedsStore({ store: store2, bus: bus2, api: api2 });
    const toastHost = BossModNeedsToast.createToastHost({
        store: store2, needs: needs2, navigate: () => {},
    });
    const barNavigations = [];
    const bar = BossModNeedsBar.createNeedsBar({
        store: store2,
        needs: needs2,
        navigate: (placeId, params) => barNavigations.push({ placeId, params }),
    });
    documentStub.body.append(bar.element);
    await drain();

    const toasts = () => documentStub.body.querySelectorAll(".toast");
    const barCards = () => bar.element.querySelectorAll(".event-card");

    // ─── 11. The boot snapshot is a baseline and toasts nothing ───

    if (store2.getState().needs.length !== 3) {
        throw new Error("the boot refresh must publish the three pending needs");
    }
    if (toasts().length !== 0) {
        throw new Error(`launching with three pending needs produced ${toasts().length} toasts`);
    }
    const noToastOnFirstSnapshot = true;

    // Open a1's conversation: q1 and q2 belong to it, q3 does not.
    store2.setState({ conversationId: "a1", conversationKind: "agent" });
    if (barCards().length !== 2) {
        throw new Error(`the bar shows only the open conversation, got ${barCards().length}`);
    }
    if (bar.element.hidden !== false) throw new Error("the bar must be visible when it has rows");

    // A new need for the OPEN conversation: bar, no toast.
    queue2 = queue2.concat([blockedRow("q4", "a1")]);
    await needs2.refresh();
    await drain();
    if (barCards().length !== 3) throw new Error("an arrival for the open conversation joins the bar");
    if (toasts().length !== 0) {
        throw new Error("a need whose conversation is open must not also toast");
    }

    // A new need for a DIFFERENT conversation: toast, no bar row.
    queue2 = queue2.concat([blockedRow("q5", "b1")]);
    await needs2.refresh();
    await drain();
    if (toasts().length !== 1) {
        throw new Error(`a need whose conversation is closed must toast, got ${toasts().length}`);
    }
    if (barCards().length !== 3) {
        throw new Error("a need for another conversation must not appear in this bar");
    }
    const suppressionRuleHolds = true;

    // ─── 12. A resync re-establishes the baseline ───
    // What comes back after an outage is a re-read, not a set of arrivals.

    [...documentStub.body.querySelectorAll(".toast")].forEach((node) => node.remove());
    queue2 = [blockedRow("r1", "b1"), blockedRow("r2", "b1"), blockedRow("r3", "b1")];
    bus2.publish("resync", { downtimeMs: 4000 });
    await drain();
    if (store2.getState().needs.length !== 3) throw new Error("resync must re-read the queue");
    if (toasts().length !== 0) {
        throw new Error(`a resync re-read produced ${toasts().length} toasts`);
    }
    // ...and a genuine arrival after it still toasts.
    queue2 = queue2.concat([blockedRow("r4", "b1")]);
    await needs2.refresh();
    await drain();
    if (toasts().length !== 1) {
        throw new Error("an arrival after a resync must still toast");
    }
    const resyncIsABaselineNotArrivals = true;

    // ─── 12b. A baseline that changed nothing still ends ───
    // baselinePending is cleared in recompute() rather than publish(), because
    // an unchanged signature returns early. If it were cleared in publish, a
    // resync whose queue came back identical would leave the flag set and
    // swallow the NEXT genuine arrival.

    [...documentStub.body.querySelectorAll(".toast")].forEach((node) => node.remove());
    bus2.publish("resync", { downtimeMs: 1000 });
    await drain();
    if (store2.getState().needs.length !== 4) {
        throw new Error("the unchanged resync must leave the queue as it was");
    }
    if (toasts().length !== 0) throw new Error("an unchanged resync must toast nothing");
    queue2 = queue2.concat([blockedRow("r5", "b1")]);
    await needs2.refresh();
    await drain();
    if (toasts().length !== 1) {
        throw new Error("an arrival after an unchanged resync must still toast");
    }
    const unchangedBaselineStillEnds = true;
    [...documentStub.body.querySelectorAll(".toast")].forEach((node) => node.remove());

    // ─── 13. A consent stays inline; the bar does not repeat it ───
    // The transcript of the open conversation already carries the consent card
    // as a `request`, so a bar row would be the same ask twice in one place.

    store2.setState({ conversationId: "a1", conversationKind: "agent", inlineNeedIds: ["c1"] });
    queue2 = [consentRow("c1")];
    await needs2.refresh();
    await drain();
    if (store2.getState().needs.length !== 1) throw new Error("the consent must reach the queue");
    if (barCards().length !== 0 || bar.element.hidden !== true) {
        throw new Error("a consent already inline in the transcript must not repeat in the bar");
    }
    const barLeavesConsentInline = true;

    // ─── 13b. A CLI approval stays inline; the bar does not repeat it ───
    store2.setState({ inlineNeedIds: ["c2"] });
    queue2 = [approvalRow("c2")];
    await needs2.refresh();
    await drain();
    if (store2.getState().needs.length !== 1) throw new Error("the approval must reach the queue");
    if (barCards().length !== 0 || bar.element.hidden !== true) {
        throw new Error("an approval already inline in the transcript must not repeat in the bar");
    }
    const barLeavesApprovalInline = true;

    // ─── 13c. A pending approval with no inline card still shows on the bar ───
    store2.setState({ conversationId: "a1", conversationKind: "agent", inlineNeedIds: [] });
    queue2 = [approvalRow("c-missing", "a1")];
    await needs2.refresh();
    await drain();
    if (bar.element.hidden !== false) {
        throw new Error("a pending approval with no inline card must appear on the bar");
    }
    if ((store2.getState().needs[0] || {}).id !== "c-missing") {
        throw new Error("the live approval id must replace a coalesced same-command row");
    }
    if (barCards().length !== 1) {
        throw new Error(`expected one fallback approval card, got ${barCards().length}`);
    }
    const fallbackLabels = bar.element.querySelectorAll("button")
        .map((node) => node.textContent)
        .filter((label) => label === "Approve" || label === "Reject");
    if (!fallbackLabels.includes("Approve") || !fallbackLabels.includes("Reject")) {
        throw new Error(`fallback approval bar must offer Approve/Reject, got ${fallbackLabels.join(",")}`);
    }
    const barShowsApprovalWhenInlineMissing = true;

    // ─── 13d. Duplicate pending CLI Approves for the same command collapse ───
    store2.setState({ conversationId: "a1", conversationKind: "agent", inlineNeedIds: [] });
    queue2 = [
        Object.assign(approvalRow("dup-old", "a1"), { created_at: "2026-09-07T12:00:00Z" }),
        Object.assign(approvalRow("dup-new", "a1"), { created_at: "2026-09-07T12:01:00Z" }),
    ];
    await needs2.refresh();
    await drain();
    const dupApprovals = store2.getState().needs.filter((item) => item.kind === "approval");
    if (dupApprovals.length !== 1) {
        throw new Error(`duplicate CLI Approves must be one card, got ${dupApprovals.length}`);
    }
    if (dupApprovals[0].id !== "dup-new") {
        throw new Error(`the live duplicate must be the newest row, got ${dupApprovals[0].id}`);
    }
    const duplicateApprovalsCoalesce = true;

    // ─── 13e. Thread-originated CLI Approve still lands on the agent's Focus bar ───
    // Origin-thread chrome must not leave Jim's composer with only "blocked"
    // while Approve lives solely in the bell.
    store2.setState({ conversationId: "a1", conversationKind: "agent", inlineNeedIds: [] });
    queue2 = [Object.assign(approvalRow("thread-appr", "th1"), {
        sub: 'pip install -e ".[dev]"',
        created_at: "2026-09-07T12:02:00Z",
    })];
    await needs2.refresh();
    await drain();
    if (bar.element.hidden !== false) {
        throw new Error("a thread-originated pending approval must appear on the agent's Focus bar");
    }
    const threadFallbackLabels = bar.element.querySelectorAll("button")
        .map((node) => node.textContent)
        .filter((label) => label === "Approve" || label === "Reject");
    if (!threadFallbackLabels.includes("Approve") || !threadFallbackLabels.includes("Reject")) {
        throw new Error(`Focus fallback for a thread card must offer Approve/Reject, got ${threadFallbackLabels.join(",")}`);
    }
    const barShowsThreadApprovalOnAgentFocus = true;

    // ─── 13f. Coalesced live id stays off the bar when a sibling is inline ───
    store2.setState({ conversationId: "a1", conversationKind: "agent", inlineNeedIds: ["dup-old"] });
    queue2 = [
        Object.assign(approvalRow("dup-old", "a1"), { created_at: "2026-09-07T12:00:00Z" }),
        Object.assign(approvalRow("dup-new", "a1"), { created_at: "2026-09-07T12:01:00Z" }),
    ];
    await needs2.refresh();
    await drain();
    const coalesced = store2.getState().needs.filter((item) => item.kind === "approval");
    if (coalesced.length !== 1 || coalesced[0].id !== "dup-new") {
        throw new Error("13f must still coalesce to the newest pending id");
    }
    if (barCards().length !== 0 || bar.element.hidden !== true) {
        throw new Error("Needs-bar must stay quiet when a coalesced sibling is already inline");
    }
    const barSuppressesCoalescedSibling = true;

    // ─── 13g. Consent fallback, then quiet, plus coalesced sibling ───
    store2.setState({ conversationId: "a1", conversationKind: "agent", inlineNeedIds: [] });
    queue2 = [consentRow("c-missing", "a1")];
    await needs2.refresh();
    await drain();
    if (bar.element.hidden !== false) {
        throw new Error("a pending consent with no inline card must appear on the bar");
    }
    const consentFallbackLabels = bar.element.querySelectorAll("button")
        .map((node) => node.textContent)
        .filter((label) => label === "Allow once" || label === "Deny");
    if (!consentFallbackLabels.includes("Allow once") || !consentFallbackLabels.includes("Deny")) {
        throw new Error(`fallback consent bar must offer Allow once/Deny, got ${consentFallbackLabels.join(",")}`);
    }
    const barShowsConsentWhenInlineMissing = true;

    store2.setState({ inlineNeedIds: ["c-old"] });
    queue2 = [
        Object.assign(consentRow("c-old", "a1"), { created_at: "2026-09-07T12:00:00Z" }),
        Object.assign(consentRow("c-new", "a1"), { created_at: "2026-09-07T12:01:00Z" }),
    ];
    await needs2.refresh();
    await drain();
    const dupConsents = store2.getState().needs.filter((item) => item.kind === "consent");
    if (dupConsents.length !== 1) {
        throw new Error(`duplicate host-path consents must be one card, got ${dupConsents.length}`);
    }
    if (bar.element.hidden !== true) {
        throw new Error("Needs-bar must stay quiet when a coalesced consent sibling is inline");
    }
    const duplicateConsentsCoalesce = true;

    queue2 = [{
        id: "shell-1",
        kind: "consent",
        card_kind: "shell_executor",
        agent_id: "a1",
        agent_name: "Jim",
        title: "Jim needs Shell Executor for validate-on-clone — enable or deny",
        sub: "pytest -q",
        created_at: "2026-09-07T12:03:00Z",
        conversation_id: "a1",
        grouped_ids: ["shell-1"],
        actions: [
            { label: "Turn on Shell Executor (company-wide)", method: "POST", tone: "primary",
              href: "/api/shell-executor/shell-1/enable" },
            { label: "Deny — Shell Executor stays off", method: "POST", tone: "quiet",
              href: "/api/shell-executor/shell-1/deny" },
        ],
    }];
    store2.setState({ inlineNeedIds: [] });
    await needs2.refresh();
    await drain();
    const shellNeed = store2.getState().needs[0];
    const projected = BossModNeedShape.requestMessageFromNeed(shellNeed);
    if (!projected || !projected.card || projected.card.kind !== "shell_executor") {
        throw new Error("need-queue live paint must carry Shell Executor card kind");
    }
    const shellNeedPaintsEnableKind = true;

    // ─── 13h. Nest git Needs: POST body, Nest git label, schema mismatch → Dismiss ───
    const store3 = BossModStore.createStore({ needs: [] });
    const bus3 = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const nestCalls = [];
    let nestQueue = [nestGitRow("ng1")];
    let nestSchema = false;
    const nestApi = (url, init) => {
        nestCalls.push({ url, method: (init && init.method) || "GET", body: init && init.body });
        if (url.startsWith("/api/needs")) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve(nestQueue) });
        }
        if (nestSchema) {
            return Promise.resolve({
                ok: false,
                status: 422,
                text: () => Promise.resolve(JSON.stringify({
                    detail: [{ type: "missing", loc: ["body"], msg: "Field required", input: null }],
                })),
            });
        }
        return Promise.resolve({ ok: true, text: () => Promise.resolve("") });
    };
    const nestNeeds = BossModNeeds.createNeedsStore({ store: store3, bus: bus3, api: nestApi });
    await drain();
    const nestNeed = store3.getState().needs.find((item) => item.id === "ng1");
    if (!nestNeed) throw new Error("nest git need must reach the queue");
    nestCalls.length = 0;
    await nestNeeds.resolve(nestNeed, nestNeed.actions[0]);
    await drain();
    const enableCall = nestCalls.find((item) => String(item.url).endsWith("/enable"));
    if (!enableCall || enableCall.body !== "{}") {
        throw new Error(`Enable must POST {{}}, got ${JSON.stringify(enableCall)}`);
    }
    nestQueue = [nestGitRow("ng1")];
    await nestNeeds.refresh();
    await drain();
    const nestNeed2 = store3.getState().needs.find((item) => item.id === "ng1");
    nestCalls.length = 0;
    await nestNeeds.resolve(nestNeed2, nestNeed2.actions[2]);
    await drain();
    const useCall = nestCalls.find((item) => String(item.url).endsWith("/use"));
    if (!useCall || useCall.body !== JSON.stringify({ credential_id: "acme" })) {
        throw new Error(`Use must POST credential_id, got ${JSON.stringify(useCall)}`);
    }
    nestQueue = [nestGitRow("ng1")];
    await nestNeeds.refresh();
    await drain();
    const nestNeed3 = store3.getState().needs.find((item) => item.id === "ng1");
    nestCalls.length = 0;
    await nestNeeds.resolve(nestNeed3, nestNeed3.actions[1]);
    await drain();
    const credCall = nestCalls.find((item) => String(item.url).endsWith("/credentials"));
    if (!credCall || credCall.body !== "{}") {
        throw new Error(`Add token must POST {{}}, got ${JSON.stringify(credCall)}`);
    }
    const nestGitPostsBody = true;

    nestQueue = [nestGitRow("ng-stale")];
    await nestNeeds.refresh();
    await drain();
    const staleNest = store3.getState().needs.find((item) => item.id === "ng-stale");
    nestSchema = true;
    let nestThrew = false;
    try {
        await nestNeeds.resolve(staleNest, {
            label: "Add a GitHub access token or SSH key",
            href: "/api/nest-git/ng-stale/credentials",
            method: "POST",
            tone: "default",
        });
    } catch (err) {
        nestThrew = true;
    }
    await drain();
    const staleAfter = store3.getState().needs.find((item) => item.id === "ng-stale");
    const staleError = staleAfter && staleAfter.error ? String(staleAfter.error) : "";
    const staleLabels = staleAfter ? staleAfter.actions.map((item) => item.label) : [];
    if (nestThrew) throw new Error("schema-mismatched nest git must not reject");
    if (!staleAfter) throw new Error("schema-mismatched nest git must stay as Dismiss, not vanish");
    if (staleLabels.join(",") !== "Dismiss") {
        throw new Error(`schema mismatch must be Dismiss only, got ${JSON.stringify(staleLabels)}`);
    }
    if (staleError.includes("Field required") || staleError.includes("detail")) {
        throw new Error(`schema mismatch must not show raw JSON, got ${staleError}`);
    }
    nestSchema = false;
    const nestGitSchemaMismatchDismisses = true;

    const nestBell = documentStub.createElement("button");
    documentStub.body.append(nestBell);
    nestQueue = [nestGitRow("ng-label")];
    await nestNeeds.refresh();
    await drain();
    const nestPopover = BossModNeedsPopover.openPopover({
        store: store3, needs: nestNeeds, anchor: nestBell, navigate: () => {},
    });
    await drain();
    const groupTitles = nestPopover.element.querySelectorAll(".popover-group-title")
        .map((node) => node.textContent);
    if (!groupTitles.some((title) => title.startsWith("Nest git"))) {
        throw new Error(`nest git group must be labeled Nest git, got ${JSON.stringify(groupTitles)}`);
    }
    if (groupTitles.some((title) => title.startsWith("Folder access"))) {
        throw new Error(`nest git must not sit under Folder access, got ${JSON.stringify(groupTitles)}`);
    }
    nestPopover.close();
    nestNeeds.destroy();
    const nestGitGroupIsLabeled = true;

    // ─── 14. Suppressing the bar hides it without touching the queue ───

    queue2 = [blockedRow("s1", "a1")];
    await needs2.refresh();
    await drain();
    if (bar.element.hidden !== false) throw new Error("the bar must show an open-conversation need");
    store2.setState({ needsBarDismissed: true });
    if (bar.element.hidden !== true) throw new Error("dismissing must hide the bar");
    if (store2.getState().needs.length !== 1) {
        throw new Error("dismissing the bar must not remove anything the bell counts");
    }
    store2.setState({ needsBarDismissed: false, needsBarEnabled: false });
    if (bar.element.hidden !== true) throw new Error("disabling must hide the bar");
    store2.setState({ needsBarEnabled: true });
    if (bar.element.hidden !== false) throw new Error("re-enabling must show it again");

    // The bar reads the same targets. A blocked need in the open conversation
    // still offers a way to Tasks; a CLI approval whose target IS the
    // conversation this bar is pinned to is omitted from the bar, because the
    // transcript already carries that ask as a `request`.
    barNavigations.length = 0;
    store2.setState({ inlineNeedIds: ["s2"] });
    queue2 = [approvalRow("s2", "a1"), blockedRow("s3", "a1")];
    await needs2.refresh();
    await drain();
    const barButtons = () => bar.element.querySelectorAll("button")
        .filter((node) => node.textContent === "Show me");
    if (barButtons().length !== 1) {
        throw new Error(`the bar must offer exactly one Show me, got ${barButtons().length}`);
    }
    await barButtons()[0].dispatchClick();
    if (barNavigations.length !== 1 || barNavigations[0].placeId !== "tasks") {
        throw new Error(`the bar's Show me must open Tasks, got ${JSON.stringify(barNavigations)}`);
    }
    if (barNavigations[0].params.taskId !== "s3") {
        throw new Error("the bar's Show me must carry the task id");
    }
    queue2 = [blockedRow("s1", "a1")];
    await needs2.refresh();
    await drain();

    const bar2Store = store2.subscriberCount();
    bar.destroy();
    toastHost.destroy();
    needs2.destroy();
    if (store2.subscriberCount() >= bar2Store) {
        throw new Error("the bar must drain its subscriptions on destroy");
    }
    if (bus2.subscriberCount() !== 0) {
        throw new Error(`bus2 leak: ${bus2.subscriberCount()} subscriptions left`);
    }

    // ─── 15. Disposers drain ───

    needs.destroy();
    if (bus.subscriberCount() !== busBaseline) {
        throw new Error(`bus leak: baseline ${busBaseline}, now ${bus.subscriberCount()}`);
    }
    if (store.subscriberCount() !== storeBaseline + 1) {
        throw new Error("the harness's own counter is the only store subscription left");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        normalisesShape,
        quietOnUnchangedTick,
        restoresOnFailedResolve,
        staleAlreadyResolvedDropsNeed,
        keepsQueueOnFailedRefresh,
        errorNeedFromDiagnostic,
        identicalErrorCardsCoalesce,
        ignoresUnknownActivity,
        popoverShowsResolutionFailure,
        barToggleNeverHidesTheBell,
        escClosesAndReturnsFocus,
        showMeKeepsShell,
        suppressionRuleHolds,
        noToastOnFirstSnapshot,
        resyncIsABaselineNotArrivals,
        unchangedBaselineStillEnds,
        inspectionDoesNotResolve,
        barLeavesConsentInline,
        barLeavesApprovalInline,
        barShowsApprovalWhenInlineMissing,
        duplicateApprovalsCoalesce,
        barShowsThreadApprovalOnAgentFocus,
        barSuppressesCoalescedSibling,
        barShowsConsentWhenInlineMissing,
        duplicateConsentsCoalesce,
        shellNeedPaintsEnableKind,
        nestGitPostsBody,
        nestGitSchemaMismatchDismisses,
        nestGitGroupIsLabeled,
        targetsNavigate,
        openFocusNeedTableHolds,
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
