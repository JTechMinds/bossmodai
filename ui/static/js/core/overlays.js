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

    /** Everything the browser will place in the tab order by default. */
    const FOCUSABLE = [
        'button:not([disabled])', '[href]', 'input:not([disabled])',
        'select:not([disabled])', 'textarea:not([disabled])',
        'summary', '[tabindex]:not([tabindex="-1"])',
    ].join(', ');

    /**
     * Keep Tab inside one overlay, and let Esc dismiss it.
     *
     * Shared by both overlays on purpose. They previously carried separate
     * implementations and only one of them was a real trap: the modal's cycled
     * over its own action buttons and bailed out when focus was anywhere else,
     * so any focusable content in the body leaked Tab into the page behind.
     *
     * @param {KeyboardEvent} event
     * @param {HTMLElement} element  The overlay root.
     * @param {() => void} close
     * @returns {void}
     */
    function trapKeydown(event, element, close) {
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
            return;
        }
        if (event.key !== 'Tab') return;
        const stops = Array.from(element.querySelectorAll(FOCUSABLE));
        if (stops.length === 0) return;
        const first = stops[0];
        const last = stops[stops.length - 1];
        const active = document.activeElement;
        // Focus outside the overlay means the trap was escaped — pull it back
        // rather than letting Tab walk into the page behind.
        if (!element.contains(active)) {
            event.preventDefault();
            first.focus();
            return;
        }
        if (event.shiftKey && active === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && active === last) {
            event.preventDefault();
            first.focus();
        }
    }

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

        // Was a cycle over `buttons` alone, which returned without preventing
        // the default whenever focus sat anywhere else — so Tab from a radio or
        // a text field in `body` walked straight into the page behind. Modals
        // do carry such bodies (context/desk-opener.js). One trap now serves
        // both overlays; a second implementation is how the two drifted apart.
        const onKeydown = (event) => trapKeydown(event, element, close);

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

    /**
     * Open a slide-over panel.
     *
     * Same accessibility contract as createModal — focus trapped inside, Esc
     * dismisses, focus returns to whatever opened it — but for a panel of
     * arbitrary content rather than a question with buttons. The Office's desk
     * and the Board's task detail both ride this; building the trap twice is
     * how one of them would end up without it.
     *
     * @param {object} options
     * @param {string} options.title  The panel's accessible name.
     * @param {HTMLElement} options.body  Content. Owned by the caller: this
     *   does not destroy it on close.
     * @param {() => void} [options.onClose]  Called once, after close, however
     *   it closed.
     * @returns {{ close: () => void, element: HTMLElement, body: HTMLElement }}
     */
    function slideOver({ title, body, onClose }) {
        const previouslyFocused = document.activeElement;

        const closeButton = h('button', {
            class: 'slide-over-close',
            type: 'button',
            'aria-label': `Close ${title}`,
            onclick: () => close(),
        }, '\u00d7');

        const element = h('div', {
            class: 'slide-over',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': title,
        },
            h('div', { class: 'slide-over-head' },
                h('h2', { class: 'slide-over-title' }, title),
                closeButton),
            body);

        const onKeydown = (event) => trapKeydown(event, element, close);

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
        closeButton.focus();

        return { close, element, body };
    }

    return { createModal, slideOver };
})();
