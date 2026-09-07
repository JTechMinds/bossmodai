/**
 * BossMod AI — the needs strip above the composer.
 *
 * Shows only what needs the operator in the conversation they are looking at
 * (spec 5.5). Its counterpart is the toast, which fires only for needs whose
 * conversation is NOT open, so one need can never produce both.
 *
 * It defines no card markup of its own: every row goes through
 * conversation/event-cards.js, which is why a need and a transcript event look
 * the same. Consent is deliberately absent — see BAR_CARDS.
 *
 * Four states: ready (cards), error (an inline line when the queue could not be
 * confirmed), and empty. Loading is deliberately indistinguishable from empty:
 * the bar is a projection of a list somebody else fetched, and a skeleton strip
 * flashing above the operator's composer for one request would be noise, not
 * information. The popover owns the visible loading state.
 */
const BossModNeedsBar = (() => {
    const { h, clear } = BossModDom;

    const REGION_LABEL = 'Needs you in this conversation';
    const DISMISS_LABEL = 'Dismiss until next launch';

    /**
     * Need kind to the `event` card tone that shows it.
     *
     * `consent` is absent on purpose, and its absence is the suppression rule
     * doing its job rather than a missing case. A host-path consent ask already
     * renders inline in the transcript of the very conversation this bar is
     * scoped to (conversation/sources/*.js emit it as `request`), so putting it
     * here too would render one ask twice in one conversation — exactly what
     * spec 4.3 and 5.5 exist to prevent. It stays reachable from the bell.
     */
    const BAR_CARDS = Object.freeze({
        approval: 'ask',
        blocked: 'blocked',
        error: 'warn',
    });

    /**
     * Build the composer bar.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store.
     * @param {object} deps.needs  From createNeedsStore; owns resolve().
     * @param {(placeId: string, params?: object) => void} deps.navigate  Where
     *   a card's "Show me" goes. Injected, so the bar knows no place ids of
     *   its own — the destination comes from the need's target.
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When store, needs, or navigate is missing.
     */
    function createNeedsBar(deps) {
        const { store, needs, navigate } = deps || {};
        if (!store) throw new Error('[needs-bar] deps.store is required');
        if (!needs) throw new Error('[needs-bar] deps.needs is required');
        if (typeof navigate !== 'function') throw new Error('[needs-bar] deps.navigate is required');

        const disposers = [];
        /** Need ids with a resolution in flight; their buttons stay disabled. */
        const inFlight = new Set();

        const listEl = h('div', { class: 'needs-bar-list' });
        const errorEl = h('p', { class: 'needs-bar-error', role: 'alert' });
        const dismiss = h('button', {
            class: 'needs-bar-dismiss',
            type: 'button',
            'aria-label': DISMISS_LABEL,
            // needsBarDismissed, not needsBarEnabled: this is the one-session
            // dismissal. Turning the bar off for good is the popover footer's
            // toggle, and neither one can silence the bell.
            onclick: () => { store.setState({ needsBarDismissed: true }); },
        }, '×');

        const element = h('div', {
            class: 'needs-bar',
            role: 'region',
            'aria-label': REGION_LABEL,
        }, listEl, errorEl, dismiss);

        /**
         * The needs belonging to the open conversation that this bar shows.
         * @returns {object[]}
         */
        function visible() {
            const state = store.getState();
            if (!state.conversationId) return [];
            return state.needs.filter((need) => need.conversationId === state.conversationId
                && Object.prototype.hasOwnProperty.call(BAR_CARDS, need.kind));
        }

        /**
         * The "Show me" a card offers, or null when there is nowhere to go.
         *
         * The destination is need.target, from the ONE mapping table in
         * need-shape.js — no kind is named here. A target that is the very
         * conversation this bar is pinned to is dropped rather than rendered:
         * "show me" pointing at the screen the operator is already looking at
         * is the noise spec 5.5 exists to prevent, and dropping it needs no
         * kind check because the target already says where it leads.
         *
         * @param {object} need
         * @returns {object|null} An event-card action.
         */
        function showMeAction(need) {
            const target = need.target;
            if (!target) return null;
            if (target.place === 'chat'
                && target.conversationId === store.getState().conversationId) {
                return null;
            }
            return {
                label: 'Show me',
                tone: 'quiet',
                disabled: false,
                onSelect: () => {
                    if (target.conversationId) {
                        store.setState({
                            conversationId: target.conversationId,
                            conversationKind: target.conversationKind,
                        });
                    }
                    navigate(target.place, target.params);
                },
            };
        }

        function toCard(need) {
            const showMe = showMeAction(need);
            const actions = need.actions.map((action) => ({
                label: action.label,
                tone: action.tone,
                disabled: inFlight.has(need.id),
                onSelect: () => { void run(need, action); },
            }));
            if (showMe) actions.push(showMe);
            return {
                tone: BAR_CARDS[need.kind],
                title: need.title,
                sub: need.sub,
                error: need.error || '',
                actions,
            };
        }

        /**
         * Run one action, leaving the card explained if it fails.
         * @param {object} need
         * @param {object} action
         * @returns {Promise<void>}
         */
        async function run(need, action) {
            if (inFlight.has(need.id)) return;
            inFlight.add(need.id);
            render();
            try {
                await needs.resolve(need, action);
            } catch (err) {
                // needs.resolve logged it and put the need back carrying its
                // message; re-rendering is what puts that in front of the
                // operator instead of leaving them to assume it worked.
                inFlight.delete(need.id);
                render();
                return;
            }
            inFlight.delete(need.id);
            render();
        }

        function render() {
            const state = store.getState();
            const suppressed = state.needsBarEnabled !== true || state.needsBarDismissed === true;
            const items = suppressed ? [] : visible();

            errorEl.textContent = items.length ? needs.getError() : '';

            if (items.length === 0) {
                clear(listEl);
                element.hidden = true;
                return;
            }
            element.hidden = false;
            clear(listEl);
            items.forEach((need) => {
                listEl.append(BossModEventCards.renderEventCard(
                    { kind: 'event', card: toCard(need) },
                    { api: null },
                ));
            });
        }

        disposers.push(store.subscribe((s) => s.needs, render));
        disposers.push(store.subscribe((s) => s.conversationId, render));
        disposers.push(store.subscribe((s) => s.needsBarEnabled, render));
        disposers.push(store.subscribe((s) => s.needsBarDismissed, render));
        disposers.push(needs.subscribeError(render));

        render();

        return {
            element,

            /**
             * Drain every subscription this bar created.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createNeedsBar };
})();
