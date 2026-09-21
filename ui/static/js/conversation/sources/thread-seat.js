/**
 * BossMod AI — seat a live agent into an existing thread.
 *
 * Create freezes the roster. This is the one write that adds someone later
 * without archiving the room or minting a replacement. Catch-up is the
 * transcript already on the thread — this never asks for a summary.
 *
 * The picker lives here, not in the source, for the same reason archive's
 * confirm does: a source must not touch the DOM. Chrome and People both call
 * `pickAndSeat`, so the fail-closed empty-roster and the POST cannot drift.
 */
const BossModThreadSeat = (() => {
    const { h } = BossModDom;

    /**
     * Live hired agents who are not already in `memberIds`.
     *
     * @param {object[]} roster
     * @param {string[]} memberIds
     * @returns {object[]}
     */
    function liveCandidates(roster, memberIds) {
        const taken = new Set((memberIds || []).filter(Boolean));
        return (roster || []).filter((agent) => agent && agent.id && !taken.has(agent.id));
    }

    /**
     * Turn a failed seat response into an Error the operator can read.
     *
     * @param {Response} res
     * @param {string} fallback
     * @returns {Promise<Error>}
     */
    async function failure(res, fallback) {
        const data = await res.json().catch(() => ({}));
        const detail = data && data.detail;
        const message = (typeof detail === 'string' && detail.trim())
            || (detail && typeof detail.message === 'string' && detail.message)
            || fallback;
        return new Error(message);
    }

    /**
     * Build the seater.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Roster is the live hire list.
     * @param {(candidates: object[]) => Promise<string|null>} [deps.prompt]
     *   Injected for tests. When absent the accessible modal is used.
     * @returns {{liveCandidates: Function, request: Function, prompt: Function,
     *            pickAndSeat: Function}}
     * @throws {Error} When `api` or `store` is missing.
     */
    function createThreadSeat(deps) {
        const api = deps && deps.api;
        const store = deps && deps.store;
        const confirm = deps && deps.prompt;
        if (typeof api !== 'function') throw new Error('[thread-seat] deps.api is required');
        if (!store) throw new Error('[thread-seat] deps.store is required');

        /**
         * POST one seat. The server is fail-closed; this surfaces that text.
         *
         * @param {string} threadId
         * @param {string} agentId
         * @returns {Promise<object>} Updated thread summary.
         * @throws {Error} On any non-2xx, with the server's message.
         */
        async function request(threadId, agentId) {
            const res = await api(`/api/channels/${threadId}/members`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_id: agentId }),
            });
            if (!res.ok) throw await failure(res, 'Could not add that agent to this thread.');
            return res.json();
        }

        /**
         * Ask which live agent to seat.
         *
         * An empty candidate list throws rather than opening a blank dialog —
         * that is the same fail-closed rule as a missing agent on the server.
         * Dismissing resolves null, so Cancel is never read as a seat.
         *
         * @param {object[]} candidates
         * @returns {Promise<string|null>}
         * @throws {Error} When nobody live is left to add.
         */
        function prompt(candidates) {
            if (!candidates.length) {
                throw new Error('Everyone live is already in this thread.');
            }
            if (typeof confirm === 'function') {
                return Promise.resolve(confirm(candidates));
            }
            return new Promise((resolve) => {
                let settled = false;
                let modal = null;
                const finish = (agentId) => {
                    if (settled) return;
                    settled = true;
                    resolve(agentId);
                };
                const list = h('ul', { class: 'thread-seat-list' });
                candidates.forEach((agent) => {
                    list.append(h('li', {},
                        h('button', {
                            class: 'thread-seat-choice',
                            type: 'button',
                            id: `thread-seat-${agent.id}`,
                            onclick: () => {
                                finish(agent.id);
                                if (modal) modal.close();
                            },
                        },
                        h('span', { class: 'thread-seat-name' }, agent.name || 'Agent'),
                        agent.role
                            ? h('span', { class: 'thread-seat-role' }, agent.role)
                            : null)));
                });
                modal = BossModOverlays.createModal({
                    title: 'Add to thread',
                    closeOnBackdrop: true,
                    body: list,
                    actions: [{
                        id: 'thread-seat-cancel',
                        label: 'Cancel',
                        onSelect: () => finish(null),
                    }],
                    onClose: () => finish(null),
                });
                BossModIcons.paint(modal.element, 'thread-seat');
            });
        }

        /**
         * Pick a live non-member and seat them.
         *
         * @param {string} threadId
         * @param {object[]} members  Current thread roster.
         * @returns {Promise<object|null>} Updated summary, or null on cancel.
         */
        async function pickAndSeat(threadId, members) {
            const memberIds = (members || []).map((member) => member && member.id).filter(Boolean);
            const chosen = await prompt(liveCandidates(store.getState().roster, memberIds));
            if (!chosen) return null;
            return request(threadId, chosen);
        }

        return { liveCandidates, request, prompt, pickAndSeat };
    }

    return { createThreadSeat, liveCandidates };
})();
