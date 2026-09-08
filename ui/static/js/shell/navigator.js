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
     * @returns {{ navigate: (id: string, params?: object) => void, getCtx: () => object }}
     */
    function createNavigator({ store, bus, container, api, needs, contextEl }) {
        let current = null;

        const ctx = {
            store,
            bus,
            api,
            needs,
            contextEl,
            navigate: (id, params) => navigate(id, params),
        };

        /**
         * Swap the centre place.
         *
         * Unmounts the outgoing place before mounting the incoming one, so two
         * places never hold subscriptions simultaneously. Moves focus to the
         * new place's heading — without this, keyboard and screen-reader users
         * are dropped to the top of the document on every navigation.
         */
        function navigate(placeId, params) {
            const place = BossModPlaces.get(placeId);
            if (!place) throw new Error(`[navigator] unknown place "${placeId}"`);

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

        return { navigate, getCtx: () => ctx };
    }

    return { createNavigator };
})();
