/**
 * BossMod AI — one card in Tasks.
 *
 * The card is an <article>, not a button: it carries a select checkbox and an
 * "Open chat" action as well as the open action, and nesting either inside a
 * button is both invalid and unreachable by keyboard. The title block is the
 * button; the checkbox and the action row are its siblings.
 *
 * No status pill: the column says what state the task is in. What the card
 * adds is who, how long, and what it carries.
 */
const BossModTaskCard = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModTasksColumns;

    /** Statuses whose card carries an explicit marker, and the copy for each. */
    const MARKERS = Object.freeze({
        // Working, but not by the person the column implies. Without this the
        // column would say someone is doing work they have handed on.
        delegated: 'delegated',
    });

    /** A glyph with its count, and the same fact in words for a screen reader. */
    function glyph(icon, title, visible, spoken) {
        return h('span', { class: 'task-card-glyph', title },
            h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }),
            visible,
            h('span', { class: 'visually-hidden' }, spoken));
    }

    /** What the task carries: deliverables, and how far its subtasks are. */
    function glyphs(task, progress) {
        const files = (task.work_contract && task.work_contract.deliverables) || [];
        const nodes = [];
        if (files.length > 0) {
            const words = `${files.length} ${files.length === 1 ? 'deliverable' : 'deliverables'}`;
            nodes.push(glyph('file-text', words, null, words));
        }
        if (progress && progress.total > 0) {
            nodes.push(glyph('list-checks', `${progress.done} of ${progress.total} subtasks done`,
                `${progress.done}/${progress.total}`, ' subtasks done'));
        }
        return nodes.length ? h('span', { class: 'task-card-glyphs' }, ...nodes) : null;
    }

    /**
     * Build one task card.
     *
     * @param {object} task  One task row, as tasks-data.js loaded it.
     * @param {object} options
     * @param {(taskId: string) => void} options.onOpen
     * @param {(taskId: string, selected: boolean) => void} options.onToggleSelect
     * @param {(task: object) => void} [options.onOpenChat]  Offered on a Needs
     *   card whose task has somewhere to chat (BossModTasksData.chatTargetFor);
     *   absent, the card renders no button rather than one that does nothing.
     * @param {boolean} [options.selected=false]
     * @param {boolean} [options.selectable=true]  False for a task that can no
     *   longer be cancelled — offering a checkbox that resolves to nothing is
     *   worse than offering none.
     * @param {{done: number, total: number}|null} [options.progress=null]  This
     *   task's subtasks, from BossModTasksData.subtaskProgress.
     * @param {string} [options.parentTitle='']  Set for a subtask, so a child
     *   shown on its own still says what it belongs to.
     * @param {(agentId: string) => (string|undefined)} options.colorOf  The
     *   assignee's roster colour; undefined is the avatar's neutral treatment.
     * @returns {HTMLElement}
     * @throws {Error} When a required callback is missing, or onOpenChat is
     *   given but is not a function — a card that cannot be opened or selected
     *   is a dead control.
     */
    function renderCard(task, options) {
        const {
            onOpen, onToggleSelect, onOpenChat = null, selected = false, selectable = true,
            progress = null, parentTitle = '', colorOf,
        } = options || {};
        if (typeof onOpen !== 'function') throw new Error('[task-card] options.onOpen is required');
        if (typeof onToggleSelect !== 'function') {
            throw new Error('[task-card] options.onToggleSelect is required');
        }
        if (typeof colorOf !== 'function') throw new Error('[task-card] options.colorOf is required');
        if (onOpenChat !== null && typeof onOpenChat !== 'function') {
            throw new Error('[task-card] options.onOpenChat must be a function');
        }

        const title = task.title || 'Untitled task';
        const closed = COLUMNS.isClosedWithoutCompleting(task.status);
        // Finished work is aged from when it finished, the same clock Done
        // sorts and groups by; a late heartbeat must not make it "2m ago".
        const age = BossModFormat.formatRelativeTime(
            COLUMNS.isTerminal(task.status) ? task.closed_at : task.last_activity);
        const marker = MARKERS[task.status];

        // Whether this task is waiting on the operator, asked of the one file
        // that knows — tasks-columns.js, whose map test_ui_tasks.py proves
        // total against the engine's TaskStatus. Spelling `blocked` and
        // `stalled` again here, or in a CSS selector on data-status, would be a
        // second copy that stops matching the day the engine adds a third.
        const needsYou = BossModTasksColumns.columnFor(task.status) === 'needs';

        const meta = h('span', { class: 'task-card-meta' },
            // An unassigned task has no colour, which tintFor() renders as the
            // neutral pair rather than as a missing circle.
            BossModAvatar.create({
                name: task.assigned_to_name,
                color: task.assigned_to ? colorOf(task.assigned_to) : undefined,
                size: 'chip',
            }),
            h('span', { class: 'task-card-owner' }, task.assigned_to_name || 'Unassigned'),
            closed
                ? h('span', { class: 'task-card-closed' }, COLUMNS.CLOSED_NOTE)
                : (age ? h('span', { class: 'task-card-age' }, age) : null),
            marker ? h('span', { class: 'task-card-marker' }, marker) : null);

        const note = task.status_note ? ` · ${task.status_note}` : '';
        const open = h('button', {
            class: 'task-card-open',
            type: 'button',
            onclick: () => onOpen(task.id),
        },
            h('span', { class: 'task-card-title' }, title),
            needsYou
                ? h('span', { class: 'task-card-note' }, `${COLUMNS.STATUS_LABELS[task.status]}${note}`)
                : null,
            parentTitle ? h('span', { class: 'task-card-parent' }, `in ${parentTitle}`) : null,
            h('span', { class: 'task-card-foot' }, meta, glyphs(task, progress)));

        const article = h('article', {
            class: `task-card${selected ? ' is-selected' : ''}`,
            'data-task-id': task.id,
            'data-status': task.status,
            'data-needs': needsYou ? 'true' : null,
            'data-closed': COLUMNS.isClosedWithoutCompleting(task.status) ? 'true' : null,
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

        // A sibling of the open button, never inside it: a button in a button
        // is invalid, and the inner one would be unreachable.
        const actions = needsYou && onOpenChat && BossModTasksData.chatTargetFor(task)
            ? h('div', { class: 'task-card-actions' },
                h('button', {
                    class: 'btn btn-sm', type: 'button', onclick: () => onOpenChat(task),
                }, 'Open chat'))
            : null;

        article.append(...[select, open, actions].filter(Boolean));
        return article;
    }

    return { renderCard, MARKERS };
})();
