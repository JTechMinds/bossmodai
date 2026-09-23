/**
 * BossMod AI — the conversation header's `⋯`: its button and the panel behind it.
 *
 * THE SEAM. conversation/chrome.js decides WHAT goes behind the `⋯` — it paints
 * the source's `slot: 'menu'` actions into `actionsEl` on every apply(), and
 * decides whether the button is shown at all. This module owns HOW the panel
 * lives: the button's name and expanded state, opening and closing through the
 * one overlay implementation, and painting glyphs inside a panel the document
 * sweep cannot reach. The chrome never touches the panel; this module never
 * reads a descriptor.
 *
 * The menu's actions are appended into a STABLE panel node owned here rather
 * than handed to the menu at open time, which is what lets a repaint that
 * happens while the panel is open — Archive succeeding and becoming Reopen —
 * land inside the panel the operator is looking at.
 */
const BossModChromeMenu = (() => {
    const { h } = BossModDom;

    /** The `⋯`'s accessible name and its tooltip: one string, never two. */
    const MENU_LABEL = 'More actions';

    /**
     * Build the `⋯` and the lifecycle of the panel it opens.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.container  The positioned header the panel
     *   hangs off. The panel is placed relative to it, and a panel hung off an
     *   unpositioned ancestor lands below the viewport.
     * @param {HTMLElement[]} [deps.viewOptions]  The surface's preference
     *   controls, moved into the panel after the actions each time it opens.
     * @returns {{ button: HTMLElement, actionsEl: HTMLElement,
     *             close: () => void, paint: () => void }}
     *   `button` is the `⋯` the chrome places in its row; `actionsEl` is the
     *   stable node the chrome fills with menu actions; `close` puts an open
     *   panel away (a no-op when closed); `paint` paints glyphs inside an open
     *   panel (a no-op when closed).
     * @throws {Error} When `container` is missing — the panel would have no
     *   positioned host to hang off.
     */
    function createChromeMenu(deps) {
        const container = deps && deps.container;
        if (!container) throw new Error('[chrome-menu] deps.container is required');
        const viewOptions = (deps && deps.viewOptions) || [];

        /** The open menu, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;
        /**
         * The menu ACTIONS' parent, owned here and reused forever.
         *
         * Built once and never replaced, for the same reason the view options
         * are the caller's nodes rather than rebuilt on open: it may be inside
         * an open panel when apply() runs, and refilling a stable node is what
         * keeps a live chrome swap visible to whoever is looking at it. While
         * the panel is closed this is simply detached, holding its buttons.
         */
        const actionsEl = h('div', { class: 'menu-actions' });
        const button = h('button', {
            class: 'btn btn-sm conversation-action conversation-view-options',
            type: 'button',
            id: 'conversation-view-options',
            'aria-label': MENU_LABEL,
            'data-tooltip': MENU_LABEL,
            // dialog, not menu: the panel holds a role="switch", which is
            // not a menuitem and must not be announced as one.
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));

        /** @returns {void} */
        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * THE PANEL IS THE ONE TREE THE SWEEP CANNOT REACH. The chrome's apply()
         * ends on BossModIcons.paintDocument, which walks document.body — and
         * while the menu is closed `actionsEl` hangs off a detached node, so an
         * action built in there kept its bare `<i>` placeholder and rendered as
         * a bare word. Archive read as a stray heading in the panel because of
         * exactly this. The painter is idempotent, so painting an open panel
         * that has nothing left to paint costs nothing.
         *
         * @returns {void}
         */
        function paint() {
            if (menu) BossModIcons.paint(menu.element, 'conversation-chrome.menu');
        }

        /**
         * Show the panel, or put it away again.
         *
         * The panel is core/overlays.js's — it already owns the focus trap, Esc,
         * and returning focus to the control that opened it. A second popover
         * implementation is exactly the duplication the primitives exist to
         * remove.
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
                label: MENU_LABEL,
                // Actions first, then preferences: one is a thing to do and
                // the other is a thing to set, and the doing comes first.
                items: [actionsEl].concat(viewOptions),
                container,
                onClose: () => {
                    menu = null;
                    button.setAttribute('aria-expanded', 'false');
                },
            });
            // Paint what was just attached; see paint().
            paint();
            button.setAttribute('aria-expanded', 'true');
        }

        return { button, actionsEl, close, paint };
    }

    return { createChromeMenu };
})();
