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
        // A fake element is "rendered" unless it is hidden — the trap asks
        // this to skip controls a browser would skip. Task 3's ‹ is hidden on
        // a base layer, and must not become the trap's first stop.
        getClientRects() { return this.hidden === true ? [] : [{}]; },
        contains(other) {
            if (other === this) return true;
            return this.children.some((c) => c.contains && c.contains(other));
        },
        // core/overlays.js asks for its FOCUSABLE list; this fake answers by
        // TAG rather than by parsing the selector, which is enough for the
        // three shapes here and honest about being a fake. It walks in
        // document order, which is what the trap's first/last depend on. The
        // panel modal is the case that needs more than buttons: its body is a
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

// ── The panel size: the same contract over a body that scrolls ──
//
// createModal was built for a short question with two buttons. The agent form
// is tall, so `size: 'panel'` gives it a scrolling body with the title and the
// action row pinned outside — and a scrolling body of form controls is exactly
// where a focus trap leaks, so the trap is re-proven over one.
const trigger3 = makeEl("button");
body.append(trigger3);
trigger3.focus();

const field = makeEl("input");
const area = makeEl("textarea");
const formBody = makeEl("div");
formBody.append(field, area);

const panelDialog = BossModOverlays.createModal({
    title: "Edit role",
    body: formBody,
    actions: [{ label: "Cancel", tone: "quiet" }],
    size: "panel",
});
// Read before anything else opens: `plain` below is created and closed while
// this dialog is up, and closing it hands focus back to whatever held it.
const panelOpenedOn = document.activeElement;

const classesOf = (el) => String(el.getAttribute("class") || "").split(/\s+/);
const panelModalIsMarked = classesOf(panelDialog.element).includes("modal-panel")
    && panelDialog.element.getAttribute("data-size") === "panel"
    && panelDialog.element.getAttribute("role") === "dialog"
    && panelDialog.element.getAttribute("aria-modal") === "true";
if (!panelModalIsMarked) {
    throw new Error(`the panel size must be the same dialog: `
        + `${panelDialog.element.getAttribute("class")} / ${panelDialog.element.getAttribute("data-size")}`);
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
const panelBody = panelDialog.element.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-body"))[0];
const panelActions = panelDialog.element.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-actions"))[0];
const cancelBtn = panelActions && panelActions.children[0];
const panelModalActionsSitOutsideTheBody = Boolean(panelBody) && Boolean(panelActions)
    && panelBody.contains(field)
    && !panelBody.contains(cancelBtn)
    && panelDialog.element.children.indexOf(panelActions) > panelDialog.element.children.indexOf(panelBody);
if (!panelModalActionsSitOutsideTheBody) {
    throw new Error("the panel modal's actions must be pinned outside the scrolling body");
}

// ── Where a form dialog starts ──
//
// Round four pinned the primary action LAST and this focused the last action,
// so opening Hire landed the keyboard on `Create Agent` and Enter submitted an
// empty form. A dialog whose BODY holds focusable content starts on the first
// of those instead — the field the operator came to fill. A dialog whose body
// holds nothing focusable still starts on its safe last action, which the
// backdrop block below re-proves on a panel one.
const panelModalFocusesFirstBodyControl = panelOpenedOn === field;
const panelModalDoesNotFocusThePrimary = panelOpenedOn !== cancelBtn;

// Tab wraps through the whole dialog rather than walking into the page behind
// it.
if (document.activeElement !== panelOpenedOn) {
    throw new Error("setup: closing `plain` must hand focus back to the panel modal");
}
const panelKeys = () => document.listeners.keydown || [];
// The head's ✕ is the dialog's FIRST stop now, so the wrap is proven from it:
// backwards off the ✕ lands on the last action, forwards off that lands on
// the ✕ again. Neither leg is a no-op.
const panelClose = panelDialog.element.children[0].children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-close"))[0];
if (!panelClose) throw new Error("the panel modal must carry the frame's close button");
panelClose.focus();
panelKeys().forEach((fn) => fn({ key: "Tab", shiftKey: true, preventDefault() {} }));
const wrappedToTheActionRow = document.activeElement === cancelBtn;
panelKeys().forEach((fn) => fn({ key: "Tab", shiftKey: false, preventDefault() {} }));
const panelModalTrapsTabAcrossItsBody = wrappedToTheActionRow
    && document.activeElement === panelClose;
if (!panelModalTrapsTabAcrossItsBody) {
    throw new Error(`Tab must stay inside the panel modal, got `
        + `${document.activeElement && document.activeElement.tagName}`);
}

panelKeys().forEach((fn) => fn({ key: "Escape", preventDefault() {} }));
const panelModalEscCloses = body.children.indexOf(panelDialog.element) === -1;
const panelModalRestoresFocus = document.activeElement === trigger3;
if (!panelModalEscCloses) throw new Error("Esc must close the panel modal too");
if (!panelModalRestoresFocus) {
    throw new Error("the panel modal must return focus to whatever opened it");
}
if ((document.listeners.keydown || []).length !== 0) {
    throw new Error("the panel modal must unbind keydown on close");
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
    size: "panel",
});
// It is a PANEL and its body is empty, so it proves the focus rule keys off what
// the body holds rather than off the size flag: nothing to type in, so the
// keyboard lands on the action row exactly as a confirm dialog's does.
const scrimmedActions = scrimmed.element.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-actions"))[0];
const bodylessPanelModalFocusesLastAction = Boolean(scrimmedActions)
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
// The panel dialog carries a half-filled form, so a stray click outside it
// must not discard the operator's typing. The scrim is shared by every layer
// and always carries one listener, which dismisses only when EVERY open layer
// opted in to closeOnBackdrop — and this dialog did not. So it is fired, and
// the dialog must still be standing.
(backdropNode.listeners.click || []).forEach((fn) => fn({ preventDefault() {} }));
const backdropClickDoesNotDismiss = body.children.indexOf(scrimmed.element) !== -1;
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
// Opening the agent form and then a Delete confirm leaves TWO
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
    size: "panel",
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
// The confirm is a LAYER over the form: one frame on screen, the form kept
// underneath rather than destroyed.
const lowerLayerIsHidden = behind.element.hidden === true && !inFront.element.hidden;
const oneScrimForTwoLayers = backdrops().length === 1;

