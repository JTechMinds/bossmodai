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

        let signals = null;

        function signal(name) {
            if (signals && typeof signals[name] === 'function') signals[name]();
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
         * receipts and consent asks are system messages that are NEVER hidden —
         * one is the operator's proof an agent moved, the other is a decision
         * they still owe.
         *
         * @param {object} raw
         * @returns {object} Message
         */
        function toMessage(raw) {
            const isSystem = raw.from === 'system' || raw.message_type === 'system';
            const isWalkReceipt = raw.notification_kind === 'receipt';
            const consent = BossModConsentCard.isHostPathConsentMessage(raw)
                && raw.host_path_consent;
            return {
                key: String(raw.id || raw.message_id || '').trim(),
                author: raw.from || 'agent',
                authorName: raw.from_name || '',
                showAuthor: false,
                text: raw.content || '',
                createdAt: raw.created_at || '',
                kind: consent ? 'request' : (isSystem ? 'note' : 'message'),
                card: raw.host_path_consent || null,
                deskPath: raw.desk_path || null,
                systemReceipt: isSystem && !isWalkReceipt && !consent,
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
         * Wake the agent with a message.
         *
         * The indicator is cleared in a `finally` because an agent that
         * produced no reply (walk_to, idle) fires no WebSocket event at all —
         * without this it would appear to be thinking forever.
         *
         * @param {string} text
         * @returns {Promise<void>}
         * @throws {Error} On any failure, so the send gate keeps the draft.
         */
        async function send(text) {
            presence.start(agentId, agentId, agent().name);
            signal('presence');
            try {
                const res = await api(`/api/agents/${agentId}/activate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: text }),
                });
                if (!res.ok) throw new Error('Failed to reach agent.');
            } finally {
                presence.stop(agentId, agentId);
                signal('presence');
            }
        }

        /**
         * Subscribe to this agent's live traffic.
         * @param {{message: Function, reset: Function, presence: Function, chrome: Function}} on
         * @returns {() => void} One disposer that drains all three subscriptions.
         */
        function subscribe(on) {
            signals = on;
            const offs = [
                bus.subscribe('chat_message', (data) => {
                    if (!data) return;
                    if (data.agent_id) {
                        // Before the "is this my conversation" guard on purpose:
                        // a reply that lands while the operator is looking
                        // elsewhere must still clear THAT agent's indicator, or
                        // it stays thinking forever.
                        presence.stop(data.agent_id, data.agent_id);
                        on.presence();
                    }
                    if (data.agent_id !== agentId) return;
                    on.message(toMessage(data));
                }),
                bus.subscribe('chat_reset', (data) => {
                    if (!data || data.agent_id !== agentId) return;
                    on.reset();
                }),
                bus.subscribe('meeting_message', (data) => {
                    if (!data || data.agent_id !== agentId) return;
                    // A meeting row carries an author, so it is labelled even
                    // though ordinary DM turns are not.
                    on.message(Object.assign(toMessage({
                        id: data.message_id,
                        from: data.author_type,
                        from_name: data.author_name,
                        content: data.content,
                        created_at: data.created_at,
                    }), { showAuthor: true }));
                }),
            ];
            return () => {
                offs.splice(0).forEach((off) => off());
                signals = null;
            };
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
         * rule event-cards.js follows for "Open in Desk".
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
