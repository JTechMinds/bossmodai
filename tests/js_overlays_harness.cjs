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
        // core/overlays.js asks for its FOCUSABLE list; this fake answers by
        // TAG rather than by parsing the selector, which is enough for the
        // three shapes here and honest about being a fake. It walks in
        // document order, which is what the trap's first/last depend on. The
        // wide modal is the case that needs more than buttons: its body is a
        // form, and a trap that only cycled buttons would leak out of it.
        querySelectorAll() {
            const FOCUSABLE_TAGS = ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"];
            const out = [];
            const walk = (node) => {
                node.children.forEach((c) => {
                    if (FOCUSABLE_TAGS.includes(c.tagName) && !c.disabled) out.push(c);
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

// ── The wide variant: the same contract over a body that scrolls ──
//
// createModal was built for a short question with two buttons. The agent form
// is tall, so `size: 'wide'` gives it a scrolling body with the title and the
// action row pinned outside — and a scrolling body of form controls is exactly
// where a focus trap leaks, so the trap is re-proven over one.
const trigger3 = makeEl("button");
body.append(trigger3);
trigger3.focus();

const field = makeEl("input");
const area = makeEl("textarea");
const formBody = makeEl("div");
formBody.append(field, area);

const wide = BossModOverlays.createModal({
    title: "Edit role",
    body: formBody,
    actions: [{ label: "Cancel", tone: "quiet" }],
    size: "wide",
});

const classesOf = (el) => String(el.getAttribute("class") || "").split(/\s+/);
const wideModalIsMarked = classesOf(wide.element).includes("modal-panel")
    && wide.element.getAttribute("data-size") === "wide"
    && wide.element.getAttribute("role") === "dialog"
    && wide.element.getAttribute("aria-modal") === "true";
if (!wideModalIsMarked) {
    throw new Error(`the wide variant must be the same dialog: `
        + `${wide.element.getAttribute("class")} / ${wide.element.getAttribute("data-size")}`);
}

// The default size still says which it is, so the stylesheet never has to
// guess and a caller cannot half-opt into the variant.
const plain = BossModOverlays.createModal({
    title: "Plain", body: "x", actions: [{ label: "Ok" }],
});
if (plain.element.getAttribute("data-size") !== "default") {
    throw new Error("a modal with no size must still declare the default one");
}
plain.close();

// The action row is a SIBLING of the body, not inside it: inside the scroller
// it would scroll away from the form it applies to.
const wideBody = wide.element.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-body"))[0];
const wideActions = wide.element.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-actions"))[0];
const cancelBtn = wideActions && wideActions.children[0];
const wideModalActionsSitOutsideTheBody = Boolean(wideBody) && Boolean(wideActions)
    && wideBody.contains(field)
    && !wideBody.contains(cancelBtn)
    && wide.element.children.indexOf(wideActions) > wide.element.children.indexOf(wideBody);
if (!wideModalActionsSitOutsideTheBody) {
    throw new Error("the wide modal's actions must be pinned outside the scrolling body");
}

// Focus opens on the safe action, and Tab wraps through the BODY rather than
// walking into the page behind it.
if (document.activeElement !== cancelBtn) {
    throw new Error("the wide modal must focus its safe action like any other");
}
const wideKeys = () => document.listeners.keydown || [];
wideKeys().forEach((fn) => fn({ key: "Tab", shiftKey: false, preventDefault() {} }));
const wrappedToTheBody = document.activeElement === field;
wideKeys().forEach((fn) => fn({ key: "Tab", shiftKey: true, preventDefault() {} }));
const wideModalTrapsTabAcrossItsBody = wrappedToTheBody
    && document.activeElement === cancelBtn;
if (!wideModalTrapsTabAcrossItsBody) {
    throw new Error(`Tab must stay inside the wide modal, got `
        + `${document.activeElement && document.activeElement.tagName}`);
}

wideKeys().forEach((fn) => fn({ key: "Escape", preventDefault() {} }));
const wideModalEscCloses = body.children.indexOf(wide.element) === -1;
const wideModalRestoresFocus = document.activeElement === trigger3;
if (!wideModalEscCloses) throw new Error("Esc must close the wide modal too");
if (!wideModalRestoresFocus) {
    throw new Error("the wide modal must return focus to whatever opened it");
}
if ((document.listeners.keydown || []).length !== 0) {
    throw new Error("the wide modal must unbind keydown on close");
}

// ── The anchored menu: same contract, third shape ──
//
// It is the chat header's `⋯`. Non-modal, but it owes the same three things
// the other two owe: Esc dismisses, Tab stays inside, and focus goes back to
// the control that opened it — named explicitly rather than read off
// document.activeElement, because a mouse click does not focus a button in
// every browser.
const container = makeEl("div");
body.append(container);
const anchor = makeEl("button");
container.append(anchor);
const optionA = makeEl("button");
const optionB = makeEl("button");
let menuClosed = 0;

const menu = BossModOverlays.createMenu({
    anchor,
    label: "View options",
    items: [optionA, optionB],
    container,
    onClose: () => { menuClosed += 1; },
});

if (menu.element.getAttribute("role") !== "dialog") {
    throw new Error("the menu must be a dialog: it carries a role=switch, which is no menuitem");
}
// Non-modal on purpose: a handful of view options does not block the page.
if (menu.element.getAttribute("aria-modal") !== null) {
    throw new Error("the menu must not claim to be modal");
}
if (container.children.indexOf(menu.element) === -1) {
    throw new Error("the menu must sit in the container it is positioned against");
}
if (document.activeElement !== optionA) {
    throw new Error("the menu must land focus on its first option");
}
const menuFocusesFirstOption = true;

// Tab from the last option wraps to the first rather than leaving the panel.
optionB.focus();
const menuKeys = () => document.listeners.keydown || [];
if (menuKeys().length === 0) throw new Error("the menu must bind a keydown handler");
menuKeys().forEach((fn) => fn({ key: "Tab", shiftKey: false, preventDefault() {} }));
if (document.activeElement !== optionA) {
    throw new Error("Tab must stay inside the menu");
}
const menuTrapsTab = true;

menuKeys().forEach((fn) => fn({ key: "Escape", preventDefault() {} }));
const menuEscCloses = container.children.indexOf(menu.element) === -1 && menuClosed === 1;
if (!menuEscCloses) throw new Error("Esc must close the menu exactly once");
const menuRestoresFocusToTheAnchor = document.activeElement === anchor;
if (!menuRestoresFocusToTheAnchor) {
    throw new Error("closing the menu must return focus to the control that opened it");
}
if ((document.listeners.keydown || []).length !== 0) {
    throw new Error("the menu must unbind keydown on close");
}

// A menu with no control to hang off is a keyboard dead end, so it refuses.
let menuNeedsAnAnchor = false;
try {
    BossModOverlays.createMenu({ label: "x", items: [], container });
} catch (err) {
    menuNeedsAnAnchor = true;
}
if (!menuNeedsAnAnchor) throw new Error("an anchorless menu must throw, not open");

process.stdout.write(JSON.stringify({
    ok: true,
    hasDialogSemantics: true,
    focusesSafeAction: true,
    escClosesWithoutConfirming: true,
    restoresFocus: true,
    unbindsOnClose: true,
    wideModalIsMarked,
    wideModalActionsSitOutsideTheBody,
    wideModalTrapsTabAcrossItsBody,
    wideModalEscCloses,
    wideModalRestoresFocus,
    menuFocusesFirstOption,
    menuTrapsTab,
    menuEscCloses,
    menuRestoresFocusToTheAnchor,
    menuNeedsAnAnchor,
}));
