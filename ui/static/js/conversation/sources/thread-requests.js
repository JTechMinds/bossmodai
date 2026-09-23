/**
 * BossMod AI — the thread-setting requests a live thread's header makes.
 *
 * THE SEAM. conversation/sources/thread-source.js adapts a thread: it holds the
 * last channel it was told about, maps wire rows to Messages, and signals the
 * chrome. What is here is the other half of each header action — the HTTP
 * request that changes one of the thread's settings (its name, paused or not,
 * CLI auto-approve, reopening a sealed room) and hands back the channel the
 * server answered with. None of it holds state, and none of it touches the DOM.
 * Archive and seating have their own dialogs and live beside this, in
 * thread-archive.js and thread-seat.js.
 *
 * Every request fails loudly: a non-2xx throws with the server's reason, so the
 * chrome's onError can say so and a setting that did not change can never look
 * like one that did.
 */
const BossModThreadRequests = (() => {

    /**
     * The server's reason, when it sent one. Raw JSON is not a reason.
     *
     * @param {Response} res
     * @param {string} fallback  Said when the body is empty or unreadable.
     * @returns {Promise<string>}
     */
    async function refusal(res, fallback) {
        let raw = '';
        try { raw = await res.text(); } catch { raw = ''; }
        const text = String(raw || '').trim();
        if (text.startsWith('{')) {
            try {
                const data = JSON.parse(text);
                const detail = data && data.detail;
                if (typeof detail === 'string' && detail.trim()) return detail.trim();
            } catch { /* keep the raw body */ }
        }
        return text || fallback;
    }

    /**
     * Bind the requests to one thread.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper (injected, never global).
     * @param {string} deps.threadId
     * @returns {{ rename: (nextName: string) => Promise<object>,
     *             pause: () => Promise<object>,
     *             resume: () => Promise<object>,
     *             reopen: () => Promise<object>,
     *             setCliAutoApprove: (enabled: boolean) => Promise<object> }}
     *   Each resolves to the channel the server returned.
     * @throws {Error} When `api` or `threadId` is missing.
     */
    function createThreadRequests(deps) {
        const api = deps && deps.api;
        const threadId = deps && deps.threadId;
        if (typeof api !== 'function') throw new Error('[thread-requests] deps.api is required');
        if (!threadId) throw new Error('[thread-requests] deps.threadId is required');

        /**
         * Rename this thread.
         *
         * @param {string} nextName  Already trimmed by the caller; trimmed
         *   again here because this is the boundary the server sees.
         * @returns {Promise<object>} The renamed channel.
         * @throws {Error} With the server's message on any non-2xx, and before
         *   the request on a name the server would reject anyway.
         */
        async function rename(nextName) {
            const name = String(nextName || '').trim();
            if (!name) throw new Error('A thread needs a name.');
            const res = await api(`/api/channels/${threadId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error((await res.text()) || 'Could not rename this thread.');
            return res.json();
        }

        /** @returns {Promise<object>} The paused channel. */
        async function pause() {
            const res = await api(`/api/channels/${threadId}/pause`, { method: 'POST' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not pause this thread.');
            return res.json();
        }

        /** @returns {Promise<object>} The resumed channel. */
        async function resume() {
            const res = await api(`/api/channels/${threadId}/resume`, { method: 'POST' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not resume this thread.');
            return res.json();
        }

        /**
         * Opt this thread in or out of System AI auto-approve.
         *
         * Off is the default. The request writes only the thread flag.
         *
         * @param {boolean} enabled
         * @returns {Promise<object>} The updated channel.
         */
        async function setCliAutoApprove(enabled) {
            const res = await api(`/api/channels/${threadId}/cli-auto-approve`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled === true }),
            });
            if (!res.ok) {
                throw new Error(await refusal(res, 'Could not update auto-approve for this thread.'));
            }
            return res.json();
        }

        /** @returns {Promise<object>} The reopened channel. */
        async function reopen() {
            const res = await api(`/api/channels/${threadId}/reopen`, { method: 'POST' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not reopen this thread.');
            return res.json();
        }

        return { rename, pause, resume, reopen, setCliAutoApprove };
    }

    return { createThreadRequests, refusal };
})();
