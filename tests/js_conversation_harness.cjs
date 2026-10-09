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
// core/markdown.js reads `marked`, `hljs` and `DOMParser`; Node has none
// of them, and this harness is not what proves the sanitiser correct.
require("./js_markdown_stub.cjs").installMarkdownStub(documentStub);
// The chrome paints its action glyphs after every apply.
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
    "BossModConversationFocus", "BossModChatRewindDialog", "BossModChatRewind", "BossModConsentActivity", "BossModConversation",
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
// Settings → Advanced "Global auto-approve", as the server reports it on
// every thread and agent payload, and each agent's own DM flag.
let autoApproveGlobal = false;
const agentAutoApprove = {};
const autoApprovePatches = [];
const agentRecord = (id) => ({
    id,
    cli_auto_approve_dm: agentAutoApprove[id] === true,
    cli_auto_approve_global: autoApproveGlobal,
});
const RENAME_FAILURE = "Thread name cannot be empty";
const renamePayloads = [];
let renameFails = false;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tick = () => new Promise((resolve) => setImmediate(resolve));

const activations = [];
// Held open to keep a send in flight; null lets activations answer at once.
let activationGate = null;
// The 422 a send gets when its attachments cannot be linked.
let linkRefused = false;
const LINK_REFUSAL = "Some attachments are unknown, already sent, or belong to another conversation.";
const linkRefusal = () => ({
    ok: false,
    status: 422,
    async text() {
        return JSON.stringify({ detail: { error: LINK_REFUSAL, code: "ATTACHMENT_LINK", missing_ids: ["gone"] } });
    },
});
const api = async (url, init) => {
    const text = String(url);
    requestLog.push(text);
    const activate = text.match(/^\/api\/agents\/([^/]+)\/activate$/);
    if (activate) {
        activations.push({ id: activate[1], body: JSON.parse((init && init.body) || "{}") });
        if (activationGate) await activationGate;
        if (linkRefused) return linkRefusal();
        return { ok: true, async json() { return {}; } };
    }
    if (/^\/api\/channels\/[^/]+\/messages$/.test(text) && linkRefused) return linkRefusal();
    const dmFlag = text.match(/^\/api\/agents\/([^/]+)\/cli-auto-approve$/);
    if (dmFlag) {
        const body = JSON.parse(init.body);
        autoApprovePatches.push({ id: dmFlag[1], method: init.method, body });
        agentAutoApprove[dmFlag[1]] = body.enabled === true;
        return { ok: true, async json() { return agentRecord(dmFlag[1]); } };
    }
    const record = text.match(/^\/api\/agents\/([^/?]+)$/);
    if (record) return { ok: true, async json() { return agentRecord(record[1]); } };
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
                return {
                    channel: { ...room, members: room.members, cli_auto_approve_global: autoApproveGlobal },
                    messages: [],
                };
            },
        };
    }
    throw new Error(`unhandled ${text}`);
};

// conversation.js uploads through the shared client, which the browser
// defines in api-client.js. Scripted here: the id names the conversation it
// was uploaded for, so a chip that followed the wrong conversation shows.
const uploads = [];
const blobFetches = [];
global.BossModApi = {
    async uploadAttachment(file, context) {
        uploads.push(file.name);
        return { id: `${context.id}:${file.name}`, file_name: file.name, file_size: 1, mime_type: file.type, preview_tier: "image" };
    },
    async deleteAttachment() {},
    async getAttachmentLimits() { return { max_size_mb: 10, max_per_message: 5 }; },
    // Thumbnails load through the token-carrying fetch, never a bare <img src>.
    async fetchBlobUrl(url) {
        blobFetches.push(url);
        if (url.includes("broken")) throw new Error("Request failed (401)");
        return `blob:${url}`;
    },
    fetch: api,
};

const store = BossModStore.createStore({
    roster: [
        { id: "a", name: "Ada", role: "Writer", color: "#1d4ed8" },
        { id: "b", name: "Bo", role: "Reviewer" },
        { id: "c", name: "Cy", role: "Engineer" },
        { id: "d", name: "Di", role: "Designer", color: "#065f46" },
    ],
    threads: [],
    hasUsableModel: true,
});
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const offOperatorInvalidate = BossModOperatorInvalidate.attach({ bus });

// The bar is part of the surface now; the queue behind it is exercised in
// js_needs_harness.cjs, so an empty one is enough to build the conversation.
const needsStub = {
    refresh: () => Promise.resolve(),
    resolve: () => Promise.resolve(),
    // The agent DM's Rewind clears an error card through this.
    acknowledge: () => {},
    getError: () => "",
    subscribeError: () => () => {},
    destroy: () => {},
};

