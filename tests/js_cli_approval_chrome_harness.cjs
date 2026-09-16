/**
 * Node harness: Focus/transcript CLI approval chrome after create.
 * Invoked by tests/test_ui_conversation.py. Not a browser bundle.
 *
 * Proves the operator watching an agent conversation gets Approve/Reject on
 * the transcript card — from a REST load (create-time chrome) and from a
 * live chat_message append.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModFormat", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModOverlays", "BossModEmptyState",
    "BossModTranscript", "BossModTranscriptCache", "BossModMessage", "BossModEventCards",
    "BossModTitleRename", "BossModConversationChrome",
    "BossModComposer", "BossModSystemReceipts", "BossModNeedShape", "BossModNeedsBar", "BossModThreadArchive",
    "BossModThreadSource", "BossModAgentSource", "BossModConversation",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModConversation } = global;
const tick = () => new Promise((resolve) => setImmediate(resolve));

function approvalPayload(id, agentId) {
    return {
        id,
        agent_id: agentId || "jim",
        kind: "cli_approval",
        title: "Approve this command?",
        command: 'pip install -e ".[dev]"',
        status: "pending",
    };
}

const REST_MESSAGES = {
    jim: [{
        id: "n-create",
        from: "system",
        from_name: "Jim",
        message_type: "system",
        notification_kind: "cli_approval",
        content: 'Jim wants to run pip install -e ".[dev]"',
        cli_approval: approvalPayload("appr-create"),
        created_at: "2026-09-16T17:00:00Z",
    }],
    live: [{
        id: "m1",
        from: "agent",
        content: "starting",
        created_at: "2026-09-16T17:00:00Z",
    }],
    reload: [{
        id: "m-reload",
        from: "agent",
        content: "working",
        created_at: "2026-09-16T17:00:00Z",
    }],
};

const blockers = {};
const api = async (url) => {
    const agent = String(url).match(/^\/api\/agents\/([^/]+)\/messages/);
    if (agent) {
        const id = agent[1];
        if (id === "slow") {
            await new Promise((resolve) => { blockers.slow = resolve; });
        }
        return { ok: true, async json() { return REST_MESSAGES[id] || []; } };
    }
    throw new Error(`unhandled ${url}`);
};

function actionLabels(root) {
    return root.querySelectorAll(".hpc-action").map((node) => node.textContent);
}

(async () => {
    const store = BossModStore.createStore({
        roster: [
            { id: "jim", name: "Jim", role: "Engineer" },
            { id: "live", name: "Live", role: "Engineer" },
            { id: "chan", name: "Chan", role: "Engineer" },
            { id: "slow", name: "Slow", role: "Engineer" },
            { id: "reload", name: "Reload", role: "Engineer" },
        ],
        threads: [],
        hasUsableModel: true,
        needs: [],
        inlineNeedIds: [],
        conversationId: null,
        needsBarEnabled: true,
        needsBarDismissed: false,
    });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const needsStub = {
        refresh: () => Promise.resolve(),
        resolve: () => Promise.resolve(),
        getError: () => "",
        subscribeError: () => () => {},
        destroy: () => {},
    };
    const conversation = BossModConversation.createConversation({
        store, bus, api, navigate() {}, needs: needsStub,
    });
    documentStub.body.append(conversation.element);

    store.setState({ conversationId: "jim", conversationKind: "agent" });
    await conversation.open("jim", "agent");
    await tick();

    const createCard = conversation.element.querySelector("#cli-approval-appr-create");
    if (!createCard) throw new Error("create-time Focus load must paint a cli_approval card");
    const createLabels = actionLabels(createCard);
    if (!createLabels.includes("Approve") || !createLabels.includes("Reject")) {
        throw new Error(`create-time card must offer Approve/Reject, got ${createLabels.join(",")}`);
    }
    const inlineAfterCreate = (store.getState().inlineNeedIds || []).join(",");
    if (inlineAfterCreate !== "appr-create") {
        throw new Error(`inlineNeedIds after create load, got ${inlineAfterCreate}`);
    }
    const paintsCreateChrome = true;

    store.setState({ conversationId: "live", conversationKind: "agent" });
    await conversation.open("live", "agent");
    await tick();
    bus.publish("chat_message", {
        agent_id: "live",
        content: 'Live wants to run pip install -e ".[dev]"',
        from: "system",
        from_name: "Live",
        message_type: "system",
        message_id: "n-live",
        notification_kind: "cli_approval",
        cli_approval: approvalPayload("appr-live"),
        created_at: "2026-09-16T17:01:00Z",
    });
    await tick();
    const liveCard = conversation.element.querySelector("#cli-approval-appr-live");
    if (!liveCard) throw new Error("live chat_message must paint a cli_approval card");
    const liveLabels = actionLabels(liveCard);
    if (!liveLabels.includes("Approve") || !liveLabels.includes("Reject")) {
        throw new Error(`live-append card must offer Approve/Reject, got ${liveLabels.join(",")}`);
    }
    const paintsLiveAppend = true;

    store.setState({ conversationId: "reload", conversationKind: "agent" });
    await conversation.open("reload", "agent");
    await tick();
    if (conversation.element.querySelector("#cli-approval-appr-reload")) {
        throw new Error("reload agent must start without a card");
    }
    REST_MESSAGES.reload = REST_MESSAGES.reload.concat([{
        id: "n-reload",
        from: "system",
        from_name: "Reload",
        message_type: "system",
        notification_kind: "cli_approval",
        content: 'Reload wants to run pip install -e ".[dev]"',
        cli_approval: approvalPayload("appr-reload"),
        created_at: "2026-09-16T17:02:00Z",
    }]);
    store.setState({
        needs: [{
            id: "appr-reload",
            kind: "approval",
            conversationId: "reload",
            title: "Reload wants to run a command",
            sub: 'pip install -e ".[dev]"',
            actions: [],
            target: { place: "chat", conversationId: "reload", conversationKind: "agent" },
        }],
    });
    await tick();
    await tick();
    const reloadedCard = conversation.element.querySelector("#cli-approval-appr-reload");
    if (!reloadedCard) {
        throw new Error("a pending need without inline chrome must refetch and paint Approve/Reject");
    }
    const reloadLabels = actionLabels(reloadedCard);
    if (!reloadLabels.includes("Approve") || !reloadLabels.includes("Reject")) {
        throw new Error(`refetched card must offer Approve/Reject, got ${reloadLabels.join(",")}`);
    }
    const refetchesWhenInlineMissing = true;

    store.setState({ conversationId: "chan", conversationKind: "agent", needs: [], inlineNeedIds: [] });
    await conversation.open("chan", "agent");
    await tick();
    bus.publish("channel_message", {
        channel_id: "th-chan",
        author_type: "system",
        author_name: "Chan",
        author_agent_id: "chan",
        message_id: "n-chan",
        notification_kind: "cli_approval",
        content: 'Chan wants to run pip install -e ".[dev]"',
        cli_approval: approvalPayload("appr-chan", "chan"),
        created_at: "2026-09-16T17:03:00Z",
    });
    await tick();
    const chanCard = conversation.element.querySelector("#cli-approval-appr-chan");
    if (!chanCard) {
        throw new Error("live channel_message must paint Approve in open Focus without a bell fetch");
    }
    const chanLabels = actionLabels(chanCard);
    if (!chanLabels.includes("Approve") || !chanLabels.includes("Reject")) {
        throw new Error(`channel live-append must offer Approve/Reject, got ${chanLabels.join(",")}`);
    }
    const paintsLiveChannelAppend = true;

    store.setState({ conversationId: "slow", conversationKind: "agent", needs: [], inlineNeedIds: [] });
    const slowOpen = conversation.open("slow", "agent");
    await tick();
    await tick();
    if (typeof blockers.slow !== "function") {
        throw new Error("slow Focus load never started");
    }
    store.setState({
        needs: [{
            id: "appr-slow",
            kind: "approval",
            agentId: "slow",
            conversationId: "th-slow",
            title: "Slow wants to run a command",
            sub: 'pip install -e ".[dev]"',
            actions: [],
            target: { place: "chat", conversationId: "th-slow", conversationKind: "thread" },
        }],
    });
    await tick();
    if (conversation.element.querySelector("#cli-approval-appr-slow")) {
        throw new Error("buffered origin-thread chrome must not paint before the in-flight load finishes");
    }
    blockers.slow();
    await slowOpen;
    await tick();
    const slowCard = conversation.element.querySelector("#cli-approval-appr-slow");
    if (!slowCard) {
        throw new Error("create-time need must paint in Focus after load without a bell fetch");
    }
    const slowLabels = actionLabels(slowCard);
    if (!slowLabels.includes("Approve") || !slowLabels.includes("Reject")) {
        throw new Error(`projected origin-thread card must offer Approve/Reject, got ${slowLabels.join(",")}`);
    }
    if ((store.getState().inlineNeedIds || []).join(",") !== "appr-slow") {
        throw new Error(`inlineNeedIds after live need paint, got ${(store.getState().inlineNeedIds || []).join(",")}`);
    }
    const barEl = conversation.element.querySelector(".needs-bar");
    if (!barEl || barEl.hidden !== true) {
        throw new Error("Needs-bar must stay quiet once the inline Approve card is present");
    }
    const paintsNeedWithoutBellFetch = true;

    process.stdout.write(JSON.stringify({
        ok: true,
        paintsCreateChrome,
        paintsLiveAppend,
        paintsLiveChannelAppend,
        paintsNeedWithoutBellFetch,
        refetchesWhenInlineMissing,
    }));
})().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
