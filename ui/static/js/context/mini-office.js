/**
 * BossMod AI — the office summary in the context column.
 *
 * A DOM room summary, not a second canvas renderer. It answers "who is around
 * and who needs me", and hands off to the Office place for the map.
 *
 * It used to hand off to Metrics as well, with an `Open metrics` link under a
 * line reading `3 on the floor · 3 need you`. Both are gone. The link was a
 * second front door to a place the header nav has carried on every screen
 * since the nav was built, and the sentence was the THIRD statement of a fact
 * already on screen twice: the bell's badge counts what needs the operator,
 * and the seats in this very panel carry a ping each. Counting the roster back
 * to someone looking at a picture of it is not news. The panel ends on the
 * rooms now.
 *
 * The room set is the FLOOR PLAN and the occupancy is the ROSTER. Those are
 * two different questions and this used to answer both with one: it grouped by
 * `agent.location` and rendered the groups, so with every agent in Main
 * Workspace exactly one box drew and the panel read as broken. The rooms come
 * from `GET /api/map` now — the same endpoint places/office/office-canvas.js
 * already consumes, so no engine change was needed for this — and the roster
 * only says who is standing in each one.
 *
 * `agent.location` is a room NAME that db.get_world_state() derives from
 * coordinates (`get_room_at`), or the literal 'Unknown' when it could place
 * nobody. There is no room id on the wire, so the join is by name, and anyone
 * the map cannot account for keeps a real `Unknown` bucket rendered last:
 * dropping them would hide people from the operator entirely (spec 7).
 *
 * States: loading until the roster's first publish, empty when nobody is
 * hired, ready otherwise. The floor plan has a failure state of its own — this
 * module owns that request, so it owns reporting it — and it degrades to the
 * occupied-rooms view rather than to a blank panel.
 */
