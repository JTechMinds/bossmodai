/**
 * BossMod AI — the requests behind an agent DM's Rewind.
 *
 * THE SEAM, as conversation/sources/thread-requests.js is for a thread:
 * conversation/sources/agent-source.js adapts the DM, and this is the HTTP
 * half of its rewind: reading the messages a rewind can start at, and asking
 * the server to cut the DM there. It holds no state and never touches the DOM;
 * the dialog and the flow around it are conversation/chat-rewind*.js.
 *
 * It lives beside the source rather than inside it because the source sits at
 * its line cap; the source still exports both calls, so the flow reads them
 * from the source like every other conversation capability.
 */
const BossModAgentRequests = (() => {

    /**
     * Build one agent's rewind requests.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper (injected).
     * @param {string} deps.agentId
     * @param {(raw: object) => object} deps.toMessage  The source's row mapper,
     *   so a rewind point is the same Message the transcript shows.
     * @param {(res: Response, fallback: string) => Promise<string>} deps.refusal
     *   The source's reading of a server refusal.
     * @returns {{rewindPoints: () => Promise<object[]>,
     *            rewind: (fromMessageId: string) => Promise<object>}}
     * @throws {Error} When any dependency is missing.
     */
    function createAgentRequests(deps) {
        const { api, agentId, toMessage, refusal } = deps || {};
        if (typeof api !== 'function') throw new Error('[agent-requests] deps.api is required');
        if (!agentId) throw new Error('[agent-requests] deps.agentId is required');
        if (typeof toMessage !== 'function') throw new Error('[agent-requests] deps.toMessage is required');
        if (typeof refusal !== 'function') throw new Error('[agent-requests] deps.refusal is required');

        /**
         * The messages a rewind can start at: the transcript's window, the
         * operator's and the agent's own lines only, oldest first. Notes,
         * receipts and request cards are facts, not conversation, and are not
         * rewound.
         *
         * @returns {Promise<object[]>} Messages.
         * @throws {Error} With the server's text on a non-OK response, or when
         *   the body is not a list.
         */
        async function rewindPoints() {
            const res = await api(`/api/agents/${agentId}/messages?limit=50`, { cache: 'no-store' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not load this conversation.');
            const rows = await res.json();
            if (!Array.isArray(rows)) throw new Error('The conversation did not come back as a list.');
            return rows.map(toMessage).filter((message) => message.kind === 'message'
                && (message.author === 'human' || message.author === 'agent'));
        }

        /**
         * Delete one DM message and everything after it.
         *
         * @param {string} fromMessageId
         * @returns {Promise<{status: string, removed_messages: number,
         *   removed_attachments: number, unremoved_files: number,
         *   stopped_turn: boolean, requeued: number}>}
         * @throws {Error} With the server's `detail` on any non-2xx (404 for a
         *   message that is no longer there, 503 when the reply could not be
         *   stopped), so the dialog can say why.
         */
        async function rewind(fromMessageId) {
            const res = await api(`/api/agents/${agentId}/chat-rewind`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ from_message_id: fromMessageId }),
            });
            if (!res.ok) throw new Error(await refusal(res, 'Could not rewind this chat.'));
            return res.json();
        }

        return { rewindPoints, rewind };
    }

    return { createAgentRequests };
})();
