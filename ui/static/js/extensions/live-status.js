/**
 * BossMod AI — which agents have a live view, the one reader for every
 * live-view extension.
 *
 * The chat header's live-view buttons and the single-agent viewer both read
 * from here, so each extension's `GET …/live` has exactly one reader however
 * many surfaces listen. Which extensions have a live view, what each one
 * calls it (its manifest's `live_view.label`, e.g. "Browser view") and which
 * icon its header button wears (`live_view.icon`, one of ICONS) come from the
 * extension list (BossModExtensionsApi.listExtensions): every enabled item
 * with a `live_view`. Nothing here names an extension.
 *
 * No polling. The server pushes `extension_live` (a nudge with no state)
 * whenever a live view may have changed: after every live-view command
 * result, on enable/disable, after the runtime worker changes what an agent
 * holds, and when the worker starts or exits. A read costs nothing while
 * nothing changes, and the server stays the single source of truth (the
 * event says "re-read", never what changed).
 *
 * What triggers a read, and only while something is subscribed:
 *   the first subscriber arriving                     → everything;
 *   a bus `extension_live` for a listed extension     → that extension's /live;
 *   a bus `extension_live` with `extension_id: null`  → everything;
 *   a bus `extension_live` for an unlisted extension  → everything: it may
 *     have just been turned on, and only the list says what its view is called;
 *   a bus `resync` (the socket dropped, so pushes may have been missed)
 *                                                     → everything;
 *   `refresh()` from the Extensions dialog after a toggle → everything.
 * "Everything" is the extension list, then each listed extension's /live.
 * With no subscriber, events are ignored and nothing is fetched.
 *
 * Coalescing: at most one read in flight and one queued, so a burst of events
 * costs at most two reads. The queued read covers everything the events
 * behind it asked for, and still sees the latest state.
 *
 * Outcomes of a read:
 *   success                → that extension's agents with a latest screenshot;
 *   409 EXTENSION_DISABLED → an explicit "off": its set is emptied (not an error);
 *   anything else          → `console.error` with context, and the last known
 *                            state is KEPT until the next successful read, so one
 *                            failed read does not blink every header button away.
 *                            A failed list read keeps the list and every set.
 * A listed extension whose icon is not one of ICONS is a server/UI contract
 * break: `console.error` naming it, and its view is left out of the list.
 */
