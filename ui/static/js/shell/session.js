/**
 * BossMod AI — session persistence.
 *
 * Restores where the operator was, so a reload does not dump them at square
 * one. Restore is validated, never trusted: a corrupt or malformed value is
 * dropped and logged rather than applied. Whether a floor's remembered chat
 * still exists is shell/floor-chat.js's check, once the live lists load.
 */
const BossModSession = (() => {
    const STORAGE_KEY = 'bossmod_ui';

    /**
     * The only keys that persist. Server collections never do, and neither
     * does transient UI: the desk is a modal (context/desk-dialog.js), and a
     * reload that reopened one would be side-panel behaviour the modal
     * standard retired. An old blob's `contextMode` is simply not read.
     * Neither is its global `conversationId`/`conversationKind`: each floor
     * now remembers its own chat in `conversationByFloor`.
     */
    const PERSISTED_KEYS = Object.freeze([
        'place', 'conversationByFloor', 'railCollapsed', 'currentFloorId',
    ]);

    const DEFAULTS = Object.freeze({
        place: 'chat',
        conversationByFloor: {},
        railCollapsed: false,
        currentFloorId: 'lobby',
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
     * Keep only well-formed `{ [floorId]: {id, kind} }` entries.
     *
     * @param {*} map  The restored value.
     * @returns {object} A fresh map; a non-object becomes `{}`. Anything
     *   dropped is logged, never silently discarded.
     */
    function sanitizeConversationByFloor(map) {
        if (!map || typeof map !== 'object' || Array.isArray(map)) {
            console.warn('[session] discarding a malformed per-floor chat map');
            return {};
        }
        const clean = {};
        let dropped = 0;
        for (const floorId of Object.keys(map)) {
            const entry = map[floorId];
            const valid = floorId !== ''
                && entry && typeof entry === 'object'
                && typeof entry.id === 'string' && entry.id !== ''
                && (entry.kind === 'agent' || entry.kind === 'thread');
            if (valid) clean[floorId] = { id: entry.id, kind: entry.kind };
            else dropped += 1;
        }
        if (dropped > 0) console.warn(`[session] dropped ${dropped} malformed per-floor chat entries`);
        return clean;
    }

    /**
     * Reconcile a restored session against what actually exists now.
     *
     * @param {object} restored
     * @param {{places: string[]}} live  The registered place ids.
     * @returns {object} a session safe to apply
     */
    function validate(restored, live) {
        const result = Object.assign({}, DEFAULTS, restored);

        if (live.places.indexOf(result.place) === -1) {
            result.place = DEFAULTS.place;
        }

        result.conversationByFloor = sanitizeConversationByFloor(result.conversationByFloor);

        result.railCollapsed = result.railCollapsed === true;
        if (typeof result.currentFloorId !== 'string' || !result.currentFloorId) {
            result.currentFloorId = DEFAULTS.currentFloorId;
        }

        return result;
    }

    return { load, save, validate, PERSISTED_KEYS, DEFAULTS };
})();
