/**
 * BossMod AI — modal overlays.
 *
 * Replaces the browser's native confirmation dialog, which cannot be styled,
 * cannot be tested, and blocks the event loop. Every overlay here is
 * keyboard-operable: focus is trapped inside while open, Esc dismisses without
 * confirming, and focus returns to whatever opened it.
 *
 * Two shapes, one contract — this modal, and the menu hanging off a control
 * (core/menu.js, split from here). They share core/overlay-focus.js's
 * trapKeydown() rather than each carrying a trap of its own, which is how the
 * modal and the old slide-over drifted apart once already. That module holds
 * the keyboard rule; this one and core/menu.js hold the shapes that obey it.
 * Slide-over panels were retired on 2026-09-21: every secondary
 * screen now opens in the modal, so there is one overlay primitive for them.
 *
 * The modal has THREE SIZES, one implementation and one frame: a question, a
 * panel that shows something and a near full-screen takeover differ in
 * geometry, not in contract, so the difference is an attribute the stylesheet
 * reads rather than three functions. Every size wears the same frame — a head
 * row (‹, title, the caller's slots, the frame's own ✕), a body, and a footer
 * band for the actions. It also owns the LAYERS and their one shared BACKDROP
 * — a modal opened from a modal is a layer in the same frame, never a second
 * dialog on top, and the scrim leaves with the last layer: a scrim outliving
 * its last layer bricks the app.
 */
