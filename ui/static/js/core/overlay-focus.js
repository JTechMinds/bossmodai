/**
 * BossMod AI — the keyboard machinery every overlay shares.
 *
 * Split out of core/overlays.js, which had reached exactly 299 lines under a
 * 300-line cap with a bug fix still to land inside createModal. The seam is a
 * real one rather than a line-count convenience: keeping Tab inside a dialog
 * and answering Esc is one responsibility, and building a modal, a slide-over
 * or an anchored menu is another. core/overlays.js keeps all three builders;
 * this file keeps the single rule they must not each own a copy of — because
 * the modal and the slide-over once did, and one of the two copies was broken.
 *
 * It also owns the OVERLAY STACK, which is the answer to a second defect no
 * single overlay could see. Each open overlay binds its own keydown handler on
 * `document`, and every document handler hears every key — so opening the
 * agent form and then a Delete confirm meant one Escape closed both, throwing
 * away a half-filled form to answer a question about something else. The fix
 * cannot be stopPropagation(): document listeners fire in REGISTRATION order,
 * so the first dialog's handler runs first and stopping there dismisses the
 * one the operator cannot even see. Depth is not registration order and it is
 * not DOM order either — the anchored menu lives in its own container — so it
 * is recorded here, and only the top entry may answer a key.
 *
 * Loaded before core/overlays.js in index.html, which is its only consumer.
 */
const BossModOverlayFocus = (() => {
    /** Everything the browser will place in the tab order by default. */
    const FOCUSABLE = [
        'button:not([disabled])', '[href]', 'input:not([disabled])',
        'select:not([disabled])', 'textarea:not([disabled])',
        'summary', '[tabindex]:not([tabindex="-1"])',
    ].join(', ');

    /** The open overlays, oldest first. The LAST one is the one on top. */
    const stack = [];

    /**
     * Where `element` sits in the stack, or -1 if it is not open.
     *
     * @param {HTMLElement} element
     * @returns {number}
     */
    function depthOf(element) {
        for (let i = 0; i < stack.length; i += 1) {
            if (stack[i] === element) return i;
        }
        return -1;
    }

    /**
     * Bind an overlay's keydown handler and record it as the topmost overlay.
     *
     * Binding and stacking are ONE call on purpose: an overlay that listened
     * without registering would answer Esc from underneath the dialog above
     * it, which is the whole defect the stack exists to remove.
     *
     * @param {HTMLElement} element  The overlay root.
     * @param {(event: KeyboardEvent) => void} onKeydown
     * @returns {void}
     */
    function mountOverlay(element, onKeydown) {
        stack.push(element);
        document.addEventListener('keydown', onKeydown);
    }

    /**
     * Unbind it and take it off the stack, at whatever depth it sits.
     *
     * By identity rather than by popping: a caller may close a dialog while
     * another is open above it, and popping would leave the wrong entry on top.
     *
     * @param {HTMLElement} element
     * @param {(event: KeyboardEvent) => void} onKeydown
     * @returns {void}
     * @throws {Error} When the overlay was never mounted. Silently ignoring it
     *   would leave the stack and the listener list disagreeing, and the next
     *   Escape would close a dialog nobody pointed at.
     */
    function unmountOverlay(element, onKeydown) {
        const depth = depthOf(element);
        if (depth === -1) {
            throw new Error('[overlay-focus] closing an overlay that was never mounted');
        }
        stack.splice(depth, 1);
        document.removeEventListener('keydown', onKeydown);
    }

    /**
     * Keep Tab inside one overlay, and let Esc dismiss it.
     *
     * Shared by all three overlays on purpose. The modal and the slide-over
     * previously carried separate implementations and only one of them was a
     * real trap: the modal's cycled over its own action buttons and bailed out
     * when focus was anywhere else, so any focusable content in the body leaked
     * Tab into the page behind.
     *
     * @param {KeyboardEvent} event
     * @param {HTMLElement} element  The overlay root.
     * @param {() => void} close
     * @returns {void}
     * @throws {Error} When `element` was never mounted — a trap wired without
     *   mountOverlay() cannot know whether it is the one on top.
     */
    function trapKeydown(event, element, close) {
        // Every open overlay hears every key. Only the one on top may answer,
        // or a single Escape tears the stack down: the confirm the operator
        // dismissed and the half-filled form behind it, together.
        const depth = depthOf(element);
        if (depth === -1) {
            throw new Error('[overlay-focus] this overlay never mounted; its Esc would close another');
        }
        if (depth !== stack.length - 1) return;
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
        // Focus escaped the overlay — pull it back, do not let Tab walk on.
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

    return { FOCUSABLE, trapKeydown, mountOverlay, unmountOverlay };
})();
