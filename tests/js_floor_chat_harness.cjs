/**
 * Node harness: each floor remembers its own chat (shell/floor-chat.js).
 *
 * Drives the REAL store, floor scope and floor-chat modules through the
 * store's own reentrant setState, which is what the source text cannot show:
 * that switching floors opens that floor's last chat or the empty state, that
 * opening a conversation records it under its own floor and follows it there,
 * that a stale entry resolves to nothing, and that nothing fires before the
 * lists have loaded.
 *
 * Invoked by tests/test_ui_floor_chat.py. Not a browser bundle.
 */
const fs = require("fs");

const NAMES = ["BossModStore", "BossModFloorScope", "BossModFloorChat"];
process.argv.slice(2).forEach((path, index) => {
    eval(`${fs.readFileSync(path, "utf8")}\n;global.${NAMES[index]} = ${NAMES[index]};\n`);
});

function check(condition, message) {
    if (!condition) throw new Error(message);
}

function open(state) {
    return `${state.conversationKind}:${state.conversationId}`;
}

/** A store on floor `one` with an agent and a thread on each of two floors. */
function setup(extra) {
    const store = BossModStore.createStore(Object.assign({
        currentFloorId: "one",
        conversationId: null,
        conversationKind: null,
        conversationByFloor: {},
        roster: [{ id: "a1", floorId: "one" }, { id: "a5", floorId: "five" }],
        threads: [{ id: "t1", floor_id: "one" }, { id: "t5", floor_id: "five" }],
    }, extra || {}));
    const baseline = store.subscriberCount();
    const chat = BossModFloorChat.attach({ store });
    return { store, chat, baseline };
}

const result = { ok: true };

// attach without a store is a programming error, not a silent no-op.
let threw = false;
try { BossModFloorChat.attach({}); } catch (err) { threw = true; }
check(threw, "attach must throw without a store");
result.attachRequiresAStore = true;

// (a) Inert before settle(): neither a floor switch nor an open writes.
{
    const { store } = setup({ conversationByFloor: { five: { id: "a5", kind: "agent" } } });
    store.setState({ currentFloorId: "five" });
    check(store.getState().conversationId === null, "(a) a floor switch before settle must not restore");
    store.setState({ currentFloorId: "one" });
    store.setState({ conversationId: "a5", conversationKind: "agent" });
    check(store.getState().currentFloorId === "one", "(a) an open before settle must not follow");
    check(!store.getState().conversationByFloor.one, "(a) an open before settle must not record");
    result.inertBeforeSettle = true;
}

// (b) settle() restores the current floor's entry.
{
    const { store, chat } = setup({ conversationByFloor: { one: { id: "a1", kind: "agent" } } });
    chat.settle();
    check(open(store.getState()) === "agent:a1", "(b) settle must restore the floor's chat");
    // A second settle is a no-op.
    store.setState({ conversationId: null, conversationKind: null });
    chat.settle();
    check(store.getState().conversationId === null, "(b) a second settle must do nothing");
    result.settleRestoresTheFloorsChat = true;
}

// (c) A conversation opened during boot wins and is recorded.
{
    const { store, chat } = setup({ conversationByFloor: { one: { id: "a1", kind: "agent" } } });
    store.setState({ conversationId: "t1", conversationKind: "thread" });
    chat.settle();
    const state = store.getState();
    check(open(state) === "thread:t1", "(c) the boot-time click must stay open");
    check(state.conversationByFloor.one.id === "t1", "(c) the boot-time click must be recorded");
    result.settleKeepsABootTimeOpen = true;
}

// (d) (e) (f) Switch to a floor with a chat, then one without, then back.
{
    const { store, chat } = setup({ conversationByFloor: {
        one: { id: "a1", kind: "agent" }, five: { id: "a5", kind: "agent" },
    } });
    chat.settle();
    store.setState({ currentFloorId: "five" });
    check(open(store.getState()) === "agent:a5", "(d) switching must open that floor's chat");
    result.switchOpensThatFloorsChat = true;

    store.setState({ currentFloorId: "lobby" });
    check(store.getState().conversationId === null
        && store.getState().conversationKind === null, "(e) a floor with no chat must be empty");
    result.switchToAnEmptyFloorIsEmpty = true;

    store.setState({ currentFloorId: "one" });
    check(open(store.getState()) === "agent:a1", "(f) switching back must restore floor one's chat");
    result.switchBackRestores = true;
}

