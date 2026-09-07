/**
 * BossMod AI — the Board's four columns, as DOM.
 *
 * Takes already-grouped data and a card renderer, and returns nodes. It makes
 * no request, holds no state, and knows nothing about selection — which is why
 * the "Closed without completing" disclosure can live here without any risk of
 * its rows leaking into the Done count.
 */
const BossModBoardGrid = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModBoardColumns;

    function header(column, count) {
        return h('h2', { class: 'board-column-title' },
            column.label,
            h('span', { class: 'board-column-count' }, String(count)));
    }

    /**
     * The Done column's disclosure.
     *
     * These tasks are inside the Done column but beneath a <details>, and their
     * count is its own — folding them into Done would report a cancelled task
     * as a completed one (spec 6.3).
     *
     * @param {object[]} rows
     * @param {(task: object) => HTMLElement} renderCard
     * @returns {HTMLElement}
     */
    function disclosure(rows, renderCard) {
        return h('details', { class: 'board-closed' },
            h('summary', { class: 'board-closed-summary' },
                `${COLUMNS.CLOSED_LABEL} (${rows.length})`),
            ...rows.map(renderCard));
    }

    /**
     * Build the four columns.
     *
     * @param {{columns: object, closed: object[]}} grouped  From
     *   BossModBoardData.groupIntoColumns.
     * @param {(task: object) => HTMLElement} renderCard
     * @returns {HTMLElement}
     * @throws {Error} When renderCard is missing.
     */
    function renderGrid(grouped, renderCard) {
        if (typeof renderCard !== 'function') {
            throw new Error('[board-grid] renderCard is required');
        }
        const grid = h('div', { class: 'board-columns' });
        COLUMNS.COLUMNS.forEach((column) => {
            const rows = grouped.columns[column.id] || [];
            const section = h('section', { class: 'board-column', 'data-column': column.id },
                header(column, rows.length));
            if (rows.length === 0) {
                section.append(h('p', { class: 'board-column-empty' }, 'Nothing here'));
            } else {
                rows.forEach((task) => section.append(renderCard(task)));
            }
            if (column.id === 'done' && grouped.closed.length > 0) {
                section.append(disclosure(grouped.closed, renderCard));
            }
            grid.append(section);
        });
        return grid;
    }

    /**
     * The loading state: the final layout, empty, rather than a spinner.
     * @returns {HTMLElement}
     */
    function renderSkeleton() {
        return h('div', { class: 'board-columns' },
            ...COLUMNS.COLUMNS.map((column) =>
                h('section', { class: 'board-column is-skeleton' }, header(column, 0))));
    }

    return { renderGrid, renderSkeleton };
})();
