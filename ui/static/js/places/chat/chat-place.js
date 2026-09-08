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
    const EMPTY_HINT = 'Pick someone from the roster to start talking';

    let store = null;
    let conversation = null;
    let contextColumn = null;
    let contextEl = null;
    let bodyEl = null;
    let container = null;
    let showingConversation = false;
    // conversationId and conversationKind change together, and each has its
    // own subscriber; without this the pair would open — and re-fetch — twice.
    let openedKey = null;
    const disposers = [];

    function renderEmpty() {
        showingConversation = false;
        openedKey = null;
        clear(bodyEl);
        bodyEl.append(h('div', { class: 'conversation-empty' },
            h('p', { class: 'conversation-empty-title' }, EMPTY_TITLE),
            h('p', { class: 'conversation-empty-hint' }, EMPTY_HINT)));
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
        const state = store.getState();
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
         * @param {object} ctx  `{ store, bus, api, needs, contextEl, navigate }`
         *   from the shell. Chat is the only place with a context column, so it
         *   is the only place that fills `ctx.contextEl`.
         * @returns {void}
         */
        mount(el, ctx) {
            container = el;
            store = ctx.store;
            contextEl = ctx.contextEl;
            showingConversation = false;
            openedKey = null;
            clear(el);
            bodyEl = h('div', { class: 'chat-place-body' });
            // The shell focuses this after navigating; exactly one per place.
            el.append(h('h1', { class: 'visually-hidden', tabindex: '-1' }, 'Chat'), bodyEl);

            conversation = BossModConversation.createConversation({
                store: ctx.store,
                bus: ctx.bus,
                api: ctx.api,
                navigate: ctx.navigate,
                needs: ctx.needs,
                // A desk path from a note, or an agent id from the chrome.
                openDesk: (target) => BossModContextColumn.openDeskFrom(ctx.store, target),
            });

            contextColumn = BossModContextColumn.createContextColumn({
                el: ctx.contextEl,
                store: ctx.store,
                bus: ctx.bus,
                api: ctx.api,
                navigate: ctx.navigate,
            });

            disposers.push(store.subscribe((s) => s.conversationId, applyConversation));
            disposers.push(store.subscribe((s) => s.conversationKind, applyConversation));
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
            // The column must not survive a navigation away from Chat: the
            // shell hides the element, but the subscriptions would leak.
            if (contextColumn) contextColumn.destroy();
            contextColumn = null;
            if (contextEl) clear(contextEl);
            contextEl = null;
            if (container) clear(container);
            container = null;
            bodyEl = null;
            store = null;
            showingConversation = false;
            openedKey = null;
        },
    };
})();

BossModPlaces.register('chat', BossModChatPlace);
