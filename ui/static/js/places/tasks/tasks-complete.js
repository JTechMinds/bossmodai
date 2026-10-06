/**
 * BossMod AI — marking a task complete on the operator's behalf.
 *
 * The operator is the final judge of whether work is done: an agent can be
 * stuck behind a done check the operator can see is met. This is the one
 * place in Tasks that completes a task, mirroring tasks-cancel.js — a detail
 * asks, this confirms and posts, and the detail repaints in place from the
 * row the promise resolves with. The confirmation needs a summary, because
 * the server records it as the completion summary and tells the origin thread
 * and a delegated parent why the work closed.
 *
 * Whether a task can be completed at all is the server's call
 * (`operator_can_complete` on each listed task); this never re-implements the
 * state machine.
 */
const BossModTasksComplete = (() => {
    const { h, clear } = BossModDom;

    const TITLE_COPY = 'Mark this task complete?';
    const BODY_COPY = 'The task closes as done. The assignee\'s open work on it stops, '
        + 'and a delegating parent task is told.';
    const SUMMARY_LABEL = 'Why is it done?';
    const FORM_ID = 'ct-complete-form';
    const SUBTASKS_COPY = 'Resolve its open subtasks first.';

    /**
     * The failure message a completion response carries.
     *
     * @param {Response} res
     * @returns {Promise<string>}
     */
    async function failureMessage(res) {
        let body = null;
        try {
            body = await res.json();
        } catch (err) {
            console.error('[tasks-complete] the error response had no JSON body', err);
            return `HTTP ${res.status}`;
        }
        if (res.status === 409 && body && Array.isArray(body.task_ids)) return SUBTASKS_COPY;
        const detail = body && (body.detail || body.reason);
        if (!detail) return `HTTP ${res.status}`;
        return typeof detail === 'string' ? detail : JSON.stringify(detail);
    }

    /**
     * Build a completer.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(ids: string[]) => void} deps.onCompleted  The server accepted.
     * @returns {{completeOne: (task: object) => Promise<object|null>}}
     * @throws {Error} When a dependency is missing.
     */
    function createCompleter(deps) {
        const { api, onCompleted } = deps || {};
        if (typeof api !== 'function') throw new Error('[tasks-complete] deps.api is required');
        if (typeof onCompleted !== 'function') throw new Error('[tasks-complete] deps.onCompleted is required');

        /**
         * POST the completion.
         * @param {string} taskId
         * @param {string} summary
         * @returns {Promise<object>} The stored row, as GET /api/tasks lists it.
         * @throws {Error} (rejects) SUBTASKS_COPY for open subtasks, else the
         *   server's `detail`.
         */
        async function post(taskId, summary) {
            let res;
            try {
                res = await api(`/api/tasks/${encodeURIComponent(taskId)}/complete`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ summary }),
                });
            } catch (err) {
                console.error('[tasks-complete] the completion request failed', err);
                throw new Error(`Could not mark complete: ${(err && err.message) || 'the request failed'}`);
            }
            if (!res.ok) throw new Error(`Could not mark complete: ${await failureMessage(res)}`);
            let row;
            try {
                row = await res.json();
            } catch (err) {
                console.error('[tasks-complete] the completion response had no JSON body', err);
                throw new Error(`Could not mark complete: HTTP ${res.status}`);
            }
            onCompleted([taskId]);
            return row;
        }

        /**
         * The summary dialog; its outcome settles the caller's promise.
         * @param {object} task
         * @param {(row: object|null) => void} resolve
         * @param {(err: Error) => void} reject
         * @returns {void}
         */
        function ask(task, resolve, reject) {
            let submitted = false;
            const summary = h('textarea', {
                class: 'field-textarea', id: 'ct-complete-summary', 'data-autogrow': true,
                'aria-required': 'true', placeholder: 'What was delivered, or why it counts as done',
            });
            const grow = BossModAutoGrow.bind(summary);
            const note = h('div', { class: 'assign-result', role: 'alert' });
            let modal = null;
            const form = h('form', {
                class: 'assign-form',
                id: FORM_ID,
                onsubmit: (event) => {
                    event.preventDefault();
                    const text = summary.value.trim();
                    clear(note);
                    if (!text) {
                        note.append(h('div', { class: 'callout', 'data-tone': 'alert' },
                            h('p', { class: 'callout-title' }, 'A summary is required')));
                        summary.focus();
                        return;
                    }
                    submitted = true;
                    modal.close();
                    post(task.id, text).then(resolve, reject);
                },
            },
                h('p', { class: 'assign-hint' }, BODY_COPY),
                h('label', { class: 'assign-field' },
                    h('span', { class: 'assign-field-label' }, SUMMARY_LABEL), summary),
                note);
            modal = BossModOverlays.createModal({
                title: TITLE_COPY,
                body: form,
                actions: [
                    { label: 'Mark complete', tone: 'primary', id: 'ct-complete-submit', form: FORM_ID },
                    { label: 'Keep open', tone: 'quiet' },
                ],
                // However it closed without a summary sent: kept open.
                onClose: () => {
                    grow.destroy();
                    if (!submitted) resolve(null);
                },
            });
        }

        return {
            /**
             * Ask for the summary, then post. Keep open is listed last, as
             * tasks-cancel.js lists its safe choice.
             *
             * @param {object} task  Only a task the server flagged
             *   `operator_can_complete` opens anything.
             * @returns {Promise<object|null>} The stored row once the server
             *   completed it (after `onCompleted`); null when the operator
             *   kept it open, or the task cannot be completed.
             * @throws {Error} (rejects) The server refused: SUBTASKS_COPY for
             *   open subtasks, else its `detail`.
             */
            completeOne(task) {
                if (!task || !task.id || !task.operator_can_complete) return Promise.resolve(null);
                return new Promise((resolve, reject) => ask(task, resolve, reject));
            },
        };
    }

    return { createCompleter, TITLE_COPY, SUBTASKS_COPY };
})();
