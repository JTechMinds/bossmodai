/**
 * BossMod AI — one Log source over two feeds.
 *
 * It owns both REST loads and all three subscriptions — `activity`,
 * `activity_update` and `diagnostic` — and emits only LogRows. Everything above
 * it sees one list; nothing above it can tell which feed a row came from.
 *
 * Two feeds paginate differently, so only the activity feed does: diagnostics
 * are a fixed newest-N window, which is what `/api/diagnostics` returns.
 */
const BossModLogSource = (() => {
    const SHAPE = BossModLogShape;

    const PAGE_SIZE = 50;
    const DIAGNOSTIC_LIMIT = 50;
    /** The client-held diagnostic window, matching the dock-era view. */
    const MAX_LIVE_DIAGNOSTICS = 100;

    /**
     * Build the source.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.bus    Topic bus.
     * @returns {{load: (options?: object) => Promise<void>,
     *            loadMore: () => Promise<void>,
     *            subscribe: (onChange: (rows: object[]) => void) => (() => void),
     *            rows: () => object[],
     *            filter: (filters: object) => object[],
     *            hasMore: () => boolean,
     *            getError: () => string,
     *            destroy: () => void}}
     * @throws {Error} When api or bus is missing.
     */
    function createLogSource(deps) {
        const { api, bus } = deps || {};
        if (typeof api !== 'function') throw new Error('[log-source] deps.api is required');
        if (!bus) throw new Error('[log-source] deps.bus is required');

        const disposers = [];
        const listeners = new Set();
        const load = BossModGates.createLoadGeneration();

        let activityRows = [];
        let diagnosticRows = [];
        let more = false;
        let lastError = '';

        function relink() {
            SHAPE.linkActivityRows(activityRows, diagnosticRows);
        }

        function announce() {
            const list = rows();
            for (const fn of Array.from(listeners)) fn(list);
        }

        /**
         * Both feeds merged, newest first, deduped by key.
         *
         * The key is what makes the WebSocket echo of a row already loaded over
         * REST one row rather than two.
         *
         * @returns {object[]}
         */
        function rows() {
            relink();
            const seen = new Set();
            return activityRows.concat(diagnosticRows)
                .filter((row) => {
                    if (seen.has(row.key)) return false;
                    seen.add(row.key);
                    return true;
                })
                .sort((a, b) => String(b.at).localeCompare(String(a.at)));
        }

        /**
         * The rows a filter set leaves.
         *
         * The agent filter matches on EITHER id or name, because the activity
         * feed identifies agents by name alone and diagnostics by both. A
         * filter that only knew ids would silently hide every activity row.
         *
         * @param {object} filters  `{agentId, agentName, type, search}`.
         * @returns {object[]}
         */
        function filter(filters) {
            const { agentId, agentName, type, search } = filters || {};
            const needle = String(search || '').trim().toLowerCase();
            return rows().filter((row) => {
                if (type && row.type !== type) return false;
                if (agentId || agentName) {
                    const byId = agentId && row.agentId === agentId;
                    const byName = agentName && row.agentName === agentName;
                    if (!byId && !byName) return false;
                }
                if (needle && !`${row.text} ${row.meta}`.toLowerCase().includes(needle)) {
                    return false;
                }
                return true;
            });
        }

        function activityUrl(options, offset) {
            const params = new URLSearchParams();
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(offset));
            if (options.search) params.set('search', options.search);
            if (options.agentName) params.set('agent_name', options.agentName);
            if (options.type) params.set('category', options.type);
            return `/api/activity/feed?${params}`;
        }

        function diagnosticsUrl(options) {
            const params = new URLSearchParams();
            params.set('limit', String(DIAGNOSTIC_LIMIT));
            if (options.agentId) params.set('agent_id', options.agentId);
            return `/api/diagnostics?${params}`;
        }

        async function readJson(url) {
            const res = await api(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
        }

        /**
         * Read both feeds.
         *
         * @param {object} [options]  `{agentId, agentName, type, search}`, passed
         *   to whichever feed can narrow on it server-side.
         * @returns {Promise<void>} Never rejects. A failure keeps the rows
         *   already on screen and reports through getError, because blanking
         *   the Log because one read failed says "nothing happened", which is a
         *   lie about a system that is still running.
         * @throws Nothing.
         */
        async function loadBoth(options) {
            const opts = options || {};
            const loadId = load.next();
            let feed;
            let diagnostics;
            try {
                [feed, diagnostics] = await Promise.all([
                    readJson(activityUrl(opts, 0)),
                    readJson(diagnosticsUrl(opts)),
                ]);
            } catch (err) {
                if (!load.isCurrent(loadId)) return;
                console.error('[log-source] could not read the feeds', err);
                lastError = 'Could not load the log. This list may be out of date.';
                announce();
                return;
            }
            if (!load.isCurrent(loadId)) return;
            lastError = '';
            activityRows = (feed.entries || []).map(SHAPE.fromActivity);
            diagnosticRows = (Array.isArray(diagnostics) ? diagnostics : [])
                .map(SHAPE.fromDiagnostic);
            more = feed.has_more === true;
            announce();
        }

        /**
         * Fetch the next activity page and append it.
         * @param {object} [options]
         * @returns {Promise<void>} Never rejects.
         */
        async function loadMore(options) {
            const loadId = load.next();
            let feed;
            try {
                feed = await readJson(activityUrl(options || {}, activityRows.length));
            } catch (err) {
                if (!load.isCurrent(loadId)) return;
                console.error('[log-source] could not read the next page', err);
                lastError = 'Could not load more of the log.';
                announce();
                return;
            }
            if (!load.isCurrent(loadId)) return;
            lastError = '';
            activityRows = activityRows.concat((feed.entries || []).map(SHAPE.fromActivity));
            more = feed.has_more === true;
            announce();
        }

        function upsertActivity(entry) {
            const row = SHAPE.fromActivity(entry);
            const index = activityRows.findIndex((item) => item.key === row.key);
            if (index === -1) activityRows.unshift(row);
            else activityRows[index] = row;
            announce();
        }

        // Both live feeds, owned here. A consumer that subscribed to one of
        // them itself would be the second renderer this merge exists to remove.
        disposers.push(bus.subscribe('activity', (entry) => {
            if (entry && entry.id !== undefined) upsertActivity(entry);
        }));
        disposers.push(bus.subscribe('activity_update', (entry) => {
            if (entry && entry.id !== undefined) upsertActivity(entry);
        }));
        disposers.push(bus.subscribe('diagnostic', (summary) => {
            if (!summary || summary.id === undefined) return;
            const row = SHAPE.fromDiagnostic(summary);
            const index = diagnosticRows.findIndex((item) => item.key === row.key);
            if (index === -1) diagnosticRows.unshift(row);
            else diagnosticRows[index] = row;
            if (diagnosticRows.length > MAX_LIVE_DIAGNOSTICS) diagnosticRows.pop();
            announce();
        }));

        return {
            load: loadBoth,
            loadMore,

            /**
             * @param {(rows: object[]) => void} onChange
             * @returns {() => void} disposer — callers MUST call this on unmount
             */
            subscribe(onChange) {
                listeners.add(onChange);
                return () => { listeners.delete(onChange); };
            },

            rows,
            filter,

            /** @returns {boolean} Whether the activity feed has another page. */
            hasMore() {
                return more;
            },

            /** @returns {string} '' when both feeds are healthy. */
            getError() {
                return lastError;
            },

            /**
             * Drain every subscription and invalidate any read in flight.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
                listeners.clear();
                load.next();
                activityRows = [];
                diagnosticRows = [];
            },
        };
    }

    return { createLogSource, PAGE_SIZE };
})();
