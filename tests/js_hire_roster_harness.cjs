/**
 * Node harness for hire color default and live roster upsert.
 * Invoked by tests/test_hire_ui_poke.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = {
    createElement() {
        return { textContent: "", innerHTML: "", style: {} };
    },
    getElementById() {
        return null;
    },
    addEventListener() {},
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModUtils = BossModUtils;\n`);

if (typeof BossModUtils !== "object") {
    throw new Error("BossModUtils missing");
}
if (typeof BossModUtils.nextUnusedAgentColor !== "function") {
    throw new Error("nextUnusedAgentColor missing");
}
if (typeof BossModUtils.mergeRosterFromWorld !== "function") {
    throw new Error("mergeRosterFromWorld missing");
}

const palette = BossModUtils.AGENT_COLOR_PALETTE;
if (!Array.isArray(palette) || palette.length < 2) {
    throw new Error("AGENT_COLOR_PALETTE missing");
}

const firstUnused = BossModUtils.nextUnusedAgentColor([]);
if (firstUnused !== palette[0]) {
    throw new Error(`empty roster should default to ${palette[0]}, got ${firstUnused}`);
}

const afterBlue = BossModUtils.nextUnusedAgentColor([{ id: "a1", color: palette[0] }]);
if (afterBlue !== palette[1]) {
    throw new Error(`next unused after blue should be ${palette[1]}, got ${afterBlue}`);
}

const skipUsed = BossModUtils.nextUnusedAgentColor([
    { id: "a1", color: palette[0] },
    { id: "a2", color: palette[2] },
]);
if (skipUsed !== palette[1]) {
    throw new Error(`should pick first unused hole, got ${skipUsed}`);
}

const excludeOwn = BossModUtils.nextUnusedAgentColor(
    [{ id: "edit-me", color: palette[0] }, { id: "peer", color: palette[1] }],
    { excludeId: "edit-me" },
);
if (excludeOwn !== palette[0]) {
    throw new Error(`editing agent should not consume its own color, got ${excludeOwn}`);
}

const wrapped = BossModUtils.nextUnusedAgentColor(
    palette.map((color, index) => ({ id: `full-${index}`, color })),
);
if (wrapped !== palette[0]) {
    throw new Error(`full palette should rotate, got ${wrapped}`);
}

const emptyIncoming = BossModUtils.mergeRosterFromWorld(
    [{ id: "gone", name: "Gone", tasks_completed: 3 }],
    [],
);
if (emptyIncoming.length !== 0) {
    throw new Error("empty world_update must drop stale roster rows");
}

const seeded = BossModUtils.mergeRosterFromWorld([], [
    { id: "new-1", name: "Nova", role: "Writer", color: palette[1], status: "idle", x: 3, y: 4, location: "Main Workspace" },
]);
if (seeded.length !== 1 || seeded[0].id !== "new-1" || seeded[0].name !== "Nova") {
    throw new Error("empty directory must accept the first created agent");
}
if (seeded[0].location !== "Main Workspace") {
    throw new Error(`new agent should keep world location, got ${seeded[0].location}`);
}

const patched = BossModUtils.mergeRosterFromWorld(
    [{ id: "keep", name: "Ada", role: "Lead", tasks_completed: 9, location: "Main Workspace", status: "idle" }],
    [{ id: "keep", name: "Ada", status: "working", currentActivityKind: "work", x: 7, y: 4 }],
);
if (patched.length !== 1 || patched[0].tasks_completed !== 9) {
    throw new Error("world tick must preserve extra roster fields");
}
if (patched[0].status !== "working" || patched[0].currentActivityKind !== "work") {
    throw new Error("world tick must patch live status");
}

const upserted = BossModUtils.mergeRosterFromWorld(
    [{ id: "old", name: "Ada" }],
    [
        { id: "old", name: "Ada", status: "idle" },
        { id: "new-2", name: "Bea", role: "Auditor" },
    ],
);
if (upserted.map((item) => item.id).join(",") !== "old,new-2") {
    throw new Error(`membership upsert failed: ${upserted.map((item) => item.id)}`);
}

const ignored = BossModUtils.mergeRosterFromWorld([{ id: "old", name: "Ada" }], null);
if (ignored.length !== 1 || ignored[0].id !== "old") {
    throw new Error("non-array incoming must leave roster unchanged");
}

process.stdout.write(JSON.stringify({
    ok: true,
    firstUnused: firstUnused === palette[0],
    nextUnusedSkipsTaken: afterBlue === palette[1] && skipUsed === palette[1],
    rotatesWhenFull: wrapped === palette[0],
    emptyRosterAcceptsCreate: seeded[0].id === "new-1",
    upsertsMembership: upserted.length === 2,
    dropsMissing: emptyIncoming.length === 0,
    preservesExtras: patched[0].tasks_completed === 9,
}) + "\n");
