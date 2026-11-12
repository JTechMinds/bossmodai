/**
 * BossMod AI — the requests a live thread's header makes.
 *
 * THE SEAM. conversation/sources/thread-source.js adapts a thread: it holds the
 * last channel it was told about, maps wire rows to Messages, and signals the
 * chrome. What is here is the other half of each header action — the HTTP
 * request that changes one of the thread's settings (its name, paused or not,
 * CLI auto-approve, Yolo mode, reopening a sealed room, taking a member out) and hands back the
 * channel the server answered with. None of it holds state, and none of it touches the DOM.
 * Archive and seating have their own dialogs and live beside this, in
 * thread-archive.js and thread-seat.js.
 *
 * It is also the HTTP half of the thread's Rewind: reading the messages a rewind
 * can start at, and asking the server to cut the thread there. The dialog and the
 * flow around it are conversation/chat-rewind*.js, which read both calls from the
 * source like every other conversation capability.
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
                // Structured refusals (e.g. a 422 attachment link) say
                // `{detail: {error, code, ...}}`; the error is the reason.
                if (detail && typeof detail.error === 'string' && detail.error.trim()) {
                    return detail.error.trim();
                }
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
     * @param {(raw: object) => object} deps.toMessage  The source's row mapper,
     *   so a rewind point is the same Message the transcript shows.
     * @returns {{ rename: (nextName: string) => Promise<object>,
     *             pause: () => Promise<object>,
     *             resume: () => Promise<object>,
     *             reopen: () => Promise<object>,
     *             setCliAutoApprove: (enabled: boolean) => Promise<object>,
     *             setCliYolo: (enabled: boolean) => Promise<object>,
     *             removeMember: (agentId: string) => Promise<object>,
     *             rewindPoints: () => Promise<object[]>,
     *             rewind: (fromMessageId: string) => Promise<object> }}
     *   Each setting request resolves to the channel the server returned;
     *   rewindPoints to Messages, and rewind to the server's rewind report.
     * @throws {Error} When `api`, `threadId` or `toMessage` is missing.
     */
    function createThreadRequests(deps) {
        const api = deps && deps.api;
        const threadId = deps && deps.threadId;
        const toMessage = deps && deps.toMessage;
        if (typeof api !== 'function') throw new Error('[thread-requests] deps.api is required');
        if (!threadId) throw new Error('[thread-requests] deps.threadId is required');
        if (typeof toMessage !== 'function') throw new Error('[thread-requests] deps.toMessage is required');

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

        /**
         * Turn this thread's Yolo mode on or off. The request writes only the
         * thread's Yolo flag; the confirm before it goes on is the caller's.
         *
         * @param {boolean} enabled
         * @returns {Promise<object>} The updated channel.
         * @throws {Error} With the server's reason on any non-2xx.
         */
        async function setCliYolo(enabled) {
            const res = await api(`/api/channels/${threadId}/cli-yolo`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled === true }),
            });
            if (!res.ok) {
                throw new Error(await refusal(res, 'Could not update Yolo mode for this thread.'));
            }
            return res.json();
        }

        /** @returns {Promise<object>} The reopened channel. */
        async function reopen() {
            const res = await api(`/api/channels/${threadId}/reopen`, { method: 'POST' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not reopen this thread.');
            return res.json();
        }

        /**
         * Take one member out of this thread. History stays.
         *
         * @param {string} agentId
         * @returns {Promise<object>} The channel without them.
         * @throws {Error} With the server's reason on any non-2xx (last member,
         *   not a member, archived).
         */
        async function removeMember(agentId) {
            if (!agentId) throw new Error('Pick someone to remove.');
            const res = await api(`/api/channels/${threadId}/members/${encodeURIComponent(agentId)}`, {
                method: 'DELETE',
            });
            if (!res.ok) throw new Error(await refusal(res, 'Could not remove that agent from this thread.'));
            return res.json();
        }

        /**
         * The messages a rewind can start at: the thread's transcript (the
         * endpoint and window the source loads), the operator's and the
         * members' own lines only, oldest first. Notes, round markers and
         * request cards are facts, not conversation, and are not offered.
         *
         * @returns {Promise<object[]>} Messages.
         * @throws {Error} With the server's text on a non-OK response, or when
         *   the transcript is not a list.
         */
        async function rewindPoints() {
            const res = await api(`/api/channels/${threadId}`, { cache: 'no-store' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not load this thread.');
            const payload = await res.json();
            if (!payload || !Array.isArray(payload.messages)) {
                throw new Error('The thread did not come back as a list.');
            }
            return payload.messages.map(toMessage).filter((message) => message.kind === 'message'
                && (message.author === 'human' || message.author === 'agent'));
        }

        /**
         * Delete one thread message and everything after it.
         *
         * @param {string} fromMessageId
         * @returns {Promise<{status: string, removed_messages: number,
         *   removed_attachments: number, unremoved_files: number,
         *   stopped_agents: number, dropped_wakes: number}>}
         * @throws {Error} With the server's reason on any non-2xx (404 for a
         *   message that is no longer there, 409 for an archived thread, 503
         *   when a reply could not be stopped), so the dialog can say why.
         */
        async function rewind(fromMessageId) {
            const res = await api(`/api/channels/${threadId}/rewind`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ from_message_id: fromMessageId }),
            });
            if (!res.ok) throw new Error(await refusal(res, 'Could not rewind this thread.'));
            return res.json();
        }

        return { rename, pause, resume, reopen, setCliAutoApprove, setCliYolo, removeMember, rewindPoints, rewind };
    }

    return { createThreadRequests, refusal };
})();
