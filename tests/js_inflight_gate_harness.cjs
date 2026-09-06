/**
 * Node harness: createInFlightGate blocks a second run until the first settles.
 * Invoked by tests/test_ui_channel_gaps.py. Not a browser bundle.
 */
const fs = require("fs");

global.document = {
    createElement() {
        return { textContent: "", innerHTML: "", style: {}, classList: { toggle() {} } };
    },
    getElementById() {
        return null;
    },
    addEventListener() {},
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModUtils = BossModUtils;\n`);

if (typeof BossModUtils.createInFlightGate !== "function") {
    throw new Error("createInFlightGate missing");
}

const gate = BossModUtils.createInFlightGate();
let firstEntered = false;
let secondStarted = false;
let firstValue = null;

const first = gate.run(async () => {
    firstEntered = true;
    if (gate.busy() !== true) {
        throw new Error("gate must be busy during the first run");
    }
    const blocked = await gate.run(async () => {
        secondStarted = true;
        return "second";
    });
    if (blocked.started !== false || blocked.reason !== "in-flight") {
        throw new Error("second run must be rejected while in flight");
    }
    return "first";
});

first.then((result) => {
    firstValue = result.value;
    if (firstEntered !== true || secondStarted !== true && firstValue !== "first") {
        /* secondStarted must stay false */
    }
    if (secondStarted) {
        throw new Error("nested run must not execute the callback");
    }
    if (result.started !== true || firstValue !== "first") {
        throw new Error("first run must complete");
    }
    if (gate.busy() !== false) {
        throw new Error("gate must clear after the first run");
    }
    return gate.run(async () => "third");
}).then((after) => {
    if (after.started !== true || after.value !== "third") {
        throw new Error("a later run must be allowed after idle");
    }
    process.stdout.write(JSON.stringify({
        ok: true,
        blocksNested: true,
        clearsAfter: true,
        allowsLater: true,
    }));
}).catch((err) => {
    console.error(err);
    process.exit(1);
});
