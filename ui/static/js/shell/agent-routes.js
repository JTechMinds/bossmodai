/**
 * BossMod AI — the way into an agent's conversation, wherever the operator
 * starts.
 *
 * The roster's People rows, the Office floor, the Tasks place and the desk all
 * offer an agent's chat, and all must mean exactly what the roster always
 * meant. So the store write lives here once, and every door calls it with its
 * own store and navigate — injected, never read off a global.
 *
 * ONE door now. The desk used to be the second: a store write that switched
 * Chat's context column. It is a modal (context/desk-dialog.js) reached
 * through the injected `ctx.openDesk`, and has nothing to write here.
 *
 * Navigation is only how the operator REACHES Chat; the store is what switches
 * the conversation. Navigating while already there would remount the place
 * and take the transcript cache, the composer draft, and the caret with it on
 * every click. A Chat covered by the Settings takeover is not on screen.
 * Navigating uncovers it, and the navigator does not remount a place it only
 * uncovers, so the draft rule still holds.
 */
const BossModAgentRoutes = (() => {
    /**
     * @param {{store: object, navigate: (placeId: string) => void}} deps
     * @returns {{store: object, navigate: (placeId: string) => void}}
     * @throws {Error} When either is missing — a door with nowhere to go
     *   should fail at the click, not write half a state.
     */
    function requireDeps(deps) {
        const { store, navigate } = deps || {};
        if (!store) throw new Error('[agent-routes] deps.store is required');
        if (typeof navigate !== 'function') throw new Error('[agent-routes] deps.navigate is required');
        return { store, navigate };
    }

    /**
     * Show one conversation in Chat.
     *
     * @param {{store: object, navigate: (placeId: string) => void}} deps
     * @param {string} id  The agent's or the thread's id.
     * @param {'agent'|'thread'} kind
     * @returns {void}
     * @throws {Error} On missing deps, an empty id, or an unknown kind.
     */
    function openConversation(deps, id, kind) {
        const { store, navigate } = requireDeps(deps);
        if (!id) throw new Error('[agent-routes] a conversation needs an id');
        if (kind !== 'agent' && kind !== 'thread') {
            throw new Error(`[agent-routes] unknown conversation kind "${kind}"`);
        }
        store.setState({ conversationId: id, conversationKind: kind });
        const state = store.getState();
        if (state.place !== 'chat' || state.settingsOpen === true) navigate('chat');
    }

    /**
     * Leave a conversation whose subject is gone — an agent just removed
     * through its `⋯` (context/agent-menu.js) — when it is the one Chat has
     * open, so Chat shows its empty state rather than a deleted agent.
     *
     * @param {object} store  Application store.
     * @param {string} id  The agent's or the thread's id.
     * @param {'agent'|'thread'} kind
     * @returns {void}  Nothing changes when another conversation, or none,
     *   is open: only the one that lost its subject is left.
     * @throws {Error} On a missing store, an empty id, or an unknown kind.
     */
    function closeConversation(store, id, kind) {
        if (!store) throw new Error('[agent-routes] closeConversation needs the store');
        if (!id) throw new Error('[agent-routes] a conversation needs an id');
        if (kind !== 'agent' && kind !== 'thread') {
            throw new Error(`[agent-routes] unknown conversation kind "${kind}"`);
        }
        const state = store.getState();
        if (state.conversationId !== id || state.conversationKind !== kind) return;
        store.setState({ conversationId: null, conversationKind: null });
    }

    return { openConversation, closeConversation };
})();
