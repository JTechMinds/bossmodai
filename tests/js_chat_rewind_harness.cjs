/**
 * Node harness: the agent DM's Rewind — its flow, its dialog, and where the
 * conversation offers it. Invoked by tests/test_ui_conversation.py with the
 * same module list as js_conversation_harness.cjs. Not a browser bundle.
 *
 * The flow (chat-rewind.js) is driven with fake source, composer and needs,
 * because what it promises is about the order it calls them in. The dialog and
 * the conversation are the real modules on the shared fake DOM.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
require("./js_icons_stub.cjs").installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModOperatorInvalidate", "BossModFormat", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuButton", "BossModEmptyState",
    "BossModTranscript", "BossModTranscriptCache", "BossModMessage", "BossModEventCards",
    "BossModTitleRename", "BossModChromeMenu", "BossModConversationChrome",
    "BossModDesktopClipboard", "BossModComposerAttachments", "BossModComposer", "BossModSystemReceipts", "BossModNeedShape", "BossModNeedsBar", "BossModThreadArchive",
    "BossModThreadSeat",
    "BossModThreadRequests", "BossModAutoApproveSwitch", "BossModThreadSource", "BossModAgentRequests", "BossModAgentSource",
    "BossModConversationFocus", "BossModChatRewindDialog", "BossModChatRewind", "BossModConsentActivity", "BossModConversation",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModChatRewind, BossModChatRewindDialog } = global;

const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i += 1) await tick(); };
const check = (ok, message) => { if (!ok) throw new Error(message); };

// ─── Dialog helpers: the open modal, found the way an operator would ───

const dialogEl = () => documentStub.body.querySelector(".chat-rewind");
const radios = () => documentStub.body.querySelectorAll(".chat-rewind-radio");
const rows = () => documentStub.body.querySelectorAll(".chat-rewind-row");
const confirmBtn = () => documentStub.body.querySelector("#chat-rewind-confirm");
const cancelBtn = () => documentStub.body.querySelector("#chat-rewind-cancel");
const statusText = () => (documentStub.body.querySelector(".chat-rewind-status") || {}).textContent || "";
const errorText = () => (documentStub.body.querySelector(".chat-rewind-error") || {}).textContent || "";
const removedFlags = () => rows().map((row) => row.classList.contains("is-removed"));
/** Choose the row whose radio carries this message key, as a click on it would. */
const choose = (key) => {
    const input = radios().find((node) => node.getAttribute("value") === key);
    check(input, `no row for ${key}`);
    input.dispatchEvent({ type: "change" });
};
const closeAnyModal = async () => {
    const close = documentStub.body.querySelector(".modal-close");
    if (close) await close.dispatchClick();
};

// Messages oldest first, as the source returns them.
const point = (key, author, text) => ({
    key, author, text, kind: "message", authorName: author === "human" ? "You" : "Ada", createdAt: "",
});
const POINTS = [
    point("m1", "human", "first question"),
    point("m2", "agent", "first answer"),
    point("m3", "human", "the regretted one"),
    point("m4", "agent", "I hit repeated runtime failures"),
];

function fakes({ fail = false, unremoved = 0 } = {}) {
    const log = [];
    const source = {
        id: "a",
        async rewindPoints() { return POINTS.slice(); },
        async rewind(key) {
            log.push(`rewind:${key}`);
            if (fail) throw new Error("Message not found in this conversation");
            return { status: "ok", removed_messages: 1, unremoved_files: unremoved };
        },
    };
    const composer = {
        draft: { text: "", attachments: [] },
        readDraft() { return this.draft; },
        setDraft(draft) { log.push("setDraft"); this.draft = draft; },
    };
    const needs = { acknowledge(need) { log.push(`acknowledge:${need.id}`); } };
    return { source, composer, needs, log };
}

