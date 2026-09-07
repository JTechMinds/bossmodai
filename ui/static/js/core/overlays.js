/**
 * BossMod AI — modal overlays.
 *
 * Replaces the browser's native confirmation dialog, which cannot be styled,
 * cannot be tested, and blocks the event loop. Every modal here is
 * keyboard-operable: focus is trapped inside while open, Esc dismisses without
 * confirming, and focus returns to whatever opened it.
 */
const BossModOverlays = (() => {
    const { h } = BossModDom;

    /**
     * Open a modal dialog.
     *
     * @param {object} options
     * @param {string} options.title
     * @param {string|HTMLElement} options.body
     * @param {Array<{label: string, tone?: string, id?: string,
     *   onSelect?: () => void}>} options.actions
     *   Rendered left to right. The LAST action receives focus on open, so put
     *   the safe choice last — Esc and the default focus should agree. An
     *   optional `id` is set on the button, so a caller whose buttons are
     *   already named (and selected by name in tests) keeps those names.
     * @param {() => void} [options.onClose] Called after close, however it
     *   closed — and after the chosen action's onSelect, so a caller can treat
     *   it as "dismissed" when no choice was recorded.
     * @returns {{ close: () => void, element: HTMLElement }}
     */
    function createModal({ title, body, actions, onClose }) {
        const previouslyFocused = document.activeElement;
        const buttons = [];

        const actionRow = h('div', { class: 'modal-actions' });
        (actions || []).forEach((action) => {
            const btn = h('button', {
                class: `modal-action ${action.tone || 'default'}`,
                type: 'button',
                onclick: () => {
                    // The chosen action runs BEFORE the dialog closes, so a
                    // caller that also passes onClose can tell a real choice
                    // from a dismissal. The finally keeps a throwing handler
                    // from leaving the modal stuck open.
                    try {
                        if (action.onSelect) action.onSelect();
                    } finally {
                        close();
                    }
                },
            }, action.label);
            // Test surface: lets a fake DOM identify a button without
            // reimplementing textContent traversal.
            btn.textLabel = action.label;
            if (action.id) btn.id = action.id;
            buttons.push(btn);
            actionRow.append(btn);
        });

        const element = h('div', {
            class: 'modal',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': title,
        },
            h('h2', { class: 'modal-title' }, title),
            h('div', { class: 'modal-body' }, body),
            actionRow);

        function onKeydown(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                close();
                return;
            }
            if (event.key !== 'Tab' || buttons.length === 0) return;
            // Trap: cycle focus within the action row.
            const index = buttons.indexOf(document.activeElement);
            if (index === -1) return;
            event.preventDefault();
            const step = event.shiftKey ? -1 : 1;
            const next = (index + step + buttons.length) % buttons.length;
            buttons[next].focus();
        }

        let closed = false;
        function close() {
            if (closed) return;
            closed = true;
            document.removeEventListener('keydown', onKeydown);
            element.remove();
            if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
            if (onClose) onClose();
        }

        document.addEventListener('keydown', onKeydown);
        document.body.append(element);
        // The safe choice holds focus, so Enter and Esc do the same thing.
        if (buttons.length) buttons[buttons.length - 1].focus();

        return { close, element };
    }

    return { createModal };
})();
