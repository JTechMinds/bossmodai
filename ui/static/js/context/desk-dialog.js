/**
 * BossMod AI — the desk modal: the one owner of "open an agent's desk".
 *
 * The desk used to be a second view of Chat's context column, switched by the
 * store keys `contextMode`, `deskAgentId` and `deskPath`, persisted across
 * reloads, and 280px wide. Every door into it wrote those keys and then
 * navigated to Chat. The modal standard retired side panels for secondary
 * screens, so the desk is a `panel`-size createModal now, opened over
 * whatever place the operator is in, and nothing about it lives in the store.
 *
 * ONE MODULE OWNS THE LIFECYCLE. context/desk-panel.js composes the body, the
 * avatar lead and the head tools and never touches overlays; this opens the
 * modal around them, closes it, and hands the panel the three ways it can end:
 * a navigation (`leave`), a removal, and Chat.
 *
 * Every navigation the desk triggers closes it FIRST. The navigator would
 * otherwise mount a place under a live modal whose panel still holds
 * subscriptions — and the operator asked to go somewhere else.
 */
const BossModDeskDialog = (() => {
    /** The title while the roster has not named the agent yet. */
    const LOADING_TITLE = 'Agent desk';

    /**
     * Build the desk opener. Called once, at boot, by the shell.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; the roster names the agent.
     * @param {object} deps.bus    Topic bus, handed to the panel.
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(placeId: string, params?: object) => void} deps.navigate  The
     *   shell's navigate. Resolved at call time by the caller, because the
     *   navigator is built after this.
     * @returns {{ open: (agentId: string, path?: string) => void,
     *   close: () => void }} `open` shows one agent's desk — closing any desk
     *   already open, since only one exists at a time — with the file browser
     *   at `path` (default `/me`). `close` closes the open desk, if any.
     * @throws {Error} When any dependency is missing: a desk door that fails at
     *   the click is worse than a boot that fails at once.
     */
    function createDeskDialog(deps) {
        const { store, bus, api, navigate } = deps || {};
        if (!store) throw new Error('[desk-dialog] deps.store is required');
        if (!bus) throw new Error('[desk-dialog] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[desk-dialog] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[desk-dialog] deps.navigate is required');

        /** The open desk as `{ modal, panel }`, or null. */
        let current = null;

        /** Close the open desk, if any. The modal's onClose tears the panel down. */
        function close() {
            if (current) current.modal.close();
        }

        /**
         * Open one agent's desk.
         *
         * @param {string} agentId
         * @param {string} [path]  Where the file browser opens — a deliverable
         *   or a note's desk path. Without one, the desk root.
         * @returns {void}
         * @throws {Error} On an empty agent id: a desk needs an owner.
         */
        function open(agentId, path) {
            if (!agentId) throw new Error('[desk-dialog] a desk needs an agent id');
            close();

            const who = store.getState().roster.find((item) => item && item.id === agentId);
            /** This desk's own handle, so a late callback cannot close the next one. */
            const entry = { modal: null, panel: null };
            const closeThis = () => { if (current === entry) close(); };

            entry.panel = BossModDeskPanel.createDeskPanel({
                store,
                bus,
                api,
                agentId,
                initialPath: path || '/me',
                // Leaving for another place: the desk must not outlive the click.
                navigate: (placeId, params) => {
                    closeThis();
                    navigate(placeId, params);
                },
                onRemoved: closeThis,
                setTitle: (name) => { if (entry.modal) entry.modal.setTitle(name); },
                // Closed explicitly: openConversation skips navigating when
                // Chat is already the place, and the desk would stay up over it.
                onOpenChat: () => {
                    closeThis();
                    BossModAgentRoutes.openConversation({ store, navigate }, agentId, 'agent');
                },
            });
            entry.modal = BossModOverlays.createModal({
                title: who ? String(who.name) : LOADING_TITLE,
                lead: entry.panel.lead,
                body: entry.panel.element,
                tools: entry.panel.tools,
                size: 'panel',
                // No footer: the actions on the agent are the head's.
                actions: [],
                // Nothing here is typed; an outside click loses nothing.
                closeOnBackdrop: true,
                onClose: () => {
                    entry.panel.destroy();
                    if (current === entry) current = null;
                },
            });
            current = entry;
            // Read only by the stylesheet, as the Agents dialog's is.
            entry.modal.element.setAttribute('data-dialog', 'desk');
            BossModIcons.paint(entry.modal.element, 'desk-dialog');
            // The description has a height only now that the modal is mounted.
            entry.panel.measure();
        }

        return { open, close };
    }

    return { createDeskDialog };
})();
