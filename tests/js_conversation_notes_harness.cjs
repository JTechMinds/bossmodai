/**
 * Node harness: the conversation-notes divider on a real conversation.
 * Invoked by tests/test_conversation_notes.py. Not a browser bundle.
 *
 * Every module is the real one (the conversation harness's stack), so the
 * properties here are the controller, the window wrapper, the transcript and
 * the event cards agreeing: the divider lands after the last summarized row,
 * earlier rows are faded and point at it, a failed run is said in words, and
 * an open divider stays open through a full repaint.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModMarkdown", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus", "BossModOperatorInvalidate", "BossModFormat", "BossModGates",
    "BossModConsentCard", "BossModOverlayFocus", "BossModModalTrail", "BossModOverlayActions", "BossModOverlays", "BossModMenu", "BossModMenuButton", "BossModEmptyState",
    "BossModTranscript", "BossModTranscriptCache", "BossModMessage", "BossModEventCards",
    "BossModTitleRename", "BossModChromeMenu", "BossModConversationChrome",
    "BossModDesktopClipboard", "BossModComposerAttachments", "BossModComposer", "BossModSystemReceipts", "BossModNeedShape", "BossModNeedsBar", "BossModThreadArchive",
    "BossModThreadSeat",
    "BossModThreadRequests", "BossModAutoApproveSwitch", "BossModThreadSource", "BossModAgentRequests", "BossModAgentSource",
    "BossModConversationFocus", "BossModConversationWindow", "BossModChatRewindDialog", "BossModChatRewind", "BossModConsentActivity", "BossModConversation",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModConversation, BossModConversationWindow } = global;

const DM_ROWS = [
    { id: "m1", from: "human", content: "can you draft the plan?", created_at: "2026-10-12T09:00:00" },
    { id: "m2", from: "agent", content: "on it", created_at: "2026-10-12T09:00:05" },
    { id: "m3", from: "human", content: "and the budget?", created_at: "2026-10-12T09:10:00" },
    { id: "m4", from: "agent", content: "budget next", created_at: "2026-10-12T09:10:05" },
];
const THREAD_ROWS = [
    { id: "c1", author_type: "agent", author_agent_id: "a", author_name: "Ada", content: "old line", created_at: "" },
    // A round marker the transcript hides: the first row the agents see.
    { id: "c2", author_type: "system", author_name: "BossMod", content: "Round 2", notification_kind: "channel_round_marker", created_at: "" },
    { id: "c3", author_type: "agent", author_agent_id: "a", author_name: "Ada", content: "new line", created_at: "" },
];
const WINDOWS = {
    "human_dm/a": {
        notes: {
            through_message_id: "m2",
            notes: "- Ada owes the plan draft",
            updated_at: "2026-10-12T09:20:00Z",
            run_state: "failed",
            last_error: "System AI returned no notes",
        },
        visible_from_message_id: "m3",
        ceiling_hit: false,
    },
    "channel/t1": { notes: null, visible_from_message_id: "c2", ceiling_hit: true },
};
const windowRequests = [];

const api = async (url) => {
    const text = String(url);
    const seen = text.match(/^\/api\/conversations\/([^/]+)\/([^/]+)\/window$/);
    if (seen) {
        const key = `${decodeURIComponent(seen[1])}/${decodeURIComponent(seen[2])}`;
        windowRequests.push(key);
        const body = WINDOWS[key];
        if (!body) return { ok: false, status: 404, async text() { return "Not found"; } };
        return { ok: true, async json() { return JSON.parse(JSON.stringify(body)); } };
    }
    if (/^\/api\/agents\/a\/messages/.test(text)) return { ok: true, async json() { return DM_ROWS; } };
    if (/^\/api\/agents\/a$/.test(text)) {
        return { ok: true, async json() { return { id: "a", cli_auto_approve_dm: false, cli_auto_approve_global: false }; } };
    }
    if (/^\/api\/channels\/t1$/.test(text)) {
        return {
            ok: true,
            async json() {
                return { channel: { id: "t1", name: "Standup", status: "active", members: [] }, messages: THREAD_ROWS };
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

const store = BossModStore.createStore({
    roster: [{ id: "a", name: "Ada", role: "Writer", color: "#1d4ed8" }],
    threads: [],
    hasUsableModel: true,
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
BossModOperatorInvalidate.attach({ bus });
const needsStub = {
    refresh: () => Promise.resolve(),
    resolve: () => Promise.resolve(),
    acknowledge: () => {},
    getError: () => "",
    subscribeError: () => () => {},
    destroy: () => {},
};
const conversation = BossModConversation.createConversation({
    store, bus, api, navigate() {}, needs: needsStub, drafts: new Map(),
    cache: global.BossModTranscriptCache.createCache(),
});
const tick = () => new Promise((resolve) => setImmediate(resolve));

function rows() {
    return conversation.element.querySelectorAll("[data-message-key]")
        .filter((node) => node.parent && node.parent.className === "transcript-list");
}

function describe(node) {
    return {
        key: node.getAttribute("data-message-key"),
        faded: String(node.className).split(/\s+/).includes("msg-turn-faded"),
        describedBy: node.getAttribute("aria-describedby"),
    };
}

function divider() {
    return conversation.element.querySelector(".conversation-notes");
}

function dividerState() {
    const node = divider();
    if (!node) return null;
    const toggle = node.querySelector(".conversation-notes-toggle");
    const body = node.querySelector(".conversation-notes-body");
    const failure = node.querySelector(".conversation-notes-failure");
    const text = node.querySelector(".note-text");
    return {
        id: node.getAttribute("id"),
        text: text ? text.textContent : "",
        toggle: Boolean(toggle),
        expanded: toggle ? toggle.getAttribute("aria-expanded") : null,
        controls: toggle ? toggle.getAttribute("aria-controls") : null,
        bodyHidden: body ? body.hidden : null,
        bodyText: body ? body.textContent : "",
        failure: failure ? failure.textContent : "",
    };
}

async function main() {
    await conversation.open("a", "agent");
    const dmRows = rows().map(describe);
    const collapsed = dividerState();

    await divider().querySelector(".conversation-notes-toggle").dispatchClick();
    const expanded = dividerState();

    // A notes change for another conversation is not this one's.
    const before = windowRequests.length;
    bus.publish("conversation_notes_updated", { scope_kind: "human_dm", scope_key: "zz" });
    await tick();
    const otherScopeRefetched = windowRequests.length !== before;

    // This conversation's notes changed: a full reload repaints every row.
    const oldNode = divider();
    bus.publish("conversation_notes_updated", { scope_kind: "human_dm", scope_key: "a" });
    for (let i = 0; i < 5; i += 1) await tick();
    const refetched = windowRequests.length === before + 1;
    const repainted = divider() !== oldNode;
    const afterRepaint = dividerState();

    await conversation.open("t1", "thread");
    const threadRows = rows().map(describe);
    const ceiling = dividerState();

    console.log(JSON.stringify({
        compactedTitle: BossModConversationWindow.COMPACTED_TITLE,
        ceilingTitle: BossModConversationWindow.CEILING_TITLE,
        dmRows,
        collapsed,
        expanded,
        otherScopeRefetched,
        refetched,
        repainted,
        afterRepaint,
        threadRows,
        ceiling,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
