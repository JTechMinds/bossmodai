/**
 * BossMod AI — cancelling tasks from the Board.
 *
 * The one destructive path the Board has, kept in one file so the confirmation
 * cannot be bypassed by a second caller. Both entry points ask first, through
 * the focus-trapped dialog rather than the browser's native one, which cannot
 * be styled, cannot be tested, and blocks the event loop.
 */
const BossModBoardCancel = (() => {
    const COLUMNS = BossModBoardColumns;

    const ONE_COPY = 'Cancel this task?';
    const BODY_COPY = 'The assignee is told and the work stops. This cannot be undone.';

    /**
     * Build a canceller.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(ids: string[]) => void} deps.onCancelled  The server accepted.
     * @param {(message: string) => void} deps.onError  Surfaced, never swallowed.
     * @returns {{cancelOne: (task: object) => void, cancelMany: (tasks: object[]) => void}}
     * @throws {Error} When a dependency is missing.
     */
    function createCanceller(deps) {
        const { api, onCancelled, onError } = deps || {};
        if (typeof api !== 'function') throw new Error('[board-cancel] deps.api is required');
        if (typeof onCancelled !== 'function') throw new Error('[board-cancel] deps.onCancelled is required');
        if (typeof onError !== 'function') throw new Error('[board-cancel] deps.onError is required');

        /**
         * POST the cancellations.
         * @param {string[]} ids
         * @returns {Promise<void>} Never rejects; a failure reaches onError.
         */
        async function post(ids) {
            try {
                const res = await api('/api/tasks/cancel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_ids: ids }),
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                onCancelled(ids);
            } catch (err) {
                console.error('[board-cancel] cancel failed', err);
                onError(`Could not cancel: ${(err && err.message) || 'the request failed'}.`);
            }
        }

        /**
         * Ask, then post. The safe choice is listed last, so it holds focus and
         * Esc and Enter agree.
         *
         * @param {string} title
         * @param {string[]} ids  An empty list opens nothing: a dialog that
         *   would cancel nothing is noise.
         * @returns {void}
         */
        function confirmCancel(title, ids) {
            if (ids.length === 0) return;
            BossModOverlays.createModal({
                title,
                body: BODY_COPY,
                actions: [
                    { label: 'Cancel tasks', tone: 'danger', onSelect: () => { void post(ids); } },
                    { label: 'Keep them', tone: 'quiet' },
                ],
            });
        }

        return {
            /**
             * Cancel one task, from its detail panel.
             * @param {object} task
             * @returns {void}
             */
            cancelOne(task) {
                if (!task || !task.id || COLUMNS.isTerminal(task.status)) return;
                confirmCancel(ONE_COPY, [task.id]);
            },

            /**
             * Cancel a selection. Tasks that already ended are dropped rather
             * than sent, because the server would only reject them.
             * @param {object[]} tasks
             * @returns {void}
             */
            cancelMany(tasks) {
                const ids = (tasks || [])
                    .filter((task) => task && !COLUMNS.isTerminal(task.status))
                    .map((task) => task.id);
                confirmCancel(`Cancel ${ids.length} tasks?`, ids);
            },
        };
    }

    return { createCanceller, ONE_COPY };
})();
