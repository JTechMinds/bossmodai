/**
 * BossMod AI — the full-width banners between header and main.
 *
 * The banner markup lives in index.html so its copy is a template fact rather
 * than a string buried in JavaScript; this module only decides when each one
 * is shown, and owns the model-availability check that decides the second.
 *
 * `refreshModelAvailability` moved here from app.js. Until Phase 4 deletes
 * app.js, tests/test_health_ops_ui.py still asserts that behaviour against
 * app.js — that assertion is re-pointed when the file is deleted, not now.
 */
const BossModBanners = (() => {
    /** The mounted instance's refresh, or null. One shell, one mount. */
    let mounted = null;

    /**
     * Wire both banners to store state.
     *
     * @param {object} deps
     * @param {object} deps.store
     * @param {object} deps.bus
     * @param {Function} deps.apiFetch            Authenticated request helper.
     * @param {HTMLElement} deps.pauseBanner      #runtime-pause-banner
     * @param {HTMLElement} deps.noModelBanner    #no-model-banner
     * @returns {() => void} disposer — drains every subscription.
     */
    function mount(deps) {
        const { store, bus, apiFetch, pauseBanner, noModelBanner } = deps;
        if (!pauseBanner) throw new Error('[banners] #runtime-pause-banner element is required');
        if (!noModelBanner) throw new Error('[banners] #no-model-banner element is required');
        if (mounted) throw new Error('[banners] already mounted');

        const disposers = [];

        function applyPaused(paused) {
            pauseBanner.classList.toggle('hidden', paused !== true);
        }

        function applyModel(hasUsableModel) {
            noModelBanner.classList.toggle('hidden', hasUsableModel === true);
        }

        /**
         * A configured AI connection is what makes chat usable, so the banner
         * follows the connection list rather than any cached flag.
         *
         * @returns {Promise<void>}
         */
        async function refreshModelAvailability() {
            let response;
            try {
                response = await apiFetch('/api/connections', { cache: 'no-store' });
            } catch (err) {
                console.error('[banners] could not load AI connections', err);
                store.setState({ hasUsableModel: false });
                return;
            }
            if (!response.ok) {
                console.error(`[banners] AI connections request failed: HTTP ${response.status}`);
                store.setState({ hasUsableModel: false });
                return;
            }
            const connections = await response.json();
            store.setState({ hasUsableModel: connections.length > 0 });
        }

        disposers.push(store.subscribe((s) => s.runtimePaused, applyPaused));
        disposers.push(store.subscribe((s) => s.hasUsableModel, applyModel));
        disposers.push(bus.subscribe('runtime_state', (payload) => {
            store.setState({ runtimePaused: payload.paused === true });
        }));
        // A dropped socket may have hidden a connection being added or removed.
        disposers.push(bus.subscribe('resync', () => { void refreshModelAvailability(); }));

        applyPaused(store.getState().runtimePaused);
        applyModel(store.getState().hasUsableModel);
        void refreshModelAvailability();

        mounted = refreshModelAvailability;
        return () => {
            mounted = null;
            disposers.splice(0).forEach((off) => off());
        };
    }

    /**
     * Re-check model availability from outside the shell.
     *
     * settings-view.js calls this when the Settings takeover closes: adding or
     * removing an AI connection is the one change to the banner's answer that
     * arrives through no WebSocket event.
     *
     * @returns {Promise<void>}
     */
    function refreshModelAvailability() {
        if (!mounted) throw new Error('[banners] refreshModelAvailability before mount');
        return mounted();
    }

    return { mount, refreshModelAvailability };
})();
