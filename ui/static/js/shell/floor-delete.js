/**
 * BossMod AI — the Delete floor layer.
 *
 * Opened from the floor settings' `Delete floor…` (shell/floor-settings.js)
 * as a LAYER over it (core/overlays.js). It says what will happen: the
 * floor's active threads are archived, its files move to Company › Archived
 * floors, and when agents live there the operator must choose, with no
 * default, whether they go on vacation or are deleted. Lobby is never
 * offered this layer.
 *
 * Requests go through shell/floor-api.js.
 */
const BossModFloorDelete = (() => {
    const { h } = BossModDom;
    const CONFIRM_ID = 'floor-delete-confirm';
    const OCCUPANTS_NAME = 'floor-delete-occupants';

    /**
     * @param {number} count
     * @param {string} one
     * @param {string} many
     * @returns {string}
     */
    function plural(count, one, many) {
        return `${count} ${count === 1 ? one : many}`;
    }

    /**
     * Agents whose home is this floor. Vacationers are not in the roster.
     *
     * @param {object} state
     * @param {string} floorId
     * @returns {object[]}
     */
    function agentsOn(state, floorId) {
        return (state.roster || []).filter((agent) => BossModFloorScope.floorOf(agent) === floorId);
    }

    /**
     * Open the Delete layer for one floor.
     *
     * @param {object} deps
     * @param {object} deps.store  Reads `roster` for the agent count.
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @param {{id: string, name: string, has_folder?: boolean}} deps.floor
     * @param {number|null} deps.threadCount  Active threads on the floor, as
     *   the settings view counted them from the server; null when it could not.
     * @param {() => Promise<boolean>} deps.reloadFloors  The switcher's loader.
     * @param {() => void} deps.onDeleted  Called after this layer closes on
     *   success, so the settings view beneath can close too.
     * @returns {{close: () => void}}
     * @throws {Error} When the floor is Lobby, which is never deleted.
     */
    function open({ store, floorApi, floor, threadCount, reloadFloors, onDeleted }) {
        if (floor.id === BossModFloorScope.LOBBY_ID) throw new Error('[floor-delete] Lobby cannot be deleted');
        const agents = agentsOn(store.getState(), floor.id).length;
        // null: the settings view could not count them. Say what happens
        // without a number rather than claim there are none.
        const threads = threadCount;
        let threadClause = '';
        if (threads === null) threadClause = ' Its active threads will be archived.';
        else if (threads) threadClause = ` Its ${plural(threads, 'thread', 'threads')} will be archived.`;
        const layerError = h('p', { class: 'context-error', role: 'alert' });
        let occupants = null;

        const body = h('div', { class: 'floor-delete' });
        if (agents === 0) {
            let summary = `${floor.name} has no agents and no active threads.`;
            if (threads === null) summary = `${floor.name} has no agents. Its active threads will be archived.`;
            else if (threads) summary = `The floor's ${plural(threads, 'thread', 'threads')} will be archived.`;
            body.append(h('p', {}, summary));
        } else {
            const option = (value, label, hint) => {
                const radio = h('input', {
                    type: 'radio',
                    name: OCCUPANTS_NAME,
                    value,
                    onchange: () => {
                        occupants = value;
                        syncConfirm();
                    },
                });
                return h('label', { class: 'floor-delete-option' },
                    radio,
                    h('span', {},
                        h('span', { class: 'floor-delete-option-label' }, label),
                        h('span', { class: 'field-hint' }, hint)));
            };
            body.append(
                h('p', {}, `${plural(agents, 'agent works', 'agents work')} on ${floor.name}.${threadClause}`),
                h('fieldset', { class: 'floor-delete-choice' },
                    h('legend', { class: 'field-label' }, 'What happens to the agents?'),
                    option('send_home', 'Send them home',
                        'They go on vacation: inactive and off every floor. '
                        + 'Bring them back from People ⋯ → On vacation.'),
                    option('delete', 'Delete them', 'Permanently removes the agents.')),
            );
        }
        // `has_folder` comes with the floor list (floor-api.js). The folder is never
        // deleted: the server moves it into the company archive.
        if (floor.has_folder === true) {
            body.append(h('p', {}, 'Its files move to Company › Archived floors.'));
        }
        body.append(layerError);

        let busy = false;
        const layer = BossModOverlays.createModal({
            title: `Delete ${floor.name}`,
            body,
            actions: deleteActions(false),
            closeOnBackdrop: false,
        });
        syncConfirm();

        function deleteActions(working) {
            return [
                {
                    label: working ? 'Deleting…' : 'Delete floor',
                    tone: 'danger',
                    id: CONFIRM_ID,
                    keepOpen: true,
                    onSelect: () => { void confirm(); },
                },
                { label: 'Cancel', tone: 'quiet' },
            ];
        }

        /** Disabled until a choice is made when agents live here, and while working. */
        function syncConfirm() {
            const button = layer.element.querySelector(`#${CONFIRM_ID}`);
            if (!button) throw new Error('[floor-delete] the Delete floor action did not render');
            button.disabled = busy || (agents > 0 && occupants === null);
        }

        async function confirm() {
            if (busy || (agents > 0 && occupants === null)) return;
            busy = true;
            layerError.textContent = '';
            layer.setActions(deleteActions(true));
            syncConfirm();
            try {
                await floorApi.deleteFloor(floor.id, agents > 0 ? occupants : null);
            } catch (err) {
                console.error('[floor-delete] could not delete the floor', err);
                // The roster this layer counted from was behind the server.
                layerError.textContent = err.code === 'occupants_choice_required'
                    ? `${err.message} Close this and open Delete floor again.`
                    : (err.message || 'The floor could not be deleted.');
                busy = false;
                layer.setActions(deleteActions(false));
                syncConfirm();
                return;
            }
            const loaded = await reloadFloors();
            layer.close();
            onDeleted();
            if (!loaded) {
                // The floor is gone either way; the switcher already shows
                // that its list could not reload.
                console.error('[floor-delete] the floor was deleted but the list did not reload');
            }
        }

        return { close: () => layer.close() };
    }

    return { open };
})();
