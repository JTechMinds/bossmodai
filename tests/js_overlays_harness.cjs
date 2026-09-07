/**
 * Node harness: modal focus trap, Esc dismissal, focus restoration.
 * Invoked by tests/test_ui_overlays.py. Not a browser bundle.
 */
const fs = require("fs");

let activeElement = null;
function makeEl(tag) {
    const el = {
        tagName: String(tag).toUpperCase(),
        nodeType: 1,
        attributes: {},
        children: [],
        listeners: {},
        parentNode: null,
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        append(...kids) {
            kids.forEach((k) => { k.parentNode = this; this.children.push(k); });
        },
        remove() {
            if (!this.parentNode) return;
            const i = this.parentNode.children.indexOf(this);
            if (i !== -1) this.parentNode.children.splice(i, 1);
            this.parentNode = null;
        },
        replaceChildren() { this.children = []; },
        addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
        removeEventListener(n, fn) {
            const l = this.listeners[n] || [];
            const i = l.indexOf(fn);
            if (i !== -1) l.splice(i, 1);
        },
        focus() { activeElement = this; },
        contains(other) {
            if (other === this) return true;
            return this.children.some((c) => c.contains && c.contains(other));
        },
        querySelectorAll() {
            const out = [];
            const walk = (node) => {
                node.children.forEach((c) => {
                    if (c.tagName === "BUTTON") out.push(c);
                    if (c.children) walk(c);
                });
            };
            walk(this);
            return out;
        },
    };
    return el;
}

const body = makeEl("body");
global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
    body,
    listeners: {},
    addEventListener(n, fn) { (this.listeners[n] = this.listeners[n] || []).push(fn); },
    removeEventListener(n, fn) {
        const l = this.listeners[n] || [];
        const i = l.indexOf(fn);
        if (i !== -1) l.splice(i, 1);
    },
    get activeElement() { return activeElement; },
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModOverlays = BossModOverlays;\n`);

// A trigger button has focus before the modal opens.
const trigger = makeEl("button");
body.append(trigger);
trigger.focus();
if (document.activeElement !== trigger) throw new Error("setup: trigger should hold focus");

let confirmed = false;
let closedVia = null;
const modal = BossModOverlays.createModal({
    title: "Pause everyone?",
    body: "Active turns will be cancelled.",
    actions: [
        { label: "Pause", tone: "danger", onSelect: () => { confirmed = true; } },
        { label: "Cancel", tone: "quiet", onSelect: () => { closedVia = "cancel"; } },
    ],
});

// Dialog semantics.
if (modal.element.getAttribute("role") !== "dialog") throw new Error("needs role=dialog");
if (modal.element.getAttribute("aria-modal") !== "true") throw new Error("needs aria-modal");

// The LAST action (Cancel) is focused by default — the safe choice.
if (!document.activeElement || document.activeElement.textLabel !== "Cancel") {
    throw new Error(`safe action must be focused, got ${document.activeElement && document.activeElement.textLabel}`);
}

// Esc closes and restores focus to the trigger.
const keyHandlers = document.listeners.keydown || [];
if (keyHandlers.length === 0) throw new Error("modal must bind a keydown handler");
keyHandlers.forEach((fn) => fn({ key: "Escape", preventDefault() {}, stopPropagation() {} }));
if (document.activeElement !== trigger) throw new Error("focus must return to the trigger on Esc");
if (confirmed) throw new Error("Esc must not confirm the action");

// The keydown handler is removed on close — no leak.
if ((document.listeners.keydown || []).length !== 0) {
    throw new Error("modal must unbind keydown on close");
}
if (body.children.indexOf(modal.element) !== -1) throw new Error("modal must detach from body");

// A second modal: the primary action fires and closes.
const trigger2 = makeEl("button");
body.append(trigger2);
trigger2.focus();
let fired = false;
const m2 = BossModOverlays.createModal({
    title: "Confirm",
    body: "sure?",
    actions: [
        { label: "Yes", tone: "danger", onSelect: () => { fired = true; } },
        { label: "No", tone: "quiet", onSelect: () => {} },
    ],
});
const buttons = m2.element.querySelectorAll("button");
const yes = buttons.filter((b) => b.textLabel === "Yes")[0];
if (!yes) throw new Error("primary action button not found");
yes.listeners.click[0]({ preventDefault() {} });
if (!fired) throw new Error("primary action must fire onSelect");
if (document.activeElement !== trigger2) throw new Error("focus must return after action");

process.stdout.write(JSON.stringify({
    ok: true,
    hasDialogSemantics: true,
    focusesSafeAction: true,
    escClosesWithoutConfirming: true,
    restoresFocus: true,
    unbindsOnClose: true,
}));
