/**
 * BossMod AI — the one conversation controller.
 *
 * It owns everything that must outlive a single conversation: the load
 * generation, the presence model, the transcript cache, and the composer. The
 * transcript and the composer are built once and reconfigured on every open,
 * because rebuilding the composer on a switch would take the operator's draft
 * and caret with it.
 *
 * Sources normalise a backend conversation into Messages and never touch the
 * DOM; the views render Messages and never read a backend field. This module
 * is the only place the two meet.
 */
const BossModConversation = (() => {
    const { h } = BossModDom;

    const NO_CONVERSATION_REASON = 'Pick someone from the roster to start talking.';

    /**
     * Build the conversation surface.
     *
     * @param {object} deps
     * @param {object} deps.store
     * @param {object} deps.bus
     * @param {Function} deps.api  Authenticated fetch helper, injected by the shell.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @param {object} deps.needs  From createNeedsStore. The bar above the
     *   composer shows the open conversation's share of the queue (spec 5.5).
     * @param {(path: string) => void} [deps.openDesk]  Optional; Phase 2B injects it.
     * @returns {{ element: HTMLElement,
     *             open: (id: string, kind: 'agent'|'thread') => Promise<void>,
     *             destroy: () => void }}
     * @throws {Error} When store, bus, api, navigate, or needs is missing.
     */
    function createConversation(deps) {
        const { store, bus, api, navigate, needs, openDesk } = deps || {};
        if (!store) throw new Error('[conversation] deps.store is required');
        if (!bus) throw new Error('[conversation] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[conversation] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[conversation] deps.navigate is required');
        if (!needs) throw new Error('[conversation] deps.needs is required');

        const generation = BossModGates.createLoadGeneration();
        const presence = BossModGates.createChannelPresenceController();
        const cache = BossModTranscriptCache.createCache();
        // One unsent draft per conversation: switching away must not throw away
        // what the operator had half-typed.
        const drafts = new Map();
        const disposers = [];
        const cardCtx = { api, navigate, openDesk };

        let source = null;
        let unsubscribe = null;
        let currentId = null;
        let currentKind = null;
        // Live messages that land between subscribing and the first paint are
        // buffered rather than dropped; setMessages would otherwise erase them.
        let pendingLive = null;

        /**
         * When the roster says this agent's active turn began, or null. The
         * transcript asks; only the controller reads the store.
         * @param {string} agentId
         * @returns {string|null}
         */
        function activitySince(agentId) {
            const row = (store.getState().roster || [])
                .find((item) => item && item.id === agentId);
            return (row && row.currentActivitySince) || null;
        }

        const transcript = BossModTranscript.createTranscript({
            presence,
            activitySince,
            renderMessage: BossModMessage.renderMessage,
            renderEventCard: (message) => BossModEventCards.renderEventCard(message, cardCtx),
        });

        /** Spec 4.4: the clipboard and the empty state open the ONE assign form. */
        function openAssign() {
            return BossModAssignForm.openAssignForm({
                api,
                store,
                bindOrigin: true, onCreated: () => navigate('board'),
            });
        }

        const composer = BossModComposer.createComposer({
            store,
            onSend: (text) => {
                if (!source) throw new Error('[conversation] no conversation is open');
                return source.send(text);
            },
            canSend: () => Boolean(source) && source.canSend(),
            disabledReason: () => (source ? source.disabledReason() : NO_CONVERSATION_REASON),
            onAssign: openAssign,
        });

        const systemReceipts = BossModSystemReceipts.createSystemReceiptsToggle({
            onChange: () => {
                const cached = currentId ? cache.recall(currentId) : null;
                if (cached) paint(cached);
            },
        });

        const chrome = BossModConversationChrome.createChrome({
            onError: (message) => composer.setError(message),
            // A preference about the VIEW, not an action on the person, so it
            // goes behind the header's `⋯`. See chrome.js's `viewOptions`.
            viewOptions: [systemReceipts.element],
        });

        // Directly above the composer: what needs the operator where they are
        // already looking. Everything else goes to the toast.
        const needsBar = BossModNeedsBar.createNeedsBar({ store, needs, navigate });

        const element = h('div', { class: 'conversation' },
            chrome.element,
            transcript.element,
            needsBar.element,
            composer.element);

        function applyChrome() {
            if (source) chrome.apply(source.chrome());
        }

        // ── Painting ──

        function visibleMessages(messages) {
            if (systemReceipts.isEnabled()) return messages;
            return messages.filter((message) => !message.systemReceipt);
        }

        function paint(messages) {
            const visible = visibleMessages(messages);
            // The source says WHO; the controller supplies what can be done,
            // because it owns the composer and the assign form.
            if (visible.length) transcript.setStatus('ready');
            else transcript.setStatus('empty', Object.assign({}, source.emptyState(), {
                onGreet: (text) => composer.sendText(text),
                onAssign: openAssign,
            }));
            transcript.setMessages(visible);
            transcript.renderPresence(currentId);
        }

        const handlers = {
            message(message) {
                if (pendingLive) {
                    pendingLive.push(message);
                    return;
                }
                if (!systemReceipts.isEnabled() && message.systemReceipt) {
                    cache.append(currentId, message);
                    return;
                }
                if (transcript.append(message)) cache.append(currentId, message);
            },
            reset() {
                cache.forget(currentId);
                void open(currentId, currentKind);
            },
            presence() { transcript.renderPresence(currentId); },
            chrome() { applyChrome(); composer.applyState(); },
        };

        function disposeSource() {
            if (unsubscribe) unsubscribe();
            unsubscribe = null;
            source = null;
        }


        function buildSource(id, kind) {
            if (kind === 'agent') {
                // openDesk is optional and documented (spec 4.1); the source
                // renders its Desk toggle only when it arrives.
                return BossModAgentSource.createAgentSource(id, {
                    api, bus, store, presence, openDesk,
                });
            }
            if (kind === 'thread') {
                return BossModThreadSource.createThreadSource(id, {
                    api,
                    bus,
                    presence,
                    archive: BossModThreadArchive.createThreadArchive({ api }),
                    forgetCache: (conversationId) => {
                        cache.forget(conversationId);
                        drafts.delete(conversationId);
                    },
                });
            }
            throw new Error(`[conversation] unknown conversation kind "${kind}"`);
        }

        /**
         * Open one conversation.
         *
         * A cached transcript is painted immediately and the loading state is
         * skipped entirely, so a re-click is not a flash of empty room. A load
         * whose generation is no longer current is discarded before it reaches
         * the DOM.
         *
         * @param {string} conversationId
         * @param {'agent'|'thread'} kind
         * @returns {Promise<void>} Resolves once painted; never rejects — a
         *   failed load becomes the transcript's error state with a retry.
         * @throws {Error} Synchronously, for a missing id or an unknown kind.
         */
        async function open(conversationId, kind) {
            const id = String(conversationId || '').trim();
            if (!id) throw new Error('[conversation] open() needs a conversation id');

            const loadId = generation.next();
            // Stash unconditionally, so a retry or a chat_reset re-opening the
            // SAME conversation does not silently discard what was typed.
            if (currentId) drafts.set(currentId, composer.readDraft());
            disposeSource();
            currentId = id;
            currentKind = kind;
            source = buildSource(id, kind);
            pendingLive = [];
            unsubscribe = source.subscribe(handlers);

            const cached = cache.recall(id);
            if (cached) paint(cached);
            else {
                transcript.setMessages([]);
                transcript.setStatus('loading');
            }
            chrome.reset();  // a half-typed rename must not follow the switch
            applyChrome();
            composer.setDraft(drafts.get(id) || '');
            composer.applyState();

            let messages;
            try {
                messages = await source.load();
            } catch (err) {
                if (!generation.isCurrent(loadId)) return;
                console.error(`[conversation] could not load ${kind} ${id}`, err);
                pendingLive = null;
                transcript.setStatus('error', {
                    message: (err && err.message) || 'This conversation could not be loaded.',
                    onRetry: () => { void open(id, kind); },
                });
                return;
            }
            if (!generation.isCurrent(loadId)) return;

            cache.remember(id, messages);
            paint(messages);
            applyChrome();
            composer.applyState();

            const buffered = pendingLive || [];
            pendingLive = null;
            buffered.forEach((message) => handlers.message(message));
        }

        // The chrome names the agent, and the roster is what knows the name.
        disposers.push(store.subscribe(
            (s) => {
                if (currentKind !== 'agent') return '';
                const row = (s.roster || []).find((item) => item && item.id === currentId);
                return row ? row.name : '';
            },
            () => { if (source) applyChrome(); }));

        // A presence row that says "working · 12m" has to keep saying the
        // right number. The roster is replaced on every simulation tick, so
        // that reference IS the repaint clock — without this the duration
        // freezes at whatever it read when the turn was announced. Note this
        // is a STORE subscription, not a socket one: the surface still never
        // remounts on a tick, which test_meeting_ui_incremental.py enforces.
        disposers.push(store.subscribe(
            (s) => s.roster,
            () => { if (currentId) transcript.renderPresence(currentId); }));

        return {
            element,
            open,
            /**
             * Drain every subscription this controller created.
             * @returns {void}
             */
            destroy() {
                disposeSource();
                composer.destroy();
                chrome.destroy();
                needsBar.destroy();
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createConversation };
})();
