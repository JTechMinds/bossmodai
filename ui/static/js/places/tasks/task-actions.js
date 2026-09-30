/**
 * BossMod AI — the operator's task actions, composed once.
 *
 * Cancel (tasks-cancel.js), Mark complete (tasks-complete.js), Resume and
 * Update (the task detail's Edit mode, task-edit-mode.js) share one pair of
 * outcomes: the server accepted, so the caller refreshes; or it did not, so
 * someone says why. Every one-task action resolves with the stored row, so
 * the detail that asked repaints in place; only a bulk cancel reports its
 * failure through `onError`. Two callers open task details — the Tasks place and the desk
 * (context/desk-task-opener.js) — and each wires this one object into
 * BossModTaskLayers rather than three.
 */
const BossModTaskActions = (() => {
    /**
     * An update refusal, typed so the edit mode can tell a specialty
     * mismatch (which it offers to override) from any other failure.
     *
     * @param {string} message
     * @param {object} fields  `kind`, and for a mismatch `reason`/`suggested`.
     * @returns {Error}
     */
    function refusal(message, fields) {
        return Object.assign(new Error(message), fields);
    }

    /**
     * Build the action set.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Application store; `roster()` reads who can
     *   be assigned from it.
     * @param {(ids: string[]) => void} deps.onChanged  The server accepted a
     *   cancel, a completion or a resume of these tasks. Only for refreshing
     *   a list: the detail that asked repaints itself in place.
     * @param {(row: object) => void} deps.onUpdated  The server accepted an
     *   update and returned this row. Only for refreshing a list: the detail
     *   that asked re-renders itself in place, so nothing is closed here.
     * @param {(message: string) => void} deps.onError  A bulk cancel failed
     *   after its dialog closed. (A one-task action's failure is its rejected
     *   promise's, shown by the detail that asked.)
     * @returns {{cancelMany: (tasks: object[]) => void,
     *   cancel: (task: object) => Promise<object|null>,
     *   complete: (task: object) => Promise<object|null>,
     *   resume: (task: object) => Promise<object>,
     *   update: (taskId: string, payload: object) => Promise<object>,
     *   roster: () => object[]}} `cancel` and `complete` resolve null when
     *   the operator backed out of their confirmation.
     * @throws {Error} When a dependency is missing.
     */
    function create(deps) {
        const { api, store, onChanged, onUpdated, onError } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-actions] deps.api is required');
        if (!store) throw new Error('[task-actions] deps.store is required');
        if (typeof onChanged !== 'function') throw new Error('[task-actions] deps.onChanged is required');
        if (typeof onUpdated !== 'function') throw new Error('[task-actions] deps.onUpdated is required');
        if (typeof onError !== 'function') throw new Error('[task-actions] deps.onError is required');

        const canceller = BossModTasksCancel.createCanceller({ api, onCancelled: onChanged, onError });
        const completer = BossModTasksComplete.createCompleter({ api, onCompleted: onChanged });

        /**
         * Hand a blocked or stalled task back to its assignee. Nothing is
         * destroyed, so nothing asks first.
         *
         * @param {object} task
         * @returns {Promise<object>} The stored row, now `pending`.
         * @throws {Error} (rejects) With the server's `detail` as the message.
         */
        async function resume(task) {
            let res;
            try {
                res = await api(`/api/tasks/${encodeURIComponent(task.id)}/resume`, { method: 'POST' });
            } catch (err) {
                console.error('[task-actions] the resume request failed', err);
                throw new Error(`Could not resume: ${(err && err.message) || 'the request failed'}`);
            }
            let body;
            try {
                body = await res.json();
            } catch (err) {
                console.error('[task-actions] the resume response had no JSON body', err);
                throw new Error(`Could not resume: HTTP ${res.status}`);
            }
            if (!res.ok) {
                const detail = body && body.detail;
                throw new Error(`Could not resume: ${typeof detail === 'string'
                    ? detail : JSON.stringify(detail || `HTTP ${res.status}`)}`);
            }
            onChanged([task.id]);
            return body;
        }

        /**
         * PATCH an open task with the fields the operator changed.
         *
         * @param {string} taskId
         * @param {object} payload  Only the changed fields, plus
         *   `confirm_specialty_mismatch` on a resend past a mismatch.
         * @returns {Promise<object>} The stored row, as GET /api/tasks lists it.
         * @throws {Error} (rejects) `kind: 'specialty_mismatch'` with `reason`
         *   and `suggested` (the server's suggested assignees), or
         *   `kind: 'failed'` with the server's `detail` as the message.
         */
        async function update(taskId, payload) {
            let res;
            try {
                res = await api(`/api/tasks/${encodeURIComponent(taskId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } catch (err) {
                console.error('[task-actions] the update request failed', err);
                throw refusal((err && err.message) || 'The request failed.', { kind: 'failed' });
            }
            let body;
            try {
                body = await res.json();
            } catch (err) {
                console.error('[task-actions] the update response had no JSON body', err);
                throw refusal(`HTTP ${res.status}`, { kind: 'failed' });
            }
            if (res.status === 409 && body && body.outcome === 'specialty_mismatch') {
                throw refusal('Specialty mismatch — nothing was saved', {
                    kind: 'specialty_mismatch',
                    reason: body.reason || '',
                    suggested: Array.isArray(body.suggested_assignees) ? body.suggested_assignees : [],
                });
            }
            if (!res.ok) {
                const detail = body && body.detail;
                const message = typeof detail === 'string' ? detail : JSON.stringify(detail || `HTTP ${res.status}`);
                throw refusal(message, { kind: 'failed' });
            }
            onUpdated(body);
            return body;
        }

        return {
            cancelMany: canceller.cancelMany,
            cancel: canceller.cancelOne,
            complete: completer.completeOne,
            resume,
            update,
            /**
             * Everyone who could be assigned, as the store lists them now.
             * @returns {object[]}
             * @throws {Error} When the store holds no roster list.
             */
            roster() {
                const { roster } = store.getState();
                if (!Array.isArray(roster)) throw new Error('[task-actions] the store has no roster');
                return roster;
            },
        };
    }

    return { create };
})();
