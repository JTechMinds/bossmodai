/**
 * BossMod AI — the office summary in the context column.
 *
 * A DOM room summary, not a second canvas renderer. It answers "who is around
 * and who needs me", and hands off to the Office place for the map and to
 * Metrics for the numbers.
 *
 * Rooms come from `agent.location` on the world snapshot — a room NAME that
 * db.get_world_state() derives from coordinates, or nothing at all when the
 * agent is off-map. There is no room id on the wire, so this groups by that
 * name and renders the unplaced group as a real room: dropping those agents
 * would hide them from the operator entirely (spec 7).
 *
 * States: loading until the roster's first publish, empty when nobody is
 * hired, ready otherwise. There is deliberately no error state — the roster is
 * fetched once, by the rail, which shows one error line for it. Two error lines
 * for one failed request is worse than one.
 */
const BossModMiniOffice = (() => {
    const { h, clear } = BossModDom;

    /** Where the agents db.get_world_state() could not place are shown. */
    const UNPLACED_ROOM = 'Unknown';

    /**
     * Build the office summary.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `roster` and `needs`.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When store or navigate is missing.
     */
    function createMiniOffice(deps) {
        const { store, navigate } = deps || {};
        if (!store) throw new Error('[mini-office] deps.store is required');
        if (typeof navigate !== 'function') throw new Error('[mini-office] deps.navigate is required');

        const disposers = [];
        let loaded = store.getState().roster.length > 0;

        const roomsEl = h('div', { class: 'mini-office-rooms' });
        const statEl = h('p', { class: 'mini-office-stat' });

        const element = h('section', { class: 'mini-office' },
            h('h2', { class: 'context-title' }, 'The office'),
            roomsEl,
            statEl,
            h('div', { class: 'mini-office-links' },
                h('button', {
                    class: 'context-link',
                    type: 'button',
                    onclick: () => navigate('office'),
                }, 'Open the map'),
                h('button', {
                    class: 'context-link',
                    type: 'button',
                    onclick: () => navigate('metrics'),
                }, 'Metrics')));

        /**
         * Group the roster by room name, unplaced agents last.
         *
         * @param {object[]} roster
         * @returns {Array<{name: string, agents: object[]}>}
         */
        function byRoom(roster) {
            const rooms = new Map();
            roster.forEach((agent) => {
                const name = String(agent.location || '').trim() || UNPLACED_ROOM;
                if (!rooms.has(name)) rooms.set(name, []);
                rooms.get(name).push(agent);
            });
            const names = Array.from(rooms.keys()).sort((a, b) => {
                if (a === UNPLACED_ROOM) return 1;
                if (b === UNPLACED_ROOM) return -1;
                return a.localeCompare(b);
            });
            return names.map((name) => ({ name, agents: rooms.get(name) }));
        }

        function seat(agent, needy) {
            const wanted = needy.has(agent.id);
            const initial = String(agent.name || '?').trim().charAt(0).toUpperCase() || '?';
            return h('button', {
                class: 'mini-office-seat',
                type: 'button',
                'data-agent-id': agent.id,
                // The ping is decorative; the name states the fact instead.
                'aria-label': wanted ? `${agent.name} — needs you` : String(agent.name),
                style: `background:${agent.color}`,
                onclick: () => {
                    store.setState({ contextMode: 'desk', deskAgentId: agent.id });
                },
            },
                h('span', { class: 'mini-office-initial', 'aria-hidden': 'true' }, initial),
                wanted
                    ? h('span', { class: 'mini-office-ping', 'aria-hidden': 'true' })
                    : null);
        }

        function render() {
            const state = store.getState();
            const roster = state.roster;
            clear(roomsEl);

            if (!loaded) {
                roomsEl.append(h('p', { class: 'context-skeleton' }, 'Loading the floor…'));
                statEl.textContent = '';
                return;
            }
            if (roster.length === 0) {
                roomsEl.append(h('p', { class: 'context-empty' },
                    'Nobody is hired yet. Hire someone from the rail and they will take a desk.'));
                statEl.textContent = '';
                return;
            }

            const needy = new Set(state.needs.map((need) => need.agentId));
            byRoom(roster).forEach((room) => {
                roomsEl.append(h('div', { class: 'mini-office-room' },
                    h('p', { class: 'mini-office-room-name' }, room.name),
                    h('div', { class: 'mini-office-seats' },
                        room.agents.map((agent) => seat(agent, needy)))));
            });

            const wanted = roster.filter((agent) => needy.has(agent.id)).length;
            statEl.textContent = wanted === 0
                ? `${roster.length} on the floor · nobody needs you`
                : `${roster.length} on the floor · ${wanted} need${wanted === 1 ? 's' : ''} you`;
        }

        disposers.push(store.subscribe((s) => s.roster, () => {
            // The first publish is what turns the skeleton off — including the
            // publish of an empty roster, which is a real answer.
            loaded = true;
            render();
        }));
        disposers.push(store.subscribe((s) => s.needs, render));

        render();

        return {
            element,

            /**
             * Drain every subscription this view created.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createMiniOffice };
})();
