/**
 * Node harness: the conversation controller's lifecycle guarantees.
 * Invoked by tests/test_ui_conversation.py. Not a browser bundle.
 *
 * Every module is the real one — store, bus, gates, transcript, composer,
 * chrome, and both sources. A stub that drifts from the module it stands in
 * for is a test that proves nothing, and the properties here are exactly the
 * ones that break when two of these modules disagree.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
// The chrome paints its action glyphs after every apply.
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModFormat", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModOverlays", "BossModEmptyState",
    "BossModTranscript", "BossModTranscriptCache", "BossModMessage", "BossModEventCards",
    "BossModTitleRename", "BossModConversationChrome",
    "BossModComposer", "BossModSystemReceipts", "BossModNeedsBar", "BossModThreadArchive",
    "BossModThreadSource", "BossModAgentSource", "BossModConversation",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModConversation } = global;

// ─── A scripted API ───

const AGENT_MESSAGES = {
    a: [{ id: "a1", from: "agent", content: "from Ada", created_at: "" }],
    b: [{ id: "b1", from: "agent", content: "from Bo", created_at: "" }],
    c: [{ id: "c1", from: "agent", content: "from Cy", created_at: "" }],
    // Nobody has said anything to Di yet: the empty state is hers.
    d: [],
};
const delays = { a: 0, b: 0, c: 0, d: 0 };
const failing = new Set();
const requestLog = [];

// Mutable on purpose: a rename is only proven if reading the thread back shows
// the new name, rather than the view painting what the operator typed.
const THREADS = {
    t1: { id: "t1", name: "Standup", status: "active", members: [{ id: "m1", name: "Ada" }] },
    // A sealed room takes no writes, and a rename is a write.
    t2: { id: "t2", name: "Old room", status: "archived", members: [{ id: "m1", name: "Ada" }] },
    // Somewhere to switch to mid-rename.
    t3: { id: "t3", name: "Design sync", status: "active", members: [{ id: "m1", name: "Ada" }] },
};
const RENAME_FAILURE = "Thread name cannot be empty";
const renamePayloads = [];
let renameFails = false;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tick = () => new Promise((resolve) => setImmediate(resolve));

const activations = [];
const api = async (url, init) => {
    const text = String(url);
    requestLog.push(text);
    const activate = text.match(/^\/api\/agents\/([^/]+)\/activate$/);
    if (activate) {
        activations.push({ id: activate[1], body: JSON.parse((init && init.body) || "{}") });
        return { ok: true, async json() { return {}; } };
    }
    const agent = text.match(/^\/api\/agents\/([^/]+)\/messages/);
    if (agent) {
        const id = agent[1];
        if (delays[id]) await wait(delays[id]);
        if (failing.has(id)) {
            return { ok: false, status: 500, async text() { return "Agent chat is unavailable."; } };
        }
        return { ok: true, async json() { return AGENT_MESSAGES[id] || []; } };
    }
    const thread = text.match(/^\/api\/channels\/([^/?]+)$/);
    if (thread) {
        const room = THREADS[thread[1]];
        if (!room) return { ok: false, status: 404, async text() { return "Thread not found"; } };
        if (init && init.method === "PATCH") {
            renamePayloads.push(JSON.parse(init.body));
            if (renameFails) {
                return { ok: false, status: 400, async text() { return RENAME_FAILURE; } };
            }
            // The server is what decides the name; the view adopts what comes
            // back rather than what was typed.
            room.name = String(JSON.parse(init.body).name).trim();
            return { ok: true, async json() { return { ...room, members: room.members }; } };
        }
        return {
            ok: true,
            async json() {
                return { channel: { ...room, members: room.members }, messages: [] };
            },
        };
    }
    throw new Error(`unhandled ${text}`);
};

const store = BossModStore.createStore({
    roster: [
        { id: "a", name: "Ada", role: "Writer" },
        { id: "b", name: "Bo", role: "Reviewer" },
        { id: "c", name: "Cy", role: "Engineer" },
        { id: "d", name: "Di", role: "Designer", color: "#065f46" },
    ],
    threads: [],
    hasUsableModel: true,
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);

// The bar is part of the surface now; the queue behind it is exercised in
// js_needs_harness.cjs, so an empty one is enough to build the conversation.
const needsStub = {
    refresh: () => Promise.resolve(),
    resolve: () => Promise.resolve(),
    getError: () => "",
    subscribeError: () => () => {},
    destroy: () => {},
};

const openedDesks = [];
const conversation = BossModConversation.createConversation({
    store, bus, api, navigate() {}, needs: needsStub,
    // Injected so the chrome has an action to carry a glyph on.
    openDesk: (id) => openedDesks.push(id),
});
const listing = conversation.element.querySelector("[data-transcript]");
const composerInput = conversation.element.querySelector(".composer-input");

function bodies() {
    return conversation.element
        .querySelectorAll(".msg-body")
        .map((node) => node.textContent);
}

function status() {
    return conversation.element.querySelector(".transcript-status");
}

async function main() {
    // ─── staleLoadDropped ───
    // A response for a conversation the operator already left is discarded,
    // not painted. Ada is slow; Bo is asked for second and answers first.
    delays.a = 30;
    const slow = conversation.open("a", "agent");
    const fast = conversation.open("b", "agent");
    await Promise.all([slow, fast]);
    await wait(50);
    const staleLoadDropped = bodies().join("|") === "from Bo";
    if (!staleLoadDropped) throw new Error(`stale load painted: ${bodies().join("|")}`);
    delays.a = 0;

    // Prime Ada's cache with a clean load.
    await conversation.open("a", "agent");
    if (bodies().join("|") !== "from Ada") throw new Error("Ada must load");

    // ─── cacheSkipsLoading ───
    // A re-click paints from the cache with no flash of an empty room.
    await conversation.open("b", "agent");
    const pending = conversation.open("a", "agent");
    const paintedImmediately = bodies().join("|") === "from Ada";
    const cacheSkipsLoading = paintedImmediately && status() === null;
    if (!cacheSkipsLoading) {
        throw new Error(`cached re-open flashed: painted=${paintedImmediately}, status=${status() && status().className}`);
    }
    await pending;

    // ─── composerSurvivesSwitch ───
    // The composer node is never rebuilt, and each conversation keeps its own
    // half-typed draft.
    const composerBefore = conversation.element.querySelector(".composer-input");
    composerInput.value = "half a thought for Ada";
    await conversation.open("b", "agent");
    const sameNode = conversation.element.querySelector(".composer-input") === composerBefore;
    const clearedForBo = composerInput.value === "";
    await conversation.open("a", "agent");
    const composerSurvivesSwitch = sameNode && clearedForBo
        && composerInput.value === "half a thought for Ada";
    if (!composerSurvivesSwitch) {
        throw new Error(`draft handling wrong: same=${sameNode} bo="${clearedForBo}" back="${composerInput.value}"`);
    }
    composerInput.value = "";

    // ─── presenceSurvivesSwitch ───
    // Send in a thread, look elsewhere, come back: the indicator is still there.
    await conversation.open("t1", "thread");
    bus.publish("channel_presence", {
        channel_id: "t1", agent_id: "m1", agent_name: "Ada", phase: "thinking",
    });
    const paintedInThread = conversation.element
        .querySelectorAll(".transcript-presence-row").length === 1;
    await conversation.open("b", "agent");
    const goneWhileAway = conversation.element
        .querySelectorAll(".transcript-presence-row").length === 0;
    await conversation.open("t1", "thread");
    const backOnReturn = conversation.element
        .querySelectorAll(".transcript-presence-row")
        .map((row) => row.textContent).join("|") === "Ada is thinking...";
    const presenceSurvivesSwitch = paintedInThread && goneWhileAway && backOnReturn;
    if (!presenceSurvivesSwitch) {
        throw new Error(`presence did not survive: ${paintedInThread}/${goneWhileAway}/${backOnReturn}`);
    }

    // ─── unsubscribesPreviousSource ───
    // Switching drains the outgoing source, so traffic for the conversation
    // the operator left never reaches the transcript, and nothing accumulates.
    await conversation.open("a", "agent");
    const baseline = bus.subscriberCount();
    for (let i = 0; i < 20; i += 1) {
        await conversation.open("b", "agent");
        await conversation.open("a", "agent");
    }
    const noLeak = bus.subscriberCount() === baseline;
    bus.publish("chat_message", {
        agent_id: "b", content: "meant for Bo", from: "agent", message_id: "leak-1",
    });
    const unsubscribesPreviousSource = noLeak && !bodies().join("|").includes("meant for Bo");
    if (!unsubscribesPreviousSource) {
        throw new Error(`leak: baseline ${baseline}, now ${bus.subscriberCount()}, body ${bodies().join("|")}`);
    }
    // The live message for the OPEN conversation still arrives.
    bus.publish("chat_message", {
        agent_id: "a", content: "a live reply", from: "agent", message_id: "live-1",
    });
    if (!bodies().join("|").includes("a live reply")) {
        throw new Error("the open conversation must still receive live messages");
    }

    // ─── errorStateRetries ───
    // A failed load is an error state with a retry, never a blank pane.
    failing.add("c");
    await conversation.open("c", "agent");
    const errorNode = status();
    if (!errorNode || errorNode.getAttribute("role") !== "alert") {
        throw new Error("a failed load must render role=alert");
    }
    if (!errorNode.textContent.includes("Agent chat is unavailable.")) {
        throw new Error(`the error must say what failed: ${errorNode.textContent}`);
    }
    const retry = errorNode.querySelector(".transcript-retry");
    if (!retry) throw new Error("the error state must offer a retry");
    failing.delete("c");
    await retry.dispatchClick();
    await tick();
    await tick();
    const errorStateRetries = bodies().join("|") === "from Cy" && status() === null;
    if (!errorStateRetries) {
        throw new Error(`retry did not recover: ${bodies().join("|")}`);
    }

    // ─── The chrome carries the identity, its glyphs, and the receipts toggle ───
    //
    // The descriptor grew two optional fields rather than the view reaching for
    // the roster. That boundary is why the conversation never subscribes to
    // world_update, so it is asserted on what the view actually renders.
    await conversation.open("a", "agent");
    const avatarSlot = () => conversation.element.querySelector(".conversation-avatar");
    const chromeAvatar = () => avatarSlot().querySelector(".avatar");
    const identity = chromeAvatar();
    const chromeShowsIdentityAvatar = Boolean(identity)
        && identity.textContent === "A"
        && identity.getAttribute("aria-hidden") === "true";
    if (!chromeShowsIdentityAvatar) {
        throw new Error(`the chrome must name who you are talking to: ${identity && identity.textContent}`);
    }

    // A repaint that changes nothing about the identity must not churn the node.
    await conversation.open("a", "agent");
    const chromeAvatarNodeIsStable = chromeAvatar() === identity;
    if (!chromeAvatarNodeIsStable) throw new Error("a repaint rebuilt an unchanged avatar");

    // The Desk action names its glyph; the source hands over a NAME, and the
    // view is the only thing that builds an element from it.
    const deskBtn = conversation.element.querySelector("#conversation-desk-toggle");
    if (!deskBtn) throw new Error("an injected openDesk must produce the Desk action");
    const glyph = deskBtn.querySelector("i");
    const chromeActionCarriesItsIcon = Boolean(glyph)
        && glyph.getAttribute("data-lucide") === "lamp-desk"
        && deskBtn.textContent.includes("Desk");
    if (!chromeActionCarriesItsIcon) {
        throw new Error(`the Desk action must carry its glyph: ${deskBtn.textContent}`);
    }

    // A thread has no one face, so it gets the group glyph rather than nothing.
    await conversation.open("t1", "thread");
    const group = avatarSlot().querySelector(".avatar-group");
    const chromeGroupGlyphForThreads = Boolean(group)
        && avatarSlot().querySelectorAll(".avatar").length === 1;
    if (!chromeGroupGlyphForThreads) throw new Error("a thread must get the group glyph");

    // The receipts preference moved BEHIND the `⋯`. It is a preference about
    // the view, not an action on the person, and its 25-character label was
    // crowding the header's one real action. It is still a mount-time slot
    // rather than a field on a descriptor that changes with every
    // conversation, which is what lets it outlive a switch.
    const actionRow = conversation.element.querySelector(".conversation-actions");
    const headerHasNoReceiptsSwitch = actionRow.querySelectorAll(".switch-row").length === 0
        && conversation.element.querySelectorAll(".conversation-controls").length === 0;
    if (!headerHasNoReceiptsSwitch) {
        throw new Error("the receipts switch must not sit in the header row");
    }

    const dots = conversation.element.querySelector("#conversation-view-options");
    if (!dots) throw new Error("the header must offer a view-options menu");
    if (dots.getAttribute("aria-label") !== "View options") {
        throw new Error(`icon-only needs its own name, got "${dots.getAttribute("aria-label")}"`);
    }
    if (dots.getAttribute("aria-expanded") !== "false") {
        throw new Error("the `⋯` must report its panel as closed before it is opened");
    }
    await dots.dispatchClick();
    const panel = conversation.element.querySelector(".menu");
    const receiptsToggleReachableFromMenu = Boolean(panel)
        && panel.getAttribute("role") === "dialog"
        && panel.querySelectorAll(".switch-row").length === 1
        && dots.getAttribute("aria-expanded") === "true";
    if (!receiptsToggleReachableFromMenu) {
        throw new Error(`the receipts toggle must be reachable from the menu: `
            + `${panel && panel.getAttribute("role")}`);
    }

    // Toggling it from its new home writes the SAME storage key it always did.
    const receiptsNode = panel.querySelector(".switch-row");
    await receiptsNode.dispatchClick();
    const receiptsPreferencePersists =
        global.window.localStorage.getItem("bossmod.chat.showSystemReceipts") === "false"
        && receiptsNode.getAttribute("aria-checked") === "false";
    if (!receiptsPreferencePersists) {
        throw new Error(`the preference must persist from its new home, got `
            + `"${global.window.localStorage.getItem("bossmod.chat.showSystemReceipts")}"`);
    }
    await receiptsNode.dispatchClick();

    // Closing returns focus to the control that opened it, rather than
    // dropping it on the body behind.
    await dots.dispatchClick();
    const menuReturnsFocusToItsButton = conversation.element.querySelectorAll(".menu").length === 0
        && documentStub.activeElement === dots
        && dots.getAttribute("aria-expanded") === "false";
    if (!menuReturnsFocusToItsButton) {
        throw new Error("closing the menu must return focus to the `⋯`");
    }

    // Re-opening moves the SAME control back in rather than building a second
    // one, which is what keeps the preference it holds.
    await dots.dispatchClick();
    const receiptsNodeSurvivesReopen =
        conversation.element.querySelector(".menu").querySelector(".switch-row") === receiptsNode;
    if (!receiptsNodeSurvivesReopen) {
        throw new Error("re-opening the menu must reuse the preference control");
    }
    await dots.dispatchClick();

    // ...and it survives a conversation switch rather than being rebuilt with
    // the actions around it.
    await conversation.open("a", "agent");
    await dots.dispatchClick();
    if (conversation.element.querySelector(".menu").querySelector(".switch-row") !== receiptsNode) {
        throw new Error("the receipts toggle must outlive a conversation switch");
    }
    await dots.dispatchClick();

    // ─── The thread title renames in place ───
    //
    // Easy but non-obvious: at rest it is the title, and it is one control in
    // two states rather than a label that swaps for an input. Driven through
    // keys because "reachable by Tab, opened by Enter" is the half a click
    // test would never see (SC 2.1.1).
    await conversation.open("t1", "thread");
    const titleSlot = () => conversation.element.querySelector(".conversation-title");
    const titleInput = () => conversation.element.querySelector("#conversation-title-edit");
    const actionLabels = () => conversation.element
        .querySelector(".conversation-actions")
        .querySelectorAll("button")
        .map((btn) => btn.textContent)
        .filter((label) => label.length > 0);
    // Round four made the rename pair ICON-ONLY, so textContent no longer
    // names them: what a screen reader announces is the label for a text
    // button and the aria-label for an icon. The `⋯` belongs to the surface
    // rather than to any conversation and is not part of this row's meaning.
    const actionNames = () => conversation.element
        .querySelector(".conversation-actions")
        .querySelectorAll("button")
        .map((btn) => btn.textContent || btn.getAttribute("aria-label") || "")
        .filter((name) => name && name !== "View options");
    const RENAME_IDS = ["conversation-title-cancel", "conversation-title-save"];
    const renameButtons = () => conversation.element
        .querySelector(".conversation-actions")
        .querySelectorAll("button")
        .filter((btn) => RENAME_IDS.includes(btn.getAttribute("id")));
    const errorLine = () => conversation.element.querySelector(".composer-error").textContent;
    const press = (node, key) => (node.listeners.keydown || []).forEach((fn) => fn({
        key, preventDefault() {}, stopPropagation() {},
    }));

    if (!titleInput()) throw new Error("a live thread's title must be renameable");
    if (titleInput().getAttribute("data-editing") !== "false") {
        throw new Error("the title must be at rest until it is asked for");
    }
    if (titleInput().getAttribute("aria-label") !== "Rename this thread") {
        throw new Error("a control that looks like a heading needs its own name");
    }
    if (titleInput().value !== "Standup") throw new Error("the title must show the name");
    if (actionLabels().join("|") !== "Archive") {
        throw new Error(`at rest the row is Archive alone, got ${actionLabels().join("|")}`);
    }
    const renameActionsAbsentAtRest = renameButtons().length === 0;
    if (!renameActionsAbsentAtRest) {
        throw new Error("neither rename control may exist when nothing is being renamed");
    }

    press(titleInput(), "Enter");
    const titleOpensEditOnEnter = titleInput().getAttribute("data-editing") === "true"
        && titleInput().readOnly === false
        && documentStub.activeElement === titleInput();
    if (!titleOpensEditOnEnter) {
        throw new Error(`Enter must open edit mode and take focus, got `
            + `${titleInput().getAttribute("data-editing")}`);
    }
    // The pair joins the row Archive is in, through the same descriptor.
    // Round four replaced the word `Save` with a green check and added the
    // red cross beside it — the operator's ask, and until then Esc cancelled
    // and nothing said so.
    const saveActionAppearsBesideArchive =
        actionNames().join("|") === "Cancel rename|Save name|Archive"
        && Boolean(conversation.element.querySelector("#conversation-title-save"));
    if (!saveActionAppearsBesideArchive) {
        throw new Error(`the rename pair must join the action row, got ${actionNames().join("|")}`);
    }
    // Icon-only, so each carries its own accessible name: colour is not the
    // only carrier (SC 1.4.1) and the two shapes differ as well as the hues.
    const renameActions = renameButtons().map((btn) => btn.getAttribute("aria-label"));
    const renameActionIcons = renameButtons().map((btn) => {
        const icon = btn.querySelectorAll("i")[0];
        return icon ? icon.getAttribute("data-lucide") : null;
    });
    const renameActionsAreIconOnly = renameButtons().length === 2
        && renameButtons().every((btn) => btn.textContent === ""
            && Boolean(btn.getAttribute("aria-label"))
            && btn.querySelectorAll("i").length === 1);
    if (!renameActionsAreIconOnly) {
        throw new Error(`an icon-only control needs its own name, got `
            + `${JSON.stringify(renameActions)}`);
    }

    // Escape backs out and puts the confirmed name back, having sent nothing.
    titleInput().value = "something half typed";
    const patchesBeforeEscape = renamePayloads.length;
    press(titleInput(), "Escape");
    const escapeCancelsRenameWithoutSaving = titleInput().value === "Standup"
        && titleInput().getAttribute("data-editing") === "false"
        && renamePayloads.length === patchesBeforeEscape
        && actionLabels().join("|") === "Archive";
    if (!escapeCancelsRenameWithoutSaving) {
        throw new Error(`Escape must discard the draft and send nothing, got `
            + `"${titleInput().value}" after ${renamePayloads.length} patches`);
    }

    // ── Cancel is a control now, not only a keystroke ──
    //
    // It restores the last SERVER-confirmed name, which is exactly what Esc
    // above just did. One path, two triggers: proven by asserting the same
    // three things — the draft is gone, the mode is closed, and nothing was
    // sent — rather than by reading which function the descriptor names.
    press(titleInput(), "Enter");
    titleInput().value = "abandoned draft";
    const patchesBeforeCancel = renamePayloads.length;
    await renameButtons()[0].dispatchClick();
    await tick();
    const cancelActionRestoresLikeEsc = titleInput().value === "Standup"
        && titleInput().getAttribute("data-editing") === "false"
        && renamePayloads.length === patchesBeforeCancel
        && renameButtons().length === 0
        && actionNames().join("|") === "Archive";
    if (!cancelActionRestoresLikeEsc) {
        throw new Error(`the cancel control must restore like Esc, got `
            + `"${titleInput().value}" after ${renamePayloads.length} patches`);
    }

    // A rename that lands: the request goes out, and re-reading the thread
    // shows the new name — so the title is the server's answer, not the draft.
    press(titleInput(), "Enter");
    titleInput().value = "Release triage";
    press(titleInput(), "Enter");
    await tick();
    await tick();
    const renamePatchesTheChannel = renamePayloads.length === patchesBeforeEscape + 1
        && renamePayloads[renamePayloads.length - 1].name === "Release triage"
        && titleInput().getAttribute("data-editing") === "false"
        && actionLabels().join("|") === "Archive";
    if (!renamePatchesTheChannel) {
        throw new Error(`the rename must PATCH and then close, got `
            + `${JSON.stringify(renamePayloads)} editing `
            + `${titleInput().getAttribute("data-editing")}`);
    }
    await conversation.open("b", "agent");
    await conversation.open("t1", "thread");
    const renameShowsTheSavedName = titleInput().value === "Release triage";
    if (!renameShowsTheSavedName) {
        throw new Error(`the renamed thread must read back renamed, got `
            + `"${titleInput().value}"`);
    }

    // A rename that fails keeps the operator's text, stays in edit mode, and
    // says what went wrong. Reverting to the old name would look exactly like
    // a rename that worked and then un-happened.
    renameFails = true;
    press(titleInput(), "Enter");
    titleInput().value = "Doomed name";
    press(titleInput(), "Enter");
    await tick();
    await tick();
    const failedRenameReportsError = errorLine().includes(RENAME_FAILURE);
    const failedRenameKeepsDraft = titleInput().value === "Doomed name";
    const failedRenameStaysInEditMode = titleInput().getAttribute("data-editing") === "true"
        && actionNames().join("|") === "Cancel rename|Save name|Archive";
    if (!failedRenameReportsError) {
        throw new Error(`a failed rename must say so, got "${errorLine()}"`);
    }
    if (!failedRenameKeepsDraft || !failedRenameStaysInEditMode) {
        throw new Error(`a failed rename must keep the draft and the mode, got `
            + `"${titleInput().value}" editing ${titleInput().getAttribute("data-editing")}`);
    }
    renameFails = false;
    press(titleInput(), "Escape");

    // A conversation switch must not carry a half-typed rename onto the next
    // thread. apply() deliberately leaves the field alone while it is being
    // edited — that is what stops a live repaint stealing the operator's
    // typing — so without an explicit reset the draft would follow the switch
    // and the new thread would wear the old one's name.
    press(titleInput(), "Enter");
    titleInput().value = "Never sent";
    const patchesBeforeSwitch = renamePayloads.length;
    await conversation.open("t3", "thread");
    const renameDoesNotFollowASwitch = titleInput().value === "Design sync"
        && titleInput().getAttribute("data-editing") === "false"
        && actionLabels().join("|") === "Archive"
        && renamePayloads.length === patchesBeforeSwitch;
    if (!renameDoesNotFollowASwitch) {
        throw new Error(`a switch must drop the rename, got "${titleInput().value}"`
            + ` editing ${titleInput().getAttribute("data-editing")}`);
    }

    // An agent conversation is not renameable, and neither is a sealed room:
    // both get a plain heading with no control in it and no tab stop.
    await conversation.open("a", "agent");
    const agentTitleIsNotEditable = titleInput() === null
        && titleSlot().textContent === "Ada";
    if (!agentTitleIsNotEditable) {
        throw new Error(`an agent conversation must not be renameable, got `
            + `"${titleSlot().textContent}"`);
    }
    await conversation.open("t2", "thread");
    const archivedThreadIsNotRenameable = titleInput() === null
        && titleSlot().textContent === "Old room";
    if (!archivedThreadIsNotRenameable) {
        throw new Error(`a sealed room takes no writes, rename included, got `
            + `"${titleSlot().textContent}"`);
    }

    // ─── The empty conversation offers the two things you can do ───
    await conversation.open("d", "agent");
    const empty = status();
    if (!empty || empty.getAttribute("data-status") !== "empty") {
        throw new Error("a conversation with no messages must render the empty state");
    }
    const bigAvatar = empty.querySelector(".avatar-lg");
    const emptyActions = empty.querySelector(".conversation-empty-actions").querySelectorAll("button");
    const emptyConversationOffersActions = Boolean(bigAvatar)
        && bigAvatar.textContent === "D"
        && emptyActions.map((b) => b.textContent).join("|") === "Say hello|Assign a task";
    if (!emptyConversationOffersActions) {
        throw new Error(`the empty state must offer both actions: ${emptyActions.map((b) => b.textContent).join("|")}`);
    }

    // "Say hello" goes through the composer's send path — one send, one gate,
    // one place that clears the draft only once the server has acknowledged.
    await emptyActions[0].dispatchClick();
    await tick();
    await tick();
    const greetingWentThroughTheComposer = activations.length === 1
        && activations[0].id === "d"
        && activations[0].body.content.includes("Di")
        && composerInput.value === "";
    if (!greetingWentThroughTheComposer) {
        throw new Error(`the greeting must send through the composer: ${JSON.stringify(activations)}`);
    }

    // Destroying drains everything the controller ever subscribed.
    conversation.destroy();
    if (bus.subscriberCount() !== 0) {
        throw new Error(`destroy left ${bus.subscriberCount()} bus subscribers`);
    }
    if (store.subscriberCount() !== 0) {
        throw new Error(`destroy left ${store.subscriberCount()} store subscribers`);
    }
    if (!listing) throw new Error("the conversation must mount a transcript");
    if (!documentStub) throw new Error("the harness needs a document");

    process.stdout.write(JSON.stringify({
        ok: true,
        staleLoadDropped,
        cacheSkipsLoading,
        composerSurvivesSwitch,
        presenceSurvivesSwitch,
        unsubscribesPreviousSource,
        errorStateRetries,
        chromeShowsIdentityAvatar,
        chromeAvatarNodeIsStable,
        chromeActionCarriesItsIcon,
        chromeGroupGlyphForThreads,
        headerHasNoReceiptsSwitch,
        receiptsToggleReachableFromMenu,
        receiptsPreferencePersists,
        menuReturnsFocusToItsButton,
        receiptsNodeSurvivesReopen,
        emptyConversationOffersActions,
        greetingWentThroughTheComposer,
        titleOpensEditOnEnter,
        saveActionAppearsBesideArchive,
        escapeCancelsRenameWithoutSaving,
        renamePatchesTheChannel,
        renameShowsTheSavedName,
        failedRenameReportsError,
        failedRenameKeepsDraft,
        failedRenameStaysInEditMode,
        renameDoesNotFollowASwitch,
        renameActions,
        renameActionIcons,
        renameActionsAreIconOnly,
        renameActionsAbsentAtRest,
        cancelActionRestoresLikeEsc,
        agentTitleIsNotEditable,
        archivedThreadIsNotRenameable,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
