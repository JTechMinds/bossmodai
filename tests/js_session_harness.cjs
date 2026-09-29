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
const PLACES = ["chat", "office", "tasks", "files", "metrics", "log"];
const ctx = { places: PLACES, agentIds: ["a1"], threadIds: ["t1"] };

// Only the five allowed keys persist. `contextMode` is not one of them any
// more: the desk is a modal, and transient UI does not survive a reload.
S.save({
    place: "tasks", conversationId: "a1", conversationKind: "agent",
    contextMode: "desk", railCollapsed: true,
    currentFloorId: "lobby",
    roster: [1, 2, 3], needs: ["secret"],
});
const raw = JSON.parse(global.localStorage.getItem("bossmod_ui"));
if ("roster" in raw || "needs" in raw) throw new Error("only whitelisted keys may persist");
if ("contextMode" in raw) throw new Error("the desk's old column mode must not persist");
if (Object.keys(raw).sort().join(",") !==
    "conversationId,conversationKind,currentFloorId,place,railCollapsed") {
    throw new Error(`unexpected persisted keys: ${Object.keys(raw).join(",")}`);
}

// A valid round-trip survives.
let restored = S.validate(S.load(), ctx);
if (restored.place !== "tasks" || restored.conversationId !== "a1") {
    throw new Error("valid session must round-trip");
}

// A conversation for a deleted agent falls back to the empty state.
S.save({ place: "chat", conversationId: "ghost", conversationKind: "agent",
         railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (restored.conversationId !== null) throw new Error("stale agent id must be dropped");
if (restored.conversationKind !== null) throw new Error("kind must clear with the id");

// A thread that no longer exists is dropped too.
S.save({ place: "chat", conversationId: "t9", conversationKind: "thread",
         railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (restored.conversationId !== null) throw new Error("stale thread id must be dropped");

// An unknown place falls back to chat.
S.save({ place: "nowhere", conversationId: null, conversationKind: null,
         railCollapsed: false });
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
for (const key of ["place", "conversationId", "conversationKind", "railCollapsed", "currentFloorId"]) {
    if (!(key in partial)) throw new Error(`restored object missing ${key}`);
}
// A blob saved before the desk became a modal still carries `contextMode`;
// load() reads only the persisted keys, so it never reaches the store.
global.localStorage.setItem("bossmod_ui", JSON.stringify({ place: "chat", contextMode: "desk" }));
const legacy = S.validate(S.load(), ctx);
if ("contextMode" in legacy) throw new Error("an old blob's contextMode must be ignored");
if (partial.place !== "log") throw new Error("valid partial key must survive");

process.stdout.write(JSON.stringify({
    ok: true,
    whitelistsKeys: true,
    dropsStaleConversation: true,
    fallsBackOnUnknownPlace: true,
    discardsCorruptBlob: true,
    fillsDefaults: true,
}));
