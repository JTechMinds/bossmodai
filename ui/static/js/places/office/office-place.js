/**
 * BossMod AI — the Office place.
 *
 * The floor: a live map of who is where, an org chart of the same people, and
 * a ticker of what just happened. Clicking a desk opens that agent's
 * conversation in a slide-over — through BossModConversation, the one
 * conversation renderer, because a second one is exactly what Phase 2A existed
 * to remove.
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
    let deskPanel = null;
    let deskConversation = null;
    let stage = null;
    let mapPane = null;
    let orgPane = null;
    let statePill = null;
    let countsLine = null;
    let tabButtons = new Map();
    let activeTab = 'map';
    const disposers = [];

    /**
     * Counts the store can actually answer. A number with no source is omitted
     * rather than shown as zero, because a zero that means "unknown" is a lie —
     * token totals need /api/metrics, which this place does not load.
     */
    function countsText(state) {
        const people = (state.roster || []).length;
        const threads = (state.threads || []).length;
        return `${people} ${people === 1 ? 'person' : 'people'} · `
            + `${threads} ${threads === 1 ? 'thread' : 'threads'}`;
    }

    function paintRuntimeState(paused) {
        statePill.textContent = paused ? 'paused' : 'live';
        statePill.setAttribute('data-state', paused ? 'paused' : 'live');
        stage.classList.toggle('is-paused', Boolean(paused));
        mapPane.querySelector('.office-paused').hidden = !paused;
    }

    /** Show one pane and mark its tab selected; both render into the stage. */
    function selectTab(id) {
        activeTab = id;
        for (const [tabId, button] of tabButtons) {
            const on = tabId === id;
            button.setAttribute('aria-selected', on ? 'true' : 'false');
            // Roving tabindex: one tab stop for the group, arrows move within.
            button.setAttribute('tabindex', on ? '0' : '-1');
        }
        mapPane.hidden = id !== 'map';
        orgPane.hidden = id !== 'org';
        // A canvas sized while hidden is a canvas sized to zero.
        if (id === 'map' && canvas) canvas.resize();
    }

    function onTabKeydown(event) {
        const order = TABS.map((tab) => tab.id);
        const at = order.indexOf(activeTab);
        let next = null;
        if (event.key === 'ArrowRight') next = order[(at + 1) % order.length];
        if (event.key === 'ArrowLeft') next = order[(at - 1 + order.length) % order.length];
        if (event.key === 'Home') next = order[0];
        if (event.key === 'End') next = order[order.length - 1];
        if (!next) return;
        event.preventDefault();
        selectTab(next);
        tabButtons.get(next).focus();
    }

    function buildTabs() {
        const list = h('div', { class: 'office-tabs', role: 'tablist', 'aria-label': 'Office view' });
        tabButtons = new Map();
        TABS.forEach((tab) => {
            const button = h('button', {
                class: 'office-tab',
                type: 'button',
                role: 'tab',
                id: `office-tab-${tab.id}`,
                'aria-controls': `office-pane-${tab.id}`,
                onclick: () => selectTab(tab.id),
                onkeydown: onTabKeydown,
            }, tab.label);
            tabButtons.set(tab.id, button);
            list.append(button);
        });
        return list;
    }

    /** Open one agent's conversation beside the floor. */
    function openDesk(agentId) {
        const agent = (ctxRef.store.getState().roster || []).find((item) => item.id === agentId);
        closeDesk();
        deskConversation = BossModConversation.createConversation({
            store: ctxRef.store,
            bus: ctxRef.bus,
            api: ctxRef.api,
            navigate: ctxRef.navigate,
            needs: ctxRef.needs,
        });
        deskPanel = BossModOverlays.slideOver({
            title: agent ? agent.name : 'Desk',
            body: deskConversation.element,
            onClose: () => {
                if (deskConversation) deskConversation.destroy();
                deskConversation = null;
                deskPanel = null;
            },
        });
        void deskConversation.open(agentId, 'agent');
    }

    function closeDesk() {
        if (deskPanel) deskPanel.close();
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
            activeTab = 'map';
            clear(el);

            statePill = h('span', { class: 'office-state', 'data-state': 'live' }, 'live');
            countsLine = h('p', { class: 'office-counts' }, countsText(ctx.store.getState()));

            const canvasEl = h('canvas', { class: 'office-canvas' });
            const canvasWrap = h('div', { class: 'office-canvas-wrap' },
                canvasEl,
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

            el.append(h('div', { class: 'office-place' },
                h('header', { class: 'office-header' },
                    h('div', { class: 'office-title' },
                        h('h1', { tabindex: '-1' }, 'Office'),
                        statePill),
                    countsLine,
                    buildTabs()),
                stage,
                ticker.element));

            canvas = BossModOfficeCanvas.createOfficeCanvas({
                canvas: canvasEl,
                container: canvasWrap,
                api: ctx.api,
                onAgentClick: openDesk,
            });
            orgView = BossModOrgView.createOrgView({
                api: ctx.api,
                store: ctx.store,
                onAgentClick: openDesk,
            });
            orgPane.append(orgView.element);

            selectTab('map');
            paintRuntimeState(ctx.store.getState().runtimePaused);

            disposers.push(ctx.store.subscribe((s) => s.roster, (roster) => {
                canvas.updateAgents(roster || []);
                countsLine.textContent = countsText(ctx.store.getState());
            }));
            disposers.push(ctx.store.subscribe((s) => s.threads, () => {
                countsLine.textContent = countsText(ctx.store.getState());
            }));
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
         * an open desk conversation and the selected tab both survive. Reached
         * both from the `resync` topic and, when the shell grows the call, from
         * the place contract.
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
            closeDesk();
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
            tabButtons = new Map();
            ctxRef = null;
        },
    };
})();

BossModPlaces.register('office', BossModOfficePlace);
