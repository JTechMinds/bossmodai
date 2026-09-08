/**
 * BossMod AI — the two controls that make the narrow layouts reachable.
 *
 * Spec 10. The layout itself is CSS: three breakpoints in shell.css decide
 * which columns are in the grid. What CSS cannot do is give the operator a way
 * back to a column it has removed, and that is all this module is — a ☰ button
 * for the roster below 768px and a Desk button for the context column below
 * 1200px.
 *
 * Both open the column that is already mounted, by MOVING it into
 * core/overlays.js's slide-over and putting it back on close. Rebuilding it
 * would throw away whatever the operator had open — the desk folder they had
 * navigated to, the roster search they had typed — and would need a second
 * copy of every view. There is one roster and one context column at every
 * width; only where they sit changes.
 *
 * The slide-over brings the focus trap, Esc, and focus restoration with it, so
 * neither of these is a third overlay implementation. Neither is gesture-only
 * either (SC 2.1.1): the opener is a real button, reachable by keyboard, by
 * switch control, and by a pointer that cannot make a gesture.
 */
const BossModResponsive = (() => {
    const { h } = BossModDom;

    /** Below this the roster leaves the grid and becomes a drawer. */
    const NARROW = '(max-width: 767px)';
    /** Below this the context column leaves the grid and becomes an overlay. */
    const MEDIUM = '(max-width: 1199px)';

    /**
     * Wire the drawer and the context overlay into the header.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.headerEl   The mounted header; the two buttons
     *   are inserted into it rather than owned by it, because they exist for a
     *   layout concern the header knows nothing about.
     * @param {HTMLElement} deps.layoutEl   #main-layout, where both columns live.
     * @param {HTMLElement} deps.rosterEl   #app-roster.
     * @param {HTMLElement} deps.placeEl    #app-place, the anchor both columns
     *   are restored around.
     * @param {HTMLElement} deps.contextEl  #app-context.
     * @param {object} deps.store           Application store; the Desk button
     *   is offered only where a context column exists (Chat).
     * @returns {() => void} disposer — closes anything open, restores both
     *   columns to the grid, and drains every listener.
     * @throws {Error} When any dependency is missing. A drawer button with no
     *   drawer behind it is worse than no button.
     */
    function mount(deps) {
        const { headerEl, layoutEl, rosterEl, placeEl, contextEl, store } = deps || {};
        for (const [name, value] of Object.entries({
            headerEl, layoutEl, rosterEl, placeEl, contextEl, store,
        })) {
            if (!value) throw new Error(`[responsive] deps.${name} is required`);
        }

        const disposers = [];
        /** The one open panel, or null. Only ever one: both are full-height. */
        let open = null;

        /**
         * Put a column back where the grid expects it.
         *
         * The roster is the first child and the context the last, either side
         * of the place. Appending blindly would leave the roster to the right
         * of the centre the next time the viewport widened.
         *
         * @param {HTMLElement} column
         * @returns {void}
         */
        function restore(column) {
            if (column === rosterEl) layoutEl.insertBefore(column, placeEl);
            else layoutEl.append(column);
        }

        function closeOpen() {
            if (!open) return;
            const panel = open;
            open = null;
            panel.close();
        }

        /**
         * Move one column into a slide-over.
         *
         * @param {HTMLElement} column
         * @param {string} title  The dialog's accessible name.
         * @returns {void}
         */
        function present(column, title) {
            closeOpen();
            const panel = BossModOverlays.slideOver({
                title,
                body: column,
                onClose: () => {
                    open = null;
                    restore(column);
                },
            });
            panel.element.classList.add('responsive-panel');
            open = panel;
        }

        const menuButton = h('button', {
            class: 'responsive-menu-btn',
            type: 'button',
            'aria-label': 'Open people and threads',
            onclick: () => present(rosterEl, 'People and threads'),
        }, h('i', { 'data-lucide': 'menu', 'aria-hidden': 'true' }));

        const deskButton = h('button', {
            class: 'responsive-desk-btn',
            type: 'button',
            'aria-label': 'Open the context column',
            onclick: () => present(contextEl, 'Context'),
        }, h('i', { 'data-lucide': 'panel-right', 'aria-hidden': 'true' }),
            h('span', { class: 'responsive-btn-label' }, 'Desk'));

        headerEl.prepend(menuButton);
        headerEl.append(deskButton);
        if (window.lucide) lucide.createIcons({ nodes: [headerEl] });

        // The Desk button is offered on Chat alone, because Chat is the only
        // place with a context column to open (spec 3.1). A button that opens
        // an empty panel is the control-that-does-nothing this project keeps
        // deleting.
        disposers.push(store.subscribe(
            (s) => s.place,
            (placeId) => {
                const place = BossModPlaces.get(placeId);
                deskButton.classList.toggle('is-available', place.hasContext === true);
                if (place.hasContext !== true && open) closeOpen();
            }));
        const startPlace = BossModPlaces.get(store.getState().place);
        deskButton.classList.toggle('is-available', startPlace.hasContext === true);

        // Widening the window puts the column back in the grid. Leaving the
        // slide-over up over a layout that already has room for it would show
        // the same column twice.
        const watchers = [
            [window.matchMedia(NARROW), rosterEl],
            [window.matchMedia(MEDIUM), contextEl],
        ];
        watchers.forEach(([query, column]) => {
            const onChange = (event) => {
                if (!event.matches && open && open.body === column) closeOpen();
            };
            query.addEventListener('change', onChange);
            disposers.push(() => query.removeEventListener('change', onChange));
        });

        return function destroy() {
            closeOpen();
            disposers.splice(0).forEach((off) => off());
            menuButton.remove();
            deskButton.remove();
        };
    }

    return { mount, NARROW, MEDIUM };
})();
