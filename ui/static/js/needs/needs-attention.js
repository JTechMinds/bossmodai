/**
 * BossMod AI — glanceable Needs outside the window.
 *
 * The bell, the bar and the toast already read `store.needs`. This host is
 * the same subscription, pointed at the desktop shell: a count for the
 * taskbar/dock badge and the tray, and a tray-click that opens the Needs
 * popover. It never keeps a second counter, and it never sends a need title
 * (commands, paths, tokens) across the OS chrome.
 *
 * Native OS toasts are parked (`OS_TOAST_ENABLED`). The in-app toast host is
 * a different surface and stays as it is.
 *
 * No-ops in a browser. The desktop injects `window.__TAURI__`; without it
 * this module must not throw, or a FastAPI-only session would die on boot.
 */
const BossModNeedsAttention = (() => {

    const COMMAND = 'sync_needs_attention';
    const FOCUS_EVENT = 'needs-focus';
    /** Parked for v1. Do not call the OS notify API. */
    const OS_TOAST_ENABLED = false;

    function desktopApi() {
        return globalThis.__TAURI__ || null;
    }

    /**
     * The only number the OS chrome is allowed to see.
     *
     * @param {object[]|undefined} needs  `store.needs`
     * @returns {number}
     */
    function countOf(needs) {
        return Array.isArray(needs) ? needs.length : 0;
    }

    /**
     * Subscribe `store.needs` to the desktop badge and tray.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `needs` is the same
     *   slice the bell reads.
     * @returns {{ setShowNeeds: (fn: () => void) => void, destroy: () => void }}
     * @throws {Error} When store is missing — a silent no-op host would
     *   freeze a stale badge after the queue cleared.
     */
    function createHost(deps) {
        const { store } = deps || {};
        if (!store) throw new Error('[needs-attention] deps.store is required');

        const disposers = [];
        let showNeeds = function () {};
        let lastCount = -1;
        let closed = false;
        let unlisten = null;

        function pushCount(count) {
            const api = desktopApi();
            if (!api || !api.core || typeof api.core.invoke !== 'function') return;
            void api.core.invoke(COMMAND, { count }).catch((err) => {
                console.error('[needs-attention] could not sync the badge', err);
            });
        }

        function apply(needs) {
            const count = countOf(needs);
            if (count === lastCount) return;
            lastCount = count;
            pushCount(count);
            if (OS_TOAST_ENABLED) {
                // Parked. A later cut that opts in still has only `count`.
            }
        }

        apply(store.getState().needs);
        disposers.push(store.subscribe((s) => s.needs, apply));

        const api = desktopApi();
        if (api && api.event && typeof api.event.listen === 'function') {
            void api.event.listen(FOCUS_EVENT, () => { showNeeds(); }).then((off) => {
                if (closed) {
                    if (typeof off === 'function') off();
                    return;
                }
                unlisten = off;
            }).catch((err) => {
                console.error('[needs-attention] could not listen for tray focus', err);
            });
        }

        return {
            /**
             * The header owns the popover. Tray click must open it without
             * toggling it shut when it is already open.
             *
             * @param {() => void} fn
             */
            setShowNeeds(fn) {
                showNeeds = typeof fn === 'function' ? fn : function () {};
            },

            destroy() {
                closed = true;
                disposers.splice(0).forEach((off) => off());
                if (typeof unlisten === 'function') unlisten();
                unlisten = null;
            },
        };
    }

    return { createHost, countOf, OS_TOAST_ENABLED, COMMAND, FOCUS_EVENT };
})();
