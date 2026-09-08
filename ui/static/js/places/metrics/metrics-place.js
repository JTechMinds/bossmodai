/**
 * BossMod AI — the Metrics place.
 *
 * company-metrics.js re-hosted on the place contract. It fetches once, paints
 * once, and refreshes after an outage; the numbers themselves are
 * metric-cards.js and metric-bars.js, which take a payload and return nodes.
 *
 * The endpoint is `GET /api/metrics/dashboard` — not `/api/metrics`, which does
 * not exist.
 *
 * The dock-era pane had two states: a spinner and the dashboard. A failed fetch
 * logged to the console and left the spinner spinning. All four are here.
 */
const BossModMetricsPlace = (() => {
    const { h, clear } = BossModDom;
    const CARDS = BossModMetricCards;
    const BARS = BossModMetricBars;

    const ENDPOINT = '/api/metrics/dashboard';
    const SKELETON_CARDS = 4;

    let ctxRef = null;
    let load = null;
    let bodyEl = null;
    let summaryEl = null;
    const disposers = [];

    function setBody(...nodes) {
        clear(bodyEl);
        bodyEl.append(...nodes);
    }

    /** The loading state: the shape of the dashboard, not a spinner. */
    function paintSkeleton() {
        summaryEl.textContent = 'Loading…';
        const cards = h('div', { class: 'metric-cards' });
        for (let i = 0; i < SKELETON_CARDS; i += 1) {
            cards.append(h('article', { class: 'metric-card is-skeleton' },
                h('span', { class: 'metric-skeleton-bar' })));
        }
        setBody(cards, h('div', { class: 'metric-panel is-skeleton' }));
    }

    function paintError(message) {
        summaryEl.textContent = '';
        setBody(h('div', { class: 'place-error-panel', role: 'alert' },
            h('p', { class: 'place-error-title' }, 'Could not load metrics'),
            h('p', { class: 'place-error-detail' }, message),
            h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } },
                'Try again')));
    }

    /**
     * True when the company has produced nothing to measure yet — no agent has
     * run, no task exists, no message was sent. A grid of zeroes reads as a
     * broken dashboard; this says which it is.
     *
     * @param {object} data
     * @returns {boolean}
     */
    function isEmpty(data) {
        const tokens = data.tokens || {};
        const tasks = data.tasks || {};
        const comms = data.communication || {};
        return (tokens.by_agent || []).length === 0
            && (tokens.total || 0) === 0
            && Object.keys(tasks.by_status || {}).length === 0
            && (comms.messages_sent || 0) === 0;
    }

    function paintEmpty() {
        summaryEl.textContent = '';
        setBody(h('div', { class: 'place-empty' },
            h('p', { class: 'place-empty-title' }, 'Nothing measured yet'),
            h('p', { class: 'place-empty-hint' },
                'Token use, task counts and error rates appear here once an agent '
                + 'takes its first turn.'),
            h('button', {
                class: 'btn', type: 'button', onclick: () => ctxRef.navigate('board'),
            }, 'Open the board')));
    }

    function paintDashboard(data) {
        // The verdict moved out of this line and into the panel below, where it
        // leads the page instead of trailing the word "Metrics". What is left
        // here is what the header row is for: what this place is.
        summaryEl.textContent = 'Company health';
        const roster = ctxRef.store.getState().roster;
        setBody(
            CARDS.renderHealthPanel(data),
            CARDS.renderStatCards(data),
            BARS.renderAgentActivity(data),
            BARS.renderTaskDistribution(data.tasks || {}),
            BARS.renderTokenUsage(data.tokens || {}, {
                roster,
                // Spec 6.5: the number is the way into what the agent did.
                onOpenLog: (agentId) => ctxRef.navigate('log', { agentId }),
            }),
            CARDS.renderHealthGrid(data),
            CARDS.renderCommunication(data.communication || {}));
    }

    /**
     * Fetch and paint. Never rejects; a failure becomes the error state.
     * @returns {Promise<void>}
     */
    async function refresh() {
        const loadId = load.next();
        paintSkeleton();
        let data;
        try {
            const res = await ctxRef.api(ENDPOINT, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            data = await res.json();
            if (!data || typeof data !== 'object') {
                throw new Error('The dashboard returned no data.');
            }
        } catch (err) {
            if (!load.isCurrent(loadId)) return;
            console.error('[metrics] could not load the dashboard', err);
            paintError((err && err.message) || 'The request failed.');
            return;
        }
        if (!load.isCurrent(loadId)) return;
        if (isEmpty(data)) {
            paintEmpty();
            return;
        }
        paintDashboard(data);
    }

    return {
        label: 'Metrics',
        icon: 'bar-chart-3',

        /**
         * @param {HTMLElement} el
         * @param {object} ctx  `{ store, bus, api, needs, navigate }`.
         * @returns {void}
         */
        mount(el, ctx) {
            ctxRef = ctx;
            load = BossModGates.createLoadGeneration();
            summaryEl = h('p', { class: 'board-summary' }, '');
            bodyEl = h('div', { class: 'metrics-body' });

            clear(el);
            el.append(h('div', { class: 'metrics-place' },
                h('header', { class: 'board-header' },
                    h('div', { class: 'board-title' },
                        h('h1', { tabindex: '-1' }, 'Metrics'), summaryEl),
                    h('div', { class: 'files-controls' },
                        h('button', {
                            class: 'file-toolbar-btn', type: 'button',
                            'aria-label': 'Refresh the metrics',
                            onclick: () => { void refresh(); },
                        }, 'Refresh'))),
                bodyEl));

            // The shell does not drive Place.resync() yet; without this the
            // dashboard would sit on numbers read before an outage.
            disposers.push(ctx.bus.subscribe('resync', () => BossModMetricsPlace.resync()));
            void refresh();
        },

        /**
         * Re-read the dashboard after an outage without remounting (spec 1.4).
         * @returns {void}
         */
        resync() {
            if (!ctxRef) return;
            void refresh();
        },

        /**
         * Drain every subscription and drop the in-flight read.
         * @returns {void}
         */
        unmount() {
            disposers.splice(0).forEach((off) => off());
            if (load) load.next();
            bodyEl = null;
            summaryEl = null;
            ctxRef = null;
        },
    };
})();

BossModPlaces.register('metrics', BossModMetricsPlace);
