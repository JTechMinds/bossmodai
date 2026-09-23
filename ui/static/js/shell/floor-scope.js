/**
 * BossMod AI — the floor the operator is on.
 *
 * The operator is always on exactly one floor: `state.currentFloorId`.
 * The header switcher is the only control that changes it, and this
 * module draws nothing. Chat, Office, Tasks, Files, Metrics, and Log read
 * the same store field, so the floor is global. There is no browse-every-
 * floor mode; cross-floor denial stays in the engine regardless.
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
     * The floor every list, the office and the metrics show.
     *
     * @param {object} state
     * @returns {string}
     */
    function visibleFloorId(state) {
        return (state && state.currentFloorId) || LOBBY_ID;
    }

    /**
     * Home floor for a new hire: the floor the operator is on.
     *
     * @returns {string}
     */
    function hireFloorId() {
        const state = storeRef ? storeRef.getState() : {};
        return visibleFloorId(state);
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
        return rows.filter((task) => floorOf(task) === floorId);
    }

    /**
     * Whether a log row's agent is on the visible floor.
     * A row with no agent stays. An unknown agent is hidden.
     *
     * @param {object} state
     * @param {string|null|undefined} agentId
     * @returns {boolean}
     */
    function allowsAgent(state, agentId) {
        const floorId = visibleFloorId(state);
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
        hireFloorId,
        floorName,
        filterPeople,
        filterThreads,
        filterTasks,
        allowsAgent,
    };
})();
