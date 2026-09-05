/**
 * Node harness: last load generation wins; stale async applies are dropped.
 * Invoked by tests/test_ui_load_generation.py. Not a browser bundle.
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

eval(fs.readFileSync(process.argv[2], "utf8"));

if (typeof BossModUtils !== "object" || typeof BossModUtils.createLoadGeneration !== "function") {
    throw new Error("createLoadGeneration missing");
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function applyIfCurrent(generation, value, waitMs, sink) {
    const loadId = generation.next();
    await delay(waitMs);
    if (!generation.isCurrent(loadId)) return false;
    sink.value = value;
    return true;
}

async function main() {
    const gen = BossModUtils.createLoadGeneration();
    const first = gen.next();
    const second = gen.next();
    if (first !== 1 || second !== 2) {
        throw new Error(`expected 1 then 2, got ${first} then ${second}`);
    }
    if (gen.isCurrent(first)) throw new Error("first id must not stay current");
    if (!gen.isCurrent(second)) throw new Error("second id must be current");

    const other = BossModUtils.createLoadGeneration();
    if (!other.isCurrent(other.next())) {
        throw new Error("independent generations must not share counters");
    }
    if (gen.isCurrent(second) !== true) {
        throw new Error("other generation must not bump this one");
    }

    const selected = { value: null };
    const applied = await Promise.all([
        applyIfCurrent(gen, "agent-a", 25, selected),
        applyIfCurrent(gen, "agent-b", 5, selected),
    ]);
    if (applied[0] !== false) throw new Error("slower first select must not apply");
    if (applied[1] !== true) throw new Error("later select must apply");
    if (selected.value !== "agent-b") {
        throw new Error(`expected agent-b, got ${selected.value}`);
    }

    const desk = { path: null };
    const deskGen = BossModUtils.createLoadGeneration();
    const deskApplied = await Promise.all([
        applyIfCurrent(deskGen, "/me", 20, desk),
        applyIfCurrent(deskGen, "/projects", 2, desk),
    ]);
    if (deskApplied[0] !== false || deskApplied[1] !== true || desk.path !== "/projects") {
        throw new Error("stale desk path must not overwrite the later navigation");
    }

    const files = { value: null };
    const filesGen = BossModUtils.createLoadGeneration();
    const searchStarted = applyIfCurrent(filesGen, "search:alpha", 20, files);
    filesGen.next();
    const searchApplied = await searchStarted;
    if (searchApplied !== false || files.value !== null) {
        throw new Error("invalidating before settle must drop the in-flight search");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        lastSelectWins: selected.value === "agent-b",
        lastDeskPathWins: desk.path === "/projects",
        invalidatedSearchDropped: searchApplied === false,
    }));
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
