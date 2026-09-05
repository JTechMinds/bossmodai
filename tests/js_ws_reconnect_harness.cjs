/**
 * Node harness for WS reconnect backoff + one-shot beforeunload.
 * Invoked by tests/test_ui_ws_canvas_p2.py. Not a browser bundle.
 */
const fs = require("fs");

const sockets = [];
const listeners = { window: {}, document: {} };
const timeouts = new Map();
let nextTimerId = 0;

function pendingTimeouts() {
    return [...timeouts.values()].filter((entry) => !entry.cleared);
}

function fireNextTimeout() {
    const entry = pendingTimeouts()[0];
    if (!entry) {
        throw new Error("no pending timeout");
    }
    entry.cleared = true;
    entry.fn();
    return entry.ms;
}

class FakeWebSocket {
    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        this.onopen = null;
        this.onclose = null;
        this.onerror = null;
        this.onmessage = null;
        sockets.push(this);
    }

    open() {
        this.readyState = FakeWebSocket.OPEN;
        if (this.onopen) this.onopen();
    }

    close() {
        if (this.readyState === FakeWebSocket.CLOSED) return;
        this.readyState = FakeWebSocket.CLOSED;
        if (this.onclose) this.onclose();
    }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;

function addTrackedListener(target, type, fn) {
    if (!listeners[target][type]) listeners[target][type] = [];
    listeners[target][type].push(fn);
}

const store = {};
const localStorage = {
    getItem(key) {
        return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    setItem(key, value) {
        store[key] = String(value);
    },
};

global.setTimeout = (fn, ms) => {
    nextTimerId += 1;
    timeouts.set(nextTimerId, { fn, ms, cleared: false });
    return nextTimerId;
};
global.clearTimeout = (id) => {
    const entry = timeouts.get(id);
    if (entry) entry.cleared = true;
};
global.setInterval = () => 1;
global.clearInterval = () => {};

global.localStorage = localStorage;
global.WebSocket = FakeWebSocket;
global.window = {
    innerWidth: 375,
    location: { protocol: "http:", host: "127.0.0.1:38471", href: "http://127.0.0.1:38471/" },
    localStorage,
    WebSocket: FakeWebSocket,
    addEventListener(type, fn) {
        addTrackedListener("window", type, fn);
    },
    confirm() {
        return false;
    },
    alert() {},
};
global.document = {
    getElementById() {
        return null;
    },
    querySelector() {
        return null;
    },
    querySelectorAll() {
        return [];
    },
    addEventListener(type, fn) {
        addTrackedListener("document", type, fn);
    },
};
global.console = console;

async function apiFetch() {
    return {
        ok: true,
        text: async () => "",
        json: async () => [],
    };
}
global.apiFetch = apiFetch;
global.window.apiFetch = apiFetch;

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModApp = BossModApp;\n`);

const BossModApp = global.BossModApp;
if (typeof BossModApp !== "object" || typeof BossModApp.init !== "function") {
    throw new Error("BossModApp.init missing");
}

BossModApp.init();

if (sockets.length !== 1) {
    throw new Error(`expected 1 socket after init, got ${sockets.length}`);
}
if ((listeners.window.beforeunload || []).length !== 1) {
    throw new Error("beforeunload should be registered once at init");
}

sockets[0].open();
if (pendingTimeouts().length !== 0) {
    throw new Error("successful open should not leave a reconnect timer");
}

sockets[0].close();
const firstDelay = pendingTimeouts()[0] && pendingTimeouts()[0].ms;
if (firstDelay !== 1000) {
    throw new Error(`first reconnect delay should be 1000, got ${firstDelay}`);
}
if ((listeners.window.beforeunload || []).length !== 1) {
    throw new Error("close must not re-register beforeunload");
}

fireNextTimeout();
if (sockets.length !== 2) {
    throw new Error(`expected reconnect socket, got ${sockets.length}`);
}
if ((listeners.window.beforeunload || []).length !== 1) {
    throw new Error("reconnect must not re-register beforeunload");
}

sockets[1].close();
const secondDelay = pendingTimeouts()[0] && pendingTimeouts()[0].ms;
if (secondDelay !== 2000) {
    throw new Error(`second reconnect delay should be 2000, got ${secondDelay}`);
}

fireNextTimeout();
sockets[2].close();
const thirdDelay = pendingTimeouts()[0] && pendingTimeouts()[0].ms;
if (thirdDelay !== 4000) {
    throw new Error(`third reconnect delay should be 4000, got ${thirdDelay}`);
}

fireNextTimeout();
sockets[3].open();
sockets[3].close();
const afterOpenDelay = pendingTimeouts()[0] && pendingTimeouts()[0].ms;
if (afterOpenDelay !== 1000) {
    throw new Error(`successful open should reset backoff, got ${afterOpenDelay}`);
}

fireNextTimeout();
if (sockets.length !== 5) {
    throw new Error(`expected live socket before unload, got ${sockets.length}`);
}

const beforeUnload = listeners.window.beforeunload[0];
beforeUnload();
if (pendingTimeouts().length !== 0) {
    throw new Error("unload close must cancel reconnect and not schedule another");
}
if (sockets[4].readyState !== FakeWebSocket.CLOSED) {
    throw new Error("unload should close the live socket");
}

process.stdout.write(JSON.stringify({
    ok: true,
    sockets: sockets.length,
    beforeunload: listeners.window.beforeunload.length,
    delays: [firstDelay, secondDelay, thirdDelay, afterOpenDelay],
}));
