/**
 * BossMod AI — DOM construction helpers.
 *
 * Deliberately tiny: this codebase has no build step and no framework.
 * Every module builds nodes through h() so attribute, listener, and child
 * handling is consistent in one place.
 */
const BossModDom = (() => {

    /**
     * Build an element.
     *
     * @param {string} tag
     * @param {object|null} attrs  Keys starting with "on" bind listeners
     *   (onclick -> "click"). Values of null, undefined, or false skip the
     *   attribute entirely. A value of true renders as an empty attribute.
     * @param {...(Node|string|Array|null|false)} children  Flattened one level;
     *   null and false are dropped; primitives become text nodes.
     * @returns {HTMLElement}
     */
    function h(tag, attrs, ...children) {
        const el = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs || {})) {
            if (value === null || value === undefined || value === false) continue;
            if (key.startsWith('on') && typeof value === 'function') {
                el.addEventListener(key.slice(2).toLowerCase(), value);
            } else {
                el.setAttribute(key, value === true ? '' : String(value));
            }
        }
        appendChildren(el, children);
        return el;
    }

    function appendChildren(el, children) {
        for (const child of children.flat()) {
            if (child === null || child === undefined || child === false) continue;
            el.append(child.nodeType ? child : document.createTextNode(String(child)));
        }
    }

    /**
     * Remove every child of an element.
     * @param {HTMLElement} el
     */
    function clear(el) {
        el.replaceChildren();
    }

    /**
     * How close to an edge still counts as "already there" (spec 4.2). One
     * threshold, so the transcript and the Log agree about what reading the
     * newest means.
     */
    const STICK_THRESHOLD_PX = 80;

    /**
     * Is this scroller already at the edge that new content arrives at?
     *
     * The transcript appends at the bottom and the Log prepends at the top, so
     * they watch opposite edges — but the rule is one rule: never move the
     * viewport of an operator who has scrolled away to read something.
     *
     * @param {HTMLElement} el
     * @param {'top'|'bottom'} edge
     * @param {number} [threshold=STICK_THRESHOLD_PX]
     * @returns {boolean}
     * @throws {Error} On an edge that is neither, rather than silently
     *   answering "yes" and yanking the view.
     */
    function isNearEdge(el, edge, threshold) {
        const slack = threshold === undefined ? STICK_THRESHOLD_PX : threshold;
        if (edge === 'top') return el.scrollTop <= slack;
        if (edge === 'bottom') {
            return el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
        }
        throw new Error(`[dom] isNearEdge: unknown edge "${edge}"`);
    }

    /**
     * Bind one delegated listener on a root element.
     *
     * Returns a disposer. Callers MUST keep it and call it on unmount —
     * an undisposed listener is the main leak vector in this shell.
     *
     * @param {HTMLElement} root
     * @param {string} selector
     * @param {string} eventName
     * @param {(event: Event, target: HTMLElement) => void} handler
     * @returns {() => void} disposer
     */
    function delegate(root, selector, eventName, handler) {
        const listener = (event) => {
            const target = event.target.closest ? event.target.closest(selector) : null;
            if (target && root.contains(target)) handler(event, target);
        };
        root.addEventListener(eventName, listener);
        return () => root.removeEventListener(eventName, listener);
    }

    /**
     * Make `parent`'s children exactly `nodes`, in order, moving only what is
     * out of place. A node already where it belongs is not touched, so a list
     * that did not change costs no DOM mutation at all.
     *
     * @param {HTMLElement} parent
     * @param {Node[]} nodes  The wanted children; each may already be attached
     *   anywhere, and is moved here.
     * @returns {void}
     */
    function syncChildren(parent, nodes) {
        nodes.forEach((node, index) => {
            const at = parent.childNodes[index];
            if (at !== node) parent.insertBefore(node, at || null);
        });
        while (parent.childNodes.length > nodes.length) {
            parent.childNodes[parent.childNodes.length - 1].remove();
        }
    }

    /**
     * A list of rows keyed by id that is patched rather than rebuilt.
     *
     * A row is reused — the same node, with its listeners and focus — while its
     * signature holds the same values; otherwise it is built afresh. Rows whose
     * key is gone are removed. This is what keeps a live update that changes
     * one agent from replacing every row on screen.
     *
     * @param {HTMLElement} parent  The element the rows are children of. The
     *   list owns its children: anything else in it is removed on `sync`.
     * @returns {{
     *   sync: (items: object[], keyOf: (item: object) => string,
     *          signatureOf: (item: object) => any[],
     *          build: (item: object) => HTMLElement) => HTMLElement[],
     *   reset: () => void
     * }} `sync` returns the rows it BUILT this pass (not the reused ones), so
     *   the caller can do first-paint work — icons — on those alone. `reset`
     *   empties the parent and forgets every row, for a non-list state such as
     *   a skeleton or an empty message.
     * @throws {Error} From `sync` when two items share a key: two rows for one
     *   key would make the reuse ambiguous.
     */
    function createKeyedList(parent) {
        let rows = new Map();
        const same = (a, b) => a.length === b.length && a.every((value, i) => value === b[i]);

        function sync(items, keyOf, signatureOf, build) {
            const next = new Map();
            const built = [];
            const nodes = items.map((item) => {
                const key = keyOf(item);
                if (next.has(key)) throw new Error(`[dom] keyed list: duplicate key "${key}"`);
                const signature = signatureOf(item);
                const had = rows.get(key);
                const row = had && same(had.signature, signature)
                    ? had
                    : { signature, node: build(item) };
                if (row !== had) built.push(row.node);
                next.set(key, row);
                return row.node;
            });
            rows = next;
            syncChildren(parent, nodes);
            return built;
        }

        function reset() {
            rows = new Map();
            clear(parent);
        }

        return { sync, reset };
    }

    return { h, clear, delegate, isNearEdge, syncChildren, createKeyedList, STICK_THRESHOLD_PX };
})();
