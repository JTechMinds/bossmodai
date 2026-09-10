/**
 * BossMod AI — the one icon painter.
 *
 * Swaps `<i data-lucide="chevron-right">` placeholders for their SVG, scoped
 * to a root the caller names, once per placeholder.
 *
 * WHY THIS EXISTS — read before "simplifying" it back to `lucide.createIcons`:
 *
 *   1. `createIcons` has no `nodes` option. Its whole signature is
 *      `{ icons, nameAttr = "data-lucide", attrs = {} }` (vendored 0.469;
 *      the string "nodes" does not appear in the bundle). Nine call sites
 *      passed `{ nodes: [el] }` believing the paint was scoped. It was not —
 *      the argument was destructured into nothing and dropped.
 *   2. It scans `document.querySelectorAll('[data-lucide]')`, and the SVG it
 *      builds copies the source element's attributes forward — `data-lucide`
 *      included. So a painted icon still matches the selector, and EVERY call
 *      replaced EVERY icon node in the document. Opening the add-agent menu
 *      rebuilt the header, the roster and the transcript's glyphs with it.
 *
 * Both are fixed here by construction: the query runs against the caller's
 * root, and the painted SVG does not carry `data-lucide`, so a second pass
 * over the same tree finds nothing to do and the nodes from the first pass
 * survive — which is what the Advanced disclosure's `#advanced-chevron`
 * (looked up and rotated after painting) depends on.
 *
 * The geometry is NOT re-implemented: paths come from `lucide.icons` and the
 * SVG is built by `lucide.createElement`. This module owns the scope, the
 * idempotency and the failure modes, and nothing else.
 *
 * No `BossModDom.h` here on purpose — h() builds HTML elements through
 * `document.createElement`, and an SVG built in the HTML namespace does not
 * render. The vendor's `createElement` uses `createElementNS`, so it is the
 * authority for these nodes.
 */
