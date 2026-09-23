/**
 * BossMod AI — the Threads section of the floor settings.
 *
 * Lists the floor's ACTIVE threads, read from the server rather than the
 * rail's list (which may be showing archives), each with its members' faces
 * and a `⋯` → Move to…. `Add threads` picks active threads from the other
 * floors. A thread brings its members; the move confirm layer
 * (shell/floor-move-confirm.js) shows who and what else.
 *
 * The frame and row shapes are shell/floor-settings.js's, passed in `ctx`.
 */
const BossModFloorThreads = (() => {
    const { h } = BossModDom;

    /**
     * The members' faces, named in words for a screen reader.
     *
     * @param {object} thread  A channel summary with `members`.
     * @returns {HTMLElement}
     */
    function faces(thread) {
        const members = Array.isArray(thread.members) ? thread.members : [];
        const names = members.map((member) => member.name).join(', ');
        return h('span', { class: 'floor-item-faces', 'aria-label': names ? `Members: ${names}` : 'No members' },
            members.map((member) => BossModAvatar.create({
                name: member.name, color: member.color || null, size: 'chip',
            })));
    }

    /**
     * Build the Threads section.
     *
     * @param {object} ctx  From the floor settings' open (shell/floor-settings.js).
     * @returns {{element: HTMLElement, load: () => Promise<void>,
     *   count: () => (number|null)}} `count` is null until a load succeeded.
     */
    function create(ctx) {
        const { floorApi, floor } = ctx;
        const floorOf = (thread) => BossModFloorScope.floorOf(thread);

        const view = ctx.section({
            key: 'threads',
            title: 'Threads',
            addLabel: 'Add threads',
            emptyText: 'No threads on this floor.',
            onAdd: () => openPicker(),
        });

        function moveTo(target, channelIds, onMoved) {
            BossModFloorMoveConfirm.open({
                floorApi,
                target,
                choice: { agentIds: [], channelIds },
                floorName: ctx.floorName,
                onMoved,
            });
        }

        async function load() {
            view.loading();
            let threads;
            try {
                threads = await floorApi.listActiveThreads();
            } catch (err) {
                console.error('[floor-threads] could not load threads', err);
                view.failed(`Threads could not load. ${err.message || ''}`.trim(), () => { void load(); });
                return;
            }
            view.rows(threads.filter((thread) => floorOf(thread) === floor.id).map((thread) => ctx.row({
                lead: null,
                name: thread.name,
                meta: faces(thread),
                targets: ctx.otherFloors,
                onMoveTo: (target) => moveTo(target, [thread.id], ctx.refresh),
            })));
        }

        function openPicker() {
            const picker = BossModFloorPicker.open({
                title: `Add threads to ${floor.name}`,
                what: 'threads',
                multi: true,
                emptyText: 'No active threads on another floor.',
                load: async () => {
                    const groups = new Map();
                    (await floorApi.listActiveThreads())
                        .filter((thread) => floorOf(thread) !== floor.id)
                        .forEach((thread) => {
                            const from = floorOf(thread);
                            if (!groups.has(from)) groups.set(from, { label: ctx.floorName(from), items: [] });
                            groups.get(from).items.push({
                                id: thread.id,
                                name: thread.name,
                                meta: (thread.members || []).map((member) => member.name).join(', '),
                                lead: null,
                            });
                        });
                    return Array.from(groups.values());
                },
                onNext: (ids) => moveTo(floor, ids, () => {
                    picker.close();
                    ctx.refresh();
                }),
            });
        }

        return { element: view.element, load, count: view.count };
    }

    return { create };
})();
