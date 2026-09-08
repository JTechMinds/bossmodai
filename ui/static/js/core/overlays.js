/**
 * BossMod AI — modal overlays.
 *
 * Replaces the browser's native confirmation dialog, which cannot be styled,
 * cannot be tested, and blocks the event loop. Every overlay here is
 * keyboard-operable: focus is trapped inside while open, Esc dismisses without
 * confirming, and focus returns to whatever opened it.
 *
 * Three shapes, one contract — a modal question, a slide-over panel, and a
 * menu hanging off a control. They share trapKeydown() rather than each
 * carrying a trap of its own, which is how the first two drifted apart once
 * already.
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

    /**
     * Open a small panel anchored to the control that asked for it.
     *
     * The same accessibility contract as the other two — Tab trapped inside,
     * Esc dismisses, focus returns to the opener — but non-modal, because the
     * page behind is not blocked by a handful of view options.
     *
     * `role="dialog"`, not `role="menu"`: a menu's children must carry
     * menuitem roles, and the first thing this holds is a `role="switch"`.
     * A dialog is the container that can carry an arbitrary control honestly.
     *
     * It is positioned by CSS against `container` rather than by measuring the
     * anchor. Measuring would need a viewport this codebase's fake DOM cannot
     * supply, and would put a number in JavaScript that the stylesheet is
     * better at.
     *
     * @param {object} options
     * @param {HTMLElement} options.anchor  The control that opened it. Focus
     *   returns HERE on close, named explicitly rather than read from
     *   document.activeElement: a mouse click does not focus a button in every
     *   browser, and the anchor is the one right answer either way.
     * @param {string} options.label  The panel's accessible name.
     * @param {HTMLElement[]} options.items  Content. Owned by the caller: this
     *   does not destroy it on close, so a preference control keeps what it
     *   holds across every open.
     * @param {HTMLElement} options.container  What it is positioned against.
     *   Must be a positioned ancestor of the anchor.
     * @param {() => void} [options.onClose]  Called once, after close, however
     *   it closed — so the anchor can drop its aria-expanded and toggle rather
     *   than stack a second panel.
     * @returns {{ close: () => void, element: HTMLElement }}
     * @throws {Error} When the anchor or the container is missing. A panel
     *   with nothing to return focus to is a keyboard dead end.
     */
    function createMenu({ anchor, label, items, container, onClose }) {
        if (!anchor) throw new Error('[overlays] a menu needs the control it hangs off');
        if (!container) throw new Error('[overlays] a menu needs a container to sit in');

        const element = h('div', {
            class: 'menu',
            role: 'dialog',
            'aria-label': label,
        }, items || []);

        const onKeydown = (event) => trapKeydown(event, element, close);
        // A press anywhere else dismisses it, which is what a panel hanging
        // off a button is expected to do. mousedown rather than click, so it
        // is gone before whatever is under the pointer reacts. The anchor is
        // excluded: it toggles, and closing here would make its own click
        // re-open the panel it just shut.
        const onPointerDown = (event) => {
            const target = event.target;
            if (element.contains(target)) return;
            if (anchor === target || (anchor.contains && anchor.contains(target))) return;
            close();
        };

        let closed = false;
        function close() {
            if (closed) return;
            closed = true;
            document.removeEventListener('keydown', onKeydown);
            document.removeEventListener('mousedown', onPointerDown);
            element.remove();
            if (anchor.focus) anchor.focus();
            if (onClose) onClose();
        }

        document.addEventListener('keydown', onKeydown);
        document.addEventListener('mousedown', onPointerDown);
        container.append(element);
        // The first option, so the keyboard lands on something to act on. With
        // no options there is nothing to focus and the anchor keeps it, which
        // is why a caller with nothing to show should not open one at all.
        const stops = element.querySelectorAll(FOCUSABLE);
        if (stops.length) stops[0].focus();

        return { close, element };
    }

    return { createModal, slideOver, createMenu };
})();
