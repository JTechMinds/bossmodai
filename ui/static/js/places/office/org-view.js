/**
 * BossMod AI — the Office's Org view.
 *
 * One card per agent: who they are, what state they are in, and what they are
 * carrying. Ported from company-org.js, which built every card from a template
 * literal — agent names, roles, and task titles are model output, and h()
 * escapes them by construction where string concatenation did not.
 *
 * Status arrives twice, from two sources that must not be conflated: the
 * per-agent stats come from GET /api/company/agents (a fetch), and the live
 * state comes from store.roster (a world_update). A membership change rebuilds
 * the grid; a status change patches the affected cards in place, so the grid
 * does not flicker on every simulation tick.
 */
const BossModOrgView = (() => {
    const { h, clear } = BossModDom;

    /** An agent is "active" when doing anything other than sitting idle. */
    function isActive(status, activityKind) {
        if (activityKind && activityKind !== 'idle') return true;
        return Boolean(status) && status !== 'idle';
    }

    /**
     * Build the Org view.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Subscribed for `roster`; the view owns the
     *   subscription so the place does not have to re-broadcast world updates.
     * @param {(agentId: string) => void} deps.onAgentClick
     * @returns {{element: HTMLElement, refresh: () => Promise<void>,
     *   handleWorldUpdate: (agents: object[]) => void, destroy: () => void}}
     * @throws {Error} When any dependency is missing.
     */
    function createOrgView(deps) {
        const { api, store, onAgentClick } = deps || {};
        if (typeof api !== 'function') throw new Error('[org-view] deps.api is required');
        if (!store) throw new Error('[org-view] deps.store is required');
        if (typeof onAgentClick !== 'function') throw new Error('[org-view] deps.onAgentClick is required');

        const load = BossModGates.createLoadGeneration();
        const body = h('div', { class: 'org-body' });
        const element = h('div', { class: 'org-view' }, body);
        /** agentId -> the nodes updateCardStatus patches, so it never queries. */
        const cardNodes = new Map();
        let agents = [];
        let destroyed = false;

        function stat(label, value) {
            return h('div', { class: 'org-stat' },
                h('span', { class: 'org-stat-label' }, label),
                h('p', { class: 'org-stat-value' }, value));
        }

        function card(agent) {
            const status = agent.status || 'idle';
            const kind = agent.currentActivityKind || null;
            const active = isActive(status, kind);
            const dot = h('span', {
                class: `org-live-dot ${active ? 'is-active' : 'is-idle'}`,
                'data-live-dot': agent.id,
            });
            const label = h('span', { 'data-status-label': agent.id },
                BossModAgentStatus.getStatusLabel(status, kind));
            const badge = h('span', {
                class: 'status-pill', 'data-status-badge': agent.id,
                'data-status': status, 'data-activity': kind || '',
            }, h('span', { class: 'status-dot' }), label);
            const root = h('button', {
                class: `org-card${active ? '' : ' is-dim'}`,
                type: 'button',
                'data-agent-id': agent.id,
                'aria-label': `Open ${agent.name || 'agent'}`,
                onclick: () => onAgentClick(agent.id),
            },
                dot,
                h('div', { class: 'org-card-head' },
                    BossModAvatar.create({ name: agent.name, color: agent.color, size: 'lg' }),
                    h('div', { class: 'org-identity' },
                        h('p', { class: 'org-name' }, agent.name || 'Unknown'),
                        h('p', { class: 'org-role' }, agent.role || 'No specialty'),
                        agent.description ? h('p', { class: 'org-note' }, agent.description) : null,
                        agent.done_fail_bar ? h('p', { class: 'org-note' }, agent.done_fail_bar) : null)),
                h('div', { class: 'org-stats' },
                    h('div', { class: 'org-stat' },
                        h('span', { class: 'org-stat-label' }, 'Status'),
                        h('div', { class: 'org-stat-value' }, badge)),
                    stat('Location', agent.location || 'Unknown'),
                    stat('Tasks done', BossModFormat.formatNumber(agent.tasks_completed ?? 0)),
                    stat('Tokens used', BossModFormat.formatNumber(agent.tokens_used ?? 0))),
                h('p', { class: 'org-task' },
                    agent.current_task ? String(agent.current_task) : 'No active task'));
            cardNodes.set(agent.id, { root, dot, badge, label });
            return root;
        }

        function renderGrid() {
            cardNodes.clear();
            clear(body);
            const grid = h('div', { class: 'org-grid' });
            agents.forEach((agent) => grid.append(card(agent)));
            body.append(grid);
        }

        function renderEmpty() {
            cardNodes.clear();
            clear(body);
            body.append(h('div', { class: 'place-empty' },
                h('p', { class: 'place-empty-title' }, 'Nobody works here yet'),
                h('p', { class: 'place-empty-hint' },
                    'Add an agent from the roster and they will appear on this chart.')));
        }

        function renderLoading() {
            cardNodes.clear();
            clear(body);
            body.append(h('div', { class: 'org-grid' },
                h('div', { class: 'org-card is-skeleton' }),
                h('div', { class: 'org-card is-skeleton' }),
                h('div', { class: 'org-card is-skeleton' })));
        }

        function renderError(message) {
            cardNodes.clear();
            clear(body);
            body.append(h('div', { class: 'place-error-panel', role: 'alert' },
                h('p', { class: 'place-error-title' }, 'Could not load the team'),
                h('p', { class: 'place-error-detail' }, message),
                h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } },
                    'Try again')));
        }

        /**
         * Load the roster with its stats and paint the grid.
         *
         * @returns {Promise<void>} Never rejects: a failure becomes the error
         *   state with a retry, because a blank panel reads as "no team".
         */
        async function refresh() {
            const loadId = load.next();
            renderLoading();
            try {
                const res = await api('/api/company/agents?include=stats', { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const rows = await res.json();
                if (destroyed || !load.isCurrent(loadId)) return;
                agents = Array.isArray(rows) ? rows : [];
                if (agents.length === 0) renderEmpty();
                else renderGrid();
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[org-view] could not load the team', err);
                renderError((err && err.message) || 'The request failed.');
            }
        }

        /**
         * Fold a world snapshot into the cards.
         *
         * A membership change rebuilds the grid. A status change patches only
         * the cards whose state actually moved — rebuilding on every tick would
         * destroy scroll position and any focus inside the chart.
         *
         * An empty incoming roster is a real answer, not a reason to bail: the
         * last agent leaving must clear the chart rather than freeze it.
         *
         * @param {object[]} incomingAgents  Normalised world rows.
         * @returns {void}
         */
        function handleWorldUpdate(incomingAgents) {
            if (destroyed || !Array.isArray(incomingAgents)) return;
            const previousIds = agents.map((agent) => agent.id).join('\0');
            const next = BossModAgentStatus.mergeRosterFromWorld(agents, incomingAgents);
            const membershipChanged = previousIds !== next.map((agent) => agent.id).join('\0');

            if (membershipChanged) {
                agents = next;
                if (agents.length === 0) renderEmpty();
                else renderGrid();
                return;
            }

            const before = new Map(agents.map((agent) => [agent.id,
                { status: agent.status, kind: agent.currentActivityKind }]));
            agents = next;
            let stale = false;
            for (const agent of agents) {
                const previous = before.get(agent.id);
                if (previous
                    && agent.status === previous.status
                    && agent.currentActivityKind === previous.kind) continue;
                if (!updateCardStatus(agent)) stale = true;
            }
            if (stale && agents.length) renderGrid();
        }

        /**
         * Patch one card's live state in place.
         *
         * @param {object} agent
         * @returns {boolean} False when the card is not on screen, which tells
         *   handleWorldUpdate to rebuild rather than silently drop the update.
         */
        function updateCardStatus(agent) {
            const nodes = cardNodes.get(agent.id);
            if (!nodes) return false;
            const status = agent.status || 'idle';
            const kind = agent.currentActivityKind || null;
            const active = isActive(status, kind);
            nodes.dot.className = `org-live-dot ${active ? 'is-active' : 'is-idle'}`;
            nodes.badge.setAttribute('data-status', status);
            nodes.badge.setAttribute('data-activity', kind || '');
            nodes.label.textContent = BossModAgentStatus.getStatusLabel(status, kind);
            nodes.root.classList.toggle('is-dim', !active);
            return true;
        }

        const unsubscribe = store.subscribe((s) => s.roster, handleWorldUpdate);
        void refresh();

        return {
            element,
            refresh,
            handleWorldUpdate,

            /**
             * Drop the roster subscription and abandon any in-flight load.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
                unsubscribe();
                cardNodes.clear();
                clear(body);
            },
        };
    }

    return { createOrgView };
})();
