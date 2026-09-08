/**
 * BossMod AI — the one toggle switch.
 *
 * Three surfaces expressed the same idea three ways: the Board's "Show
 * subtasks" was a bare checkbox in a label, the Log's "Follow" was a button
 * that rewrote its own text and carried aria-pressed, and the conversation's
 * receipts preference was a second bare checkbox. Nothing was wrong with any
 * of them individually, which is exactly why the places looked unrelated.
 *
 * It is a <button role="switch"> rather than a checkbox: the concept's control
 * is a pill, a checkbox cannot be styled into one without hiding the real input
 * behind a fake, and `switch` is the role that announces on/off rather than
 * ticked/unticked.
 *
 * TARGET SIZE. The pill is 26x14, well under the 24x24 floor (SC 2.5.8), so the
 * pill is NOT the control. The whole label+pill row is one <button>; the pill
 * inside it is an aria-hidden span. The label text is part of the target, which
 * is what makes the row both legal and easy to hit — and it is asserted on the
 * built node in tests/test_ui_visual_parity.py, not merely claimed here.
 */
const BossModSwitch = (() => {
    const { h } = BossModDom;

    /**
     * Build a labelled toggle.
     *
     * @param {object} deps
     * @param {string} deps.label  Visible text, and the control's accessible
     *   name. Required: an unlabelled switch announces only its state.
     * @param {boolean} [deps.pressed=false]  Initial state.
     * @param {(pressed: boolean) => void} deps.onChange  Called with the new
     *   state after the operator toggles it — never by `set()`, which exists
     *   for the opposite direction.
     * @returns {{element: HTMLElement, set: (pressed: boolean) => void}}
     * @throws {Error} When the label or the handler is missing. Either one
     *   absent is a control that looks live and is not.
     */
    function create(deps) {
        const opts = deps || {};
        const label = String(opts.label || '').trim();
        if (!label) throw new Error('[switch] deps.label is required');
        if (typeof opts.onChange !== 'function') {
            throw new Error('[switch] deps.onChange is required');
        }

        let pressed = opts.pressed === true;

        const element = h('button', {
            class: 'switch-row',
            type: 'button',
            'role': 'switch',
            'aria-checked': pressed ? 'true' : 'false',
            onclick: () => {
                pressed = !pressed;
                element.setAttribute('aria-checked', pressed ? 'true' : 'false');
                opts.onChange(pressed);
            },
        },
            h('span', { class: 'switch', 'aria-hidden': 'true' }),
            h('span', { class: 'switch-label' }, label));

        return {
            element,

            /**
             * Re-sync the control from state it does not own.
             *
             * Deliberately silent: firing onChange here would turn every
             * repaint into an operator action.
             *
             * @param {boolean} next
             * @returns {void}
             */
            set(next) {
                pressed = next === true;
                element.setAttribute('aria-checked', pressed ? 'true' : 'false');
            },
        };
    }

    return { create };
})();
