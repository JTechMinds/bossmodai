/**
 * BossMod AI — the search field a toolbar puts first.
 *
 * A magnifier and a text input in one bordered box, so the field says what it
 * is before anything is typed. Tasks, its Archive and the Log all search this
 * way; it is one builder because three hand-built copies of "icon, input, one
 * box around both" are three chances for the box and the focus ring to drift.
 *
 * The box, not the input, carries the border and the focus ring: the input is
 * borderless inside it, so the ring goes round the glyph too and the two read
 * as one control.
 */
const BossModSearchField = (() => {
    const { h } = BossModDom;

    /**
     * Build a search field.
     *
     * @param {object} deps
     * @param {string} deps.placeholder  What to type, on screen.
     * @param {string} deps.label  The input's accessible name. The glyph is
     *   decorative, so this is what a screen reader hears.
     * @param {(event: Event) => void} deps.onInput  Every keystroke. Callers
     *   that re-read or repaint debounce here; the field does not guess a delay.
     * @param {string} [deps.className]  Extra class on the box, for the width a
     *   caller's layout wants.
     * @returns {{element: HTMLElement, input: HTMLInputElement}} `element` is the
     *   box to place; `input` is what to read `.value` from.
     * @throws {Error} When the placeholder, the label or onInput is missing — a
     *   field nobody can name, or that reports to nobody, is a dead control.
     */
    function create(deps) {
        const { placeholder, label, onInput, className = '' } = deps || {};
        if (!placeholder) throw new Error('[search-field] deps.placeholder is required');
        if (!label) throw new Error('[search-field] deps.label is required');
        if (typeof onInput !== 'function') throw new Error('[search-field] deps.onInput is required');

        const input = h('input', {
            type: 'search',
            class: 'search-field-input',
            placeholder,
            'aria-label': label,
            oninput: onInput,
        });
        // A <label> round the pair, so a click on the glyph lands in the input.
        const element = h('label', { class: className ? `search-field ${className}` : 'search-field' },
            h('i', { 'data-lucide': 'search', 'aria-hidden': 'true' }),
            input);
        return { element, input };
    }

    return { create };
})();
