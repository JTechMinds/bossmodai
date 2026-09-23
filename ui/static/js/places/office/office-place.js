/**
 * BossMod AI — the Office place.
 *
 * The floor: a live map of who is where, an org chart of the same people, and
 * a ticker of what just happened. Clicking an agent — on the floor or in the
 * org chart — offers the doors into them (their chat, their desk) in a small
 * dialog; both are the roster's routes, so the Office hosts no conversation of
 * its own.
 *
 * It registers itself, so shell/places.js is never edited to add a real place.
 */
const BossModOfficePlace = (() => {
    const { h, clear } = BossModDom;

    const PAUSED_COPY = 'Everyone is paused';
    const TABS = Object.freeze([
        { id: 'map', label: 'Map' },
        { id: 'org', label: 'Org' },
    ]);

    let ctxRef = null;
    let canvas = null;
    let orgView = null;
    let ticker = null;
    let agentDialog = null;
    let stage = null;
    let mapPane = null;
    let orgPane = null;
    let statePill = null;
    let countsLine = null;
    /** Map | Org — core/tabs.js, which owns the tabs; the panes are this file's. */
    let tabs = null;
    const disposers = [];

    /**
     * Counts the store can actually answer. A number with no source is omitted
     * rather than shown as zero, because a zero that means "unknown" is a lie —
     * token totals need /api/metrics, which this place does not load.
     */
    function countsText(state) {
        const people = BossModFloorScope.filterPeople(state, state.roster || []).length;
        const threads = (state.threads || []).filter(
            (thread) => BossModFloorScope.floorOf(thread) === BossModFloorScope.visibleFloorId(state),
        ).length;
        return `${people} ${people === 1 ? 'person' : 'people'} · `
            + `${threads} ${threads === 1 ? 'thread' : 'threads'}`;
    }

    function paintRuntimeState(paused) {
        statePill.textContent = paused ? 'paused' : 'live';
        statePill.setAttribute('data-state', paused ? 'paused' : 'live');
        stage.classList.toggle('is-paused', Boolean(paused));
        mapPane.querySelector('.office-paused').hidden = !paused;
    }

    /**
     * Show one pane; both render into the stage. Which TAB is selected is
     * BossModTabs' to mark — this is the half only the Office knows about.
     *
     * @param {'map'|'org'} id
     * @returns {void}
     */
    function showPane(id) {
        mapPane.hidden = id !== 'map';
        orgPane.hidden = id !== 'org';
        // A canvas sized while hidden is a canvas sized to zero.
        if (id === 'map' && canvas) canvas.resize();
    }

    /**
     * Offer the doors into one agent — their chat, their desk.
     *
     * @param {string} agentId  From the floor or the org chart.
     * @returns {void}
     * @throws {Error} When the agent is not in the roster: the floor drew
     *   someone the store no longer knows, which is a bug to see, not a
     *   dialog to open on nobody.
     */
    function openAgentActions(agentId) {
        const agent = (ctxRef.store.getState().roster || []).find((item) => item.id === agentId);
        if (!agent) throw new Error(`[office] no agent "${agentId}" in the roster`);
        closeAgentActions();
        const routes = { store: ctxRef.store, navigate: ctxRef.navigate };
        agentDialog = BossModOfficeAgentActions.open({
            agent,
            onOpenChat: () => BossModAgentRoutes.openConversation(routes, agent.id, 'agent'),
            onViewDesk: () => BossModAgentRoutes.openDesk(routes, agent.id),
            onClose: () => { agentDialog = null; },
        });
    }

    function closeAgentActions() {
        if (agentDialog) agentDialog.close();
    }

    function showCanvasError(message) {
        const holder = mapPane.querySelector('.office-canvas-wrap');
        holder.append(h('div', { class: 'place-error-panel', role: 'alert' },
            h('p', { class: 'place-error-title' }, 'The floor could not be drawn'),
            h('p', { class: 'place-error-detail' }, message),
            h('button', {
                class: 'btn',
                type: 'button',
                onclick: () => { void startCanvas(); },
            }, 'Try again')));
    }

    /**
     * Load the map and paint it.
     *
     * @returns {Promise<void>} Never rejects: a failed /api/map becomes the
     *   error state with a retry, never a blank rectangle.
     */
    async function startCanvas() {
        const holder = mapPane.querySelector('.office-canvas-wrap');
        holder.querySelectorAll('.place-error-panel').forEach((node) => node.remove());
        try {
            await canvas.init();
            canvas.updateAgents(ctxRef.store.getState().roster || []);
        } catch (err) {
            console.error('[office] the floor could not be loaded', err);
            showCanvasError((err && err.message) || 'The request failed.');
        }
    }

    return {
        label: 'Office',
        icon: 'building',

        /**
         * @param {HTMLElement} el
         * @param {object} ctx  `{ store, bus, api, needs, navigate }`.
         * @returns {void}
         */
        mount(el, ctx) {
            ctxRef = ctx;
            clear(el);

            statePill = h('span', { class: 'office-state', 'data-state': 'live' }, 'live');
            countsLine = h('p', { class: 'office-counts' }, countsText(ctx.store.getState()));
            const floorEcho = h('p', { class: 'office-floor-echo' });

            const canvasEl = h('canvas', { class: 'office-canvas' });
            const canvasWrap = h('div', { class: 'office-canvas-wrap' },
                canvasEl,
                floorEcho,
                h('div', { class: 'office-paused', role: 'status', hidden: true },
                    h('p', { class: 'office-paused-copy' }, PAUSED_COPY)));
            mapPane = h('div', {
                class: 'office-pane', id: 'office-pane-map',
                role: 'tabpanel', 'aria-labelledby': 'office-tab-map',
            }, canvasWrap);

            orgPane = h('div', {
                class: 'office-pane', id: 'office-pane-org',
                role: 'tabpanel', 'aria-labelledby': 'office-tab-org', hidden: true,
            });

            stage = h('div', { class: 'office-stage' }, mapPane, orgPane);

            ticker = BossModTicker.createTicker({ bus: ctx.bus, navigate: ctx.navigate });

            tabs = BossModTabs.create({
                label: 'Office view',
                idPrefix: 'office-tab',
                tabs: TABS.map((tab) => ({ ...tab, panelId: `office-pane-${tab.id}` })),
                selected: 'map',
                onSelect: showPane,
            });

            el.append(h('div', { class: 'office-place' },
                h('header', { class: 'office-header' },
                    h('div', { class: 'office-title' },
                        h('h1', { tabindex: '-1' }, 'Office'),
                        statePill),
                    countsLine,
                    tabs.element),
                stage,
                ticker.element));

            canvas = BossModOfficeCanvas.createOfficeCanvas({
                canvas: canvasEl,
                container: canvasWrap,
                api: ctx.api,
                onAgentClick: openAgentActions,
            });
            orgView = BossModOrgView.createOrgView({
                api: ctx.api,
                store: ctx.store,
                onAgentClick: openAgentActions,
            });
            orgPane.append(orgView.element);

            function paintFloor() {
                const state = ctx.store.getState();
                const seated = BossModFloorScope.filterPeople(state, state.roster || []);
                canvas.updateAgents(seated);
                floorEcho.textContent = BossModFloorScope.floorName(
                    state,
                    BossModFloorScope.visibleFloorId(state),
                );
                countsLine.textContent = countsText(state);
            }

            showPane('map');
            paintRuntimeState(ctx.store.getState().runtimePaused);
            paintFloor();

            disposers.push(ctx.store.subscribe((s) => s.roster, paintFloor));
            disposers.push(ctx.store.subscribe((s) => s.threads, paintFloor));
            disposers.push(ctx.store.subscribe(
                (s) => s.currentFloorId,
                paintFloor,
            ));
            disposers.push(ctx.store.subscribe((s) => s.runtimePaused, paintRuntimeState));
            disposers.push(ctx.bus.subscribe('activity', (entry) => canvas.handleActivity(entry)));
            disposers.push(ctx.bus.subscribe('agent_thought', (data) => {
                if (data && data.agent_id) canvas.showThought(data.agent_id, data.thought);
            }));
            // The shell does not drive Place.resync() yet, and a place that
            // waits to be asked would sit stale after every outage. Subscribing
            // here makes the hook live and drains it with the place.
            disposers.push(ctx.bus.subscribe('resync', () => BossModOfficePlace.resync()));

            void startCanvas();
            // The canvas was built inside a container the browser had not laid
            // out yet, so it measured zero. One resize on entry sizes it once.
            window.dispatchEvent(new Event('panel-resize'));
        },

        /**
         * Re-fetch after a WebSocket outage without remounting (spec 1.4), so
         * the selected tab survives. Reached both from the `resync` topic and,
         * when the shell grows the call, from the place contract.
         * @returns {void}
         */
        resync() {
            // Public, so it can arrive after unmount; there is nothing to
            // refresh then, and touching the torn-down nodes would throw.
            if (!canvas) return;
            void startCanvas();
            if (orgView) void orgView.refresh();
        },

        /**
         * Drain every subscription and destroy every child controller.
         * @returns {void}
         */
        unmount() {
            disposers.splice(0).forEach((off) => off());
            closeAgentActions();
            if (canvas) canvas.destroy();
            if (orgView) orgView.destroy();
            if (ticker) ticker.destroy();
            canvas = null;
            orgView = null;
            ticker = null;
            stage = null;
            mapPane = null;
            orgPane = null;
            statePill = null;
            countsLine = null;
            tabs = null;
            ctxRef = null;
        },
    };
})();

BossModPlaces.register('office', BossModOfficePlace);
