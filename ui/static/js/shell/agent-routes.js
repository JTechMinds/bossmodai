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
 * every click.
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
        if (store.getState().place !== 'chat') navigate('chat');
    }

    return { openConversation };
})();