async function flowCases() {
    // A source that cannot rewind must not get entry points at all.
    let refusesHalfSource = false;
    try {
        BossModChatRewind.createChatRewind({
            source: { id: "a" }, agentName: () => "Ada", composer: fakes().composer, needs: fakes().needs,
        });
    } catch (err) {
        refusesHalfSource = /rewindPoints/.test(String(err.message));
    }
    check(refusesHalfSource, "a source without rewind calls must be refused");

    const base = fakes();
    const flow = BossModChatRewind.createChatRewind({
        source: base.source, agentName: () => "Ada", composer: base.composer, needs: base.needs,
    });
    const menu = flow.menuAction();
    const menuActionShape = menu.id === "agent-chat-rewind" && menu.label === "Rewind…"
        && menu.icon === "rotate-ccw" && menu.slot === "menu" && menu.section === "chat"
        && typeof menu.onSelect === "function";
    check(menuActionShape, `menu action shape: ${JSON.stringify(menu)}`);

    const errorNeed = { id: "d1", kind: "error", agentId: "a", groupedIds: ["d1"] };
    const cardActionsOnlyForThisAgentsErrors = flow.cardActions(errorNeed).length === 1
        && flow.cardActions(errorNeed)[0].label === "Rewind…"
        && flow.cardActions({ id: "d2", kind: "error", agentId: "b" }).length === 0
        && flow.cardActions({ id: "q1", kind: "blocked", agentId: "a" }).length === 0;
    check(cardActionsOnlyForThisAgentsErrors, "card actions must be offered only on this agent's error needs");

    // Own message into an EMPTY composer: the text comes back as the draft.
    await flow.open({});
    await settle();
    choose("m3");
    await confirmBtn().dispatchClick();
    await settle();
    const ownMessageFillsEmptyComposer = base.log.join(",") === "rewind:m3,setDraft"
        && base.composer.draft.text === "the regretted one"
        && dialogEl() === null;
    check(ownMessageFillsEmptyComposer, `own message, empty composer: ${base.log} / ${JSON.stringify(base.composer.draft)}`);

    // Own message into a composer that already holds typing: nothing is lost.
    const typed = fakes();
    typed.composer.draft = { text: "half a new thought", attachments: [{ id: "up1" }] };
    const typedFlow = BossModChatRewind.createChatRewind({
        source: typed.source, agentName: () => "Ada", composer: typed.composer, needs: typed.needs,
    });
    await typedFlow.open({});
    await settle();
    choose("m1");
    await confirmBtn().dispatchClick();
    await settle();
    const ownMessagePrependsToTyping = typed.composer.draft.text === "first question\n\nhalf a new thought"
        && typed.composer.draft.attachments.length === 1
        && typed.composer.draft.attachments[0].id === "up1";
    check(ownMessagePrependsToTyping, `prepend: ${JSON.stringify(typed.composer.draft)}`);

    // An agent message: the composer is not touched.
    const agentRow = fakes();
    agentRow.composer.draft = { text: "keep me", attachments: [] };
    const agentFlow = BossModChatRewind.createChatRewind({
        source: agentRow.source, agentName: () => "Ada", composer: agentRow.composer, needs: agentRow.needs,
    });
    await agentFlow.open({});
    await settle();
    choose("m4");
    await confirmBtn().dispatchClick();
    await settle();
    const agentMessageLeavesComposer = agentRow.log.join(",") === "rewind:m4"
        && agentRow.composer.draft.text === "keep me";
    check(agentMessageLeavesComposer, `agent message touched the composer: ${agentRow.log}`);

    // From the error card: preselected, and the card is acknowledged only
    // after the server said yes.
    const card = fakes();
    const cardFlow = BossModChatRewind.createChatRewind({
        source: card.source, agentName: () => "Ada", composer: card.composer, needs: card.needs,
    });
    await cardFlow.cardActions(errorNeed)[0].onSelect();
    await settle();
    const preselected = radios().filter((node) => node.checked).map((node) => node.getAttribute("value"));
    check(preselected.join() === "m3", `the card must preselect the newest own message, got ${preselected}`);
    await confirmBtn().dispatchClick();
    await settle();
    const cardAcknowledgesAfterSuccess = card.log.join(",") === "rewind:m3,setDraft,acknowledge:d1";
    check(cardAcknowledgesAfterSuccess, `card order: ${card.log}`);

    // A refused rewind changes nothing local, and says why in the dialog.
    const refused = fakes({ fail: true });
    refused.composer.draft = { text: "untouched", attachments: [] };
    const refusedFlow = BossModChatRewind.createChatRewind({
        source: refused.source, agentName: () => "Ada", composer: refused.composer, needs: refused.needs,
    });
    await refusedFlow.cardActions(errorNeed)[0].onSelect();
    await settle();
    await confirmBtn().dispatchClick();
    await settle();
    const rejectedRewindTouchesNothing = refused.log.join(",") === "rewind:m3"
        && refused.composer.draft.text === "untouched"
        && dialogEl() !== null
        && errorText() === "Message not found in this conversation";
    check(rejectedRewindTouchesNothing, `refused rewind: ${refused.log} / ${errorText()}`);
    await closeAnyModal();
    check(dialogEl() === null, "the dialog must close from its ✕");

    // Files the server could not delete: a done state the operator reads.
    const leftover = fakes({ unremoved: 2 });
    const leftoverFlow = BossModChatRewind.createChatRewind({
        source: leftover.source, agentName: () => "Ada", composer: leftover.composer, needs: leftover.needs,
    });
    await leftoverFlow.open({});
    await settle();
    choose("m4");
    await confirmBtn().dispatchClick();
    await settle();
    const doneButton = documentStub.body.querySelector("#chat-rewind-done");
    const unremovedFilesShowWarning = dialogEl() !== null
        && statusText() === "Rewound, but 2 attached files couldn't be deleted."
        && doneButton && doneButton.textContent === "Close" && confirmBtn() === null;
    check(unremovedFilesShowWarning, `warning state: ${statusText()}`);
    await doneButton.dispatchClick();
    check(dialogEl() === null, "Close must close the done state");

    return {
        refusesHalfSource,
        menuActionShape,
        cardActionsOnlyForThisAgentsErrors,
        ownMessageFillsEmptyComposer,
        ownMessagePrependsToTyping,
        agentMessageLeavesComposer,
        cardAcknowledgesAfterSuccess,
        rejectedRewindTouchesNothing,
        unremovedFilesShowWarning,
    };
}

