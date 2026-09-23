/**
 * BossMod AI — the operator's current floor scope.
 *
 * The header switcher is the only control. This module does not draw
 * anything. Chat, Office, Tasks, Files, Metrics, and Log read the same
 * store fields so the scope is global. "All floors" is browse: it finds
 * people and threads. It is not permission to assign, hire, or wake
 * across a floor. That denial stays in the engine.
 */
const BossModFloorScope = (() => {
    const LOBBY_ID = 'lobby';

    let storeRef = null;

    /**
     * Remember the store so a hire can read the floor the operator is on.
     *
     * @param {object} store
     */
    function attach(store) {
        storeRef = store;
    }

    /**
     * @param {object} agent
     * @returns {string}
     */
    function floorOf(agent) {
        if (!agent) return '';
        return String(agent.floorId || agent.floor_id || '');
    }

    /**
     * The floor a list should show. Null means every floor (browse).
     *
     * @param {object} state
     * @returns {string|null}
     */
    function visibleFloorId(state) {
        const scope = state && state.floorScope;
        if (scope === 'all') return null;
        if (scope === 'other') {
            return state.browseFloorId || state.currentFloorId || LOBBY_ID;
        }
        return (state && state.currentFloorId) || LOBBY_ID;
    }

    /**
     * The one floor the office draws. Browse-all does not mix the map.
     *
     * @param {object} state
     * @returns {string}
     */
    function officeFloorId(state) {
        if (state && state.floorScope === 'other' && state.browseFloorId) {
            return state.browseFloorId;
        }
        return (state && state.currentFloorId) || LOBBY_ID;
    }

    /**
     * Home floor for a new hire: the concrete floor on screen, never "all".
     *
     * @returns {string}
     */
    function hireFloorId() {
        const state = storeRef ? storeRef.getState() : {};
        return officeFloorId(state);
    }

    /**
     * @param {object} state
     * @param {string} floorId
     * @returns {string}
     */
    function floorName(state, floorId) {
        const floors = (state && state.floors) || [];
        const found = floors.find((floor) => floor && floor.id === floorId);
        if (found && found.name) return found.name;
        if (floorId === LOBBY_ID) return 'Lobby';
        return floorId || 'Lobby';
    }

    /**
     * @param {object} state
     * @param {object[]} people
     * @returns {object[]}
     */
    function filterPeople(state, people) {
        const floorId = visibleFloorId(state);
        const rows = Array.isArray(people) ? people : [];
        if (!floorId) return rows.slice();
        return rows.filter((agent) => floorOf(agent) === floorId);
    }

    /**
     * People seated on the office's one floor, including while browsing all.
     *
     * @param {object} state
     * @param {object[]} people
     * @returns {object[]}
     */
    function officePeople(state, people) {
        const floorId = officeFloorId(state);
        const rows = Array.isArray(people) ? people : [];
        return rows.filter((agent) => floorOf(agent) === floorId);
    }

    /**
     * @param {object} state
     * @param {object[]} threads
     * @returns {object[]}
     */
    function filterThreads(state, threads) {
        const floorId = visibleFloorId(state);
        const rows = Array.isArray(threads) ? threads : [];
        if (!floorId) return rows.slice();
        return rows.filter((thread) => floorOf(thread) === floorId);
    }

    /**
     * @param {object} state
     * @param {object[]} tasks
     * @returns {object[]}
     */
    function filterTasks(state, tasks) {
        const floorId = visibleFloorId(state);
        const rows = Array.isArray(tasks) ? tasks : [];
        if (!floorId) return rows.slice();
        return rows.filter((task) => floorOf(task) === floorId);
    }

    /**
     * Whether a log row's agent is on the visible floor.
     * A row with no agent stays. An unknown agent is hidden while scoped.
     *
     * @param {object} state
     * @param {string|null|undefined} agentId
     * @returns {boolean}
     */
    function allowsAgent(state, agentId) {
        const floorId = visibleFloorId(state);
        if (!floorId) return true;
        if (!agentId) return true;
        const roster = (state && state.roster) || [];
        const agent = roster.find((item) => item && item.id === agentId);
        if (!agent) return false;
        return floorOf(agent) === floorId;
    }

    return {
        LOBBY_ID,
        attach,
        floorOf,
        visibleFloorId,
        officeFloorId,
        hireFloorId,
        floorName,
        filterPeople,
        officePeople,
        filterThreads,
        filterTasks,
        allowsAgent,
    };
})();
