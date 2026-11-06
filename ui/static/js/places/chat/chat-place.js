/**
 * BossMod AI — the Chat place.
 *
 * The centre column when `store.place` is 'chat': one conversation for
 * `store.conversationId`, or the empty state that is the first thing an
 * operator ever sees.
 *
 * It registers itself, so the nav order in shell/places.js is never edited to
 * add a real place.
 */
const BossModChatPlace = (() => {
    const { h, clear } = BossModDom;

    const EMPTY_TITLE = 'No conversation open';
    const EMPTY_HINT = 'Pick a person or thread to start talking';

    let ctx = null;  // The shell's, whole: store, context column, roster drawer.
    let conversation = null;
    let bodyEl = null;
    let container = null;
    let showingConversation = false;
    // conversationId and conversationKind change together, and each has its
    // own subscriber; without this the pair would open — and re-fetch — twice.
    let openedKey = null;
    const disposers = [];
    // Page-lifetime: each mount builds a new controller; drafts must survive.
    const drafts = new Map();
    const transcriptCache = BossModTranscriptCache.createCache();

    function renderEmpty() {
        showingConversation = false;
        openedKey = null;
        clear(bodyEl);
        // The button shows only where the rail is hidden (conversation.css).
        bodyEl.append(h('div', { class: 'conversation-empty' },
            h('p', { class: 'conversation-empty-title' }, EMPTY_TITLE),
            h('p', { class: 'conversation-empty-hint' }, EMPTY_HINT),
            h('button', { class: 'btn conversation-empty-browse', type: 'button',
                onclick: () => ctx.openRoster() }, 'Choose a conversation')));
    }

    /**
     * Show the conversation named by the store, or the empty state.
     *
     * A conversation switch re-points the one controller; it never remounts
     * the place, so the composer, its draft, and the caret survive every click
     * in the roster.
     *
     * @returns {void}
     */
    function applyConversation() {
        const state = ctx.store.getState();
        if (!state.conversationId || !state.conversationKind) {
            renderEmpty();
            return;
        }
        const key = `${state.conversationKind}:${state.conversationId}`;
        if (key === openedKey) return;
        openedKey = key;
        if (!showingConversation) {
            clear(bodyEl);
            bodyEl.append(conversation.element);
            showingConversation = true;
        }
        void conversation.open(state.conversationId, state.conversationKind);
    }

    return {
        label: 'Chat',
        icon: 'message-circle',
        hasContext: true,

        /**
         * @param {HTMLElement} el
         * @param {object} placeCtx  `{ store, bus, api, needs, contextEl, openDesk, agentMenu,
         *   openRoster, navigate }` from the shell. Chat alone fills `contextEl`, with the office
         *   summary and chatter; `openDesk` is the one desk modal; `openRoster` the roster drawer.
         * @returns {void}
         */
        mount(el, placeCtx) {
            ctx = placeCtx;
            container = el;
            showingConversation = false;
            openedKey = null;
            clear(el);
            bodyEl = h('div', { class: 'chat-place-body' });
            // The shell focuses this after navigating; exactly one per place.
            el.append(h('h1', { class: 'visually-hidden', tabindex: '-1' }, 'Chat'), bodyEl);

            if (typeof BossModMentions !== 'undefined') {
                BossModMentions.configure({ store: ctx.store, navigate: ctx.navigate,
                    onViewDesk: (agent) => ctx.openDesk(agent.id) });
            }

            conversation = BossModConversation.createConversation({
                store: ctx.store,
                bus: ctx.bus,
                api: ctx.api,
                navigate: ctx.navigate,
                needs: ctx.needs,
                drafts, cache: transcriptCache,
                // A deliverable's desk; the agent `⋯` rows and a memory line's Memory.
                openDesk: ctx.openDesk, agentMenu: ctx.agentMenu,
                // One screen button per live-view extension in an agent's header.
                liveViews: BossModExtensionsLive.headerCapability(),
            });

            // The column's two views; the desk is a modal. Their destroyers drain
            // with `disposers`, before unmount clears the column they live in.
            clear(ctx.contextEl);
            const miniOffice = BossModMiniOffice.createMiniOffice({
                store: ctx.store, api: ctx.api, navigate: ctx.navigate, openDesk: ctx.openDesk,
            });
            const officeChatter = BossModOfficeChatter.createOfficeChatter({
                store: ctx.store, bus: ctx.bus, api: ctx.api, openDesk: ctx.openDesk,
            });
            ctx.contextEl.append(miniOffice.element, officeChatter.element);
            disposers.push(() => miniOffice.destroy(), () => officeChatter.destroy());

            disposers.push(ctx.store.subscribe((s) => s.conversationId, applyConversation));
            disposers.push(ctx.store.subscribe((s) => s.conversationKind, applyConversation));
            applyConversation();
        },

        /**
         * Drain every subscription and destroy the conversation.
         *
         * The shell's leak guard asserts the bus subscriber count returns to
         * baseline after repeated navigation; a missed disposer fails it.
         *
         * @returns {void}
         */
        unmount() {
            disposers.splice(0).forEach((off) => off());
            if (conversation) conversation.destroy();
            conversation = null;
            if (typeof BossModMentions !== 'undefined') BossModMentions.configure(null);
            if (ctx) clear(ctx.contextEl);
            if (container) clear(container);
            container = null;
            bodyEl = null;
            ctx = null;
            showingConversation = false;
            openedKey = null;
        },
    };
})();

BossModPlaces.register('chat', BossModChatPlace);
