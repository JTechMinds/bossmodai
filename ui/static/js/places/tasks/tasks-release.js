/**
 * BossMod AI — releasing a held task on the operator's behalf.
 *
 * A held task starts by itself once everything it waits on is complete; the
 * boss can start it sooner, or start one they held by hand. This is the one
 * place in Tasks that releases a task, mirroring tasks-complete.js — a detail
 * asks, this posts, and the detail repaints in place from the row the promise
 * resolves with. Only while a prerequisite is still unfinished does it ask
 * first: starting then means the assignee begins without that work, and the
 * server drops the wait for good.
 *
 * Whether a task can be released at all is the server's call
 * (`operator_can_release` on each listed task); what is unfinished is
 * BossModTasksData.unfinishedPrerequisites, the rule the card and the
 * detail's Waiting on read too.
 */
const BossModTasksRelease = (() => {
    const TITLE_COPY = 'Start it now?';

    /**
     * What the confirm says the task still waits on.
     *
     * @param {Array<{title: string}>} unfinished  At least one.
     * @returns {string} `It is still waiting on "A".`, `… "A" and "B".`, or
     *   `… "A", "B" and "C".`
     */
    function waitingCopy(unfinished) {
        const titles = unfinished.map((row) => `"${row.title}"`);
        const last = titles.pop();
        return `It is still waiting on ${titles.length ? `${titles.join(', ')} and ${last}` : last}.`;
    }

    /**
     * Build a releaser.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(ids: string[]) => void} deps.onReleased  The server accepted.
     * @returns {{releaseOne: (task: object) => Promise<object|null>}}
     * @throws {Error} When a dependency is missing.
     */
    function createReleaser(deps) {
        const { api, onReleased } = deps || {};
        if (typeof api !== 'function') throw new Error('[tasks-release] deps.api is required');
        if (typeof onReleased !== 'function') throw new Error('[tasks-release] deps.onReleased is required');

        /**
         * POST the release.
         * @param {string} taskId
         * @returns {Promise<object>} The stored row, as GET /api/tasks lists it.
         * @throws {Error} (rejects) With the server's `detail` as the message.
         */
        async function post(taskId) {
            let res;
            try {
                res = await api(`/api/tasks/${encodeURIComponent(taskId)}/release`, { method: 'POST' });
            } catch (err) {
                console.error('[tasks-release] the release request failed', err);
                throw new Error(`Could not release: ${(err && err.message) || 'the request failed'}`);
            }
            let body;
            try {
                body = await res.json();
            } catch (err) {
                console.error('[tasks-release] the release response had no JSON body', err);
                throw new Error(`Could not release: HTTP ${res.status}`);
            }
            if (!res.ok) {
                const detail = body && body.detail;
                throw new Error(`Could not release: ${typeof detail === 'string'
                    ? detail : JSON.stringify(detail || `HTTP ${res.status}`)}`);
            }
            onReleased([taskId]);
            return body;
        }

        /**
         * The confirm; its outcome settles the caller's promise.
         * @param {object} task
         * @param {object[]} unfinished  What the task still waits on.
         * @param {(row: object|null) => void} resolve
         * @param {(err: Error) => void} reject
         * @returns {void}
         */
        function confirm(task, unfinished, resolve, reject) {
            let confirmed = false;
            BossModOverlays.createModal({
                title: TITLE_COPY,
                closeOnBackdrop: true,
                body: waitingCopy(unfinished),
                actions: [
                    {
                        label: 'Start now',
                        tone: 'primary',
                        id: 'ct-release-submit',
                        onSelect: () => { confirmed = true; post(task.id).then(resolve, reject); },
                    },
                    { label: 'Cancel', tone: 'quiet' },
                ],
                // Runs after the chosen action's onSelect, however it closed.
                onClose: () => { if (!confirmed) resolve(null); },
            });
        }

        return {
            /**
             * Release at once when nothing it waits on is unfinished (a hold
             * by hand, or every prerequisite complete); otherwise confirm
             * first. Cancel is listed last, as tasks-cancel.js lists its safe
             * choice, so it holds focus.
             *
             * @param {object} task  Only a task the server flagged
             *   `operator_can_release` releases anything.
             * @returns {Promise<object|null>} The stored row once the server
             *   released it (after `onReleased`); null when the operator
             *   cancelled, or the task cannot be released.
             * @throws {Error} (rejects) The server refused, with its `detail`.
             */
            releaseOne(task) {
                if (!task || !task.id || !task.operator_can_release) return Promise.resolve(null);
                const unfinished = BossModTasksData.unfinishedPrerequisites(task);
                if (unfinished.length === 0) return post(task.id);
                return new Promise((resolve, reject) => confirm(task, unfinished, resolve, reject));
            },
        };
    }

    return { createReleaser, TITLE_COPY };
})();