async function dialogCases() {
    // Loading: the copy and the status, and nothing to confirm yet.
    let resolveConfirm = null;
    const dialog = BossModChatRewindDialog.open({
        agentName: "Ada",
        onConfirm: () => new Promise((resolve, reject) => { resolveConfirm = { resolve, reject }; }),
    });
    const honesty = documentStub.body.querySelector(".chat-rewind-note").textContent;
    const loadingState = statusText() === "Loading messages…" && confirmBtn().disabled === true
        && honesty.includes("Anything Ada has already done") && honesty.includes("Only this conversation is rewound.")
        && documentStub.activeElement === cancelBtn();
    check(loadingState, `loading: ${statusText()} / focus on ${documentStub.activeElement && documentStub.activeElement.id}`);

    // Ready, nothing preselected from the menu: newest first, nothing marked.
    dialog.setPoints(POINTS.slice());
    const order = radios().map((node) => node.getAttribute("value")).join();
    const listGroup = documentStub.body.querySelector(".chat-rewind-list");
    check(order === "m4,m3,m2,m1", `rows must be newest first, got ${order}`);
    check(listGroup.getAttribute("role") === "radiogroup", "the list is a radio group");
    check(removedFlags().every((flag) => !flag) && confirmBtn().disabled === true, "nothing is chosen yet");
    check(documentStub.activeElement === radios()[0], "the keyboard moves into the group");

    // Choosing m2 marks it and every newer row; older rows stay.
    choose("m2");
    const marks = rows().map((row) => !row.querySelector(".chat-rewind-mark").hidden);
    const choosingMarksNewerRows = removedFlags().join() === "true,true,true,false"
        && marks.join() === "true,true,true,false"
        && confirmBtn().disabled === false;
    check(choosingMarksNewerRows, `marks: ${removedFlags()} / ${marks}`);
    const restoreNote = () => documentStub.body.querySelectorAll(".chat-rewind-note")[1];
    check(restoreNote().hidden === true, "an agent row restores nothing, so the restore note is hidden");
    choose("m3");
    check(restoreNote().hidden === false, "an own row says it goes back in the message box");

    // Enter in the group confirms; a rejection keeps the dialog with its reason.
    const list = documentStub.body.querySelector(".chat-rewind-list");
    list.dispatchEvent({ type: "keydown", key: "Enter", preventDefault() {} });
    await settle();
    check(confirmBtn().disabled === true && confirmBtn().textContent === "Rewinding…", "busy while the cut runs");
    resolveConfirm.reject(new Error("Could not stop Ada's reply: worker died"));
    await settle();
    const rejectedConfirmKeepsModal = dialogEl() !== null
        && errorText() === "Could not stop Ada's reply: worker died"
        && confirmBtn().disabled === false && confirmBtn().textContent === "Rewind";
    check(rejectedConfirmKeepsModal, `rejection: ${errorText()}`);

    // Confirming again with a warning switches to the done state.
    await confirmBtn().dispatchClick();
    await settle();
    resolveConfirm.resolve({ warning: "Rewound, but 1 attached file couldn't be deleted." });
    await settle();
    const warningShowsDoneState = statusText().startsWith("Rewound, but 1 attached file")
        && documentStub.body.querySelector("#chat-rewind-done") !== null
        && documentStub.activeElement === documentStub.body.querySelector("#chat-rewind-done");
    check(warningShowsDoneState, "a warning must leave a done state with Close focused");
    dialog.close();
    check(dialogEl() === null, "close() must close the dialog");

    // Preselection from the error card: the newest OWN row, not the newest row.
    const pre = BossModChatRewindDialog.open({ agentName: "Ada", preselectLatestHuman: true, onConfirm: async () => ({}) });
    pre.setPoints(POINTS.slice());
    const preselectsNewestHuman = radios().filter((node) => node.checked).map((node) => node.getAttribute("value")).join() === "m3"
        && removedFlags().join() === "true,true,false,false";
    check(preselectsNewestHuman, `preselect: ${removedFlags()}`);
    pre.close();

    // Empty and load-error states keep Rewind disabled.
    const empty = BossModChatRewindDialog.open({ agentName: "Ada", onConfirm: async () => ({}) });
    empty.setPoints([]);
    const emptyState = statusText() === "Nothing to rewind yet." && confirmBtn().disabled === true && radios().length === 0;
    check(emptyState, `empty: ${statusText()}`);
    empty.close();
    const failed = BossModChatRewindDialog.open({ agentName: "Ada", onConfirm: async () => ({}) });
    failed.setLoadError("Agent chat is unavailable.");
    const loadErrorState = errorText() === "Agent chat is unavailable." && confirmBtn().disabled === true
        && documentStub.body.querySelector(".chat-rewind-list").hidden === true;
    check(loadErrorState, `load error: ${errorText()}`);
    failed.close();

    return {
        loadingState,
        emptyState,
        loadErrorState,
        preselectsNewestHuman,
        choosingMarksNewerRows,
        rejectedConfirmKeepsModal,
        warningShowsDoneState,
    };
}

