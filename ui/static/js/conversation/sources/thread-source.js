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
    // Shared with the thread-setting requests: one reading of a refusal body.
    const { refusal } = BossModThreadRequests;

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
     * @param {object} ctx.seat  From BossModThreadSeat.createThreadSeat.
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
        const seat = ctx && ctx.seat;
        const forgetCache = ctx && ctx.forgetCache;
        const store = ctx && ctx.store;
        if (typeof api !== 'function') throw new Error('[thread-source] ctx.api is required');
        if (!bus) throw new Error('[thread-source] ctx.bus is required');
        if (!presence) throw new Error('[thread-source] ctx.presence is required');
        if (!archive) throw new Error('[thread-source] ctx.archive is required');
        if (!seat) throw new Error('[thread-source] ctx.seat is required');
        if (typeof forgetCache !== 'function') {
            throw new Error('[thread-source] ctx.forgetCache is required');
        }

        // Rename, pause, resume, auto-approve and reopen: the HTTP half of
        // each header action. What stays here is adopting the answer.
        const requests = BossModThreadRequests.createThreadRequests({ api, threadId });

        let channel = null;
        let signals = null;

        function signal(name) {
            if (signals && typeof signals[name] === 'function') signals[name]();
        }

        function members() {
            return Array.isArray(channel && channel.members) ? channel.members : [];
        }

        function colorFor(id) {
            if (!store || !id) return null;
            const row = (store.getState().roster || []).find((item) => item && item.id === id);
            return (row && row.color) || null;
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
         * Round markers stay on the channel for agent wakes and diagnostics.
         * They are not operator transcript lines — paint them there and they
         * read as confusing chrome ("Round 2") between real posts.
         *
         * @param {object} raw
         * @returns {boolean}
         */
        function isRoundMarker(raw) {
            return !!raw && raw.notification_kind === 'channel_round_marker';
        }

        /**
         * Normalise one backend row. Both the REST load and the WebSocket echo
         * pass through here, so both produce the same dedupe key.
         *
         * @param {object} raw
         * @returns {object} Message
         */
        function toMessage(raw) {
            const card = BossModConsentCard.cardFromMessage(raw);
            const isQueue = raw.notification_kind === 'queue_visibility';
            const isSystem = raw.author_type === 'system';
            const text = raw.content || '';
            const queueKey = raw.author_name || raw.author_agent_id || '';
            const cardKey = card && card.id
                ? `${BossModConsentCard.isCliApprovalCard(card) ? 'cli-approval' : 'consent'}:${card.id}`
                : '';
            return {
                key: isQueue ? `queue-visibility:${queueKey}` : (cardKey || String(raw.message_id || raw.id || '').trim()),
                author: raw.author_type || 'agent',
                authorName: raw.author_name || 'Unknown',
                authorAgentId: raw.author_agent_id || null,
                authorColor: colorFor(raw.author_agent_id),
                showAuthor: true,
                text,
                createdAt: raw.created_at || '',
                kind: card ? 'request' : (isSystem || isQueue ? 'note' : 'message'),
                card,
                deskPath: raw.desk_path || null,
                taskId: raw.task_id || null,
                systemReceipt: false,
                live: isQueue,
                cleared: isQueue && !String(text).trim(),
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
            return (Array.isArray(payload.messages) ? payload.messages : [])
                .filter((raw) => !isRoundMarker(raw))
                .map(toMessage);
        }

        /**
         * Post to the thread.
         *
         * Presence waits for the server. A lane is claimed before thinking is
         * broadcast; a turn with no lane arrives as Queued, not as thinking.
         * The thrown error is the server's reason, so the composer can show it
         * and keep the text.
         *
         * @param {string} text
         * @returns {Promise<void>}
         * @throws {Error} On any failure, so the send gate keeps the draft.
         */
        async function send(text) {
            const res = await api(`/api/channels/${threadId}/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: text }),
            });
            if (!res.ok) {
                throw new Error(await refusal(res, 'Could not post to this thread.'));
            }
        }

        /**
         * Subscribe to this thread's live traffic.
         * @param {{message: Function, reset: Function, presence: Function, chrome: Function}} on
         * @returns {() => void} One disposer that drains all three subscriptions.
         */
        function subscribe(on) {
            signals = on;
            const off = BossModOperatorInvalidate.register({
                id: `thread:${threadId}`,
                topics: ['channel_message', 'channel_presence', 'channel_updated'],
                onEvent(topic, data) {
                    if (topic === 'channel_message') {
                        if (!data || data.channel_id !== threadId) return;
                        if (!isLiveThread()) {
                            seal();
                            return;
                        }
                        if (data.author_type === 'agent' && data.author_agent_id) {
                            presence.stop(threadId, data.author_agent_id);
                            on.presence();
                        }
                        if (isRoundMarker(data)) return;
                        on.message(toMessage(data));
                        return;
                    }
                    if (topic === 'channel_presence') {
                        if (!data || data.channel_id !== threadId || !data.agent_id) return;
                        if (!isLiveThread()) {
                            seal();
                            return;
                        }
                        if (data.phase === 'thinking') {
                            presence.start(threadId, data.agent_id, data.agent_name, { phase: 'thinking' });
                        } else if (data.phase === 'queued') {
                            presence.start(threadId, data.agent_id, data.agent_name, {
                                phase: 'queued',
                                ahead: data.ahead,
                            });
                        } else {
                            presence.stop(threadId, data.agent_id);
                        }
                        on.presence();
                        return;
                    }
                    if (topic === 'channel_updated') {
                        if (!data || data.id !== threadId) return;
                        channel = data;
                        if (!isLiveThread()) seal();
                        on.chrome();
                    }
                },
            });
            return () => {
                off();
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
         * @param {string} nextName
         * @returns {Promise<void>}
         * @throws {Error} From thread-requests.js, with the server's message.
         */
        async function renameThread(nextName) {
            channel = await requests.rename(nextName);
            signal('chrome');
        }

        async function pauseThread() {
            channel = await requests.pause();
            signal('chrome');
        }

        async function resumeThread() {
            channel = await requests.resume();
            signal('chrome');
        }

        /**
         * Opt this thread in or out of System AI auto-approve.
         *
         * Off is the default. The request writes only the thread flag.
         *
         * @param {boolean} enabled
         * @returns {Promise<void>}
         */
        async function setCliAutoApprove(enabled) {
            channel = await requests.setCliAutoApprove(enabled);
            signal('chrome');
        }

        async function reopenThread() {
            channel = await requests.reopen();
            signal('chrome');
        }

        /**
         * Seat one live agent who is not already in this room.
         *
         * Cancel is null, not a failure. A rejected seat throws so chrome's
         * onError can say so — adding someone must never look like it worked.
         *
         * @returns {Promise<void>}
         */
        async function seatAgent() {
            const updated = await seat.pickAndSeat(threadId, members());
            if (!updated) return;
            channel = updated;
            signal('chrome');
        }

        /**
         * Title, participant count, Add to thread, and Archive / Reopen.
         *
         * A LIVE thread also carries `onRename`, which is what makes its title
         * editable in place. A sealed room does not: archiving seals it against
         * writes, and renaming it is a write. Reopen is the way back.
         *
         * Archive / Reopen sit behind the header's `⋯`. Add to thread stays on
         * the action row: seating someone is the ordinary next step, not a
         * once-a-month seal.
         *
         * @returns {{title: string, subtitle: string, actions: object[],
         *            onRename?: (name: string) => Promise<void>}}
         */
        function chrome() {
            const archived = !isLiveThread();
            const actions = [];
            if (!archived) {
                actions.push({
                    id: 'channel-seat-btn',
                    label: 'Add to thread',
                    icon: 'user-plus',
                    iconOnly: true,
                    onSelect: seatAgent,
                });
                actions.push({
                    id: 'channel-cli-auto-approve',
                    kind: 'switch',
                    slot: 'menu',
                    label: 'Auto-approve safe commands',
                    pressed: !!(channel && channel.cli_auto_approve),
                    onSelect: setCliAutoApprove,
                });
                actions.push(channel && channel.conversation_paused
                    ? {
                        id: 'channel-resume-btn',
                        label: 'Resume',
                        icon: 'play',
                        slot: 'menu',
                        onSelect: resumeThread,
                    }
                    : {
                        id: 'channel-pause-btn',
                        label: 'Pause thread',
                        icon: 'pause',
                        slot: 'menu',
                        onSelect: pauseThread,
                    });
            }
            actions.push(archived
                ? {
                    id: 'channel-reopen-btn',
                    label: 'Reopen',
                    icon: 'archive-restore',
                    slot: 'menu',
                    onSelect: reopenThread,
                }
                : {
                    id: 'channel-archive-btn',
                    label: 'Archive',
                    icon: 'archive',
                    slot: 'menu',
                    onSelect: archiveThread,
                });
            return {
                title: (channel && channel.name) || 'Thread',
                subtitle: `${members().length} participants`,
                onRename: archived ? null : renameThread,
                actions,
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
