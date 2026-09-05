/**
 * Node harness for HA-STRUCT-P1-08: api-auth wrap + apiFetch token attach.
 * Invoked by tests/test_js_api_client.py. Not a browser bundle.
 */
const fs = require("fs");

const calls = [];
const token = "test-token-1234";
const TOKEN_HEADER = "X-BossMod-Token";

class HeadersPolyfill {
    constructor(init) {
        this._map = new Map();
        if (!init) return;
        if (init instanceof HeadersPolyfill) {
            for (const [key, value] of init._map) this._map.set(key, value);
            return;
        }
        if (Array.isArray(init)) {
            for (const [key, value] of init) this._map.set(String(key).toLowerCase(), String(value));
            return;
        }
        for (const [key, value] of Object.entries(init)) {
            this._map.set(key.toLowerCase(), String(value));
        }
    }
    has(name) {
        return this._map.has(String(name).toLowerCase());
    }
    get(name) {
        const value = this._map.get(String(name).toLowerCase());
        return value === undefined ? null : value;
    }
    set(name, value) {
        this._map.set(String(name).toLowerCase(), String(value));
    }
}

function WebSocketStub() {}
WebSocketStub.prototype = {};
WebSocketStub.CONNECTING = 0;
WebSocketStub.OPEN = 1;
WebSocketStub.CLOSING = 2;
WebSocketStub.CLOSED = 3;

const originalFetch = async (input, init) => {
    calls.push({ input, init });
    return { ok: true, status: 200 };
};

global.document = {
    querySelector() {
        return { getAttribute() { return token; } };
    },
};
global.Headers = HeadersPolyfill;
global.window = {
    location: { href: "http://127.0.0.1:8000/" },
    fetch: originalFetch,
    WebSocket: WebSocketStub,
    console,
    Headers: HeadersPolyfill,
};
global.console = console;

const authSrc = process.argv[2];
const clientSrc = process.argv[3];
// eslint-disable-next-line no-eval
eval(fs.readFileSync(authSrc, "utf8"));
eval(fs.readFileSync(clientSrc, "utf8"));

if (typeof window.apiFetch !== "function") {
    throw new Error("apiFetch missing");
}
if (window.fetch === originalFetch) {
    throw new Error("api-auth did not wrap window.fetch");
}

async function main() {
    await window.apiFetch("/api/runtime/state", { cache: "no-store" });
    await window.apiFetch("/api/connections", {
        headers: { "Content-Type": "application/json" },
    });
    await window.apiFetch("/api/already", {
        headers: { [TOKEN_HEADER]: "pre-set" },
    });

    if (calls.length !== 3) {
        throw new Error(`expected 3 wrapped fetch calls, got ${calls.length}`);
    }
    for (const call of calls) {
        if (!(call.init.headers instanceof HeadersPolyfill)) {
            throw new Error("headers not a Headers instance");
        }
    }
    if (calls[0].init.headers.get(TOKEN_HEADER) !== token) {
        throw new Error("missing token on first call");
    }
    if (calls[1].init.headers.get(TOKEN_HEADER) !== token) {
        throw new Error("missing token on json call");
    }
    if (calls[1].init.headers.get("Content-Type") !== "application/json") {
        throw new Error("lost content-type");
    }
    if (calls[2].init.headers.get(TOKEN_HEADER) !== "pre-set") {
        throw new Error("overwrote existing token");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        calls: calls.length,
        wrapped: window.fetch !== originalFetch,
    }));
}

main();
