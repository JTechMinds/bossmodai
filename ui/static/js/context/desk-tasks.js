/**
 * BossMod AI — the Tasks section of an agent's desk.
 *
 * The top few items across both boards the agent appears on: what they are
 * carrying (`scope=self`) and what they own but handed out (`scope=owned`).
 *
 * CONTENT ONLY. The section header and its right-aligned "See all" belong to
 * desk-panel.js, which owns the desk's one section vocabulary — "where all of
 * this agent's tasks live" is a panel-level fact, not something the list that
 * loads five of them should know, and a header authored here is a header that
 * drifts from the three beside it.
 *
 * Split out of desk-panel.js because the panel would otherwise pass the
 * ~300-line cap, and because this is the only part of the desk with a request
 * and a load generation of its own.
 */
const BossModDeskTasks = (() => {
    const { h, clear } = BossModDom;

    /** The desk is a summary; the Tasks place is the list. */
    const TOP_N = 5;

    /**
     * Build the Tasks section.
     *
     * @param {object} deps
     * @param {Function} deps.api      Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @param {(taskId: string) => void} deps.onOpenTask  A row was clicked.
     *   Where a task opens is the panel's decision (a layer over the desk),
     *   not the list's.
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>,
     *   showError: (message: string) => void, destroy: () => void }}
     *   `refresh` re-reads both boards — after a cancel, say — and clears any
     *   error `showError` put up. `showError` is for a failure that happened
     *   AROUND the list rather than in its own read (a task that could not be
     *   opened, a cancel refused): the message goes above the rows as an
     *   alert, and the rows stay, because they are still true.
     * @throws {Error} When api, agentId or onOpenTask is missing.
     */
    function createDeskTasks(deps) {
        const { api, agentId, onOpenTask } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-tasks] deps.api is required');
        if (!agentId) throw new Error('[desk-tasks] deps.agentId is required');
        if (typeof onOpenTask !== 'function') throw new Error('[desk-tasks] deps.onOpenTask is required');

        const load = BossModGates.createLoadGeneration();
        const listEl = h('div', { class: 'desk-tasks' });
        /** The alert showError() put above the rows, or null. Kept by name so
         *  a second error replaces it and refresh() clears it. */
        let errorEl = null;
        let destroyed = false;

        const element = listEl;

        function boardUrl(scope) {
            return `/api/tasks/board?agent_id=${encodeURIComponent(agentId)}&scope=${scope}`;
        }

        /**
         * Flatten a board's sections into a task list.
         * @param {object} board
         * @returns {object[]}
         */
        function rows(board) {
            const sections = (board && board.sections) || {};
            const out = [];
            Object.keys(sections).forEach((key) => {
                const value = sections[key];
                if (Array.isArray(value)) out.push(...value);
            });
            return out;
        }

        /**
         * One task as a row: its title and the shared status pill, the whole
         * row a button into the task. What done means for it is the task
         * detail's (task-detail-sections.js `doneContract()`), one click away,
         * rather than three lines repeated under every open row here.
         *
         * @param {object} task
         * @returns {HTMLElement}
         */
        function card(task) {
            return h('button', {
                class: 'desk-task',
                type: 'button',
                'data-task-id': task.id,
                onclick: () => onOpenTask(task.id),
            },
                h('span', { class: 'desk-task-title' }, String(task.title || 'Untitled task')),
                // The Tasks place's own labels, so a status reads the same
                // in both places.
                h('span', { class: 'status-pill', 'data-status': task.status || null },
                    BossModTasksColumns.STATUS_LABELS[task.status] || String(task.status || '')));
        }

        /**
         * Load both boards and paint the top few.
         *
         * @returns {Promise<void>} Never rejects; a failure becomes the error
         *   state, with a retry, rather than an empty section that reads as
         *   "this agent has nothing to do".
         */
        async function refresh() {
            const loadId = load.next();
            clear(listEl);
            errorEl = null;
            listEl.append(h('p', { class: 'context-skeleton' }, 'Loading tasks…'));

            let boards;
            try {
                const responses = await Promise.all([
                    api(boardUrl('self'), { cache: 'no-store' }),
                    api(boardUrl('owned'), { cache: 'no-store' }),
                ]);
                const failed = responses.find((res) => !res.ok);
                if (failed) throw new Error(`HTTP ${failed.status}`);
                boards = await Promise.all(responses.map((res) => res.json()));
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[desk-tasks] could not load the boards', err);
                clear(listEl);
                listEl.append(
                    h('p', { class: 'context-error', role: 'alert' }, 'Could not load tasks.'),
                    h('button', {
                        class: 'btn btn-sm',
                        type: 'button',
                        onclick: () => { void refresh(); },
                    }, 'Try again'));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;

            const seen = new Set();
            const tasks = [];
            boards.forEach((board) => {
                rows(board).forEach((task) => {
                    if (!task || seen.has(task.id)) return;
                    seen.add(task.id);
                    tasks.push(task);
                });
            });

            clear(listEl);
            if (tasks.length === 0) {
                // A dashed placeholder, so an empty section still reads as a
                // section rather than as a gap that failed to render.
                listEl.append(h('p', { class: 'context-empty empty-slot' },
                    'No tasks for this agent.'));
                return;
            }
            tasks.slice(0, TOP_N).forEach((task) => listEl.append(card(task)));
        }

        void refresh();

        /**
         * Say something went wrong around the list, above its rows.
         *
         * @param {string} message
         * @returns {void}
         * @throws {Error} On an empty message: an alert that says nothing is
         *   a failure nobody can act on.
         */
        function showError(message) {
            if (typeof message !== 'string' || !message.trim()) {
                throw new Error('[desk-tasks] showError needs a message');
            }
            if (errorEl) errorEl.remove();
            errorEl = h('p', { class: 'context-error', role: 'alert' }, message);
            // First in the list's own column, so its gap spaces the alert
            // from the rows it sits above.
            listEl.prepend(errorEl);
        }

        return {
            element,
            refresh,
            showError,

            /**
             * Stop painting; an in-flight board response is dropped.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
            },
        };
    }

    return { createDeskTasks };
})();
