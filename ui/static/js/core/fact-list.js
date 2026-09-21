/**
 * BossMod AI — a list of labelled facts.
 *
 * Label/value pairs are a <dl>. Promoted out of the Log's diagnostic detail
 * when the task detail became the second surface to need one: two private
 * copies of one layout is how a Log fact and a task fact drift apart.
 */
const BossModFactList = (() => {
    const { h } = BossModDom;
    const LAYOUTS = Object.freeze([1, 2]);

    /**
     * Build the list.
     *
     * @param {Array<{label: string, value: string|Node}>} facts  Already
     *   filtered: the caller knows what "nothing to say" means for its data.
     * @param {object} [options]
     * @param {1|2} [options.pairsPerRow=1]  Two puts label/value pairs side
     *   by side, which is what a short record like a task's people reads as.
     * @returns {HTMLElement} `dl.fact-list`.
     * @throws {Error} On a non-array, an unlabelled fact, or an unknown layout.
     */
    function create(facts, options) {
        const { pairsPerRow = 1 } = options || {};
        if (!Array.isArray(facts)) throw new Error('[fact-list] facts must be an array');
        if (LAYOUTS.indexOf(pairsPerRow) === -1) {
            throw new Error(`[fact-list] unknown pairsPerRow "${pairsPerRow}"`);
        }
        const list = h('dl', { class: 'fact-list', 'data-pairs': String(pairsPerRow) });
        facts.forEach((fact) => {
            if (!fact || !fact.label) throw new Error('[fact-list] every fact needs a label');
            list.append(
                h('dt', { class: 'fact-label' }, fact.label),
                h('dd', { class: 'fact-value' }, fact.value));
        });
        return list;
    }

    return { create };
})();
