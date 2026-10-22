/**
 * BossMod AI — a task's checklist, as the detail and the card draw it.
 *
 * The checklist is the assignee's own plan for its task, in numbered parts
 * it checks off (core/tasking/checklist.py). Tasks shows it read-only and
 * verbatim: the text and notes are the agent's own words, exactly what it
 * reads under Current Task, so nothing here rewrites or fills them in.
 *
 * `row()` is also the Subtasks section's row: both are a state glyph, a
 * title, and what the state is in words for a screen reader.
 */
const BossModTaskChecklist = (() => {
    const { h } = BossModDom;

    /** Each item status: its glyph, and the word a screen reader hears. */
    const STATES = Object.freeze({
        open: Object.freeze({ icon: 'circle', label: 'Open' }),
        done: Object.freeze({ icon: 'check-circle-2', label: 'Done' }),
        skipped: Object.freeze({ icon: 'circle-slash', label: 'Skipped' }),
    });

    /**
     * One checklist row: the state glyph, then the title with its note under it.
     *
     * @param {object} spec
     * @param {string} spec.state  The row's `data-state`; places.css tones the
     *   glyph and strikes the title by it.
     * @param {string} spec.icon  A lucide icon name.
     * @param {string} spec.title
     * @param {string|null} [spec.note]  Shown under the title when given.
     * @param {string} spec.statusLabel  Said after the title to a screen
     *   reader: the glyph and the strike are visual only.
     * @param {() => void} [spec.onClick]  Makes the row a button that runs it.
     * @returns {HTMLElement} `button.task-detail-check` with `onClick`, else
     *   `div.task-detail-check`.
     */
    function row({ state, icon, title, note, statusLabel, onClick }) {
        const attrs = { class: 'task-detail-check', 'data-state': state };
        if (onClick) Object.assign(attrs, { type: 'button', onclick: onClick });
        return h(onClick ? 'button' : 'div', attrs,
            h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }),
            // Phrasing content only, so the same row is valid inside a button.
            h('span', { class: 'task-detail-check-body' },
                h('span', { class: 'task-detail-check-title' }, title),
                note ? h('span', { class: 'task-detail-meta' }, note) : null),
            h('span', { class: 'visually-hidden' }, ` — ${statusLabel}`));
    }

    /**
     * The task's checklist items. Every row the server sends carries the list
     * (Task.checklist defaults to []); like `references`, a row built without
     * it has no checklist.
     */
    function itemsOf(task) {
        return task.checklist || [];
    }

    /**
     * How far the checklist is.
     *
     * @param {object} task
     * @returns {{resolved: number, total: number}} resolved counts the items
     *   done or skipped.
     */
    function progress(task) {
        const items = itemsOf(task);
        return { resolved: items.filter((item) => item.status !== 'open').length, total: items.length };
    }

    /**
     * Every checklist item as a read-only row, in item order.
     *
     * @param {object} task
     * @returns {HTMLElement[]} `[]` without a checklist.
     * @throws {Error} When an item has no text or an unknown status: a row
     *   with a made-up title or state would not be what the agent reads.
     */
    function rows(task) {
        return itemsOf(task).map((item, index) => {
            const state = STATES[item.status];
            if (!state || typeof item.text !== 'string' || !item.text) {
                throw new Error(`[task-checklist] item ${index + 1} of task "${task.id}" is malformed`);
            }
            return row({
                state: item.status,
                icon: state.icon,
                title: item.text,
                note: item.note || null,
                statusLabel: state.label,
            });
        });
    }

    return { row, progress, rows };
})();
