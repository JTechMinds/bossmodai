/**
 * BossMod AI — one setting switch behind the conversation header's `⋯`: its
 * node, built from a `kind: 'switch'` menu action, and re-synced from the
 * next one on every paint.
 *
 * Split from conversation/chrome.js on the seam between two jobs. Building
 * and re-syncing one switch node from a descriptor is this file's. Laying out
 * the header, reusing nodes by id across repaints and gating runs stay in
 * chrome.js, which owns WHEN a toggle runs the action and which action it
 * runs. Nothing here reads a source or knows what a setting means.
 *
 * The pill is core/switch.js. `sync` restores the server's value through its
 * silent `set`, so a failed save puts the pill back.
 */
const BossModChromeSwitch = (() => {
    const { h } = BossModDom;

    /**
     * Build one switch wrapper.
     *
     * @param {object} opts
     * @param {string} opts.id  The switch's DOM id; its hint line is
     *   `${id}-hint`.
     * @param {string} opts.label  Visible text and accessible name.
     * @param {(pressed: boolean) => void} opts.onToggle  What core/switch.js's
     *   onChange calls with the new state after the operator toggles it.
     * @returns {HTMLElement} `div.menu-switch`: `_row` is the switch itself,
     *   `_switch` its core/switch.js handle, `_hint` its (detached) hint line.
     * @throws {Error} When `id` is missing — a hint with no id cannot describe
     *   its switch — or, from core/switch.js, when `label` or `onToggle` is.
     */
    function create({ id, label, onToggle }) {
        if (!id) throw new Error('[chrome-switch] opts.id is required');
        const control = BossModSwitch.create({ label, onChange: onToggle });
        const row = control.element;
        row.id = id;
        const node = h('div', { class: 'menu-switch' }, row);
        node._row = row;
        node._switch = control;
        node._hint = h('p', { class: 'menu-switch-hint', id: `${id}-hint` });
        return node;
    }

    /**
     * Re-sync a switch wrapper from the descriptor painted now.
     *
     * `disabled` means the setting is decided somewhere else, which the
     * source names in `hint`. It is announced as well as styled
     * (`aria-disabled`), and the hint is the switch's accessible
     * description. The hint sits BESIDE the switch rather than inside
     * it: the disabled row is dimmed, and its explanation must stay readable.
     * (The row's `disabled` property is chrome.js's, which also gates it
     * while an action is in flight.)
     *
     * @param {HTMLElement} node  From `create`.
     * @param {{pressed?: boolean, disabled?: boolean, hint?: string}} action
     * @returns {void}
     */
    function sync(node, action) {
        node._switch.set(action.pressed === true);
        if (action.disabled === true) node._row.setAttribute('aria-disabled', 'true');
        else node._row.removeAttribute('aria-disabled');
        const hint = String(action.hint || '').trim();
        node._hint.textContent = hint;
        if (hint) {
            node.append(node._hint);
            node._row.setAttribute('aria-describedby', node._hint.id);
        } else {
            node._hint.remove();
            node._row.removeAttribute('aria-describedby');
        }
    }

    /**
     * The reused switch for a menu setting, built on first sight and re-synced
     * on every paint.
     *
     * The handler is rewritten every paint because the open conversation
     * changes and the node does not: a switch built for the previous thread
     * must not keep that thread's callback.
     *
     * @param {Map<string, HTMLElement>} nodes  The header's nodes by action id.
     * @param {object} action  A `kind: 'switch'` menu action.
     * @param {object} hooks
     * @param {() => void} hooks.closeMenu  Puts the `⋯` panel away on toggle.
     * @param {(btn: HTMLElement, action: object) => Promise<void>} hooks.run
     *   The header's in-flight gate and error path.
     * @returns {HTMLElement} The wrapper; `_row` is the switch itself.
     */
    function ensure(nodes, action, { closeMenu, run }) {
        let node = nodes.get(action.id);
        if (!node) {
            node = create({
                id: action.id,
                label: action.label,
                onToggle: (pressed) => {
                    const current = nodes.get(action.id);
                    const select = current && current._onSelect;
                    closeMenu();
                    if (typeof select !== 'function') return;
                    void run(current._row, {
                        label: action.label,
                        onSelect: () => select(pressed),
                    });
                },
            });
            nodes.set(action.id, node);
        }
        node._onSelect = action.onSelect;
        sync(node, action);
        return node;
    }

    return { create, sync, ensure };
})();
