/**
 * Node harness: composer send stays editable, queues a second line, and
 * puts a rejected line back when the box is empty.
 * Invoked by tests/test_ui_composer_send.py. Not a browser bundle.
 */
const fs = require("fs");

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

class FakeEl {
    constructor(attrs = {}) {
        this.className = attrs.class || "";
        this._classes = new Set(String(this.className).split(/\s+/).filter(Boolean));
        this.textContent = "";
        this.value = attrs.value || "";
        this.disabled = false;
        this.style = {};
        this.classList = {
            toggle: (name, force) => {
                const on = force === undefined ? !this._classes.has(name) : Boolean(force);
                if (on) this._classes.add(name);
                else this._classes.delete(name);
                this.className = Array.from(this._classes).join(" ");
            },
            contains: (name) => this._classes.has(name),
        };
    }
}

global.document = {
    createElement() {
        return new FakeEl();
    },
    getElementById() {
        return null;
    },
    addEventListener() {},
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModGates = BossModGates;\n`);

if (typeof BossModGates !== "object" || typeof BossModGates.createComposerSendGate !== "function") {
    throw new Error("createComposerSendGate missing");
}
if (typeof BossModGates.setComposerError !== "function") {
    throw new Error("setComposerError missing");
}

async function main() {
    const gate = BossModGates.createComposerSendGate();
    const input = new FakeEl({ value: "keep this draft" });
    const sendBtn = new FakeEl();
    const sent = [];
    let release;
    const pending = new Promise((resolve) => {
        release = resolve;
    });
    const queuedCounts = [];

    const first = gate.submit({
        input,
        sendBtn,
        onQueued(count) { queuedCounts.push(count); },
        async send(text) {
            sent.push(text);
            await pending;
        },
    });

    if (!gate.busy()) throw new Error("submit must mark the gate busy");
    if (sendBtn.disabled || input.disabled) {
        throw new Error("controls must stay enabled while a send is in flight");
    }
    if (input.value !== "") {
        throw new Error("accepted send must free the field so the next line can be typed");
    }

    input.value = "next line";
    const secondPromise = gate.submit({
        input,
        sendBtn,
        onQueued(count) { queuedCounts.push(count); },
        async send(text) {
            sent.push(text);
        },
    });
    if (sendBtn.disabled || input.disabled) {
        throw new Error("a queued follow-up must not gray the field");
    }
    if (input.value !== "") {
        throw new Error("queued follow-up must also free the field");
    }

    release();
    const firstResult = await first;
    const second = await secondPromise;
    if (firstResult.ok !== true || second.ok !== true) {
        throw new Error(`expected both sends to post, got ${firstResult.ok} ${second.ok}`);
    }
    if (sent.join("|") !== "keep this draft|next line") {
        throw new Error(`sends must post in order, got ${sent.join("|")}`);
    }
    if (input.value !== "") throw new Error("success must leave the freed composer clear");
    if (gate.busy()) throw new Error("gate must idle after ack");
    if (sendBtn.disabled || input.disabled) {
        throw new Error("controls must stay enabled after ack");
    }
    if (!queuedCounts.includes(2) || queuedCounts[queuedCounts.length - 1] !== 0) {
        throw new Error(`queued hint counts must rise and clear, got ${queuedCounts.join(",")}`);
    }

    input.value = "retry me";
    let failedDraft = "";
    const failed = await gate.submit({
        input,
        sendBtn,
        async send() {
            await delay(1);
            throw new Error("Channel has no members");
        },
        onError(_err, draft) {
            failedDraft = draft;
        },
    });
    if (failed.ok !== false || failed.submitted !== true) {
        throw new Error("failed send must report submitted + not ok");
    }
    if (input.value !== "retry me") {
        throw new Error("failure must restore the typed draft when the box is empty");
    }
    if (failedDraft !== "retry me") {
        throw new Error("onError must receive the kept draft");
    }
    const keptDraftOnFailure = true;

    input.value = "first fails";
    let releaseFail;
    const holdFail = new Promise((resolve) => {
        releaseFail = resolve;
    });
    const failing = gate.submit({
        input,
        sendBtn,
        async send() {
            await holdFail;
            throw new Error("queue full");
        },
    });
    input.value = "newer draft";
    releaseFail();
    const failResult = await failing;
    if (failResult.ok !== false) throw new Error("held send must fail");
    if (input.value !== "newer draft") {
        throw new Error("failure must leave a newer draft intact");
    }
    const leftNewerDraftIntact = true;

    const sendsBeforeBlock = sent.length;
    const blocked = await gate.submit({
        input,
        sendBtn,
        canSubmit: () => false,
        async send(text) {
            sent.push(text);
        },
    });
    if (blocked.reason !== "blocked" || sent.length !== sendsBeforeBlock) {
        throw new Error("canSubmit false must not send");
    }
    if (input.value !== "newer draft") {
        throw new Error("a blocked send must leave the text intact");
    }

    const errorEl = new FakeEl({ class: "hidden" });
    BossModGates.setComposerError(errorEl, "Failed to send meeting message.");
    if (errorEl.textContent !== "Failed to send meeting message.") {
        throw new Error("error banner must show the message");
    }
    if (errorEl.classList.contains("hidden")) {
        throw new Error("error banner must unhide");
    }
    BossModGates.setComposerError(errorEl, "");
    if (errorEl.textContent !== "" || !errorEl.classList.contains("hidden")) {
        throw new Error("clearing the error must hide the banner");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        keptDraftOnFailure,
        leftNewerDraftIntact,
        clearedOnSuccess: firstResult.ok === true && second.ok === true,
        queuedSecondSend: sent.join("|") === "keep this draft|next line",
        stayedEnabled: sendBtn.disabled === false && input.disabled === false,
        surfacedError: failedDraft === "retry me",
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
