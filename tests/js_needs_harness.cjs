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
    domPath, storePath, busPath, gatesPath, formatPath, cardsPath, shapePath,
    needsPath, popoverPath, barPath, toastPath,
] = process.argv.slice(2);
load(domPath, "BossModDom");
load(storePath, "BossModStore");
load(busPath, "BossModBus");
// needs-store.js guards refresh() with the shared load generation.
load(gatesPath, "BossModGates");
load(formatPath, "BossModFormat");
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
        actions: [
            { label: "Approve", method: "POST", tone: "primary",
              href: `/api/cli-policy/approvals/${id}/approve` },
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

    function api(url, init) {
        calls.push({ url, method: (init && init.method) || "GET" });
        if (url.startsWith("/api/needs")) {
            if (queueFails) return Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve("down") });
            return Promise.resolve({ ok: true, json: () => Promise.resolve(queue) });
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

    // ─── 6. A failed refresh keeps the last good list ───
    // Blanking the bell because one fetch failed tells the operator nothing
    // needs them, which is a lie.

    queue = [consentRow("n2"), consentRow("n3")];
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
    store.setState({ place: "board" });
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
    // Phase 2B performed the server-described GET and refreshed, because Board
    // and Log did not exist. They do now. The destination comes from ONE
    // mapping table in need-shape.js, so the bell cannot form one opinion about
    // where a blocked task lives and the bar another.

    queue = [blockedRow("t1", "a1")];
    await needs.refresh();
    await drain();
    const blocked = store.getState().needs.find((item) => item.id === "t1");
    if (!blocked) throw new Error("the blocked task must reach the queue");
    if (!blocked.target || blocked.target.place !== "board") {
        throw new Error(`a blocked need must lead to the board, got ${JSON.stringify(blocked.target)}`);
    }
    if (blocked.target.params.taskId !== "t1") {
        throw new Error("a blocked need must carry its task id to the board");
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
    if (wentTo.length !== 1 || wentTo[0].placeId !== "board") {
        throw new Error(`Show me on a blocked need must open the board, got ${JSON.stringify(wentTo)}`);
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
    queue2 = [approvalRow("q1", "a1"), approvalRow("q2", "a1"), approvalRow("q3", "b1")];
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
    queue2 = queue2.concat([approvalRow("q4", "a1")]);
    await needs2.refresh();
    await drain();
    if (barCards().length !== 3) throw new Error("an arrival for the open conversation joins the bar");
    if (toasts().length !== 0) {
        throw new Error("a need whose conversation is open must not also toast");
    }

    // A new need for a DIFFERENT conversation: toast, no bar row.
    queue2 = queue2.concat([approvalRow("q5", "b1")]);
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
    queue2 = [approvalRow("r1", "b1"), approvalRow("r2", "b1"), approvalRow("r3", "b1")];
    bus2.publish("resync", { downtimeMs: 4000 });
    await drain();
    if (store2.getState().needs.length !== 3) throw new Error("resync must re-read the queue");
    if (toasts().length !== 0) {
        throw new Error(`a resync re-read produced ${toasts().length} toasts`);
    }
    // ...and a genuine arrival after it still toasts.
    queue2 = queue2.concat([approvalRow("r4", "b1")]);
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
    queue2 = queue2.concat([approvalRow("r5", "b1")]);
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

    store2.setState({ conversationId: "a1", conversationKind: "agent" });
    queue2 = [consentRow("c1")];
    await needs2.refresh();
    await drain();
    if (store2.getState().needs.length !== 1) throw new Error("the consent must reach the queue");
    if (barCards().length !== 0 || bar.element.hidden !== true) {
        throw new Error("a consent already inline in the transcript must not repeat in the bar");
    }
    const barLeavesConsentInline = true;

    // ─── 14. Suppressing the bar hides it without touching the queue ───

    queue2 = [approvalRow("s1", "a1")];
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
    // still offers a way to the board; an approval whose target IS the
    // conversation this bar is pinned to offers nothing, because "show me"
    // pointing at the screen you are looking at is the noise spec 5.5 forbids.
    barNavigations.length = 0;
    queue2 = [approvalRow("s2", "a1"), blockedRow("s3", "a1")];
    await needs2.refresh();
    await drain();
    const barButtons = () => bar.element.querySelectorAll("button")
        .filter((node) => node.textContent === "Show me");
    if (barButtons().length !== 1) {
        throw new Error(`the bar must offer exactly one Show me, got ${barButtons().length}`);
    }
    await barButtons()[0].dispatchClick();
    if (barNavigations.length !== 1 || barNavigations[0].placeId !== "board") {
        throw new Error(`the bar's Show me must open the board, got ${JSON.stringify(barNavigations)}`);
    }
    if (barNavigations[0].params.taskId !== "s3") {
        throw new Error("the bar's Show me must carry the task id");
    }
    queue2 = [approvalRow("s1", "a1")];
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
        keepsQueueOnFailedRefresh,
        errorNeedFromDiagnostic,
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
        targetsNavigate,
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
