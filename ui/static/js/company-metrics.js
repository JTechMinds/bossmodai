/**
 * BossMod AI — Company Metrics tab.
 * Displays organization-wide performance metrics and analytics dashboards.
 * All visualizations use pure CSS (no chart libraries).
 */
const CompanyMetrics = (() => {
    let container = null;

    // ─── Formatters ───

    const formatNumber = BossModUtils.formatNumber;

    function formatDuration(seconds) {
        if (seconds == null || isNaN(seconds) || seconds < 0) return '--';
        const s = Math.floor(Number(seconds));
        if (s < 60) return '< 1m';
        const hours = Math.floor(s / 3600);
        const minutes = Math.floor((s % 3600) / 60);
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }

    function formatTokenCount(n) {
        if (n == null || isNaN(n)) return '0';
        return formatNumber(n) + ' tokens';
    }

    // ─── Error-rate health helpers ───

    function getErrorRateHealth(rate) {
        const pct = (rate || 0) * 100;
        if (pct < 5)  return { label: 'Healthy',  dotClass: 'bg-emerald-500', textClass: 'text-emerald-600' };
        if (pct <= 15) return { label: 'Warning', dotClass: 'bg-amber-500',   textClass: 'text-amber-600' };
        return { label: 'Critical', dotClass: 'bg-red-500', textClass: 'text-red-600' };
    }

    // ─── Bar color palette for agents ───

    const AGENT_COLORS = [
        'bg-blue-500', 'bg-emerald-500', 'bg-amber-500', 'bg-purple-500',
        'bg-rose-500', 'bg-cyan-500', 'bg-indigo-500', 'bg-teal-500',
    ];

    function getAgentBarColor(index) {
        return AGENT_COLORS[index % AGENT_COLORS.length];
    }

    // ─── Status bar colors ───

    const STATUS_BAR_COLORS = {
        complete: 'bg-emerald-500',
        active:   'bg-blue-500',
        pending:  'bg-amber-500',
        accepted: 'bg-amber-400',
        stalled:  'bg-red-500',
        blocked:  'bg-red-400',
    };

    // ─── Rendering ───

    function render(el) {
        container = el;
        renderLoading();
        fetchMetrics();
    }

    function renderLoading() {
        if (!container) return;
        container.innerHTML = `
            <div class="p-6 flex items-center justify-center gap-2 text-bm-muted">
                <i data-lucide="loader-2" class="w-5 h-5 animate-spin"></i>
                <span class="text-sm">Loading metrics...</span>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderError(message) {
        if (!container) return;
        const escaped = BossModUtils.escapeHtml(message || 'Failed to load metrics');
        container.innerHTML = `
            <div class="p-6">
                <div class="bg-red-50 border border-red-200 rounded-lg p-4 text-center">
                    <i data-lucide="alert-triangle" class="w-6 h-6 text-red-500 mx-auto mb-2"></i>
                    <p class="text-sm text-red-700 font-medium">${escaped}</p>
                    <button id="metrics-retry-btn"
                        class="mt-3 px-4 py-1.5 text-xs font-medium bg-white border border-red-200 text-red-700 rounded-lg hover:bg-red-50 transition-colors">
                        Retry
                    </button>
                </div>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
        const retryBtn = container.querySelector('#metrics-retry-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', () => {
                renderLoading();
                fetchMetrics();
            });
        }
    }

    async function fetchMetrics() {
        try {
            const res = await apiFetch('/api/metrics/dashboard', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (!container) return;
            renderDashboard(data);
        } catch (err) {
            console.error('[CompanyMetrics] Failed to fetch metrics:', err);
            renderError(err.message);
        }
    }

    function renderDashboard(data) {
        if (!container) return;

        const tasks   = data.tasks   || {};
        const agents  = data.agents  || {};
        const tokens  = data.tokens  || {};
        const errors  = data.errors  || {};
        const comms   = data.communication || {};
        const tools   = data.tool_calls    || {};
        const uptime  = data.uptime        || {};

        const errorHealth = getErrorRateHealth(errors.rate);
        const errorPct = ((errors.rate || 0) * 100).toFixed(1);

        container.innerHTML = `
            <div class="p-4 sm:p-6 space-y-4 overflow-y-auto" style="max-height: calc(100vh - 160px);">

                <!-- Section 1: Top Stat Cards -->
                ${renderStatCards(tasks, agents, tokens, errors, errorHealth, errorPct)}

                <!-- Section 2: Agent Activity -->
                ${renderAgentActivity(agents, data)}

                <!-- Section 3: Task Distribution -->
                ${renderTaskDistribution(tasks)}

                <!-- Section 4: Token Usage by Agent -->
                ${renderTokenUsage(tokens)}

                <!-- Section 5: Health & Errors -->
                ${renderHealthGrid(errors, tools, uptime)}

                <!-- Section 6: Communication -->
                ${renderCommunication(comms)}
            </div>`;

        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    // ─── Section 1: Top Stat Cards ───

    function renderStatCards(tasks, agents, tokens, errors, errorHealth, errorPct) {
        const completedToday = tasks.completed_today || 0;
        const idleCount = agents.idle || 0;
        const todayTokens = tokens.today || 0;

        return `
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                ${statCard('check-circle-2', 'Tasks Completed', formatNumber(tasks.completed || 0),
                    `+${formatNumber(completedToday)} today`)}
                ${statCard('users', 'Active Agents', formatNumber(agents.active || 0),
                    `${formatNumber(idleCount)} idle`)}
                ${statCard('coins', 'Tokens Today', formatNumber(todayTokens),
                    `${formatNumber(tokens.total || 0)} all-time`)}
                ${statCardWithHealth('activity', 'Error Rate', `${errorPct}%`, errorHealth)}
            </div>`;
    }

    function statCard(icon, label, value, subtitle) {
        const escapedLabel = BossModUtils.escapeHtml(label);
        const escapedValue = BossModUtils.escapeHtml(value);
        const escapedSub   = BossModUtils.escapeHtml(subtitle);
        return `
            <div class="bg-white border border-bm-border rounded-lg p-4">
                <div class="flex items-center gap-2 mb-1">
                    <i data-lucide="${icon}" class="w-4 h-4 text-bm-muted"></i>
                    <span class="text-xs text-bm-muted font-medium">${escapedLabel}</span>
                </div>
                <p class="text-2xl font-bold text-bm-text">${escapedValue}</p>
                <p class="text-xs text-bm-muted mt-0.5">${escapedSub}</p>
            </div>`;
    }

    function statCardWithHealth(icon, label, value, health) {
        const escapedLabel = BossModUtils.escapeHtml(label);
        const escapedValue = BossModUtils.escapeHtml(value);
        const escapedHealth = BossModUtils.escapeHtml(health.label);
        return `
            <div class="bg-white border border-bm-border rounded-lg p-4">
                <div class="flex items-center gap-2 mb-1">
                    <i data-lucide="${icon}" class="w-4 h-4 text-bm-muted"></i>
                    <span class="text-xs text-bm-muted font-medium">${escapedLabel}</span>
                </div>
                <p class="text-2xl font-bold text-bm-text">${escapedValue}</p>
                <div class="flex items-center gap-1.5 mt-0.5">
                    <span class="w-2 h-2 rounded-full ${health.dotClass}"></span>
                    <span class="text-xs font-medium ${health.textClass}">${escapedHealth}</span>
                </div>
            </div>`;
    }

    // ─── Section 2: Agent Activity Bars ───

    function renderAgentActivity(agentsData, fullData) {
        const byAgent = (fullData.tokens && fullData.tokens.by_agent) || [];
        const totalAgents = agentsData.total || 0;
        const activeAgents = agentsData.active || 0;
        const idleAgents = agentsData.idle || 0;

        if (totalAgents === 0 && byAgent.length === 0) {
            return `
                <div class="bg-white border border-bm-border rounded-lg p-4">
                    <h3 class="text-sm font-semibold text-bm-text mb-3 flex items-center gap-2">
                        <i data-lucide="bar-chart-3" class="w-4 h-4 text-bm-muted"></i>
                        Agent Activity
                    </h3>
                    <p class="text-xs text-bm-muted text-center py-2">No agent data available</p>
                </div>`;
        }

        // If we have per-agent token data, use that to approximate activity
        // Otherwise fall back to aggregate active/idle split
        let agentBars = '';
        if (byAgent.length > 0) {
            const totalCalls = byAgent.reduce((sum, a) => sum + (a.api_calls || 0), 0);
            agentBars = byAgent.map(agent => {
                const name = BossModUtils.escapeHtml(agent.agent_name || agent.agent_id || 'Unknown');
                const calls = agent.api_calls || 0;
                const activePct = totalCalls > 0 ? Math.round((calls / totalCalls) * 100) : 0;
                const idlePct = 100 - activePct;
                return `
                    <div class="flex items-center gap-3 text-xs">
                        <span class="w-2 h-2 rounded-full bg-blue-500 flex-shrink-0"></span>
                        <span class="w-20 truncate font-medium text-bm-text">${name}</span>
                        <div class="flex-1 h-5 rounded overflow-hidden flex bg-slate-200">
                            <div class="bg-blue-500 h-full transition-all" style="width: ${activePct}%"></div>
                        </div>
                        <span class="w-10 text-right text-bm-muted">${activePct}%</span>
                    </div>`;
            }).join('');
        } else {
            // Single aggregate bar
            const activePct = totalAgents > 0 ? Math.round((activeAgents / totalAgents) * 100) : 0;
            agentBars = `
                <div class="flex items-center gap-3 text-xs">
                    <span class="w-2 h-2 rounded-full bg-blue-500 flex-shrink-0"></span>
                    <span class="w-20 truncate font-medium text-bm-text">All Agents</span>
                    <div class="flex-1 h-5 rounded overflow-hidden flex bg-slate-200">
                        <div class="bg-blue-500 h-full transition-all" style="width: ${activePct}%"></div>
                    </div>
                    <span class="w-10 text-right text-bm-muted">${activePct}%</span>
                </div>`;
        }

        return `
            <div class="bg-white border border-bm-border rounded-lg p-4">
                <h3 class="text-sm font-semibold text-bm-text mb-3 flex items-center gap-2">
                    <i data-lucide="bar-chart-3" class="w-4 h-4 text-bm-muted"></i>
                    Agent Activity
                </h3>
                <div class="space-y-2">
                    ${agentBars}
                </div>
            </div>`;
    }

    // ─── Section 3: Task Distribution (vertical CSS bar chart) ───

    function renderTaskDistribution(tasks) {
        const statuses = [
            { key: 'complete', label: 'Complete', color: 'bg-emerald-500' },
            { key: 'active',   label: 'Active',   color: 'bg-blue-500' },
            { key: 'pending',  label: 'Pending',  color: 'bg-amber-500' },
            { key: 'stalled',  label: 'Stalled',  color: 'bg-red-500' },
        ];

        const byStatus = tasks.by_status || {};
        const counts = statuses.map(s => {
            let count = byStatus[s.key] || 0;
            // Merge 'accepted' into 'pending' per the backend logic
            if (s.key === 'pending') count += (byStatus['accepted'] || 0);
            return { ...s, count };
        });

        const maxCount = Math.max(...counts.map(c => c.count), 1);
        const maxBarHeight = 100;

        const bars = counts.map(item => {
            const height = Math.round((item.count / maxCount) * maxBarHeight);
            const escapedLabel = BossModUtils.escapeHtml(item.label);
            return `
                <div class="flex flex-col items-center gap-1">
                    <span class="text-xs font-bold text-bm-text">${formatNumber(item.count)}</span>
                    <div class="w-10 flex items-end" style="height: ${maxBarHeight}px;">
                        <div class="${item.color} w-full rounded-t transition-all" style="height: ${height}px;"></div>
                    </div>
                    <span class="text-xs text-bm-muted">${escapedLabel}</span>
                </div>`;
        }).join('');

        return `
            <div class="bg-white border border-bm-border rounded-lg p-4">
                <h3 class="text-sm font-semibold text-bm-text mb-3 flex items-center gap-2">
                    <i data-lucide="pie-chart" class="w-4 h-4 text-bm-muted"></i>
                    Task Distribution
                </h3>
                <div class="flex items-end justify-around py-2">
                    ${bars}
                </div>
            </div>`;
    }

    // ─── Section 4: Token Usage by Agent ───

    function renderTokenUsage(tokens) {
        const byAgent = tokens.by_agent || [];

        if (byAgent.length === 0) {
            return `
                <div class="bg-white border border-bm-border rounded-lg p-4">
                    <h3 class="text-sm font-semibold text-bm-text mb-3 flex items-center gap-2">
                        <i data-lucide="coins" class="w-4 h-4 text-bm-muted"></i>
                        Token Usage by Agent
                    </h3>
                    <p class="text-xs text-bm-muted text-center py-2">No token data available</p>
                </div>`;
        }

        const maxTokens = Math.max(...byAgent.map(a => a.total_tokens || 0), 1);

        const rows = byAgent.map((agent, idx) => {
            const name = BossModUtils.escapeHtml(agent.agent_name || agent.agent_id || 'Unknown');
            const agentTokens = agent.total_tokens || 0;
            const calls = agent.api_calls || 0;
            const widthPct = Math.round((agentTokens / maxTokens) * 100);
            const barColor = getAgentBarColor(idx);
            return `
                <div class="space-y-1">
                    <div class="flex items-center justify-between text-xs">
                        <span class="font-medium text-bm-text">${name}</span>
                        <span class="text-bm-muted">${formatTokenCount(agentTokens)} &middot; ${formatNumber(calls)} calls</span>
                    </div>
                    <div class="w-full h-3 rounded-full bg-slate-100 overflow-hidden">
                        <div class="${barColor} h-full rounded-full transition-all" style="width: ${widthPct}%"></div>
                    </div>
                </div>`;
        }).join('');

        return `
            <div class="bg-white border border-bm-border rounded-lg p-4">
                <h3 class="text-sm font-semibold text-bm-text mb-3 flex items-center gap-2">
                    <i data-lucide="coins" class="w-4 h-4 text-bm-muted"></i>
                    Token Usage by Agent
                </h3>
                <div class="space-y-3">
                    ${rows}
                </div>
            </div>`;
    }

    // ─── Section 5: Health & Errors (2x2 grid) ───

    function renderHealthGrid(errors, tools, uptime) {
        const errorsToday = errors.today || 0;
        const invalidDecisions = errors.invalid_decisions || 0;
        const toolCallsTotal = tools.total || 0;
        const uptimeDisplay = uptime.seconds != null ? formatDuration(uptime.seconds) : '--';

        return `
            <div class="grid grid-cols-2 gap-3">
                ${healthCell('alert-triangle', errorsToday, 'Errors Today', 'text-red-600')}
                ${healthCell('x-circle', invalidDecisions, 'Invalid Decisions', 'text-amber-600')}
                ${healthCell('terminal', formatNumber(toolCallsTotal), 'Tool Calls', 'text-blue-600')}
                ${healthCell('clock', BossModUtils.escapeHtml(uptimeDisplay), 'Uptime', 'text-emerald-600')}
            </div>`;
    }

    function healthCell(icon, value, label, valueColor) {
        const escapedLabel = BossModUtils.escapeHtml(label);
        const displayValue = typeof value === 'number' ? formatNumber(value) : value;
        return `
            <div class="bg-white border border-bm-border rounded-lg p-3 text-center">
                <i data-lucide="${icon}" class="w-4 h-4 ${valueColor} mx-auto mb-1"></i>
                <p class="text-xl font-bold ${valueColor}">${displayValue}</p>
                <p class="text-xs text-bm-muted mt-0.5">${escapedLabel}</p>
            </div>`;
    }

    // ─── Section 6: Communication (inline stats row) ───

    function renderCommunication(comms) {
        const items = [
            { value: comms.messages_sent || 0,        label: 'messages sent' },
            { value: comms.agent_conversations || 0,  label: 'agent conversations' },
            { value: comms.active_channels || 0,      label: 'active channels' },
            { value: comms.meetings_held || 0,         label: 'meetings held' },
        ];

        const parts = items.map(item => {
            const escapedLabel = BossModUtils.escapeHtml(item.label);
            return `<span class="whitespace-nowrap"><strong class="font-semibold text-bm-text">${formatNumber(item.value)}</strong> ${escapedLabel}</span>`;
        }).join('<span class="text-bm-border mx-1">&middot;</span>');

        return `
            <div class="bg-white border border-bm-border rounded-lg p-4">
                <div class="flex items-center gap-2 mb-2">
                    <i data-lucide="message-circle" class="w-4 h-4 text-bm-muted"></i>
                    <span class="text-sm font-semibold text-bm-text">Communication</span>
                </div>
                <div class="flex flex-wrap items-center gap-1 text-xs text-bm-muted">
                    ${parts}
                </div>
            </div>`;
    }

    // ─── Lifecycle ───

    function destroy() {
        container = null;
    }

    return { render, destroy };
})();
