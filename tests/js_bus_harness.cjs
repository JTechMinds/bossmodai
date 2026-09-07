/**
 * Node harness: topic fan-out, unknown-topic rejection, disposer teardown.
 * Invoked by tests/test_ui_bus.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = { createElement: () => ({}), addEventListener() {} };
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModBus = BossModBus;\n`);

const { createBus, KNOWN_TOPICS } = BossModBus;

// Every server message type from api/websocket.py, plus the client-side resync.
const REQUIRED = [
    "world_update", "runtime_state", "chat_message", "chat_reset",
    "meeting_message", "channel_message", "channel_presence", "channel_updated",
    "diagnostic", "agent_thought", "activity", "activity_update",
    "unified_feed", "resync",
];
for (const topic of REQUIRED) {
    if (!KNOWN_TOPICS.includes(topic)) throw new Error(`KNOWN_TOPICS missing ${topic}`);
}

const bus = createBus(KNOWN_TOPICS);

// Fan-out: many subscribers, one publish.
const seen = [];
const offA = bus.subscribe("activity", (d) => seen.push(["a", d]));
const offB = bus.subscribe("activity", (d) => seen.push(["b", d]));
bus.publish("activity", { id: 1 });
if (seen.length !== 2) throw new Error(`expected 2 deliveries, got ${seen.length}`);

// Topics are isolated.
bus.publish("diagnostic", { id: 2 });
if (seen.length !== 2) throw new Error("diagnostic must not reach activity subscribers");

// Disposers detach.
offA();
bus.publish("activity", { id: 3 });
if (seen.length !== 3) throw new Error("disposed subscriber still receiving");
offB();
if (bus.subscriberCount() !== 0) throw new Error("disposers must drop count to 0");

// Unknown topics throw on BOTH sides — a typo fails loudly, never silently.
let subscribeThrew = false;
try { bus.subscribe("wolrd_update", () => {}); } catch (e) { subscribeThrew = true; }
if (!subscribeThrew) throw new Error("subscribe to unknown topic must throw");

let publishThrew = false;
try { bus.publish("wolrd_update", {}); } catch (e) { publishThrew = true; }
if (!publishThrew) throw new Error("publish to unknown topic must throw");

// A throwing subscriber must not block the rest.
let reached = false;
const offBad = bus.subscribe("activity", () => { throw new Error("boom"); });
const offGood = bus.subscribe("activity", () => { reached = true; });
bus.publish("activity", { id: 4 });
offBad(); offGood();
if (!reached) throw new Error("one throwing subscriber must not block delivery");

process.stdout.write(JSON.stringify({
    ok: true,
    fansOut: true,
    isolatesTopics: true,
    rejectsUnknownTopics: subscribeThrew && publishThrew,
    isolatesThrowingSubscriber: true,
}));
