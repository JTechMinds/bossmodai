/**
 * BossMod AI — the desk head's `⋯`: the agent menu's rows, painted in the
 * desk's frame.
 *
 * Split from context/desk-panel.js, which composes the desk's body. The rows
 * are not this module's: they are context/agent-menu.js's, the one
 * definition the DM header's `⋯` paints too, so the two menus cannot drift.
 * This renders that descriptor vocabulary with the conversation `⋯`'s
 * section order and words (conversation/chrome-menu.js `SECTIONS`, `LABELS`)
 * and its switch node (conversation/chrome-switch.js), so a row reads,
 * announces and describes itself the same in both. The `⋯` and its panel are
 * core/menu-button.js's.
 *
 * Not conversation/chrome.js's renderer, deliberately: the header keeps
 * stable nodes, reuses them across rows and the menu, and gates every action
 * through one in-flight gate, none of which a panel rebuilt on each open
 * needs. So this one is small: sections in order, actions above switches,
 * each picked row closing the panel first and its failure said on the
 * desk's Details error line.
 */
const BossModDeskMenu = (() => {
    const { h } = BossModDom;

    /** The `⋯`'s accessible name, tooltip and panel label — the chat's word. */
    const MENU_LABEL = 'More actions';

    /**
     * Build the desk head's `⋯` and the sectioned panel behind it.
     *
     * @param {object} deps
     * @param {() => {menuTitle: string, actions: object[]}} deps.getMenu  The
     *   agent menu's rows now (context/agent-menu.js `actions()`; its Yolo badge
     *   is not the head's). Called on each open, so every row's state — Edit
     *   role's roster row, the switches' flags — is read at that moment.
     * @param {(message: string) => void} deps.onError  Where a row's failure
     *   is said: the desk's Details error line.
     * @returns {{ button: HTMLElement, close: () => void, destroy: () => void }}
     *   `button` is the head's one tool.
     * @throws {Error} When a dependency is missing — a menu with no rows, or
     *   rows whose failures go nowhere, is worse than no menu.
     */
    function create(deps) {
        const { getMenu, onError } = deps || {};
        if (typeof getMenu !== 'function') throw new Error('[desk-menu] deps.getMenu is required');
        if (typeof onError !== 'function') throw new Error('[desk-menu] deps.onError is required');

        /**
         * Run one picked row and say its failure on the desk.
         * @param {string} label
         * @param {() => Promise<void>|void} work
         * @returns {Promise<void>} Never rejects; a failure goes to `onError`.
         */
        async function run(label, work) {
            try {
                await work();
            } catch (err) {
                console.error(`[desk-menu] the "${label}" row failed`, err);
                onError((err && err.message) || `${label} failed.`);
            }
        }

        /**
         * An action row. Picking it closes the panel first, so focus is back
         * on the `⋯` and a confirm layer returns there.
         * @param {object} action  A button descriptor from the agent menu.
         * @returns {HTMLElement}
         */
        function actionRow(action) {
            return h('button', {
                class: 'menu-action',
                id: action.id,
                type: 'button',
                'data-tone': action.tone || null,
                disabled: action.disabled === true,
                onclick: () => { menu.close(); void run(action.label, () => action.onSelect()); },
            }, action.icon ? h('i', { 'data-lucide': action.icon, 'aria-hidden': 'true' }) : null, action.label);
        }

        /**
         * A setting row: the chat's switch node, hint and description wiring
         * included. Built per open, so a cancelled confirm or a failed save
         * shows the true state the next time.
         * @param {object} action  A `kind: 'switch'` descriptor.
         * @returns {HTMLElement}
         */
        function switchRow(action) {
            const node = BossModChromeSwitch.create({
                id: action.id,
                label: action.label,
                onToggle: (pressed) => { menu.close(); void run(action.label, () => action.onSelect(pressed)); },
            });
            BossModChromeSwitch.sync(node, action);
            node._row.disabled = action.disabled === true;
            return node;
        }

        /**
         * The panel's content, built on each open from the agent menu's rows.
         * @returns {HTMLElement[]}
         * @throws {Error} On a row whose section the panel has no place for.
         */
        function getItems() {
            const { menuTitle, actions } = getMenu();
            const bySection = new Map(BossModChromeMenu.SECTIONS.map((key) => [key, []]));
            actions.forEach((action) => {
                if (!bySection.has(action.section)) {
                    throw new Error(`[desk-menu] row "${action.id}" names an unknown section "${action.section}"`);
                }
                bySection.get(action.section).push(action);
            });
            const parts = [];
            bySection.forEach((rows, key) => {
                if (!rows.length) return;
                if (parts.length) parts.push(h('hr', { class: 'menu-divider' }));
                // The chat's two bodies: the doing first, then the settings.
                parts.push(BossModMenu.createMenuSection({
                    id: `desk-menu-${key}`,
                    label: key === 'subject' ? menuTitle : BossModChromeMenu.LABELS[key],
                    children: [
                        h('div', { class: 'menu-actions' },
                            rows.filter((action) => action.kind !== 'switch').map(actionRow)),
                        h('div', { class: 'menu-actions' },
                            rows.filter((action) => action.kind === 'switch').map(switchRow)),
                    ],
                }));
            });
            return [h('div', { class: 'menu-sections' }, parts)];
        }

        const menu = BossModMenuButton.create({
            id: 'desk-options',
            label: MENU_LABEL,
            size: 'header',
            // The desk's head is the frame's; the panel hangs off it.
            getContainer: () => menu.button.closest('.modal-head'),
            getItems,
        });

        return { button: menu.button, close: menu.close, destroy: menu.destroy };
    }

    return { create };
})();
