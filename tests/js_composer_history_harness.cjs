/**
 * Node harness: the composer field's own undo history — the pure steps in
 * composer-history.js and the field mention-draft.js binds them to.
 * Invoked by tests/test_ui_composer_history.py. Not a browser bundle.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();

const paths = process.argv.slice(2);
const NAMES = [
    "BossModDom", "BossModAvatar", "BossModMentions", "BossModMentionPills",
    "BossModComposerHistory", "BossModMentionDraft",
];
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});

const History = global.BossModComposerHistory;
const Draft = global.BossModMentionDraft;

const JOE = { id: "joe", name: "Joe", role: "Eng", color: "#1d4ed8" };
const LIVE = [JOE];

// ─── Pure history ───

/** Type `text` one character at a time at the end, as an operator would. */
function typeEach(history, before, text) {
    let value = before;
    for (const ch of text) {
        value += ch;
        history.record(value, value.length, true);
    }
    return value;
}

const words = History.createHistory("");
typeEach(words, "", "hello world");
const wordSteps = [words.undo(), words.undo(), words.undo(), words.redo(), words.redo()];

const backspaces = History.createHistory("hello");
backspaces.record("hell", 4, true);
backspaces.record("hel", 3, true);
backspaces.record("he", 2, true);
const backspaceRunIsOneStep = [backspaces.undo(), backspaces.undo()];

const overwrite = History.createHistory("important draft");
overwrite.record("x", 1, true);
const selectAllThenTypeIsOneStep = [overwrite.undo(), overwrite.undo()];

const discrete = History.createHistory("");
typeEach(discrete, "", "ab");
discrete.record("abX", 3, false);
typeEach(discrete, "abX", "cd");
const discreteStepMakesThreeSteps = [
    discrete.undo(), discrete.undo(), discrete.undo(), discrete.undo(),
].map((step) => (step ? step.text : null));

const cleared = History.createHistory("");
typeEach(cleared, "", "ab");
cleared.undo();
cleared.record("z", 1, true);
const newRecordClearsRedo = cleared.redo();

const sealed = History.createHistory("");
typeEach(sealed, "", "hello world");
sealed.undo();
sealed.record("hellox", 6, true);
const typingAfterUndoDoesNotMerge = [sealed.undo(), sealed.undo()].map((step) => step && step.text);

// "hel|lo" + "l": a prefix-greedy diff would put the letter at 4.
const repeated = History.createHistory("hello");
repeated.record("helllo", 4, true);
const repeatedLetterDiffAtCaret = { undo: repeated.undo(), redo: repeated.redo() };

const fresh = History.createHistory("");
typeEach(fresh, "", "ab");
fresh.undo();
fresh.reset("fresh");
const resetClearsBoth = { undo: fresh.undo(), redo: fresh.redo() };

const settled = History.createHistory("hi @joe");
settled.settle("hi @Joe");
const settleAddsNoStep = settled.undo();
settled.record("hi @Joe!", 8, true);
const settleChangesPresent = settled.undo();

let caretRefused = "";
try {
    History.createHistory("").record("a", undefined, true);
} catch (err) {
    caretRefused = err instanceof TypeError ? err.message : `wrong error: ${err}`;
}

