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

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModStore", "BossModBus", "BossModGates", "BossModConsentCard",
    "BossModTranscript", "BossModMessage", "BossModEventCards", "BossModConversationChrome",
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
};
const delays = { a: 0, b: 0, c: 0 };
const failing = new Set();
const requestLog = [];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tick = () => new Promise((resolve) => setImmediate(resolve));

const api = async (url) => {
    const text = String(url);
    requestLog.push(text);
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
        return {
            ok: true,
            async json() {
                return {
                    channel: {
                        id: thread[1],
                        name: "Standup",
                        status: "active",
                        members: [{ id: "m1", name: "Ada" }],
                    },
                    messages: [],
                };
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

const conversation = BossModConversation.createConversation({
    store, bus, api, navigate() {}, needs: needsStub,
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
    }));
}

main().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
