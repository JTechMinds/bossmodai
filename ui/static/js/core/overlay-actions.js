/**
 * BossMod AI — a modal's action row.
 *
 * Split from core/overlays.js when it reached its line cap with the task
 * detail's title editor still to land, on the seam core/overlay-focus.js and
 * core/menu.js were cut along: overlays.js owns the layers and the frame;
 * this owns how the footer's buttons are built and what a click on one does.
 * createModal's `actions` option documents the contract these buttons keep.
 *
 * Loaded before core/overlays.js in index.html, its only consumer.
 */
const BossModOverlayActions = (() => {
    const { h, clear } = BossModDom;

    /**
     * Fill an action row with buttons, replacing whatever it held: one
     * implementation for construction and for setActions(), so the `form`,
     * `keepOpen` and close semantics documented on createModal's `actions`
     * cannot drift.
     * @returns {HTMLElement[]} The buttons, in render order.
     */
    function render(actionRow, actions, close) {
        const buttons = [];
        clear(actionRow);
        (actions || []).forEach((action) => {
            // A `form` makes this that form's submit button from outside it,
            // and it must NOT close: a refused save keeps the draft on screen.
            const btn = h('button', {
                class: `modal-action ${action.tone || 'default'}`,
                type: action.form ? 'submit' : 'button',
                form: action.form || null,
                onclick: action.form ? null : () => {
                    // Runs BEFORE close (options.onClose); finally unwedges it.
                    // `keepOpen` skips the close: the action opened a layer.
                    try {
                        if (action.onSelect) action.onSelect();
                    } finally {
                        if (!action.keepOpen) close();
                    }
                },
            }, action.label);
            // Test surface: a fake DOM can name a button without textContent.
            btn.textLabel = action.label;
            if (action.id) btn.id = action.id;
            buttons.push(btn);
            actionRow.append(btn);
        });
        return buttons;
    }

    return { render };
})();
