/**
 * Node harness: the context column's mode switch and its teardown.
 *
 * Invoked by tests/test_ui_context.py. Not a browser bundle.
 *
 * It mounts the real Chat place against the real modules, because the property
 * that matters — the column does not outlive a navigation away from Chat — is
 * a property of how those two are wired together, and a stub for either half
 * would prove nothing about it.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
global.lucide = { createIcons() {} };
global.window.lucide = global.lucide;

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModStore", "BossModBus", "BossModUtils", "BossModGates",
    "BossModConsentCard", "BossModOverlays", "BossModTranscript", "BossModMessage",
    "BossModEventCards", "BossModConversationChrome", "BossModComposer",
    "BossModSystemReceipts", "BossModNeedShape", "BossModNeeds", "BossModNeedsBar",
    "BossModThreadArchive", "BossModThreadSource", "BossModAgentSource",
    "BossModConversation", "BossModPlaces", "CompanyFileViewer", "BossModMiniOffice",
    "BossModDeskOpener", "BossModDeskFiles", "BossModDeskTasks", "BossModDeskActions",
    "AgentPanel", "BossModAgentEdit", "BossModDeskPanel",
    "BossModContextColumn", "BossModChatPlace",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

// Read off `global` rather than destructured into module-scope consts: a
// `const` here would be in its temporal dead zone while the evals above run,
// and chat-place.js calls BossModPlaces.register() at load time.
const { BossModStore, BossModBus, BossModNeeds } = global;

const settle = () => new Promise((resolve) => setImmediate(resolve));
const drain = async () => { for (let i = 0; i < 8; i += 1) await settle(); };

// Ada is off-map: db.get_world_state() gives her no room name. She must still
// get a seat, or the operator loses sight of her entirely.
const ROSTER = [
    { id: "a1", name: "Jim", role: "Engineer", color: "#3b82f6", status: "idle",
      currentActivityKind: null, location: "Main office" },
    { id: "a2", name: "Laura", role: "Writer", color: "#f59e0b", status: "idle",
      currentActivityKind: null, location: "Main office" },
    { id: "a3", name: "Ada", role: "Analyst", color: "#10b981", status: "idle",
      currentActivityKind: null, location: null },
];

function api(url) {
    if (url.startsWith("/api/needs")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve([]), text: () => Promise.resolve("") });
}

async function main() {
    const store = BossModStore.createStore({
        place: "chat",
        placeParams: {},
        conversationId: null,
        conversationKind: null,
        contextMode: "office",
        deskAgentId: null,
        roster: [],
        threads: [],
        rosterQuery: "",
        runtimePaused: false,
        hasUsableModel: true,
        needs: [],
        needsBarEnabled: true,
        needsBarDismissed: false,
    });
    const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
    const needs = BossModNeeds.createNeedsStore({ store, bus, api });
    await drain();

    // Baselines are taken with the needs store already built: it lives for the
    // life of the app, so what must come back to zero is the place's share.
    const storeBaseline = store.subscriberCount();
    const busBaseline = bus.subscriberCount();

    const placeEl = documentStub.createElement("div");
    const contextEl = documentStub.createElement("aside");
    documentStub.body.append(placeEl);
    documentStub.body.append(contextEl);

    const navigated = [];
    const ctx = {
        store,
        bus,
        api,
        needs,
        contextEl,
        navigate: (placeId) => navigated.push(placeId),
    };

    const chat = global.BossModPlaces.get("chat");
    chat.mount(placeEl, ctx);
    store.setState({ roster: ROSTER });
    await drain();

    // ─── 1. Every agent gets a seat, including the unplaced one ───

    const seats = () => contextEl.querySelectorAll(".mini-office-seat");
    const roomNames = () => contextEl.querySelectorAll(".mini-office-room-name")
        .map((node) => node.textContent);

    if (seats().length !== 3) {
        throw new Error(`every agent needs a seat, got ${seats().length}`);
    }
    if (!roomNames().includes("Unknown")) {
        throw new Error(`an off-map agent needs a real room, got rooms ${roomNames().join(", ")}`);
    }
    // ...and "Unknown" sorts last, so a real room is never buried under it.
    if (roomNames()[roomNames().length - 1] !== "Unknown") {
        throw new Error(`Unknown must sort last, got ${roomNames().join(", ")}`);
    }
    const ada = seats().filter((seat) => seat.getAttribute("data-agent-id") === "a3")[0];
    if (!ada) throw new Error("the off-map agent has no seat");
    const rendersUnknownRoom = true;

    // A need on an agent pings their seat, silently.
    store.setState({ needs: [{ id: "n1", kind: "blocked", agentId: "a1", conversationId: "a1" }] });
    const jim = seats().filter((seat) => seat.getAttribute("data-agent-id") === "a1")[0];
    if (!jim.querySelector(".mini-office-ping")) {
        throw new Error("an agent with an open need must be pinged in the office summary");
    }
    if (!String(jim.getAttribute("aria-label")).includes("needs you")) {
        throw new Error("the ping is decorative; the accessible name must state the fact");
    }
    store.setState({ needs: [] });

    // ─── 2. A seat opens that agent's desk ───

    await ada.dispatchClick();
    if (store.getState().contextMode !== "desk" || store.getState().deskAgentId !== "a3") {
        throw new Error("clicking a seat must open that agent's desk");
    }
    const seatOpensDesk = true;

    // ─── 3. The column switches modes, one view at a time ───

    if (contextEl.querySelectorAll(".mini-office").length !== 0) {
        throw new Error("the outgoing view must be unmounted before the incoming one mounts");
    }
    if (contextEl.querySelectorAll(".desk-panel").length !== 1) {
        throw new Error("desk mode must mount exactly one desk view");
    }
    store.setState({ contextMode: "office" });
    if (contextEl.querySelectorAll(".desk-panel").length !== 0) {
        throw new Error("switching back must unmount the desk view");
    }
    if (contextEl.querySelectorAll(".mini-office").length !== 1) {
        throw new Error("office mode must mount exactly one summary");
    }

    // Switching back and forth must not accumulate subscriptions. Measured in
    // the same mode at both ends: the two views hold different numbers of
    // subscriptions, so a cross-mode comparison would prove nothing.
    const officeSubscribers = store.subscriberCount();
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    store.setState({ contextMode: "office" });
    store.setState({ contextMode: "desk", deskAgentId: "a2" });
    store.setState({ contextMode: "office" });
    if (store.subscriberCount() !== officeSubscribers) {
        throw new Error(
            `mode switching leaks subscriptions: ${officeSubscribers} -> ${store.subscriberCount()}`
        );
    }
    // Switching between two agents' desks must swap the view, not stack it.
    store.setState({ contextMode: "desk", deskAgentId: "a1" });
    store.setState({ contextMode: "desk", deskAgentId: "a2" });
    if (contextEl.querySelectorAll(".desk-panel").length !== 1) {
        throw new Error("a desk-to-desk switch must leave exactly one view mounted");
    }
    store.setState({ contextMode: "office" });
    const switchesModes = true;

    // ─── 4. Navigating away from Chat takes the column with it ───

    chat.unmount();
    await drain();

    if (contextEl.children.length !== 0) {
        throw new Error("unmounting Chat must clear the context element");
    }
    if (store.subscriberCount() !== storeBaseline) {
        throw new Error(`store leak: baseline ${storeBaseline}, now ${store.subscriberCount()}`);
    }
    if (bus.subscriberCount() !== busBaseline) {
        throw new Error(`bus leak: baseline ${busBaseline}, now ${bus.subscriberCount()}`);
    }

    // A second mount/unmount cycle returns to the same baseline.
    chat.mount(placeEl, ctx);
    await drain();
    chat.unmount();
    await drain();
    if (store.subscriberCount() !== storeBaseline || bus.subscriberCount() !== busBaseline) {
        throw new Error("a second Chat cycle must return to the same baseline");
    }
    const drainsOnDestroy = true;

    process.stdout.write(JSON.stringify({
        ok: true,
        switchesModes,
        drainsOnDestroy,
        rendersUnknownRoom,
        seatOpensDesk,
    }));
}

main().catch((err) => {
    process.stderr.write(String((err && err.stack) || err));
    process.exit(1);
});
