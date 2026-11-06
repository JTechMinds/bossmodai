/**
 * BossMod AI — the place navigator.
 *
 * The half of the shell that swaps the centre column. Split out in Phase 4
 * when the tree-wide 300-line cap went in: booting the app and swapping a
 * place are different jobs on different clocks — boot runs once, this runs on
 * every navigation — and only one of them needs to be read to understand why a
 * place leaked a subscription.
 *
 * Places receive ctx and are never wired by global name, so a missing module
 * fails loudly at mount instead of silently no-opping.
 */
const BossModNavigator = (() => {
    const { h, clear } = BossModDom;

    /**
     * @param {object} options
     * @param {object} options.store      From BossModStore.createStore.
     * @param {object} options.bus        From BossModBus.createBus.
     * @param {HTMLElement} options.container  The centre column element.
     * @param {Function} options.api      Authenticated request helper, apiFetch.
     * @param {object} options.needs      From BossModNeeds.createNeedsStore.
     * @param {HTMLElement} options.contextEl  #app-context. The shell owns the
     *   element and toggles the column; it knows nothing about what fills it.
     * @param {(agentId: string, path?: string) => void} options.openDesk  Opens
     *   one agent's desk modal (context/desk-dialog.js). Handed to every place
     *   as `ctx.openDesk`, so each door into a desk is the same one.
     * @param {{actions: Function, openMemory: Function}} options.agentMenu
     *   The page's agent `⋯` (context/agent-menu.js): a DM header's agent
     *   rows, and a memory line's Memory dialog. Handed on as `ctx.agentMenu`.
     * @param {() => void} options.openRoster  Presents the roster drawer
     *   (shell/responsive.js); handed on as `ctx.openRoster`, so Chat's empty
     *   state opens the same drawer the ☰ does where the rail is hidden.
     * @param {{isOpen: () => boolean, close: () => void}} options.takeover
     *   The full-screen view that can cover the centre column (Settings).
     *   Injected, as openDesk and openRoster are, so this file never names the
     *   view: choosing a place leaves it.
     * @returns {{ navigate: (id: string, params?: object) => void, getCtx: () => object }}
     * @throws {Error} When openDesk or openRoster is not a function, or
     *   agentMenu is missing: a place would otherwise render a door that
     *   fails only when clicked. When takeover is missing or either of its
     *   members is not a function: a navigation would swap a place nobody
     *   can see.
     */
    function createNavigator({
        store, bus, container, api, needs, contextEl, openDesk, agentMenu, openRoster, takeover,
    }) {
        if (typeof openDesk !== 'function') throw new Error('[navigator] openDesk is required');
        if (!agentMenu) throw new Error('[navigator] agentMenu is required');
        if (typeof openRoster !== 'function') throw new Error('[navigator] openRoster is required');
        if (!takeover || typeof takeover.isOpen !== 'function' || typeof takeover.close !== 'function') {
            throw new Error('[navigator] takeover is required');
        }
        let current = null;

        const ctx = {
            store,
            bus,
            api,
            needs,
            contextEl,
            openDesk,
            agentMenu,
            openRoster,
            navigate: (id, params) => navigate(id, params),
        };

        /**
         * Swap the centre place.
         *
         * Unmounts the outgoing place before mounting the incoming one, so two
         * places never hold subscriptions simultaneously. Moves focus to the
         * new place's heading — without this, keyboard and screen-reader users
         * are dropped to the top of the document on every navigation.
         *
         * Two rules for the takeover. Choosing a place while it is up closes
         * it, because the operator asked to see that place. And when the place
         * asked for is the one already mounted under it, with no params, the
         * takeover closing is the whole navigation: the place is still live,
         * and a remount would drop Chat's draft, transcript cache and caret,
         * Office's loaded plan and Tasks' filter. Uncovered, re-choosing the
         * current place still remounts it, which is how a header click resets
         * that place.
         *
         * @param {string} placeId
         * @param {object} [params]  Written to `store.placeParams`.
         * @returns {void}
         * @throws {Error} On an unknown place id, before the takeover is touched.
         */
        function navigate(placeId, params) {
            const place = BossModPlaces.get(placeId);
            if (!place) throw new Error(`[navigator] unknown place "${placeId}"`);

            const covered = takeover.isOpen();
            if (covered) takeover.close();
            if (covered && current === place && noParams(params)) {
                focusHeading();
                return;
            }

            if (current) {
                try {
                    current.unmount();
                } catch (err) {
                    // A failing unmount must not strand the UI on the old place,
                    // but it is a real defect and is never swallowed silently.
                    console.error(`[navigator] unmount of "${store.getState().place}" threw`, err);
                }
            }

            clear(container);
            current = place;
            store.setState({ place: placeId, placeParams: params || {} });

            try {
                place.mount(container, ctx);
            } catch (err) {
                console.error(`[navigator] mount of "${placeId}" threw`, err);
                renderMountError(placeId, err);
                return;
            }

            focusHeading();
        }

        function renderMountError(placeId, err) {
            clear(container);
            container.append(h('div', { class: 'place-error', role: 'alert' },
                h('h1', { tabindex: '-1' }, `${placeId} could not be opened`),
                h('p', {}, String((err && err.message) || err)),
                h('button', {
                    class: 'btn',
                    type: 'button',
                    onclick: () => navigate(placeId, store.getState().placeParams),
                }, 'Try again')));
            focusHeading();
        }

        function focusHeading() {
            const heading = container.querySelector('h1');
            if (!heading) {
                // Every place owes the shell exactly one h1; without it focus
                // falls to <body> and keyboard navigation silently degrades.
                console.warn('[navigator] mounted place rendered no h1 — focus not moved');
                return;
            }
            heading.setAttribute('tabindex', '-1');
            heading.focus();
        }

        // Chat need targets pass `{}`; the header and the shortcuts pass nothing.
        function noParams(params) {
            return !params || Object.keys(params).length === 0;
        }

        return { navigate, getCtx: () => ctx };
    }

    return { createNavigator };
})();
