/**
 * BossMod AI — the Projects section of the floor settings.
 *
 * Lists the floor's projects (its company folder's top-level directories),
 * each with when it last changed and a `⋯` → Move to…. `Add project` picks
 * ONE project from another floor: a move is one folder per request. Both
 * ask first in a small layer that says what the move costs: agents on the
 * floor the project leaves lose access to it.
 *
 * The frame and row shapes are shell/floor-settings.js's, passed in `ctx`.
 */
const BossModFloorProjects = (() => {
    const { h } = BossModDom;
    const CONFIRM_ID = 'floor-project-move-confirm';

    /**
     * Ask, then move one project. Closes itself on success and calls onMoved.
     *
     * @param {object} deps
     * @param {object} deps.floorApi
     * @param {string} deps.project
     * @param {{id: string, name: string}} deps.from
     * @param {{id: string, name: string}} deps.to
     * @param {() => void} deps.onMoved
     * @returns {void}
     */
    function confirmMove({ floorApi, project, from, to, onMoved }) {
        const error = h('p', { class: 'context-error', role: 'alert' });
        const body = h('div', { class: 'floor-move-confirm' },
            h('p', {}, `Move ${project} to ${to.name}? Agents on ${from.name} lose access.`),
            error);
        let busy = false;
        const actions = (working) => [
            { label: 'Cancel', tone: 'quiet' },
            {
                label: working ? 'Moving…' : 'Move project',
                tone: 'primary',
                id: CONFIRM_ID,
                keepOpen: true,
                onSelect: () => { void move(); },
            },
        ];
        const layer = BossModOverlays.createModal({
            title: `Move ${project}`, body, actions: actions(false), closeOnBackdrop: false,
        });

        async function move() {
            if (busy) return;
            busy = true;
            error.textContent = '';
            layer.setActions(actions(true));
            layer.element.querySelector(`#${CONFIRM_ID}`).disabled = true;
            try {
                await floorApi.moveProject(to.id, project, from.id);
            } catch (err) {
                console.error('[floor-projects] could not move the project', err);
                error.textContent = err.message || `${project} could not be moved.`;
                busy = false;
                layer.setActions(actions(false));
                return;
            }
            layer.close();
            onMoved();
        }
    }

    /**
     * Build the Projects section.
     *
     * @param {object} ctx  From the floor settings' open (shell/floor-settings.js).
     * @returns {{element: HTMLElement, load: () => Promise<void>}}
     */
    function create(ctx) {
        const { floorApi, floor } = ctx;

        const view = ctx.section({
            key: 'projects',
            title: 'Projects',
            addLabel: 'Add project',
            emptyText: 'No projects on this floor.',
            onAdd: () => openPicker(),
        });

        async function load() {
            view.loading();
            let projects;
            try {
                projects = await floorApi.listProjects(floor.id);
            } catch (err) {
                console.error('[floor-projects] could not load projects', err);
                view.failed(`Projects could not load. ${err.message || ''}`.trim(), () => { void load(); });
                return;
            }
            view.rows(projects.map((project) => {
                const modified = BossModFormat.formatDateTime(project.modified_at);
                return ctx.row({
                    lead: null,
                    name: project.name,
                    meta: modified ? `Modified ${modified}` : null,
                    targets: ctx.otherFloors,
                    onMoveTo: (target) => confirmMove({
                        floorApi, project: project.name, from: floor, to: target, onMoved: ctx.refresh,
                    }),
                });
            }));
        }

        function openPicker() {
            // Keyed `<floor id>/<project>`: two floors can each hold a "books".
            const sources = new Map();
            const picker = BossModFloorPicker.open({
                title: `Add a project to ${floor.name}`,
                what: 'projects',
                multi: false,
                emptyText: 'No projects on another floor.',
                load: async () => {
                    sources.clear();
                    const others = ctx.otherFloors();
                    const lists = await Promise.all(others.map((other) => floorApi.listProjects(other.id)));
                    return others.map((other, index) => ({
                        label: other.name,
                        items: lists[index].map((project) => {
                            const key = `${other.id}/${project.name}`;
                            sources.set(key, { floor: other, project: project.name });
                            return { id: key, name: project.name, meta: '', lead: null };
                        }),
                    })).filter((group) => group.items.length);
                },
                onNext: ([key]) => {
                    const source = sources.get(key);
                    if (!source) throw new Error(`[floor-projects] unknown project choice "${key}"`);
                    confirmMove({
                        floorApi,
                        project: source.project,
                        from: source.floor,
                        to: floor,
                        onMoved: () => {
                            picker.close();
                            ctx.refresh();
                        },
                    });
                },
            });
        }

        return { element: view.element, load };
    }

    return { create };
})();