const BossModIcons = (() => {

    /** The attribute a placeholder carries, and the selector that finds one. */
    const NAME_ATTR = 'data-lucide';
    const PLACEHOLDER = `[${NAME_ATTR}]`;

    /**
     * Kebab icon name to the PascalCase key `lucide.icons` is keyed by.
     *
     * Vendor parity, deliberately: this is the vendor's own `_$` helper,
     * regex included. Matching its behaviour matters at the edges — digits
     * ride along with the word before them (`bar-chart-3` -> `BarChart3`,
     * `trash-2` -> `Trash2`), a one-letter name is still capitalised
     * (`x` -> `X`), and the tail of every word is lowercased, so a name
     * already written in Pascal would come back mangled rather than working
     * by accident. Guessing at those rules instead of copying them is how a
     * painter drifts from the icon set it is painting.
     *
     * @param {string} name  The `data-lucide` value, e.g. "octagon-x".
     * @returns {string} The key into `lucide.icons`, e.g. "OctagonX".
     */
    function iconKey(name) {
        return String(name).replace(
            /(\w)(\w*)(_|-|\s*)/g,
            (_match, head, tail) => head.toUpperCase() + tail.toLowerCase(),
        );
    }

    /**
     * The vendored lucide bundle, or a thrown error naming the call site.
     *
     * `window.lucide` is a vendored file loaded by index.html ahead of every
     * module that paints, so its absence is a broken build — not a runtime
     * state to degrade around. Degrading would leave bare `<i>` placeholders
     * on screen, which read as a CSS bug and send the next person to the
     * stylesheet; throwing names the real cause once, at the first paint.
     *
     * @param {string} context  Call site, for the message.
     * @returns {{icons: object, createElement: Function}}
     * @throws {Error} When the bundle is missing or is not the expected shape.
     */
    function vendor(context) {
        const lucide = typeof window === 'undefined' ? undefined : window.lucide;
        if (!lucide || !lucide.icons || typeof lucide.createElement !== 'function') {
            throw new Error(
                `[icons] ${context}: window.lucide is missing or incomplete. `
                + 'The vendored bundle (js/vendor/lucide.min.js) failed to load — '
                + 'this is a broken build, not a state to paint around.',
            );
        }
        return lucide;
    }

    /**
     * An element's attributes as a plain object, in document order.
     *
     * @param {Element} el
     * @returns {Object<string, string>}
     */
    function attributesOf(el) {
        return Array.from(el.attributes).reduce((out, attr) => {
            out[attr.name] = attr.value;
            return out;
        }, {});
    }

    /**
     * Compose one class attribute: lucide's own two, then the source's.
     *
     * Same output as the vendor's replace — `lucide lucide-<name>` in front of
     * whatever the placeholder carried — because the stylesheet and the sizing
     * utilities on the placeholder (`w-4 h-4 text-bm-muted`) both have to
     * survive the swap.
     *
     * @param {Array<string|undefined>} parts
     * @returns {string} Deduplicated, space-joined.
     */
    function composeClass(parts) {
        const seen = [];
        for (const part of parts) {
            for (const name of String(part || '').split(/\s+/)) {
                if (name && !seen.includes(name)) seen.push(name);
            }
        }
        return seen.join(' ');
    }

    /**
     * Build the SVG that replaces one placeholder.
     *
     * @param {Element} el  The placeholder.
     * @param {string} name  Its `data-lucide` value.
     * @param {Array} definition  `lucide.icons[key]`: [tag, attrs, children].
     * @param {object} lucide  The vendored bundle.
     * @returns {Element} A detached SVG element.
     */
    function buildSvg(el, name, definition, lucide) {
        const [tag, baseAttrs, children] = definition;
        const source = attributesOf(el);
        const attrs = { ...baseAttrs, ...source };
        // THE fix. Lucide carries `data-lucide` onto the SVG, which is what
        // makes its own paint re-paint everything it has already painted.
        // Without this line every guarantee in this module's docstring is a
        // lie, and painting a tree twice rebuilds it.
        delete attrs[NAME_ATTR];
        const composed = composeClass(['lucide', `lucide-${name}`, source.class]);
        if (composed) attrs.class = composed;
        return lucide.createElement([tag, attrs, children]);
    }

    /**
     * Find the placeholders under a root, including the root itself.
     *
     * A caller that hands over the placeholder directly means it, so a root
     * that carries the attribute counts — otherwise `paint(icon)` would be a
     * silent no-op, which is the failure mode this module exists to end.
     *
     * @param {Element} root
     * @returns {Element[]}
     */
    function placeholdersIn(root) {
        const found = Array.from(root.querySelectorAll(PLACEHOLDER));
        const self = typeof root.matches === 'function' && root.matches(PLACEHOLDER);
        return self ? [root, ...found] : found;
    }

    /**
     * Paint every lucide placeholder inside one root.
     *
     * Scoped: nothing outside `root` is read or replaced. Idempotent: a tree
     * that has already been painted has no placeholders left, so a second call
     * replaces nothing and every node from the first pass — with its id, its
     * classes and any listener bound to it — is still there.
     *
     * Resolution happens before the first replacement, so an unrecognised name
     * leaves the tree exactly as it found it rather than half-painted.
     *
     * @param {Element} root  The subtree to paint.
     * @param {string} context  Who is painting, e.g. "add-agent-menu". Appears
     *   in every error this call can raise; it is the only thing that turns
     *   "unknown icon" into a fixable report.
     * @returns {number} How many placeholders were replaced.
     * @throws {Error} When `root` is not an element, `context` is missing, the
     *   lucide bundle is unavailable, an icon name is not in the icon set, or
     *   a placeholder has no parent to be replaced in.
     */
    function paint(root, context) {
        if (typeof context !== 'string' || !context) {
            throw new Error('[icons] paint: a context naming the call site is required');
        }
        if (!root || typeof root.querySelectorAll !== 'function') {
            throw new Error(`[icons] ${context}: paint needs an element, got ${root}`);
        }
        const lucide = vendor(context);

        const resolved = [];
        const unknown = [];
        for (const el of placeholdersIn(root)) {
            const name = el.getAttribute(NAME_ATTR);
            const key = iconKey(name);
            if (!Object.prototype.hasOwnProperty.call(lucide.icons, key)) {
                unknown.push(`"${name}" (looked up as "${key}")`);
                continue;
            }
            resolved.push({ el, name, definition: lucide.icons[key] });
        }
        if (unknown.length) {
            throw new Error(
                `[icons] ${context}: no such lucide icon: ${unknown.join(', ')}. `
                + 'Fix the name at the call site — an icon that does not exist '
                + 'would otherwise leave an empty placeholder on screen.',
            );
        }

        for (const { el, name, definition } of resolved) {
            const parent = el.parentNode;
            if (!parent) {
                throw new Error(
                    `[icons] ${context}: the "${name}" placeholder has no parent, `
                    + 'so it cannot be replaced. Paint after the node is attached.',
                );
            }
            parent.replaceChild(buildSvg(el, name, definition, lucide), el);
        }
        return resolved.length;
    }

    /**
     * Paint every unpainted placeholder in the document.
     *
     * For the surfaces that render placeholders they do not own: the composer
     * builds `<i data-lucide>` and never paints, and the shell's sweeps are
     * what put its glyphs on screen. Narrowing those callers to their own
     * subtree would silently drop icons somebody else built, so the sweep is
     * kept and made cheap instead — after the first pass there is nothing left
     * to match, which is exactly what the vendor's own sweep could not say.
     *
     * @param {string} context  Who is sweeping. See `paint`.
     * @returns {number} How many placeholders were replaced.
     * @throws {Error} As `paint`.
     */
    function paintDocument(context) {
        return paint(document.body, context);
    }

    return { paint, paintDocument, iconKey };
})();
