/**
 * BossMod AI — session persistence.
 *
 * Restores where the operator was, so a reload does not dump them at square
 * one. Restore is validated against live data, never trusted: a persisted
 * conversation whose agent has since been removed silently pointing at nothing
 * is worse than starting clean.
 */
const BossModSession = (() => {
    const STORAGE_KEY = 'bossmod_ui';

    /** The only keys that persist. Server collections never do. */
    const PERSISTED_KEYS = Object.freeze([
        'place', 'conversationId', 'conversationKind', 'contextMode', 'railCollapsed',
    ]);

    const DEFAULTS = Object.freeze({
        place: 'chat',
        conversationId: null,
        conversationKind: null,
        contextMode: 'office',
        railCollapsed: false,
    });

    /**
     * Read the persisted blob. Always returns a complete object.
     * A corrupt blob is discarded whole and logged — never partially applied.
     *
     * @returns {object}
     */
    function load() {
        let raw;
        try {
            raw = localStorage.getItem(STORAGE_KEY);
        } catch (err) {
            console.warn('[session] localStorage unavailable', err);
            return Object.assign({}, DEFAULTS);
        }
        if (!raw) return Object.assign({}, DEFAULTS);

        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch (err) {
            console.warn('[session] discarding corrupt session blob', err);
            return Object.assign({}, DEFAULTS);
        }
        if (!parsed || typeof parsed !== 'object') {
            console.warn('[session] discarding non-object session blob');
            return Object.assign({}, DEFAULTS);
        }

        const result = Object.assign({}, DEFAULTS);
        for (const key of PERSISTED_KEYS) {
            if (Object.prototype.hasOwnProperty.call(parsed, key)) result[key] = parsed[key];
        }
        return result;
    }

    /**
     * Persist only the whitelisted keys.
     * @param {object} state
     */
    function save(state) {
        const slice = {};
        for (const key of PERSISTED_KEYS) slice[key] = state[key];
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(slice));
        } catch (err) {
            console.warn('[session] could not persist session', err);
        }
    }

    /**
     * Reconcile a restored session against what actually exists now.
     *
     * @param {object} restored
     * @param {{places: string[], agentIds: string[], threadIds: string[]}} live
     * @returns {object} a session safe to apply
     */
    function validate(restored, live) {
        const result = Object.assign({}, DEFAULTS, restored);

        if (live.places.indexOf(result.place) === -1) {
            result.place = DEFAULTS.place;
        }

        if (result.conversationId !== null) {
            const pool = result.conversationKind === 'thread' ? live.threadIds : live.agentIds;
            const known = Array.isArray(pool) && pool.indexOf(result.conversationId) !== -1;
            if (!known) {
                result.conversationId = null;
                result.conversationKind = null;
            }
        }

        if (result.contextMode !== 'office' && result.contextMode !== 'desk') {
            result.contextMode = DEFAULTS.contextMode;
        }
        result.railCollapsed = result.railCollapsed === true;

        return result;
    }

    return { load, save, validate, PERSISTED_KEYS, DEFAULTS };
})();