pressEscape();
const escapeClosesOnlyTheTopOverlay = panels().length === 1;
// ...and the one still standing is the FORM, not whichever bound its listener
// first — which is the same dialog under both the bug and a naive fix.
const escapeClosesTheConfirmNotTheFormBehindIt = panels().length === 1
    && panels()[0] === behind.element
    && body.children.indexOf(inFront.element) === -1;
const oneScrimForOneLayer = backdrops().length === 1;

// Read BEFORE the second press. Without it "nothing is open afterwards" is
// true in the buggy world too — the first Escape had already closed both — and
// the assertion would pass while proving nothing.
const oneWasStillStanding = panels().length === 1;
pressEscape();
const secondEscapeClosesTheRemainingOverlay = oneWasStillStanding
    && panels().length === 0
    && body.children.indexOf(behind.element) === -1;
// One scrim for the whole stack, and none once the last layer goes: a scrim
// that outlives its panels covers the app forever, so the count is read at
// every step of the sequence rather than only at the end.
const oneScrimForTheWholeStack = oneScrimForTwoLayers && oneScrimForOneLayer
    && backdrops().length === 0;
if ((document.listeners.keydown || []).length !== 0) {
    throw new Error("both stacked dialogs must unbind keydown on close");
}

// ── The frame: a chat-chrome head on every dialog ──
//
// The ‹ (hidden on a base layer), the title, an optional subtitle and tools,
// and the frame's own ✕, in that order on one row ABOVE the body. The ✕ closes exactly as Esc does, and it is where
// the keyboard lands when neither the body nor the action row has a stop.
const trigger6 = makeEl("button");
body.append(trigger6);
trigger6.focus();
let framedClosed = 0;
const tool = makeEl("button");
const framed = BossModOverlays.createModal({
    title: "a1-audit-ruling.md",
    subtitle: "working",
    tools: [tool],
    body: "read-only",
    actions: [],
    size: "panel",
    onClose: () => { framedClosed += 1; },
});
const frameHead = framed.element.children[0];
const headKids = frameHead.children.filter((c) => c.nodeType === 1);
const headIsFirstAndOrdered = classesOf(frameHead).includes("modal-head")
    && headKids.length === 5
    && classesOf(headKids[0]).includes("modal-back") && headKids[0].hidden === true
    && classesOf(headKids[1]).includes("modal-title")
    && headKids[1].children[0].textContent === "a1-audit-ruling.md"
    && classesOf(headKids[2]).includes("modal-subtitle")
    && classesOf(headKids[3]).includes("modal-tools") && headKids[3].contains(tool)
    && classesOf(headKids[4]).includes("modal-close")
    && classesOf(headKids[4]).includes("header-icon-btn");
