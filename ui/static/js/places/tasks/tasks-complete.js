/**
 * BossMod AI — marking a task complete on the operator's behalf.
 *
 * The operator is the final judge of whether work is done: an agent can be
 * stuck behind a done check the operator can see is met. This is the one
 * place in Tasks that completes a task, mirroring tasks-cancel.js — a detail
 * asks, this confirms and posts. The confirmation needs a summary, because
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
     * @param {(message: string) => void} deps.onError  Surfaced, never swallowed.
     * @returns {{completeOne: (task: object) => void}}
     * @throws {Error} When a dependency is missing.
     */
    function createCompleter(deps) {
        const { api, onCompleted, onError } = deps || {};
        if (typeof api !== 'function') throw new Error('[tasks-complete] deps.api is required');
        if (typeof onCompleted !== 'function') throw new Error('[tasks-complete] deps.onCompleted is required');
        if (typeof onError !== 'function') throw new Error('[tasks-complete] deps.onError is required');

        /**
         * POST the completion.
         * @param {string} taskId
         * @param {string} summary
         * @returns {Promise<void>} Never rejects; a failure reaches onError.
         */
        async function post(taskId, summary) {
            try {
                const res = await api(`/api/tasks/${encodeURIComponent(taskId)}/complete`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ summary }),
                });
                if (!res.ok) throw new Error(await failureMessage(res));
                onCompleted([taskId]);
            } catch (err) {
                console.error('[tasks-complete] complete failed', err);
                onError(`Could not mark complete: ${(err && err.message) || 'the request failed'}`);
            }
        }

        return {
            /**
             * Ask for the summary, then post. Keep open is listed last, as
             * tasks-cancel.js lists its safe choice.
             *
             * @param {object} task  Only a task the server flagged
             *   `operator_can_complete` opens anything.
             * @returns {void}
             */
            completeOne(task) {
                if (!task || !task.id || !task.operator_can_complete) return;
                const summary = h('textarea', {
                    class: 'assign-textarea', id: 'ct-complete-summary', rows: '3', maxlength: '2000',
                    'aria-required': 'true', placeholder: 'What was delivered, or why it counts as done',
                });
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
                        modal.close();
                        void post(task.id, text);
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
                });
            },
        };
    }

    return { createCompleter, TITLE_COPY, SUBTASKS_COPY };
})();
