/**
 * BossMod AI — the needs-you queue.
 *
 * One list of everything waiting on an operator decision, merged from two
 * places: `GET /api/needs` (consent, approval, blocked) and the existing
 * `diagnostic` topic (error). Four dumb surfaces read `store.needs`; only this
 * module writes it, and `needs/need-shape.js` is the only file that ever sees
 * a backend field name.
 *
 * Two properties matter more than the rest and are commented where they live:
 * the signature guard, which stops a continuously ticking world from
 * re-rendering the whole shell, and the rollback in `resolve`, without which a
 * failed approval would look like a granted one.
 */
const BossModNeeds = (() => {
    const { ACTIVITY_TRIGGERS, normalise, normaliseDiagnostic } = BossModNeedShape;

    const NEEDS_URL = '/api/needs';

    /**
     * Build the needs store.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `needs` is the only slice
     *   this module writes.
     * @param {object} deps.bus    Topic bus, for `diagnostic`, `activity` and
     *   `resync`.
     * @param {Function} deps.api  Authenticated fetch helper.
     * @returns {{ refresh: () => Promise<void>,
     *             resolve: (need: object, action: object) => Promise<void>,
     *             getError: () => string,
     *             subscribeError: (fn: (message: string) => void) => (() => void),
     *             destroy: () => void }}
     * @throws {Error} When store, bus, or api is missing — a half-wired queue
     *   would report "nothing needs you" forever.
     */
    function createNeedsStore(deps) {
        const { store, bus, api } = deps || {};
        if (!store) throw new Error('[needs] deps.store is required');
        if (!bus) throw new Error('[needs] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[needs] deps.api is required');

        const disposers = [];
        /** Client-held error needs, deduped by diagnostic id. */
        const errorNeeds = new Map();
        const errorListeners = new Set();
        const arrivalListeners = new Set();

        let serverNeeds = [];
        let current = [];
        let signature = '';
        let lastError = '';
        let knownIds = new Set();
        /**
         * True while the next snapshot is a BASELINE rather than a set of
         * arrivals — at construction, and again after a resync.
         *
         * Only this module can tell the difference. A boot list of eight
         * pending needs is eight things that were already true; a resync list
         * is a re-read of everything missed during the outage. Either one,
         * diffed naively, throws the whole queue at the operator as arrivals —
         * the cure becoming the disease. An activity-triggered refresh is NOT a
         * baseline: whatever it brings really did just happen.
         */
        let baselinePending = true;

        function setError(message) {
            if (message === lastError) return;
            lastError = message;
            for (const fn of Array.from(errorListeners)) {
                try {
                    fn(lastError);
                } catch (err) {
                    console.error('[needs] an error listener threw', err);
                }
            }
        }

        /**
         * Publish a list, but only when it actually differs.
         *
         * `world_update` and `diagnostic` arrive continuously. The store
         * compares selected values by reference, so writing a freshly built
         * array on every tick would re-render the roster, the bell, the bar and
         * the toast forever. The signature is the identity of the queue —
         * which needs, of which kind — and nothing below that granularity is
         * worth a repaint.
         *
         * @param {object[]} next
         * @param {boolean} force  Publish even when the signature matches. Used
         *   only by the rollback path, where the list holds the same needs but
         *   one of them now carries an error the operator must see.
         */
        function publish(next, force) {
            const nextSignature = next.map((item) => `${item.id}:${item.kind}`).sort().join('|');
            if (!force && nextSignature === signature) return;
            signature = nextSignature;
            current = next;
            store.setState({ needs: next });
            notifyArrivals(next);
        }

        /** Tell the toast which ids are new, and never on a baseline. */
        function notifyArrivals(next) {
            const arrivals = baselinePending
                ? []
                : next.filter((item) => !knownIds.has(item.id));
            knownIds = new Set(next.map((item) => item.id));
            if (arrivals.length === 0) return;
            for (const fn of Array.from(arrivalListeners)) {
                try {
                    fn(arrivals);
                } catch (err) {
                    console.error('[needs] an arrival listener threw', err);
                }
            }
        }

        /** Merge the server list with the client-held error needs, newest first. */
        function recompute() {
            const merged = serverNeeds.concat(Array.from(errorNeeds.values()));
            const seen = new Set();
            const deduped = merged.filter((item) => {
                // The inline consent card and the queue entry are one need
                // (spec 13): dedupe by request id so it resolves once.
                if (seen.has(item.id)) return false;
                seen.add(item.id);
                return true;
            });
            deduped.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
            publish(deduped, false);
            // Cleared here rather than inside publish: an unchanged signature
            // returns early, and a baseline that never became a real snapshot
            // would swallow the next genuine arrival.
            baselinePending = false;
        }

        /**
         * Re-read `GET /api/needs`.
         *
         * Guarded by a load generation because refresh has four callers — boot,
         * resync, a resolution, and every matching `activity` broadcast — and
         * `status_changed` fires on each agent lifecycle transition, so
         * overlapping reads are routine rather than theoretical. Without the
         * guard, whichever response resolves LAST wins instead of whichever was
         * issued last, and the queue can settle on an older snapshot until some
         * later event happens to dislodge it. Every other async loader in this
         * codebase uses this gate; the queue is not an exception.
         *
         * @returns {Promise<void>} Never rejects. A failed refresh keeps the
         *   last good list: blanking the bell because one fetch failed would
         *   tell the operator nothing needs them, which is a lie. The failure
         *   is logged and surfaced through `getError` / `subscribeError`.
         */
        async function refresh() {
            const loadId = refreshLoad.next();
            try {
                const res = await api(NEEDS_URL, { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const rows = await res.json();
                if (!Array.isArray(rows)) throw new Error('the queue did not return a list');
                if (!refreshLoad.isCurrent(loadId)) return;
                serverNeeds = rows.map(normalise);
            } catch (err) {
                console.error('[needs] could not refresh the queue', err);
                // A superseded read's failure is not the operator's problem: a
                // newer read is already in flight and owns the error line.
                if (!refreshLoad.isCurrent(loadId)) return;
                setError('Could not refresh what needs you. This list may be out of date.');
                return;
            }
            setError('');
            recompute();
        }

        /**
         * Carry out one of a need's server-described actions.
         *
         * @param {object} need
         * @param {object} action  One entry from `need.actions`; the client
         *   never builds a resolution URL of its own (spec 5.2).
         * @returns {Promise<void>}
         * @throws {Error} Re-thrown after the queue has been restored, so the
         *   caller leaves the entry explained rather than assuming success.
         */
        async function resolve(need, action) {
            if (!need || !action) throw new Error('[needs] resolve() needs a need and an action');
            const before = current.slice();
            // A GET action inspects ("Open task", "Open diagnostics"); it
            // decides nothing, so removing the entry for one would tell the
            // operator they had settled something they had only looked at.
            const decides = action.method !== 'GET';
            if (decides) {
                publish(current.filter((item) => item.id !== need.id), false);
            }
            try {
                const res = await api(action.href, { method: action.method });
                if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
            } catch (err) {
                // Restoring is not optional. A dropped failure leaves the
                // operator believing they approved something they did not.
                console.error('[needs] resolution failed', err);
                publish(before.map((item) => (item.id === need.id
                    ? Object.assign({}, item, { error: (err && err.message) || 'Could not resolve.' })
                    : item)), true);
                throw err;
            }
            // An error need is client-held: the server list does not contain
            // it, so nothing else would ever clear it. Reading the diagnostic
            // is the acknowledgement, and a queue entry that can never leave is
            // worse than one dismissed by being read.
            errorNeeds.delete(need.id);
            await refresh();
        }

        // A stale response must never overwrite a newer one; see refresh().
        const refreshLoad = BossModGates.createLoadGeneration();

        // ─── Live updates ───

        disposers.push(bus.subscribe('diagnostic', (data) => {
            const need = normaliseDiagnostic(data);
            if (!need) return;
            if (errorNeeds.has(need.id)) return;
            errorNeeds.set(need.id, need);
            recompute();
        }));

        disposers.push(bus.subscribe('activity', (entry) => {
            if (!entry) return;
            if (ACTIVITY_TRIGGERS.indexOf(String(entry.event || '')) === -1) return;
            void refresh();
        }));

        // A dropped socket loses every activity broadcast in the gap. What
        // comes back is a re-read, so it re-establishes the baseline.
        disposers.push(bus.subscribe('resync', () => {
            baselinePending = true;
            void refresh();
        }));

        // No polling interval, by design (spec 5.3): a timer would only mask a
        // missing trigger instead of failing the test that guards the set.
        void refresh();

        return {
            refresh,
            resolve,

            /**
             * The last refresh failure, or '' when the queue is healthy.
             * @returns {string}
             */
            getError() {
                return lastError;
            },

            /**
             * Watch the refresh-failure line. A failed fetch changes no need,
             * so `store.needs` would never notify a surface that it is showing
             * a list it could not confirm.
             *
             * @param {(message: string) => void} fn
             * @returns {() => void} disposer — callers MUST call this on unmount
             */
            subscribeError(fn) {
                errorListeners.add(fn);
                return () => { errorListeners.delete(fn); };
            },

            /**
             * Watch for needs that are NEW since the previous snapshot.
             *
             * Never the contents of a boot or resync reload — see
             * `baselinePending`. This is what the toast fires on; the bell and
             * the bar read the whole list from the store instead.
             *
             * @param {(arrivals: object[]) => void} fn
             * @returns {() => void} disposer — callers MUST call this on unmount
             */
            subscribeArrivals(fn) {
                arrivalListeners.add(fn);
                return () => { arrivalListeners.delete(fn); };
            },

            /**
             * Drain every subscription this store created.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
                errorListeners.clear();
                arrivalListeners.clear();
            },
        };
    }

    return { createNeedsStore };
})();
