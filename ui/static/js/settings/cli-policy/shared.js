/**
 * BossMod AI — CLI Policy shared helpers.
 *
 * The one home for everything more than one CLI-policy tab needs: the agent
 * roster cache, the badge renderers, and the save-feedback affordances. Ported
 * unchanged from cli-policy-section.js, where each of these sat in the same
 * closure as the five tabs that used them; splitting the tabs into files made
 * that closure a shared dependency, so it became a module rather than a copy
 * in each tab.
 *
 * `agentsCache` is deliberately module-level and not per-render: the rules
 * table resolves an agent name for every row and the rule form fills a select
 * from the same list, so re-fetching per tab would be one request per tab
 * switch for a list that does not change while Settings is open.
 */
const BossModCliPolicyShared = (() => {
    const esc = BossModFormat.escapeHtml;

    let agentsCache = [];
    let agentsFetched = false;

    /**
     * Re-run Lucide over one subtree.
     *
     * @param {Element} root  The subtree whose `data-lucide` placeholders to replace.
     * @returns {void}
     */
    function icons(root) {
        if (window.lucide) lucide.createIcons({ nodes: [root] });
    }

    /**
     * Read the agent roster once per Settings session.
     *
     * @returns {Promise<object[]>} The roster, or `[]` when the read failed —
     *   the CLI policy tabs degrade to raw agent ids rather than failing to
     *   render, which is the behaviour this carries over unchanged.
     *
     *   A FAILED read is not cached. apiFetch resolves on 4xx, so the old code
     *   stored the error body as the roster and set `agentsFetched`, and every
     *   tab for the rest of the Settings session then showed raw uuids with no
     *   way to recover but a reload. Now the failure is logged and the next
     *   render retries.
     */
    async function fetchAgents() {
        if (agentsFetched) return agentsCache;
        try {
            const res = await apiFetch('/api/agents');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            agentsCache = await res.json();
            agentsFetched = true;
        } catch (err) {
            console.error('[cli-policy] could not read the agent roster', err);
            agentsCache = [];
        }
        return agentsCache;
    }

    /**
     * The roster as already fetched, for the synchronous render paths.
     *
     * @returns {object[]} Empty until `fetchAgents` has resolved; every caller
     *   runs after the section's render has awaited it.
     */
    function getAgents() {
        return agentsCache;
    }

    /**
     * Display name for a rule or approval's `agent_id`.
     *
     * @param {string|null} agentId
     * @returns {string} The agent's name, the raw id when the roster does not
     *   know it, or 'Global' for a rule that applies to every agent.
     */
    function agentName(agentId) {
        if (!agentId) return 'Global';
        const agent = agentsCache.find(a => a.id === agentId);
        return agent ? agent.name : agentId;
    }

    /**
     * Pill for an approval request's status.
     *
     * @param {string} status
     * @returns {string} HTML for one badge; `status` is escaped.
     */
    function statusBadge(status) {
        const map = {
            pending:  'bg-amber-500/10 text-amber-400',
            approved: 'bg-emerald-500/10 text-emerald-400',
            rejected: 'bg-red-500/10 text-red-400',
            expired:  'bg-slate-500/10 text-slate-400',
        };
        const cls = map[status] || 'bg-slate-500/10 text-slate-400';
        return `<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}">${esc(status)}</span>`;
    }

    /**
     * Flash a card's border green or red for one second.
     *
     * Private: `applySettingSaveResult`, below, is its only caller and always
     * was. Exporting it invited a second way to report a save — one that
     * flashes the border and never writes the status line a screen reader can
     * read.
     *
     * @param {Element} el
     * @param {boolean} success
     * @returns {void}
     */
    function flashBorder(el, success) {
        const cls = success ? 'border-emerald-400' : 'border-red-400';
        el.classList.add(cls);
        setTimeout(() => el.classList.remove(cls), 1000);
    }

    /**
     * Report the outcome of one setting write on its own card.
     *
     * @param {Element|null} card  The `[data-setting-card]` element, or null
     *   when the card has already been re-rendered away.
     * @param {boolean} ok
     * @param {string} message  Empty clears the status line rather than
     *   printing a blank one.
     * @returns {void}
     */
    function applySettingSaveResult(card, ok, message) {
        if (!card) return;
        flashBorder(card, ok);
        let node = card.querySelector('[data-setting-status]');
        if (!message) {
            if (node) node.textContent = '';
            return;
        }
        if (!node) {
            node = document.createElement('p');
            node.setAttribute('data-setting-status', '');
            card.appendChild(node);
        }
        node.textContent = message;
        node.className = ok ? 'text-xs mt-2 text-emerald-600' : 'text-xs mt-2 text-red-500';
    }

    return {
        icons,
        fetchAgents,
        getAgents,
        agentName,
        statusBadge,
        applySettingSaveResult,
    };
})();
