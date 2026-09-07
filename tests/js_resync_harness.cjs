/**
 * Node harness: `resync` fires on reconnect but never on first connect,
 * and backoff is exponential and capped.
 * Invoked by tests/test_ui_resync.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = { createElement: () => ({}), addEventListener() {} };
global.window = { document: global.document, addEventListener() {} };

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

process.stdout.write(JSON.stringify({
    ok: true,
    noResyncOnFirstConnect: true,
    resyncOnEveryReconnect: resyncs.length === 2,
    backoffCapped: true,
    routesMessages: true,
}));
