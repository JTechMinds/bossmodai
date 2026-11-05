/**
 * BossMod AI — the composer field's own undo history.
 *
 * The composer is a contenteditable field that code rewrites (a paste, an
 * @mention pick), and every rewrite orphans the browser's native undo. So
 * the field keeps its own history, and this is the text half of it: no DOM,
 * no globals, no clock. mention-draft.js binds it to the element.
 *
 * Steps are diffs `{at, removed, inserted, mergeable}`, never snapshots, so
 * memory grows only with what the operator types or pastes in one editing
 * session and no depth cap is needed. Typing merges per word, a backspace or
 * forward-delete run is one step, and anything else is a step of its own.
 */
const BossModComposerHistory = (() => {
    const MERGEABLE_INPUT = new Set([
        'insertText',
        'insertLineBreak',
        'insertParagraph',
        'deleteContentBackward',
        'deleteContentForward',
    ]);

    function clamp(value, low, high) {
        return Math.min(Math.max(value, low), high);
    }

    // Anchored at the caret AFTER the edit, so a repeated letter ("hel|lo"
    // + "l") lands where it was typed, not at the end of the common prefix.
    function diff(prev, next, caretAfter) {
        const caret = clamp(caretAfter, 0, next.length);
        const maxSuffix = Math.min(prev.length, next.length - caret);
        let suffix = 0;
        while (suffix < maxSuffix
            && prev.charAt(prev.length - 1 - suffix) === next.charAt(next.length - 1 - suffix)) {
            suffix += 1;
        }
        const maxPrefix = Math.min(prev.length, next.length) - suffix;
        let prefix = 0;
        while (prefix < maxPrefix && prev.charAt(prefix) === next.charAt(prefix)) prefix += 1;
        return {
            at: prefix,
            removed: prev.slice(prefix, prev.length - suffix),
            inserted: next.slice(prefix, next.length - suffix),
        };
    }

    function merge(top, next) {
        if (!top.mergeable || !next.mergeable) return null;
        if (top.removed === '' && next.removed === '' && top.inserted !== ''
            && next.at === top.at + top.inserted.length) {
            // A space after a word starts the next step: undo goes a word at a time.
            if (/^\s/.test(next.inserted) && /\S$/.test(top.inserted)) return null;
            return { at: top.at, removed: '', inserted: top.inserted + next.inserted, mergeable: true };
        }
        if (top.inserted !== '' || next.inserted !== '') return null;
        if (next.at + next.removed.length === top.at) {
            return { at: next.at, removed: next.removed + top.removed, inserted: '', mergeable: true };
        }
        if (next.at === top.at) {
            return { at: top.at, removed: top.removed + next.removed, inserted: '', mergeable: true };
        }
        return null;
    }

    /**
     * An undo history over one field's text.
     *
     * The history keeps the field's present text and two stacks of diff
     * steps. `record(text, caret, mergeable)` turns a new text into a step,
     * merging typing runs; a text equal to the present one is a no-op.
     * `undo()` / `redo()` move one step and return the text and caret the
     * field should show, or null when there is nothing to move. `reset(text)`
     * empties both stacks around a new baseline, and `settle(text)` adopts
     * the text the field reads back after a restore without making a step.
     *
     * Failure mode: `record` throws a TypeError when `caret` is not an
     * integer. The diff is anchored at it, and a guessed anchor would put
     * undo in the wrong place without saying so.
     *
     * @param {string} text  The field's text when the history starts.
     * @returns {{
     *   record: (text: string, caret: number, mergeable: boolean) => void,
     *   undo: () => ({text: string, caret: number}|null),
     *   redo: () => ({text: string, caret: number}|null),
     *   reset: (text: string) => void,
     *   settle: (text: string) => void,
     * }}
     */
    function createHistory(text) {
        let present = String(text);
        let done = [];
        let undone = [];
        // Set by undo/redo so the next edit never merges into a restored step.
        let sealed = false;

        function record(nextText, caret, mergeable) {
            if (!Number.isInteger(caret)) {
                throw new TypeError('[composer-history] record needs an integer caret');
            }
            const next = String(nextText);
            // The field's own dispatched `input` lands here with what it already shows.
            if (next === present) return;
            const step = { ...diff(present, next, caret), mergeable: Boolean(mergeable) };
            const top = done[done.length - 1];
            const merged = top && !sealed ? merge(top, step) : null;
            if (merged) done[done.length - 1] = merged;
            else done.push(step);
            undone = [];
            sealed = false;
            present = next;
        }

        function undo() {
            const step = done.pop();
            if (!step) return null;
            undone.push(step);
            present = present.slice(0, step.at) + step.removed + present.slice(step.at + step.inserted.length);
            sealed = true;
            return { text: present, caret: step.at + step.removed.length };
        }

        function redo() {
            const step = undone.pop();
            if (!step) return null;
            done.push(step);
            present = present.slice(0, step.at) + step.inserted + present.slice(step.at + step.removed.length);
            sealed = true;
            return { text: present, caret: step.at + step.inserted.length };
        }

        function reset(nextText) {
            present = String(nextText);
            done = [];
            undone = [];
            sealed = false;
        }

        /**
         * Adopt the field's read-back text without making a step. A restore
         * repaints, and the field may re-render a restored `@name` in its
         * live case (`@Joe`); the history must hold what the field shows.
         * Only the case changes, never the length, so stored positions hold.
         * @param {string} nextText
         * @returns {void}
         */
        function settle(nextText) {
            present = String(nextText);
        }

        return { record, undo, redo, reset, settle };
    }

    /**
     * Whether an `input` event's type may merge into the previous step.
     *
     * Typing, line breaks and single-character deletes merge into runs. A
     * paste, a pick, a drop, a cut, a select-all delete, a word delete, a
     * replacement, a composition commit and a missing type are each a step
     * of their own.
     *
     * @param {string|undefined} inputType  `InputEvent.inputType`.
     * @returns {boolean}
     */
    function mergeableInput(inputType) {
        return MERGEABLE_INPUT.has(inputType);
    }

    /**
     * The history action a keystroke asks for.
     *
     * Ctrl/Cmd+Z undoes, Ctrl/Cmd+Shift+Z redoes, and Ctrl+Y redoes off a
     * Mac. Alt (AltGr on many layouts) is never a shortcut, so text typed
     * with it is left alone. The actions use `InputEvent.inputType`'s names,
     * so a keystroke and the browser's own `beforeinput` speak one vocabulary.
     *
     * @param {{key: string, ctrlKey: boolean, metaKey: boolean,
     *          shiftKey: boolean, altKey: boolean}} event  A keydown.
     * @returns {'historyUndo'|'historyRedo'|null}  null when the keystroke
     *   is not one.
     */
    function shortcutAction(event) {
        if (event.altKey) return null;
        if (!event.ctrlKey && !event.metaKey) return null;
        const key = typeof event.key === 'string' ? event.key.toLowerCase() : '';
        if (key === 'z') return event.shiftKey ? 'historyRedo' : 'historyUndo';
        if (key === 'y' && event.ctrlKey && !event.metaKey && !event.shiftKey) return 'historyRedo';
        return null;
    }

    return { createHistory, mergeableInput, shortcutAction };
})();
