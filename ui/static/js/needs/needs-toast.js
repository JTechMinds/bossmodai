/**
 * BossMod AI — arrival toasts for needs the operator cannot see.
 *
 * The other half of the suppression rule (spec 5.5): the composer bar covers
 * the open conversation, so this covers everything else. One need can never
 * produce both, and nothing is announced twice.
 *
 * It fires on `needs.subscribeArrivals`, not on `store.needs`, because only the
 * queue can tell an arrival from a boot or resync reload. Diffing the store
 * naively would throw the whole backlog at the operator on launch — the cure
 * becoming the disease.
 */
const BossModNeedsToast = (() => {
    const { h } = BossModDom;

    /** Spec 5.5. Long enough to read a title and a sub line, not a paragraph. */
    const TOAST_MS = 6000;
    const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

    /**
     * Build the app-wide toast host.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; read for the open
     *   conversation, written when the operator opens a need.
     * @param {object} deps.needs  From createNeedsStore; the arrival source.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @returns {{ destroy: () => void }}
     * @throws {Error} When store, needs, or navigate is missing.
     */
    function createToastHost(deps) {
        const { store, needs, navigate } = deps || {};
        if (!store) throw new Error('[needs-toast] deps.store is required');
        if (!needs) throw new Error('[needs-toast] deps.needs is required');
        if (typeof navigate !== 'function') throw new Error('[needs-toast] deps.navigate is required');

        const disposers = [];
        const timers = new Set();

        const host = h('div', { class: 'toast-host' });
        document.body.append(host);

        /**
         * Reduced motion means a toast that waits to be dismissed rather than
         * one that vanishes: without the animation, a timed disappearance is
         * just information the operator may never have seen.
         *
         * @returns {boolean}
         */
        function reducedMotion() {
            return window.matchMedia(REDUCED_MOTION).matches === true;
        }

        function dismiss(toast, timer) {
            if (timer) {
                clearTimeout(timer);
                timers.delete(timer);
            }
            toast.remove();
        }

        function show(need) {
            const toast = h('div', { class: 'toast', role: 'status' },
                h('p', { class: 'toast-title' }, need.title),
                h('p', { class: 'toast-sub' }, need.sub));

            const actions = h('div', { class: 'toast-actions' });
            let timer = null;

            if (need.conversationId) {
                actions.append(h('button', {
                    class: 'toast-action primary',
                    type: 'button',
                    onclick: () => {
                        store.setState({
                            conversationId: need.conversationId,
                            // The queue carries no conversation kind; a
                            // conversation that is not the agent's own id is a
                            // thread (api/routes/needs.py sets it from the
                            // originating channel).
                            conversationKind: need.conversationId === need.agentId
                                ? 'agent'
                                : 'thread',
                        });
                        // Navigating to Chat while already there remounts the
                        // place and discards the transcript cache and the
                        // composer draft; the store alone switches the
                        // conversation (shell/roster.js documents the rule).
                        if (store.getState().place !== 'chat') navigate('chat');
                        dismiss(toast, timer);
                    },
                }, 'Open'));
            }
            actions.append(h('button', {
                class: 'toast-action',
                type: 'button',
                onclick: () => dismiss(toast, timer),
            }, 'Dismiss'));

            toast.append(actions);
            host.append(toast);

            if (!reducedMotion()) {
                timer = setTimeout(() => dismiss(toast, timer), TOAST_MS);
                timers.add(timer);
            }
        }

        disposers.push(needs.subscribeArrivals((arrivals) => {
            const openConversation = store.getState().conversationId;
            arrivals.forEach((need) => {
                // The bar has this one covered, quietly, where the operator is
                // already looking. Announcing it here as well is the double
                // announcement the suppression rule forbids.
                if (need.conversationId && need.conversationId === openConversation) return;
                show(need);
            });
        }));

        return {
            /**
             * Drain subscriptions, cancel pending dismissals, remove the host.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
                timers.forEach((timer) => clearTimeout(timer));
                timers.clear();
                host.remove();
            },
        };
    }

    return { createToastHost };
})();