if (!headIsFirstAndOrdered) throw new Error("the head must be back, title, subtitle, tools, close");
const panelSizeIsDeclared = framed.element.getAttribute("data-size") === "panel";
const closeButtonTakesFocusWhenNothingElseCan = document.activeElement === headKids[4];
const closeLabelNamesTheDialog =
    headKids[4].getAttribute("aria-label") === "Close a1-audit-ruling.md";
headKids[4].listeners.click[0]({ preventDefault() {} });
const closeButtonCloses = body.children.indexOf(framed.element) === -1
    && framedClosed === 1
    && document.activeElement === trigger6
    && (document.listeners.keydown || []).length === 0
    && scrims().length === 0;
if (!closeButtonCloses) throw new Error("the frame's close must close, once, and restore focus");

// ── The backdrop, opted into ──
//
// A viewer or a confirm has nothing a stray click can destroy, so it may
// close on the scrim. A function is asked AT CLICK TIME, which is how the
// file viewer refuses while an edit is unsaved.
const trigger7 = makeEl("button");
body.append(trigger7);
trigger7.focus();
const dismissable = BossModOverlays.createModal({
    title: "Task", body: "x", actions: [], closeOnBackdrop: true,
});
scrims()[0].listeners.click[0]({ preventDefault() {} });
const optedInBackdropCloses = body.children.indexOf(dismissable.element) === -1
    && scrims().length === 0
    && document.activeElement === trigger7;

let editing = true;
const guarded = BossModOverlays.createModal({
    title: "Viewer", body: "x", actions: [], closeOnBackdrop: () => !editing,
});
scrims()[0].listeners.click[0]({ preventDefault() {} });
const guardRefusesWhileEditing = body.children.indexOf(guarded.element) !== -1;
editing = false;
scrims()[0].listeners.click[0]({ preventDefault() {} });
const guardAllowsOnceClean = body.children.indexOf(guarded.element) === -1
    && scrims().length === 0;

let rejectsBadBackdropOption = false;
try {
    BossModOverlays.createModal({ title: "x", body: "x", actions: [], closeOnBackdrop: "yes" });
} catch (err) {
    rejectsBadBackdropOption = true;
}
const badOptionMountsNothing = scrims().length === 0
    && body.children.filter((n) => classesOf(n).includes("modal-panel")).length === 0;

// ── A hidden control is not a place to land ──
//
// The file viewer's editor waits in the body, hidden, until Edit. A browser
// answers focus() on a hidden control by doing nothing, so opening on "the
// first focusable" left the keyboard on the opener, behind the scrim. The fake
// models that exactly: a focus() that does not move focus.
const hiddenLike = (tag) => {
    const node = makeEl(tag);
    node.focus = () => {};
    node.getClientRects = () => [];
    return node;
};
const trigger8 = makeEl("button");
body.append(trigger8);
trigger8.focus();
const hiddenEditor = hiddenLike("textarea");
const visibleField = makeEl("input");
const mixedBody = makeEl("div");
mixedBody.append(hiddenEditor, visibleField);
const mixed = BossModOverlays.createModal({ title: "Mixed", body: mixedBody, actions: [] });
const skipsAHiddenFirstControl = document.activeElement === visibleField;
mixed.close();

const onlyHiddenBody = makeEl("div");
onlyHiddenBody.append(hiddenLike("textarea"));
const readOnly = BossModOverlays.createModal({ title: "Read only", body: onlyHiddenBody, actions: [] });
const readOnlyClose = readOnly.element.children[0].children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-close"))[0];
const allHiddenBodyFallsBackToClose = document.activeElement === readOnlyClose;
readOnly.close();
if (document.activeElement !== trigger8) throw new Error("setup: focus must return to trigger8");

