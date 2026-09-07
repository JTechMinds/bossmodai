/**
 * BossMod AI — observable application state.
 *
 * Selector-subscribed rather than broadcast-on-any-change: world_update
 * arrives on every simulation tick, and a shell that re-rendered on each one
 * would destroy scroll position, drafts, focus, and open disclosures.
 *
 * Server collections (transcripts, file listings, task lists) deliberately do
 * NOT live here. They are owned by the component that fetched them and
 * discarded on unmount.
 */
const BossModStore = (() => {

    /**
     * @param {object} initial
     * @returns {{
     *   getState: () => object,
     *   setState: (patch: object) => void,
     *   subscribe: (selector: (s: object) => any, fn: (value: any, previous: any) => void) => (() => void),
     *   subscriberCount: () => number
     * }}
     */
    function createStore(initial) {
        let state = Object.assign({}, initial);
        const entries = new Set();

        function getState() {
            return state;
        }

        /**
         * Shallow-merge a patch and notify subscribers whose selected value
         * changed by reference.
         *
         * Selectors must return primitives or stable references. A selector
         * building a fresh object each call notifies on every setState.
         */
        function setState(patch) {
            const previous = state;
            state = Object.assign({}, state, patch);
            for (const entry of Array.from(entries)) {
                const next = entry.selector(state);
                if (next === entry.value) continue;
                const before = entry.value;
                entry.value = next;
                try {
                    entry.fn(next, before);
                } catch (err) {
                    // One bad subscriber must not stop the rest or leave state
                    // half-applied. Surfaced, never swallowed.
                    console.error('[store] subscriber threw', err);
                }
            }
        }

        /**
         * @param {(s: object) => any} selector
         * @param {(value: any, previous: any) => void} fn
         * @returns {() => void} disposer — callers MUST call this on unmount
         */
        function subscribe(selector, fn) {
            const entry = { selector, fn, value: selector(state) };
            entries.add(entry);
            return () => { entries.delete(entry); };
        }

        /** Live subscription count. Test surface for the leak guard. */
        function subscriberCount() {
            return entries.size;
        }

        return { getState, setState, subscribe, subscriberCount };
    }

    return { createStore };
})();
