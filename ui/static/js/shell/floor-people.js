/**
 * BossMod AI — the People section of the floor settings.
 *
 * Lists the agents who live on the floor (from the store's roster, which
 * the world broadcast keeps current), each with a `⋯` → Move to…. `Add
 * people` picks agents from the other floors. Both go through the move
 * confirm layer (shell/floor-move-confirm.js), which shows what else moves.
 *
 * The frame and row shapes are shell/floor-settings.js's, passed in `ctx`.
 */
const BossModFloorPeople = (() => {
    /**
     * @param {object} state
     * @param {(agent: object) => boolean} keep
     * @returns {object[]} Roster agents that pass `keep`, by name.
     */
    function agentsWhere(state, keep) {
        return (state.roster || [])
            .filter(keep)
            .sort((a, b) => String(a.name).localeCompare(String(b.name)));
    }

    /**
     * Build the People section.
     *
     * @param {object} ctx  From the floor settings' open (shell/floor-settings.js):
     *   `store`, `floorApi`, `floor`, `section`, `row`, `otherFloors`,
     *   `floorName`, `refresh`.
     * @returns {{element: HTMLElement, render: () => void}} `render` repaints
     *   from the store; the roster is always in hand, so there is no load.
     */
    function create(ctx) {
        const { store, floorApi, floor } = ctx;
        const onFloor = (agent) => BossModFloorScope.floorOf(agent) === floor.id;

        const view = ctx.section({
            key: 'people',
            title: 'People',
            addLabel: 'Add people',
            emptyText: 'No one works here yet.',
            onAdd: () => openPicker(),
        });

        function avatar(agent) {
            return BossModAvatar.create({ name: agent.name, color: agent.color || null, size: 'sm' });
        }

        function render() {
            view.rows(agentsWhere(store.getState(), onFloor).map((agent) => ctx.row({
                lead: avatar(agent),
                name: agent.name,
                meta: agent.role || null,
                targets: ctx.otherFloors,
                onMoveTo: (target) => BossModFloorMoveConfirm.open({
                    floorApi,
                    target,
                    choice: { agentIds: [agent.id], channelIds: [] },
                    floorName: ctx.floorName,
                    onMoved: ctx.refresh,
                }),
            })));
        }

        function openPicker() {
            const picker = BossModFloorPicker.open({
                title: `Add people to ${floor.name}`,
                what: 'people',
                multi: true,
                emptyText: 'No one works on another floor.',
                load: async () => {
                    const groups = new Map();
                    agentsWhere(store.getState(), (agent) => !onFloor(agent)).forEach((agent) => {
                        const from = BossModFloorScope.floorOf(agent);
                        if (!groups.has(from)) groups.set(from, { label: ctx.floorName(from), items: [] });
                        groups.get(from).items.push({
                            id: agent.id, name: agent.name, meta: agent.role || '', lead: avatar(agent),
                        });
                    });
                    return Array.from(groups.values());
                },
                onNext: (ids) => BossModFloorMoveConfirm.open({
                    floorApi,
                    target: floor,
                    choice: { agentIds: ids, channelIds: [] },
                    floorName: ctx.floorName,
                    onMoved: () => {
                        picker.close();
                        ctx.refresh();
                    },
                }),
            });
        }

        return { element: view.element, render };
    }

    return { create };
})();
