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
 *
 * It also owns NAVIGATION between those layers: each is its title plus the
 * STEPS its dialog reports (setSteps), the head shows them all as one trail,
 * and ‹, Esc and a crumb walk back through it. A dialog never builds a back
 * control of its own — the Add agent form's and the marketplace detail's were
 * two more. How the trail looks and walks is core/modal-trail.js's; which
 * layers it walks is this module's. It paints its own head (core/icons.js
 * loads ahead of it).
 */
const BossModOverlays = (() => {
    const { h } = BossModDom;
    // The trap and the tab-order selector are core/overlay-focus.js's: one
    // rule, two overlays, and no room left in this file to keep it here.
    const { FOCUSABLE, trapKeydown } = BossModOverlayFocus;
    // The trail, the ‹ and the walk back; the stack they walk is this file's.
    const TRAIL = BossModModalTrail;
    // The footer's buttons (core/overlay-actions.js); the row is this file's.
    const ACTIONS = BossModOverlayActions;

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

    /**
     * Rebuild every layer's trail (core/modal-trail.js): a layer's crumbs
     * include every layer beneath it, so a change to one changes all above
     * it. A crumb click walks back until that crumb is the current one.
     */
    function renderTrails() {
        const goTo = (target, depth) => TRAIL.walkTo(layers, target, depth);
        layers.forEach((layer, index) => TRAIL.render(layer, TRAIL.crumbsOf(layers, index), goTo));
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
     * Open a modal dialog.
     *
     * Opened while another modal is up, it becomes a LAYER in the same frame:
     * the one beneath is hidden, not destroyed, and the title ends a trail of
     * every layer and step beneath it. ‹ and Esc go back one — this layer's
     * last step (`setSteps`), else this layer; a crumb goes back to itself; ✕
     * closes every layer, top first, each onClose firing; an outside click
     * closes them all only when every layer opted in to closeOnBackdrop.
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
     * @param {HTMLElement} [options.lead] An identity mark for the TITLE ROW,
     *   before the trail — the desk's avatar. It belongs to the caller; this
     *   only decides where it renders. Never a back control: going back is
     *   the frame's (see `setSteps`).
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
     * @param {boolean} [options.focusBody=true]  False skips the body on open
     *   (see "Focus on open"): for a view whose first control is a field that
     *   reads as text, where focusing it paints its focus indicator on open.
     * @param {'default'|'panel'|'takeover'} [options.size='default']
     *   Geometry only. 'default' is the compact question/short-form size;
     *   'panel' is 80% x 85% of the window for anything that shows content;
     *   'takeover' is 95% for a browse-and-read surface. All pin the head and
     *   actions outside the body, and none touches the trap, Esc or focus
     *   restoration. Anything else is default.
     * @returns {{ close: () => void, closeFrom: () => void,
     *   element: HTMLElement, focusBack: () => void,
     *   setActions: (actions: Array<object>) => void,
     *   setSteps: (steps: Array<{title: string, onBack: () => void}>) => void,
     *   setTitle: (title: string) => void,
     *   setTitleEditor: (node: HTMLElement|null) => void }} `close` removes exactly
     *   this layer, wherever it sits: layers above it stay. `closeFrom`
     *   closes the layers above, top first, then this one — for leaving the
     *   modal world from here, so nothing stacked on it is orphaned. `setActions` rebuilds
     *   the action row IN PLACE — same node, same panel, same trap — because a
     *   two-step dialog needs a footer per step, step one has no form for a
     *   `form:` primary to submit, and a second stacked dialog would be a
     *   second focus trap over one task. The trap re-reads the panel on every
     *   Tab so it finds the new buttons; the button that held focus may be one
     *   just removed, so placing focus after a swap is the caller's.
     *   `setTitle` renames THIS layer in place (a rename saved in the dialog):
     *   its base crumb, the dialog's accessible name while it has no steps,
     *   its ✕ ("Close …"), and every crumb and ‹ above it that reads the title.
     *   `setSteps` says where the dialog is INSIDE this layer (the Agents
     *   dialog's pack detail) as crumbs after the title. Back one pops the last
     *   step FIRST, then calls its `onBack`, which re-sends setSteps from the
     *   dialog's own state — idempotent, so the two cannot drift. It throws on
     *   malformed steps (core/modal-trail.js `copySteps`). `focusBack` puts
     *   the keyboard on this layer's ‹, and throws while it is hidden.
     *   `setTitleEditor` mounts a caller's control (the task detail's title
     *   input) in place of this layer's title text, and keeps it there across
     *   every trail re-render — a caller-mounted node would otherwise be
     *   wiped by the next layer change. The accessible name stays the title,
     *   crumbs above still read it, and null restores the text. While a
     *   control is mounted the title carries `is-editing`, which lifts the
     *   ellipsis clip (overlays.css) that would otherwise cut the control's
     *   edit hairline; null removes it.
     *
     *   Focus on open: the first control in the BODY that actually takes focus
     *   (a hidden one does not) unless `focusBody` is false; else the last
     *   action; else the frame's ✕, so the keyboard always lands inside.
     * @throws {Error} When closeOnBackdrop is set to anything but a boolean or
     *   a function.
     */
    function createModal({
        title, body, actions, onClose, size, lead, subtitle, tools, closeOnBackdrop, focusBody = true,
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
            buttons.push(...ACTIONS.render(actionRow, nextActions, close));
        }
        setActions(actions);

        // The frame's own exit, on every dialog: it closes every layer, back to
        // the base screen. `.header-icon-btn` is the app's one icon button
        // (shell.css) — the bell's and the gear's shape, not a fifth geometry.
        // The ✕ stays a typed glyph; the ‹ below is a lucide chevron because
        // it is the frame's one back control (core/modal-trail.js), and this
        // module paints the head it builds once the panel is mounted.
        const closeButton = h('button', {
            class: 'header-icon-btn modal-close',
            type: 'button',
            'aria-label': `Close ${title}`,
            onclick: () => closeAll(),
        }, '\u2715');

        // ‹ — back one, which is what Esc does too. Built on every panel and
        // hidden while the trail is a single crumb, so the head row never
        // changes shape. `layer` is declared below; the click comes later.
        const back = TRAIL.backButton(() => layer.backOne());

        // Kept by name and never recreated: each trail render moves it into the
        // rebuilt trail as the current crumb, so setTitle works in place.
        const titleNode = h('h2', { class: 'modal-title' }, title);
        const trailNode = h('nav', { class: 'modal-trail', 'aria-label': 'Breadcrumb' });

        // One row, the conversation header's: the way back, whatever leads,
        // the trail ending in the name, a fact about it, the caller's tools,
        // and the exit. Empty slots render nothing, so a confirm's head is its
        // title and its ✕.
        const head = h('div', { class: 'modal-head' },
            back,
            lead || null,
            trailNode,
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
            titleNode,
            trailNode,
            /** Where the dialog is inside this layer; see setSteps. */
            steps: [],
            /** A caller's control shown in place of the title; see setTitleEditor. */
            titleEditor: null,
            /** Back one: this layer's last step, else this layer. */
            backOne() {
                if (!layer.steps.length) {
                    close();
                    return;
                }
                const step = layer.steps.pop();
                renderTrails();
                step.onBack();
            },
            allowsBackdrop: () => (typeof closeOnBackdrop === 'function'
                ? closeOnBackdrop() === true
                : closeOnBackdrop === true),
            close: () => close(),
            focusClose: () => closeButton.focus(),
            adoptOpener: (node) => { previouslyFocused = node; },
        };

        // Esc is ‹: back one step, else this layer, and at the base, closed.
        const onKeydown = (event) => trapKeydown(event, element, () => layer.backOne());

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
            renderTrails();
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
        renderTrails();
        BossModOverlayFocus.mountOverlay(element, onKeydown);
        document.body.append(element);
        // The ‹ chevron is a placeholder until painted. Scoped to the head this
        // call built, and the painter is idempotent, so a caller's lead or
        // tools painted again by its own dialog-wide paint are unaffected.
        BossModIcons.paint(head, 'overlays');
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
        const bodyStops = focusBody ? Array.from(bodyNode.querySelectorAll(FOCUSABLE)) : [];
        if (!bodyStops.some(takesFocus)) {
            if (buttons.length) buttons[buttons.length - 1].focus();
            else closeButton.focus();
        }

        /** Rename this layer; see @returns. */
        function setTitle(next) {
            layer.title = next;
            closeButton.setAttribute('aria-label', `Close ${next}`);
            renderTrails();
        }

        /** Edit this layer's title in place, or stop; see @returns. */
        function setTitleEditor(node) {
            layer.titleEditor = node;
            titleNode.classList.toggle('is-editing', Boolean(node));
            renderTrails();
        }

        /** Report where the dialog is inside this layer; see @returns. */
        function setSteps(steps) {
            layer.steps = TRAIL.copySteps(steps);
            renderTrails();
        }

        /** Put the keyboard on this layer's ‹; see @returns. */
        function focusBack() {
            if (back.hidden) throw new Error('[overlays] focusBack with nowhere to go back to');
            back.focus();
        }

        /** This layer and everything stacked on it, top first; see @returns. */
        function closeFrom() {
            // Already closed: nothing above it is its to close.
            if (!layers.includes(layer)) return;
            layers.slice(layers.indexOf(layer) + 1).reverse().forEach((above) => above.close());
            close();
        }

        return { close, closeFrom, element, focusBack, setActions, setSteps, setTitle, setTitleEditor };
    }

    return { createModal };
})();
