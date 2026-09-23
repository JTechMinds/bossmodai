/**
 * BossMod AI — floor settings: Name, People, Threads, Projects.
 *
 * Opened from a floor row's `⋯` in the header switcher. One panel-size modal
 * titled with the floor's name, with no footer: its only ways out are the
 * head's ✕ and, for any floor but Lobby, the head's trash tool, which opens
 * the Delete layer (shell/floor-delete.js) — the "are you sure" step. The
 * name renames in place (core/inline-rename.js). People, Threads and
 * Projects are lists (shell/floor-people.js, floor-threads.js,
 * floor-projects.js) whose rows each carry a `⋯` → Move to…, and whose
 * headers each carry an Add door that picks from the other floors
 * (shell/floor-picker.js). People and threads move through the confirm
 * layer (shell/floor-move-confirm.js).
 *
 * This file owns the frame and the two shapes the three lists share — the
 * section (heading, count, Add door, and its loading / error / empty / rows
 * states) and the row with its Move to… menu — so the lists cannot drift.
 * The menu hangs off a positioned host inside the row, never document.body
 * (the header-collapse RCA in floor-switcher.js).
 *
 * While open it follows the store: a roster or floor-list change (the world
 * and floors_updated broadcasts) repaints People and the Move to… targets.
 */
