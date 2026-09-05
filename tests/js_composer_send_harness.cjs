/**
 * Node harness: composer send keeps the draft until ack and blocks double-submit.
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

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModUtils = BossModUtils;\n`);

if (typeof BossModUtils !== "object" || typeof BossModUtils.createComposerSendGate !== "function") {
    throw new Error("createComposerSendGate missing");
}
if (typeof BossModUtils.setComposerError !== "function") {
    throw new Error("setComposerError missing");
}

async function main() {
    const gate = BossModUtils.createComposerSendGate();
    const input = new FakeEl({ value: "keep this draft" });
    const sendBtn = new FakeEl();
    let sends = 0;
    let release;
    const pending = new Promise((resolve) => {
        release = resolve;
    });

    const first = gate.submit({
        input,
        sendBtn,
        async send() {
            sends += 1;
            await pending;
        },
    });

    if (!gate.busy()) throw new Error("submit must mark the gate busy");
    if (!sendBtn.disabled || !input.disabled) {
        throw new Error("controls must disable while in-flight");
    }
    if (input.value !== "keep this draft") {
        throw new Error("draft must stay until ack");
    }

    const second = await gate.submit({
        input,
        sendBtn,
        async send() {
            sends += 1;
        },
    });
    if (second.submitted !== false || second.reason !== "in-flight") {
        throw new Error("second submit must no-op while in-flight");
    }

    release();
    const firstResult = await first;
    if (firstResult.ok !== true || sends !== 1) {
        throw new Error(`expected one successful send, got sends=${sends} ok=${firstResult.ok}`);
    }
    if (input.value !== "") throw new Error("success must clear the composer");
    if (gate.busy()) throw new Error("gate must idle after ack");
    if (sendBtn.disabled || input.disabled) {
        throw new Error("controls must re-enable after ack");
    }

    input.value = "retry me";
    let failedDraft = "";
    const failed = await gate.submit({
        input,
        sendBtn,
        async send() {
            await delay(1);
            throw new Error("agent unreachable");
        },
        onError(_err, draft) {
            failedDraft = draft;
        },
    });
    if (failed.ok !== false || failed.submitted !== true) {
        throw new Error("failed send must report submitted + not ok");
    }
    if (input.value !== "retry me") {
        throw new Error("failure must restore/keep the typed draft");
    }
    if (failedDraft !== "retry me") {
        throw new Error("onError must receive the kept draft");
    }

    const blocked = await gate.submit({
        input,
        sendBtn,
        canSubmit: () => false,
        async send() {
            sends += 1;
        },
    });
    if (blocked.reason !== "blocked" || sends !== 1) {
        throw new Error("canSubmit false must not send");
    }

    const errorEl = new FakeEl({ class: "hidden" });
    BossModUtils.setComposerError(errorEl, "Failed to send meeting message.");
    if (errorEl.textContent !== "Failed to send meeting message.") {
        throw new Error("error banner must show the message");
    }
    if (errorEl.classList.contains("hidden")) {
        throw new Error("error banner must unhide");
    }
    BossModUtils.setComposerError(errorEl, "");
    if (errorEl.textContent !== "" || !errorEl.classList.contains("hidden")) {
        throw new Error("clearing the error must hide the banner");
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        keptDraftOnFailure: input.value === "retry me",
        clearedOnSuccess: firstResult.ok === true,
        blockedDoubleSubmit: second.reason === "in-flight",
        surfacedError: true,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
