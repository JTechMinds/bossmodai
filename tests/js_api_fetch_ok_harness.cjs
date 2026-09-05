/**
 * Node harness: apiFetch does not throw on 4xx; apiFetchOk does, with parsed body.
 * Invoked by tests/test_js_api_client.py. Not a browser bundle.
 */
const fs = require("fs");

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

function jsonResponse(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        async json() {
            return body;
        },
        async text() {
            return JSON.stringify(body);
        },
    };
}

const originalFetch = async (input) => {
    const url = String(input);
    if (url.includes("/ok")) return jsonResponse(200, { saved: true });
    if (url.includes("/string-detail")) {
        return jsonResponse(400, { detail: "Host workspace root must be an absolute path" });
    }
    if (url.includes("/list-detail")) {
        return jsonResponse(422, { detail: [{ loc: ["body", "name"], msg: "Field required" }] });
    }
    if (url.includes("/empty")) return jsonResponse(500, {});
    return jsonResponse(404, { detail: "missing" });
};

global.document = {
    querySelector() {
        return { getAttribute() { return "test-token"; } };
    },
};
global.Headers = HeadersPolyfill;
global.window = {
    location: { href: "http://127.0.0.1:8000/" },
    fetch: originalFetch,
    WebSocket: function WebSocketStub() {},
    console,
    Headers: HeadersPolyfill,
};
global.console = console;

const authSrc = process.argv[2];
const clientSrc = process.argv[3];
eval(fs.readFileSync(authSrc, "utf8"));
eval(fs.readFileSync(clientSrc, "utf8"));

if (typeof window.apiFetch !== "function" || typeof window.apiFetchOk !== "function") {
    throw new Error("apiFetch / apiFetchOk missing");
}

async function main() {
    const okRes = await window.apiFetch("/api/settings/string-detail", { method: "PUT" });
    if (okRes.ok) throw new Error("apiFetch should not treat 400 as ok");
    if (okRes.status !== 400) throw new Error("apiFetch should return the 400 response");

    const saved = await window.apiFetchOk("/api/settings/ok", { method: "PUT" });
    if (!saved.ok) throw new Error("apiFetchOk should resolve on 200");

    let stringDetail = "";
    try {
        await window.apiFetchOk("/api/settings/string-detail", { method: "PUT" });
        throw new Error("apiFetchOk should throw on 400");
    } catch (err) {
        stringDetail = err.message;
    }

    let listDetail = "";
    try {
        await window.apiFetchOk("/api/list-detail", { method: "POST" });
        throw new Error("apiFetchOk should throw on 422");
    } catch (err) {
        if (err.message === "apiFetchOk should throw on 422") throw err;
        listDetail = err.message;
    }

    let emptyDetail = "";
    try {
        await window.apiFetchOk("/api/empty", { method: "PUT" });
    } catch (err) {
        emptyDetail = err.message;
    }

    const formatted = window.BossModApi.formatError(
        { detail: "Host workspace root must be an absolute path" },
        400,
    );

    process.stdout.write(JSON.stringify({
        ok: true,
        apiFetchDoesNotThrowOn400: true,
        stringDetail,
        listDetail,
        emptyDetail,
        formatted,
    }));
}

main();
