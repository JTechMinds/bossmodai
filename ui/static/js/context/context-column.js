/**
 * BossMod AI — the right-hand column on Chat.
 *
 * Two modes and one rule: whoever is showing owns its own subscriptions, and a
 * switch unmounts the outgoing view and drains them BEFORE mounting the
 * incoming one — exactly what the shell does for places. Two views holding
 * subscriptions at once is the leak this design's one real failure mode.
 *
 * The column belongs to the Chat place, not to the shell. `#app-context` lives
 * outside `#app-place` and the shell only toggles it from `place.hasContext`,
 * so the shell stays dumb about what a context column contains.
 */
const BossModContextColumn = (() => {
    const { clear } = BossModDom;

    /**
     * Mount the context column into `el`.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.el   #app-context, from ctx.contextEl.
     * @param {object}   deps.store
     * @param {object}   deps.bus
     * @param {Function} deps.api
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @returns {{ destroy: () => void }}
     * @throws {Error} When any dependency is missing. The column is torn down
     *   and rebuilt on every Chat mount, so a missing one must fail there
     *   rather than leave an empty rail nobody notices.
     */
    function createContextColumn(deps) {
        const { el, store, bus, api, navigate } = deps || {};
        if (!el) throw new Error('[context-column] deps.el is required');
        if (!store) throw new Error('[context-column] deps.store is required');
        if (!bus) throw new Error('[context-column] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[context-column] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[context-column] deps.navigate is required');

        const disposers = [];
        /** The mounted view, or null. Exactly one at a time. */
        let view = null;
        /** What `view` was built for, so an unchanged switch is a no-op. */
        let viewKey = null;

        function unmountView() {
            if (view) view.destroy();
            view = null;
            viewKey = null;
            clear(el);
        }

        function toOffice() {
            store.setState({ contextMode: 'office', deskAgentId: null, deskPath: null });
        }

        function build(mode, agentId) {
            if (mode === 'desk' && !agentId) {
                // Desk mode with nobody in it is the desk about to exist: the
                // hire form. This is where roster.js's "Hire someone" lands.
                return BossModAgentEdit.createAgentEdit({
                    store,
                    agent: null,
                    onDone: toOffice,
                    onCancel: toOffice,
                });
            }
            if (mode === 'desk') {
                return BossModDeskPanel.createDeskPanel({
                    store, bus, api, navigate, agentId,
                    onBack: toOffice,
                });
            }
            return BossModMiniOffice.createMiniOffice({ store, navigate });
        }

        /**
         * Show the view the store asks for.
         *
         * Rebuilding on an unchanged key would throw away the desk's in-flight
         * loads and the operator's place in its file browser on every tick that
         * happens to touch the store.
         *
         * @returns {void}
         */
        function apply() {
            const state = store.getState();
            const mode = state.contextMode === 'desk' ? 'desk' : 'office';
            const agentId = mode === 'desk' ? state.deskAgentId : null;
            const key = `${mode}:${agentId || ''}`;
            if (key === viewKey) return;

            // Outgoing first, drained, then incoming — never both alive.
            unmountView();
            view = build(mode, agentId);
            viewKey = key;
            el.append(view.element);
        }

        disposers.push(store.subscribe((s) => s.contextMode, apply));
        disposers.push(store.subscribe((s) => s.deskAgentId, apply));

        clear(el);
        apply();

        return {
            /**
             * Tear the column down. The Chat place calls this on unmount: the
             * shell hides the element but the subscriptions would leak.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
                unmountView();
            },
        };
    }

    /**
     * Open a desk from a conversation.
     *
     * Lives here rather than in the Chat place because deriving which desk to
     * show from the open conversation is knowledge about the column, not about
     * the place that hosts it.
     *
     * @param {object} store
     * @param {string} [target]  A desk path from a note, or an agent id from
     *   the conversation chrome. Anything that is not a path leaves the desk
     *   at its root.
     * @returns {void}
     */
    function openDeskFrom(store, target) {
        const state = store.getState();
        store.setState({
            contextMode: 'desk',
            deskAgentId: state.conversationKind === 'agent'
                ? state.conversationId : state.deskAgentId,
            deskPath: typeof target === 'string' && target.startsWith('/')
                ? target : null,
        });
    }

    return { createContextColumn, openDeskFrom };
})();
