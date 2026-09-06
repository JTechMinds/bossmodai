/**
 * BossMod AI — Company Org Chart tab.
 * Visualizes the team as a card-based agent overview with live status updates.
 */
const CompanyOrg = (() => {
    let container = null;
    let agents = [];

    // ─── Formatters ───

    const formatNumber = BossModUtils.formatNumber;

    // ─── Active status check ───

    function isActive(status, activityKind) {
        if (activityKind && activityKind !== 'idle') return true;
        return status && status !== 'idle';
    }

    // ─── Rendering ───

    function render(el) {
        container = el;
        agents = [];
        renderLoading();
        fetchAgents();
    }

    function renderLoading() {
        if (!container) return;
        container.innerHTML = `
            <div class="p-6 flex items-center justify-center gap-2 text-bm-muted">
                <i data-lucide="loader-2" class="w-5 h-5 animate-spin"></i>
                <span class="text-sm">Loading team...</span>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderError(message) {
        if (!container) return;
        const escaped = BossModUtils.escapeHtml(message || 'Failed to load agents');
        container.innerHTML = `
            <div class="p-6">
                <div class="bg-red-50 border border-red-200 rounded-lg p-4 text-center">
                    <i data-lucide="alert-triangle" class="w-6 h-6 text-red-500 mx-auto mb-2"></i>
                    <p class="text-sm text-red-700 font-medium">${escaped}</p>
                    <button id="org-retry-btn"
                        class="mt-3 px-4 py-1.5 text-xs font-medium bg-white border border-red-200 text-red-700 rounded-lg hover:bg-red-50 transition-colors">
                        Retry
                    </button>
                </div>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
        const retryBtn = container.querySelector('#org-retry-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', () => {
                renderLoading();
                fetchAgents();
            });
        }
    }

    function renderEmpty() {
        if (!container) return;
        container.innerHTML = `
            <div class="p-6 text-center text-bm-muted">
                <i data-lucide="users" class="w-10 h-10 mx-auto mb-3 opacity-30"></i>
                <p class="text-sm font-medium">No agents created yet</p>
                <p class="text-xs mt-1">Agents will appear here once added to the company</p>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    async function fetchAgents() {
        try {
            const res = await apiFetch('/api/company/agents?include=stats', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            agents = await res.json();
            if (!container) return;
            if (agents.length === 0) {
                renderEmpty();
            } else {
                renderGrid();
            }
        } catch (err) {
            console.error('[CompanyOrg] Failed to fetch agents:', err);
            renderError(err.message);
        }
    }

    function renderGrid() {
        if (!container) return;

        const cards = agents.map(agent => renderCard(agent)).join('');

        container.innerHTML = `
            <div class="p-4 sm:p-6 overflow-y-auto" style="max-height: calc(100vh - 160px);">
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    ${cards}
                </div>
            </div>`;

        if (window.lucide) lucide.createIcons({ nodes: [container] });
        bindCardClicks();
    }

    function renderCard(agent) {
        const id        = BossModUtils.escapeHtml(agent.id || '');
        const name      = BossModUtils.escapeHtml(agent.name || 'Unknown');
        const role      = BossModUtils.escapeHtml(agent.role || 'No specialty');
        const description = agent.description ? BossModUtils.escapeHtml(agent.description) : '';
        const doneBar   = agent.done_fail_bar ? BossModUtils.escapeHtml(agent.done_fail_bar) : '';
        const color     = BossModUtils.escapeHtml(agent.color || '#3b82f6');
        const initial   = (agent.name || '?')[0].toUpperCase();
        const status    = agent.status || 'idle';
        const actKind   = agent.currentActivityKind || null;
        const location  = BossModUtils.escapeHtml(agent.location || 'Unknown');
        const active    = isActive(status, actKind);
        const dimClass  = active ? '' : 'opacity-60';

        const statusLabel   = BossModUtils.escapeHtml(BossModUtils.getStatusLabel(status, actKind));
        const statusClasses = BossModUtils.getStatusClasses(status, actKind);
        const statusDot     = BossModUtils.getStatusDot(status, actKind);
        const liveDot       = active ? 'bg-emerald-500' : 'bg-slate-400';

        const tasksCompleted = formatNumber(agent.tasks_completed ?? 0);
        const tokensUsed     = formatNumber(agent.tokens_used ?? 0);
        const currentTask    = agent.current_task
            ? BossModUtils.escapeHtml(agent.current_task)
            : null;

        return `
            <div class="bg-white border border-bm-border rounded-xl p-4 cursor-pointer hover:shadow-md transition-shadow relative ${dimClass}"
                 data-agent-id="${id}">
                <!-- Live status dot -->
                <span class="absolute top-3 right-3 w-2.5 h-2.5 rounded-full ${liveDot}" data-live-dot="${id}"></span>

                <!-- Header: Avatar + Name -->
                <div class="flex items-center gap-3 mb-3">
                    <div class="w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0"
                         style="background: ${color}">
                        ${BossModUtils.escapeHtml(initial)}
                    </div>
                    <div class="min-w-0">
                        <p class="text-sm font-bold text-bm-text truncate">${name}</p>
                        <p class="text-xs text-bm-muted truncate">${role}</p>
                        ${description ? `<p class="text-[11px] text-bm-muted truncate" title="${description}">${description}</p>` : ''}
                        ${doneBar ? `<p class="text-[11px] text-bm-muted truncate" title="${doneBar}">${doneBar}</p>` : ''}
                    </div>
                </div>

                <!-- 2x2 Stat Grid -->
                <div class="grid grid-cols-2 gap-2 mb-3 text-xs">
                    <div>
                        <span class="text-bm-muted">Status</span>
                        <div class="mt-0.5">
                            <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${statusClasses}" data-status-badge="${id}">
                                <span class="w-1.5 h-1.5 rounded-full ${statusDot}"></span>
                                <span data-status-label="${id}">${statusLabel}</span>
                            </span>
                        </div>
                    </div>
                    <div>
                        <span class="text-bm-muted">Location</span>
                        <p class="mt-0.5 font-medium text-bm-text truncate">${location}</p>
                    </div>
                    <div>
                        <span class="text-bm-muted">Tasks Done</span>
                        <p class="mt-0.5 font-medium text-bm-text">${tasksCompleted}</p>
                    </div>
                    <div>
                        <span class="text-bm-muted">Tokens Used</span>
                        <p class="mt-0.5 font-medium text-bm-text">${tokensUsed}</p>
                    </div>
                </div>

                <!-- Current Task -->
                <div class="border-t border-bm-border pt-2 text-xs" data-task-section="${id}">
                    ${currentTask
                        ? `<p class="text-bm-text truncate"><i data-lucide="briefcase" class="w-3 h-3 inline mr-1 text-bm-muted"></i>${currentTask}</p>`
                        : `<p class="text-bm-muted"><i data-lucide="pause" class="w-3 h-3 inline mr-1"></i>No active task</p>`
                    }
                </div>
            </div>`;
    }

    function bindCardClicks() {
        if (!container) return;
        container.querySelectorAll('[data-agent-id]').forEach(card => {
            card.addEventListener('click', () => {
                const agentId = card.dataset.agentId;
                const agent = agents.find(a => a.id === agentId);
                if (agent && typeof BossModApp !== 'undefined') {
                    BossModApp.selectAgent(agent);
                }
            });
        });
    }

    // ─── Live updates via WebSocket ───

    function handleWorldUpdate(incomingAgents) {
        if (!container) return;
        if (!Array.isArray(incomingAgents)) return;

        const previousIds = agents.map(agent => agent.id).join('\0');
        const next = BossModUtils.mergeRosterFromWorld(agents, incomingAgents);
        const nextIds = next.map(agent => agent.id).join('\0');
        const membershipChanged = previousIds !== nextIds;

        if (membershipChanged) {
            agents = next;
            if (agents.length === 0) {
                renderEmpty();
            } else {
                renderGrid();
            }
            return;
        }

        const previousById = new Map(
            agents.map(agent => [agent.id, {
                status: agent.status,
                kind: agent.currentActivityKind,
            }])
        );
        let needsFullRerender = false;
        agents = next;

        for (const agent of agents) {
            const previous = previousById.get(agent.id);
            if (
                previous
                && agent.status === previous.status
                && agent.currentActivityKind === previous.kind
            ) {
                continue;
            }
            const updated = updateCardStatus(agent);
            if (!updated) needsFullRerender = true;
        }

        if (needsFullRerender && agents.length) {
            renderGrid();
        }
    }

    function updateCardStatus(agent) {
        if (!container) return false;

        const id = agent.id;
        const status = agent.status || 'idle';
        const actKind = agent.currentActivityKind || null;
        const active = isActive(status, actKind);

        // Update live dot
        const dot = container.querySelector(`[data-live-dot="${CSS.escape(id)}"]`);
        if (dot) {
            dot.className = `absolute top-3 right-3 w-2.5 h-2.5 rounded-full ${active ? 'bg-emerald-500' : 'bg-slate-400'}`;
        }

        // Update status badge
        const badge = container.querySelector(`[data-status-badge="${CSS.escape(id)}"]`);
        if (badge) {
            const statusClasses = BossModUtils.getStatusClasses(status, actKind);
            const statusDot = BossModUtils.getStatusDot(status, actKind);
            badge.className = `inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${statusClasses}`;
            const dotEl = badge.querySelector('span:first-child');
            if (dotEl) dotEl.className = `w-1.5 h-1.5 rounded-full ${statusDot}`;
        }

        // Update status label
        const labelEl = container.querySelector(`[data-status-label="${CSS.escape(id)}"]`);
        if (labelEl) {
            labelEl.textContent = BossModUtils.getStatusLabel(status, actKind);
        }

        // Update card opacity
        const card = container.querySelector(`[data-agent-id="${CSS.escape(id)}"]`);
        if (card) {
            card.classList.toggle('opacity-60', !active);
        }

        return !!(dot && badge && labelEl && card);
    }

    // ─── Lifecycle ───

    function destroy() {
        container = null;
        agents = [];
    }

    return { render, handleWorldUpdate, destroy };
})();
