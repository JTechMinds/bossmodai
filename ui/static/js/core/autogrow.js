/**
 * BossMod AI — a textarea that grows with its text.
 *
 * One home for an algorithm three surfaces carried privately: the agent
 * form's prompt fields (context/agent-form-bindings.js), the task detail's
 * description (places/tasks/task-edit-mode.js) and, missing it, the schedule
 * layer's instructions — which is how a four-row box came to clip a long
 * instruction with no scrollbar. A fourth surface now imports it instead of
 * copying it.
 *
 * The ceiling is CSS, never a number here: a field's `max-height` (the
 * `--field-grow-max` token on `.field-textarea[data-autogrow]` and an editable
 * `.edit-field-multiline`) clamps the height this sets, and the clamp is read
 * back as "the content is taller than the box", which is exactly when the
 * scrollbar is needed.
 *
 * Text wraps to the field's width, so a width change is a height change the
 * operator did not type: page CSS arriving after the form was mounted (the
 * settings sections bind straight after `innerHTML`, before the Tailwind
 * runtime styles them), a window resize, a panel revealed from hidden. `bind`
 * therefore owns one input listener AND one ResizeObserver; `fit` itself is
 * pure measuring with no state.
 */
const BossModAutoGrow = (() => {

    /**
     * Size one textarea to its text, up to its CSS ceiling, and show a
     * scrollbar only past that ceiling.
     *
     * The height is reset to `auto` first: scrollHeight reports the CONTENT
     * height only while the box is not already taller than it, so a field
     * grown once would otherwise never shrink back. Every surface sets
     * `box-sizing: border-box` (base.css), so the box's own border is added
     * back on: a height of scrollHeight alone is two pixels short of the
     * text, which is a permanent scrollbar on every field.
     *
     * @param {HTMLTextAreaElement} textarea
     * @returns {void} Skips a node with no layout — no numeric scrollHeight
     *   (the suite's fake DOM), or one that measures 0 because it is not on
     *   screen (detached, or inside a hidden panel). Sizing it there would
     *   pin a zero height that clips its text once it is shown; it is sized
     *   again by the next input or by its owner after it is revealed.
     */
    function fit(textarea) {
        if (!textarea || !textarea.style || typeof textarea.scrollHeight !== 'number') return;
        textarea.style.height = 'auto';
        const content = textarea.scrollHeight;
        if (!content) {
            textarea.style.height = '';
            return;
        }
        const frame = typeof textarea.offsetHeight === 'number' && typeof textarea.clientHeight === 'number'
            ? Math.max(0, textarea.offsetHeight - textarea.clientHeight) : 0;
        textarea.style.height = `${content + frame}px`;
        // After the CSS max-height clamps it, the box is shorter than its
        // content exactly when the scrollbar is needed. When it fits, the
        // override is cleared rather than set to `hidden`: a later reflow this
        // cannot see (the window narrowing, late-arriving page CSS) can make
        // the text taller than the box, and `hidden` would then clip it with
        // no way to scroll — the defect this module exists to remove.
        textarea.style.overflowY = textarea.scrollHeight > textarea.clientHeight ? 'auto' : '';
    }

    /**
     * Keep one textarea sized to its text as the operator types and as its
     * width changes.
     *
     * Fits once immediately. A caller that changes the value from script
     * (restoring a draft, filling a template) calls the returned `fit`,
     * because a scripted value fires no input event.
     *
     * A ResizeObserver re-fits when the field's content-box width differs
     * from the last width seen — including the first observation, and a
     * field going from 0 wide (hidden) to shown. Widths are compared, not
     * every notification acted on, because `fit` changes the height and that
     * notifies the observer again: re-fitting on height would loop. The fit
     * runs on the next animation frame, not inside the notification: resizing
     * the observed field from its own callback is a layout change the browser
     * cannot deliver in the same frame, and it reports that as a
     * "ResizeObserver loop" error. A field removed from the document without
     * `destroy` (a settings section re-rendered through `innerHTML`)
     * disconnects its own observer on the next notification, so the observer
     * never outlives the field.
     *
     * @param {HTMLTextAreaElement} textarea
     * @returns {{fit: () => void, destroy: () => void}} `destroy` removes the
     *   input listener and disconnects the observer; the field stops growing.
     * @throws {Error} When `textarea` is not a <textarea> — a contenteditable
     *   or an input has no rows to grow, and binding one would silently do
     *   nothing.
     */
    function bind(textarea) {
        if (!textarea || String(textarea.tagName).toUpperCase() !== 'TEXTAREA') {
            throw new Error('[autogrow] bind takes a textarea element');
        }
        const onInput = () => fit(textarea);
        let lastWidth = null;
        let frame = null;
        const observer = new ResizeObserver((entries) => {
            if (!textarea.isConnected) {
                destroy();
                return;
            }
            const width = entries[entries.length - 1].contentRect.width;
            if (width === lastWidth) return;
            lastWidth = width;
            // A 0-wide field is not on screen; it has no wrap to measure, and
            // the change back to a real width is the one that refits it.
            if (width <= 0 || frame !== null) return;
            frame = requestAnimationFrame(() => {
                frame = null;
                fit(textarea);
            });
        });
        function destroy() {
            textarea.removeEventListener('input', onInput);
            observer.disconnect();
            if (frame !== null) cancelAnimationFrame(frame);
            frame = null;
        }
        textarea.addEventListener('input', onInput);
        observer.observe(textarea);
        fit(textarea);
        return { fit: () => fit(textarea), destroy };
    }

    return { fit, bind };
})();
