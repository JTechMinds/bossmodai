/**
 * BossMod AI — the rail of filters beside a list of packs.
 *
 * The second half of the browse chrome the marketplace takeover and the Add
 * agent picker share (the first is marketplace/pack-card.js). Both narrow one
 * list of packs by the same two kinds of row — a SCOPE, which decides which
 * packs are on the table at all, and a CATEGORY, which is the catalog's own
 * bucket — and both count what each row would leave.
 *
 * GROUPED, not one flat list. Run together as `All 5 / Installed 0 /
 * Engineering 3 / Product 2`, four rows claim the catalog has four buckets when
 * it has two of one kind and two of another. Each group is a heading over a
 * list, which is the treatment the roster rail already gives `PEOPLE` and
 * `THREADS` — one vocabulary for "these rows are a set" rather than a second
 * one invented here. Real structure and not a gap: the list is NAMED by its
 * heading, so a row announces which group it belongs to. Every row stays a
 * plain button, so the rail is as many tab stops as it has rows and Tab crosses
 * every group in order.
 *
 * SELECTION IS PAINTED IN PLACE, never rebuilt. `select()` moves
 * `aria-current` between rows that are already in the document, so the button
 * the operator just clicked survives its own click and keeps the keyboard. The
 * marketplace rebuilds its whole view per interaction and restores focus by id
 * afterwards; that machinery exists there because the grid and the detail swap
 * under one host, and this rail does not need it.
 *
 * Built with BossModDom.h: a category id and its label are catalog data.
 */
const BossModFilterRail = (() => {
    const { h } = BossModDom;

    /**
     * One row. `at` is its index across the WHOLE rail rather than within its
     * group: the id is what a caller hands focus back to after a rebuild, and a
     * per-group number would name two different rows.
     *
     * @param {{id: string, label: string, count: number}} row
     * @param {number} at
     * @param {object} config  The createRail config, for `idPrefix`, `current`
     *   and `onSelect`.
     * @returns {HTMLElement} An `<li>` holding the button.
     */
    function railRow(row, at, config) {
        return h('li', {},
            h('button', {
                class: 'market-rail-item', type: 'button',
                id: `${config.idPrefix}-${at}`,
                'data-rail-id': row.id,
                'aria-current': config.current === row.id ? 'true' : null,
                onclick: () => config.onSelect(row.id, `#${config.idPrefix}-${at}`),
            },
            h('span', { class: 'market-rail-label' }, row.label),
            h('span', { class: 'market-rail-count' }, String(row.count))));
    }

    /**
     * One heading over one list.
     *
     * @param {string} id  Names the heading, so `aria-labelledby` can point the
     *   list at it.
     * @param {{title: string, rows: object[]}} group
     * @param {number} from  This group's first index across the whole rail.
     * @param {object} config
     * @returns {HTMLElement}
     */
    function railGroup(id, group, from, config) {
        return h('div', { class: 'market-rail-group' },
            h('h3', { class: 'market-rail-title', id }, group.title),
            h('ul', { class: 'market-rail-list', 'aria-labelledby': id },
                group.rows.map((row, at) => railRow(row, from + at, config))));
    }

    /**
     * Build a filter rail.
     *
     * @param {object} config
     * @param {string} config.label  The nav's accessible name.
     * @param {string} config.idPrefix  Namespaces every row id, so two rails on
     *   one page cannot collide. Required.
     * @param {Array<{id: string, title: string, rows: Array<{id: string,
     *   label: string, count: number}>}>} config.groups  Drawn in order. A
     *   group with NO rows is dropped whole — a heading over nothing claims a
     *   group that is not there, and a failed catalog read is exactly when that
     *   happens.
     * @param {string} config.current  The live row's id.
     * @param {(id: string, focusSelector: string) => void} config.onSelect
     *   Given the row's id and the selector of the button that was clicked, so
     *   a caller that DOES rebuild can hand focus back to it.
     * @returns {{element: HTMLElement, select: (id: string) => void}}
     *   `select` repaints `aria-current` in place and is what a caller uses
     *   instead of rebuilding the rail on every click.
     * @throws {Error} Without an idPrefix or an onSelect: rows that collide on
     *   id cannot be focused back, and rows that answer to nobody are dead.
     */
    function createRail(config) {
        const { label, idPrefix, groups, current, onSelect } = config || {};
        if (!idPrefix) throw new Error('[filter-rail] config.idPrefix is required');
        if (typeof onSelect !== 'function') throw new Error('[filter-rail] config.onSelect is required');
        const state = { current, idPrefix, onSelect };
        let at = 0;
        const drawn = (groups || []).filter((group) => (group.rows || []).length)
            .map((group) => {
                const node = railGroup(`${idPrefix}-group-${group.id}`,
                    { title: group.title, rows: group.rows }, at, state);
                at += group.rows.length;
                return node;
            });
        const element = h('nav', { class: 'market-rail', 'aria-label': label }, drawn);
        return {
            element,
            /**
             * Move the live mark to `id`, in place.
             *
             * @param {string} id
             * @returns {void} Silently a no-op for an id the rail does not
             *   hold: a caller filtering to a category that has just left the
             *   library is asking a reasonable question, and the answer is an
             *   empty grid rather than an exception.
             */
            select(id) {
                state.current = id;
                element.querySelectorAll('.market-rail-item').forEach((button) => {
                    const mine = button.getAttribute('data-rail-id') === id;
                    if (mine) button.setAttribute('aria-current', 'true');
                    else button.removeAttribute('aria-current');
                });
            },
        };
    }

    return { createRail };
})();
