/**
 * Node harness: session restore is validated, never trusted.
 * Invoked by tests/test_ui_session_restore.py. Not a browser bundle.
 */
const fs = require("fs");

const store = {};
global.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
};
global.document = { createElement: () => ({}), addEventListener() {} };
global.window = { document: global.document, localStorage: global.localStorage };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModSession = BossModSession;\n`);

const S = BossModSession;
const PLACES = ["chat", "office", "board", "files", "metrics", "log"];
const ctx = { places: PLACES, agentIds: ["a1"], threadIds: ["t1"] };

// Only the five allowed keys persist.
S.save({
    place: "board", conversationId: "a1", conversationKind: "agent",
    contextMode: "desk", railCollapsed: true,
    roster: [1, 2, 3], needs: ["secret"],
});
const raw = JSON.parse(global.localStorage.getItem("bossmod_ui"));
if ("roster" in raw || "needs" in raw) throw new Error("only whitelisted keys may persist");
if (Object.keys(raw).sort().join(",") !==
    "contextMode,conversationId,conversationKind,place,railCollapsed") {
    throw new Error(`unexpected persisted keys: ${Object.keys(raw).join(",")}`);
}

// A valid round-trip survives.
let restored = S.validate(S.load(), ctx);
if (restored.place !== "board" || restored.conversationId !== "a1") {
    throw new Error("valid session must round-trip");
}

// A conversation for a deleted agent falls back to the empty state.
S.save({ place: "chat", conversationId: "ghost", conversationKind: "agent",
         contextMode: "office", railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (restored.conversationId !== null) throw new Error("stale agent id must be dropped");
if (restored.conversationKind !== null) throw new Error("kind must clear with the id");

// A thread that no longer exists is dropped too.
S.save({ place: "chat", conversationId: "t9", conversationKind: "thread",
         contextMode: "office", railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (restored.conversationId !== null) throw new Error("stale thread id must be dropped");

// An unknown place falls back to chat.
S.save({ place: "nowhere", conversationId: null, conversationKind: null,
         contextMode: "office", railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (restored.place !== "chat") throw new Error("unknown place must fall back to chat");

// Corrupt JSON is discarded whole, never partially applied.
global.localStorage.setItem("bossmod_ui", "{not json");
const afterCorrupt = S.validate(S.load(), ctx);
if (afterCorrupt.place !== "chat" || afterCorrupt.conversationId !== null) {
    throw new Error("corrupt blob must yield clean defaults");
}

// A partial object gets full defaults, not undefined holes.
global.localStorage.setItem("bossmod_ui", JSON.stringify({ place: "log" }));
const partial = S.validate(S.load(), ctx);
for (const key of ["place", "conversationId", "conversationKind", "contextMode", "railCollapsed"]) {
    if (!(key in partial)) throw new Error(`restored object missing ${key}`);
}
if (partial.place !== "log") throw new Error("valid partial key must survive");

process.stdout.write(JSON.stringify({
    ok: true,
    whitelistsKeys: true,
    dropsStaleConversation: true,
    fallsBackOnUnknownPlace: true,
    discardsCorruptBlob: true,
    fillsDefaults: true,
}));
