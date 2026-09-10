/**
 * BossMod AI — the rail's Add agent menu.
 *
 * The `+ Add agent` row used to open the create dialog. It opens two doors
 * now, because the dialog stopped being the only one: templates are installed
 * from the marketplace and only then offered by the create form's picker, so
 * "find an agent to start from" and "author one" are separate errands and the
 * row must not silently pick one.
 *
 * Click-triggered and toggling, following conversation/chrome.js's `⋯`. A
 * hover-only menu is unreachable by keyboard and by touch, and the panel
 * itself is core/overlays.js's — one focus trap, Esc, and focus returned to
 * the row — rather than a second popover implementation.
 *
 * Both doors read the same way: a lucide icon, then what it opens. The pair
 * used to be `Browse Marketplace →` and `+ Add Agent` — one trailing glyph,
 * one leading one, two different ideas of where a mark goes and neither of
 * them the shell's own icon system.
 */
const BossModAddAgentMenu = (() => {
    const { h } = BossModDom;

    /**
     * Bind the roster's Add agent row to its menu.
     *
     * Marks the row as a menu button at bind time rather than on first use: an
     * anchor that grows `aria-haspopup` when it is first clicked has already
     * lied to every operator who read it with a screen reader.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.anchor     The row. Focus returns here, and
     *   the panel hangs off it.
     * @param {HTMLElement} deps.container  What the panel is positioned in.
     * @param {object} deps.store           Handed to the create dialog, which
     *   writes the new agent into it.
     * @returns {{toggle: () => void}} `toggle` is what the roster's onHire
     *   calls: it opens the menu, or puts away the one already open.
     * @throws {Error} When any of the three is missing. A menu with no anchor
     *   is a keyboard dead end, and one with no store would open a dialog that
     *   saves an agent the operator is never shown.
     */
    function createAddAgentMenu(deps) {
        const { anchor, container, store } = deps || {};
        if (!anchor) throw new Error('[add-agent-menu] deps.anchor is required');
        if (!container) throw new Error('[add-agent-menu] deps.container is required');
        if (!store) throw new Error('[add-agent-menu] deps.store is required');

        anchor.setAttribute('aria-haspopup', 'dialog');
        anchor.setAttribute('aria-expanded', 'false');
        let menu = null;

        /**
         * One door: put the panel away, then open what it names.
         *
         * An icon in front and a label after it, the same shape `+ Add agent`
         * has on the row this hangs off. The icon is a lucide placeholder that
         * `BossModIcons.paint` swaps for an SVG once the panel is in the
         * document — the shell's one icon mechanism, not a glyph typed into
         * the label — and it is DECORATIVE: the accessible name is the text
         * beside it, so the row still reads correctly to a screen reader.
         *
         * @param {string} icon   A lucide icon name.
         * @param {string} label  What the row says, and its accessible name.
         * @param {Function} open What to open once the panel is away.
         * @returns {HTMLElement}
         */
        function door(icon, label, open) {
            return h('button', {
                class: 'add-agent-choice', type: 'button',
                // Closed FIRST, on purpose: close() puts focus back on the
                // row, so the dialog or takeover that follows captures the row
                // as the thing to return focus to when it closes in turn.
                onclick: () => { menu.close(); open(); },
            },
            h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }),
            h('span', {}, label));
        }

        function toggle() {
            if (menu) {
                menu.close();
                return;
            }
            menu = BossModOverlays.createMenu({
                anchor,
                label: 'Add agent',
                items: [
                    // `blocks`, never `building`: shell/places.js already
                    // spends that mark on the Office, and two doors in one
                    // shell wearing the same icon is an icon that identifies
                    // neither of them.
                    door('blocks', 'Agent Marketplace', () => BossModMarketplace.open()),
                    // A drawn `plus`, not the literal `+` this used to type
                    // into the label: the two rows have to line their labels
                    // up on one left edge, and a character has the metrics of
                    // whatever font renders it while an icon has the icon's.
                    door('plus', 'Add Agent',
                        () => BossModAgentEdit.openAgentModal({ store })),
                ],
                container,
                onClose: () => {
                    menu = null;
                    anchor.setAttribute('aria-expanded', 'false');
                },
            });
            // Read only by the stylesheet. The rail clips its own overflow and
            // this row sits at the foot of it, so the panel is placed against
            // the viewport instead of under its container the way the
            // conversation's menu is.
            menu.element.setAttribute('data-menu', 'add-agent');
            // createMenu has already appended the panel, so both placeholders
            // are in the document and can be swapped for their SVGs. Scoped to
            // the panel for real: this used to hand lucide a `nodes` option it
            // does not have, which meant opening the menu rebuilt every icon
            // in the shell rather than the two on the panel.
            BossModIcons.paint(menu.element, 'add-agent-menu');
            anchor.setAttribute('aria-expanded', 'true');
        }

        return { toggle };
    }

    return { createAddAgentMenu };
})();