const key = (init) => Object.assign(
    { key: "z", ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, init);
const shortcuts = {
    ctrlZ: History.shortcutAction(key({ ctrlKey: true })),
    cmdZ: History.shortcutAction(key({ metaKey: true })),
    ctrlShiftZ: History.shortcutAction(key({ key: "Z", ctrlKey: true, shiftKey: true })),
    ctrlY: History.shortcutAction(key({ key: "y", ctrlKey: true })),
    cmdY: History.shortcutAction(key({ key: "y", metaKey: true })),
    ctrlAltZ: History.shortcutAction(key({ ctrlKey: true, altKey: true })),
    plainZ: History.shortcutAction(key({})),
};

const mergeable = {};
for (const type of [
    "insertText", "insertLineBreak", "insertParagraph", "deleteContentBackward", "deleteContentForward",
    "insertFromPaste", "deleteContent", "deleteByCut", "insertFromDrop", "insertReplacementText",
    "deleteWordBackward",
]) {
    mergeable[type] = History.mergeableInput(type);
}
mergeable.missing = History.mergeableInput(undefined);

// ─── The bound field ───

function boundField(value) {
    const el = documentStub.createElement("div");
    el.setAttribute("contenteditable", "true");
    el.className = "composer-input";
    Draft.bindEditable(el, { agents: LIVE });
    el.value = value;
    el.inputs = 0;
    el.addEventListener("input", () => { el.inputs += 1; });
    return el;
}

/** Native typing at the end, as far as the field can tell: text lands, then `input`. */
function typeInto(el, text) {
    for (const ch of text) {
        const kids = el.childNodes;
        const last = kids[kids.length - 1];
        const node = last && last.nodeType === 3 ? last : documentStub.createTextNode("");
        if (node !== last) el.append(node);
        node.textContent += ch;
        global.window._setSelection(node, node.textContent.length);
        el.dispatchEvent({ type: "input", inputType: "insertText", data: ch, isComposing: false });
    }
}

function press(el, init) {
    const event = Object.assign({
        type: "keydown", key: "z", ctrlKey: true, metaKey: false, shiftKey: false, altKey: false,
        isComposing: false, prevented: false, preventDefault() { this.prevented = true; },
    }, init);
    el.dispatchEvent(event);
    return event;
}

function historyCommand(el, inputType) {
    const event = { type: "beforeinput", inputType, prevented: false, preventDefault() { this.prevented = true; } };
    el.dispatchEvent(event);
    return event;
}

const typedField = boundField("draft ");
typeInto(typedField, "more");
const typedBefore = typedField.value;
typedField.inputs = 0;
const typedUndo = press(typedField, {});
const typingThenCtrlZ = {
    before: typedBefore,
    after: typedField.value,
    prevented: typedUndo.prevented,
    inputs: typedField.inputs,
};

const editField = boundField("say ");
editField.editValue("say hello", 9);
const editValueIsOneStep = {
    edited: editField.value,
    editInputs: editField.inputs,
    caret: editField.selectionStart,
};
press(editField, {});
editValueIsOneStep.undone = editField.value;
editValueIsOneStep.undoneCaret = editField.selectionStart;
press(editField, { key: "Z", shiftKey: true });
editValueIsOneStep.redone = editField.value;
editValueIsOneStep.redoneCaret = editField.selectionStart;

const cutField = boundField("");
typeInto(cutField, "abc");
cutField.value = "fresh";
const cutUndo = press(cutField, {});
const valueCutsHistory = { value: cutField.value, prevented: cutUndo.prevented };

const commandField = boundField("");
typeInto(commandField, "abc");
const undoCommand = historyCommand(commandField, "historyUndo");
const afterUndoCommand = commandField.value;
const redoCommand = historyCommand(commandField, "historyRedo");
const beforeinputHistoryUndo = {
    undoPrevented: undoCommand.prevented,
    afterUndo: afterUndoCommand,
    redoPrevented: redoCommand.prevented,
    afterRedo: commandField.value,
};

// An IME's draft text arrives as `input` with isComposing; only the commit counts.
const imeField = boundField("");
const imeNode = documentStub.createTextNode("");
imeField.append(imeNode);
for (const draft of ["に", "日本"]) {
    imeNode.textContent = draft;
    global.window._setSelection(imeNode, draft.length);
    imeField.dispatchEvent({ type: "input", inputType: "insertCompositionText", isComposing: true });
}
const composingKey = press(imeField, { isComposing: true });
imeField.dispatchEvent({ type: "compositionend", data: "日本" });
imeField.dispatchEvent({ type: "input", inputType: "insertCompositionText", isComposing: false });
press(imeField, {});
const composition = {
    composingKeyLeftToIme: composingKey.prevented === false,
    afterOneUndo: imeField.value,
};
press(imeField, { key: "Z", shiftKey: true });
composition.afterRedo = imeField.value;

const lockedField = boundField("");
typeInto(lockedField, "abc");
lockedField.setAttribute("contenteditable", "false");
const lockedUndo = press(lockedField, {});
const disabledFieldDoesNotRestore = { value: lockedField.value, prevented: lockedUndo.prevented };

// A select-all delete took the pill; Ctrl+Z repaints it, not bare text.
const pillField = boundField("ping @Joe ");
const hadPill = Boolean(pillField.querySelector(".mention-pill"));
pillField.replaceChildren();
global.window._setSelection(null);
pillField.dispatchEvent({ type: "input", inputType: "deleteContent", isComposing: false });
const clearedValue = pillField.value;
press(pillField, {});
const restoredPill = pillField.querySelector(".mention-pill");
const undoRepaintsPill = {
    hadPill,
    cleared: clearedValue,
    value: pillField.value,
    pillEditable: restoredPill ? restoredPill.getAttribute("contenteditable") : null,
};

// The history keeps what the field reads back (`@Joe`), not what was written.
const caseField = boundField("");
caseField.editValue("@joe ", 5);
const editValueReadsBack = { edited: caseField.value };
press(caseField, {});
editValueReadsBack.undone = caseField.value;
press(caseField, { key: "y" });
editValueReadsBack.redone = caseField.value;
press(caseField, {});
editValueReadsBack.undoneAgain = caseField.value;

// A refused edit changes nothing: the field and its history stay in step.
const refusingField = boundField("keep");
let refusal = "";
try {
    refusingField.editValue("x", 1.5);
} catch (err) {
    refusal = err instanceof TypeError ? err.message : `wrong error: ${err}`;
}
const refusedValue = refusingField.value;
press(refusingField, {});
const editValueRefusesBadCaret = {
    error: refusal,
    value: refusedValue,
    afterUndo: refusingField.value,
};

console.log(JSON.stringify({
    ok: true,
    wordSteps,
    backspaceRunIsOneStep,
    selectAllThenTypeIsOneStep,
    discreteStepMakesThreeSteps,
    newRecordClearsRedo,
    typingAfterUndoDoesNotMerge,
    repeatedLetterDiffAtCaret,
    resetClearsBoth,
    settleAddsNoStep,
    settleChangesPresent,
    caretRefused,
    shortcuts,
    mergeable,
    typingThenCtrlZ,
    editValueIsOneStep,
    valueCutsHistory,
    beforeinputHistoryUndo,
    composition,
    disabledFieldDoesNotRestore,
    undoRepaintsPill,
    editValueReadsBack,
    editValueRefusesBadCaret,
}));
