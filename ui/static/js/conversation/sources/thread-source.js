/**
 * BossMod AI — the shared-thread conversation adapter.
 *
 * Adapts `GET/POST /api/channels/{id}` and the three `channel_*` broadcasts to
 * the one Message shape the renderer understands. It is a data adapter and
 * nothing else: it never touches the DOM. That boundary is the point of the
 * consolidation — the three renderers it replaces each knew their own wire
 * format, which is why they drifted apart.
 */
const BossModThreadSource = (() => {

    /** What the composer says instead of a placeholder once a room is sealed. */
    const ARCHIVED_REASON = 'Archived — reopen to post again.';

    /**
     * Adapt one thread.
     *
     * @param {string} threadId
     * @param {object} ctx
     * @param {Function} ctx.api  Authenticated fetch helper (injected, never global).
     * @param {object} ctx.bus  Topic bus.
     * @param {object} ctx.presence  Shared presence controller, keyed by conversation.
     * @param {object} ctx.archive  From BossModThreadArchive.createThreadArchive.
     * @param {(conversationId: string) => void} ctx.forgetCache  Drops the cached
     *   transcript when a room is sealed, so a re-open does not paint stale posts.
     * @returns {object} ConversationSource (spec 4.1).
     * @throws {Error} When any capability is missing.
     */
    function createThreadSource(threadId, ctx) {
        const api = ctx && ctx.api;
        const bus = ctx && ctx.bus;
        const presence = ctx && ctx.presence;
        const archive = ctx && ctx.archive;
        const forgetCache = ctx && ctx.forgetCache;
        if (typeof api !== 'function') throw new Error('[thread-source] ctx.api is required');
        if (!bus) throw new Error('[thread-source] ctx.bus is required');
        if (!presence) throw new Error('[thread-source] ctx.presence is required');
        if (!archive) throw new Error('[thread-source] ctx.archive is required');
        if (typeof forgetCache !== 'function') {
            throw new Error('[thread-source] ctx.forgetCache is required');
        }

        let channel = null;
        let signals = null;

        function signal(name) {
            if (signals && typeof signals[name] === 'function') signals[name]();
        }

        function members() {
            return Array.isArray(channel && channel.members) ? channel.members : [];
        }

        /** Unknown until loaded: an unloaded thread is treated as live. */
        function isLiveThread() {
            return !channel || channel.status !== 'archived';
        }

        /** A sealed room keeps no presence and no cached transcript. */
        function seal() {
            presence.stopAll(threadId);
            forgetCache(threadId);
        }

        /**
         * Normalise one backend row. Both the REST load and the WebSocket echo
         * pass through here, so both produce the same dedupe key.
         *
         * @param {object} raw
         * @returns {object} Message
         */
        function toMessage(raw) {
            const consent = BossModConsentCard.isHostPathConsentMessage(raw) && raw.host_path_consent;
            return {
                key: String(raw.message_id || raw.id || '').trim(),
                author: raw.author_type || 'agent',
                authorName: raw.author_name || 'Unknown',
                showAuthor: true,
                text: raw.content || '',
                createdAt: raw.created_at || '',
                kind: consent ? 'request' : 'message',
                card: raw.host_path_consent || null,
                deskPath: null,
                systemReceipt: false,
            };
        }

        /**
         * Fetch the thread and its transcript.
         * @returns {Promise<object[]>} Messages.
         * @throws {Error} On a non-OK response, so the controller renders its
         *   error state instead of an empty room that looks like a quiet one.
         */
        async function load() {
            const res = await api(`/api/channels/${threadId}`, { cache: 'no-store' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not load this thread.');
            const payload = await res.json();
            channel = payload.channel || null;
            return (Array.isArray(payload.messages) ? payload.messages : []).map(toMessage);
        }

        /**
         * Post to the thread.
         *
         * Every member is marked mid-turn before the request, because the round
         * starts server-side the moment it lands. A failure clears them again,
         * so a rejected post never leaves the room looking busy.
         *
         * @param {string} text
         * @returns {Promise<void>}
         * @throws {Error} On any failure, so the send gate keeps the draft.
         */
        async function send(text) {
            const roster = members().filter((member) => member && member.id);
            roster.forEach((member) => presence.start(threadId, member.id, member.name));
            signal('presence');
            let res;
            try {
                res = await api(`/api/channels/${threadId}/messages`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: text }),
                });
            } catch (err) {
                roster.forEach((member) => presence.stop(threadId, member.id));
                signal('presence');
                throw err;
            }
            if (!res.ok) {
                roster.forEach((member) => presence.stop(threadId, member.id));
                signal('presence');
                throw new Error((await res.text()) || 'Could not post to this thread.');
            }
        }

        /**
         * Subscribe to this thread's live traffic.
         * @param {{message: Function, reset: Function, presence: Function, chrome: Function}} on
         * @returns {() => void} One disposer that drains all three subscriptions.
         */
        function subscribe(on) {
            signals = on;
            const offs = [
                bus.subscribe('channel_message', (data) => {
                    if (!data || data.channel_id !== threadId) return;
                    if (!isLiveThread()) {
                        seal();
                        return;
                    }
                    if (data.author_type === 'agent' && data.author_agent_id) {
                        presence.stop(threadId, data.author_agent_id);
                        on.presence();
                    }
                    on.message(toMessage(data));
                }),
                bus.subscribe('channel_presence', (data) => {
                    if (!data || data.channel_id !== threadId || !data.agent_id) return;
                    if (!isLiveThread()) {
                        seal();
                        return;
                    }
                    if (data.phase === 'thinking') {
                        presence.start(threadId, data.agent_id, data.agent_name);
                    } else {
                        presence.stop(threadId, data.agent_id);
                    }
                    on.presence();
                }),
                bus.subscribe('channel_updated', (data) => {
                    if (!data || data.id !== threadId) return;
                    channel = data;
                    if (!isLiveThread()) seal();
                    on.chrome();
                }),
            ];
            return () => {
                offs.splice(0).forEach((off) => off());
                signals = null;
            };
        }

        async function archiveThread() {
            const open = await archive.openTasks(threadId);
            const choice = await archive.prompt(open.count);
            if (archive.isAbort(choice)) return;
            channel = await archive.request(threadId, {
                cancelOpenTasks: choice === 'cancel_and_archive',
            });
            seal();
            signal('chrome');
        }

        /**
         * Rename this thread.
         *
         * The title the header shows afterwards is the SERVER's, read back off
         * the response, so a rename that did not land cannot look like one that
         * did. The view keeps the operator's text on a rejection; this only has
         * to fail loudly.
         *
         * @param {string} nextName  Already trimmed by the caller; trimmed
         *   again here because this is the boundary the server sees.
         * @returns {Promise<void>}
         * @throws {Error} With the server's message on any non-2xx, and before
         *   the request on a name the server would reject anyway.
         */
        async function renameThread(nextName) {
            const name = String(nextName || '').trim();
            if (!name) throw new Error('A thread needs a name.');
            const res = await api(`/api/channels/${threadId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error((await res.text()) || 'Could not rename this thread.');
            channel = await res.json();
            signal('chrome');
        }

        async function reopenThread() {
            const res = await api(`/api/channels/${threadId}/reopen`, { method: 'POST' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not reopen this thread.');
            channel = await res.json();
            signal('chrome');
        }

        /**
         * Title, participant count, and exactly one of Archive / Reopen.
         *
         * A LIVE thread also carries `onRename`, which is what makes its title
         * editable in place. A sealed room does not: archiving seals it against
         * writes, and renaming it is a write. Reopen is the way back.
         *
         * @returns {{title: string, subtitle: string, actions: object[],
         *            onRename?: (name: string) => Promise<void>}}
         */
        function chrome() {
            const archived = !isLiveThread();
            return {
                title: (channel && channel.name) || 'Thread',
                subtitle: `${members().length} participants`,
                onRename: archived ? null : renameThread,
                actions: [archived
                    ? { id: 'channel-reopen-btn', label: 'Reopen', onSelect: reopenThread }
                    : { id: 'channel-archive-btn', label: 'Archive', onSelect: archiveThread }],
            };
        }

        return {
            id: threadId,
            kind: 'thread',
            load,
            send,
            subscribe,
            chrome,
            emptyState: () => ({ title: 'No thread messages yet.', hint: '' }),
            canSend: isLiveThread,
            disabledReason: () => (isLiveThread() ? '' : ARCHIVED_REASON),
        };
    }

    return { createThreadSource, ARCHIVED_REASON };
})();
