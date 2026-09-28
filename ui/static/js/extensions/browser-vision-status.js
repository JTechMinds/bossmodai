/**
 * BossMod AI — which agents have a Browser Vision view, the one poller.
 *
 * The chat header's screen button and the single-agent viewer both read from
 * here, so there is exactly one request loop however many surfaces listen.
 *
 * Polling, not a websocket event: `bv` runs synchronously on a pool thread and
 * the runtime has no thread-safe sync→async event emit, so pushing a "new
 * screenshot" event would be a runtime change. One small localhost read every
 * 3 s, and only while something is subscribed, is proportionate.
 *
 * Outcomes of a poll:
 *   success               → the set of agents with a latest screenshot;
 *   409 EXTENSION_DISABLED → an explicit "off": the set is emptied (not an error);
 *   anything else         → `console.error` with context, and the last known
 *                           state is KEPT until the next successful poll, so one
 *                           failed read does not blink every header button away.
 */
const BossModBrowserVisionStatus = (() => {
    const EXTENSION_ID = 'browser-vision';
    const POLL_MS = 3000;

    /** agent id → the live item `GET …/live` returned for it. */
    let items = new Map();
    const listeners = new Set();
    let timer = null;
    let inFlight = null;

    function notify() {
        listeners.forEach((fn) => fn());
    }

    async function poll() {
        try {
            const payload = await BossModExtensionsApi.liveView(EXTENSION_ID);
            items = new Map(payload.items.map((item) => [item.agent_id, item]));
        } catch (err) {
            if (err && err.code === 'EXTENSION_DISABLED') {
                items = new Map();
            } else {
                console.error('[browser-vision-status] live view poll failed; keeping the last known state', err);
            }
        }
        notify();
    }

    function schedule() {
        clearTimeout(timer);
        timer = null;
        if (!listeners.size) return;
        timer = setTimeout(() => { void tick(); }, POLL_MS);
    }

    async function tick() {
        if (!inFlight) inFlight = poll().finally(() => { inFlight = null; });
        await inFlight;
        schedule();
    }

    /**
     * Listen for changes. The first subscriber starts polling (with an
     * immediate read); the last one to leave stops it.
     *
     * @param {() => void} fn  Called after every poll.
     * @returns {() => void} Unsubscribe.
     */
    function subscribe(fn) {
        listeners.add(fn);
        if (listeners.size === 1) void tick();
        return () => {
            listeners.delete(fn);
            if (!listeners.size) {
                clearTimeout(timer);
                timer = null;
            }
        };
    }

    /**
     * Read now instead of waiting for the next tick — after the operator turns
     * the extension on or off, so the headers follow at once.
     *
     * @returns {Promise<void>}
     */
    async function refresh() {
        if (!listeners.size) return;
        await tick();
    }

    /** @param {string} agentId @returns {boolean} */
    function hasView(agentId) {
        return items.has(agentId);
    }

    /** @param {string} agentId @returns {object|null} The agent's latest live item. */
    function latest(agentId) {
        return items.get(agentId) || null;
    }

    return { subscribe, refresh, hasView, latest };
})();