// ── The trap wraps past a hidden LAST control ──
//
// The file viewer ends its body with the editor, hidden until Edit. It matched
// FOCUSABLE, so the trap took it for the last stop and never saw Tab leave the
// last VISIBLE control — the browser then walked focus out of the dialog.
const trigger11 = makeEl("button");
body.append(trigger11);
trigger11.focus();
const readField = makeEl("input");
const trailingBody = makeEl("div");
trailingBody.append(readField, hiddenLike("textarea"));
const trailing = BossModOverlays.createModal({ title: "Viewer", body: trailingBody, actions: [] });
const trailingClose = trailing.element.children[0].children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes("modal-close"))[0];
if (document.activeElement !== readField) throw new Error("setup: the visible field must hold focus");
(document.listeners.keydown || []).forEach((fn) => fn({ key: "Tab", shiftKey: false, preventDefault() {} }));
const trapWrapsPastAHiddenLastControl = document.activeElement === trailingClose;
trailing.close();

// ── Layers: one frame on screen, ‹ goes back, ✕ closes all ──
//
// A modal opened from a modal is a LAYER in the same frame, not a second
// dialog on top: the one beneath is hidden (kept, not destroyed), the head
// grows a ‹ named for it, ‹ and Esc go back one, and ✕ closes every layer.
const findIn = (node, cls) => node.children.filter(
    (c) => c.nodeType === 1 && classesOf(c).includes(cls))[0];
const headOf = (handle) => handle.element.children[0];
const trigger9 = makeEl("button");
body.append(trigger9);
trigger9.focus();
const closedOrder = [];
const baseBody = makeEl("div");
const opener = makeEl("button");
baseBody.append(makeEl("input"), opener);
const baseLayer = BossModOverlays.createModal({
    title: "Open A1 PR", body: baseBody, actions: [], size: "panel",
    closeOnBackdrop: true, onClose: () => closedOrder.push("base"),
});
opener.focus();
const topLayer = BossModOverlays.createModal({
    title: "Cancel task?", body: "Stop the work?", actions: [{ label: "Keep it" }],
    closeOnBackdrop: true, onClose: () => closedOrder.push("top"),
});
const topBack = findIn(headOf(topLayer), "modal-back");
const oneFrameOnScreen = baseLayer.element.hidden === true
    && !topLayer.element.hidden && scrims().length === 1;
const backNamesTheLayerBeneath = Boolean(topBack) && topBack.hidden === false
    && topBack.getAttribute("aria-label") === "Back to Open A1 PR"
    && findIn(headOf(baseLayer), "modal-back").hidden === true;

topBack.listeners.click[0]({ preventDefault() {} });
const backReturnsToTheLayerBeneath = body.children.indexOf(topLayer.element) === -1
    && baseLayer.element.hidden === false
    && document.activeElement === opener
    && closedOrder.join(",") === "top"
    && scrims().length === 1;

const again = BossModOverlays.createModal({
    title: "Viewer", body: "x", actions: [], onClose: () => closedOrder.push("again"),
});
pressEscape();
const escGoesBackOneLayer = body.children.indexOf(again.element) === -1
    && body.children.indexOf(baseLayer.element) !== -1
    && baseLayer.element.hidden === false;

const third = BossModOverlays.createModal({
    title: "Third", body: "x", actions: [], onClose: () => closedOrder.push("third"),
});
findIn(headOf(third), "modal-close").listeners.click[0]({ preventDefault() {} });
const closeClosesTheWholeStack = panels().length === 0 && scrims().length === 0
    && closedOrder.join(",") === "top,again,third,base"
    && document.activeElement === trigger9
    && (document.listeners.keydown || []).length === 0;