async function conversationCases() {
    const posted = [];
    const api = async (url, init) => {
        const text = String(url);
        if (/^\/api\/agents\/a\/messages/.test(text)) {
            return {
                ok: true,
                async json() {
                    return [
                        { id: "h1", from: "human", content: "please summarise", created_at: "2026-10-08T10:00:00" },
                        { id: "n1", from: "system", message_type: "system", content: "Task done", created_at: "2026-10-08T10:00:01" },
                        { id: "g1", from: "agent", content: "I hit repeated runtime failures", created_at: "2026-10-08T10:00:02" },
                    ];
                },
            };
        }
        if (text === "/api/agents/a/chat-rewind") {
            posted.push(JSON.parse(init.body));
            return { ok: true, async json() { return { status: "ok", removed_messages: 2, unremoved_files: 0 }; } };
        }
        if (/^\/api\/agents\/[^/?]+$/.test(text)) {
            return { ok: true, async json() { return { cli_auto_approve_dm: false, cli_auto_approve_global: false }; } };
        }
        if (/^\/api\/channels\/t1$/.test(text)) {
            return {
                ok: true,
                async json() {
                    return { channel: { id: "t1", name: "Standup", status: "active", members: [] }, messages: [] };
                },
            };
        }
        throw new Error(`unhandled ${text}`);
    };
    global.BossModApi = {
        async uploadAttachment() { throw new Error("no uploads here"); },
        async deleteAttachment() {},
        async getAttachmentLimits() { return { max_size_mb: 10, max_per_message: 5 }; },
        async fetchBlobUrl(url) { return `blob:${url}`; },
        fetch: api,
    };
    const errorNeed = {
        id: "d9", kind: "error", agentId: "a", conversationId: "a", title: "Ada hit an error",
        sub: "Connection refused", createdAt: "2026-10-08T10:00:03", groupedIds: ["d9"],
        actions: [{ label: "Open diagnostics", method: "GET", tone: "quiet", href: "/api/diagnostics/d9" }],
        target: { place: "chat", conversationId: "a", conversationKind: "agent" },
    };
    // The chat place's order: the store names the conversation (and the bar
    // renders) BEFORE the controller opens it.
    const store = global.BossModStore.createStore({
        roster: [{ id: "a", name: "Ada", role: "Writer" }],
        threads: [],
        hasUsableModel: true,
        needs: [errorNeed],
        needsBarEnabled: true,
        needsBarDismissed: false,
        inlineNeedIds: [],
        conversationId: null,
        conversationKind: null,
    });
    const bus = global.BossModBus.createBus(global.BossModBus.KNOWN_TOPICS);
    global.BossModOperatorInvalidate.attach({ bus });
    const acknowledged = [];
    const needs = {
        refresh: () => Promise.resolve(),
        resolve: () => Promise.resolve(),
        acknowledge: (need) => acknowledged.push(need.id),
        getError: () => "",
        subscribeError: () => () => {},
        destroy: () => {},
    };
    const conversation = global.BossModConversation.createConversation({
        store, bus, api, navigate() {}, needs, drafts: new Map(), cache: global.BossModTranscriptCache.createCache(),
    });
    documentStub.body.append(conversation.element);

    const menuNames = async () => {
        const dots = conversation.element.querySelector("#conversation-view-options");
        if (!dots) return "";
        await dots.dispatchClick();
        const open = conversation.element.querySelector(".menu");
        // Every section's bodies, in DOM order: the panel is sectioned now.
        const names = open
            ? open.querySelectorAll(".menu-actions").flatMap((body) => body.querySelectorAll("button"))
                .map((b) => b.textContent)
            : [];
        await dots.dispatchClick();
        return names.join("|");
    };
    const barButtons = () => conversation.element.querySelector(".needs-bar")
        .querySelectorAll("button").map((b) => b.textContent);

    store.setState({ conversationId: "a", conversationKind: "agent" });
    await conversation.open("a", "agent");
    await settle();
    const menuOffersRewindOnAgentDm = (await menuNames()).split("|").includes("Rewind…");
    check(menuOffersRewindOnAgentDm, `agent menu: ${await menuNames()}`);

    bus.publish("agent_presence", { agent_id: "a", agent_name: "Ada", phase: "thinking" });
    await settle();
    const menuOffersRewindWhileThinking = (await menuNames()).split("|").includes("Rewind…");
    check(menuOffersRewindWhileThinking, "Rewind must stay offered while the agent is thinking");

    const labels = barButtons();
    const errorCardOffersRewind = labels.indexOf("Rewind…") === labels.indexOf("Open diagnostics") + 1;
    check(errorCardOffersRewind, `error card buttons: ${labels.join("|")}`);

    // End to end through the real source: the card preselects the own
    // message, the POST names it, and its text comes back into the composer.
    const rewindButton = conversation.element.querySelector(".needs-bar")
        .querySelectorAll("button").find((b) => b.textContent === "Rewind…");
    await rewindButton.dispatchClick();
    await settle();
    const offered = radios().map((node) => node.getAttribute("value")).join();
    check(offered === "g1,h1", `the dialog offers the DM's messages only, newest first: ${offered}`);
    await confirmBtn().dispatchClick();
    await settle();
    const composerInput = conversation.element.querySelector(".composer-input");
    const cardRewindRestoresAndAcknowledges = posted.length === 1 && posted[0].from_message_id === "h1"
        && composerInput.value === "please summarise" && acknowledged.join() === "d9" && dialogEl() === null;
    check(cardRewindRestoresAndAcknowledges, `card rewind: ${JSON.stringify(posted)} / ${composerInput.value} / ${acknowledged}`);

    store.setState({ conversationId: "t1", conversationKind: "thread" });
    await conversation.open("t1", "thread");
    await settle();
    const menuOmitsRewindOnThreads = !(await menuNames()).split("|").includes("Rewind…");
    check(menuOmitsRewindOnThreads, `thread menu: ${await menuNames()}`);

    conversation.destroy();
    return {
        menuOffersRewindOnAgentDm,
        menuOffersRewindWhileThinking,
        errorCardOffersRewind,
        cardRewindRestoresAndAcknowledges,
        menuOmitsRewindOnThreads,
    };
}

async function main() {
    const flow = await flowCases();
    const dialog = await dialogCases();
    const conversation = await conversationCases();
    process.stdout.write(JSON.stringify({ ok: true, ...flow, ...dialog, ...conversation }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
