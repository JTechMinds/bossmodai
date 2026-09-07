/**
 * Node harness: selector subscriptions fire only on change, and disposers
 * fully detach. Invoked by tests/test_ui_store.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = { createElement: () => ({}), addEventListener() {} };
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModStore = BossModStore;\n`);

const store = BossModStore.createStore({ place: "chat", conversationId: null, roster: [] });

// Selector fires on change only.
const placeCalls = [];
const offPlace = store.subscribe((s) => s.place, (v, prev) => placeCalls.push([prev, v]));

store.setState({ place: "board" });
store.setState({ place: "board" });          // no change -> no call
store.setState({ conversationId: "abc" });   // different slice -> no call
if (placeCalls.length !== 1) {
    throw new Error(`expected 1 place call, got ${placeCalls.length}`);
}
if (JSON.stringify(placeCalls[0]) !== JSON.stringify(["chat", "board"])) {
    throw new Error(`bad payload: ${JSON.stringify(placeCalls[0])}`);
}

// A second subscriber on a different slice is independent.
let convoCalls = 0;
const offConvo = store.subscribe((s) => s.conversationId, () => { convoCalls += 1; });
store.setState({ conversationId: "xyz" });
store.setState({ place: "files" });
if (convoCalls !== 1) throw new Error(`expected 1 conversation call, got ${convoCalls}`);

// setState shallow-merges; untouched keys survive.
if (store.getState().conversationId !== "xyz") throw new Error("merge lost a key");
if (store.getState().place !== "files") throw new Error("merge lost place");

// Disposers detach. Snapshot at the moment of disposal: the place subscriber
// legitimately fired again on the setState above, so the property under test is
// "no further delivery after disposal", not an absolute call count.
const placeCallsAtDisposal = placeCalls.length;
const convoCallsAtDisposal = convoCalls;
offPlace();
offConvo();
const before = store.subscriberCount();
store.setState({ place: "metrics", conversationId: "q" });
if (placeCalls.length !== placeCallsAtDisposal || convoCalls !== convoCallsAtDisposal) {
    throw new Error("disposed subscriber still firing");
}
if (before !== 0) throw new Error(`disposers must drop count to 0, got ${before}`);

// LEAK GUARD (spec 13): a full mount/unmount cycle returns to baseline.
const baseline = store.subscriberCount();
for (let i = 0; i < 50; i += 1) {
    const disposers = [
        store.subscribe((s) => s.place, () => {}),
        store.subscribe((s) => s.roster, () => {}),
    ];
    disposers.forEach((d) => d());
}
const after = store.subscriberCount();
if (after !== baseline) {
    throw new Error(`subscriber leak: baseline ${baseline}, after 50 cycles ${after}`);
}

// A throwing subscriber must not stop the others or corrupt state.
let survived = false;
const offBad = store.subscribe((s) => s.place, () => { throw new Error("boom"); });
const offGood = store.subscribe((s) => s.place, () => { survived = true; });
store.setState({ place: "log" });
offBad();
offGood();
if (!survived) throw new Error("one throwing subscriber must not block the rest");
if (store.getState().place !== "log") throw new Error("state must still be applied");

process.stdout.write(JSON.stringify({
    ok: true,
    firesOnlyOnChange: true,
    disposersDetach: true,
    noLeakAfter50Cycles: after === baseline,
    isolatesThrowingSubscriber: true,
}));
