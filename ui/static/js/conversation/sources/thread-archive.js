/**
 * BossMod AI — the thread archive decision, and the copy that carries it.
 *
 * Archiving looks irreversible and is not, and it can strand tasks that were
 * born in the thread. Every string here is what the operator is told before
 * they commit, so this flow gets its own file rather than living inside the
 * source that triggers it.
 *
 * Two branches, both preserved from the dock-era implementation: a thread with
 * open origin tasks offers Cancel-tasks-and-archive / Archive-only / Back; a
 * thread with none offers a plain Archive / Cancel confirm.
 */
const BossModThreadArchive = (() => {

    /** Said in every branch, because "archive" reads as "delete" and is not. */
    const HONESTY_COPY =
        'Not permanently deleted — leaves the active list and seals the room (no new posts).';

    /**
     * Does this thread have open origin tasks to decide about?
     * @param {number} count
     * @returns {boolean}
     */
    function shouldPrompt(count) {
        return Number(count) > 0;
    }

    /**
     * The prompt for one open-task count.
     *
     * `buttons` is ordered as the operator reads the choices: the way out
     * first, the committing action last.
     *
     * @param {number} count  Open origin tasks.
     * @returns {{title: string, body: string, honesty: string,
     *            dismissChoice: string, buttons: object[]}}
     */
    function spec(count) {
        if (shouldPrompt(count)) {
            const n = Number(count);
            return {
                title: 'Archive thread?',
                body: `This thread has ${n} open tasks. Sealing stops new posts and access cards.`,
                honesty: HONESTY_COPY,
                dismissChoice: 'back',
                buttons: [
                    { id: 'channel-archive-back', label: 'Back', choice: 'back', primary: false },
                    { id: 'channel-archive-only', label: 'Archive only', choice: 'archive_only', primary: false },
                    { id: 'channel-archive-cancel-tasks', label: 'Cancel tasks & archive', choice: 'cancel_and_archive', primary: true },
                ],
            };
        }
        return {
            title: 'Archive thread?',
            body: 'Hides it from the active list and seals the room — no new messages or access cards. Open tasks stay on the board.',
            honesty: HONESTY_COPY,
            dismissChoice: 'back',
            buttons: [
                { id: 'channel-archive-back', label: 'Cancel', choice: 'back', primary: false },
                { id: 'channel-archive-confirm', label: 'Archive', choice: 'archive_only', primary: true },
            ],
        };
    }

    /**
     * The full sentence an operator reads for one open-task count.
     * @param {number} count
     * @returns {string}
     */
    function copy(count) {
        const prompt = spec(count);
        return `${prompt.body} ${prompt.honesty}`;
    }

    /**
     * Is this choice a decision not to archive?
     * @param {string} choice
     * @returns {boolean} True for an explicit back/cancel AND for no choice at
     *   all — a dismissed dialog must never be read as consent.
     */
    function isAbort(choice) {
        return choice === 'back' || choice === 'cancel' || !choice;
    }

    /**
     * Build the archive flow.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(spec: object) => Promise<string>|string} [deps.confirm]  Injected
     *   for tests. When absent the accessible modal is used — that default IS
     *   the production implementation, not a fallback for a missing one.
     * @returns {{HONESTY_COPY: string, spec: Function, copy: Function,
     *            shouldPrompt: Function, isAbort: Function, openTasks: Function,
     *            request: Function, prompt: Function}}
     * @throws {Error} When `api` is missing.
     */
    function createThreadArchive(deps) {
        const api = deps && deps.api;
        const confirm = deps && deps.confirm;
        if (typeof api !== 'function') throw new Error('[thread-archive] deps.api is required');

        /**
         * Count the still-open tasks this thread started.
         *
         * @param {string} threadId
         * @returns {Promise<{count: number, tasks: object[]}>}
         * @throws {Error} On a non-OK response. Archiving without knowing the
         *   count would silently skip the branch that warns about them.
         */
        async function openTasks(threadId) {
            const res = await api(`/api/channels/${threadId}/open-tasks`, { cache: 'no-store' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not read open tasks.');
            const body = await res.json();
            const tasks = Array.isArray(body && body.tasks) ? body.tasks : [];
            const count = Number.isFinite(body && body.count) ? body.count : tasks.length;
            return { count, tasks };
        }

        /**
         * Archive the thread, with or without cancelling its open tasks.
         *
         * @param {string} threadId
         * @param {{cancelOpenTasks: boolean}} options
         * @returns {Promise<object>} The updated thread summary.
         * @throws {Error} On a non-OK response.
         */
        async function request(threadId, options) {
            const cancelOpenTasks = Boolean(options && options.cancelOpenTasks);
            const url = cancelOpenTasks
                ? `/api/channels/${threadId}/archive?cancel_open_tasks=true`
                : `/api/channels/${threadId}`;
            const res = await api(url, { method: cancelOpenTasks ? 'POST' : 'DELETE' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not archive the thread.');
            return res.json();
        }

        /**
         * Ask the operator what to do.
         *
         * @param {number} count  Open origin tasks.
         * @returns {Promise<string>} The chosen `choice`. Dismissing the dialog
         *   resolves with `spec.dismissChoice`, so closing means "back", never
         *   "archive".
         */
        function prompt(count) {
            const prompted = spec(count);
            if (typeof confirm === 'function') {
                return Promise.resolve(confirm(prompted));
            }
            return new Promise((resolve) => {
                let settled = false;
                const finish = (choice) => {
                    if (settled) return;
                    settled = true;
                    resolve(choice);
                };
                BossModOverlays.createModal({
                    title: prompted.title,
                    body: BossModDom.h('div', {},
                        BossModDom.h('p', {}, prompted.body),
                        BossModDom.h('p', { class: 'modal-honesty' }, prompted.honesty)),
                    // createModal focuses the first focusable control in the
                    // BODY, and falls back to the LAST action only when the
                    // body has none. This body is two <p>s, so the fallback is
                    // what runs here and the last action takes focus — which is
                    // why the row is rendered committing-action-first, leaving
                    // the safe choice last where focus lands. The same shape as
                    // the Pause dialog. Ids, labels, and choices are unchanged.
                    actions: prompted.buttons.slice().reverse().map((btn) => ({
                        id: btn.id,
                        label: btn.label,
                        tone: btn.primary ? 'danger' : 'quiet',
                        onSelect: () => finish(btn.choice),
                    })),
                    onClose: () => finish(prompted.dismissChoice),
                });
            });
        }

        return { HONESTY_COPY, spec, copy, shouldPrompt, isAbort, openTasks, request, prompt };
    }

    return { createThreadArchive, HONESTY_COPY, spec, copy, shouldPrompt, isAbort };
})();
