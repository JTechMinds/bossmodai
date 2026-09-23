/**
 * BossMod AI — the menu: a small panel hanging off the control that opened it.
 *
 * Split from core/overlays.js along the seam between the two overlay shapes.
 * A MENU hangs off an anchor: it is non-modal, has no backdrop, is positioned
 * by CSS against a container the caller names, and closes on a press
 * anywhere else. A MODAL (core/overlays.js) is a layered frame: one shared
 * scrim, a stack of layers with ‹ and ✕, and the page behind it blocked.
 * They share one keyboard rule — Tab trapped inside, Esc dismisses, focus
 * returns to the opener — and it lives in core/overlay-focus.js, which both
 * files read; neither carries a trap of its own.
 */
const BossModMenu = (() => {
    const { h } = BossModDom;
    // One rule, two overlays: the trap and the tab-order selector are
    // core/overlay-focus.js's, exactly as core/overlays.js reads them.
    const { FOCUSABLE, trapKeydown } = BossModOverlayFocus;

    /**
     * Open a small panel anchored to the control that asked for it.
     *
     * The same accessibility contract as the modal — Tab trapped inside,
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

    return { createMenu };
})();