const openedDesks = [];
// The Browser Vision capability, as chat-place.js injects it: which agents
// have a view, change notifications, and the viewer.
const browserViews = new Set();
const viewListeners = new Set();
const openedViews = [];
const browserView = {
    hasView: (id) => browserViews.has(id),
    subscribe: (fn) => { viewListeners.add(fn); return () => viewListeners.delete(fn); },
    open: (id, name) => openedViews.push([id, name]),
};
const announceViews = () => viewListeners.forEach((fn) => fn());
// Owned by the caller so they outlive one controller, as chat-place.js does.
const drafts = new Map();
const transcriptCache = global.BossModTranscriptCache.createCache();
const conversation = BossModConversation.createConversation({
    store, bus, api, navigate() {}, needs: needsStub, drafts, cache: transcriptCache,
    // Injected so the chrome has an action to carry a glyph on.
    openDesk: (id) => openedDesks.push(id),
    browserView,
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

    const adaTurn = conversation.element.querySelector(".msg-turn-agent");
    const adaFace = adaTurn && adaTurn.querySelector(".msg-face");
    const adaName = adaTurn && adaTurn.querySelector(".msg-author");
    const adaBubble = adaTurn && adaTurn.querySelector(".msg");
    const adaStack = adaTurn && adaTurn.querySelector(".msg-stack");
    const nameOutsideBubble = Boolean(
        adaName && adaBubble && adaStack
        && !adaBubble.contains(adaName)
        && adaStack.contains(adaName)
        && adaStack.contains(adaBubble)
        && adaStack.children[0] === adaName
    );
    const faceLowerLeftBesideBubble = Boolean(
        adaFace && adaBubble && adaStack
        && adaTurn.children[0] === adaFace
        && adaTurn.children[1] === adaStack
        && !adaStack.contains(adaFace)
        && !adaBubble.contains(adaFace)
    );
    const nameIsQuiet = Boolean(
        adaName && adaName.textContent === "Ada"
        && !/background:/.test(adaName.getAttribute("style") || "")
        && !/border:/.test(adaName.getAttribute("style") || "")
    );
    const conversationCss = fs.readFileSync(
        require("path").join(__dirname, "..", "ui", "static", "css", "conversation.css"),
        "utf8",
    );
    const authorRule = conversationCss.split(".msg-author {")[1].split("}")[0];
    const quietAuthor = /font-weight:\s*400/.test(authorRule)
        && !/font-weight:\s*600/.test(authorRule)
        && /color:\s*var\(--muted\)/.test(authorRule)
        && !/color:\s*var\(--ink\)/.test(authorRule)
        && /background:\s*none/.test(authorRule)
        && !/background:\s*var\(--btn-face-active\)/.test(authorRule)
        && /border:\s*0/.test(authorRule)
        && !/border:\s*1px/.test(authorRule)
        && !/border-radius:\s*999px/.test(authorRule);
    if (!quietAuthor) throw new Error(`quiet author missing: ${authorRule}`);
    const adaTint = global.BossModAvatar.tintFor("#1d4ed8");
    const authorUsesAgentColor = Boolean(
        adaName
        && adaTint
        && (adaName.getAttribute("style") || "").indexOf(`color:${adaTint.ink}`) !== -1
        && !/var\(--muted\)/.test(adaName.getAttribute("style") || "")
    );
    if (!authorUsesAgentColor) {
        throw new Error(
            `author must use agent color: style=${adaName && adaName.getAttribute("style")} `
            + `ink=${adaTint && adaTint.ink}`
        );
    }
    const turnRule = conversationCss.split(".msg-turn {")[1].split("}")[0];
    const faceBottomAligned = /align-items:\s*flex-end/.test(turnRule)
        && !/flex-direction:\s*column/.test(turnRule);
    if (!faceBottomAligned) throw new Error(`initial must sit lower-left: ${turnRule}`);
    const listRule = conversationCss.split(".transcript-list {")[1].split("}")[0];
    const turnGap = /gap:\s*16px/.test(listRule) && !/gap:\s*8px/.test(listRule);
    if (!turnGap) throw new Error(`turn gap must breathe: ${listRule}`);
    const agentNameIsChromeOutside = Boolean(
        adaTurn && adaFace && nameOutsideBubble && faceLowerLeftBesideBubble
        && nameIsQuiet && quietAuthor && faceBottomAligned && authorUsesAgentColor
    );
    if (!agentNameIsChromeOutside) {
        throw new Error(
            `agent name chrome wrong: face=${Boolean(adaFace)} name=${adaName && adaName.textContent} `
            + `outside=${nameOutsideBubble} lowerLeft=${faceLowerLeftBesideBubble} `
            + `quiet=${nameIsQuiet}`
        );
    }

    // Live miss after #98: an agent face with showAuthor off still paints
    // quiet .msg-author. TheAuditor's top turn was an orphan initial.
    const auditorTurn = global.BossModMessage.renderMessage({
        kind: "message",
        key: "auditor-1",
        author: "agent",
        authorName: "TheAuditor",
        authorColor: "#6d28d9",
        showAuthor: false,
        text: "Review parked.",
        createdAt: "",
    });
    const auditorFace = auditorTurn.querySelector(".msg-face");
    const auditorName = auditorTurn.querySelector(".msg-author");
    const auditorBubble = auditorTurn.querySelector(".msg");
    const auditorStack = auditorTurn.querySelector(".msg-stack");
    const auditorTint = global.BossModAvatar.tintFor("#6d28d9");
    const noOrphanAgentFace = Boolean(
        auditorFace
        && auditorName
        && auditorName.textContent === "TheAuditor"
        && auditorTurn.children[0] === auditorFace
        && auditorStack
        && auditorStack.contains(auditorName)
        && auditorBubble
        && !auditorBubble.contains(auditorName)
        && auditorTint
        && (auditorName.getAttribute("style") || "").indexOf(`color:${auditorTint.ink}`) !== -1
    );
    if (!noOrphanAgentFace) {
        throw new Error(
            `orphan agent face: face=${Boolean(auditorFace)} name=${auditorName && auditorName.textContent}`
        );
    }

    // The human's own line is "You" whatever name the row carries: the
    // agent-facing label ("Jordan (the boss)") is not presentation.
    const humanTurn = global.BossModMessage.renderMessage({
        kind: "message",
        key: "human-1",
        author: "human",
        authorName: "Jordan (the boss)",
        showAuthor: false,
        text: "Ship it.",
        createdAt: "",
    });
    const humanFace = humanTurn.querySelector(".msg-face");
    const humanFaceSaysYou = Boolean(humanFace && humanFace.textContent === "Y");
    if (!humanFaceSaysYou) {
        throw new Error(`human face must be "You": face=${humanFace && humanFace.textContent}`);
    }

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

    // Open desk lives behind the `⋯`, in the Agent section — one `⋯` per
    // header, and nothing icon-only beside it. The source still hands over a
    // glyph NAME and the view is the only thing that builds an element from
    // it; inside the panel the label is visible text, so the row names itself.
    const rowButtons = () => conversation.element
        .querySelector(".conversation-actions").querySelectorAll("button");
    if (rowButtons().some((btn) => btn.getAttribute("id") === "conversation-open-desk")) {
        throw new Error("Open desk must not sit in the header row");
    }
    // The panel's shape: its sections (and the rule between them), their
    // headings, and each section's rows across both of its bodies.
    const panelSections = (open) => open.querySelector(".menu-sections").children;
    const headingsOf = (open) => open.querySelectorAll(".menu-label").map((label) => label.textContent);
    const sectionRows = (section) => section.querySelectorAll(".menu-actions")
        .flatMap((body) => body.querySelectorAll("button"))
        .map((btn) => btn.textContent);
    // A section is a group named by its own heading: one string on screen and
    // in the accessibility tree.
    const sectionIsLabelled = (section) => {
        const heading = section.querySelector(".menu-label");
        return section.getAttribute("role") === "group" && Boolean(heading)
            && Boolean(heading.getAttribute("id"))
            && section.getAttribute("aria-labelledby") === heading.getAttribute("id");
    };
    const deskDots = conversation.element.querySelector("#conversation-view-options");
    await deskDots.dispatchClick();
    const deskPanel = conversation.element.querySelector(".menu");
    const deskBtn = deskPanel && deskPanel.querySelector("#conversation-open-desk");
    if (!deskBtn) throw new Error("an injected openDesk must produce Open desk behind the `⋯`");
    const glyph = deskBtn.querySelector("i");
    const deskSection = deskBtn.closest(".menu-section");
    const chromeActionCarriesItsIcon = Boolean(glyph)
        && glyph.getAttribute("data-lucide") === "lamp-desk"
        && deskBtn.textContent === "Open desk"
        && deskBtn.getAttribute("aria-label") === null
        && Boolean(deskSection)
        && deskSection.querySelector(".menu-label").textContent === "Agent";
    if (!chromeActionCarriesItsIcon) {
        throw new Error(`Open desk must be a glyph and its words under "Agent": `
            + `${deskBtn.textContent} / ${deskSection && deskSection.querySelector(".menu-label").textContent}`);
    }
    // The DM's panel: Agent, one rule, then Chat — and inside Chat the action
    // (Rewind…) comes before the settings, the doing first.
    const dmSections = panelSections(deskPanel);
    const dmMenuIsSectioned = headingsOf(deskPanel).join("|") === "Agent|Chat"
        && dmSections.length === 3
        && dmSections[1].matches(".menu-divider")
        && deskPanel.querySelectorAll(".menu-divider").length === 1
        && sectionRows(dmSections[0]).join("|") === "Open desk"
        && sectionRows(dmSections[2]).join("|")
            === "Rewind…|Auto-approve safe commands|Show system notifications"
        && sectionIsLabelled(dmSections[0]) && sectionIsLabelled(dmSections[2]);
    if (!dmMenuIsSectioned) {
        throw new Error(`the DM's \`⋯\` must read Agent | Chat, got ${headingsOf(deskPanel).join("|")}: `
            + dmSections.map((node) => (node.matches(".menu-divider") ? "—" : sectionRows(node).join("|"))).join(" / "));
    }
    // Choosing it puts the panel away first, then opens this agent's desk.
    await deskBtn.dispatchClick();
    await tick();
    const deskOpensFromTheMenu = openedDesks.join("|") === "a"
        && conversation.element.querySelectorAll(".menu").length === 0
        && deskDots.getAttribute("aria-expanded") === "false";
    if (!deskOpensFromTheMenu) {
        throw new Error(`Open desk must close the panel and open "a", got ${openedDesks.join("|")}`);
    }

    // The Browser Vision screen: absent until the status lists this agent,
    // then a soft-green, icon-only "Browser view" — the row's first action,
    // now that Desk is behind the `⋯`; gone again when the view goes (the
    // extension was turned off).
    const viewBtn = () => conversation.element.querySelector("#conversation-browser-view");
    const browserViewAbsentWithoutAView = !viewBtn() && viewListeners.size === 1;
    browserViews.add("a");
    announceViews();
    const shown = viewBtn();
    const shownGlyph = shown && shown.querySelector("i");
    const browserViewShowsWhenListed = Boolean(shown)
        && shown.getAttribute("data-tone") === "live"
        && shown.getAttribute("aria-label") === "Browser view"
        && Boolean(shownGlyph) && shownGlyph.getAttribute("data-lucide") === "monitor"
        // The first action button in the row.
        && rowButtons()[0] === shown;
    await shown.click();
    const browserViewOpensTheViewer = openedViews.length === 1 && openedViews[0][0] === "a";
    browserViews.delete("a");
    announceViews();
    const browserViewGoesWhenTurnedOff = !viewBtn();

    // A thread has no one face, so it gets the group glyph rather than nothing.
    await conversation.open("t1", "thread");
    // Leaving the agent's conversation drops its status subscription.
    const browserViewUnsubscribesOnLeave = viewListeners.size === 0;
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

    // `More actions`, not `View options`: the panel holds the source's
    // `slot: 'menu'` actions as well as the surface's preferences now.
    const dots = conversation.element.querySelector("#conversation-view-options");
    if (!dots) throw new Error("the header must offer an overflow menu");
    if (dots.getAttribute("aria-label") !== "More actions"
        || dots.getAttribute("data-tooltip") !== "More actions") {
        throw new Error(`icon-only needs its own name, got "${dots.getAttribute("aria-label")}"`);
    }
    if (dots.getAttribute("aria-expanded") !== "false") {
        throw new Error("the `⋯` must report its panel as closed before it is opened");
    }
    // The app's one `⋯` (core/menu-button.js): the plain header glyph, never
    // a bordered button.
    const dotsClasses = String(dots.getAttribute("class") || "").split(/\s+/).filter(Boolean);
    if (dotsClasses.join(" ") !== "menu-trigger" || dots.getAttribute("data-size") !== "header") {
        throw new Error(`the header's ⋯ must be the shared .menu-trigger[data-size="header"] and no .btn, `
            + `got class "${dots.getAttribute("class")}" size "${dots.getAttribute("data-size")}"`);
    }
    await dots.dispatchClick();
    const panel = conversation.element.querySelector(".menu");
    // The panel is not the receipts switch's alone: a live thread puts its own
    // `slot: 'menu'` switch ("Auto-approve safe commands") in there too, ahead
    // of the view options. So the receipts switch is found by its label, and
    // must be there exactly once; the thread's switch must be there as well.
    const receiptsIn = (root) => root.querySelectorAll(".switch-row").filter((row) =>
        row.querySelector(".switch-label").textContent === "Show system notifications");
    const receiptsToggleReachableFromMenu = Boolean(panel)
        && panel.getAttribute("role") === "dialog"
        && receiptsIn(panel).length === 1
        && Boolean(panel.querySelector("#channel-cli-auto-approve"))
        && dots.getAttribute("aria-expanded") === "true";
    if (!receiptsToggleReachableFromMenu) {
        throw new Error(`the receipts toggle must be reachable from the menu: `
            + `${panel && panel.getAttribute("role")}`);
    }

    // Toggling it from its new home writes the SAME storage key it always did.
    const receiptsNode = receiptsIn(panel)[0];
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
        receiptsIn(conversation.element.querySelector(".menu"))[0] === receiptsNode;
    if (!receiptsNodeSurvivesReopen) {
        throw new Error("re-opening the menu must reuse the preference control");
    }
    await dots.dispatchClick();

    // ...and it survives a conversation switch rather than being rebuilt with
    // the actions around it.
    await conversation.open("a", "agent");
    await dots.dispatchClick();
    if (receiptsIn(conversation.element.querySelector(".menu"))[0] !== receiptsNode) {
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
        .filter((name) => name && name !== "More actions");
    // `3 participants` sits at the END of the row with the actions now — a
    // fact about the room, where a long name cannot shove it.
    const subtitleIsWithTheActions = () => Boolean(conversation.element
        .querySelector(".conversation-actions")
        .querySelector(".conversation-subtitle"));
    /**
     * The overflow panel's row labels, every section's bodies in DOM order,
     * with the panel opened and shut again.
     *
     * Archive and Reopen moved behind the `⋯`: rare and irreversible-looking,
     * so they do not hold a permanent seat beside the title. Reading them means
     * opening the panel, which is the point — the row is clean at rest.
     */
    const menuActionNames = async () => {
        const dotsBtn = conversation.element.querySelector("#conversation-view-options");
        if (!dotsBtn) return null;
        await dotsBtn.dispatchClick();
        const open = conversation.element.querySelector(".menu");
        const names = open
            ? open.querySelectorAll(".menu-actions")
                .flatMap((body) => body.querySelectorAll("button"))
                .map((btn) => btn.textContent)
            : [];
        await dotsBtn.dispatchClick();
        return names.join("|");
    };
    // The rename pair moved BESIDE the title — the thing they act on. They sat
    // at the far right of the header for a while, which meant crossing the bar
    // to answer a question it was asking on the left.
    const RENAME_IDS = ["conversation-title-cancel", "conversation-title-save"];
    const renameButtons = () => conversation.element
        .querySelector(".conversation-title-actions")
        .querySelectorAll("button")
        .filter((btn) => RENAME_IDS.includes(btn.getAttribute("id")));
    const renameSlotNames = () => conversation.element
        .querySelector(".conversation-title-actions")
        .querySelectorAll("button")
        .map((btn) => btn.textContent || btn.getAttribute("aria-label") || "")
        .filter((name) => name);
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
    // At rest the row carries no action at all: Add people… moved behind the
    // `⋯` with the thread's other controls.
    if (actionLabels().join("|") !== "") {
        throw new Error(`at rest the row carries no text action, got ${actionLabels().join("|")}`);
    }
    if (actionNames().join("|") !== ""
        || conversation.element.querySelector(".conversation-actions").querySelector("#channel-seat-btn")) {
        throw new Error(`a live thread's row must be empty at rest, got ${actionNames().join("|")}`);
    }
    // The panel, in DOM order: the Thread section (Add people…, Pause, then
    // Archive), then the Chat section's switches. Archive is in there and
    // nowhere on the row.
    const archiveLivesInTheMenu = await menuActionNames()
        === "Add people…|Pause thread|Archive|Auto-approve safe commands|Show system notifications";
    if (!archiveLivesInTheMenu) {
        throw new Error(`Archive must be behind the \`⋯\`, got ${await menuActionNames()}`);
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
    // The pair joins the action row through the same descriptor every other
    // action uses. Round four replaced the word `Save` with a green check and
    // added the red cross beside it — the operator's ask, and until then Esc
    // cancelled and nothing said so. Archive used to share this row and is
    // behind the `⋯` now, so the pair is the whole of it while a rename is open.
    // Beside the TITLE, and nowhere near the action row at the other end.
    const saveActionAppearsBesideArchive =
        renameSlotNames().join("|") === "Cancel rename|Save name"
        && actionNames().join("|") === ""
        && Boolean(conversation.element.querySelector("#conversation-title-save"));
    if (!saveActionAppearsBesideArchive) {
        throw new Error(`the rename pair must sit beside the title, got `
            + `${renameSlotNames().join("|")} / ${actionNames().join("|")}`);
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
        && actionLabels().join("|") === "";
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
        && renameSlotNames().join("|") === "";
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
        && actionLabels().join("|") === "";
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
        && renameSlotNames().join("|") === "Cancel rename|Save name";
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
        && actionLabels().join("|") === ""
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

    // ─── Auto-approve: the DM's own switch, and Global greying both out ───
    //
    // The DM switch is the thread switch's twin (auto-approve-switch.js):
    // same label, same place behind the `⋯`, its own flag on the agent.
    // While Global auto-approve is on, every conversation's switch shows ON,
    // is disabled and announced so, and says where the setting that wins
    // lives — as a visible line that is also its accessible description.
    const openMenu = async () => {
        const dotsBtn = conversation.element.querySelector("#conversation-view-options");
        await dotsBtn.dispatchClick();
        return { dotsBtn, panel: conversation.element.querySelector(".menu") };
    };
    const switchState = (panel, id) => {
        const row = panel.querySelector(`#${id}`);
        if (!row) return null;
        const hint = panel.querySelector(`#${id}-hint`);
        return {
            label: row.querySelector(".switch-label").textContent,
            checked: row.getAttribute("aria-checked"),
            disabled: row.disabled,
            ariaDisabled: row.getAttribute("aria-disabled"),
            describedBy: row.getAttribute("aria-describedby"),
            hint: hint ? hint.textContent : null,
            hintOutsideTheRow: Boolean(hint) && !row.contains(hint),
        };
    };
    await conversation.open("a", "agent");
    let menu = await openMenu();
    const dmOff = switchState(menu.panel, "agent-cli-auto-approve");
    const dmSwitchStartsOffAndLive = Boolean(dmOff)
        && dmOff.label === "Auto-approve safe commands"
        && dmOff.checked === "false"
        && dmOff.disabled === false
        && dmOff.ariaDisabled === null
        && dmOff.describedBy === null
        && dmOff.hint === null;
    if (!dmSwitchStartsOffAndLive) {
        throw new Error(`the DM switch must start off and live, got ${JSON.stringify(dmOff)}`);
    }
    await menu.panel.querySelector("#agent-cli-auto-approve").dispatchClick();
    await tick();
    await tick();
    const dmPatch = autoApprovePatches[autoApprovePatches.length - 1];
    menu = await openMenu();
    const dmOn = switchState(menu.panel, "agent-cli-auto-approve");
    const dmSwitchPatchesTheAgent = autoApprovePatches.length === 1
        && dmPatch.id === "a" && dmPatch.method === "PATCH" && dmPatch.body.enabled === true
        && dmOn.checked === "true" && dmOn.disabled === false;
    if (!dmSwitchPatchesTheAgent) {
        throw new Error(`the DM switch must PATCH its agent, got `
            + `${JSON.stringify(autoApprovePatches)} / ${JSON.stringify(dmOn)}`);
    }
    await menu.dotsBtn.dispatchClick();

    // A Settings save of Global auto-approve reaches an open conversation as
    // operator_invalidate(chat), which refetches it.
    autoApproveGlobal = true;
    BossModOperatorInvalidate.notifyLocal(["advanced-system", "chat"]);
    await wait(5);
    menu = await openMenu();
    const dmGlobal = switchState(menu.panel, "agent-cli-auto-approve");
    await menu.dotsBtn.dispatchClick();
    await conversation.open("t1", "thread");
    menu = await openMenu();
    const threadGlobal = switchState(menu.panel, "channel-cli-auto-approve");
    await menu.dotsBtn.dispatchClick();
    const greyedOut = (state, id) => Boolean(state)
        && state.checked === "true"
        && state.disabled === true
        && state.ariaDisabled === "true"
        && state.hint === BossModAutoApproveSwitch.GLOBAL_HINT
        && state.describedBy === `${id}-hint`
        && state.hintOutsideTheRow;
    const globalGreysOutTheDmSwitch = greyedOut(dmGlobal, "agent-cli-auto-approve");
    const globalGreysOutTheThreadSwitch = greyedOut(threadGlobal, "channel-cli-auto-approve");
    if (!globalGreysOutTheDmSwitch || !globalGreysOutTheThreadSwitch) {
        throw new Error(`Global must grey out both switches, got `
            + `${JSON.stringify(dmGlobal)} / ${JSON.stringify(threadGlobal)}`);
    }

    // Off again: each conversation's own flag comes back, live and unhinted.
    autoApproveGlobal = false;
    BossModOperatorInvalidate.notifyLocal(["advanced-system", "chat"]);
    await wait(5);
    menu = await openMenu();
    const threadBack = switchState(menu.panel, "channel-cli-auto-approve");
    await menu.dotsBtn.dispatchClick();
    await conversation.open("a", "agent");
    menu = await openMenu();
    const dmBack = switchState(menu.panel, "agent-cli-auto-approve");
    await menu.dotsBtn.dispatchClick();
    const ownFlagsComeBack = threadBack.checked === "false" && threadBack.disabled === false
        && threadBack.ariaDisabled === null && threadBack.hint === null
        && threadBack.describedBy === null
        && dmBack.checked === "true" && dmBack.disabled === false
        && dmBack.ariaDisabled === null && dmBack.hint === null;
    if (!ownFlagsComeBack) {
        throw new Error(`turning Global off must restore each flag, got `
            + `${JSON.stringify(threadBack)} / ${JSON.stringify(dmBack)}`);
    }

    // ─── The thread's `⋯`: Thread, one rule, then Chat ───
    await conversation.open("t1", "thread");
    menu = await openMenu();
    const threadSections = panelSections(menu.panel);
    const threadMenuIsSectioned = headingsOf(menu.panel).join("|") === "Thread|Chat"
        && threadSections.length === 3
        && threadSections[1].matches(".menu-divider")
        && menu.panel.querySelectorAll(".menu-divider").length === 1
        && sectionRows(threadSections[0]).join("|") === "Add people…|Pause thread|Archive"
        && sectionRows(threadSections[2]).join("|")
            === "Auto-approve safe commands|Show system notifications"
        && sectionIsLabelled(threadSections[0]) && sectionIsLabelled(threadSections[2])
        && !conversation.element.querySelector(".conversation-actions").querySelector("#channel-seat-btn");
    if (!threadMenuIsSectioned) {
        throw new Error(`the thread's \`⋯\` must read Thread | Chat, got ${headingsOf(menu.panel).join("|")}: `
            + threadSections.map((node) => (node.matches(".menu-divider") ? "—" : sectionRows(node).join("|"))).join(" / "));
    }
    // Archived elsewhere while the panel is open: the repaint lands in the
    // panel on screen, and Reopen lands in the Thread section.
    bus.publish("channel_updated", Object.assign({}, THREADS.t1, { status: "archived" }));
    const reopenSections = panelSections(menu.panel);
    const reopenLandsInTheThreadSection = conversation.element.querySelector(".menu") === menu.panel
        && headingsOf(menu.panel).join("|") === "Thread|Chat"
        && sectionRows(reopenSections[0]).join("|") === "Reopen"
        && reopenSections[0].querySelector(".menu-label").textContent === "Thread";
    if (!reopenLandsInTheThreadSection) {
        throw new Error(`Reopen must land in the open panel's Thread section, got `
            + `${headingsOf(menu.panel).join("|")}: ${sectionRows(reopenSections[0]).join("|")}`);
    }
    await menu.dotsBtn.dispatchClick();
    await tick();
    await tick();

    // A DM with no openDesk has nothing about the agent to put there: only
    // Chat, and no rule with nothing on its other side.
    const bare = BossModConversation.createConversation({
        store, bus, api, navigate() {}, needs: needsStub, drafts: new Map(),
        cache: global.BossModTranscriptCache.createCache(),
    });
    await bare.open("a", "agent");
    const bareDots = bare.element.querySelector("#conversation-view-options");
    await bareDots.dispatchClick();
    const barePanel = bare.element.querySelector(".menu");
    const dmWithoutDeskShowsOnlyChat = Boolean(barePanel)
        && headingsOf(barePanel).join("|") === "Chat"
        && barePanel.querySelectorAll(".menu-divider").length === 0
        && panelSections(barePanel).length === 1
        && !barePanel.querySelector("#conversation-open-desk");
    await bareDots.dispatchClick();
    bare.destroy();
    if (!dmWithoutDeskShowsOnlyChat) {
        throw new Error(`a DM without openDesk must show only Chat, got ${barePanel && headingsOf(barePanel).join("|")}`);
    }

    // No unheaded fallback: a menu action must name its section, and a
    // subject section must have the source's word for its heading.
    const lone = global.BossModConversationChrome.createChrome({ onError() {} });
    const refusalOf = (descriptor) => {
        try {
            lone.apply(descriptor);
        } catch (err) {
            return err.message;
        }
        return "";
    };
    const menuItem = (extra) => ({
        title: "x", subtitle: "",
        actions: [Object.assign({ id: "x-act", label: "X", slot: "menu", onSelect() {} }, extra)],
    });
    const UNSECTIONED = '[chrome] menu action "x-act" needs section "subject" or "chat"';
    const applyRefusesBadMenuActions = refusalOf(menuItem({})) === UNSECTIONED
        && refusalOf(menuItem({ section: "elsewhere" })) === UNSECTIONED
        && refusalOf(menuItem({ section: "subject" }))
            === "[chrome] a subject menu action needs descriptor.menuTitle"
        && refusalOf(Object.assign(menuItem({ section: "subject" }), { menuTitle: "Agent" })) === "";
    lone.destroy();
    if (!applyRefusesBadMenuActions) {
        throw new Error("apply must refuse a menu action without a known section, "
            + "and a subject action without menuTitle");
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

    // ─── attachmentsFollowTheirConversation ───
    // Pending uploads are scoped to one conversation on the server, so they
    // are stashed and restored with that conversation's text draft.
    const chips = () => conversation.element
        .querySelectorAll(".composer-attach-chip-name").map((node) => node.textContent);
    const paste = async (name) => {
        composerInput.dispatchEvent({
            type: "paste",
            clipboardData: { files: [{ name, type: "image/png" }] },
            preventDefault() {},
        });
        for (let i = 0; i < 5; i += 1) await tick();
    };
    await conversation.open("a", "agent");
    composerInput.value = "see file";
    await paste("x.png");
    const onA = chips().join("|");
    await conversation.open("b", "agent");
    const onB = chips().join("|");
    await conversation.open("a", "agent");
    const attachmentsFollowTheirConversation = onA === "x.png" && onB === ""
        && chips().join("|") === "x.png" && composerInput.value === "see file";
    if (!attachmentsFollowTheirConversation) {
        throw new Error(`chips must follow their conversation: a="${onA}" b="${onB}" back="${chips().join("|")}"`);
    }

    // ─── inFlightSendKeepsNewerChips ───
    // A send drops only the uploads it linked; one attached while it was in
    // flight belongs to the next message.
    let release = null;
    activationGate = new Promise((resolve) => { release = resolve; });
    const sent = activations.length;
    await conversation.element.querySelector(".composer-send").dispatchClick();
    await tick();
    await paste("y.png");
    release();
    activationGate = null;
    for (let i = 0; i < 5; i += 1) await tick();
    const inFlightSendKeepsNewerChips = chips().join("|") === "y.png"
        && JSON.stringify(activations[sent].body.attachment_ids) === JSON.stringify(["a:x.png"]);
    if (!inFlightSendKeepsNewerChips) {
        throw new Error(`in-flight send wrong: chips="${chips().join("|")}" sent=${JSON.stringify(activations[sent])}`);
    }

    // ─── pickedFileSurvivesInputReset / emptyPickSaysSo ───
    // A file input's FileList is live: resetting `value` right after the
    // change handler hands it off empties it. The tray must copy it first.
    await conversation.open("b", "agent");
    const errorText = () => conversation.element.querySelector(".composer-error").textContent;
    const fileInput = conversation.element.querySelector("input[type=file]");
    let liveFiles = null;
    const liveFileList = (files) => ({
        items: files.slice(),
        get length() { return this.items.length; },
        item(index) { return this.items[index] || null; },
        [Symbol.iterator]() { return this.items[Symbol.iterator](); },
    });
    Object.defineProperty(fileInput, "files", { configurable: true, get: () => liveFiles });
    Object.defineProperty(fileInput, "value", {
        configurable: true,
        get: () => "",
        set: (next) => { if (next === "" && liveFiles) liveFiles.items.length = 0; },
    });
    const uploadsBefore = uploads.length;
    liveFiles = liveFileList([{ name: "picked.pdf", type: "application/pdf", size: 4, lastModified: 1 }]);
    fileInput.dispatchEvent({ type: "change" });
    for (let i = 0; i < 5; i += 1) await tick();
    const pickedFileSurvivesInputReset = chips().join("|") === "picked.pdf"
        && uploads.slice(uploadsBefore).join("|") === "picked.pdf";
    if (!pickedFileSurvivesInputReset) {
        throw new Error(`the picked file must upload: chips="${chips().join("|")}" uploads=${JSON.stringify(uploads.slice(uploadsBefore))}`);
    }
    liveFiles = liveFileList([]);
    fileInput.dispatchEvent({ type: "change" });
    for (let i = 0; i < 5; i += 1) await tick();
    const emptyPickSaysSo = errorText() === "No file was attached." && chips().join("|") === "picked.pdf";
    if (!emptyPickSaysSo) throw new Error(`an empty pick must say so: "${errorText()}"`);

    // ─── pasteReadsItems / pasteTextIsPlain / pasteOfNothingSaysSo ───
    // WebKitGTK hands a pasted screenshot over as an item, with `files`
    // empty. The browser must never drop the image (or any markup) into the
    // field itself, so every paste is prevented and routed by the composer.
    const pasteWith = (clip) => {
        const event = { type: "paste", clipboardData: clip, prevented: false, preventDefault() { this.prevented = true; } };
        composerInput.dispatchEvent(event);
        return event;
    };
    const shot = { name: "shot.png", type: "image/png", size: 3, lastModified: 2 };
    const imagePaste = pasteWith({
        items: [{ kind: "file", type: "image/png", getAsFile: () => shot }],
        files: [],
        getData: () => "",
    });
    for (let i = 0; i < 5; i += 1) await tick();
    const pasteReadsItems = imagePaste.prevented && chips().join("|") === "picked.pdf|shot.png";
    if (!pasteReadsItems) throw new Error(`an items-only image paste must upload: "${chips().join("|")}"`);

    composerInput.value = "say ";
    const textPaste = pasteWith({
        items: [{ kind: "string", type: "text/html" }],
        files: [],
        getData: (type) => (type === "text/plain" ? "hello" : "<b>hello</b>"),
    });
    const pasteTextIsPlain = textPaste.prevented && composerInput.value === "say hello"
        && Array.from(composerInput.childNodes || []).every((node) => node.nodeType === 3);
    if (!pasteTextIsPlain) throw new Error(`a text paste must insert plain text: "${composerInput.value}"`);

    // ─── pasteReplacesSelection ───
    // A prevented paste gets no native replace, so the composer must delete
    // the selected text itself. The selection here runs to the end of the
    // draft, which is where the plain field shim (no caret) inserts.
    composerInput.value = "say hello";
    const draftText = composerInput.childNodes[0];
    const realGetSelection = global.window.getSelection;
    global.window.getSelection = () => ({
        rangeCount: 1,
        getRangeAt: () => ({
            collapsed: false,
            commonAncestorContainer: draftText,
            deleteContents() { draftText.textContent = draftText.textContent.slice(0, 4); },
        }),
    });
    pasteWith({ items: [], files: [], getData: (type) => (type === "text/plain" ? "bye" : "") });
    global.window.getSelection = realGetSelection;
    const pasteReplacesSelection = composerInput.value === "say bye";
    if (!pasteReplacesSelection) throw new Error(`a paste must replace the selection: "${composerInput.value}"`);

    const emptyPaste = pasteWith({ items: [], files: [], getData: () => "" });
    const pasteOfNothingSaysSo = emptyPaste.prevented
        && errorText() === "Could not read the pasted content. Use the 📎 button to attach files.";
    if (!pasteOfNothingSaysSo) throw new Error(`an unreadable paste must say so: "${errorText()}"`);
    composerInput.value = "";

    // ─── desktopPasteReadsTheShellClipboard / desktopPasteRefusalShowsReason ───
    // Inside the desktop shell, a paste with neither a file nor text (the
    // WebKitGTK screenshot) is read natively and attached as a PNG. Outside
    // it, pasteOfNothingSaysSo above is the browser's answer.
    const invoked = [];
    let clipboardRefusal = null;
    global.__TAURI__ = {
        core: {
            async invoke(command) {
                invoked.push(command);
                if (clipboardRefusal) throw clipboardRefusal;
                return new ArrayBuffer(8);
            },
        },
    };
    const desktopUploadsBefore = uploads.length;
    const desktopPaste = pasteWith({ items: [], files: [], getData: () => "" });
    for (let i = 0; i < 8; i += 1) await tick();
    const pastedName = uploads.slice(desktopUploadsBefore).join("|");
    const desktopPasteReadsTheShellClipboard = desktopPaste.prevented
        && invoked.join("|") === "read_clipboard_image_png"
        && /^pasted-image-\d{8}-\d{6}\.png$/.test(pastedName)
        && chips().includes(pastedName);
    if (!desktopPasteReadsTheShellClipboard) {
        throw new Error(`a desktop paste must attach the shell's image: invoked=${JSON.stringify(invoked)} uploaded="${pastedName}" chips="${chips().join("|")}"`);
    }
    clipboardRefusal = "The clipboard holds no image: The clipboard contents were not available in the requested format";
    pasteWith({ items: [], files: [], getData: () => "" });
    for (let i = 0; i < 8; i += 1) await tick();
    const desktopPasteRefusalShowsReason = errorText() === clipboardRefusal;
    if (!desktopPasteRefusalShowsReason) throw new Error(`a refused desktop read must say why: "${errorText()}"`);
    delete global.__TAURI__;

    // ─── thumbnailUsesAuthenticatedBlob / thumbnailIsCached / failedThumbnailShowsChip ───
    const shotAtt = { id: "att-1", file_name: "shot.png", file_size: 3, preview_tier: "image", company_path: "/lobby/.attachments/direct/a/u_shot.png" };
    const renderAtts = (atts) => BossModMessage.renderMessage({
        kind: "message", key: `k-${atts.map((a) => a.id).join("-")}`, author: "human", text: "", createdAt: "", attachments: atts,
    });
    const firstPaint = renderAtts([shotAtt]);
    for (let i = 0; i < 5; i += 1) await tick();
    const secondPaint = renderAtts([shotAtt]);
    for (let i = 0; i < 5; i += 1) await tick();
    const srcOf = (node) => node.querySelector(".msg-att-img").getAttribute("src");
    const thumbnailUsesAuthenticatedBlob = srcOf(firstPaint) === "blob:/api/attachments/att-1/preview";
    if (!thumbnailUsesAuthenticatedBlob) throw new Error(`thumbnail must load a blob URL: src=${srcOf(firstPaint)}`);
    const thumbnailIsCached = srcOf(secondPaint) === srcOf(firstPaint)
        && blobFetches.filter((url) => url.includes("att-1")).length === 1;
    if (!thumbnailIsCached) throw new Error(`a repaint must reuse the URL: ${JSON.stringify(blobFetches)}`);
    const brokenPaint = renderAtts([{ ...shotAtt, id: "broken", file_name: "gone.png" }]);
    for (let i = 0; i < 5; i += 1) await tick();
    const brokenChip = brokenPaint.querySelector(".file-chip");
    const failedThumbnailShowsChip = Boolean(brokenChip)
        && !brokenPaint.querySelector(".msg-att-img")
        && brokenChip.getAttribute("aria-label") === "gone.png: preview failed"
        && brokenChip.classList.contains("is-failed");
    if (!failedThumbnailShowsChip) throw new Error("a failed thumbnail must become a labelled chip");

    // ─── structuredRefusalShowsItsError ───
    // A 422 with `{detail: {error, code, missing_ids}}` reads as its error
    // text on the composer, for a DM and a thread alike — never raw JSON.
    const composerError = () => conversation.element.querySelector(".composer-error").textContent;
    linkRefused = true;
    const refusedErrors = [];
    for (const [id, kind] of [["a", "agent"], ["t1", "thread"]]) {
        await conversation.open(id, kind);
        composerInput.value = "refused";
        await conversation.element.querySelector(".composer-send").dispatchClick();
        for (let i = 0; i < 5; i += 1) await tick();
        refusedErrors.push(composerError());
    }
    linkRefused = false;
    const structuredRefusalShowsItsError = refusedErrors.every((text) => text === LINK_REFUSAL);
    if (!structuredRefusalShowsItsError) {
        throw new Error(`a structured refusal must show its error: ${JSON.stringify(refusedErrors)}`);
    }

    // ─── draftSurvivesDestroy ───
    // Leaving Chat destroys the controller; the draft (text and pending
    // upload) must be on the next controller built with the same drafts.
    await conversation.open("a", "agent");
    composerInput.value = "left mid-thought";
    await paste("z.png");
    const chipsBeforeDestroy = chips().join("|");
    // Destroying drains everything the controller ever subscribed.
    conversation.destroy();
    offOperatorInvalidate();
    if (bus.subscriberCount() !== 0) {
        throw new Error(`destroy left ${bus.subscriberCount()} bus subscribers`);
    }
    if (store.subscriberCount() !== 0) {
        throw new Error(`destroy left ${store.subscriberCount()} store subscribers`);
    }
    const rebuilt = BossModConversation.createConversation({
        store, bus, api, navigate() {}, needs: needsStub, drafts, cache: transcriptCache,
    });
    await rebuilt.open("a", "agent");
    const rebuiltInput = rebuilt.element.querySelector(".composer-input");
    const rebuiltChips = rebuilt.element
        .querySelectorAll(".composer-attach-chip-name").map((node) => node.textContent);
    const draftSurvivesDestroy = rebuiltInput.value === "left mid-thought"
        && chipsBeforeDestroy.includes("z.png")
        && rebuiltChips.join("|") === chipsBeforeDestroy;
    if (!draftSurvivesDestroy) {
        throw new Error(`draft lost across destroy: "${rebuiltInput.value}" chips=${rebuiltChips.join("|")}`);
    }
    rebuilt.destroy();
    if (bus.subscriberCount() !== 0 || store.subscriberCount() !== 0) {
        throw new Error("the rebuilt controller must drain on destroy too");
    }

    // ─── requiresDraftsAndCache ───
    const refusal = (extra) => {
        try {
            BossModConversation.createConversation(Object.assign({
                store, bus, api, navigate() {}, needs: needsStub,
            }, extra));
        } catch (err) {
            return err.message;
        }
        return "";
    };
    const requiresDraftsAndCache = refusal({ cache: transcriptCache }).includes("deps.drafts")
        && refusal({ drafts: new Map() }).includes("deps.cache");
    if (!requiresDraftsAndCache) throw new Error("createConversation must refuse without drafts/cache");
    if (!listing) throw new Error("the conversation must mount a transcript");
    if (!documentStub) throw new Error("the harness needs a document");

    process.stdout.write(JSON.stringify({
        ok: true,
        draftSurvivesDestroy,
        requiresDraftsAndCache,
        staleLoadDropped,
        cacheSkipsLoading,
        composerSurvivesSwitch,
        presenceSurvivesSwitch,
        unsubscribesPreviousSource,
        errorStateRetries,
        chromeShowsIdentityAvatar,
        chromeAvatarNodeIsStable,
        chromeActionCarriesItsIcon,
        browserViewAbsentWithoutAView,
        browserViewShowsWhenListed,
        browserViewOpensTheViewer,
        browserViewGoesWhenTurnedOff,
        browserViewUnsubscribesOnLeave,
        chromeGroupGlyphForThreads,
        archiveLivesInTheMenu,
        subtitleIsWithTheActions: subtitleIsWithTheActions(),
        headerHasNoReceiptsSwitch,
        receiptsToggleReachableFromMenu,
        receiptsPreferencePersists,
        menuReturnsFocusToItsButton,
        receiptsNodeSurvivesReopen,
        emptyConversationOffersActions,
        greetingWentThroughTheComposer,
        attachmentsFollowTheirConversation,
        inFlightSendKeepsNewerChips,
        structuredRefusalShowsItsError,
        pickedFileSurvivesInputReset,
        emptyPickSaysSo,
        pasteReadsItems,
        pasteTextIsPlain,
        pasteOfNothingSaysSo,
        pasteReplacesSelection,
        desktopPasteReadsTheShellClipboard,
        desktopPasteRefusalShowsReason,
        thumbnailUsesAuthenticatedBlob,
        thumbnailIsCached,
        failedThumbnailShowsChip,
        agentNameIsChromeOutside,
        quietAuthor,
        authorUsesAgentColor,
        faceLowerLeftBesideBubble,
        noOrphanAgentFace,
        humanFaceSaysYou,
        turnGap,
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
        dmSwitchStartsOffAndLive,
        dmSwitchPatchesTheAgent,
        globalGreysOutTheDmSwitch,
        globalGreysOutTheThreadSwitch,
        ownFlagsComeBack,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
