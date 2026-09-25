/**
 * BossMod AI — the agent direct-message conversation adapter.
 *
 * Adapts `/api/agents/{id}/messages` and `/api/agents/{id}/activate` plus the
 * `chat_*` and `meeting_message` broadcasts to the one Message shape. Like
 * every source it is a data adapter and never touches the DOM.
 *
 * An agent's meeting turns render inline in their conversation rather than in a
 * separate sub-view: the operator asked one person a question and the answer is
 * the answer, wherever the agent happened to be standing.
 */
const BossModAgentSource = (() => {

    /**
     * Adapt one agent's direct conversation.
     *
     * @param {string} agentId
     * @param {object} ctx
     * @param {Function} ctx.api  Authenticated fetch helper (injected, never global).
     * @param {object} ctx.bus  Topic bus.
     * @param {object} ctx.store  Read for the roster, which names the agent.
     * @param {object} ctx.presence  Shared presence controller.
     * @returns {object} ConversationSource (spec 4.1).
     * @throws {Error} When any capability is missing.
     */
    function createAgentSource(agentId, ctx) {
        const api = ctx && ctx.api;
        const bus = ctx && ctx.bus;
        const store = ctx && ctx.store;
        const presence = ctx && ctx.presence;
        if (typeof api !== 'function') throw new Error('[agent-source] ctx.api is required');
        if (!bus) throw new Error('[agent-source] ctx.bus is required');
        if (!store) throw new Error('[agent-source] ctx.store is required');
        if (!presence) throw new Error('[agent-source] ctx.presence is required');

        function colorFor(id) {
            if (!id) return null;
            const row = (store.getState().roster || []).find((item) => item && item.id === id);
            return (row && row.color) || null;
        }

        /**
         * The roster row for this agent.
         *
         * The roster is fetched by the rail, so during boot it can legitimately
         * not contain this agent yet. The placeholder is a display default, not
         * a swallowed failure: conversation.js repaints the chrome when the
         * agent's name lands.
         *
         * @returns {{id: string, name: string, role: string}}
         */
        function agent() {
            const roster = store.getState().roster || [];
            return roster.find((item) => item && item.id === agentId)
                || { id: agentId, name: 'Agent', role: '' };
        }

        /**
         * Normalise one backend row.
         *
         * `systemReceipt` is what the system-notifications toggle hides. Walk
         * receipts, consent asks, and never-allowed gate notes are NEVER hidden —
         * one is the operator's proof an agent moved, another is a decision
         * they still owe, and a never-allowed deny is not a silent miss.
         *
         * @param {object} raw
         * @returns {object} Message
         */
        function toMessage(raw) {
            const isSystem = raw.from === 'system' || raw.from_type === 'system'
                || raw.author_type === 'system'
                || raw.message_type === 'system';
            const isWalkReceipt = raw.notification_kind === 'receipt';
            const isQueue = raw.notification_kind === 'queue_visibility';
            const card = BossModConsentCard.cardFromMessage(raw);
            const isDecisionAsk = raw.notification_kind === 'cli_approval'
                || raw.notification_kind === 'host_path_consent';
            const isGateNote = raw.notification_kind === 'blocked';
            const cardKey = card && card.id
                ? `${BossModConsentCard.isCliApprovalCard(card) ? 'cli-approval' : 'consent'}:${card.id}`
                : '';
            const text = raw.content || '';
            const author = raw.from || raw.from_type || raw.author_type || 'agent';
            const authorAgentId = raw.author_agent_id || (author === 'agent' ? agentId : null);
            return {
                key: isQueue ? `queue-visibility:${agentId}` : (cardKey || String(raw.id || raw.message_id || '').trim()),
                author,
                authorName: raw.from_name || raw.author_name
                    || (author === 'agent' ? agent().name : ''),
                authorAgentId,
                authorColor: colorFor(authorAgentId),
                showAuthor: author === 'agent',
                text,
                createdAt: raw.created_at || '',
                kind: card ? 'request' : (isSystem || isQueue ? 'note' : 'message'),
                card,
                deskPath: raw.desk_path || null,
                taskId: raw.task_id || null,
                systemReceipt: isSystem && !isWalkReceipt && !card && !isQueue && !isDecisionAsk && !isGateNote,
                live: isQueue,
                cleared: isQueue && !String(text).trim(),
            };
        }

        /**
         * Fetch the last 50 turns.
         * @returns {Promise<object[]>} Messages.
         * @throws {Error} On a non-OK response, so the controller shows its
         *   error state. Falling back to a cached transcript here is what let
         *   a failed load look like a quiet agent.
         */
        async function load() {
            const res = await api(`/api/agents/${agentId}/messages?limit=50`, { cache: 'no-store' });
            if (!res.ok) throw new Error((await res.text()) || 'Could not load this conversation.');
            const rows = await res.json();
            return (Array.isArray(rows) ? rows : []).map(toMessage);
        }

        /**
         * The server's reason, when it sent one. Otherwise the standing line.
         *
         * @param {Response} res
         * @returns {Promise<string>}
         */
        async function refusal(res) {
            const fallback = 'Failed to reach agent.';
            let raw = '';
            try { raw = await res.text(); } catch { raw = ''; }
            const text = String(raw || '').trim();
            if (text.startsWith('{')) {
                try {
                    const data = JSON.parse(text);
                    const detail = data && data.detail;
                    if (typeof detail === 'string' && detail.trim()) return detail.trim();
                } catch { /* keep the fallback */ }
            }
            return text || fallback;
        }

        /**
         * Wake the agent with a message.
         *
         * Presence waits for the server. A lane is claimed before thinking;
         * no lane is Queued on the desk, not a thinking row. A refusal throws
         * the server's reason so the composer can show it.
         *
         * @param {string} text
         * @returns {Promise<void>}
         * @throws {Error} On any failure, so the send gate keeps the draft.
         */
        async function send(text) {
            const res = await api(`/api/agents/${agentId}/activate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: text }),
            });
            if (!res.ok) throw new Error(await refusal(res));
        }

        /**
         * Subscribe to this agent's live traffic.
         * @param {{message: Function, reset: Function, presence: Function, chrome: Function}} on
         * @returns {() => void} One disposer that drains all three subscriptions.
         */
        function subscribe(on) {
            return BossModOperatorInvalidate.register({
                id: `agent:${agentId}`,
                topics: [
                    'chat_message',
                    'chat_reset',
                    'agent_presence',
                    'channel_message',
                    'meeting_message',
                ],
                onEvent(topic, data) {
                    if (topic === 'chat_message') {
                        if (!data) return;
                        if (data.agent_id) {
                            presence.stop(data.agent_id, data.agent_id);
                            on.presence();
                        }
                        if (data.agent_id !== agentId) return;
                        on.message(toMessage(data));
                        return;
                    }
                    if (topic === 'chat_reset') {
                        if (!data || data.agent_id !== agentId) return;
                        on.reset();
                        return;
                    }
                    if (topic === 'agent_presence') {
                        if (!data || data.agent_id !== agentId) return;
                        if (data.phase === 'thinking') {
                            presence.start(agentId, agentId, data.agent_name || agent().name, { phase: 'thinking' });
                        } else if (data.phase === 'queued') {
                            presence.start(agentId, agentId, data.agent_name || agent().name, {
                                phase: 'queued',
                                ahead: data.ahead,
                            });
                        } else {
                            presence.stop(agentId, agentId);
                        }
                        on.presence();
                        return;
                    }
                    if (topic === 'channel_message') {
                        if (!data) return;
                        const card = BossModConsentCard.cardFromMessage(data);
                        if (!card) return;
                        const owner = String(card.agent_id || data.author_agent_id || '');
                        if (owner !== agentId) return;
                        on.message(toMessage(data));
                        return;
                    }
                    if (topic === 'meeting_message') {
                        if (!data || data.agent_id !== agentId) return;
                        on.message(Object.assign(toMessage({
                            id: data.message_id,
                            from: data.author_type,
                            from_name: data.author_name,
                            content: data.content,
                            created_at: data.created_at,
                        }), { showAuthor: true }));
                    }
                },
            });
        }

        /**
         * Face, name, role, and — when the context column is there to receive
         * it — the Desk toggle.
         *
         * `avatar` and `icon` are DATA: a name, a colour, and a glyph name. The
         * adapter still builds nothing, which is what keeps the conversation
         * from having to read the roster from inside its view.
         *
         * `ctx.openDesk` is an optional, documented capability (spec 4.1), and
         * the action renders only when it is injected. A control that renders
         * but does nothing is worse than one that is absent, which is the same
         * rule event-cards.js follows for the origin note link.
         *
         * @returns {{title: string, subtitle: string, avatar: object, actions: object[]}}
         */
        function chrome() {
            const who = agent();
            const actions = [];
            if (typeof ctx.openDesk === 'function') {
                actions.push({
                    id: 'conversation-desk-toggle',
                    label: 'Desk',
                    icon: 'lamp-desk',
                    // The lamp says desk on its own, and the word beside it
                    // spent a quarter of the header row repeating the glyph.
                    // `iconOnly` keeps `label` as the accessible name and as
                    // the hover tooltip, so nothing is lost but the ink.
                    iconOnly: true,
                    onSelect: () => ctx.openDesk(agentId),
                });
            }
            return {
                title: who.name,
                subtitle: who.role || '',
                avatar: { name: who.name, color: who.color || null },
                actions,
            };
        }

        return {
            id: agentId,
            kind: 'agent',
            load,
            send,
            subscribe,
            chrome,
            // The face too: a conversation nobody has spoken in is where the
            // operator is least sure who they are looking at.
            emptyState: () => ({
                title: `Chat with ${agent().name}`,
                hint: 'Send a message to activate this agent.',
                avatar: { name: agent().name, color: agent().color || null },
            }),
            // Model gating is the composer's job, through the store.
            canSend: () => true,
            disabledReason: () => '',
        };
    }

    return { createAgentSource };
})();
