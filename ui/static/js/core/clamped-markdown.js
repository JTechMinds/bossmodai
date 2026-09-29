/**
 * BossMod AI — markdown clamped to six lines, with a "show more" that appears
 * only when there is more.
 *
 * Extracted on its SECOND use. The task detail's instructions were the first:
 * rendered as markdown, clamped until measured, and offered in full through a
 * quiet link only if the clamp actually cut something off. The agent desk's
 * description is the second, and a copy of that measure-then-reveal rule in a
 * second file is how the two would come to disagree about what "overflows"
 * means.
 *
 * The clamp is CSS (controls.css, `.clamped-md > .is-clamped`); this module
 * decides whether it stays. It cannot decide at build time: a node that is not
 * on screen has no height, so the caller calls `measure()` once the block is
 * mounted.
 */
const BossModClampedMarkdown = (() => {
    const { h } = BossModDom;

    /**
     * Build one clamped markdown block.
     *
     * @param {object} options
     * @param {string} options.text  Markdown source. Rendered through
     *   BossModMarkdown, which parses inertly and sanitises — agent output is
     *   untrusted.
     * @param {string} [options.className]  Extra class(es) for the text node,
     *   so a surface keeps its own hook (the task detail's
     *   `task-detail-instructions`).
     * @param {string} options.moreLabel  What the reveal link says.
     * @returns {{ element: HTMLElement, measure: () => void }} `element` is
     *   `div.clamped-md > (div.md.is-clamped, button.btn-link.clamped-more)`,
     *   the button hidden until measured — a toggle for text that fits would
     *   promise more than there is. `measure()` decides the clamp: text that
     *   fits loses the clamp and the button; text that overflows shows the
     *   button, which removes both on click. Measuring again after either
     *   outcome is a no-op.
     * @throws {Error} When `text` is empty — an empty clamp would draw a link
     *   to nothing — or `moreLabel` is missing.
     */
    function create({ text, className, moreLabel } = {}) {
        if (typeof text !== 'string' || !text.trim()) {
            throw new Error('[clamped-markdown] text is required');
        }
        if (typeof moreLabel !== 'string' || !moreLabel.trim()) {
            throw new Error('[clamped-markdown] moreLabel is required');
        }
        const body = h('div', { class: className ? `md is-clamped ${className}` : 'md is-clamped' },
            BossModMarkdown.render(text));
        /** Set once the clamp has been decided or lifted, so measure is idempotent. */
        let settled = false;
        const more = h('button', {
            class: 'btn-link clamped-more',
            type: 'button',
            onclick: () => {
                settled = true;
                body.classList.remove('is-clamped');
                more.remove();
            },
        }, moreLabel);
        more.hidden = true;
        const element = h('div', { class: 'clamped-md' }, body, more);

        /**
         * Decide the clamp once the block is on screen and has a height.
         * @returns {void}
         */
        function measure() {
            if (settled) return;
            // One pixel of slack: line boxes round, and a text that fits
            // exactly can report a scrollHeight a pixel over its box.
            if (body.scrollHeight <= body.clientHeight + 1) {
                settled = true;
                body.classList.remove('is-clamped');
                more.remove();
                return;
            }
            settled = true;
            more.hidden = false;
        }

        return { element, measure };
    }

    return { create };
})();
