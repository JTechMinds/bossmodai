/**
 * BossMod AI — one card on the Board.
 *
 * The card is an <article>, not a button: it carries a select checkbox as well
 * as an open action, and nesting a checkbox inside a button is both invalid
 * and unreachable by keyboard. The title is the button; the checkbox is its
 * sibling with a real <label>.
 */
const BossModTaskCard = (() => {
    const { h } = BossModDom;

    /** Statuses whose card carries an explicit marker, and the copy for each. */
    const MARKERS = Object.freeze({
        // Working, but not by the person the column implies. Without this the
        // column would say someone is doing work they have handed on.
        delegated: 'delegated',
    });

    function initial(name) {
        return (String(name || '?').trim()[0] || '?').toUpperCase();
    }

    /**
     * Build one task card.
     *
     * @param {object} task  One task row, as board-data.js loaded it.
     * @param {object} options
     * @param {(taskId: string) => void} options.onOpen
     * @param {(taskId: string, selected: boolean) => void} options.onToggleSelect
     * @param {boolean} [options.selected=false]
     * @param {boolean} [options.selectable=true]  False for a task that can no
     *   longer be cancelled — offering a checkbox that resolves to nothing is
     *   worse than offering none.
     * @param {number} [options.subtaskCount=0]
     * @param {string} [options.parentTitle='']  Set for a subtask, so a child
     *   shown on its own still says what it belongs to. The old table conveyed
     *   this by indenting rows under their parent; columns cannot.
     * @returns {HTMLElement}
     * @throws {Error} When a callback is missing — a card that cannot be opened
     *   or selected is a dead control.
     */
    function renderCard(task, options) {
        const {
            onOpen, onToggleSelect, selected = false, selectable = true,
            subtaskCount = 0, parentTitle = '',
        } = options || {};
        if (typeof onOpen !== 'function') throw new Error('[task-card] options.onOpen is required');
        if (typeof onToggleSelect !== 'function') {
            throw new Error('[task-card] options.onToggleSelect is required');
        }

        const title = task.title || 'Untitled task';
        const age = BossModUtils.formatRelativeTime(task.last_activity);
        const marker = MARKERS[task.status];

        const meta = h('p', { class: 'task-card-meta' },
            h('span', { class: 'task-card-avatar', style: `background:${task.assigned_to_color || ''}` },
                initial(task.assigned_to_name)),
            h('span', { class: 'task-card-owner' }, task.assigned_to_name || 'Unassigned'),
            age ? h('span', { class: 'task-card-age' }, age) : null);

        const open = h('button', {
            class: 'task-card-open',
            type: 'button',
            onclick: () => onOpen(task.id),
        },
            h('span', { class: 'task-card-title' }, title),
            meta,
            task.status_note ? h('span', { class: 'task-card-note' }, task.status_note) : null,
            h('span', { class: 'task-card-tags' },
                h('span', { class: 'status-pill', 'data-status': task.status }, task.status || ''),
                marker ? h('span', { class: 'task-card-marker' }, marker) : null,
                subtaskCount > 0
                    ? h('span', { class: 'task-card-subs' },
                        `${subtaskCount} ${subtaskCount === 1 ? 'subtask' : 'subtasks'}`)
                    : null,
                parentTitle ? h('span', { class: 'task-card-parent' }, `in ${parentTitle}`) : null));

        const article = h('article', {
            class: `task-card${selected ? ' is-selected' : ''}`,
            'data-task-id': task.id,
            'data-status': task.status,
        });

        // Always in the tab order. CSS reveals it on hover, on focus-within,
        // and whenever it is checked — never hover alone (SC 2.1.1).
        //
        // The card paints its own selected state rather than having the place
        // find it again by id: a task id is server-generated, but building a
        // selector out of data is how an id with a quote in it would silently
        // stop matching, and the node is already right here.
        const select = selectable
            ? h('label', { class: 'task-card-select' },
                h('input', {
                    type: 'checkbox',
                    checked: selected,
                    'aria-label': `Select ${title}`,
                    'data-select-task': task.id,
                    onchange: (event) => {
                        article.classList.toggle('is-selected', event.target.checked);
                        onToggleSelect(task.id, event.target.checked);
                    },
                }))
            : null;

        article.append(...[select, open].filter(Boolean));
        return article;
    }

    return { renderCard, MARKERS };
})();
