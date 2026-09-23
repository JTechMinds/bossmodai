/**
 * BossMod AI — move one agent's home floor.
 *
 * An explicit operator action, separate from the role form's PATCH.
 * Open work on the old floor must be confirmed. The engine releases
 * membership on other floors and leaves tasks, soft-block, and sticky
 * rows in place.
 */
const BossModAgentFloor = (() => {
    const { h } = BossModDom;

    /**
     * Append the home-floor control under an existing agent's form.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.container
     * @param {object} deps.agent
     * @param {object} deps.store
     * @returns {void}
     */
    function mount({ container, agent, store }) {
        if (!container || !agent || !agent.id || !store) {
            throw new Error('[agent-floor] container, agent, and store are required');
        }
        const select = h('select', {
            class: 'field-input',
            'aria-label': 'Home floor',
        });
        const note = h('p', { class: 'field-hint' });

        function currentId() {
            return String(agent.floorId || agent.floor_id || 'lobby');
        }

        function paintOptions() {
            const state = store && typeof store.getState === 'function' ? store.getState() : {};
            const floors = Array.isArray(state.floors) && state.floors.length
                ? state.floors
                : [{ id: 'lobby', name: 'Lobby' }];
            const current = currentId();
            select.replaceChildren();
            floors.forEach((floor) => {
                if (!floor || !floor.id) return;
                select.append(h('option', {
                    value: floor.id,
                    selected: floor.id === current,
                }, floor.name || floor.id));
            });
        }

        async function move(confirm) {
            const floorId = String(select.value || '').trim();
            if (!floorId || floorId === currentId()) {
                note.textContent = 'This agent already lives on that floor.';
                return;
            }
            note.textContent = 'Moving home floor…';
            try {
                const saved = await BossModAgentApi.apiMoveHomeFloor(agent.id, floorId, confirm);
                agent.floorId = saved.floor_id || floorId;
                agent.floor_id = agent.floorId;
                paintOptions();
                note.textContent = 'Home floor updated.';
            } catch (err) {
                if (err && err.code === 'confirm_open_work' && !confirm) {
                    note.textContent = '';
                    BossModOverlays.createModal({
                        title: 'Move home floor',
                        body: h('p', {}, err.message || 'This agent has open work on the current floor.'),
                        actions: [
                            {
                                label: 'Move anyway',
                                onSelect: () => { void move(true); },
                            },
                            { label: 'Cancel', tone: 'quiet' },
                        ],
                    });
                    return;
                }
                note.textContent = (err && err.message) || 'Could not move the home floor.';
            }
        }

        paintOptions();
        container.append(h('section', { class: 'agent-home-floor' },
            h('h2', {}, 'Home floor'),
            h('p', {}, 'Moving home is separate from saving the role. Open work on the current floor must be confirmed.'),
            select,
            h('button', {
                class: 'btn',
                type: 'button',
                onclick: () => { void move(false); },
            }, 'Move home floor'),
            note));
    }

    return { mount };
})();
