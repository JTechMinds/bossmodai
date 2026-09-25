/** BossMod AI — one conversation controller (sources meet transcript + composer). */
const BossModConversation = (() => {
    const { h } = BossModDom;

    const NO_CONVERSATION_REASON = 'Pick someone from the roster to start talking.';

    /** @param {object} deps store, bus, api, navigate, needs; optional openDesk */
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

        function openDeliverable(path, agentId) {
            if (typeof BossModTaskDeliverables !== 'undefined'
                && typeof BossModTaskDeliverables.openDeliverablePath === 'function') {
                return BossModTaskDeliverables.openDeliverablePath(api, path, agentId || '');
            }
            if (typeof openDesk === 'function') openDesk(path);
            return undefined;
        }
        cardCtx.openDeliverable = openDeliverable;

        let source = null;
        let unsubscribe = null;
        /** @type {((topic: string, data: any) => void)|null} */
        let liveSink = null;
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

        /**
         * Open the ONE assign form. The empty state's `Assign a task` is its
         * only caller here now; the Tasks place owns the third door.
         * @returns {object} The open modal, from BossModAssignForm.
         */
        function openAssign() {
            return BossModAssignForm.openAssignForm({
                api,
                store,
                bindOrigin: true, onCreated: () => navigate('tasks'),
            });
        }

        const composer = BossModComposer.createComposer({
            store,
            onSend: (text, attachmentIds) => {
                if (!source) throw new Error('[conversation] no conversation is open');
                return source.send(text, attachmentIds);
            },
            canSend: () => Boolean(source) && source.canSend(),
            disabledReason: () => (source ? source.disabledReason() : NO_CONVERSATION_REASON),
            onAttach: (files, ctx) => {
                const results = [];
                for (const f of files) {
                    results.push(BossModApi.uploadAttachment(f, ctx));
                }
                return Promise.all(results);
            },
            getContext: () => {
                if (!source) return { type: 'unscoped', id: '' };
                return source.context ? source.context() : { type: 'direct', id: '' };
            },
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

        function shellExecutorActivityCard(entry) {
            const event = String((entry && entry.event) || '');
            if (event === 'shell_executor_enabled') {
                return entry.host_path_consent || {
                    status: 'enabled',
                    kind: 'shell_executor',
                    grant_root: 'cli_shell_enabled',
                    decision_note: entry.detail,
                };
            }
            return null;
        }

        // ── Painting ──

        function visibleMessages(messages) {
            if (systemReceipts.isEnabled()) return messages;
            return messages.filter((message) => !message.systemReceipt);
        }

        /** Pending request-card ids in this transcript (or the live buffer). */
        function pendingRequestIds(messages) {
            const ids = [];
            (messages || []).forEach((message) => {
                if (message.kind !== 'request' || !message.card || !message.card.id) return;
                if ((message.card.status || 'pending') !== 'pending') return;
                ids.push(String(message.card.id));
            });
            return ids;
        }

        function openRequestIds() {
            const cached = visibleMessages(cache.recall(currentId) || []);
            return pendingRequestIds(cached.concat(pendingLive || []));
        }

        function syncInlineNeedIds(messages) {
            const ids = pendingRequestIds(messages);
            const current = store.getState().inlineNeedIds || [];
            if (current.length === ids.length && current.every((id, i) => id === ids[i])) return;
            store.setState({ inlineNeedIds: ids });
        }

        function paint(messages) {
            const visible = visibleMessages(messages);
            if (visible.length) transcript.setStatus('ready');
            else transcript.setStatus('empty', Object.assign({}, source.emptyState(), {
                onGreet: (text) => composer.sendText(text),
                onAssign: openAssign,
            }));
            transcript.setMessages(visible);
            transcript.renderPresence(currentId);
            syncInlineNeedIds(visible);
        }

        /** Live-paint pending chrome in Focus; never wait on a bell fetch. */
        async function reloadIfPendingChromeMissing() {
            const id = currentId;
            const src = source;
            if (!src || !id) return;
            const inline = new Set(openRequestIds());
            const missing = (store.getState().needs || []).filter((need) => (
                need
                && need.target
                && need.target.place === 'chat'
                && BossModNeedShape.belongsOnOpenFocus(need, id, currentKind)
                && !BossModNeedShape.coversInlineNeed(need, inline)
            ));
            if (!missing.length) return;
            missing.forEach((need) => {
                if (need.conversationId === id) return;
                const message = BossModNeedShape.requestMessageFromNeed(need);
                if (message) handlers.message(message);
            });
            if (!missing.some((need) => need.conversationId === id)) return;
            if (pendingLive) return;
            try {
                const messages = await src.load();
                if (source !== src || currentId !== id || pendingLive) return;
                cache.remember(id, messages);
                paint(messages);
            } catch (err) {
                console.error(`[conversation] could not refresh ${id}`, err);
            }
        }

        /** Fail-closed refetch when resync or operator_invalidate says Focus drifted. */
        const refetchOpenConversation = BossModConversationFocus.createRefetch({
            getContext: () => ({
                id: currentId,
                kind: currentKind,
                source,
                pendingLive,
            }),
            cache,
            paint,
            applyChrome,
            applyComposer: () => composer.applyState(),
            transcript,
            reopen: open,
        });

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
                syncInlineNeedIds(visibleMessages(cache.recall(currentId) || []));
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
            liveSink = null;
            source = null;
        }

        function buildSource(id, kind) {
            if (kind === 'agent') {
                return BossModAgentSource.createAgentSource(id, {
                    api, bus, store, presence, openDesk,
                });
            }
            if (kind === 'thread') {
                return BossModThreadSource.createThreadSource(id, {
                    api, bus, store, presence,
                    archive: BossModThreadArchive.createThreadArchive({ api }),
                    seat: BossModThreadSeat.createThreadSeat({ api, store }),
                    forgetCache: (conversationId) => {
                        cache.forget(conversationId);
                        drafts.delete(conversationId);
                    },
                });
            }
            throw new Error(`[conversation] unknown conversation kind "${kind}"`);
        }

        /**
         * Open one conversation. A cached transcript is painted immediately
         * and the loading state skipped, so a re-click is not a flash of empty
         * room; a load whose generation is stale is discarded before the DOM.
         * @param {string} conversationId @param {'agent'|'thread'} kind
         * @returns {Promise<void>} Resolves once painted; never rejects.
         * @throws {Error} For a missing id or an unknown kind.
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
            cardCtx.agentId = kind === 'agent' ? id : null;
            source = buildSource(id, kind);
            pendingLive = [];
            const live = source.subscribe(handlers);
            unsubscribe = live.dispose;
            liveSink = live.onLiveEvent;

            const cached = cache.recall(id);
            if (cached) paint(cached);
            else {
                transcript.setMessages([]);
                transcript.setStatus('loading');
                syncInlineNeedIds([]);
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
            void reloadIfPendingChromeMissing();
        }

        // The chrome names the agent, and the roster is what knows the name.
        disposers.push(store.subscribe(
            (s) => {
                if (currentKind !== 'agent') return '';
                const row = (s.roster || []).find((item) => item && item.id === currentId);
                return row ? row.name : '';
            },
            () => { if (source) applyChrome(); }));

        // Roster ticks are the presence duration clock; never remount on them.
        disposers.push(store.subscribe(
            (s) => s.roster,
            () => { if (currentId) transcript.renderPresence(currentId); }));

        disposers.push(bus.subscribe('activity', (entry) => {
            const card = shellExecutorActivityCard(entry);
            if (card) BossModConsentCard.collapseGrantedConsentCards(card);
            const event = String((entry && entry.event) || '');
            if (event === 'cli_approval_approved' || event === 'cli_approval_rejected'
                || event === 'cli_approval_resolved') {
                BossModConsentCard.collapseGrantedConsentCards({
                    kind: 'cli_approval',
                    status: event === 'cli_approval_rejected'
                        ? 'rejected'
                        : ((entry && entry.status) || 'approved'),
                    command: (entry && entry.command) || '',
                    cwd: (entry && entry.cwd) || '',
                    decision_note: (entry && entry.decision_note) || '',
                });
            }
        }));

        disposers.push(store.subscribe(
            (s) => s.needs,
            () => { void reloadIfPendingChromeMissing(); },
        ));

        disposers.push(BossModConversationFocus.attach({
            getLiveSink: () => liveSink,
            getOpenTarget: () => ({ id: currentId, kind: currentKind }),
            refetchOpen: refetchOpenConversation,
        }));

        return {
            element,
            open,
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