const BossModLiveStatus = (() => {
    /**
     * The icons a live view's header button may wear: the host's fixed
     * vocabulary (core/extensions/manifest.py `LiveViewIcon`, which the
     * server validates every manifest against; a test holds the two lists
     * equal). Written as literals so scripts/build_lucide_subset.cjs keeps
     * each one in the shipped icon subset — core/icons.js throws on a name
     * outside it.
     */
    const ICONS = Object.freeze(['globe', 'monitor']);

    /** The enabled live-view extensions, `{id, label, icon}`, in the server's list order. */
    let listed = [];
    /** extension id → (agent id → the live item its `GET …/live` returned). */
    let items = new Map();
    const listeners = new Set();
    /** The read now running, or null. */
    let inFlight = null;
    /** The one read waiting for `inFlight` to finish, with what it covers; or null. */
    let queued = null;

    const everything = () => ({ all: true, ids: new Set() });
    const one = (extensionId) => ({ all: false, ids: new Set([extensionId]) });
    const isListed = (extensionId) => listed.some((view) => view.id === extensionId);

    function notify() {
        listeners.forEach((fn) => {
            // One broken listener must not stop the others, nor leave a read
            // rejected (which would wedge the queue).
            try {
                fn();
            } catch (err) {
                console.error('[live-status] a listener threw', err);
            }
        });
    }

    /**
     * Whether a live-view item's icon is one the UI ships. The server holds
     * manifests to the same vocabulary, so anything else means the two have
     * drifted apart: said loudly, and the view is left out rather than handed
     * to core/icons.js, which would throw while painting the chat header.
     *
     * @param {{id: string, live_view: {icon: string}}} item  An extension list item.
     * @returns {boolean}
     */
    function hasKnownIcon(item) {
        if (ICONS.includes(item.live_view.icon)) return true;
        console.error(
            `[live-status] ${item.id} names the live-view icon "${item.live_view.icon}", `
            + `which is not one of ${ICONS.join(', ')}; leaving its view out`,
        );
        return false;
    }

    /** @returns {Promise<boolean>} Whether the list was read. */
    async function readList() {
        try {
            const list = await BossModExtensionsApi.listExtensions();
            listed = list
                .filter((item) => item.enabled && item.live_view)
                .filter(hasKnownIcon)
                .map((item) => ({ id: item.id, label: item.live_view.label, icon: item.live_view.icon }));
        } catch (err) {
            console.error('[live-status] extension list read failed; keeping the last known state', err);
            return false;
        }
        // An extension no longer listed (turned off) has no view left to show.
        items = new Map(listed.filter((view) => items.has(view.id)).map((view) => [view.id, items.get(view.id)]));
        return true;
    }

    async function readOne(extensionId) {
        try {
            const payload = await BossModExtensionsApi.liveView(extensionId);
            items.set(extensionId, new Map(payload.items.map((item) => [item.agent_id, item])));
        } catch (err) {
            if (err && err.code === 'EXTENSION_DISABLED') {
                items.set(extensionId, new Map());
            } else {
                console.error(`[live-status] ${extensionId} live view read failed; keeping the last known state`, err);
            }
        }
    }

    /**
     * One read. Never rejects: every failure is handled above, so the queue
     * behind it always runs.
     *
     * @param {{all: boolean, ids: Set<string>}} scope
     * @returns {Promise<void>}
     */
    async function read(scope) {
        let ids = Array.from(scope.ids).filter(isListed);
        if (scope.all) ids = (await readList()) ? listed.map((view) => view.id) : [];
        await Promise.all(ids.map(readOne));
        notify();
    }

    /**
     * Read now, or once the running read finishes. Every caller gets a
     * promise for a read that STARTED after its call, so none acts on stale
     * state; callers that arrive while one is already queued share it, and
     * widen what it covers.
     *
     * @param {{all: boolean, ids: Set<string>}} scope
     * @returns {Promise<void>}
     */
    function request(scope) {
        if (!inFlight) {
            inFlight = read(scope).finally(() => { inFlight = null; });
            return inFlight;
        }
        if (!queued) {
            const next = { all: false, ids: new Set() };
            next.promise = inFlight.then(() => {
                queued = null;
                return request(next);
            });
            queued = next;
        }
        if (scope.all) queued.all = true;
        scope.ids.forEach((extensionId) => queued.ids.add(extensionId));
        return queued.promise;
    }

    /**
     * Listen for changes. The first subscriber triggers a read of everything.
     *
     * @param {() => void} fn  Called after every read.
     * @returns {() => void} Unsubscribe.
     */
    function subscribe(fn) {
        listeners.add(fn);
        if (listeners.size === 1) void request(everything());
        return () => { listeners.delete(fn); };
    }

    /**
     * Read everything now — after the operator turns an extension on or off,
     * so the headers follow at once (the server's `extension_live` also
     * covers it; the two coalesce).
     *
     * @returns {Promise<void>}
     */
    async function refresh() {
        if (!listeners.size) return;
        await request(everything());
    }

    function onExtensionLive(data) {
        if (!listeners.size) return;
        if (!data || !Object.prototype.hasOwnProperty.call(data, 'extension_id')) {
            console.error('[live-status] extension_live without an extension_id', data);
            return;
        }
        const extensionId = data.extension_id;
        void request(extensionId !== null && isListed(extensionId) ? one(extensionId) : everything());
    }

    function onResync() {
        if (!listeners.size) return;
        void request(everything());
    }

    /**
     * Wire the bus once at boot, before the socket connects.
     *
     * @param {object} deps
     * @param {object} deps.bus  From BossModBus.createBus.
     * @returns {() => void} Disposer.
     * @throws {Error} When the bus is missing.
     */
    function attach({ bus }) {
        if (!bus) throw new Error('[live-status] attach() needs deps.bus');
        const offs = [
            bus.subscribe('extension_live', onExtensionLive),
            bus.subscribe('resync', onResync),
        ];
        return () => offs.forEach((off) => off());
    }

    /**
     * The live views this agent has right now: one per listed extension with
     * a latest screenshot for it.
     *
     * @param {string} agentId
     * @returns {Array<{extensionId: string, label: string, icon: string}>}
     *   In the extension list's order; empty when it has none. `icon` is
     *   always one of ICONS.
     */
    function views(agentId) {
        return listed
            .filter((view) => items.has(view.id) && items.get(view.id).has(agentId))
            .map((view) => ({ extensionId: view.id, label: view.label, icon: view.icon }));
    }

    /**
     * @param {string} extensionId
     * @param {string} agentId
     * @returns {object|null} The agent's latest live item in that extension.
     */
    function latest(extensionId, agentId) {
        const forExtension = items.get(extensionId);
        return (forExtension && forExtension.get(agentId)) || null;
    }

    /**
     * What a listed extension calls its view, e.g. "Browser view".
     *
     * @param {string} extensionId
     * @returns {string}
     * @throws {Error} When the extension is not a listed live view: a caller
     *   asking for one it never saw is a bug, not a view with no name.
     */
    function label(extensionId) {
        const view = listed.find((entry) => entry.id === extensionId);
        if (!view) throw new Error(`[live-status] "${extensionId}" is not a listed live view`);
        return view.label;
    }

    return { ICONS, attach, subscribe, refresh, views, latest, label };
})();
