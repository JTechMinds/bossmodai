/**
 * BossMod AI — putting a task on hold on the operator's behalf.
 *
 * A pending task its assignee has not started can be parked until the boss
 * releases it (tasks-release.js). This is the one place in Tasks that holds a
 * task, mirroring tasks-complete.js — a detail asks, this asks why and posts,
 * and the detail repaints in place from the row the promise resolves with.
 * The why is optional, as the needs queue's Nudge note is: the server stores
 * it as the task's status note, and a blank one is sent as none.
 *
 * Whether a task can be held at all is the server's call (`operator_can_hold`
 * on each listed task); this never re-implements the state machine.
 */
const BossModTasksHold = (() => {
    const { h } = BossModDom;

    const TITLE_COPY = 'Put this on hold?';
    const NOTE_LABEL = 'Why? (optional)';
    const FORM_ID = 'ct-hold-form';

    /**
     * Build a holder.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(ids: string[]) => void} deps.onHeld  The server accepted.
     * @returns {{holdOne: (task: object) => Promise<object|null>}}
     * @throws {Error} When a dependency is missing.
     */
    function createHolder(deps) {
        const { api, onHeld } = deps || {};
        if (typeof api !== 'function') throw new Error('[tasks-hold] deps.api is required');
        if (typeof onHeld !== 'function') throw new Error('[tasks-hold] deps.onHeld is required');

        /**
         * POST the hold.
         * @param {string} taskId
         * @param {string|null} note  Why, or null for no reason given.
         * @returns {Promise<object>} The stored row, as GET /api/tasks lists it.
         * @throws {Error} (rejects) With the server's `detail` as the message.
         */
        async function post(taskId, note) {
            let res;
            try {
                res = await api(`/api/tasks/${encodeURIComponent(taskId)}/hold`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ note }),
                });
            } catch (err) {
                console.error('[tasks-hold] the hold request failed', err);
                throw new Error(`Could not put on hold: ${(err && err.message) || 'the request failed'}`);
            }
            let body;
            try {
                body = await res.json();
            } catch (err) {
                console.error('[tasks-hold] the hold response had no JSON body', err);
                throw new Error(`Could not put on hold: HTTP ${res.status}`);
            }
            if (!res.ok) {
                const detail = body && body.detail;
                throw new Error(`Could not put on hold: ${typeof detail === 'string'
                    ? detail : JSON.stringify(detail || `HTTP ${res.status}`)}`);
            }
            onHeld([taskId]);
            return body;
        }

        /**
         * The why dialog; its outcome settles the caller's promise.
         * @param {object} task
         * @param {(row: object|null) => void} resolve
         * @param {(err: Error) => void} reject
         * @returns {void}
         */
        function ask(task, resolve, reject) {
            let submitted = false;
            const note = h('textarea', {
                class: 'field-textarea', id: 'ct-hold-note', 'data-autogrow': true,
            });
            const grow = BossModAutoGrow.bind(note);
            let modal = null;
            const form = h('form', {
                class: 'assign-form',
                id: FORM_ID,
                onsubmit: (event) => {
                    event.preventDefault();
                    submitted = true;
                    modal.close();
                    post(task.id, note.value.trim() || null).then(resolve, reject);
                },
            },
                h('label', { class: 'assign-field' },
                    h('span', { class: 'assign-field-label' }, NOTE_LABEL), note));
            modal = BossModOverlays.createModal({
                title: TITLE_COPY,
                body: form,
                actions: [
                    { label: 'Hold', tone: 'primary', id: 'ct-hold-submit', form: FORM_ID },
                    { label: 'Cancel', tone: 'quiet' },
                ],
                // However it closed without Hold: nothing was sent.
                onClose: () => {
                    grow.destroy();
                    if (!submitted) resolve(null);
                },
            });
        }

        return {
            /**
             * Ask why, then post. Cancel is listed last, as tasks-cancel.js
             * lists its safe choice.
             *
             * @param {object} task  Only a task the server flagged
             *   `operator_can_hold` opens anything.
             * @returns {Promise<object|null>} The stored row once the server
             *   held it (after `onHeld`); null when the operator cancelled,
             *   or the task cannot be held.
             * @throws {Error} (rejects) The server refused, with its `detail`.
             */
            holdOne(task) {
                if (!task || !task.id || !task.operator_can_hold) return Promise.resolve(null);
                return new Promise((resolve, reject) => ask(task, resolve, reject));
            },
        };
    }

    return { createHolder, TITLE_COPY };
})();
