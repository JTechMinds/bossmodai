/**
 * Node harness: composer send stays editable, queues a second line, and
 * puts a rejected line back when the box is empty. Then the real composer, on
 * both kinds of pointer: on a touch screen Enter is a newline and only Send
 * sends; with a mouse, Enter sends as it always has.
 * Invoked by tests/test_ui_composer_send.py. Not a browser bundle.
 *
 * argv: gates.js, dom.js, composer-attachments.js, composer-history.js,
 *       mention-draft.js, composer.js
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

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

// The gate checks below hand the gate their own FakeEl objects; the shared
// fake DOM is for the real composer, which builds its own elements.
const documentStub = installDom();

const paths = process.argv.slice(2);
const NAMES = ["BossModGates", "BossModDom", "BossModComposerAttachments",
    "BossModComposerHistory", "BossModMentionDraft", "BossModComposer"];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

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

    // ─── Touch screens: Enter is a newline, Send sends (D3) ───
    //
    // A soft keyboard has no Shift+Enter, so an Enter that always sent left a
    // phone with no way to type a newline. The query is read on every key, so
    // flipping it mid-life is what a convertible does.
    const coarse = global.window.matchMedia("(pointer: coarse)");
    coarse._emit(true);
    const touchSent = [];
    const composer = BossModComposer.createComposer({
        store: { getState: () => ({ hasUsableModel: true }), subscribe: () => () => {} },
        onSend: async (text) => { touchSent.push(text); },
        canSend: () => true,
        disabledReason: () => "",
        onAttach: async () => [],
        getContext: () => ({ type: "agent", id: "a1" }),
        onRemoveAttachment: async () => {},
        getAttachmentLimits: async () => ({ max_per_message: 5 }),
        mentionScope: () => ({ everyone: true, memberIds: null }),
    });
    documentStub.body.append(composer.element);
    const field = composer.element.querySelector(".composer-input");
    const pressEnter = () => {
        const event = {
            type: "keydown", key: "Enter", shiftKey: false, ctrlKey: false, metaKey: false,
            altKey: false, isComposing: false, prevented: false,
            preventDefault() { this.prevented = true; },
        };
        field.dispatchEvent(event);
        return event;
    };
    const settle = async () => { for (let i = 0; i < 5; i += 1) await delay(0); };

    field.value = "two lines";
    const touchEnter = pressEnter();
    await settle();
    const touchEnterInsertsNewline = touchEnter.prevented === false && touchSent.length === 0
        && field.value === "two lines";
    if (!touchEnterInsertsNewline) {
        throw new Error(`a touch Enter must be left to the browser: prevented ${touchEnter.prevented}, sent ${JSON.stringify(touchSent)}`);
    }
    const touchPlaceholderHasNoShiftEnter = !/Shift\+Enter/.test(field.getAttribute("data-placeholder"))
        && field.getAttribute("data-placeholder") !== "";
    if (!touchPlaceholderHasNoShiftEnter) {
        throw new Error(`the touch placeholder still says ${field.getAttribute("data-placeholder")}`);
    }
    await composer.element.querySelector(".composer-send").dispatchClick();
    await settle();
    const touchSendButtonSends = touchSent.join("|") === "two lines";
    if (!touchSendButtonSends) throw new Error(`Send must send on touch, got ${JSON.stringify(touchSent)}`);

    // ─── A mouse and keyboard: today's behaviour, unchanged ───
    coarse._emit(false);
    composer.applyState();
    const pointerPlaceholderKeepsShiftEnter = /Shift\+Enter/.test(field.getAttribute("data-placeholder"));
    if (!pointerPlaceholderKeepsShiftEnter) {
        throw new Error(`the pointer placeholder lost its hint: ${field.getAttribute("data-placeholder")}`);
    }
    field.value = "one line";
    const pointerEnter = pressEnter();
    await settle();
    const pointerEnterSends = pointerEnter.prevented === true && touchSent.join("|") === "two lines|one line";
    if (!pointerEnterSends) {
        throw new Error(`a pointer Enter must send: prevented ${pointerEnter.prevented}, sent ${JSON.stringify(touchSent)}`);
    }
    composer.destroy();

    // ─── A new placeholder re-measures the empty field ───
    //
    // The placeholder IS the empty field's content, so swapping it can change
    // the field's height. The fake DOM has no layout, so the measure is
    // modelled from what a 390px phone renders: the one-line "no conversation"
    // reason is 33px, and the ready hint wraps to two lines, 54px. The field
    // is sized while it shows the reason (the conversation's setDraft), then
    // a conversation opens and only applyState runs.
    let allowed = false;
    const sized = BossModComposer.createComposer({
        store: { getState: () => ({ hasUsableModel: true }), subscribe: () => () => {} },
        onSend: async () => {},
        canSend: () => allowed,
        disabledReason: () => "Pick a person or thread to start talking.",
        onAttach: async () => [],
        getContext: () => ({ type: "agent", id: "a1" }),
        onRemoveAttachment: async () => {},
        getAttachmentLimits: async () => ({ max_per_message: 5 }),
        mentionScope: () => ({ everyone: true, memberIds: null }),
    });
    const sizedField = sized.element.querySelector(".composer-input");
    Object.defineProperty(sizedField, "scrollHeight", {
        configurable: true,
        get: () => (/Shift\+Enter/.test(sizedField.getAttribute("data-placeholder") || "") ? 54 : 33),
    });
    sized.setDraft(null);
    if (sizedField.style.height !== "33px") {
        throw new Error(`the reason placeholder must size the field to 33px, got ${sizedField.style.height}`);
    }
    allowed = true;
    sized.applyState();
    const placeholderChangeRemeasures = sizedField.style.height === "54px";
    if (!placeholderChangeRemeasures) {
        throw new Error(`the ready placeholder's second line is clipped: height ${sizedField.style.height}`);
    }
    sized.destroy();

    process.stdout.write(JSON.stringify({
        ok: true,
        keptDraftOnFailure,
        leftNewerDraftIntact,
        clearedOnSuccess: firstResult.ok === true && second.ok === true,
        queuedSecondSend: sent.join("|") === "keep this draft|next line",
        stayedEnabled: sendBtn.disabled === false && input.disabled === false,
        surfacedError: failedDraft === "retry me",
        touchEnterInsertsNewline,
        touchPlaceholderHasNoShiftEnter,
        touchSendButtonSends,
        pointerEnterSends,
        pointerPlaceholderKeepsShiftEnter,
        placeholderChangeRemeasures,
    }));
}

main().catch((err) => {
    process.stderr.write(String(err && err.stack || err));
    process.exit(1);
});
