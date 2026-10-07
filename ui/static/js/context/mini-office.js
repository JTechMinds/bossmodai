/**
 * BossMod AI — the office summary: the whole of Chat's context column.
 *
 * A DOM floor plan, not a second canvas renderer. It answers "who is around
 * and who needs me", and hands off to the Office place for the full map.
 *
 * It must LOOK like that map. It used to be a summary rather than a map — one
 * wide box over a two-column list, tinted by position — and the operator,
 * holding the two side by side, could not tell which room was which. So the
 * operator reversed the "no geometry here" rule: each room now sits on a CSS
 * grid at its tile `bounds` from GET /api/map, and is coloured by its
 * `room_type` with the very tokens office-canvas.js paints those tiles with.
 * What stays out is everything that would make this a renderer: no canvas, no
 * tiles, no desks, and no agent placed by coordinates — seats still group by
 * room name. A future floor plan lays itself out with no code change. Reading
 * the plan and the grid arithmetic are pure, so they live in floor-plan.js.
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
 * It is floor-scoped like every other view of people: it seats only the
 * agents on the visible floor (`currentFloorId`, BossModFloorScope), so it
 * agrees with the roster rail and with the Office chatter under it.
 *
 * States: loading until the roster's first publish, empty when nobody is
 * hired, floor-empty when people are hired but none work on this floor,
 * ready otherwise. The floor plan has a failure state of its own — this
 * module owns that request, so it owns reporting it — and it degrades to the
 * occupied-rooms view rather than to a blank panel. A plan that loads but
 * carries a room it cannot draw takes the same path, loudly: a guessed colour
 * or position would be a map that lies.
 */