const BossModOverlays = (() => {
    const { h, clear } = BossModDom;
    // The trap and the tab-order selector are core/overlay-focus.js's: one
    // rule, two overlays, and no room left in this file to keep it here.
    const { FOCUSABLE, trapKeydown } = BossModOverlayFocus;

    /** The sizes the stylesheet knows. Anything else renders as 'default'. */
    const SIZES = ['panel', 'takeover'];

    /**
     * The open modal LAYERS, base first. One frame is on screen at a time: a
     * modal opened while another is up becomes the layer on top, and the one
     * beneath is hidden — kept, not destroyed — so a draft or a scroll
     * position survives the trip. Nothing ever sits on top of anything.
     */
    const layers = [];
    /** The one scrim every layer shares; null while no modal is open. */
    let scrim = null;

    /** Point each layer's ‹ at the layer beneath it now; the base has none. */
    function relabel() {
        layers.forEach((layer, index) => {
            layer.back.hidden = index === 0;
            if (index > 0) layer.back.setAttribute('aria-label', `Back to ${layers[index - 1].title}`);
        });
    }

    /** ✕: every layer, top first, so each caller's onClose still runs. */
    function closeAll() {
        layers.slice().reverse().forEach((layer) => layer.close());
    }

    /** An outside click closes the stack only if EVERY layer allows it — a
     *  form anywhere beneath must not lose its typing to a stray click. */
    function onScrimClick() {
        if (layers.every((layer) => layer.allowsBackdrop())) closeAll();
    }

    /**
     * Fill an action row with buttons, replacing whatever it held: one
     * implementation for construction and for setActions(), so the `form`,
     * `keepOpen` and close semantics documented on createModal's `actions`
     * cannot drift.
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

    /**
     * Open a modal dialog.
     *
     * Opened while another modal is up, it becomes a LAYER in the same frame:
     * the one beneath is hidden, not destroyed, and the head gains a ‹ named
     * for it. ‹ and Esc go back one layer; ✕ closes every layer, top first,
     * each onClose firing; an outside click closes them all only when every
     * layer opted in to closeOnBackdrop.
     *
     * @param {object} options
     * @param {string} options.title
     * @param {string|HTMLElement} options.body
     * @param {Array<{label: string, tone?: string, id?: string, form?: string,
     *   keepOpen?: boolean, onSelect?: () => void}>} options.actions
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
     *   `keepOpen: true` runs `onSelect` and leaves the dialog up too: for an
     *   action that opens a layer over it, such as Save as template.
     * @param {() => void} [options.onClose] Called after close, however it
     *   closed — and after the chosen action's onSelect, so a caller can treat
     *   it as "dismissed" when no choice was recorded.
     * @param {HTMLElement} [options.lead] A node to sit on the TITLE ROW,
     *   before the heading: a step dialog's Back, or an identity mark such as
     *   an avatar. It belongs to the caller, which keeps it and may hide it per
     *   step; this only decides where it renders.
     * @param {string} [options.subtitle] A short fact after the title — an
     *   agent's status. Text only; it is read as part of the head row.
     * @param {HTMLElement[]} [options.tools] Controls at the right end of the
     *   head, before ✕ — the file viewer's View/Edit/Save/Print. Owned by the
     *   caller, which may hide or relabel them; this only places them.
     * @param {boolean|(() => boolean)} [options.closeOnBackdrop=false] Whether
     *   a click on the scrim closes the dialog. False by default because a
     *   form must never lose typing to a stray click. A function is asked at
     *   click time, so a surface can refuse while it holds an unsaved edit.
     *   The scrim is shared, so a click closes every layer, and only when
     *   EVERY open layer allows it.
     * @param {'default'|'panel'|'takeover'} [options.size='default']
     *   Geometry only. 'default' is the compact question/short-form size;
     *   'panel' is 80% x 85% of the window for anything that shows content;
     *   'takeover' is 95% for a browse-and-read surface. All pin the head and
     *   actions outside the body, and none touches the trap, Esc or focus
     *   restoration. Anything else is default.
     * @returns {{ close: () => void, element: HTMLElement,
     *   setActions: (actions: Array<object>) => void,
     *   setTitle: (title: string) => void }} `close` removes exactly
     *   this layer, wherever it sits: layers above it stay, and each ‹ is
     *   relabelled to whatever is now beneath it. `setActions` rebuilds
     *   the action row IN PLACE — same node, same panel, same trap — because a
     *   two-step dialog needs a footer per step, step one has no form for a
     *   `form:` primary to submit, and a second stacked dialog would be a
     *   second focus trap over one task. The trap re-reads the panel on every
     *   Tab so it finds the new buttons; the button that held focus may be one
     *   just removed, so placing focus after a swap is the caller's.
     *   `setTitle` renames THIS layer in place (a rename saved in the dialog):
     *   the head's title, the dialog's accessible name, its ✕ ("Close …"),
     *   and the ‹ of the layer above it ("Back to …"), which reads the title.
     *
     *   Focus on open: the first control in the BODY that actually takes focus
     *   (a hidden one does not); else the last action; else the frame's ✕, so
     *   the keyboard always lands inside.
     * @throws {Error} When closeOnBackdrop is set to anything but a boolean or
     *   a function.
     */
    function createModal({
        title, body, actions, onClose, size, lead, subtitle, tools, closeOnBackdrop,
    }) {
        if (closeOnBackdrop !== undefined && typeof closeOnBackdrop !== 'boolean'
            && typeof closeOnBackdrop !== 'function') {
            throw new Error('[overlays] closeOnBackdrop must be a boolean or a function');
        }
        // Where focus returns when this layer closes. Reassigned only when the
        // layer beneath closes first and hands its own opener up.
        let previouslyFocused = document.activeElement;
        const buttons = [];

        const actionRow = h('div', { class: 'modal-actions' });
        // Refilled, never replaced: the focus fallback at the end of this
        // function closes over `buttons`, and a fresh array would strand it.
        function setActions(nextActions) {
            buttons.length = 0;
            buttons.push(...renderActions(actionRow, nextActions, close));
        }
        setActions(actions);

        // The frame's own exit, on every dialog: it closes every layer, back to
        // the base screen. `.header-icon-btn` is the app's one icon button
        // (shell.css) — the bell's and the gear's shape, not a fifth geometry.
        // A glyph, not a lucide placeholder: this module paints nothing and
        // depends on nothing but the DOM helpers and the focus rule.
        const closeButton = h('button', {
            class: 'header-icon-btn modal-close',
            type: 'button',
            'aria-label': `Close ${title}`,
            onclick: () => closeAll(),
        }, '\u2715');

        // ‹ — back one layer, which is what Esc does too. Built on every panel
        // and hidden on the base, so the head row never changes shape.
        const back = h('button', {
            class: 'header-icon-btn modal-back',
            type: 'button',
            onclick: () => close(),
        }, '‹');

        // Kept by name: setTitle renames it in place.
        const titleNode = h('h2', { class: 'modal-title' }, title);

        // One row, the conversation header's: the way back, whatever leads,
        // the name, a fact about it, the caller's tools, and the exit. Empty
        // slots render nothing, so a confirm's head is its title and its ✕.
        const head = h('div', { class: 'modal-head' },
            back,
            lead || null,
            titleNode,
            subtitle ? h('span', { class: 'modal-subtitle' }, subtitle) : null,
            tools && tools.length ? h('div', { class: 'modal-tools' }, tools) : null,
            closeButton);

        const bodyNode = h('div', { class: 'modal-body' }, body);

        const element = h('div', {
            class: 'modal-panel',
            // Read by the stylesheet, never by script: geometry is CSS's.
            'data-size': SIZES.includes(size) ? size : 'default',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': title,
        }, head, bodyNode, actionRow);

        // This dialog as the stack sees it. The scrim asks allowsBackdrop at
        // click time, so a function option can refuse mid-edit.
        const layer = {
            element,
            title,
            back,
            allowsBackdrop: () => (typeof closeOnBackdrop === 'function'
                ? closeOnBackdrop() === true
                : closeOnBackdrop === true),
            close: () => close(),
            focusClose: () => closeButton.focus(),
            adoptOpener: (node) => { previouslyFocused = node; },
        };

        // Esc runs THIS layer's close: back one, and at the base, closed.
        const onKeydown = (event) => trapKeydown(event, element, close);

        let closed = false;
        /** Remove exactly THIS layer, wherever it sits in the stack. */
        function close() {
            if (closed) return;
            closed = true;
            const index = layers.indexOf(layer);
            const wasTop = index === layers.length - 1;
            // The layer above inherits this one's opener: "back" from it now
            // leads where this layer's did — the layer beneath, or the screen.
            if (!wasTop) layers[index + 1].adoptOpener(previouslyFocused);
            layers.splice(index, 1);
            BossModOverlayFocus.unmountOverlay(element, onKeydown);
            element.remove();
            const top = layers[layers.length - 1] || null;
            if (!top) {
                // With the last layer, always: a leaked scrim bricks the app.
                scrim.remove();
                scrim = null;
            } else if (wasTop) {
                top.element.hidden = false;
            }
            relabel();
            if (wasTop) {
                // Back to what opened this layer — unless that lived in a layer
                // closed meanwhile, in which case the new top's ✕ takes it
                // rather than nothing.
                if (top && !top.element.contains(previouslyFocused)) top.focusClose();
                else if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
            }
            if (onClose) onClose();
        }

        // The first layer brings the scrim, appended first so every panel paints
        // over it in document order too; a later layer hides the one beneath.
        if (!scrim) {
            scrim = h('div', { class: 'modal-backdrop', onclick: onScrimClick });
            document.body.append(scrim);
        } else {
            layers[layers.length - 1].element.hidden = true;
        }
        layers.push(layer);
        relabel();
        BossModOverlayFocus.mountOverlay(element, onKeydown);
        document.body.append(element);
        // A FORM dialog starts in the form. Round four pinned the primary last
        // and this focused the last action, so opening Hire put the keyboard on
        // `Create Agent` and Enter submitted an empty form. The test is what the
        // BODY holds, not the `size` flag: a dialog with nothing to type in but
        // an action row starts on its last action, which is the safe one a
        // destructive prompt should open on. A dialog with neither starts on
        // the frame's ✕, so the keyboard is always inside the thing on top.
        //
        // A body stop counts only once it has TAKEN focus. focus() on a hidden
        // control is a silent no-op in a browser — the file viewer's editor
        // waits hidden in the body until Edit — and trusting the selector left
        // the keyboard on the opener, behind the scrim.
        const takesFocus = (node) => {
            node.focus();
            return document.activeElement === node;
        };
        const bodyStops = Array.from(bodyNode.querySelectorAll(FOCUSABLE));
        if (!bodyStops.some(takesFocus)) {
            if (buttons.length) buttons[buttons.length - 1].focus();
            else closeButton.focus();
        }

        /** Rename this layer; see @returns. */
        function setTitle(next) {
            layer.title = next;
            titleNode.textContent = next;
            element.setAttribute('aria-label', next);
            closeButton.setAttribute('aria-label', `Close ${next}`);
            relabel();
        }

        return { close, element, setActions, setTitle };
    }

    return { createModal };
})();
