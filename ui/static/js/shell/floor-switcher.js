/**
 * BossMod AI — the header floor switcher.
 *
 * One compact control in the empty band: this floor, another floor, or
 * all floors to browse. Browse finds people. Assign, hire, and wake
 * stay fail-closed on the target thread's floor.
 */
const BossModFloorSwitcher = (() => {
    const { h, clear } = BossModDom;

    /**
     * @param {object} deps
     * @param {object} deps.store
     * @param {Function} deps.apiFetch
     * @returns {{element: HTMLElement, destroy: () => void}}
     */
    function mount({ store, apiFetch }) {
        const thisFloorBtn = h('button', {
            class: 'floor-switcher-item',
            type: 'button',
            'data-floor-scope': 'this',
            onclick: () => store.setState({ floorScope: 'this' }),
        });
        const otherFloorBtn = h('button', {
            class: 'floor-switcher-item',
            type: 'button',
            'data-floor-scope': 'other',
            'aria-haspopup': 'menu',
            'aria-expanded': 'false',
            onclick: openOtherFloors,
        });
        const allFloorsBtn = h('button', {
            class: 'floor-switcher-item',
            type: 'button',
            'data-floor-scope': 'all',
            onclick: () => store.setState({ floorScope: 'all' }),
        }, 'All floors');
        const element = h('div', {
            class: 'floor-switcher',
            role: 'group',
            'aria-label': 'Floor',
        },
            thisFloorBtn,
            h('span', { class: 'floor-switcher-dot', 'aria-hidden': 'true' }, '·'),
            otherFloorBtn,
            h('span', { class: 'floor-switcher-dot', 'aria-hidden': 'true' }, '·'),
            allFloorsBtn);

        let otherMenu = null;

        function floorState(state) {
            const floors = Array.isArray(state.floors) && state.floors.length
                ? state.floors
                : [{ id: 'lobby', name: 'Lobby' }];
            const scope = state.floorScope === 'other' || state.floorScope === 'all'
                ? state.floorScope
                : 'this';
            return {
                floors,
                scope,
                currentFloorId: state.currentFloorId || 'lobby',
                browseFloorId: state.browseFloorId || null,
            };
        }

        function floorLabel(floors, floorId) {
            const found = floors.find((floor) => floor && floor.id === floorId);
            return (found && found.name) || (floorId === 'lobby' ? 'Lobby' : 'This floor');
        }

        function paint(state) {
            const view = floorState(state);
            const currentName = floorLabel(view.floors, view.currentFloorId);
            const otherName = view.browseFloorId
                ? floorLabel(view.floors, view.browseFloorId)
                : 'Other floor';
            clear(thisFloorBtn);
            thisFloorBtn.append(currentName);
            thisFloorBtn.setAttribute('aria-label', `This floor, ${currentName}`);
            thisFloorBtn.setAttribute('aria-pressed', view.scope === 'this' ? 'true' : 'false');
            clear(otherFloorBtn);
            otherFloorBtn.append(view.scope === 'other' ? otherName : 'Other floor');
            otherFloorBtn.setAttribute(
                'aria-label',
                view.browseFloorId ? `Other floor, ${otherName}` : 'Other floor',
            );
            otherFloorBtn.setAttribute('aria-pressed', view.scope === 'other' ? 'true' : 'false');
            allFloorsBtn.setAttribute('aria-label', 'All floors, browse');
            allFloorsBtn.setAttribute('aria-pressed', view.scope === 'all' ? 'true' : 'false');
        }

        function closeOtherMenu() {
            if (!otherMenu) return;
            otherMenu.close();
            otherMenu = null;
            otherFloorBtn.setAttribute('aria-expanded', 'false');
        }

        function openOtherFloors() {
            if (otherMenu) {
                closeOtherMenu();
                return;
            }
            const view = floorState(store.getState());
            const choices = view.floors.filter((floor) => floor && floor.id !== view.currentFloorId);
            const items = choices.map((floor) => h('button', {
                class: 'menu-action',
                type: 'button',
                onclick: () => {
                    store.setState({ floorScope: 'other', browseFloorId: floor.id });
                    closeOtherMenu();
                },
            }, floor.name));
            items.push(h('button', {
                class: 'menu-action',
                type: 'button',
                onclick: () => {
                    closeOtherMenu();
                    askNewFloor();
                },
            }, 'New floor'));
            otherFloorBtn.setAttribute('aria-expanded', 'true');
            otherMenu = BossModOverlays.createMenu({
                anchor: otherFloorBtn,
                label: 'Other floor',
                items,
                container: document.body,
                onClose: () => {
                    otherMenu = null;
                    otherFloorBtn.setAttribute('aria-expanded', 'false');
                },
            });
        }

        function askNewFloor() {
            const input = h('input', {
                class: 'field-input',
                type: 'text',
                placeholder: 'Finance',
                'aria-label': 'Floor name',
            });
            BossModOverlays.createModal({
                title: 'New floor',
                closeOnBackdrop: true,
                body: input,
                actions: [
                    {
                        label: 'Create floor',
                        onSelect: () => { void createFloor(input.value); },
                    },
                    { label: 'Cancel', tone: 'quiet' },
                ],
            });
        }

        async function createFloor(name) {
            const label = String(name || '').trim();
            if (!label) return;
            let response;
            try {
                response = await apiFetch('/api/floors', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: label }),
                });
            } catch (err) {
                console.error('[floor-switcher] could not create a floor', err);
                return;
            }
            if (!response.ok) return;
            const created = await response.json();
            await loadFloors();
            if (created && created.id) {
                store.setState({ floorScope: 'other', browseFloorId: created.id });
            }
        }

        async function loadFloors() {
            let response;
            try {
                response = await apiFetch('/api/floors');
            } catch (err) {
                console.error('[floor-switcher] could not load floors', err);
                return;
            }
            if (!response.ok) return;
            const rows = await response.json();
            if (!Array.isArray(rows) || rows.length === 0) return;
            const ids = new Set(rows.map((floor) => floor && floor.id));
            const state = store.getState();
            const patch = { floors: rows };
            if (state.currentFloorId && !ids.has(state.currentFloorId)) {
                patch.currentFloorId = 'lobby';
            }
            if (state.browseFloorId && !ids.has(state.browseFloorId)) {
                patch.browseFloorId = null;
                if (state.floorScope === 'other') patch.floorScope = 'this';
            }
            store.setState(patch);
        }

        paint(store.getState());
        void loadFloors();
        const off = store.subscribe(
            (s) => [
                s.floorScope || 'this',
                s.currentFloorId || 'lobby',
                s.browseFloorId || '',
                (s.floors || []).map((floor) => `${floor.id}:${floor.name}`).join(','),
            ].join('|'),
            () => paint(store.getState()),
        );

        return {
            element,
            destroy() {
                off();
                closeOtherMenu();
            },
        };
    }

    return { mount };
})();