const BossModMiniOffice = (() => {
    const { h } = BossModDom;

    /** Where the agents db.get_world_state() could not place are shown. */
    const UNPLACED_ROOM = 'Unknown';

    /** A room with nobody in it is still a room, and says which it is. */
    const EMPTY_ROOM_COPY = 'Empty';

    /** Nobody is hired at all, on any floor. */
    const EMPTY_ROSTER_COPY = 'Nobody is on the roster yet. '
        + 'Add an agent from the rail and they will take a desk.';

    /** People are hired, just not on the floor the operator is looking at. */
    const EMPTY_FLOOR_COPY = 'Nobody works on this floor yet. '
        + 'Add an agent from the rail, or move someone here.';

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

    /** The neutral tone of a room with no place: Unknown, and the degraded list. */
    const UNPLACED_TONE = 'unplaced';

    /**
     * Build the office summary.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `roster`, `needs` and
     *   `currentFloorId`, which scopes the seats to the visible floor.
     * @param {Function} deps.api  Authenticated fetch helper. Used once, for
     *   the floor plan; who is on it comes from the store.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @param {(agentId: string) => void} deps.openDesk  A seat opens that
     *   agent's desk modal (context/desk-dialog.js).
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When store, api, navigate or openDesk is missing.
     */
    function createMiniOffice(deps) {
        const { store, api, navigate, openDesk } = deps || {};
        if (!store) throw new Error('[mini-office] deps.store is required');
        if (typeof api !== 'function') throw new Error('[mini-office] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[mini-office] deps.navigate is required');
        if (typeof openDesk !== 'function') throw new Error('[mini-office] deps.openDesk is required');

        const disposers = [];
        let loaded = store.getState().roster.length > 0;
        let destroyed = false;
        /**
         * The drawable floor plan from GET /api/map: its rooms in map order
         * and their tile bounding box. Null until it answers, and for good
         * when it fails or carries a room this view cannot draw.
         * @type {null|{rooms: Array<{name: string, roomType: string, bounds: number[]}>,
         *   minX: number, minY: number, cols: number, rows: number}}
         */
        let floor = null;

        const roomsEl = h('div', { class: 'mini-office-rooms' });
        // Rooms are patched by name and each room's seats by agent id, so a
        // world tick that moves one agent touches two seats, not the panel.
        const roomRows = BossModDom.createKeyedList(roomsEl);
        /** A room node's own keyed seat list; null for an empty room. */
        const seatLists = new WeakMap();
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
         * With no plan there is no type or position to draw from, so every
         * room here is `unplaced` and the grid lays them out as a list.
         *
         * @param {object[]} roster
         * @returns {Array<{name: string, tone: string, agents: object[], place: null}>}
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
            return names.map((name) => ({
                name, tone: UNPLACED_TONE, agents: rooms.get(name), place: null,
            }));
        }

        /**
         * Every room on the floor, in map order, with who is standing in it.
         *
         * @param {object[]} roster
         * @returns {Array<{name: string, tone: string, agents: object[], place: object|null}>}
         *   One entry per mapped room whether or not anyone is in it, toned
         *   by its type and placed by its bounds, then a single `Unknown`
         *   bucket for everyone the map could not account for — agents with
         *   no location (or on a corridor tile no room covers), and the
         *   anomaly of a location the floor plan does not name. Both are
         *   people, so neither is dropped; the bucket has no place on the plan.
         */
        function floorRooms(roster) {
            const occupancy = byRoom(roster);
            const seats = new Map(occupancy.map((room) => [room.name, room.agents]));
            const rooms = floor.rooms.map((room) => ({
                name: room.name,
                tone: room.roomType,
                agents: seats.get(room.name) || [],
                place: BossModFloorPlan.place(floor, room.bounds),
            }));
            const mapped = new Set(floor.rooms.map((room) => room.name));
            const strays = occupancy
                .filter((room) => !mapped.has(room.name))
                .reduce((all, room) => all.concat(room.agents), []);
            if (strays.length) {
                rooms.push({ name: UNPLACED_ROOM, tone: UNPLACED_TONE, agents: strays, place: null });
            }
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
                onclick: () => openDesk(agent.id),
            },
                // A chip, because a map of a floor is read as a shape rather
                // than as a list of faces. The BUTTON keeps the 24px floor
                // (SC 2.5.8); only the paint inside it shrinks.
                BossModAvatar.create({ name: agent.name, color: agent.color, size: 'chip' }),
                wanted
                    ? h('span', { class: 'mini-office-ping', 'aria-hidden': 'true' })
                    : null);
        }

        /**
         * One room's box. Its seats are filled by render() through the keyed
         * list registered here, so a reused room keeps its seat nodes too.
         *
         * Its grid position is inline because it is data — the plan's bounds,
         * like an avatar's colour is the roster's — not theme.
         *
         * @param {{name: string, tone: string, agents: object[], place: object|null}} room
         * @returns {HTMLElement}
         */
        function roomBox(room) {
            const { place } = room;
            const seats = room.agents.length === 0 ? null : h('div', { class: 'mini-office-seats' });
            const box = h('div', {
                class: room.name === UNPLACED_ROOM
                    ? 'mini-office-room mini-office-room--unplaced' : 'mini-office-room',
                'data-tone': room.tone,
                'data-orient': place && place.vertical ? 'vertical' : null,
                style: place ? `grid-column: ${place.column}; grid-row: ${place.row}` : null,
            },
                h('p', { class: 'mini-office-room-name' }, room.name),
                seats || h('p', { class: 'mini-office-room-empty' }, EMPTY_ROOM_COPY));
            seatLists.set(box, seats ? BossModDom.createKeyedList(seats) : null);
            return box;
        }

        /**
         * A non-room state: the one line replaces every room. It is a
         * sentence, so it drops the layout and is not set on the corridor.
         */
        function message(className, text) {
            roomRows.reset();
            delete roomsEl.dataset.layout;
            roomsEl.append(h('p', { class: className }, text));
        }

        function render() {
            const state = store.getState();
            const roster = BossModFloorScope.filterPeople(state, state.roster);

            if (!loaded) {
                message('context-skeleton', 'Loading the floor…');
                return;
            }
            // The global roster decides which empty state: an office with
            // nobody hired is a different fix from a floor nobody works on.
            if (state.roster.length === 0) {
                message('context-empty', EMPTY_ROSTER_COPY);
                return;
            }
            if (roster.length === 0) {
                message('context-empty', EMPTY_FLOOR_COPY);
                return;
            }

            const needy = new Set(state.needs.map((need) => need.agentId));
            const rooms = floor ? floorRooms(roster) : byRoom(roster);
            // `map` places rooms on the plan's tracks, `list` is the degraded
            // two columns; CSS scopes each to its own value and never mixes them.
            roomsEl.dataset.layout = floor ? 'map' : 'list';
            // A room is rebuilt only when its tone, its emptiness or its place
            // changes; who sits in it is the seat list's business.
            roomRows.sync(rooms, (room) => room.name,
                (room) => [room.tone, room.agents.length === 0,
                    room.place && room.place.column, room.place && room.place.row],
                roomBox);
            rooms.forEach((room, index) => {
                const seats = seatLists.get(roomsEl.childNodes[index]);
                if (!seats) return;
                seats.sync(room.agents, (agent) => agent.id,
                    (agent) => [agent, needy.has(agent.id)], (agent) => seat(agent, needy));
            });
        }

        /**
         * Read the floor plan once, at construction.
         *
         * @returns {Promise<void>} Never rejects. A floor plan that will not
         *   load, or that BossModFloorPlan.read() refuses, is reported
         *   and the panel degrades to the occupied-rooms view — blanking it
         *   would lose the people as well as the rooms.
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
            let plan;
            try {
                plan = BossModFloorPlan.read(rooms);
            } catch (err) {
                console.error('[mini-office] could not draw the floor plan', err);
                mapErrorEl.textContent = MAP_ERROR_COPY;
                return;
            }
            floor = plan;
            // The track counts are the plan's, so they are written once, as
            // data, for context.css's `repeat()`s to read.
            roomsEl.setAttribute('style',
                `--mini-office-cols: ${plan.cols}; --mini-office-rows: ${plan.rows}`);
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
        // The floor plan is the same on every floor, so a switch only re-seats.
        disposers.push(store.subscribe((s) => s.currentFloorId, render));

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
