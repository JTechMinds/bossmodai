/**
 * BossMod AI — Company Tasks tab.
 * Displays all tasks across the organization with filtering, search, and status tracking.
 * Supports real-time updates via WebSocket activity events.
 */
const CompanyTasks = (() => {
    let container = null;
    let tasks = [];
    let agents = [];
    let statusFilter = 'all';
    let agentFilter = null;
    let searchQuery = '';
    let expandedTaskId = null;
    let searchTimer = null;
    let refreshTimer = null;

    const STATUS_COLORS = {
        active:    { dot: 'bg-green-500',   badge: 'bg-green-100 text-green-700',     row: 'bg-amber-50/50' },
        pending:   { dot: 'bg-amber-400',   badge: 'bg-amber-100 text-amber-700',     row: '' },
        accepted:  { dot: 'bg-blue-400',    badge: 'bg-blue-100 text-blue-700',       row: '' },
        complete:  { dot: 'bg-emerald-500', badge: 'bg-emerald-100 text-emerald-700', row: '' },
        stalled:   { dot: 'bg-red-500',     badge: 'bg-red-100 text-red-700',         row: 'bg-red-50/50' },
        blocked:   { dot: 'bg-orange-500',  badge: 'bg-orange-100 text-orange-700',   row: '' },
        abandoned: { dot: 'bg-gray-400',    badge: 'bg-gray-100 text-gray-600',       row: '' },
        delegated: { dot: 'bg-purple-400',  badge: 'bg-purple-100 text-purple-700',   row: '' },
        declined:  { dot: 'bg-gray-400',    badge: 'bg-gray-100 text-gray-600',       row: '' },
    };

    const STATUS_FILTERS = ['all', 'active', 'pending', 'complete', 'stalled', 'blocked'];

    const DEFAULT_COLORS = { dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600', row: '' };

    // ─── Helpers ───

    function getColors(status) {
        return STATUS_COLORS[status] || DEFAULT_COLORS;
    }

    const formatRelativeTime = BossModUtils.formatRelativeTime;

    function uniqueAgents() {
        const seen = new Map(); // id → display name
        for (const task of tasks) {
            const id = task.assigned_to;
            if (id && !seen.has(id)) {
                seen.set(id, task.assigned_to_name || id);
            }
        }
        return Array.from(seen.entries())
            .map(([id, name]) => ({ id, name }))
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    function filteredTasks() {
        return tasks.filter(task => {
            if (statusFilter !== 'all' && task.status !== statusFilter) return false;
            if (agentFilter && task.assigned_to !== agentFilter) return false;
            if (searchQuery) {
                const q = searchQuery.toLowerCase();
                const title = (task.title || '').toLowerCase();
                if (!title.includes(q)) return false;
            }
            return true;
        });
    }

    function taskCounts() {
        const counts = { total: tasks.length, active: 0, pending: 0, complete: 0, stalled: 0, blocked: 0 };
        for (const task of tasks) {
            if (counts[task.status] !== undefined) counts[task.status]++;
        }
        return counts;
    }

    // ─── Rendering ───

    function render(el) {
        container = el;
        fetchAndRender();
    }

    async function fetchAndRender() {
        if (!container) return;
        container.innerHTML = `
            <div class="p-6 text-center text-bm-muted">
                <i data-lucide="loader" class="w-6 h-6 mx-auto mb-2 opacity-40 animate-spin"></i>
                <p class="text-sm">Loading tasks...</p>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });

        try {
            const res = await fetch('/api/tasks', { cache: 'no-store' });
            if (!res.ok) throw new Error(await res.text());
            tasks = await res.json();
            if (!Array.isArray(tasks)) tasks = [];
            agents = uniqueAgents();
            renderTaskBoard();
        } catch (err) {
            console.error('[CompanyTasks] Load failed:', err);
            renderError();
        }
    }

    function renderTaskBoard() {
        if (!container) return;
        const esc = BossModUtils.escapeHtml;
        const filtered = filteredTasks();
        const counts = taskCounts();
        const activeCount = counts.active;

        let html = `<div class="flex flex-col h-full">`;

        // Header bar
        html += `
            <div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-bm-border">
                <div class="flex items-center gap-2 min-w-0">
                    <i data-lucide="list-checks" class="w-4 h-4 text-bm-accent shrink-0"></i>
                    <h3 class="text-sm font-semibold truncate">Company Tasks</h3>
                    ${activeCount > 0 ? `<span class="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-green-100 text-green-700">${activeCount}</span>` : ''}
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <div class="relative">
                        <input type="text" id="ct-search-input" placeholder="Search tasks..."
                               value="${esc(searchQuery)}"
                               class="w-40 pl-7 pr-2 py-1 text-xs border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent">
                        <i data-lucide="search" class="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-bm-muted pointer-events-none"></i>
                    </div>
                    <button type="button" id="ct-refresh-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors"
                            title="Refresh">
                        <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                    </button>
                </div>
            </div>`;

        // Filter row
        html += `
            <div class="flex items-center gap-2 px-4 py-2 border-b border-bm-border bg-slate-50/50 overflow-x-auto">
                <div class="flex items-center gap-1 shrink-0">
                    ${STATUS_FILTERS.map(s => {
                        const label = s.charAt(0).toUpperCase() + s.slice(1);
                        const count = s === 'all' ? counts.total : (counts[s] || 0);
                        const isActive = statusFilter === s;
                        return `<button type="button"
                                    class="activity-chip ct-status-chip ${isActive ? 'active' : ''}"
                                    data-status="${esc(s)}">${esc(label)} <span class="ml-0.5 opacity-70">${count}</span></button>`;
                    }).join('')}
                </div>
                ${agents.length > 0 ? `
                    <div class="ml-auto shrink-0">
                        <select id="ct-agent-filter"
                                class="text-xs border border-bm-border rounded-lg px-2 py-1 bg-white focus:outline-none focus:border-bm-accent">
                            <option value="">All Agents</option>
                            ${agents.map(a => `<option value="${esc(a.id)}" ${agentFilter === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
                        </select>
                    </div>` : ''}
            </div>`;

        // Task list
        html += `<div class="flex-1 overflow-y-auto">`;
        if (filtered.length === 0) {
            const message = (statusFilter !== 'all' || agentFilter || searchQuery)
                ? 'No tasks match the current filters.'
                : 'No tasks found.';
            html += `
                <div class="text-center py-8 text-bm-muted">
                    <i data-lucide="clipboard-list" class="w-8 h-8 mx-auto mb-2 opacity-30"></i>
                    <p class="text-sm">${esc(message)}</p>
                </div>`;
        } else {
            html += `<div class="divide-y divide-bm-border">`;
            for (const task of filtered) {
                html += renderTaskRow(task);
            }
            html += `</div>`;
        }
        html += `</div>`;

        // Footer summary
        html += `
            <div class="px-4 py-2 border-t border-bm-border text-[11px] text-bm-muted bg-slate-50/50">
                ${counts.total} total &middot; ${counts.active} active &middot; ${counts.pending} pending &middot; ${counts.complete} complete
            </div>`;

        html += `</div>`;
        container.innerHTML = html;
        bindInteractions();
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderTaskRow(task) {
        const esc = BossModUtils.escapeHtml;
        const colors = getColors(task.status);
        const isExpanded = expandedTaskId === task.id;
        const timeText = formatRelativeTime(task.last_activity);

        let html = `
            <div class="ct-task-row cursor-pointer transition-colors hover:bg-slate-50 ${colors.row}"
                 data-task-id="${esc(task.id)}">
                <div class="flex items-center gap-3 px-4 py-2.5">
                    <span class="w-2 h-2 rounded-full shrink-0 ${colors.dot}"></span>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="text-sm font-medium truncate">${esc(task.title)}</span>
                            <span class="px-1.5 py-0.5 rounded-full text-[10px] font-medium shrink-0 ${colors.badge}">${esc(task.status)}</span>
                        </div>
                        <div class="flex items-center gap-2 mt-0.5 text-[11px] text-bm-muted">
                            ${task.assigned_to ? `<span>${esc(task.assigned_to_name || task.assigned_to)}</span>` : ''}
                            ${task.assigned_to && task.project ? '<span>&middot;</span>' : ''}
                            ${task.project ? `<span class="truncate">${esc(task.project)}</span>` : ''}
                        </div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        ${timeText ? `<span class="text-[11px] text-bm-muted">${esc(timeText)}</span>` : ''}
                        <i data-lucide="chevron-right" class="w-3.5 h-3.5 text-bm-muted expand-icon ${isExpanded ? 'rotate-90' : ''}"></i>
                    </div>
                </div>`;

        if (isExpanded) {
            html += renderTaskDetail(task);
        }

        html += `</div>`;
        return html;
    }

    function renderTaskDetail(task) {
        const esc = BossModUtils.escapeHtml;
        const fields = [];

        if (task.description) {
            fields.push({ label: 'Description', value: task.description });
        }
        if (task.completion_summary) {
            fields.push({ label: 'Completion Summary', value: task.completion_summary });
        }
        if (task.status_note) {
            fields.push({ label: 'Status Note', value: task.status_note });
        }
        if (task.created_at) {
            fields.push({ label: 'Created', value: new Date(task.created_at).toLocaleString() });
        }
        if (task.last_activity) {
            fields.push({ label: 'Last Activity', value: new Date(task.last_activity).toLocaleString() });
        }
        if (task.assigned_to) {
            fields.push({ label: 'Assigned To', value: task.assigned_to_name || task.assigned_to });
        }
        if (task.owner_id) {
            fields.push({ label: 'Owner', value: task.owner_name || task.owner_id });
        }
        if (task.requester_id) {
            fields.push({ label: 'Requester', value: task.requester_name || task.requester_id });
        }
        if (task.project) {
            fields.push({ label: 'Project', value: task.project });
        }
        if (task.parent_task_id) {
            fields.push({ label: 'Parent Task', value: task.parent_task_id });
        }

        if (fields.length === 0) {
            return `
                <div class="activity-detail px-4 pb-3 pl-9">
                    <p class="text-xs text-bm-muted italic">No additional details available.</p>
                </div>`;
        }

        let html = `<div class="activity-detail px-4 pb-3 pl-9"><div class="space-y-1.5">`;
        for (const field of fields) {
            html += `
                <div class="text-xs">
                    <span class="font-medium text-bm-muted">${esc(field.label)}:</span>
                    <span class="text-bm-text ml-1 whitespace-pre-wrap break-words">${esc(field.value)}</span>
                </div>`;
        }
        html += `</div></div>`;
        return html;
    }

    function renderError() {
        if (!container) return;
        container.innerHTML = `
            <div class="p-4">
                <div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    <div class="flex items-center gap-2 mb-2">
                        <i data-lucide="alert-circle" class="w-4 h-4 shrink-0"></i>
                        <span class="font-medium">Failed to load tasks</span>
                    </div>
                    <p class="text-xs text-red-600">Check the server connection and try again.</p>
                    <button type="button" id="ct-retry-btn"
                            class="mt-3 px-3 py-1.5 rounded border border-red-300 text-xs font-medium hover:bg-red-100 transition-colors">
                        Retry
                    </button>
                </div>
            </div>`;
        container.querySelector('#ct-retry-btn')?.addEventListener('click', () => fetchAndRender());
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    // ─── Interaction binding ───

    function bindInteractions() {
        if (!container) return;

        // Search input with debounce
        const searchInput = container.querySelector('#ct-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    searchQuery = e.target.value.trim();
                    renderTaskBoard();
                    const newInput = container?.querySelector('#ct-search-input');
                    if (newInput) {
                        newInput.focus();
                        newInput.setSelectionRange(newInput.value.length, newInput.value.length);
                    }
                }, 200);
            });
        }

        // Status filter chips
        container.querySelectorAll('.ct-status-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                statusFilter = btn.dataset.status;
                renderTaskBoard();
            });
        });

        // Agent filter dropdown
        const agentSelect = container.querySelector('#ct-agent-filter');
        if (agentSelect) {
            agentSelect.addEventListener('change', (e) => {
                agentFilter = e.target.value || null;
                renderTaskBoard();
            });
        }

        // Task row expand/collapse
        container.querySelectorAll('.ct-task-row').forEach(row => {
            row.addEventListener('click', () => {
                const taskId = row.dataset.taskId;
                expandedTaskId = expandedTaskId === taskId ? null : taskId;
                renderTaskBoard();
            });
        });

        // Refresh
        container.querySelector('#ct-refresh-btn')?.addEventListener('click', () => {
            fetchAndRender();
        });
    }

    // ─── WebSocket event handler ───

    function handleTaskEvent(data) {
        if (!container) return;
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
            refreshSilent();
        }, 500);
    }

    async function refreshSilent() {
        if (!container) return;
        try {
            const res = await fetch('/api/tasks', { cache: 'no-store' });
            if (!res.ok) return;
            const newTasks = await res.json();
            if (!Array.isArray(newTasks)) return;
            tasks = newTasks;
            agents = uniqueAgents();
            renderTaskBoard();
        } catch {
            // Silent refresh failure — keep existing data
        }
    }

    // ─── Cleanup ───

    function destroy() {
        clearTimeout(searchTimer);
        clearTimeout(refreshTimer);
        container = null;
        tasks = [];
        agents = [];
        statusFilter = 'all';
        agentFilter = null;
        searchQuery = '';
        expandedTaskId = null;
    }

    return { render, handleTaskEvent, destroy };
})();
