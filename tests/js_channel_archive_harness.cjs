/**
 * Node harness: Archive stays clickable across thread switches and handoffs.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 *
 * Phase 2A moved the thread chrome from the dock-era ChannelsView to the
 * conversation controller and its chrome view. The three properties are
 * unchanged and the emitted payload keys are byte-identical:
 *
 *   sameButton              a switch rebinds the action on the same node
 *   keepShellSwitchEnabled  ... and leaves it enabled
 *   archiveHandoffEnabled   an archive never strands a disabled control
 *
 * A later round moved Archive and Reopen behind the header's `⋯` (the source
 * marks them `slot: 'menu'`), so reaching either one means opening that panel
 * first. Every property above survives the move unchanged — the node is still
 * reused across switches, still disabled while a request is in flight, and
 * still re-enabled by an abort — because all that changed is which parent
 * conversation/chrome.js appends it to.
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
    "BossModDom", "BossModMarkdown", "BossModAvatar", "BossModSwitch", "BossModStore", "BossModBus",
    "BossModFormat", "BossModGates", "BossModConsentCard",
    "BossModOverlayFocus", "BossModOverlays",
    "BossModEmptyState", "BossModTranscript", "BossModTranscriptCache", "BossModMessage", "BossModEventCards",
    "BossModTitleRename", "BossModConversationChrome", "BossModComposer", "BossModSystemReceipts",
    "BossModNeedsBar", "BossModThreadArchive", "BossModThreadSource",
    "BossModAgentSource", "BossModConversation",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const { BossModStore, BossModBus, BossModConversation } = global;

const threads = {
    a: { id: "a", name: "Ann", status: "active", members: [{ id: "m-a", name: "Ann" }] },
    b: { id: "b", name: "Bea", status: "active", members: [{ id: "m-b", name: "Bea" }] },
};

const api = async (url, opts = {}) => {
    const method = String(opts.method || "GET").toUpperCase();
    const path = String(url).split("?")[0];
    const open = path.match(/^\/api\/channels\/([^/]+)\/open-tasks$/);
    if (open) return { ok: true, async json() { return { count: 0, tasks: [] }; } };
    const one = path.match(/^\/api\/channels\/([^/]+)$/);
    if (one) {
        const item = threads[one[1]];
        if (!item) return { ok: false, async text() { return "missing"; } };
        if (method === "DELETE") {
            item.status = "archived";
            return { ok: true, async json() { return { ...item }; } };
        }
        return {
            ok: true,
            async json() {
                return { channel: { ...item }, messages: [] };
            },
        };
    }
    throw new Error(`unhandled ${method} ${url}`);
};

const store = BossModStore.createStore({ roster: [], threads: [], hasUsableModel: true });
const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
// The needs bar is part of the surface; its own behaviour is proven in
// js_needs_harness.cjs, so an empty queue is enough to build the conversation.
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

const tick = () => new Promise((resolve) => setImmediate(resolve));
// A chrome action is fired and forgotten (`void run(...)`), so a click cannot
// be awaited directly; drain the microtask chain it starts instead.
async function settle() {
    for (let i = 0; i < 8; i += 1) await tick();
}

/**
 * Open the header's overflow panel, if it is not already open.
 *
 * A conversation switch closes it (chrome.reset) and so does picking anything
 * in it, which is the behaviour the operator wants and the reason every reach
 * for Archive below goes through here rather than caching a node.
 *
 * @returns {Promise<object>} The `⋯` button.
 */
async function openMenu() {
    const dots = conversation.element.querySelector("#conversation-view-options");
    if (!dots) throw new Error("the header must offer the overflow menu");
    if (dots.getAttribute("aria-expanded") !== "true") await dots.dispatchClick();
    return dots;
}

async function archiveBtn() {
    await openMenu();
    return conversation.element.querySelector("#channel-archive-btn");
}

async function reopenBtn() {
    await openMenu();
    return conversation.element.querySelector("#channel-reopen-btn");
}

function assertEnabled(btn, label) {
    if (!btn) throw new Error(`${label}: button missing`);
    if (btn.disabled) throw new Error(`${label}: button stayed disabled`);
}

async function main() {
    await conversation.open("a", "thread");
    const first = await archiveBtn();
    assertEnabled(first, "initial A");

    // ─── sameButton / keepShellSwitchEnabled ───
    await conversation.open("b", "thread");
    if (await archiveBtn() !== first) {
        throw new Error("a keep-shell switch must rebind the same Archive node");
    }
    assertEnabled(await archiveBtn(), "switch A→B");

    await conversation.open("a", "thread");
    if (await archiveBtn() !== first) {
        throw new Error("switching back must still be the same Archive node");
    }
    assertEnabled(await archiveBtn(), "switch B→A");
    const sameButton = await archiveBtn() === first && first.disabled === false;
    const keepShellSwitchEnabled = sameButton;

    // ─── archiveHandoffEnabled ───
    // The whole real path: the panel is open, the row in it puts the panel away
    // and opens the accessible modal, the modal's Archive resolves the choice,
    // and the chrome swaps in place.
    await openMenu();
    void first.dispatchClick();
    await settle();
    if (first.disabled !== true) throw new Error("Archive must disable while in flight");
    const confirm = documentStub.body.querySelector("#channel-archive-confirm");
    if (!confirm) throw new Error("the archive confirm modal did not render");
    await confirm.dispatchClick();
    await settle();

    if (threads.a.status !== "archived") throw new Error("the confirm must archive the thread");
    if (await archiveBtn()) throw new Error("an archived thread must not still offer Archive");
    assertEnabled(await reopenBtn(), "after archive");

    // Handing back to a live thread leaves a usable Archive, not a stranded one.
    await conversation.open("b", "thread");
    assertEnabled(await archiveBtn(), "archive A → land on B");
    if (await reopenBtn()) throw new Error("a live thread must not offer Reopen");
    const archiveHandoffEnabled = (await archiveBtn()).disabled === false;

    // An aborted archive also re-enables, rather than wedging the control.
    void (await archiveBtn()).dispatchClick();
    await settle();
    await documentStub.body.querySelector("#channel-archive-back").dispatchClick();
    await settle();
    if (threads.b.status !== "active") throw new Error("Cancel must abort the archive");
    assertEnabled(await archiveBtn(), "aborted archive");

    conversation.destroy();

    process.stdout.write(JSON.stringify({
        ok: true,
        archiveHandoffEnabled,
        keepShellSwitchEnabled,
        sameButton,
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
