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
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModOverlayFocus = BossModOverlayFocus;\n`);
eval(`${fs.readFileSync(process.argv[4], "utf8")}\n;global.BossModOverlays = BossModOverlays;\n`);

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

// The LAST action (Cancel) is focused by default — the safe choice, and the
// right one for a destructive prompt. This body is a sentence with nothing
// focusable in it, which is what keeps a confirm dialog on its action row now
// that a dialog whose body IS focusable starts inside the body instead.
const confirmModalFocusesLastAction = Boolean(document.activeElement)
    && document.activeElement.textLabel === "Cancel";
if (!confirmModalFocusesLastAction) {
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
// Read before anything else opens: `plain` below is created and closed while
// this dialog is up, and closing it hands focus back to whatever held it.
const wideOpenedOn = document.activeElement;

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

// ── Where a form dialog starts ──
//
// Round four pinned the primary action LAST and this focused the last action,
// so opening Hire landed the keyboard on `Create Agent` and Enter submitted an
// empty form. A dialog whose BODY holds focusable content starts on the first
// of those instead — the field the operator came to fill. A dialog whose body
// holds nothing focusable still starts on its safe last action, which the
// backdrop block below re-proves on a wide one.
const wideModalFocusesFirstBodyControl = wideOpenedOn === field;
const wideModalDoesNotFocusThePrimary = wideOpenedOn !== cancelBtn;

// Tab wraps through the whole dialog rather than walking into the page behind
// it. Driven from wherever focus actually opened, so neither leg is a no-op:
// backwards off the first stop, then forwards off the last.
if (document.activeElement !== wideOpenedOn) {
    throw new Error("setup: closing `plain` must hand focus back to the wide modal");
}
const wideKeys = () => document.listeners.keydown || [];
wideKeys().forEach((fn) => fn({ key: "Tab", shiftKey: true, preventDefault() {} }));
const wrappedToTheActionRow = document.activeElement === cancelBtn;
wideKeys().forEach((fn) => fn({ key: "Tab", shiftKey: false, preventDefault() {} }));
const wideModalTrapsTabAcrossItsBody = wrappedToTheActionRow
    && document.activeElement === field;
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

// ── The backdrop: the thing that makes a modal modal ──
//
// There was none — not in the JS and not in the stylesheet. The panel floated
// over a fully live page and clicks reached the controls behind it, whatever
// aria-modal claimed. Two properties matter: it EXISTS behind the panel, and
// it leaves WITH it. A leaked scrim covers the app and nothing is ever
// clickable again, which is far worse than the bug it fixes.
const trigger4 = makeEl("button");
body.append(trigger4);
trigger4.focus();

const scrimmed = BossModOverlays.createModal({
    title: "Edit role",
    body: makeEl("div"),
    actions: [{ label: "Cancel", tone: "quiet" }],
    size: "wide",
});
// It is WIDE and its body is empty, so it proves the focus rule keys off what
// the body holds rather than off the size flag: nothing to type in, so the
// keyboard lands on the action row exactly as a confirm dialog's does.
const scrimmedActions = scrimmed.element.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-actions"))[0];
const bodylessWideModalFocusesLastAction = Boolean(scrimmedActions)
    && document.activeElement === scrimmedActions.children[scrimmedActions.children.length - 1];
const scrims = () => body.children.filter(
    (node) => classesOf(node).includes("modal-backdrop"));
const backdropNode = scrims()[0];
const backdropExists = scrims().length === 1;
if (!backdropExists) {
    throw new Error(`a modal must block the page behind it, got ${scrims().length} backdrops`);
}
// Behind, not over: appended first, so the panel paints on top of it before
// z-index is even consulted.
const backdropSitsBehindThePanel =
    body.children.indexOf(backdropNode) < body.children.indexOf(scrimmed.element);
if (!backdropSitsBehindThePanel) {
    throw new Error("the backdrop must sit behind the panel, not over it");
}
// The wide variant carries a half-filled form, so a stray click outside it
// must not discard the operator's typing: the scrim carries no dismissal at
// all. Fired anyway, in case one is ever bound.
(backdropNode.listeners.click || []).forEach((fn) => fn({ preventDefault() {} }));
const backdropClickDoesNotDismiss = Object.keys(backdropNode.listeners).length === 0
    && body.children.indexOf(scrimmed.element) !== -1;
if (!backdropClickDoesNotDismiss) {
    throw new Error("clicking the backdrop must not throw the operator's typing away");
}
// Esc is unchanged — and takes the backdrop with it.
(document.listeners.keydown || []).forEach((fn) => fn({ key: "Escape", preventDefault() {} }));
const escStillCloses = body.children.indexOf(scrimmed.element) === -1;
const closeRemovesBackdrop = scrims().length === 0;
if (!escStillCloses) throw new Error("Esc must still close the modal");
if (!closeRemovesBackdrop) {
    throw new Error("a leaked backdrop leaves the whole app unclickable");
}

// ── Two dialogs, one Escape ──
//
// Opening the wide agent form and then a Delete confirm leaves TWO
// document-level keydown listeners, and every document listener hears every
// key press. Neither dialog knew the other existed, so one Escape tore down
// both — the confirm the operator meant to dismiss AND the half-filled form
// behind it. stopPropagation() alone cannot fix it: listeners fire in
// REGISTRATION order, so the FIRST dialog's handler runs first and stopping
// there closes the wrong one. The property is that one Escape closes exactly
// one overlay, and it is the one on top.
const trigger5 = makeEl("button");
body.append(trigger5);
trigger5.focus();

const panels = () => body.children.filter((n) => classesOf(n).includes("modal-panel"));
const backdrops = () => body.children.filter((n) => classesOf(n).includes("modal-backdrop"));

// A dispatch faithful to the DOM: the listener list is snapshotted when the
// event fires, and a listener removed part-way through is not called. Without
// the snapshot a `forEach` over the live array would skip the second listener
// purely because the first spliced itself out, which would hide the bug.
const pressEscape = () => {
    const live = document.listeners.keydown || [];
    live.slice().forEach((fn) => {
        if (live.indexOf(fn) === -1) return;
        fn({ key: "Escape", preventDefault() {}, stopPropagation() {} });
    });
};

const stackedField = makeEl("input");
const stackedFormBody = makeEl("div");
stackedFormBody.append(stackedField);
const behind = BossModOverlays.createModal({
    title: "Edit role",
    body: stackedFormBody,
    actions: [{ label: "Cancel", tone: "quiet" }],
    size: "wide",
});
const inFront = BossModOverlays.createModal({
    title: "Delete Nadia?",
    body: "This cannot be undone.",
    actions: [
        { label: "Delete", tone: "danger" },
        { label: "Cancel", tone: "quiet" },
    ],
});
if (panels().length !== 2) {
    throw new Error(`setup: two dialogs must be open, got ${panels().length}`);
}
const backdropsAtTwo = backdrops().length === 2;

pressEscape();
const escapeClosesOnlyTheTopOverlay = panels().length === 1;
// ...and the one still standing is the FORM, not whichever bound its listener
// first — which is the same dialog under both the bug and a naive fix.
const escapeClosesTheConfirmNotTheFormBehindIt = panels().length === 1
    && panels()[0] === behind.element
    && body.children.indexOf(inFront.element) === -1;
const backdropsAtOne = backdrops().length === 1;

// Read BEFORE the second press. Without it "nothing is open afterwards" is
// true in the buggy world too — the first Escape had already closed both — and
// the assertion would pass while proving nothing.
const oneWasStillStanding = panels().length === 1;
pressEscape();
const secondEscapeClosesTheRemainingOverlay = oneWasStillStanding
    && panels().length === 0
    && body.children.indexOf(behind.element) === -1;
// A scrim that outlives its panel covers the app forever, so the two counts
// are read at every step of the sequence rather than only at the end.
const backdropCountTracksPanelCount = backdropsAtTwo && backdropsAtOne
    && backdrops().length === 0;
if ((document.listeners.keydown || []).length !== 0) {
    throw new Error("both stacked dialogs must unbind keydown on close");
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
    wideModalFocusesFirstBodyControl,
    wideModalDoesNotFocusThePrimary,
    confirmModalFocusesLastAction,
    bodylessWideModalFocusesLastAction,
    escapeClosesOnlyTheTopOverlay,
    escapeClosesTheConfirmNotTheFormBehindIt,
    secondEscapeClosesTheRemainingOverlay,
    backdropCountTracksPanelCount,
    backdropExists,
    backdropSitsBehindThePanel,
    backdropClickDoesNotDismiss,
    closeRemovesBackdrop,
    escStillCloses,
    menuFocusesFirstOption,
    menuTrapsTab,
    menuEscCloses,
    menuRestoresFocusToTheAnchor,
    menuNeedsAnAnchor,
}));
