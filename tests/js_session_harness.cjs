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
const ctx = { places: PLACES };

// Only the four allowed keys persist. `contextMode` is not one of them any
// more: the desk is a modal, and transient UI does not survive a reload.
S.save({
    place: "tasks", conversationByFloor: { lobby: { id: "a1", kind: "agent" } },
    conversationId: "a1", conversationKind: "agent",
    contextMode: "desk", railCollapsed: true,
    currentFloorId: "lobby",
    roster: [1, 2, 3], needs: ["secret"],
});
const raw = JSON.parse(global.localStorage.getItem("bossmod_ui"));
if ("roster" in raw || "needs" in raw) throw new Error("only whitelisted keys may persist");
if ("contextMode" in raw) throw new Error("the desk's old column mode must not persist");
if ("conversationId" in raw) throw new Error("the open conversation is per floor, not global");
if (Object.keys(raw).sort().join(",") !==
    "conversationByFloor,currentFloorId,place,railCollapsed") {
    throw new Error(`unexpected persisted keys: ${Object.keys(raw).join(",")}`);
}

// A valid round-trip survives.
let restored = S.validate(S.load(), ctx);
if (restored.place !== "tasks" || restored.conversationByFloor.lobby.id !== "a1"
    || restored.conversationByFloor.lobby.kind !== "agent") {
    throw new Error("valid session must round-trip");
}

// A malformed per-floor chat map becomes empty, and bad entries are dropped
// while good ones survive — each with a warning, never silently.
const warnings = [];
const realWarn = console.warn;
console.warn = (...args) => { warnings.push(args.join(" ")); };
S.save({ place: "chat", conversationByFloor: ["a1"], railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (JSON.stringify(restored.conversationByFloor) !== "{}") {
    throw new Error("a non-object map must become {}");
}
if (warnings.length !== 1) throw new Error(`expected one warning, got ${warnings.length}`);
S.save({ place: "chat", railCollapsed: false, conversationByFloor: {
    lobby: { id: "a1", kind: "agent" },
    fin: { id: "t1", kind: "thread" },
    "": { id: "a2", kind: "agent" },
    ops: { id: "", kind: "agent" },
    hr: { id: "a3", kind: "desk" },
    eng: "a4",
} });
restored = S.validate(S.load(), ctx);
console.warn = realWarn;
if (JSON.stringify(restored.conversationByFloor) !==
    JSON.stringify({ lobby: { id: "a1", kind: "agent" }, fin: { id: "t1", kind: "thread" } })) {
    throw new Error(`bad entries must drop, good ones stay: ${JSON.stringify(restored.conversationByFloor)}`);
}
if (warnings.length !== 2 || warnings[1].indexOf("4") === -1) {
    throw new Error(`one warning must name the dropped count: ${warnings.join(" | ")}`);
}

// An unknown place falls back to chat.
S.save({ place: "nowhere", railCollapsed: false });
restored = S.validate(S.load(), ctx);
if (restored.place !== "chat") throw new Error("unknown place must fall back to chat");

// Corrupt JSON is discarded whole, never partially applied.
global.localStorage.setItem("bossmod_ui", "{not json");
const afterCorrupt = S.validate(S.load(), ctx);
if (afterCorrupt.place !== "chat" || JSON.stringify(afterCorrupt.conversationByFloor) !== "{}") {
    throw new Error("corrupt blob must yield clean defaults");
}

// A partial object gets full defaults, not undefined holes.
global.localStorage.setItem("bossmod_ui", JSON.stringify({ place: "log" }));
const partial = S.validate(S.load(), ctx);
for (const key of ["place", "conversationByFloor", "railCollapsed", "currentFloorId"]) {
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
    sanitizesFloorConversations: true,
    fallsBackOnUnknownPlace: true,
    discardsCorruptBlob: true,
    fillsDefaults: true,
}));