// (g) An agent that moved floor, or was removed, resolves to empty.
{
    const moved = setup({
        conversationByFloor: { one: { id: "a1", kind: "agent" } },
        roster: [{ id: "a1", floorId: "five" }],
    });
    moved.chat.settle();
    check(moved.store.getState().conversationId === null, "(g) a moved agent must not restore");
    const removed = setup({
        conversationByFloor: { one: { id: "gone", kind: "agent" } },
    });
    removed.chat.settle();
    check(removed.store.getState().conversationId === null, "(g) a removed agent must not restore");
    result.staleAgentEntryIsEmpty = true;
}

// (h) A thread entry is validated the same way.
{
    const good = setup({ conversationByFloor: { five: { id: "t5", kind: "thread" } } });
    good.chat.settle();
    good.store.setState({ currentFloorId: "five" });
    check(open(good.store.getState()) === "thread:t5", "(h) a valid thread must restore");
    const moved = setup({
        conversationByFloor: { one: { id: "t1", kind: "thread" } },
        threads: [{ id: "t1", floor_id: "five" }],
    });
    moved.chat.settle();
    check(moved.store.getState().conversationId === null, "(h) a moved thread must not restore");
    // An agent id under the thread kind is not that thread.
    const wrongKind = setup({ conversationByFloor: { one: { id: "a1", kind: "thread" } } });
    wrongKind.chat.settle();
    check(wrongKind.store.getState().conversationId === null, "(h) the kind must match the list");
    result.threadEntryIsValidated = true;
}

// (i) Opening a conversation records it under its own floor.
{
    const { store, chat } = setup();
    chat.settle();
    store.setState({ conversationId: "t1", conversationKind: "thread" });
    const state = store.getState();
    check(state.conversationByFloor.one.id === "t1"
        && state.conversationByFloor.one.kind === "thread", "(i) the open must be recorded");
    check(state.currentFloorId === "one", "(i) a same-floor open must not move the floor");
    result.openRecordsUnderItsFloor = true;
}

// (j) Opening a conversation on another floor follows it there.
{
    const { store, chat } = setup({ conversationByFloor: { one: { id: "a1", kind: "agent" } } });
    chat.settle();
    const floors = [];
    store.subscribe((s) => s.currentFloorId, (value) => floors.push(value));
    store.setState({ conversationId: "a5", conversationKind: "agent" });
    const state = store.getState();
    check(state.currentFloorId === "five", "(j) a cross-floor open must move the floor");
    check(state.conversationByFloor.five.id === "a5", "(j) it must be recorded on its own floor");
    check(state.conversationByFloor.one.id === "a1", "(j) the old floor's chat must be kept");
    check(open(state) === "agent:a5", "(j) the opened conversation must stay open");
    check(floors.join(",") === "five", `(j) the floor must change exactly once: ${floors}`);
    result.crossFloorOpenFollows = true;
}

// (k) A conversation not yet in the lists is recorded under the current floor.
{
    const { store, chat } = setup();
    chat.settle();
    store.setState({ conversationId: "new-agent", conversationKind: "agent" });
    const state = store.getState();
    check(state.currentFloorId === "one", "(k) an unknown conversation must not move the floor");
    check(state.conversationByFloor.one.id === "new-agent", "(k) it must be recorded on the current floor");
    result.unknownConversationRecordsOnTheCurrentFloor = true;
}

// (l) A no-op open notifies no conversationByFloor subscriber.
{
    const { store, chat } = setup({ conversationByFloor: { one: { id: "a1", kind: "agent" } } });
    chat.settle();
    let notified = 0;
    store.subscribe((s) => s.conversationByFloor, () => { notified += 1; });
    const before = store.getState().conversationByFloor;
    store.setState({ conversationId: null, conversationKind: null });
    store.setState({ conversationId: "a1", conversationKind: "agent" });
    check(notified === 0, `(l) re-opening the recorded chat must not notify (${notified})`);
    check(store.getState().conversationByFloor === before, "(l) the map reference must be kept");
    result.noOpOpenDoesNotNotify = true;
}

// (m) destroy() returns the subscriber count to baseline.
{
    const { store, chat, baseline } = setup();
    check(store.subscriberCount() === baseline + 2, "(m) attach must add exactly two subscriptions");
    chat.destroy();
    check(store.subscriberCount() === baseline, "(m) destroy must drain both subscriptions");
    result.destroyDrainsTheStore = true;
}

process.stdout.write(JSON.stringify(result));