// An outside click closes the stack only when EVERY layer allows it: a form
// beneath a viewer must not lose its typing to a stray click.
const formLayer = BossModOverlays.createModal({
    title: "Edit agent", body: makeEl("div"), actions: [{ label: "Cancel" }],
});
const browseLayer = BossModOverlays.createModal({
    title: "Marketplace", body: "x", actions: [], closeOnBackdrop: true,
});
scrims()[0].listeners.click[0]({ preventDefault() {} });
const scrimSparesAStackHoldingAForm = panels().length === 2;
findIn(headOf(browseLayer), "modal-close").listeners.click[0]({ preventDefault() {} });
const formStackClosedByTheX = panels().length === 0 && scrims().length === 0;

// close() removes exactly its own layer. A middle one closing — an action
// that opened a dialog before its own closed — relabels the ‹ above it, and
// the top's lost opener falls back to the new top's ✕.
const layerA = BossModOverlays.createModal({ title: "A", body: "x", actions: [] });
const layerB = BossModOverlays.createModal({ title: "B", body: "x", actions: [] });
const layerC = BossModOverlays.createModal({ title: "C", body: "x", actions: [] });
layerB.close();
const middleCloseRelabels = findIn(headOf(layerC), "modal-back").getAttribute("aria-label") === "Back to A"
    && panels().length === 2 && !layerC.element.hidden && layerA.element.hidden === true;
layerC.close();
const lostOpenerFallsBackToClose = document.activeElement === findIn(headOf(layerA), "modal-close")
    && layerA.element.hidden === false;
layerA.close();
if (panels().length !== 0 || scrims().length !== 0) throw new Error("layers block must leave nothing open");

// A base closing UNDER a layer — an action that opened a dialog before its own
// closed — hands its opener up. When the last layer goes, focus returns to the
// base screen, not to a node that left with the old base.
const trigger12 = makeEl("button");
body.append(trigger12);
trigger12.focus();
const oldBase = BossModOverlays.createModal({ title: "Old base", body: "x", actions: [] });
const survivor = BossModOverlays.createModal({ title: "Survivor", body: "x", actions: [] });
oldBase.close();
const survivorBecomesTheBase = findIn(headOf(survivor), "modal-back").hidden === true
    && !survivor.element.hidden && panels().length === 1 && scrims().length === 1;
survivor.close();
const baseCloseHandsItsOpenerUp = document.activeElement === trigger12
    && panels().length === 0 && scrims().length === 0;

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
    panelModalIsMarked,
    panelModalActionsSitOutsideTheBody,
    panelModalTrapsTabAcrossItsBody,
    panelModalEscCloses,
    panelModalRestoresFocus,
    panelModalFocusesFirstBodyControl,
    panelModalDoesNotFocusThePrimary,
    confirmModalFocusesLastAction,
    bodylessPanelModalFocusesLastAction,
    escapeClosesOnlyTheTopOverlay,
    escapeClosesTheConfirmNotTheFormBehindIt,
    secondEscapeClosesTheRemainingOverlay,
    oneScrimForTheWholeStack,
    lowerLayerIsHidden,
    backdropExists,
    backdropSitsBehindThePanel,
    backdropClickDoesNotDismiss,
    closeRemovesBackdrop,
    escStillCloses,
    headIsFirstAndOrdered,
    panelSizeIsDeclared,
    closeButtonTakesFocusWhenNothingElseCan,
    closeLabelNamesTheDialog,
    closeButtonCloses,
    optedInBackdropCloses,
    guardRefusesWhileEditing,
    guardAllowsOnceClean,
    rejectsBadBackdropOption,
    badOptionMountsNothing,
    skipsAHiddenFirstControl,
    allHiddenBodyFallsBackToClose,
    trapWrapsPastAHiddenLastControl,
    oneFrameOnScreen,
    backNamesTheLayerBeneath,
    backReturnsToTheLayerBeneath,
    escGoesBackOneLayer,
    closeClosesTheWholeStack,
    scrimSparesAStackHoldingAForm,
    formStackClosedByTheX,
    middleCloseRelabels,
    lostOpenerFallsBackToClose,
    survivorBecomesTheBase,
    baseCloseHandsItsOpenerUp,
    slideOverIsGone: typeof BossModOverlays.slideOver === "undefined",
    menuFocusesFirstOption,
    menuTrapsTab,
    menuEscCloses,
    menuRestoresFocusToTheAnchor,
    menuNeedsAnAnchor,
}));
