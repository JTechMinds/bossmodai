/**
 * BossMod AI — the composer field that can hold mention pills.
 *
 * A textarea cannot keep a pill while the operator types. This is the
 * contenteditable adapter: `.value` is still `@Name` text (send and drafts
 * do not change), and a pick paints a pill that stays in the tree until
 * the field is next written. The adapter gives the field the rest of a
 * textarea's behaviour too, undo included: code repaints the field, so the
 * browser's native undo cannot follow it. Painting lives in mention-pill.js
 * and the undo steps in composer-history.js; this file serializes, places
 * the caret, and binds both to the field.
 */
const BossModMentionDraft = (() => {
    function mentionPill(node) {
        if (!node || node.nodeType !== 1) return null;
        if (node.classList && node.classList.contains('mention-pill')) return node;
        if (node.classList && node.classList.contains('mention-host')) {
            return node.querySelector ? node.querySelector('.mention-pill') : null;
        }
        return null;
    }

    function mentionName(node) {
        const pill = mentionPill(node);
        return String((pill && pill.getAttribute('data-agent-name')) || '').trim();
    }

    function mentionGlue(node) {
        const pill = mentionPill(node);
        return String((pill && pill.getAttribute('data-mention-glue')) || '');
    }

    function mentionTokenText(node) {
        const name = mentionName(node);
        return name ? `@${name}${mentionGlue(node)}` : '';
    }

    function isMentionNode(node) {
        return Boolean(mentionName(node));
    }

    /**
     * Flatten a composer field that may hold pills back to `@Name` text.
     * @param {HTMLElement|null} root
     * @returns {string}
     */
    function readEditable(root) {
        let out = '';
        function walk(node) {
            if (!node) return;
            if (node.nodeType === 3) {
                out += node.textContent || '';
                return;
            }
            if (node.nodeType !== 1) return;
            if (isMentionNode(node)) {
                out += mentionTokenText(node);
                return;
            }
            if (node.tagName === 'BR') {
                out += '\n';
                return;
            }
            const block = node !== root && (node.tagName === 'DIV' || node.tagName === 'P');
            if (block && out && !out.endsWith('\n')) out += '\n';
            const kids = Array.from(node.childNodes || []);
            for (const child of kids) walk(child);
        }
        walk(root);
        return out;
    }

    function selectionApi() {
        return typeof window !== 'undefined' && window.getSelection
            ? window.getSelection() : null;
    }

    /**
     * Character offset of the caret inside a pill-bearing field.
     *
     * The live Selection is the only thing that knows where the caret is, and
     * it is GONE the moment a click moves focus out of the field — which is
     * every mouse pick from the `@` picker, because the option is not
     * editable and mousedown blurs the composer before the click handler
     * runs. So every successful live read is written back to `selectionStart`:
     * that shim is the fallback, nothing else maintains it (typing does not,
     * only placeCaret does), and an unmaintained fallback answers with the
     * caret the field was BOUND with. insertAtCaret then finds no `@query` in
     * front of that caret, replaces nothing, and the operator's half-typed
     * `@jo` survives next to the pill they just picked.
     *
     * The write is a cache of an authoritative read, not a second source of
     * truth: the live Selection always wins when there is one. A native
     * <textarea> never reaches it — its internal caret is not in the DOM
     * tree, so `inRoot` is false and its real `selectionStart` is untouched.
     *
     * @param {HTMLElement|null} root
     * @returns {number}
     */
    function caretIn(root) {
        if (!root) return 0;
        const sel = selectionApi();
        const inRoot = sel && sel.rangeCount
            && root.contains
            && (root === sel.anchorNode || root.contains(sel.anchorNode));
        if (inRoot) {
            const range = sel.getRangeAt(0);
            const at = distanceTo(root, range.startContainer, range.startOffset);
            root.selectionStart = at;
            root.selectionEnd = at;
            return at;
        }
        if (Number.isInteger(root.selectionStart)) return root.selectionStart;
        return readEditable(root).length;
    }

    function distanceTo(root, target, offset) {
        let count = 0;
        let found = false;
        function walk(node) {
            if (found || !node) return;
            if (node === target && node.nodeType === 3) {
                count += offset;
                found = true;
                return;
            }
            if (node.nodeType === 3) {
                count += (node.textContent || '').length;
                return;
            }
            if (node.nodeType !== 1) return;
            if (isMentionNode(node)) {
                count += mentionTokenText(node).length;
                if (node === target || (node.contains && target && node.contains(target))) {
                    found = true;
                }
                return;
            }
            if (node.tagName === 'BR') {
                count += 1;
                if (node === target) found = true;
                return;
            }
            if (node === target) {
                const kids = Array.from(node.childNodes || []);
                for (let i = 0; i < offset && i < kids.length; i += 1) walk(kids[i]);
                found = true;
                return;
            }
            for (const child of Array.from(node.childNodes || [])) walk(child);
        }
        walk(root);
        return count;
    }

    function findPosition(root, offset) {
        let remaining = Math.max(0, offset);
        let at = null;
        function walk(node) {
            if (at || !node) return;
            if (node.nodeType === 3) {
                const len = (node.textContent || '').length;
                if (remaining <= len) {
                    at = { node, offset: remaining };
                    return;
                }
                remaining -= len;
                return;
            }
            if (node.nodeType !== 1) return;
            if (isMentionNode(node)) {
                const len = mentionTokenText(node).length;
                if (remaining <= len) {
                    at = { node, offset: 0, after: true };
                    remaining = 0;
                    return;
                }
                remaining -= len;
                return;
            }
            if (node.tagName === 'BR') {
                if (remaining <= 1) {
                    at = { node, offset: 0, after: true };
                    return;
                }
                remaining -= 1;
                return;
            }
            for (const child of Array.from(node.childNodes || [])) walk(child);
        }
        walk(root);
        return at;
    }

    /**
     * Put the caret at a `@Name`-string offset. No-ops without a Selection
     * API (the fake DOM): callers still write `selectionStart` for insert.
     * @param {HTMLElement|null} root
     * @param {number} offset
     * @returns {void}
     */
    function placeCaret(root, offset) {
        if (!root) return;
        const atOff = Number.isInteger(offset) ? offset : readEditable(root).length;
        root.selectionStart = atOff;
        root.selectionEnd = atOff;
        const sel = selectionApi();
        if (!sel || typeof document.createRange !== 'function') return;
        const range = document.createRange();
        const at = findPosition(root, atOff);
        if (!at) {
            range.selectNodeContents(root);
            range.collapse(false);
        } else if (at.after) {
            range.setStartAfter(at.node);
            range.collapse(true);
        } else {
            range.setStart(at.node, at.offset);
            range.collapse(true);
        }
        sel.removeAllRanges();
        sel.addRange(range);
    }

    /**
     * Make a contenteditable field behave like a textarea, undo included.
     *
     * Reading `.value` serializes pills back to `@Name` text. Assigning it
     * paints live `@Name` tokens as pills, replaces the text and starts a
     * fresh undo history, as a textarea does; use it for a new draft, never
     * for an edit. `el.editValue(text, caret)` is the edit: one undoable
     * step, the caret placed at `caret`, and a bubbling `input` dispatched.
     * Native typing and deleting are recorded from `input` (typing merges
     * per word). Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z, Ctrl+Y and the browser's own
     * historyUndo / historyRedo run this field's history, never the native
     * one, which cannot follow code that repaints the field. A restore
     * dispatches a bubbling `input`, as native undo does. While the field's
     * `contenteditable` is not `'true'` the shortcuts are swallowed and
     * nothing is restored. Tests pass `agents`; Chat relies on configure().
     *
     * @param {HTMLElement} el
     * @param {{agents?: object[], getAgents?: () => object[]}} [options]
     * @returns {HTMLElement}  `el`, with `value`, `editValue` and
     *   `setSelectionRange` defined on it.
     * @throws {Error} When `el` is missing, or composer-history.js has not
     *   loaded: a field without its history would quietly lose undo again.
     *   The bound `editValue` throws a TypeError, before it changes
     *   anything, when `caret` is not an integer.
     */
    function bindEditable(el, options) {
        if (!el) throw new Error('[mention-draft] bindEditable needs a field');
        if (typeof BossModComposerHistory === 'undefined') {
            throw new Error('[mention-draft] composer-history.js must load before bindEditable is called');
        }
        const opts = options || {};
        function roster() {
            if (opts.agents) return opts.agents;
            if (opts.getAgents) return opts.getAgents();
            return BossModMentions.currentAgents();
        }
        const history = BossModComposerHistory.createHistory(readEditable(el));

        function paint(raw) {
            if (typeof BossModMentionPills === 'undefined') {
                el.replaceChildren();
                if (raw) el.append(document.createTextNode(raw));
                return;
            }
            BossModMentionPills.paintDraft(el, raw, { agents: roster(), editable: true });
        }

        function announce() {
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }

        // The history is given what the field reads back, never what was
        // written: painting re-cases a live `@name`.
        function restore(step) {
            paint(step.text);
            history.settle(readEditable(el));
            placeCaret(el, step.caret);
            announce();
        }

        function run(inputType) {
            if (el.getAttribute('contenteditable') !== 'true') return;
            const step = inputType === 'historyUndo' ? history.undo() : history.redo();
            if (step) restore(step);
        }

        Object.defineProperty(el, 'value', {
            configurable: true,
            enumerable: true,
            get() { return readEditable(el); },
            set(text) {
                paint(String(text == null ? '' : text));
                history.reset(readEditable(el));
            },
        });
        el.editValue = (text, caret) => {
            // Refused before anything changes: a repaint without its step
            // would leave the history out of sync with the field.
            if (!Number.isInteger(caret)) throw new TypeError('[mention-draft] editValue needs an integer caret');
            paint(text);
            placeCaret(el, caret);
            history.record(readEditable(el), caret, false);
            announce();
        };
        el.addEventListener('input', (event) => {
            // Mid-composition text is the IME's draft; compositionend commits it.
            if (event.isComposing) return;
            history.record(readEditable(el), caretIn(el),
                BossModComposerHistory.mergeableInput(event.inputType));
        });
        el.addEventListener('compositionend', () => {
            history.record(readEditable(el), caretIn(el), false);
        });
        el.addEventListener('keydown', (event) => {
            if (event.isComposing) return;
            const action = BossModComposerHistory.shortcutAction(event);
            if (!action) return;
            // Swallowed even when disabled or empty: a native undo here would
            // run a second history over the same field.
            event.preventDefault();
            run(action);
        });
        // The Edit menu and assistive tech reach undo without a keystroke.
        el.addEventListener('beforeinput', (event) => {
            if (event.inputType !== 'historyUndo' && event.inputType !== 'historyRedo') return;
            event.preventDefault();
            run(event.inputType);
        });
        if (!Number.isInteger(el.selectionStart)) {
            el.selectionStart = readEditable(el).length;
        }
        el.setSelectionRange = (start) => {
            placeCaret(el, Number.isInteger(start) ? start : readEditable(el).length);
        };
        return el;
    }

    return { readEditable, caretIn, placeCaret, bindEditable };
})();
