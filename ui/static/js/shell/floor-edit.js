/**
 * BossMod AI — the Edit floor modal, and the Delete layer it opens.
 *
 * Opened from a floor row's `⋯` in the header switcher. Rename is a form in
 * the modal; `Delete floor…` opens a LAYER over it (core/overlays.js) that
 * says what will happen: the floor's active threads are archived, and when
 * agents live there the operator must choose, with no default, whether they
 * go on vacation or are deleted. Lobby can be renamed and never deleted.
 *
 * Split from shell/floor-switcher.js: the switcher picks a floor, this edits
 * one. Requests go through shell/floor-api.js.
 */
const BossModFloorEdit = (() => {
    const { h } = BossModDom;
    const LOBBY_ID = 'lobby';
    const FORM_ID = 'floor-edit-form';
    const NAME_ID = 'floor-edit-name';
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
     * Active threads the rail knows on this floor. `state.threads` is the
     * rail's current list, so while it shows archives this counts none.
     *
     * @param {object} state
     * @param {string} floorId
     * @returns {object[]}
     */
    function activeThreadsOn(state, floorId) {
        return (state.threads || []).filter((thread) => thread.status === 'active'
            && BossModFloorScope.floorOf(thread) === floorId);
    }

    /**
     * Open the Edit floor modal for one floor.
     *
     * @param {object} deps
     * @param {object} deps.store  Reads `floors`, `roster`, `threads`.
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @param {string} deps.floorId
     * @param {() => Promise<boolean>} deps.reloadFloors  The switcher's
     *   loader: refreshes `floors` and moves the operator to Lobby when the
     *   floor they were on is gone.
     * @returns {{close: () => void}}
     * @throws {Error} When the floor is not in `state.floors` — an edit form
     *   for a floor nobody can see would rename the wrong thing.
     */
    function open({ store, floorApi, floorId, reloadFloors }) {
        const floor = (store.getState().floors || []).find((item) => item.id === floorId);
        if (!floor) throw new Error(`[floor-edit] floor "${floorId}" is not loaded`);
        const isLobby = floor.id === LOBBY_ID;

        const input = h('input', {
            class: 'field-input',
            id: NAME_ID,
            type: 'text',
            maxlength: '80',
            autocomplete: 'off',
        });
        input.value = floor.name;
        const error = h('p', { class: 'context-error', role: 'alert' });
        const form = h('form', {
            class: 'field',
            id: FORM_ID,
            onsubmit: (event) => {
                event.preventDefault();
                void save();
            },
        },
            h('label', { class: 'field-label', for: NAME_ID }, 'Name'),
            input,
            isLobby
                ? h('p', { class: 'field-hint' }, "Lobby is the default floor and can't be deleted.")
                : null,
            error);

        const saveAction = { label: 'Save', tone: 'primary', form: FORM_ID };
        const actions = [
            isLobby ? null : {
                label: 'Delete floor…', keepOpen: true, onSelect: () => openDelete(),
            },
            { label: 'Cancel', tone: 'quiet' },
            saveAction,
        ].filter(Boolean);

        const modal = BossModOverlays.createModal({
            title: 'Edit floor',
            body: form,
            actions,
            closeOnBackdrop: false,
        });

        async function save() {
            const name = String(input.value || '').trim();
            if (!name) {
                error.textContent = 'Give the floor a name.';
                return;
            }
            error.textContent = '';
            input.disabled = true;
            try {
                await floorApi.renameFloor(floor.id, name);
            } catch (err) {
                console.error('[floor-edit] could not rename the floor', err);
                error.textContent = err.status === 409
                    ? 'Another floor already uses that name.'
                    : (err.message || 'The floor could not be renamed.');
                input.disabled = false;
                input.focus();
                return;
            }
            const loaded = await reloadFloors();
            if (!loaded) {
                input.disabled = false;
                error.textContent = 'Renamed, but the floor list could not reload.';
                return;
            }
            modal.close();
        }

        /** The Delete layer. It closes both layers on success. */
        function openDelete() {
            const state = store.getState();
            const agents = agentsOn(state, floor.id).length;
            const threads = activeThreadsOn(state, floor.id).length;
            const threadClause = threads
                ? ` Its ${plural(threads, 'thread', 'threads')} will be archived.`
                : '';
            const layerError = h('p', { class: 'context-error', role: 'alert' });
            let occupants = null;

            const body = h('div', { class: 'floor-delete' });
            if (agents === 0) {
                body.append(h('p', {}, threads
                    ? `The floor's ${plural(threads, 'thread', 'threads')} will be archived.`
                    : `${floor.name} has no agents and no active threads.`));
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
                if (!button) throw new Error('[floor-edit] the Delete floor action did not render');
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
                    console.error('[floor-edit] could not delete the floor', err);
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
                modal.close();
                if (!loaded) {
                    // The floor is gone either way; the switcher already shows
                    // that its list could not reload.
                    console.error('[floor-edit] the floor was deleted but the list did not reload');
                }
            }
        }

        return { close: () => modal.close() };
    }

    return { open };
})();
