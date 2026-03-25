/**
 * BossMod AI — Company roster view.
 *
 * Renders the global company list, supports multi-select, and creates
 * shared channels from the selected agents.
 */

const CompanyView = (() => {
    let roster = [];
    let selectedIds = new Set();
    let activeContainer = null;
    let callbacks = {
        onSelectAgent: null,
        onOpenChannel: null,
    };

    function setCallbacks(next) {
        callbacks = { ...callbacks, ...(next || {}) };
    }

    async function loadRoster() {
        const res = await fetch('/api/company/agents', { cache: 'no-store' });
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

    async function render(container) {
        activeContainer = container;
        if (!container) return;
        container.innerHTML = `
            <div class="h-full flex flex-col">
                <div class="p-4 border-b border-bm-border shrink-0">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <p class="text-xs uppercase tracking-wide text-bm-muted">Company</p>
                            <h3 class="text-sm font-semibold mt-1">Agent Roster</h3>
                            <p class="text-xs text-bm-muted mt-1">Select teammates and start a shared channel.</p>
                        </div>
                        <button id="company-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                    <div class="mt-3 flex items-center justify-between gap-3">
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

        try {
            await loadRoster();
            renderRosterList(container.querySelector('#company-roster-list'));
        } catch (err) {
            console.error('[CompanyView] Failed to load roster:', err);
            const listEl = container.querySelector('#company-roster-list');
            if (listEl) {
                listEl.innerHTML = `
                    <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                        Failed to load company roster.
                    </div>`;
            }
        }

        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderRosterList(listEl) {
        if (!listEl) return;
        if (!roster.length) {
            listEl.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-8">
                    <p>No agents yet.</p>
                </div>`;
            return;
        }

        listEl.innerHTML = roster.map(agent => {
            const checked = selectedIds.has(agent.id) ? 'checked' : '';
            const statusClasses = BossModUtils.getStatusClasses(agent.status || 'idle', agent.currentActivityKind);
            const statusLabel = BossModUtils.getStatusLabel(agent.status || 'idle', agent.currentActivityKind);
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
                            <p class="text-xs text-bm-muted mt-1 truncate">${BossModUtils.escapeHtml(agent.role || 'No role')}</p>
                            <div class="mt-2 flex items-center flex-wrap gap-2">
                                <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${statusClasses}">
                                    <span class="w-1.5 h-1.5 rounded-full ${BossModUtils.getStatusDot(agent.status || 'idle', agent.currentActivityKind)}"></span>
                                    ${BossModUtils.escapeHtml(statusLabel)}
                                </span>
                                <span class="text-[11px] text-bm-muted">${BossModUtils.escapeHtml(agent.location || 'Unknown')}</span>
                            </div>
                        </button>
                    </div>
                </div>`;
        }).join('');

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
            const res = await fetch('/api/channels', {
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
