/**
 * BossMod AI — the Suspend floor layer.
 *
 * Opened from the floor settings' Suspend tool (shell/floor-settings.js) as a
 * LAYER over it (core/overlays.js): the "are you sure" step, shaped like the
 * Delete layer (shell/floor-delete.js). It says what stops: everyone on the
 * floor, their work in progress, their open tasks, and their desktops and
 * browsers. The floor comes back from the switcher's Suspended floors door.
 * Lobby is never offered this layer.
 *
 * On success it reloads the floor list, as the Delete layer does, then closes:
 * the reload takes the floor off this window's switcher (and moves it to Lobby
 * when it was on it) even with the socket down, and the server's
 * `floors_updated` broadcast does the same for every other window.
 *
 * Requests go through shell/floor-api.js.
 */
const BossModFloorSuspend = (() => {
    const { h } = BossModDom;
    const CONFIRM_ID = 'floor-suspend-confirm';
    const CANCEL_ID = 'floor-suspend-cancel';

    /**
     * Open the Suspend layer for one floor.
     *
     * @param {object} deps
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @param {{id: string, name: string}} deps.floor
     * @param {() => Promise<boolean>} deps.reloadFloors  The switcher's loader;
     *   false when the list did not reload (the switcher shows that itself).
     * @param {() => void} deps.onSuspended  Called after this layer closes on
     *   success, so the settings view beneath can close too.
     * @returns {{close: () => void}}
     * @throws {Error} When the floor is Lobby, which is never suspended, or
     *   when `reloadFloors` is missing.
     */
    function open({ floorApi, floor, reloadFloors, onSuspended }) {
        if (floor.id === BossModFloorScope.LOBBY_ID) throw new Error('[floor-suspend] Lobby cannot be suspended');
        if (typeof reloadFloors !== 'function') throw new Error('[floor-suspend] deps.reloadFloors is required');
        const layerError = h('p', { class: 'context-error', role: 'alert' });
        const body = h('div', { class: 'floor-suspend' },
            h('p', {}, `Everyone on ${floor.name} stops now: work in progress ends, open tasks are `
                + 'cancelled, and their desktops and browsers shut down. '
                + 'You can resume the floor later from Suspended floors.'),
            layerError);

        let busy = false;
        const layer = BossModOverlays.createModal({
            // A question: this layer is the "are you sure" step.
            title: `Suspend ${floor.name}?`,
            body,
            actions: suspendActions(false),
            closeOnBackdrop: false,
        });

        function suspendActions(working) {
            return [
                {
                    label: working ? 'Suspending…' : 'Suspend floor',
                    tone: 'danger',
                    id: CONFIRM_ID,
                    keepOpen: true,
                    onSelect: () => { void confirm(); },
                },
                { label: 'Cancel', tone: 'quiet', id: CANCEL_ID },
            ];
        }

        /** Both buttons wait while the request runs: Cancel cannot un-send it. */
        function paintActions(working) {
            layer.setActions(suspendActions(working));
            [CONFIRM_ID, CANCEL_ID].forEach((id) => {
                const button = layer.element.querySelector(`#${id}`);
                if (!button) throw new Error(`[floor-suspend] the #${id} action did not render`);
                button.disabled = working;
            });
        }

        async function confirm() {
            if (busy) return;
            busy = true;
            layerError.textContent = '';
            paintActions(true);
            try {
                await floorApi.suspendFloor(floor.id);
            } catch (err) {
                console.error('[floor-suspend] could not suspend the floor', err);
                layerError.textContent = err.message || 'The floor could not be suspended.';
                busy = false;
                paintActions(false);
                return;
            }
            const loaded = await reloadFloors();
            layer.close();
            onSuspended();
            if (!loaded) {
                // The floor is suspended either way; the switcher already shows
                // that its list could not reload.
                console.error('[floor-suspend] the floor was suspended but the list did not reload');
            }
        }

        return { close: () => layer.close() };
    }

    return { open };
})();