const BossModMiniOffice = (() => {
    const { h, clear } = BossModDom;

    /** Where the agents db.get_world_state() could not place are shown. */
    const UNPLACED_ROOM = 'Unknown';

    /** A room with nobody in it is still a room, and says which it is. */
    const EMPTY_ROOM_COPY = 'Empty';

    /** The map link's accessible name and its tooltip: one string, never two. */
    const OPEN_OFFICE_LABEL = 'Open the office';

    /**
     * What the operator is told when the floor plan will not load.
     *
     * It names the consequence rather than the request: the panel is still
     * useful, it is just back to showing only where people actually are.
     */
    const MAP_ERROR_COPY = 'Could not load the floor plan. '
        + 'Showing only the rooms someone is standing in.';

    /**
     * The tint ramp the rooms after the first cycle through.
     *
     * The concept hardcodes three rooms; ours come from the map — five today,
     * plus the Unknown bucket, and whatever a future floor plan carries. So
     * the rule is POSITIONAL: rooms keep the map's own order, the first one
     * gets the panel treatment, and each one after takes the next tone. Map
     * order does not change when somebody walks, so a room does not change
     * colour because an agent left it.
     *
     * Four tones with measured ink pairs in tokens.css (6.20-6.41:1). `ok` is
     * deliberately not among them: --ok on --ok-bg measures 4.33:1, which is
     * under AA, and there is no --ok-ink token to fix it with.
     */
    const TONES = Object.freeze(['blue', 'amber', 'teal', 'pink']);

    /**
     * Build the office summary.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `roster` and `needs`.
     * @param {Function} deps.api  Authenticated fetch helper. Used once, for
     *   the floor plan; who is on it comes from the store.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When store, api, or navigate is missing.
     */
    function createMiniOffice(deps) {
        const { store, api, navigate } = deps || {};
        if (!store) throw new Error('[mini-office] deps.store is required');
        if (typeof api !== 'function') throw new Error('[mini-office] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[mini-office] deps.navigate is required');

        const disposers = [];
        let loaded = store.getState().roster.length > 0;
        let destroyed = false;
        /** Room names from GET /api/map, in map order. Null until it answers. */
        let floor = null;

        const roomsEl = h('div', { class: 'mini-office-rooms' });
        // Empty until there is something to say; `.context-error:empty` keeps
        // an empty alert from painting a bordered box around nothing.
        const mapErrorEl = h('p', { class: 'context-error', role: 'alert' });

        const element = h('section', { class: 'mini-office' },
            h('div', { class: 'context-head' },
                h('h2', { class: 'context-title' }, 'Office'),
                // A property of the VIEW, not of the socket: this repaints from
                // the store on every world tick. Whether the connection is
                // healthy is the footer's job, and two indicators that can
                // disagree are worse than one.
                h('span', { class: 'context-meta' }, 'live'),
                // The whole panel is a picture of the floor, so the way to the
                // full one is a map rather than the word `Open` — which named
                // the verb and left the operator to guess the noun. `label` is
                // spent twice, as the accessible name and as the tooltip a
                // pointer gets, so the two cannot drift apart.
                h('button', {
                    class: 'context-head-link',
                    type: 'button',
                    'aria-label': OPEN_OFFICE_LABEL,
                    'data-tooltip': OPEN_OFFICE_LABEL,
                    onclick: () => navigate('office'),
                }, h('i', { 'data-lucide': 'map', 'aria-hidden': 'true' }))),
            mapErrorEl,
            roomsEl);
        // The head's glyph is the only lucide placeholder in this column, and
        // nothing else mounted here paints — so this view paints its OWN
        // subtree rather than sweeping the document for a node it built. It is
        // built once and never replaced, so once is enough.
        BossModIcons.paint(element, 'mini-office');

        /**
         * Group the roster by room name, unplaced agents last.
         *
         * Occupancy only. This is also the degraded view when the floor plan
         * is unreachable: it is less than the whole floor, but it is every
         * person, which is the half the operator cannot do without.
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

        /**
         * Every room on the floor, in map order, with who is standing in it.
         *
         * @param {object[]} roster
         * @returns {Array<{name: string, agents: object[]}>} One entry per
         *   mapped room whether or not anyone is in it, then a single
         *   `Unknown` bucket for everyone the map could not account for —
         *   agents with no location, and the anomaly of a location the floor
         *   plan does not name. Both are people, so neither is dropped.
         */
        function floorRooms(roster) {
            const occupancy = byRoom(roster);
            const seats = new Map(occupancy.map((room) => [room.name, room.agents]));
            const rooms = floor.map((name) => ({ name, agents: seats.get(name) || [] }));
            const mapped = new Set(floor);
            const strays = occupancy
                .filter((room) => !mapped.has(room.name))
                .reduce((all, room) => all.concat(room.agents), []);
            if (strays.length) rooms.push({ name: UNPLACED_ROOM, agents: strays });
            return rooms;
        }

        function seat(agent, needy) {
            const wanted = needy.has(agent.id);
            // The seat is the target and carries the accessible name; the
            // avatar inside it is paint, which is why it is built decoratively
            // rather than as a second, nested control.
            return h('button', {
                class: 'mini-office-seat',
                type: 'button',
                'data-agent-id': agent.id,
                // The ping is decorative; the name states the fact instead.
                'aria-label': wanted ? `${agent.name} — needs you` : String(agent.name),
                onclick: () => {
                    store.setState({ contextMode: 'desk', deskAgentId: agent.id });
                },
            },
                // A chip, because a map of a floor is read as a shape rather
                // than as a list of faces. The BUTTON keeps the 24px floor
                // (SC 2.5.8); only the paint inside it shrinks.
                BossModAvatar.create({ name: agent.name, color: agent.color, size: 'chip' }),
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
                return;
            }
            if (roster.length === 0) {
                roomsEl.append(h('p', { class: 'context-empty' },
                    'Nobody is on the roster yet. Add an agent from the rail and they will take a desk.'));
                return;
            }

            const needy = new Set(state.needs.map((need) => need.agentId));
            const rooms = floor ? floorRooms(roster) : byRoom(roster);
            rooms.forEach((room, index) => {
                roomsEl.append(h('div', {
                    class: 'mini-office-room',
                    // The first room is the wide one and takes the panel
                    // treatment from :first-child; the rest start at the top of
                    // the ramp and cycle. Offset by one, so the ramp's first
                    // tone is the first tint an operator actually sees.
                    'data-tone': index === 0 ? 'main' : TONES[(index - 1) % TONES.length],
                },
                    h('p', { class: 'mini-office-room-name' }, room.name),
                    room.agents.length === 0
                        ? h('p', { class: 'mini-office-room-empty' }, EMPTY_ROOM_COPY)
                        : h('div', { class: 'mini-office-seats' },
                            room.agents.map((agent) => seat(agent, needy)))));
            });
        }

        /**
         * Read the floor plan once, at construction.
         *
         * @returns {Promise<void>} Never rejects. A floor plan that will not
         *   load is reported and the panel degrades to the occupied-rooms view
         *   — blanking it would lose the people as well as the rooms.
         */
        async function loadFloor() {
            let mapData;
            try {
                const res = await api('/api/map', { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                mapData = await res.json();
            } catch (err) {
                if (destroyed) return;
                console.error('[mini-office] could not load the floor plan', err);
                mapErrorEl.textContent = MAP_ERROR_COPY;
                return;
            }
            if (destroyed) return;
            const rooms = mapData && Array.isArray(mapData.rooms) ? mapData.rooms : null;
            if (!rooms) {
                console.error(
                    '[mini-office] could not load the floor plan: /api/map carried no rooms',
                    mapData,
                );
                mapErrorEl.textContent = MAP_ERROR_COPY;
                return;
            }
            floor = rooms
                .map((room) => String((room && room.name) || '').trim())
                .filter((name) => name !== '');
            mapErrorEl.textContent = '';
            render();
        }

        disposers.push(store.subscribe((s) => s.roster, () => {
            // The first publish is what turns the skeleton off — including the
            // publish of an empty roster, which is a real answer.
            loaded = true;
            render();
        }));
        disposers.push(store.subscribe((s) => s.needs, render));

        render();
        void loadFloor();

        return {
            element,

            /**
             * Drain every subscription this view created, and stop painting —
             * an in-flight floor plan is dropped rather than landing in a
             * detached node.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createMiniOffice };
})();
