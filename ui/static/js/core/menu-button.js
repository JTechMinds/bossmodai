/**
 * BossMod AI — the `⋯`: the one menu trigger, and the panel lifecycle behind it.
 *
 * core/menu.js is the PANEL. A control that opens a panel is a different job —
 * the button, whether its panel is open, and where that panel hangs — and it
 * composes the panel rather than living inside it. What goes INSIDE the panel
 * is a third job, and it belongs to whoever owns the choice being made there,
 * which is why the items arrive through a thunk and never from here.
 *
 * ONE GLYPH FOR A MENU, across the app. The rail's Threads control was a gear
 * for a day and it was the third different mark for the same idea in one
 * window — `⋯` on the conversation header, sliders on the rail, the
 * application gear in the app header. The rule that replaced them: `⋯` opens
 * a menu of things you can do to the thing beside it, and a gear means
 * application settings and appears once. ONE LOOK FOR THE GLYPH, too: every
 * `⋯` is `button.menu-trigger` (controls.css), borderless and muted, in one of
 * two sizes. Seven hand-built copies, each with its own class, is how one of
 * them ended up a bordered button.
 *
 * Using a control inside the panel does NOT close it. The control's own state
 * changing is the confirmation, and a menu that vanished as it answered would
 * take the answer with it — the same reason the conversation's receipts
 * preference stays put when it is toggled. A row that takes the operator
 * somewhere else closes the panel itself, through `close()`.
 */
const BossModMenuButton = (() => {
    const { h } = BossModDom;

    /** `header`: a toolbar or modal-head `⋯`. `inline`: one in a list or rail row. */
    const SIZES = Object.freeze(['header', 'inline']);

    /**
     * @param {unknown} value
     * @returns {boolean} Whether `value` is a string with something in it.
     */
    function isText(value) {
        return typeof value === 'string' && value.trim() !== '';
    }

    /**
     * Build a `⋯` button: the single look, with no panel of its own.
     *
     * For a `⋯` whose click does something other than open a core/menu.js
     * panel — a modal, a host-owned menu — so it still looks and is named
     * like every other one.
     *
     * @param {object} options
     * @param {string} [options.id]  The button's DOM id.
     * @param {string} options.label  Accessible name and tooltip — one
     *   string, so what is announced and what hovers cannot drift apart.
     * @param {'header'|'inline'} options.size  Which of the two sizes.
     * @param {'dialog'|'menu'} [options.haspopup='dialog']  What the click
     *   opens. `dialog` by default: core/menu.js panels are `role="dialog"`,
     *   and so is a modal.
     * @param {string} [options.extraClass]  A layout hook for the host's own
     *   positioning rules. Never carries look; the look is `.menu-trigger`'s.
     * @param {(event: Event) => void} options.onClick
     * @returns {HTMLElement} `button.menu-trigger[data-size]`, collapsed
     *   (`aria-expanded="false"`), holding the Lucide `ellipsis` glyph.
     * @throws {Error} When `label` or `onClick` is missing, or `size` is not
     *   one of the two — a `⋯` with no name, no action or no size renders and
     *   then misleads.
     */
    function createTrigger({ id, label, size, haspopup = 'dialog', extraClass, onClick } = {}) {
        if (!isText(label)) throw new Error('[menu-button] a trigger needs a label');
        if (!SIZES.includes(size)) {
            throw new Error(`[menu-button] size must be "header" or "inline", not "${size}"`);
        }
        if (typeof onClick !== 'function') throw new Error('[menu-button] a trigger needs onClick');
        return h('button', {
            class: extraClass ? `menu-trigger ${extraClass}` : 'menu-trigger',
            id,
            type: 'button',
            'data-size': size,
            'aria-label': label,
            'data-tooltip': label,
            'aria-haspopup': haspopup,
            'aria-expanded': 'false',
            onclick: onClick,
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));
    }

    /**
     * Build a `⋯` and the core/menu.js panel it toggles.
     *
     * The panel is core/menu.js's — it already owns the focus trap, Esc, the
     * press-outside dismiss and returning focus to the `⋯`. A second popover
     * implementation is exactly the duplication the primitives exist to
     * remove.
     *
     * @param {object} deps
     * @param {string} [deps.id]  The `⋯` button's DOM id.
     * @param {string} deps.label  Accessible name, tooltip and panel label:
     *   one string.
     * @param {'header'|'inline'} deps.size  See createTrigger.
     * @param {string} [deps.extraClass]  See createTrigger.
     * @param {string} [deps.menuName]  Written to the panel's `data-menu`;
     *   overlays.css sizes and places some panels by it.
     * @param {() => HTMLElement} deps.getContainer  What the panel is
     *   positioned against. A THUNK, resolved at open time, because a host row
     *   is often built after the button that goes in it.
     * @param {() => HTMLElement[]} deps.getItems  The panel's content,
     *   resolved on EVERY open: a host with stable nodes returns the same ones
     *   (a control keeps its state across opens), and a host whose rows read
     *   live state builds them fresh.
     * @param {(panel: HTMLElement) => void} [deps.onOpen]  Called once the
     *   panel is attached and painted.
     * @returns {{ button: HTMLElement, close: () => void,
     *             openElement: () => (HTMLElement|null), destroy: () => void }}
     *   `close` is a no-op when closed. `openElement` is the open panel, or
     *   null. `destroy` closes it, and a later click opens nothing.
     * @throws {Error} When `label`, `getContainer` or `getItems` is missing,
     *   or as createTrigger does on a bad `size`.
     */
    function create(deps) {
        const {
            id, label, size, extraClass, menuName, getContainer, getItems, onOpen,
        } = deps || {};
        if (!isText(label)) throw new Error('[menu-button] deps.label is required');
        if (typeof getContainer !== 'function') throw new Error('[menu-button] deps.getContainer is required');
        if (typeof getItems !== 'function') throw new Error('[menu-button] deps.getItems is required');

        /** The open panel, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;
        /** Set by destroy(): the host is gone, so the `⋯` opens nothing more. */
        let destroyed = false;
        const button = createTrigger({ id, label, size, extraClass, onClick: () => toggle() });

        /** @returns {void} */
        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the panel, or put it away again.
         * @returns {void}
         */
        function toggle() {
            if (destroyed) return;
            if (menu) {
                close();
                return;
            }
            menu = BossModMenu.createMenu({
                anchor: button,
                label,
                items: getItems(),
                container: getContainer(),
                onClose: () => {
                    menu = null;
                    button.setAttribute('aria-expanded', 'false');
                },
            });
            if (menuName) menu.element.setAttribute('data-menu', menuName);
            // A menu panel is DETACHED while it is closed, so the document
            // sweep that paints a surface at mount can never reach inside one
            // — which is the bug that rendered the conversation's Archive row
            // as a bare word. Painted here, as it opens; the painter is
            // idempotent, so a panel with nothing to paint costs nothing.
            BossModIcons.paint(menu.element, 'menu-button');
            button.setAttribute('aria-expanded', 'true');
            if (onOpen) onOpen(menu.element);
        }

        return {
            button,
            close,
            openElement: () => (menu ? menu.element : null),

            /**
             * Put the panel away. A panel left open would outlive the host it
             * hangs off, and its press-outside listener would outlive both.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                close();
            },
        };
    }

    return { createTrigger, create };
})();
