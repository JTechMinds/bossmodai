/**
 * Node harness: `resync` fires on reconnect but never on first connect,
 * and backoff is exponential and capped.
 * Invoked by tests/test_ui_resync.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = { createElement: () => ({}), addEventListener() {} };
global.window = { document: global.document, addEventListener() {} };

// Controllable timers. The reconnect DELAY is a subject here, not an
// implementation detail, so it has to be observable; and a real timer would
// fire a reconnect in the middle of a scripted drop.
const scheduled = [];
global.setTimeout = (fn, delay) => {
    const handle = { fn, delay, cleared: false };
    scheduled.push(handle);
    return handle;
};
global.clearTimeout = (handle) => { if (handle) handle.cleared = true; };
const pendingTimers = () => scheduled.filter((handle) => !handle.cleared).length;
const lastTimerDelay = () => (scheduled.length ? scheduled[scheduled.length - 1].delay : null);

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModBus = BossModBus;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModSocket = BossModSocket;\n`);

const sockets = [];
class FakeWebSocket {
    constructor(url) {
        this.url = url;
        this.readyState = 0;
        sockets.push(this);
    }
    close() { this.readyState = 3; if (this.onclose) this.onclose(); }
    open() { this.readyState = 1; if (this.onopen) this.onopen(); }
    emit(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
}

const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);
const resyncs = [];
bus.subscribe("resync", (d) => resyncs.push(d));
const activity = [];
bus.subscribe("activity", (d) => activity.push(d));

const statuses = [];
const socket = BossModSocket.createSocket({
    bus,
    url: "ws://test/api/ws",
    WebSocketImpl: FakeWebSocket,
    onStatus: (s) => statuses.push(s),
});

// Backoff: exponential from 1s, capped at 30s.
const delays = [0, 1, 2, 3, 4, 5, 6, 10].map((n) => socket.nextDelay(n));
if (delays[0] !== 1000 || delays[1] !== 2000 || delays[2] !== 4000) {
    throw new Error(`bad backoff ramp: ${delays.join(",")}`);
}
if (delays[delays.length - 1] !== 30000) throw new Error("backoff must cap at 30000");

// First connect: no resync.
socket.connect();
sockets[0].open();
if (resyncs.length !== 0) throw new Error("first connect must NOT publish resync");
if (statuses[statuses.length - 1] !== "connected") throw new Error("expected connected status");

// Messages route onto their topic.
sockets[0].emit({ type: "activity", data: { id: 7 } });
if (activity.length !== 1 || activity[0].id !== 7) throw new Error("activity not routed");

// Unknown server message types are ignored, not thrown — the server may ship
// a new type before the client learns it.
sockets[0].emit({ type: "brand_new_type", data: {} });

// Drop, then reconnect: resync fires exactly once, with downtime.
sockets[0].close();
if (statuses[statuses.length - 1] !== "disconnected") throw new Error("expected disconnected");
socket.connect();
sockets[1].open();
if (resyncs.length !== 1) throw new Error(`expected 1 resync, got ${resyncs.length}`);
if (typeof resyncs[0].downtimeMs !== "number") throw new Error("resync needs downtimeMs");

// A second clean reconnect fires again.
sockets[1].close();
socket.connect();
sockets[2].open();
if (resyncs.length !== 2) throw new Error(`expected 2 resyncs, got ${resyncs.length}`);
// Snapshotted before the backoff ramp below, which opens the socket again.
const resyncOnEveryReconnect = resyncs.length === 2;

// The attempt counter climbs while retries fail and RESETS on a successful
// open. Without the reset a flaky link reaches the 30s cap and stays there:
// every later blip then waits half a minute while the operator watches a
// stale UI. Re-pointed from js_ws_reconnect_harness.cjs, whose subject
// (app.js) Phase 4 deleted; the ramp it asserted was [1000, 2000, 4000, 1000].
const ramp = [];
sockets[2].close();
ramp.push(lastTimerDelay());   // 1000
socket.connect();              // sockets[3] — never opens
sockets[3].close();
ramp.push(lastTimerDelay());   // 2000
socket.connect();              // sockets[4] — never opens
sockets[4].close();
ramp.push(lastTimerDelay());   // 4000
socket.connect();
sockets[5].open();             // a good connect clears the climb
sockets[5].close();
ramp.push(lastTimerDelay());   // 1000 again
const backoffResetsAfterConnect =
    ramp.join(",") === [1000, 2000, 4000, 1000].join(",");
if (!backoffResetsAfterConnect) {
    throw new Error(`expected ramp 1000,2000,4000,1000; got ${ramp.join(",")}`);
}

// The unload guard: closing deliberately must not schedule a reconnect, and
// must leave no timer that fires after the page is gone.
const socketsBefore = sockets.length;
socket.close();
const unloadStopsReconnect = pendingTimers() === 0 && sockets.length === socketsBefore;
if (!unloadStopsReconnect) {
    throw new Error(
        `close() must stop reconnects: ${pendingTimers()} timers, `
        + `${sockets.length - socketsBefore} new sockets`
    );
}

process.stdout.write(JSON.stringify({
    ok: true,
    noResyncOnFirstConnect: true,
    resyncOnEveryReconnect,
    backoffCapped: true,
    backoffResetsAfterConnect,
    unloadStopsReconnect,
    routesMessages: true,
}));
