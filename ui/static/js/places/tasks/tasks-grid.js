/**
 * BossMod AI — the Tasks place's four columns, as DOM.
 *
 * Takes already-grouped data and a card renderer, and returns nodes. It makes
 * no request, holds no state, and knows nothing about selection. Done holds
 * closed-without-completing work beside completed work, so the one rule this
 * file owns about honesty is that the Done heading counts only the latter.
 */
const BossModTasksGrid = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModTasksColumns;

    function header(column, count) {
        return h('h2', { class: 'tasks-column-title' },
            column.label,
            h('span', { class: 'tasks-column-count' }, String(count)));
    }

    /**
     * Finished work under the day it finished on.
     *
     * One label per run of cards that share a `formatDayLabel(closed_at)`, in
     * the order the tasks arrive — so newest-first and oldest-first both read
     * as runs, and Archive uses exactly the same grouping as Done.
     *
     * @param {object[]} tasks  Finished tasks, already sorted.
     * @param {(task: object) => HTMLElement} renderCard
     * @returns {HTMLElement} `div.tasks-days`.
     * @throws {Error} When renderCard is missing, or a task has no closed_at:
     *   groupIntoColumns reports those as undated, so one reaching here is a
     *   caller that skipped it, and filing it under a guessed day would lie.
     */
    function renderDayGroups(tasks, renderCard) {
        if (typeof renderCard !== 'function') {
            throw new Error('[tasks-grid] renderCard is required');
        }
        const days = h('div', { class: 'tasks-days' });
        let current = null;
        tasks.forEach((task) => {
            const label = BossModFormat.formatDayLabel(task.closed_at);
            if (!label) throw new Error(`[tasks-grid] task "${task.id}" has no finish time`);
            if (label !== current) {
                days.append(h('h3', { class: 'tasks-day-label' }, label));
                current = label;
            }
            days.append(renderCard(task));
        });
        return days;
    }

    /**
     * Build the four columns.
     *
     * @param {{columns: object}} grouped  From BossModTasksData.groupIntoColumns.
     * @param {object} options
     * @param {(task: object) => HTMLElement} options.renderCard
     * @param {string} options.windowPhrase  The Done window's phrase ("this
     *   week"), which finishes Done's empty copy.
     * @param {number} options.olderCount  Finished tasks past the window.
     * @param {() => void} options.onOpenArchive  What `{n} older` opens.
     * @returns {HTMLElement} `div.tasks-columns`.
     * @throws {Error} When a callback or the phrase is missing.
     */
    function renderGrid(grouped, options) {
        const { renderCard, windowPhrase, olderCount, onOpenArchive } = options || {};
        if (typeof renderCard !== 'function') {
            throw new Error('[tasks-grid] options.renderCard is required');
        }
        if (typeof onOpenArchive !== 'function') {
            throw new Error('[tasks-grid] options.onOpenArchive is required');
        }
        if (!windowPhrase) throw new Error('[tasks-grid] options.windowPhrase is required');
        const grid = h('div', { class: 'tasks-columns' });
        COLUMNS.COLUMNS.forEach((column) => {
            const rows = grouped.columns[column.id];
            // A closed-without-completing task sits in Done, marked, and is
            // never counted as done — the same rule counts() keeps.
            const count = column.id === 'done'
                ? rows.filter((task) => !COLUMNS.isClosedWithoutCompleting(task.status)).length
                : rows.length;
            const section = h('section', { class: 'tasks-column', 'data-column': column.id },
                header(column, count));
            if (rows.length === 0) {
                const copy = column.id === 'done' ? `${column.empty} ${windowPhrase}` : column.empty;
                section.append(h('p', { class: 'empty-slot tasks-column-empty' }, copy));
            } else if (column.id === 'done') {
                section.append(renderDayGroups(rows, renderCard));
            } else {
                rows.forEach((task) => section.append(renderCard(task)));
            }
            if (column.id === 'done' && olderCount > 0) {
                section.append(h('button', {
                    class: 'btn-link tasks-older', type: 'button', onclick: onOpenArchive,
                }, `${olderCount} older`));
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
        return h('div', { class: 'tasks-columns' },
            ...COLUMNS.COLUMNS.map((column) =>
                h('section', { class: 'tasks-column is-skeleton' }, header(column, 0))));
    }

    return { renderGrid, renderDayGroups, renderSkeleton };
})();
