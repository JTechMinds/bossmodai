/**
 * BossMod AI — one Log row.
 *
 * ONE renderer for both feeds. It reads only the LogRow shape, so there is no
 * branch anywhere in this file on where a row came from: an activity entry and
 * a diagnostic summary produce the same node with the same affordances. The
 * only thing it asks is whether the row has more to show, and that is a
 * property of the row, not of its feed.
 *
 * Colour is a `data-type` attribute resolved in places.css against tokens.css.
 * activity.js carried a twenty-five entry map of Tailwind colour classes; the
 * four types the filter bar offers are the map now, and it lives in one place.
 */
const BossModLogRow = (() => {
    const { h } = BossModDom;

    /** Wall-clock time, to the second, as the dock-era feed showed it. */
    function clockTime(iso) {
        if (!iso) return '';
        const date = new Date(iso);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleTimeString([], {
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
    }

    /**
     * Build one row.
     *
     * @param {object} row  A LogRow.
     * @param {object} deps
     * @param {boolean} deps.expanded
     * @param {(row: object) => void} deps.onToggle  Called with the row when
     *   the operator opens or closes it.
     * @param {HTMLElement|null} [deps.detail]  The expansion, when open. It is
     *   appended INSIDE this row (spec 6.6): a diagnostic never opens a second
     *   view.
     * @returns {HTMLElement}
     */
    function renderRow(row, { expanded, onToggle, detail }) {
        // Four cells: when, who, what, and the trailing state. The type dot
        // lives inside the time cell rather than taking a fifth column — it
        // colours the type, and the timestamp is a timestamp OF that type. The
        // Active pill and the meta share the trailing cell so that a row
        // without a pill still lines its meta up with the rows that have one.
        const head = h('div', { class: 'log-row-head' },
            h('span', { class: 'log-row-time' },
                h('span', { class: 'log-row-dot', 'aria-hidden': 'true' }),
                clockTime(row.at)),
            h('span', { class: 'log-row-agent' }, row.agentName),
            h('span', { class: 'log-row-text' }, row.text),
            h('span', { class: 'log-row-tail' },
                row.active ? h('span', { class: 'log-row-active' }, 'Active') : null,
                h('span', { class: 'log-row-meta' }, row.meta)));

        const element = h('article', {
            class: `log-row${expanded ? ' is-expanded' : ''}`,
            'data-type': row.type,
            'data-key': row.key,
        });

        if (!row.expandable) {
            // Not a button: there is nothing to press. A control that renders
            // and does nothing is worse than one that is absent.
            element.append(h('div', { class: 'log-row-static' }, head));
            return element;
        }

        const toggle = h('button', {
            class: 'log-row-toggle',
            type: 'button',
            'aria-expanded': expanded ? 'true' : 'false',
            onclick: () => onToggle(row),
        }, head);
        element.append(toggle);
        if (expanded && detail) element.append(detail);
        return element;
    }

    /**
     * The empty state, worded for whether a filter is hiding everything.
     *
     * @param {boolean} filtered
     * @returns {HTMLElement}
     */
    function renderEmpty(filtered) {
        return h('div', { class: 'place-empty' },
            h('p', { class: 'place-empty-title' },
                filtered ? 'Nothing matches those filters' : 'Nothing has happened yet'),
            h('p', { class: 'place-empty-hint' },
                filtered
                    ? 'Clear the agent, type or search filter to see everything again.'
                    : 'Agent turns, task events and errors all arrive here as they happen.'));
    }

    /**
     * The loading state: rows in the shape of rows.
     * @returns {HTMLElement}
     */
    function renderSkeleton() {
        const list = h('div', { class: 'log-list is-skeleton', 'aria-hidden': 'true' });
        for (let i = 0; i < 8; i += 1) {
            list.append(h('article', { class: 'log-row is-skeleton' },
                h('span', { class: 'log-skeleton-bar' })));
        }
        return list;
    }

    return { renderRow, renderEmpty, renderSkeleton, clockTime };
})();
