/**
 * BossMod AI — the header floor switcher.
 *
 * One trigger naming the floor the operator is on. It opens a panel listing
 * EVERY floor with its agent count and a check on the current one, a `⋯` per
 * row to edit that floor (shell/floor-edit.js), and a `+ New floor` door that
 * turns into an inline name field in place. There is no browse mode: the
 * operator is always on exactly one floor (`state.currentFloorId`).
 *
 * The panel hangs off `.floor-switcher`, a positioned host wrapping the
 * trigger — the core/menu-select.js pattern. It used to be appended to
 * `document.body`, which is not positioned, so `.menu`'s `top: 100%` put it
 * below the viewport and focusing its first row scrolled the header away.
 *
 * Requests go through shell/floor-api.js; this file draws.
 */
const BossModFloorSwitcher = (() => {
    const { h, clear } = BossModDom;
    const LOBBY_ID = 'lobby';
    const LOAD_ERROR = 'Floors could not load.';

    /**
     * @param {number} count
     * @returns {string} "1 agent", "3 agents".
     */
    function agentsLabel(count) {
        return count === 1 ? '1 agent' : `${count} agents`;
    }

    /**
     * Mount the switcher.
     *
     * @param {object} deps
     * @param {object} deps.store  Reads `floors`, `currentFloorId`, `roster`;
     *   writes `floors` and `currentFloorId`.
     * @param {Function} deps.apiFetch  The shell's authenticated fetch.
     * @returns {{element: HTMLElement, destroy: () => void}}
     */
    function mount({ store, apiFetch }) {
        const floorApi = BossModFloorApi.createFloorApi({ apiFetch });

        const nameEl = h('span', { class: 'floor-switcher-name' });
        const errorEl = h('span', {
            class: 'visually-hidden', id: 'floor-switcher-error', role: 'alert',
        });
        const trigger = h('button', {
            class: 'floor-switcher-trigger',
            type: 'button',
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        },
            h('i', { 'data-lucide': 'layers', 'aria-hidden': 'true' }),
            nameEl,
            h('i', { 'data-lucide': 'chevron-down', 'aria-hidden': 'true' }));
        // Positioned (shell.css): THE host the panel hangs off.
        const element = h('div', { class: 'floor-switcher' }, trigger, errorEl);

        let menu = null;

        function floorsOf(state) {
            return Array.isArray(state.floors) ? state.floors : [];
        }

        function currentName(state) {
            return BossModFloorScope.floorName(state, BossModFloorScope.visibleFloorId(state));
        }

        function paint(state) {
            const name = currentName(state);
            nameEl.textContent = name;
            trigger.setAttribute('aria-label', `Floor: ${name}`);
        }

        /** Say it on the trigger, not only in the console. */
        function showLoadError(err) {
            console.error('[floor-switcher] could not load floors', err);
            errorEl.textContent = LOAD_ERROR;
            trigger.setAttribute('title', LOAD_ERROR);
            trigger.setAttribute('aria-describedby', 'floor-switcher-error');
            trigger.setAttribute('data-error', 'true');
        }

        function clearLoadError() {
            errorEl.textContent = '';
            trigger.removeAttribute('title');
            trigger.removeAttribute('aria-describedby');
            trigger.removeAttribute('data-error');
        }

        /**
         * Read the floors into the store. A current floor that no longer
         * exists (deleted elsewhere) falls back to Lobby.
         *
         * @returns {Promise<boolean>} False when the request failed; the
         *   failure is already shown and logged.
         */
        async function loadFloors() {
            let rows;
            try {
                rows = await floorApi.listFloors();
            } catch (err) {
                showLoadError(err);
                return false;
            }
            clearLoadError();
            const patch = { floors: rows };
            const current = store.getState().currentFloorId;
            if (!rows.some((floor) => floor.id === current)) patch.currentFloorId = LOBBY_ID;
            store.setState(patch);
            return true;
        }

        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /** One floor's row: choose it, or `⋯` to edit it. */
        function floorRow(state, floor) {
            const count = (state.roster || [])
                .filter((agent) => BossModFloorScope.floorOf(agent) === floor.id).length;
            const current = floor.id === BossModFloorScope.visibleFloorId(state);
            const choice = h('button', {
                class: 'menu-action floor-choice',
                type: 'button',
                'aria-pressed': String(current),
                'aria-label': `${floor.name}, ${agentsLabel(count)}`,
                onclick: () => {
                    close();
                    if (!current) store.setState({ currentFloorId: floor.id });
                },
            },
                // The slot is always there so every name starts on one edge;
                // only the current row fills it.
                h('span', { class: 'floor-check', 'aria-hidden': 'true' },
                    current ? h('i', { 'data-lucide': 'check', 'aria-hidden': 'true' }) : null),
                h('span', { class: 'menu-select-label' }, floor.name),
                h('span', { class: 'menu-action-count', 'aria-hidden': 'true' }, String(count)));
            const more = h('button', {
                class: 'floor-row-more',
                type: 'button',
                'aria-label': `Edit floor ${floor.name}`,
                'data-tooltip': `Edit floor ${floor.name}`,
                onclick: () => {
                    close();
                    BossModFloorEdit.open({ store, floorApi, floorId: floor.id, reloadFloors: loadFloors });
                },
            }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));
            return h('div', { class: 'floor-row' }, choice, more);
        }

        /**
         * The `+ New floor` door, and the inline field it becomes.
         *
         * Esc inside the field goes back to the door without closing the
         * panel. The overlay's Esc handler listens on `document` in the bubble
         * phase (core/overlay-focus.js), so stopping propagation at the input
         * keeps it from ever hearing this one key.
         */
        function newFloorSlot() {
            const slot = h('div', { class: 'floor-new' });
            const door = h('button', {
                class: 'menu-door', type: 'button', onclick: () => showForm(),
            },
                h('i', { 'data-lucide': 'plus', 'aria-hidden': 'true' }),
                h('span', {}, 'New floor'));

            function showDoor() {
                clear(slot);
                slot.append(door);
                door.focus();
            }

            function showForm() {
                const input = h('input', {
                    class: 'field-input',
                    type: 'text',
                    'aria-label': 'Floor name',
                    placeholder: 'Floor name',
                    maxlength: '80',
                    autocomplete: 'off',
                    onkeydown: (event) => {
                        if (event.key !== 'Escape') return;
                        event.preventDefault();
                        event.stopPropagation();
                        showDoor();
                    },
                });
                const error = h('p', { class: 'context-error', role: 'alert' });
                const form = h('form', {
                    class: 'floor-new-form',
                    onsubmit: (event) => {
                        event.preventDefault();
                        void create(input, error);
                    },
                }, input, error);
                clear(slot);
                slot.append(form);
                input.focus();
            }

            slot.append(door);
            return slot;
        }

        async function create(input, error) {
            const name = String(input.value || '').trim();
            if (!name) {
                error.textContent = 'Give the floor a name.';
                return;
            }
            error.textContent = '';
            input.disabled = true;
            let created;
            try {
                created = await floorApi.createFloor(name);
            } catch (err) {
                console.error('[floor-switcher] could not create a floor', err);
                error.textContent = err.message || 'The floor could not be created.';
                input.disabled = false;
                input.focus();
                return;
            }
            const loaded = await loadFloors();
            if (!loaded) {
                error.textContent = `${created.name} was created, but the floor list could not reload.`;
                input.disabled = false;
                return;
            }
            store.setState({ currentFloorId: created.id });
            close();
        }

        function toggle() {
            if (menu) {
                close();
                return;
            }
            const state = store.getState();
            const rows = floorsOf(state).map((floor) => floorRow(state, floor));
            menu = BossModOverlays.createMenu({
                anchor: trigger,
                label: 'Floors',
                container: element,
                items: [
                    h('div', { class: 'menu-actions' }, rows),
                    h('hr', { class: 'menu-divider' }),
                    newFloorSlot(),
                ],
                onClose: () => {
                    menu = null;
                    trigger.setAttribute('aria-expanded', 'false');
                },
            });
            menu.element.setAttribute('data-menu', 'floor');
            BossModIcons.paint(menu.element, 'floor-switcher');
            trigger.setAttribute('aria-expanded', 'true');
            // Open on the floor you are on, as a list does, not the first row.
            const chosen = menu.element.querySelector('[aria-pressed="true"]');
            if (chosen) chosen.focus();
        }

        paint(store.getState());
        BossModIcons.paint(element, 'floor-switcher');
        void loadFloors();
        const off = store.subscribe(
            (s) => [
                s.currentFloorId || LOBBY_ID,
                floorsOf(s).map((floor) => `${floor.id}:${floor.name}`).join(','),
            ].join('|'),
            () => paint(store.getState()),
        );

        return {
            element,
            destroy() {
                off();
                close();
            },
        };
    }

    return { mount };
})();
