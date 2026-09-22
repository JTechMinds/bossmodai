/**
 * BossMod AI — the `⋯` on a rail section header, and the panel behind it.
 *
 * Extracted from shell/thread-view-menu.js when the PEOPLE header grew the
 * same control. The button, whether its panel is open, and where that panel
 * hangs are one job; what goes INSIDE the panel is another, and it belongs to
 * whoever owns the choice being made there. Two copies of the first job is how
 * two `⋯`s on one rail would start to disagree about toggling, aria-expanded,
 * or what a press outside does.
 *
 * ONE GLYPH FOR A MENU, across the app. The Threads control was a gear for a
 * day and it was the third different mark for the same idea in one window —
 * `⋯` on the conversation header, sliders on the rail, the application gear in
 * the app header. The rule that replaced them: `⋯` opens a menu of things you
 * can do to the thing beside it, and a gear means application settings and
 * appears once.
 *
 * Using a control inside the panel does NOT close it. The control's own state
 * changing is the confirmation, and a menu that vanished as it answered would
 * take the answer with it — the same reason the conversation's receipts
 * preference stays put when it is toggled.
 */
const BossModRosterHeaderMenu = (() => {
    const { h } = BossModDom;

    /**
     * @param {unknown} value
     * @returns {boolean} Whether `value` is a string with something in it.
     */
    function isText(value) {
        return typeof value === 'string' && value.trim() !== '';
    }

    /**
     * Build a section header's `⋯` and the panel it toggles.
     *
     * @param {object} deps
     * @param {string} deps.id  The `⋯` button's id.
     * @param {string} deps.label  Accessible name, tooltip and panel label —
     *   one string, so what is announced, what hovers and what the panel is
     *   called cannot drift apart.
     * @param {string} deps.menuName  Written to the panel's `data-menu`;
     *   overlays.css sizes the panel by it.
     * @param {() => HTMLElement} deps.getContainer  What the panel is
     *   positioned against — the section header row. A THUNK, resolved at open
     *   time, because that row cannot be built until this button exists to go
     *   in it.
     * @param {HTMLElement[]} deps.items  Built ONCE by the caller and moved into
     *   the panel on each open, so a control keeps its state across opens.
     *   Rebuilding them per open would also swap a node under a pointer that
     *   is already on it.
     * @returns {{ button: HTMLElement, close: () => void, destroy: () => void }}
     * @throws {Error} When any dependency is missing, the wrong type, an empty
     *   string, or when `items` is empty — a `⋯` whose panel has nothing in it,
     *   nowhere to hang, or no name renders and then does nothing.
     */
    function createHeaderMenu(deps) {
        const { id, label, menuName, getContainer, items } = deps || {};
        if (!isText(id)) throw new Error('[roster-header-menu] deps.id is required');
        if (!isText(label)) throw new Error('[roster-header-menu] deps.label is required');
        if (!isText(menuName)) throw new Error('[roster-header-menu] deps.menuName is required');
        if (typeof getContainer !== 'function') {
            throw new Error('[roster-header-menu] deps.getContainer is required');
        }
        if (!Array.isArray(items) || items.length === 0
            || !items.every((item) => item && item.nodeType === 1)) {
            throw new Error('[roster-header-menu] deps.items must be a non-empty array of elements');
        }

        const button = h('button', {
            class: 'roster-section-action',
            id,
            type: 'button',
            'aria-label': label,
            'data-tooltip': label,
            // dialog, not menu: core/overlays.js's panel is a role="dialog" and
            // its children are ordinary controls rather than menuitems. Same
            // call the conversation chrome's `⋯` makes, for the same reason.
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));

        /** The open panel, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;

        /** @returns {void} */
        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the panel, or put it away again.
         *
         * The panel is core/overlays.js's — it already owns the focus trap,
         * Esc, the press-outside dismiss and returning focus to the `⋯`. A
         * second popover implementation is exactly the duplication the
         * primitives exist to remove.
         *
         * @returns {void}
         */
        function toggle() {
            if (menu) {
                close();
                return;
            }
            menu = BossModOverlays.createMenu({
                anchor: button,
                label,
                items,
                container: getContainer(),
                onClose: () => {
                    menu = null;
                    button.setAttribute('aria-expanded', 'false');
                },
            });
            // The rail is exactly as wide as the menu's own minimum, so the
            // panel takes the header row's width instead — keyed on the name
            // written here. See overlays.css.
            menu.element.setAttribute('data-menu', menuName);
            // A menu panel is DETACHED while it is closed, so the document
            // sweep that paints the rail at mount can never reach inside one —
            // which is the bug that rendered the conversation's Archive row as
            // a bare word. Neither panel on the rail carries a glyph today;
            // this is what keeps that from becoming true again the first time
            // one is added, and it costs nothing when there is nothing to paint.
            BossModIcons.paint(menu.element, 'roster-header-menu');
            button.setAttribute('aria-expanded', 'true');
        }

        return {
            button,
            close,

            /**
             * Put the panel away. A panel left open would outlive the rail it
             * hangs off, and its press-outside listener would outlive both.
             * @returns {void}
             */
            destroy() {
                close();
            },
        };
    }

    return { createHeaderMenu };
})();
