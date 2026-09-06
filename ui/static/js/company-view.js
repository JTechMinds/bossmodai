/**
 * BossMod AI — Directory roster view (formerly "Company").
 *
 * Renders the agent directory with search, status filters, and status badges.
 * Supports multi-select and shared channel creation from selected agents.
 */

const CompanyView = (() => {
    let roster = [];
    let selectedIds = new Set();
    let activeContainer = null;
    let searchQuery = '';
    let activeFilter = 'all';
    let debounceTimer = null;
    let callbacks = {
        onSelectAgent: null,
        onOpenChannel: null,
    };

    function setCallbacks(next) {
        callbacks = { ...callbacks, ...(next || {}) };
    }

    async function loadRoster() {
        const res = await apiFetch('/api/company/agents', { cache: 'no-store' });
        if (!res.ok) {
            throw new Error(await res.text());
        }
        roster = await res.json();
        pruneSelection();
        return roster;
    }

    function handleWorldUpdate(agents) {
        if (!Array.isArray(agents) || !roster.length) return;
        const byId = new Map(agents.map(agent => [agent.id, agent]));
        roster = roster.map(item => {
            const runtime = byId.get(item.id);
            if (!runtime) return item;
            return {
                ...item,
                status: runtime.status || item.status,
                currentActivityKind: runtime.currentActivityKind ?? item.currentActivityKind,
                idle_since: runtime.idle_since ?? item.idle_since,
                x: runtime.x ?? item.x,
                y: runtime.y ?? item.y,
            };
        });
        if (activeContainer) {
            renderRosterList(activeContainer.querySelector('#company-roster-list'));
            updateSelectionUi(activeContainer);
        }
    }

    function pruneSelection() {
        const validIds = new Set(roster.map(item => item.id));
        selectedIds = new Set([...selectedIds].filter(id => validIds.has(id)));
    }

    function getFilteredRoster() {
        const query = searchQuery.toLowerCase().trim();
        return roster.filter(agent => {
            if (query) {
                const name = (agent.name || '').toLowerCase();
                const role = (agent.role || '').toLowerCase();
                if (!name.includes(query) && !role.includes(query)) return false;
            }
            if (activeFilter === 'active') return (agent.status || 'idle') !== 'idle';
            if (activeFilter === 'idle') return (agent.status || 'idle') === 'idle';
            return true;
        });
    }

    const formatRelativeTime = BossModUtils.formatRelativeTime;

    function buildFilterChip(label, value) {
        const isActive = activeFilter === value;
        const base = 'px-2.5 py-1 rounded-full text-[11px] font-medium cursor-pointer transition-colors border';
        const active = 'bg-bm-accent text-white border-bm-accent';
        const inactive = 'bg-white text-bm-muted border-bm-border hover:border-slate-300';
        return `<button type="button" class="directory-filter-chip ${base} ${isActive ? active : inactive}" data-filter="${value}">${BossModUtils.escapeHtml(label)}</button>`;
    }

    function updateSummary(container) {
        const el = container?.querySelector('#directory-summary');
        if (!el) return;
        const activeCount = roster.filter(a => (a.status || 'idle') !== 'idle').length;
        el.textContent = `${roster.length} agent${roster.length !== 1 ? 's' : ''} \u00b7 ${activeCount} active`;
    }

    async function render(container) {
        activeContainer = container;
        if (!container) return;
        container.innerHTML = `
            <div class="h-full flex flex-col">
                <div class="p-4 border-b border-bm-border shrink-0">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <p class="text-xs uppercase tracking-wide text-bm-muted">Directory</p>
                            <h3 class="text-sm font-semibold mt-1">Agent Directory</h3>
                            <p class="text-xs text-bm-muted mt-1">Select teammates and start a shared channel.</p>
                        </div>
                        <button id="company-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                    <input id="directory-search" type="text" placeholder="Search by name or role..."
                           class="mt-3 w-full px-3 py-1.5 rounded-lg border border-bm-border text-xs placeholder:text-bm-muted focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent transition-colors"
                           value="${BossModUtils.escapeHtml(searchQuery)}">
                    <div id="directory-filters" class="mt-2 flex items-center gap-1.5 flex-wrap">
                        ${buildFilterChip('All', 'all')}
                        ${buildFilterChip('Active', 'active')}
                        ${buildFilterChip('Idle', 'idle')}
                    </div>
                    <p id="directory-summary" class="mt-2 text-[11px] text-bm-muted"></p>
                    <div class="mt-2 flex items-center justify-between gap-3">
                        <p class="text-xs text-bm-muted"><span id="company-selected-count">${selectedIds.size}</span> selected</p>
                        <button id="company-create-channel-btn"
                                class="px-3 py-2 rounded-lg bg-bm-accent text-white text-xs font-medium hover:bg-bm-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                ${selectedIds.size ? '' : 'disabled'}>
                            Create Channel
                        </button>
                    </div>
                </div>
                <div id="company-roster-list" class="flex-1 overflow-y-auto p-4">
                    <div class="text-bm-muted text-sm text-center mt-8">Loading roster...</div>
                </div>
            </div>`;

        container.querySelector('#company-refresh-btn')?.addEventListener('click', () => {
            void render(container);
        });
        container.querySelector('#company-create-channel-btn')?.addEventListener('click', async () => {
            await createChannelFromSelection(container);
        });
        container.querySelector('#directory-search')?.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                searchQuery = e.target.value;
                renderRosterList(container.querySelector('#company-roster-list'));
            }, 300);
        });
        container.querySelectorAll('.directory-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                activeFilter = chip.dataset.filter;
                refreshFilterChips(container);
                renderRosterList(container.querySelector('#company-roster-list'));
            });
        });

        try {
            await loadRoster();
            updateSummary(container);
            renderRosterList(container.querySelector('#company-roster-list'));
        } catch (err) {
            console.error('[CompanyView] Failed to load roster:', err);
            const listEl = container.querySelector('#company-roster-list');
            if (listEl) {
                listEl.innerHTML = `
                    <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                        Failed to load agent directory.
                    </div>`;
            }
        }

        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function refreshFilterChips(container) {
        const filtersEl = container?.querySelector('#directory-filters');
        if (!filtersEl) return;
        filtersEl.innerHTML = [
            buildFilterChip('All', 'all'),
            buildFilterChip('Active', 'active'),
            buildFilterChip('Idle', 'idle'),
        ].join('');
        filtersEl.querySelectorAll('.directory-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                activeFilter = chip.dataset.filter;
                refreshFilterChips(container);
                renderRosterList(container.querySelector('#company-roster-list'));
            });
        });
    }

    function renderRosterList(listEl) {
        if (!listEl) return;
        const filtered = getFilteredRoster();

        if (!roster.length) {
            listEl.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-8">
                    <p>No agents yet.</p>
                </div>`;
            return;
        }

        if (!filtered.length) {
            listEl.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-8">
                    <p>No agents match your search.</p>
                </div>`;
            return;
        }

        listEl.innerHTML = filtered.map(agent => {
            const checked = selectedIds.has(agent.id) ? 'checked' : '';
            const status = agent.status || 'idle';
            const statusClasses = BossModUtils.getStatusClasses(status, agent.currentActivityKind);
            const statusLabel = BossModUtils.getStatusLabel(status, agent.currentActivityKind);
            const statusDot = BossModUtils.getStatusDot(status, agent.currentActivityKind);
            const isIdle = status === 'idle';
            const idleTime = isIdle ? formatRelativeTime(agent.idle_since) : '';
            const activityDesc = !isIdle && agent.currentActivityKind
                ? BossModUtils.escapeHtml(agent.currentActivityKind)
                : '';
            return `
                <div class="rounded-xl border border-bm-border bg-white p-3 mb-3 shadow-sm">
                    <div class="flex items-start gap-3">
                        <input type="checkbox"
                               class="company-agent-checkbox mt-1 rounded border-bm-border text-bm-accent focus:ring-bm-accent/30"
                               data-agent-id="${BossModUtils.escapeHtml(agent.id)}"
                               ${checked}>
                        <button type="button"
                                class="company-open-agent flex-1 min-w-0 text-left"
                                data-agent-id="${BossModUtils.escapeHtml(agent.id)}">
                            <div class="flex items-center gap-2 min-w-0">
                                <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${BossModUtils.escapeHtml(agent.color || '#3b82f6')}"></span>
                                <span class="font-medium truncate">${BossModUtils.escapeHtml(agent.name || 'Unknown')}</span>
                            </div>
                            <p class="text-xs text-bm-muted mt-1 truncate">${BossModUtils.escapeHtml(agent.role || 'No specialty')}</p>
                            ${agent.description ? `<p class="text-[11px] text-bm-muted mt-1 truncate" title="${BossModUtils.escapeHtml(agent.description)}">${BossModUtils.escapeHtml(agent.description)}</p>` : ''}
                            ${agent.done_fail_bar ? `<p class="text-[11px] text-bm-muted mt-1 truncate" title="${BossModUtils.escapeHtml(agent.done_fail_bar)}">${BossModUtils.escapeHtml(agent.done_fail_bar)}</p>` : ''}
                            <div class="mt-2 flex items-center flex-wrap gap-2">
                                <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${statusClasses}">
                                    <span class="w-1.5 h-1.5 rounded-full ${statusDot}"></span>
                                    ${BossModUtils.escapeHtml(statusLabel)}
                                </span>
                                ${activityDesc ? `<span class="text-[11px] text-bm-muted italic">${activityDesc}</span>` : ''}
                                ${idleTime ? `<span class="text-[11px] text-bm-muted">idle ${BossModUtils.escapeHtml(idleTime)}</span>` : ''}
                                <span class="text-[11px] text-bm-muted">${BossModUtils.escapeHtml(agent.location || 'Unknown')}</span>
                            </div>
                        </button>
                    </div>
                </div>`;
        }).join('');

        if (activeContainer) updateSummary(activeContainer);

        listEl.querySelectorAll('.company-agent-checkbox').forEach(input => {
            input.addEventListener('change', () => {
                const agentId = input.dataset.agentId;
                if (!agentId) return;
                if (input.checked) selectedIds.add(agentId);
                else selectedIds.delete(agentId);
                updateSelectionUi(listEl.closest('.flex.flex-col'));
            });
        });

        listEl.querySelectorAll('.company-open-agent').forEach(button => {
            button.addEventListener('click', async () => {
                const agentId = button.dataset.agentId;
                const agent = roster.find(item => item.id === agentId);
                if (!agent || typeof callbacks.onSelectAgent !== 'function') return;
                await callbacks.onSelectAgent(agent);
            });
        });

        if (window.lucide) lucide.createIcons({ nodes: [listEl] });
    }

    function updateSelectionUi(root) {
        const countEl = root?.querySelector('#company-selected-count');
        const createBtn = root?.querySelector('#company-create-channel-btn');
        if (countEl) countEl.textContent = String(selectedIds.size);
        if (createBtn) createBtn.disabled = selectedIds.size === 0;
    }

    async function createChannelFromSelection(root) {
        const agentIds = [...selectedIds];
        if (!agentIds.length) return;
        try {
            const res = await apiFetch('/api/channels', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_ids: agentIds }),
            });
            if (!res.ok) {
                throw new Error(await res.text());
            }
            const channel = await res.json();
            selectedIds.clear();
            updateSelectionUi(root);
            renderRosterList(root?.querySelector('#company-roster-list'));
            if (typeof callbacks.onOpenChannel === 'function') {
                callbacks.onOpenChannel(channel.id);
            }
        } catch (err) {
            console.error('[CompanyView] Failed to create channel:', err);
        }
    }

    return {
        render,
        setCallbacks,
        handleWorldUpdate,
    };
})();
