/**
 * BossMod AI — the Metrics bars: agent activity, task distribution, and tokens
 * by agent with the former-agents disclosure.
 *
 * Two things the dock-era version did not do.
 *
 * Each token bar is a button that opens Log filtered to that agent (spec 6.5).
 * "Who spent this" and "what did they do" were two panes with no path between
 * them; now the number is the way in.
 *
 * Token totals come from `diagnostics`, which outlives the agent that produced
 * them, so the list mixes the current team with everyone who has ever run. The
 * roster decides: anyone not on it goes behind a "Former agents" disclosure
 * rather than sitting in the middle of a comparison of the living (spec 6.5).
 * That needs no endpoint — the roster is already in the store.
 */
const BossModMetricBars = (() => {
    const { h } = BossModDom;
    const U = BossModFormat;

    /** Bar tints, cycled. Every one is a token; no hex reaches this file. */
    const HUES = 4;
    const FORMER_LABEL = 'Former agents';

    /**
     * The four statuses the distribution chart shows, in order.
     * `accepted` is folded into `pending`, matching db/metrics.py.
     */
    const DISTRIBUTION = Object.freeze([
        { key: 'complete', label: 'Complete' },
        { key: 'active', label: 'Active' },
        { key: 'pending', label: 'Pending', plus: 'accepted' },
        { key: 'stalled', label: 'Stalled' },
    ]);

    function panel(title, ...children) {
        return h('section', { class: 'metric-panel' },
            h('h2', { class: 'metric-panel-title' }, title), ...children);
    }

    function bar(percent, hue) {
        return h('div', { class: 'metric-bar' },
            h('div', { class: 'metric-bar-fill', 'data-hue': String(hue % HUES),
                style: `width: ${percent}%` }));
    }

    /**
     * Share of API calls per agent, or the aggregate active/idle split when the
     * endpoint reports no per-agent rows.
     *
     * @param {object} data  The dashboard payload.
     * @returns {HTMLElement}
     */
    function renderAgentActivity(data) {
        const agents = data.agents || {};
        const byAgent = ((data.tokens || {}).by_agent) || [];
        if ((agents.total || 0) === 0 && byAgent.length === 0) {
            return panel('Agent Activity',
                h('p', { class: 'place-empty-hint' }, 'No agent data yet.'));
        }

        const rows = h('div', { class: 'metric-rows' });
        if (byAgent.length > 0) {
            const calls = byAgent.reduce((sum, item) => sum + (item.api_calls || 0), 0);
            byAgent.forEach((agent, index) => {
                const share = calls > 0 ? Math.round(((agent.api_calls || 0) / calls) * 100) : 0;
                rows.append(h('div', { class: 'metric-row' },
                    h('span', { class: 'metric-row-name' },
                        String(agent.agent_name || agent.agent_id || 'Unknown')),
                    bar(share, index),
                    h('span', { class: 'metric-row-value' }, `${share}%`)));
            });
        } else {
            const share = (agents.total || 0) > 0
                ? Math.round(((agents.active || 0) / agents.total) * 100) : 0;
            rows.append(h('div', { class: 'metric-row' },
                h('span', { class: 'metric-row-name' }, 'All Agents'),
                bar(share, 0),
                h('span', { class: 'metric-row-value' }, `${share}%`)));
        }
        return panel('Agent Activity', rows);
    }

    /**
     * Task counts by status, as columns.
     *
     * @param {object} tasks
     * @returns {HTMLElement}
     */
    function renderTaskDistribution(tasks) {
        const byStatus = tasks.by_status || {};
        const counts = DISTRIBUTION.map((entry) => ({
            label: entry.label,
            key: entry.key,
            count: (byStatus[entry.key] || 0) + (entry.plus ? (byStatus[entry.plus] || 0) : 0),
        }));
        const max = Math.max(...counts.map((entry) => entry.count), 1);

        const chart = h('div', { class: 'metric-columns' });
        counts.forEach((entry) => {
            chart.append(h('div', { class: 'metric-column' },
                h('span', { class: 'metric-column-value' }, U.formatNumber(entry.count)),
                h('div', { class: 'metric-column-track' },
                    h('div', {
                        class: 'metric-column-fill',
                        'data-status': entry.key,
                        style: `height: ${Math.round((entry.count / max) * 100)}%`,
                    })),
                h('span', { class: 'metric-column-label' }, entry.label)));
        });
        return panel('Task Distribution', chart);
    }

    function tokenRow(agent, index, max, onOpenLog) {
        const total = agent.total_tokens || 0;
        const width = Math.round((total / max) * 100);
        const name = String(agent.agent_name || agent.agent_id || 'Unknown');
        return h('div', { class: 'metric-row metric-row-stacked' },
            h('button', {
                class: 'metric-row-link',
                type: 'button',
                'aria-label': `Show ${name} in the Log`,
                onclick: () => onOpenLog(agent.agent_id),
            }, name),
            h('span', { class: 'metric-row-value' },
                `${U.formatTokenCount(total)} · ${U.formatNumber(agent.api_calls || 0)} calls`),
            bar(width, index));
    }

    /**
     * Tokens by agent, with everyone who has left behind a disclosure.
     *
     * @param {object} tokens  The payload's `tokens` block.
     * @param {object} deps
     * @param {object[]} deps.roster  The live team, from the store.
     * @param {(agentId: string) => void} deps.onOpenLog
     * @returns {HTMLElement}
     */
    function renderTokenUsage(tokens, { roster, onOpenLog }) {
        const byAgent = tokens.by_agent || [];
        if (byAgent.length === 0) {
            return panel('Token Usage by Agent',
                h('p', { class: 'place-empty-hint' }, 'No token usage recorded yet.'));
        }
        const live = new Set((roster || []).map((agent) => agent.id));
        // An empty roster means the first world_update has not landed yet, not
        // that everyone has left. Splitting on it would file the whole team
        // under "Former agents" for as long as that took.
        const knowWhoIsHere = live.size > 0;
        const max = Math.max(...byAgent.map((agent) => agent.total_tokens || 0), 1);
        const current = [];
        const former = [];
        byAgent.forEach((agent, index) => {
            const row = tokenRow(agent, index, max, onOpenLog);
            const isFormer = knowWhoIsHere && !live.has(agent.agent_id);
            (isFormer ? former : current).push(row);
        });

        const rows = h('div', { class: 'metric-rows' }, ...current);
        if (current.length === 0) {
            rows.append(h('p', { class: 'place-empty-hint' }, 'Nobody on the team has run yet.'));
        }
        if (former.length > 0) {
            rows.append(h('details', { class: 'metric-former' },
                h('summary', { class: 'metric-former-summary' },
                    `${FORMER_LABEL} (${former.length})`),
                ...former));
        }
        return panel('Token Usage by Agent', rows);
    }

    return { renderAgentActivity, renderTaskDistribution, renderTokenUsage, FORMER_LABEL };
})();
