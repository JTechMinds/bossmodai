/**
 * BossMod AI — the Tasks section of an agent's desk.
 *
 * The top few items across both boards the agent appears on: what they are
 * carrying (`scope=self`) and what they own but handed out (`scope=owned`).
 *
 * CONTENT ONLY. The section header and its right-aligned "See all" belong to
 * desk-panel.js, which owns the desk's one section vocabulary — "where all of
 * this agent's tasks live" is a panel-level fact, not something the list that
 * loads three of them should know, and a header authored here is a header that
 * drifts from the three beside it.
 *
 * Split out of desk-panel.js because the panel would otherwise pass the
 * ~300-line cap, and because this is the only part of the desk with a request
 * and a load generation of its own.
 */
const BossModDeskTasks = (() => {
    const { h, clear } = BossModDom;

    /** The desk is a summary; the Board is the list. */
    const TOP_N = 3;
    const BLOCKED_COPY = 'Blocked — checkable claim missing';
    const NEEDED_COPY = 'What’s needed: tests evidence, an artifact path, or an allow/deny proof.';

    /**
     * Build the Tasks section.
     *
     * @param {object} deps
     * @param {Function} deps.api      Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When api or agentId is missing.
     */
    function createDeskTasks(deps) {
        const { api, agentId } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-tasks] deps.api is required');
        if (!agentId) throw new Error('[desk-tasks] deps.agentId is required');

        const load = BossModGates.createLoadGeneration();
        const listEl = h('div', { class: 'desk-tasks' });
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

        function card(task) {
            const blocked = task.status === 'blocked' || task.status === 'stalled';
            return h('div', { class: 'desk-task', 'data-task-id': task.id },
                h('p', { class: 'desk-task-title' }, String(task.title || 'Untitled task')),
                h('p', { class: 'desk-task-status' }, String(task.status || '')),
                // The operator's only view of what "done" means for this task.
                task.status !== 'complete'
                    ? h('div', { class: 'desk-task-claim' },
                        h('p', { class: 'desk-task-blocked' }, blocked ? BLOCKED_COPY : 'Done claim'),
                        h('p', { class: 'desk-task-guidance' },
                            BossModSpecialty.doneClaimGuidance(task)),
                        h('p', { class: 'desk-task-guidance' }, NEEDED_COPY))
                    : null);
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
                        class: 'desk-files-btn',
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
                listEl.append(h('p', { class: 'context-empty desk-empty' },
                    'No board items for this agent.'));
                return;
            }
            tasks.slice(0, TOP_N).forEach((task) => listEl.append(card(task)));
        }

        void refresh();

        return {
            element,

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
