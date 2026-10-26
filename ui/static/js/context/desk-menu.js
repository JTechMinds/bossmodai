/**
 * BossMod AI — the desk head's `⋯`: which doors an agent's desk offers.
 *
 * Split from context/desk-panel.js, which composes the desk's body: what the
 * head's one menu holds is a separate job from laying out the sections under
 * it. It owns the rows and their order and nothing else — every row's action
 * is an injected callback, so this never learns how a chat opens or how a
 * runtime is reset — and the `⋯` and its panel are core/menu-button.js's.
 *
 * THREE SECTIONS, each a core/menu.js `createMenuSection`, the conversation
 * panel's shape (`.menu-sections`): **Agent** — the agent's own doors (Open
 * chat, Memory, Edit role) — then a divider and **Manage** — the operational
 * ones (Diagnostics, Reset runtime, Remove agent) — then a divider and
 * **Advanced** — the Yolo mode switch, a setting that removes safeguards,
 * kept apart from everyday ones. Destructive actions belong behind a menu and
 * a confirm, not at the bottom of the reading flow.
 */
const BossModDeskMenu = (() => {
    const { h } = BossModDom;

    /** The `⋯`'s accessible name, tooltip and panel label: one string. */
    const MENU_LABEL = 'Desk options';
    /** The section headings, and the rows' labels: one string each. */
    const LABELS = Object.freeze({
        agent: 'Agent',
        manage: 'Manage',
        advanced: 'Advanced',
        chat: 'Open chat',
        memory: 'Memory',
        edit: 'Edit role',
        diagnostics: 'Diagnostics',
        reset: 'Reset runtime',
        remove: 'Remove agent',
    });

    /**
     * A `⋯` row: glyph and label.
     *
     * @param {string} id
     * @param {string} icon  Lucide glyph name.
     * @param {string} label
     * @param {() => void} onclick
     * @param {{danger?: boolean, disabled?: boolean}} [state]  `danger` marks
     *   a destructive row; `disabled` withholds one that cannot act yet.
     * @returns {HTMLElement}
     */
    function menuRow(id, icon, label, onclick, { danger = false, disabled = false } = {}) {
        return h('button', {
            class: 'menu-action', id, type: 'button', 'data-tone': danger ? 'danger' : null, disabled, onclick,
        }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }), label);
    }

    /**
     * Build the desk head's `⋯` and the sectioned panel behind it.
     *
     * @param {object} deps
     * @param {() => boolean} deps.canEdit  Whether Edit role may act: a role
     *   form needs the roster row it edits. Read on each open.
     * @param {() => void} deps.onChat  Open this agent's conversation.
     * @param {() => void} deps.onMemory  Open the Memory layer.
     * @param {() => void} deps.onEdit  Open the role form.
     * @param {() => void} deps.onDiagnostics  Leave for this agent's log.
     * @param {() => void} deps.onReset  Ask to reset the agent's runtime.
     * @param {() => void} deps.onRemove  Ask to remove the agent.
     * @param {() => boolean|null} deps.yoloEnabled  The agent's Yolo flag, or
     *   null while its detail has not loaded. Read on each open.
     * @param {(enabled: boolean) => void} deps.onYolo  Turn Yolo mode on
     *   (confirmed by the callee) or off.
     * @returns {{ button: HTMLElement, close: () => void, destroy: () => void }}
     *   `button` is the head's one tool.
     * @throws {Error} When any callback is missing — a row that does nothing
     *   when picked is worse than no row.
     */
    function create(deps) {
        const {
            canEdit, onChat, onMemory, onEdit, onDiagnostics, onReset, onRemove, yoloEnabled, onYolo,
        } = deps || {};
        const required = {
            canEdit, onChat, onMemory, onEdit, onDiagnostics, onReset, onRemove, yoloEnabled, onYolo,
        };
        Object.entries(required).forEach(([name, fn]) => {
            if (typeof fn !== 'function') throw new Error(`[desk-menu] deps.${name} is required`);
        });

        /**
         * Picking a row closes the panel first, so focus is back on the `⋯`
         * and a confirmation layer returns there.
         * @param {() => void} run
         * @returns {() => void}
         */
        const pick = (run) => () => { menu.close(); run(); };

        /**
         * The Advanced section's one row: the Yolo switch, with its hint as a
         * visible line that is also its accessible description.
         *
         * Withheld until the agent's detail loads, the same rule as Edit role.
         * Picking closes the panel first, so the confirm returns focus to the
         * `⋯`. Rebuilt on every open, so a cancelled confirm or a failed save
         * shows the true state the next time.
         * @returns {HTMLElement}
         */
        function yoloRow() {
            const state = yoloEnabled();
            const control = BossModSwitch.create({
                label: BossModYoloMode.LABEL,
                pressed: state === true,
                onChange: (pressed) => { menu.close(); onYolo(pressed); },
            });
            const row = control.element;
            row.id = 'desk-yolo';
            row.disabled = state === null;
            row.setAttribute('aria-describedby', 'desk-yolo-hint');
            return h('div', { class: 'menu-switch' }, row,
                h('p', { class: 'menu-switch-hint', id: 'desk-yolo-hint' }, BossModYoloMode.HINTS.agent));
        }

        /**
         * The panel's content, built on each open: Edit role's and Yolo
         * mode's states are read at that moment.
         * @returns {HTMLElement[]}
         */
        function getItems() {
            return [h('div', { class: 'menu-sections' },
                BossModMenu.createMenuSection({
                    id: 'desk-menu-agent',
                    label: LABELS.agent,
                    children: [h('div', { class: 'menu-actions' },
                        menuRow('desk-chat', 'message-circle', LABELS.chat, pick(onChat)),
                        menuRow('desk-memory', 'brain', LABELS.memory, pick(onMemory)),
                        // Withheld rather than live and doing nothing until
                        // the roster has the row the form would edit.
                        menuRow('desk-edit', 'pencil', LABELS.edit, pick(onEdit), { disabled: !canEdit() }))],
                }),
                h('hr', { class: 'menu-divider' }),
                BossModMenu.createMenuSection({
                    id: 'desk-menu-manage',
                    label: LABELS.manage,
                    children: [h('div', { class: 'menu-actions' },
                        menuRow('desk-diagnostics', 'activity', LABELS.diagnostics, pick(onDiagnostics)),
                        menuRow('desk-reset-runtime', 'rotate-ccw', LABELS.reset, pick(onReset), { danger: true }),
                        menuRow('desk-remove', 'trash-2', LABELS.remove, pick(onRemove), { danger: true }))],
                }),
                h('hr', { class: 'menu-divider' }),
                BossModMenu.createMenuSection({
                    id: 'desk-menu-advanced',
                    label: LABELS.advanced,
                    children: [h('div', { class: 'menu-actions' }, yoloRow())],
                }))];
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
