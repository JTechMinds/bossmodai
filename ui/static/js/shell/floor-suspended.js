/**
 * BossMod AI — the Suspended floors view.
 *
 * Suspended floors are off the header switcher, so this modal is the one place
 * they are listed. It is opened from the switcher's "Suspended floors" door
 * (shell/floor-switcher.js), and shaped like the On vacation view
 * (shell/vacation-dialog.js) with the floor settings' rows. Each row resumes
 * its floor.
 *
 * It reads `state.suspendedFloors` and follows it while open. A resume here
 * reloads the floor list, as Rename and Delete do, so the floor leaves that
 * list and its row goes with it even with the socket down; a resume in another
 * window arrives as the server's `floors_updated` broadcast, the same way. A
 * row whose resume failed keeps its error until it is tried again.
 *
 * Requests go through shell/floor-api.js.
 */
const BossModFloorSuspended = (() => {
    const { h, clear } = BossModDom;
    const EMPTY_TEXT = 'No floors are suspended.';

    /**
     * @param {object} state
     * @returns {Array<{id: string, name: string, suspended_at: string}>}
     */
    function suspendedOf(state) {
        return Array.isArray(state.suspendedFloors) ? state.suspendedFloors : [];
    }

    /**
     * What a row shows. A change in any of it rebuilds that row; anything
     * else keeps the row, and with it a busy Resume or a shown error.
     *
     * @param {{id: string, name: string, suspended_at: string}} floor
     * @returns {string}
     */
    function rowKey(floor) {
        return JSON.stringify([floor.id, floor.name, floor.suspended_at]);
    }

    /**
     * Open the Suspended floors modal.
     *
     * @param {object} deps
     * @param {object} deps.store  Reads and follows `suspendedFloors`.
     * @param {object} deps.floorApi  From BossModFloorApi.createFloorApi.
     * @param {() => Promise<boolean>} deps.reloadFloors  The switcher's loader;
     *   false when the list did not reload (the switcher shows that itself).
     * @returns {{close: () => void}}
     * @throws {Error} When store, floorApi or reloadFloors is missing.
     */
    function open(deps) {
        const { store, floorApi, reloadFloors } = deps || {};
        if (!store) throw new Error('[floor-suspended] deps.store is required');
        if (!floorApi) throw new Error('[floor-suspended] deps.floorApi is required');
        if (typeof reloadFloors !== 'function') throw new Error('[floor-suspended] deps.reloadFloors is required');

        const content = h('div', {});
        const list = h('ul', { class: 'floor-item-list' });
        const empty = h('p', { class: 'field-hint' }, EMPTY_TEXT);
        /** rowKey → the row's `<li>`, in the store's order. */
        let rows = new Map();

        /**
         * Gone rows leave and new ones join; a kept row is never detached,
         * because detaching a focused Resume would drop the keyboard's place.
         */
        function render() {
            const floors = suspendedOf(store.getState());
            const next = new Map(floors.map((floor) => [rowKey(floor), rows.get(rowKey(floor)) || row(floor)]));
            rows.forEach((item, key) => { if (!next.has(key)) item.remove(); });
            Array.from(next.values()).forEach((item, index) => {
                if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
            });
            rows = next;
            const shown = floors.length ? list : empty;
            if (content.children[0] !== shown) {
                clear(content);
                content.append(shown);
            }
        }

        function row(floor) {
            const since = BossModFormat.formatDateTime(floor.suspended_at);
            const error = h('p', { class: 'context-error', role: 'alert' });
            const resume = h('button', {
                class: 'btn btn-sm',
                type: 'button',
                'aria-label': `Resume ${floor.name}`,
                onclick: () => { void resumeFloor(); },
            }, 'Resume');

            async function resumeFloor() {
                resume.disabled = true;
                error.textContent = '';
                try {
                    await floorApi.resumeFloor(floor.id);
                } catch (err) {
                    console.error('[floor-suspended] could not resume the floor', err);
                    error.textContent = err.message || `${floor.name} could not be resumed.`;
                    resume.disabled = false;
                    return;
                }
                // Resumed: the reload removes this row through the store. Resume
                // stays disabled either way, because the floor is no longer
                // suspended; a failed reload says so without looking failed.
                if (!(await reloadFloors())) error.textContent = 'Resumed, but the floor list could not reload.';
            }

            return h('li', { class: 'floor-item' },
                h('div', { class: 'floor-item-who' },
                    h('span', { class: 'floor-item-name' }, floor.name),
                    since ? h('span', { class: 'floor-item-meta' }, `Suspended ${since}`) : null,
                    error),
                resume);
        }

        // Filled before it opens, so focus lands on the first Resume as the
        // overlay's focus rule says, not on ✕ ahead of a list drawn later.
        render();
        const modal = BossModOverlays.createModal({
            title: 'Suspended floors',
            size: 'default',
            closeOnBackdrop: true,
            body: content,
            actions: [],
            onClose: () => off(),
        });
        const off = store.subscribe(
            (s) => JSON.stringify(suspendedOf(s).map(rowKey)),
            () => render(),
        );
        return { close: () => modal.close() };
    }

    return { open };
})();