const BossModFloorSettings = (() => {
    const { h, clear } = BossModDom;
    const NAME_ID = 'floor-settings-name';
    const DELETE_LABEL = 'Delete floor';
    /** Matches db/floors.py FLOOR_NAME_MAX_LENGTH. */
    const NAME_MAX_LENGTH = 80;

    /**
     * One list section: heading with its count, an Add door, and a body that
     * is always in exactly one of loading, failed, or rows (empty included).
     *
     * @param {object} opts
     * @param {string} opts.key  Unique within the view; names the heading id.
     * @param {string} opts.title  "People", "Threads", "Projects".
     * @param {string} opts.addLabel  The Add door's label.
     * @param {() => void} opts.onAdd
     * @param {string} opts.emptyText  What an empty list says.
     * @returns {{element: HTMLElement, loading: () => void,
     *   failed: (message: string, retry: () => void) => void,
     *   rows: (nodes: HTMLElement[]) => void, count: () => (number|null)}}
     *   `count` is null until rows have been shown.
     */
    function section({ key, title, addLabel, onAdd, emptyText }) {
        const headingId = `floor-section-${key}`;
        const heading = h('h3', { class: 'floor-section-title', id: headingId }, title);
        const add = h('button', { class: 'menu-door floor-section-add', type: 'button', onclick: onAdd },
            h('i', { 'data-lucide': 'plus', 'aria-hidden': 'true' }),
            h('span', {}, addLabel));
        const body = h('div', { class: 'floor-section-body' });
        const element = h('section', { class: 'floor-section', 'aria-labelledby': headingId },
            h('div', { class: 'floor-section-head' }, heading, add),
            body);
        let shown = null;

        function say(node) {
            clear(body);
            body.append(node);
        }

        return {
            element,
            loading() {
                shown = null;
                heading.textContent = title;
                say(h('p', { class: 'field-hint' }, 'Loading…'));
            },
            failed(message, retry) {
                shown = null;
                heading.textContent = title;
                say(h('div', { class: 'floor-section-error' },
                    h('p', { class: 'context-error', role: 'alert' }, message),
                    h('button', { class: 'btn btn-sm', type: 'button', onclick: retry }, 'Retry')));
            },
            rows(nodes) {
                shown = nodes.length;
                heading.textContent = `${title} (${nodes.length})`;
                say(nodes.length
                    ? h('ul', { class: 'floor-item-list' }, nodes)
                    : h('p', { class: 'field-hint' }, emptyText));
                BossModIcons.paint(body, 'floor-settings');
            },
            count: () => shown,
        };
    }

    /**
     * One list row with a `⋯` whose menu lists the other floors.
     *
     * @param {object} opts
     * @param {HTMLElement|null} opts.lead  An avatar, or null.
     * @param {string} opts.name
     * @param {HTMLElement|string|null} opts.meta  Under the name.
     * @param {() => Array<{id: string, name: string}>} opts.targets  The
     *   other floors, read when the menu opens.
     * @param {(floor: {id: string, name: string}) => void} opts.onMoveTo
     * @returns {HTMLElement} The `<li>`.
     */
    function row({ lead, name, meta, targets, onMoveTo }) {
        const more = h('button', {
            class: 'floor-row-more',
            type: 'button',
            'aria-label': `Move ${name} to another floor`,
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));
        // Positioned (overlays.css): THE host the Move to… menu hangs off.
        const host = h('div', { class: 'floor-item-more' }, more);
        let menu = null;

        function toggle() {
            if (menu) {
                menu.close();
                return;
            }
            const floors = targets();
            const choices = floors.map((floor) => h('button', {
                class: 'menu-action',
                type: 'button',
                onclick: () => {
                    menu.close();
                    onMoveTo(floor);
                },
            }, h('span', { class: 'menu-select-label' }, floor.name)));
            menu = BossModMenu.createMenu({
                anchor: more,
                container: host,
                label: `Move ${name} to`,
                items: [
                    h('p', { class: 'menu-label' }, 'Move to…'),
                    choices.length
                        ? h('div', { class: 'menu-actions' }, choices)
                        : h('p', { class: 'field-hint' }, 'There is no other floor yet.'),
                ],
                onClose: () => {
                    menu = null;
                    more.setAttribute('aria-expanded', 'false');
                },
            });
            menu.element.setAttribute('data-menu', 'floor-move');
            more.setAttribute('aria-expanded', 'true');
        }

        return h('li', { class: 'floor-item' },
            lead,
            h('div', { class: 'floor-item-who' },
                h('span', { class: 'floor-item-name' }, name),
                meta ? h('span', { class: 'floor-item-meta' }, meta) : null),
            host);
    }

    /**
     * Open the floor settings for one floor.
     *
     * @param {object} deps
     * @param {object} deps.store  Reads `floors` and `roster`.
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @param {string} deps.floorId
     * @param {() => Promise<boolean>} deps.reloadFloors  The switcher's
     *   loader: refreshes `floors` and moves the operator to Lobby when the
     *   floor they were on is gone.
     * @returns {{close: () => void}}
     * @throws {Error} When the floor is not in `state.floors` — settings for a
     *   floor nobody can see would rename or move the wrong thing.
     */
    function open({ store, floorApi, floorId, reloadFloors }) {
        const found = (store.getState().floors || []).find((item) => item.id === floorId);
        if (!found) throw new Error(`[floor-settings] floor "${floorId}" is not loaded`);
        // A copy: a rename updates this view's name without touching the store's row.
        const floor = { ...found };
        const isLobby = floor.id === BossModFloorScope.LOBBY_ID;

        const nameField = BossModInlineRename.create({
            id: NAME_ID,
            label: 'Floor name',
            placeholder: 'Floor name',
            maxLength: NAME_MAX_LENGTH,
            emptyMessage: 'Give the floor a name.',
            saveLabel: 'Save floor name',
            cancelLabel: 'Cancel rename',
            value: floor.name,
            onRename: (name) => rename(name),
        });
        // The rename landed but the list behind it did not reload: said
        // here, because the rename itself succeeded and must not look failed.
        const nameNotice = h('p', { class: 'context-error', role: 'alert' });
        const nameSection = h('div', { class: 'floor-name' },
            nameField.element,
            isLobby
                ? h('p', { class: 'field-hint' }, "Lobby is the default floor and can't be deleted.")
                : null,
            nameNotice);

        const ctx = {
            store,
            floorApi,
            floor,
            section,
            row,
            /** Every floor but this one, as the store has them now. */
            otherFloors: () => (store.getState().floors || []).filter((item) => item.id !== floor.id),
            floorName: (id) => BossModFloorScope.floorName(store.getState(), id),
            /** Repaint every list after a move; broadcasts fix the rest of the app. */
            refresh: () => refresh(),
        };
        const people = BossModFloorPeople.create(ctx);
        const threads = BossModFloorThreads.create(ctx);
        const projects = BossModFloorProjects.create(ctx);
        const content = h('div', { class: 'floor-settings' },
            nameSection, people.element, threads.element, projects.element);

        // Before the frame's ✕, the task detail's `⋯` shape. Lobby has none.
        const deleteTool = isLobby ? null : h('button', {
            class: 'header-icon-btn floor-delete-tool',
            type: 'button',
            'aria-label': DELETE_LABEL,
            'data-tooltip': DELETE_LABEL,
            onclick: () => openDelete(),
        }, h('i', { 'data-lucide': 'trash-2', 'aria-hidden': 'true' }));

        const modal = BossModOverlays.createModal({
            title: floor.name,
            size: 'panel',
            body: content,
            tools: deleteTool ? [deleteTool] : [],
            // No footer: Close would repeat the head's ✕, and Delete is a tool.
            actions: [],
            closeOnBackdrop: false,
            onClose: () => off(),
        });
        BossModIcons.paint(modal.element, 'floor-settings');

        const off = store.subscribe(
            (s) => [
                (s.roster || []).map((agent) => `${agent.id}:${BossModFloorScope.floorOf(agent)}`).join(','),
                (s.floors || []).map((item) => `${item.id}:${item.name}`).join(','),
            ].join('|'),
            () => people.render(),
        );

        function refresh() {
            people.render();
            void threads.load();
            void projects.load();
        }

        /**
         * The inline rename's `onRename`: rejects (with the words to show)
         * when the server refuses, so the draft and edit mode are kept.
         */
        async function rename(name) {
            nameNotice.textContent = '';
            try {
                await floorApi.renameFloor(floor.id, name);
            } catch (err) {
                console.error('[floor-settings] could not rename the floor', err);
                throw new Error(err.status === 409
                    ? 'Another floor already uses that name.'
                    : (err.message || 'The floor could not be renamed.'));
            }
            floor.name = name;
            modal.setTitle(name);
            const loaded = await reloadFloors();
            if (!loaded) nameNotice.textContent = 'Renamed, but the floor list could not reload.';
        }

        function openDelete() {
            BossModFloorDelete.open({
                store,
                floorApi,
                floor,
                threadCount: threads.count(),
                reloadFloors,
                onDeleted: () => modal.close(),
            });
        }

        refresh();
        return { close: () => modal.close() };
    }

    return { open };
})();
