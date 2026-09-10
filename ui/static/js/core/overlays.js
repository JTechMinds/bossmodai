/**
 * BossMod AI — modal overlays.
 *
 * Replaces the browser's native confirmation dialog, which cannot be styled,
 * cannot be tested, and blocks the event loop. Every overlay here is
 * keyboard-operable: focus is trapped inside while open, Esc dismisses without
 * confirming, and focus returns to whatever opened it.
 *
 * Three shapes, one contract — a modal question, a slide-over panel, and a
 * menu hanging off a control. They share core/overlay-focus.js's trapKeydown()
 * rather than each carrying a trap of its own, which is how the first two
 * drifted apart once already. That module holds the keyboard rule; this one
 * holds the three shapes that obey it.
 *
 * The modal has THREE SIZES and one implementation: a confirm dialog, the
 * agent form and a full-screen takeover differ in geometry, not in contract,
 * so the difference is an attribute the stylesheet reads rather than three
 * functions. It also owns the BACKDROP that blocks the page — built and
 * removed with the panel: a scrim outliving its dialog bricks the app.
 */
const BossModOverlays = (() => {
    const { h, clear } = BossModDom;
    // The trap and the tab-order selector are core/overlay-focus.js's: one
    // rule, three overlays, and no room left in this file to keep it here.
    const { FOCUSABLE, trapKeydown } = BossModOverlayFocus;

    /**
     * Fill an action row with buttons, replacing whatever it held: one
     * implementation for construction and for setActions(), so the `form` and
     * close semantics documented on createModal's `actions` cannot drift.
     * @returns {HTMLElement[]} The buttons, in render order.
     */
    function renderActions(actionRow, actions, close) {
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
                    try {
                        if (action.onSelect) action.onSelect();
                    } finally {
                        close();
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

    /**
     * Open a modal dialog.
     *
     * @param {object} options
     * @param {string} options.title
     * @param {string|HTMLElement} options.body
     * @param {Array<{label: string, tone?: string, id?: string, form?: string,
     *   onSelect?: () => void}>} options.actions
     *   Rendered left to right. The LAST receives focus on open when — and
     *   only when — the body has nothing focusable in it: that is the safe
     *   choice in a confirm dialog, and it is wrong in a form one, where the
     *   primary is now the last action and focusing it means Enter submits an
     *   empty form. See the focus line at the end of this function. `id` names
     *   the button, so a caller whose buttons are already named (and selected
     *   by name in tests) keeps those names. `form` is the id of a form THIS
     *   BUTTON SUBMITS from outside it — how a form's primary can be pinned
     *   above a scrolling body — and such an action does not close the dialog,
     *   because the form's own handler and validation own the outcome.
     * @param {() => void} [options.onClose] Called after close, however it
     *   closed — and after the chosen action's onSelect, so a caller can treat
     *   it as "dismissed" when no choice was recorded.
     * @param {'default'|'wide'|'takeover'} [options.size='default'] Geometry
     *   only. 'wide' is broad enough for a form; 'takeover' is the near
     *   full-screen variant a browse-and-read surface needs. Both keep the
     *   scrolling BODY with title and actions pinned outside it, and neither
     *   touches the trap, Esc or focus restoration. Anything else is default.
     * @returns {{ close: () => void, element: HTMLElement,
     *   setActions: (actions: Array<object>) => void }} `setActions` rebuilds
     *   the action row IN PLACE — same node, same panel, same trap — because a
     *   two-step dialog needs a footer per step, step one has no form for a
     *   `form:` primary to submit, and a second stacked dialog would be a
     *   second focus trap over one task. The trap re-reads the panel on every
     *   Tab so it finds the new buttons; the button that held focus may be one
     *   just removed, so placing focus after a swap is the caller's.
     */
    function createModal({ title, body, actions, onClose, size }) {
        const previouslyFocused = document.activeElement;
        const buttons = [];

        const actionRow = h('div', { class: 'modal-actions' });
        // Refilled, never replaced: the focus fallback at the end of this
        // function closes over `buttons`, and a fresh array would strand it.
        function setActions(nextActions) {
            buttons.length = 0;
            buttons.push(...renderActions(actionRow, nextActions, close));
        }
        setActions(actions);

        const bodyNode = h('div', { class: 'modal-body' }, body);

        const element = h('div', {
            class: 'modal-panel',
            // Read by the stylesheet, never by script: geometry is CSS's.
            'data-size': size === 'wide' || size === 'takeover' ? size : 'default',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': title,
        },
            h('h2', { class: 'modal-title' }, title),
            bodyNode,
            actionRow);

        // What makes it modal: without this the panel floated over a LIVE page
        // and clicks reached the controls behind it. No dismiss handler — the
        // wide variant holds a half-filled form a stray click must not discard.
        const backdrop = h('div', { class: 'modal-backdrop' });

        const onKeydown = (event) => trapKeydown(event, element, close);

        let closed = false;
        function close() {
            if (closed) return;
            closed = true;
            BossModOverlayFocus.unmountOverlay(element, onKeydown);
            // With the panel, always: a leaked scrim bricks the app.
            backdrop.remove();
            element.remove();
            if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
            if (onClose) onClose();
        }

        BossModOverlayFocus.mountOverlay(element, onKeydown);
        // Backdrop first, so the panel paints over it in document order too.
        document.body.append(backdrop, element);
        // A FORM dialog starts in the form. Round four pinned the primary last
        // and this focused the last action, so opening Hire put the keyboard on
        // `Create Agent` and Enter submitted an empty form. The test is what the
        // BODY holds, not the `size` flag: a wide dialog with nothing to type in
        // has no better place for focus than its action row, and a confirm
        // dialog's last action is the safe one a destructive prompt should open
        // on. Both fall through to the same line.
        const bodyStops = bodyNode.querySelectorAll(FOCUSABLE);
        if (bodyStops.length) bodyStops[0].focus();
        else if (buttons.length) buttons[buttons.length - 1].focus();

        return { close, element, setActions };
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
            BossModOverlayFocus.unmountOverlay(element, onKeydown);
            element.remove();
            if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
            if (onClose) onClose();
        }

        BossModOverlayFocus.mountOverlay(element, onKeydown);
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
            BossModOverlayFocus.unmountOverlay(element, onKeydown);
            document.removeEventListener('mousedown', onPointerDown);
            element.remove();
            if (anchor.focus) anchor.focus();
            if (onClose) onClose();
        }

        BossModOverlayFocus.mountOverlay(element, onKeydown);
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
