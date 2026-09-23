/**
 * BossMod AI — the On vacation view.
 *
 * Agents on vacation are off every floor and hidden from the roster, the
 * office and Telegram, so this modal is the one place they are listed. It is
 * opened from the People header's `⋯` (shell/people-view-menu.js). Each row
 * brings its agent back to the floor the operator is on; the world broadcast
 * that follows puts them back in the roster, so this view only drops the row.
 *
 * Four states, each said in words: loading, a failed load with Retry, no one
 * away, and the list.
 */
const BossModVacationDialog = (() => {
    const { h, clear } = BossModDom;

    /**
     * Open the On vacation modal.
     *
     * @param {object} deps
     * @param {object} deps.store  Reads `currentFloorId` and `floors` for the
     *   floor a Bring back lands on.
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @returns {{close: () => void}}
     * @throws {Error} When store or floorApi is missing.
     */
    function open(deps) {
        const { store, floorApi } = deps || {};
        if (!store) throw new Error('[vacation-dialog] deps.store is required');
        if (!floorApi) throw new Error('[vacation-dialog] deps.floorApi is required');

        const content = h('div', { class: 'vacation-view' });
        const modal = BossModOverlays.createModal({
            title: 'On vacation',
            size: 'default',
            closeOnBackdrop: true,
            body: content,
            actions: [],
        });

        function say(text) {
            clear(content);
            content.append(h('p', { class: 'field-hint' }, text));
        }

        function destination() {
            const state = store.getState();
            const floorId = BossModFloorScope.visibleFloorId(state);
            return { id: floorId, name: BossModFloorScope.floorName(state, floorId) };
        }

        async function load() {
            say('Loading…');
            let rows;
            try {
                rows = await floorApi.listVacation();
            } catch (err) {
                console.error('[vacation-dialog] could not load agents on vacation', err);
                clear(content);
                content.append(
                    h('p', { class: 'context-error', role: 'alert' },
                        `Could not load who is on vacation. ${err.message || ''}`.trim()),
                    h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { void load(); } }, 'Retry'),
                );
                return;
            }
            render(rows);
        }

        function render(rows) {
            if (rows.length === 0) {
                say('No one is on vacation.');
                return;
            }
            clear(content);
            const list = h('ul', { class: 'vacation-list' });
            rows.forEach((agent) => list.append(row(agent, list)));
            content.append(list);
        }

        function row(agent, list) {
            const floor = destination();
            const since = BossModFormat.formatDateTime(agent.vacation_since);
            const error = h('p', { class: 'context-error', role: 'alert' });
            const back = h('button', {
                class: 'btn btn-sm',
                type: 'button',
                'aria-label': `Bring ${agent.name} back to ${floor.name}`,
                onclick: () => { void bringBack(); },
            }, `Bring back to ${floor.name}`);
            const item = h('li', { class: 'vacation-row' },
                BossModAvatar.create({ name: agent.name, color: agent.color || null, size: 'sm' }),
                h('div', { class: 'vacation-who' },
                    h('span', { class: 'vacation-name' }, agent.name),
                    agent.role ? h('span', { class: 'vacation-meta' }, agent.role) : null,
                    since ? h('span', { class: 'vacation-meta' }, `Since ${since}`) : null,
                    error),
                back);

            async function bringBack() {
                back.disabled = true;
                error.textContent = '';
                try {
                    await floorApi.returnFromVacation(agent.id, floor.id);
                } catch (err) {
                    console.error('[vacation-dialog] could not bring an agent back', err);
                    error.textContent = err.message || `${agent.name} could not be brought back.`;
                    back.disabled = false;
                    return;
                }
                item.remove();
                if (list.children.length === 0) say('No one is on vacation.');
            }

            return item;
        }

        void load();
        return { close: () => modal.close() };
    }

    return { open };
})();
