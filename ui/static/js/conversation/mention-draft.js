/**
 * BossMod AI — the composer field that can hold mention pills.
 *
 * A textarea cannot keep a pill while the operator types. This is the
 * contenteditable adapter: `.value` is still `@Name` text (send and drafts
 * do not change), and a pick paints a pill that stays in the tree until
 * the next `.value` write. Painting lives in mention-pill.js; this file
 * only serializes, places the caret, and binds the shim.
 */
const BossModMentionDraft = (() => {
    function mentionName(node) {
        if (!node || node.nodeType !== 1) return '';
        if (node.classList && node.classList.contains('mention-pill')) {
            return String(node.getAttribute('data-agent-name') || '').trim();
        }
        if (node.classList && node.classList.contains('mention-host')) {
            const pill = node.querySelector && node.querySelector('.mention-pill');
            return mentionName(pill);
        }
        return '';
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
                out += `@${mentionName(node)}`;
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
            return distanceTo(root, range.startContainer, range.startOffset);
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
                count += 1 + mentionName(node).length;
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
                const len = 1 + mentionName(node).length;
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
     * Make a contenteditable field speak `.value` / caret like a textarea.
     * Setting `.value` paints live `@Name` tokens as pills; reading it
     * serializes them back. Tests pass `agents`; Chat relies on configure().
     *
     * @param {HTMLElement} el
     * @param {{agents?: object[], getAgents?: () => object[]}} [options]
     * @returns {HTMLElement}
     */
    function bindEditable(el, options) {
        if (!el) throw new Error('[mention-draft] bindEditable needs a field');
        const opts = options || {};
        function roster() {
            if (opts.agents) return opts.agents;
            if (opts.getAgents) return opts.getAgents();
            return BossModMentions.currentAgents();
        }
        Object.defineProperty(el, 'value', {
            configurable: true,
            enumerable: true,
            get() { return readEditable(el); },
            set(text) {
                const raw = String(text == null ? '' : text);
                if (typeof BossModMentionPills === 'undefined') {
                    el.replaceChildren();
                    if (raw) el.append(document.createTextNode(raw));
                    return;
                }
                BossModMentionPills.paintDraft(el, raw, { agents: roster(), editable: true });
            },
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
