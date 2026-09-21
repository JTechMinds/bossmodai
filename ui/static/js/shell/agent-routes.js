/**
 * BossMod AI — the two ways into an agent, wherever the operator starts.
 *
 * The roster's People rows and the Office floor both offer an agent's chat and
 * an agent's desk, and both must mean exactly what the roster always meant. So
 * the store writes live here once, and every door calls them with its own
 * store and navigate — injected, never read off a global.
 *
 * Navigation is only how the operator REACHES Chat; the store is what switches
 * the conversation or the context column. Navigating while already there
 * would remount the place and take the transcript cache, the composer draft,
 * and the caret with it on every click.
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

    /**
     * Open one agent's desk in Chat's context column.
     *
     * @param {{store: object, navigate: (placeId: string) => void}} deps
     * @param {string} agentId
     * @returns {void}
     * @throws {Error} On missing deps or an empty agent id.
     */
    function openDesk(deps, agentId) {
        const { store, navigate } = requireDeps(deps);
        if (!agentId) throw new Error('[agent-routes] a desk needs an agent id');
        store.setState({ contextMode: 'desk', deskAgentId: agentId });
        if (store.getState().place !== 'chat') navigate('chat');
    }

    return { openConversation, openDesk };
})();
