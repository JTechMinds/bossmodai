/**
 * BossMod AI — which agents have a Browser Vision view, the one reader.
 *
 * The chat header's screen button and the single-agent viewer both read from
 * here, so there is exactly one reader of `GET …/live` however many surfaces
 * listen.
 *
 * No polling. The server pushes `extension_live` (a nudge with no state)
 * whenever a live view may have changed: after every `bv` result, on
 * enable/disable, and when the runtime worker starts or exits. A read costs
 * nothing while nothing changes, and `/live` stays the single source of truth
 * (the event says "re-read", never what changed).
 *
 * What triggers a read of `/live`, and only while something is subscribed:
 *   the first subscriber arriving;
 *   a bus `extension_live` for 'browser-vision' or for `null` (any extension);
 *   a bus `resync` (the socket dropped, so pushes may have been missed);
 *   `refresh()` from the Extensions dialog after a toggle.
 * With no subscriber, events are ignored and nothing is fetched.
 *
 * Coalescing: at most one read in flight and one queued, so a burst of events
 * costs at most two requests, and the queued read still sees the latest state.
 *
 * Outcomes of a read:
 *   success               → the set of agents with a latest screenshot;
 *   409 EXTENSION_DISABLED → an explicit "off": the set is emptied (not an error);
 *   anything else         → `console.error` with context, and the last known
 *                           state is KEPT until the next successful read, so one
 *                           failed read does not blink every header button away.
 */
const BossModBrowserVisionStatus = (() => {
    const EXTENSION_ID = 'browser-vision';

    /** agent id → the live item `GET …/live` returned for it. */
    let items = new Map();
    const listeners = new Set();
    /** The read now running, or null. */
    let inFlight = null;
    /** The one read waiting for `inFlight` to finish, or null. */
    let queued = null;

    function notify() {
        listeners.forEach((fn) => {
            // One broken listener must not stop the others, nor leave a read
            // rejected (which would wedge the queue).
            try {
                fn();
            } catch (err) {
                console.error('[browser-vision-status] a listener threw', err);
            }
        });
    }

    async function read() {
        try {
            const payload = await BossModExtensionsApi.liveView(EXTENSION_ID);
            items = new Map(payload.items.map((item) => [item.agent_id, item]));
        } catch (err) {
            if (err && err.code === 'EXTENSION_DISABLED') {
                items = new Map();
            } else {
                console.error('[browser-vision-status] live view read failed; keeping the last known state', err);
            }
        }
        notify();
    }

    /**
     * Read now, or once the running read finishes. Every caller gets a
     * promise for a read that STARTED after its call, so none acts on stale
     * state; callers that arrive while one is already queued share it.
     *
     * @returns {Promise<void>}
     */
    function request() {
        if (!inFlight) {
            inFlight = read().finally(() => { inFlight = null; });
            return inFlight;
        }
        if (!queued) {
            queued = inFlight.then(() => {
                queued = null;
                return request();
            });
        }
        return queued;
    }

    /**
     * Listen for changes. The first subscriber triggers an immediate read.
     *
     * @param {() => void} fn  Called after every read.
     * @returns {() => void} Unsubscribe.
     */
    function subscribe(fn) {
        listeners.add(fn);
        if (listeners.size === 1) void request();
        return () => { listeners.delete(fn); };
    }

    /**
     * Read now — after the operator turns the extension on or off, so the
     * headers follow at once (the server's `extension_live` also covers it;
     * the two coalesce).
     *
     * @returns {Promise<void>}
     */
    async function refresh() {
        if (!listeners.size) return;
        await request();
    }

    function onExtensionLive(data) {
        if (!listeners.size) return;
        if (!data || !Object.prototype.hasOwnProperty.call(data, 'extension_id')) {
            console.error('[browser-vision-status] extension_live without an extension_id', data);
            return;
        }
        if (data.extension_id !== null && data.extension_id !== EXTENSION_ID) return;
        void request();
    }

    function onResync() {
        if (!listeners.size) return;
        void request();
    }

    /**
     * Wire the bus once at boot, before the socket connects.
     *
     * @param {object} deps
     * @param {object} deps.bus  From BossModBus.createBus.
     * @returns {() => void} Disposer.
     */
    function attach({ bus }) {
        if (!bus) throw new Error('[browser-vision-status] attach() needs deps.bus');
        const offs = [
            bus.subscribe('extension_live', onExtensionLive),
            bus.subscribe('resync', onResync),
        ];
        return () => offs.forEach((off) => off());
    }

    /** @param {string} agentId @returns {boolean} */
    function hasView(agentId) {
        return items.has(agentId);
    }

    /** @param {string} agentId @returns {object|null} The agent's latest live item. */
    function latest(agentId) {
        return items.get(agentId) || null;
    }

    return { attach, subscribe, refresh, hasView, latest };
})();
