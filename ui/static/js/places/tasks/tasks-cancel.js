/**
 * BossMod AI — cancelling tasks from the Tasks place.
 *
 * The one destructive path the Tasks place has, kept in one file so the confirmation
 * cannot be bypassed by a second caller. Both entry points ask first, through
 * the focus-trapped dialog rather than the browser's native one, which cannot
 * be styled, cannot be tested, and blocks the event loop. The desk's task
 * layers cancel through a canceller built here too
 * (context/desk-task-opener.js), so that path asks the same question.
 *
 * The two entry points answer differently. A selection is cancelled in bulk
 * and a failure reaches `onError`. One task is cancelled from its detail,
 * which repaints in place, so `cancelOne` returns a promise of the stored row
 * and a failure rejects for the detail to show.
 */
const BossModTasksCancel = (() => {
    const COLUMNS = BossModTasksColumns;

    const ONE_COPY = 'Cancel this task?';
    const BODY_COPY = 'The assignee is told and the work stops. This cannot be undone.';

    /**
     * Build a canceller.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(ids: string[]) => void} deps.onCancelled  The server accepted.
     * @param {(message: string) => void} deps.onError  A bulk cancel failed;
     *   surfaced, never swallowed. (One task's failure is its promise's.)
     * @returns {{cancelOne: (task: object) => Promise<object|null>,
     *   cancelMany: (tasks: object[]) => void}}
     * @throws {Error} When a dependency is missing.
     */
    function createCanceller(deps) {
        const { api, onCancelled, onError } = deps || {};
        if (typeof api !== 'function') throw new Error('[tasks-cancel] deps.api is required');
        if (typeof onCancelled !== 'function') throw new Error('[tasks-cancel] deps.onCancelled is required');
        if (typeof onError !== 'function') throw new Error('[tasks-cancel] deps.onError is required');

        /**
         * POST a selection's cancellations to the bulk route.
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
                console.error('[tasks-cancel] cancel failed', err);
                onError(`Could not cancel: ${(err && err.message) || 'the request failed'}.`);
            }
        }

        /**
         * POST one task's cancellation.
         * @param {string} taskId
         * @returns {Promise<object>} The stored row, as GET /api/tasks lists it.
         * @throws {Error} (rejects) With the server's `detail` as the message.
         */
        async function postOne(taskId) {
            let res;
            try {
                res = await api(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
            } catch (err) {
                console.error('[tasks-cancel] the cancel request failed', err);
                throw new Error(`Could not cancel: ${(err && err.message) || 'the request failed'}.`);
            }
            let body;
            try {
                body = await res.json();
            } catch (err) {
                console.error('[tasks-cancel] the cancel response had no JSON body', err);
                throw new Error(`Could not cancel: HTTP ${res.status}.`);
            }
            if (!res.ok) {
                const detail = body && body.detail;
                throw new Error(`Could not cancel: ${typeof detail === 'string'
                    ? detail : JSON.stringify(detail || `HTTP ${res.status}`)}`);
            }
            onCancelled([taskId]);
            return body;
        }

        /**
         * Ask, then run `choices.onConfirm(ids)`. The safe choice is listed
         * last, so it holds focus and Esc and Enter agree.
         *
         * @param {string} title
         * @param {string[]} ids  An empty list opens nothing: a dialog that
         *   would cancel nothing is noise.
         * @param {{confirm: string, keep: string, onConfirm: (ids: string[]) => void,
         *   onKeep?: () => void}} choices  The two buttons' words, and what
         *   each outcome runs; `onKeep` runs when the dialog closed without
         *   confirming.
         * @returns {void}
         */
        function confirmCancel(title, ids, choices) {
            if (ids.length === 0) return;
            let confirmed = false;
            BossModOverlays.createModal({
                title,
                closeOnBackdrop: true,
                body: BODY_COPY,
                actions: [
                    {
                        label: choices.confirm,
                        tone: 'danger',
                        onSelect: () => { confirmed = true; choices.onConfirm(ids); },
                    },
                    { label: choices.keep, tone: 'quiet' },
                ],
                // Runs after the chosen action's onSelect, however it closed.
                onClose: () => { if (!confirmed && choices.onKeep) choices.onKeep(); },
            });
        }

        return {
            /**
             * Cancel one task, from its detail panel.
             * @param {object} task  Only an open task opens anything.
             * @returns {Promise<object|null>} The stored row once the server
             *   cancelled it (after `onCancelled`); null when the operator
             *   kept it, or the task has already ended.
             * @throws {Error} (rejects) The server refused, with its `detail`.
             */
            cancelOne(task) {
                if (!task || !task.id || COLUMNS.isTerminal(task.status)) return Promise.resolve(null);
                return new Promise((resolve, reject) => {
                    confirmCancel(ONE_COPY, [task.id], {
                        confirm: 'Cancel task',
                        keep: 'Keep it',
                        onConfirm: () => { postOne(task.id).then(resolve, reject); },
                        onKeep: () => resolve(null),
                    });
                });
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
                confirmCancel(`Cancel ${ids.length} tasks?`, ids, {
                    confirm: 'Cancel tasks',
                    keep: 'Keep them',
                    onConfirm: (selected) => { void post(selected); },
                });
            },
        };
    }

    return { createCanceller, ONE_COPY };
})();
