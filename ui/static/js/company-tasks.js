/**
 * BossMod AI — Company Tasks tab.
 * Sortable data table (left) + detail panel (right) for viewing all organization tasks.
 * Delegates detail rendering to CompanyTaskDetail via dependency injection.
 */
const CompanyTasks = (() => {
    let container = null;
    let tasks = [];
    let agents = [];
    let subtaskCountMap = new Map();
    let statusFilter = 'all';
    let agentFilter = null;
    let searchQuery = '';
    let selectedTaskId = null;
    let sortColumn = 'last_active';
    let sortDirection = 'desc';
    let searchTimer = null;
    let refreshTimer = null;
    let splitInstance = null;
    let initialized = false;
    let showChildren = false;

    const STATUS_COLORS = {
        active:    { dot: 'bg-green-500',   badge: 'bg-green-100 text-green-700' },
        pending:   { dot: 'bg-amber-400',   badge: 'bg-amber-100 text-amber-700' },
        accepted:  { dot: 'bg-blue-400',    badge: 'bg-blue-100 text-blue-700' },
        waiting:   { dot: 'bg-sky-500',     badge: 'bg-sky-100 text-sky-700' },
        complete:  { dot: 'bg-emerald-500', badge: 'bg-emerald-100 text-emerald-700' },
        stalled:   { dot: 'bg-red-500',     badge: 'bg-red-100 text-red-700' },
        blocked:   { dot: 'bg-orange-500',  badge: 'bg-orange-100 text-orange-700' },
        abandoned: { dot: 'bg-gray-400',    badge: 'bg-gray-100 text-gray-600' },
        delegated: { dot: 'bg-purple-400',  badge: 'bg-purple-100 text-purple-700' },
        declined:  { dot: 'bg-gray-400',    badge: 'bg-gray-100 text-gray-600' },
    };

    const STATUS_ORDER = {
        active: 0, pending: 1, accepted: 2, waiting: 3,
        blocked: 4, stalled: 5, delegated: 6,
        complete: 7, abandoned: 8, declined: 9,
    };

    const STATUS_FILTERS = ['all', 'active', 'pending', 'waiting', 'complete', 'stalled', 'blocked'];
    const DEFAULT_COLORS = { dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600' };
    const TABLE_COLUMNS = [
        { key: 'title',       label: 'Task',     cls: 'flex-1 text-left' },
        { key: 'assignee',    label: 'Assignee',  cls: 'w-[110px] text-left' },
        { key: 'subtasks',    label: 'Subs',      cls: 'w-[60px] text-center' },
        { key: 'last_active', label: 'Updated',   cls: 'w-[85px] text-right' },
    ];

    function getColors(status) { return STATUS_COLORS[status] || DEFAULT_COLORS; }

    function uniqueAgents() {
        const seen = new Map();
        for (const task of tasks) {
            if (task.assigned_to && task.assigned_to_name && !seen.has(task.assigned_to)) {
                seen.set(task.assigned_to, task.assigned_to_name);
            }
        }
        return Array.from(seen.entries())
            .map(([id, name]) => ({ id, name }))
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    function buildSubtaskCounts() {
        const counts = new Map();
        for (const t of tasks) {
            if (t.parent_task_id) counts.set(t.parent_task_id, (counts.get(t.parent_task_id) || 0) + 1);
        }
        return counts;
    }

    function filteredTasks() {
        return tasks.filter(task => {
            if (!showChildren && task.parent_task_id) return false;
            if (statusFilter !== 'all' && task.status !== statusFilter) return false;
            if (agentFilter && task.assigned_to !== agentFilter) return false;
            if (searchQuery) {
                const q = searchQuery.toLowerCase();
                if (!(task.title || '').toLowerCase().includes(q)) return false;
            }
            return true;
        });
    }

    function sortedFilteredTasks() {
        const rows = filteredTasks();
        if (!sortColumn) return rows;
        rows.sort((a, b) => {
            let va, vb;
            switch (sortColumn) {
                case 'title':
                    va = (a.title || '').toLowerCase();
                    vb = (b.title || '').toLowerCase();
                    break;
                case 'assignee':
                    va = (a.assigned_to_name || '').toLowerCase();
                    vb = (b.assigned_to_name || '').toLowerCase();
                    break;
                case 'subtasks':
                    va = subtaskCountMap.get(a.id) || 0;
                    vb = subtaskCountMap.get(b.id) || 0;
                    break;
                case 'last_active':
                    va = a.last_activity ? new Date(a.last_activity).getTime() : 0;
                    vb = b.last_activity ? new Date(b.last_activity).getTime() : 0;
                    break;
                default: return 0;
            }
            const cmp = va < vb ? -1 : va > vb ? 1 : 0;
            return sortDirection === 'asc' ? cmp : -cmp;
        });
        return rows;
    }

    function taskCounts() {
        const visible = showChildren ? tasks : tasks.filter(t => !t.parent_task_id);
        const counts = { total: visible.length, active: 0, pending: 0, waiting: 0, complete: 0, stalled: 0, blocked: 0 };
        for (const task of visible) {
            if (counts[task.status] !== undefined) counts[task.status]++;
        }
        return counts;
    }

    function buildDisplayOrder(sorted) {
        if (!showChildren) return sorted;
        const topLevel = [];
        const childrenByParent = new Map();
        for (const t of sorted) {
            if (t.parent_task_id) {
                if (!childrenByParent.has(t.parent_task_id)) childrenByParent.set(t.parent_task_id, []);
                childrenByParent.get(t.parent_task_id).push(t);
            } else {
                topLevel.push(t);
            }
        }
        const result = [];
        for (const parent of topLevel) {
            result.push(parent);
            const kids = childrenByParent.get(parent.id);
            if (kids) for (const child of kids) result.push(child);
        }
        return result;
    }

    // ─── Rendering ───

    function render(el) {
        container = el;
        if (!initialized) {
            CompanyTaskDetail.init({
                statusColors: STATUS_COLORS,
                statusOrder: STATUS_ORDER,
                escapeHtml: BossModUtils.escapeHtml,
                formatRelativeTime: BossModUtils.formatRelativeTime,
            });
            CompanyTaskDetail.setNavigateCallback(navigateToTask);
            initialized = true;
        }
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
            subtaskCountMap = buildSubtaskCounts();
            renderLayout();
        } catch (err) {
            console.error('[CompanyTasks] Load failed:', err);
            renderError();
        }
    }

    function renderLayout() {
        if (!container) return;
        const escape = BossModUtils.escapeHtml;
        const counts = taskCounts();

        let html = `<div class="flex flex-col h-full">`;

        // ── Header bar ──
        html += `
            <div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-bm-border shrink-0">
                <div class="flex items-center gap-2 min-w-0">
                    <i data-lucide="list-checks" class="w-4 h-4 text-bm-accent shrink-0"></i>
                    <h3 class="text-sm font-semibold truncate">Company Tasks</h3>
                    ${counts.active > 0 ? `<span class="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-green-100 text-green-700">${counts.active}</span>` : ''}
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <div class="relative">
                        <input type="text" id="ct-search-input" placeholder="Search tasks..."
                               value="${escape(searchQuery)}"
                               class="w-40 pl-7 pr-2 py-1 text-xs border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent">
                        <i data-lucide="search" class="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-bm-muted pointer-events-none"></i>
                    </div>
                    <button type="button" id="ct-refresh-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors" title="Refresh">
                        <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                    </button>
                </div>
            </div>`;

        // ── Filter row ──
        html += `
            <div class="flex items-center gap-2 px-4 py-2 border-b border-bm-border bg-slate-50/50 overflow-x-auto shrink-0">
                <div class="flex items-center gap-1 shrink-0">
                    ${STATUS_FILTERS.map(s => {
                        const label = s.charAt(0).toUpperCase() + s.slice(1);
                        const count = s === 'all' ? counts.total : (counts[s] || 0);
                        const isActive = statusFilter === s;
                        return `<button type="button" class="activity-chip ct-status-chip ${isActive ? 'active' : ''}"
                                    data-status="${escape(s)}">${escape(label)} <span class="ml-0.5 opacity-70">${count}</span></button>`;
                    }).join('')}
                </div>
                <div class="ml-auto flex items-center gap-2 shrink-0">
                    ${agents.length > 0 ? `
                        <select id="ct-agent-filter"
                                class="text-xs border border-bm-border rounded-lg px-2 py-1 bg-white focus:outline-none focus:border-bm-accent">
                            <option value="">All Agents</option>
                            ${agents.map(a => `<option value="${escape(a.id)}" ${agentFilter === a.id ? 'selected' : ''}>${escape(a.name)}</option>`).join('')}
                        </select>` : ''}
                    <label class="inline-flex items-center gap-1.5 text-xs text-bm-muted cursor-pointer select-none">
                        <input type="checkbox" id="ct-show-children"
                               class="rounded border-bm-border text-bm-accent focus:ring-bm-accent/30"
                               ${showChildren ? 'checked' : ''}>
                        Show child tasks
                    </label>
                </div>
            </div>`;

        // ── 2-column body (Split.js resizable) ──
        html += `<div id="ct-split-wrap" class="flex flex-1 min-h-0">`;

        // Left: table
        html += `<div id="ct-pane-table" class="flex flex-col min-h-0 min-w-0 bg-slate-50/60">`;

        // Table header
        html += `<div class="flex items-center px-1 shrink-0 bg-slate-50/80 border-b border-bm-border">`;
        html += `<div class="w-[32px] shrink-0"></div>`;
        for (const col of TABLE_COLUMNS) {
            const isSorted = sortColumn === col.key;
            const arrow = isSorted ? (sortDirection === 'asc' ? '↑' : '↓') : '↕';
            html += `<div class="bm-th ct-sort-header ${isSorted ? 'bm-sorted' : ''} ${col.cls}" data-sort="${col.key}">
                ${escape(col.label)} <span class="bm-sort-icon">${arrow}</span>
            </div>`;
        }
        html += `</div>`;

        // Table body (scrollable)
        html += `<div id="ct-table-body" class="flex-1 overflow-y-auto">`;
        html += renderTableRows();
        html += `</div>`;

        // Footer summary
        html += `
            <div class="px-4 py-2 border-t border-bm-border text-[11px] text-bm-muted bg-slate-50/50 shrink-0">
                ${counts.total} total &middot; ${counts.active} active &middot; ${counts.pending} pending &middot; ${counts.complete} complete
            </div>`;

        html += `</div>`;

        // Right: detail panel
        html += `<div id="ct-detail-panel" class="flex flex-col min-h-0 min-w-0 bg-white"></div>`;

        html += `</div></div>`;
        container.innerHTML = html;
        initSplit();
        bindInteractions();
        if (window.lucide) lucide.createIcons({ nodes: [container] });
        renderDetailPanel();
    }

    function initSplit() {
        if (!window.Split) return;
        const table = container.querySelector('#ct-pane-table');
        const detail = container.querySelector('#ct-detail-panel');
        if (!table || !detail) return;
        if (splitInstance) { splitInstance.destroy(); splitInstance = null; }
        splitInstance = Split(['#ct-pane-table', '#ct-detail-panel'], {
            sizes: [58, 42],
            minSize: [280, 240],
            gutterSize: 5,
            cursor: 'col-resize',
            elementStyle: (dim, size, gutter) => ({ 'flex-basis': `calc(${size}% - ${gutter}px)` }),
            gutterStyle: (dim, gutter) => ({ 'flex-basis': `${gutter}px` }),
        });
    }

    function renderTableRows() {
        const escape = BossModUtils.escapeHtml;
        const relTime = BossModUtils.formatRelativeTime;
        const sorted = buildDisplayOrder(sortedFilteredTasks());

        if (sorted.length === 0) {
            const msg = (statusFilter !== 'all' || agentFilter || searchQuery)
                ? 'No tasks match the current filters.' : 'No tasks found.';
            return `
                <div class="text-center py-8 text-bm-muted">
                    <i data-lucide="clipboard-list" class="w-8 h-8 mx-auto mb-2 opacity-30"></i>
                    <p class="text-sm">${escape(msg)}</p>
                </div>`;
        }

        let html = '';
        for (const task of sorted) {
            const colors = getColors(task.status);
            const isSelected = selectedTaskId === task.id;
            const isChild = !!task.parent_task_id;
            const subCount = subtaskCountMap.get(task.id) || 0;
            const timeText = relTime(task.last_activity);

            html += `
                <div class="ct-task-row ${isSelected ? 'ct-selected' : ''} ${isChild ? 'ct-child-row' : ''}" data-task-id="${escape(task.id)}">
                    <div class="flex items-center px-1 py-2">
                        <div class="w-[32px] flex justify-center shrink-0">
                            ${isChild ? `<span class="text-[11px] text-bm-muted/50 leading-none">↳</span>` : `<span class="w-2 h-2 rounded-full ${colors.dot}"></span>`}
                        </div>
                        <div class="flex-1 min-w-0 flex items-center gap-2 pr-2">
                            <span class="text-sm font-medium truncate">${escape(task.title)}</span>
                            <span class="px-1.5 py-0.5 rounded-full text-[10px] font-medium shrink-0 ${colors.badge}">${escape(task.status)}</span>
                        </div>
                        <div class="w-[110px] text-xs text-bm-muted truncate shrink-0">${escape(task.assigned_to_name || '')}</div>
                        <div class="w-[60px] text-center shrink-0">
                            ${subCount > 0 ? `<span class="ct-subtask-badge">${subCount}</span>` : ''}
                        </div>
                        <div class="w-[85px] text-[11px] text-bm-muted text-right shrink-0">${timeText ? escape(timeText) : ''}</div>
                    </div>
                </div>`;
        }
        return html;
    }

    function renderDetailPanel() {
        if (!container) return;
        const panelEl = container.querySelector('#ct-detail-panel');
        if (!panelEl) return;

        if (!selectedTaskId) {
            CompanyTaskDetail.renderEmpty(panelEl);
            return;
        }
        const task = tasks.find(t => t.id === selectedTaskId);
        if (!task) {
            CompanyTaskDetail.renderEmpty(panelEl);
            return;
        }
        // If the selected task is filtered out of the table, show empty detail
        const visible = filteredTasks();
        if (!visible.find(t => t.id === selectedTaskId)) {
            CompanyTaskDetail.renderEmpty(panelEl);
            return;
        }
        CompanyTaskDetail.renderDetail(panelEl, task, tasks);
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
                            class="mt-3 px-3 py-1.5 rounded border border-red-300 text-xs font-medium hover:bg-red-100 transition-colors">Retry</button>
                </div>
            </div>`;
        container.querySelector('#ct-retry-btn')?.addEventListener('click', () => fetchAndRender());
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    // ─── Interaction binding ───

    function bindInteractions() {
        if (!container) return;

        // Search
        const searchInput = container.querySelector('#ct-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    searchQuery = e.target.value.trim();
                    refreshTableBody();
                    const newInput = container?.querySelector('#ct-search-input');
                    if (newInput) { newInput.focus(); newInput.setSelectionRange(newInput.value.length, newInput.value.length); }
                }, 200);
            });
        }

        // Status chips
        container.querySelectorAll('.ct-status-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                statusFilter = btn.dataset.status;
                updateStatusChips();
                refreshTableBody();
            });
        });

        // Agent filter
        container.querySelector('#ct-agent-filter')?.addEventListener('change', (e) => {
            agentFilter = e.target.value || null;
            refreshTableBody();
        });

        // Show child tasks toggle
        container.querySelector('#ct-show-children')?.addEventListener('change', (e) => {
            showChildren = e.target.checked;
            if (!showChildren && selectedTaskId) {
                const selected = tasks.find(t => t.id === selectedTaskId);
                if (selected && selected.parent_task_id) selectedTaskId = null;
            }
            renderLayout();
        });

        // Sort headers
        container.querySelectorAll('.ct-sort-header').forEach(header => {
            header.addEventListener('click', () => handleSort(header.dataset.sort));
        });

        // Row clicks
        bindRowClicks();

        // Refresh
        container.querySelector('#ct-refresh-btn')?.addEventListener('click', () => fetchAndRender());
    }

    function bindRowClicks() {
        if (!container) return;
        container.querySelectorAll('.ct-task-row').forEach(row => {
            row.addEventListener('click', () => {
                const taskId = row.dataset.taskId;
                selectedTaskId = selectedTaskId === taskId ? null : taskId;
                updateRowSelection();
                renderDetailPanel();
            });
        });
    }

    function handleSort(col) {
        if (sortColumn === col) {
            if (sortDirection === 'asc') { sortDirection = 'desc'; }
            else { sortColumn = null; sortDirection = 'asc'; }
        } else {
            sortColumn = col;
            sortDirection = 'asc';
        }
        updateSortHeaders();
        refreshTableBody();
    }

    function updateStatusChips() {
        if (!container) return;
        container.querySelectorAll('.ct-status-chip').forEach(chip => {
            chip.classList.toggle('active', chip.dataset.status === statusFilter);
        });
    }

    function updateSortHeaders() {
        if (!container) return;
        container.querySelectorAll('.ct-sort-header').forEach(header => {
            const col = header.dataset.sort;
            const isSorted = sortColumn === col;
            header.classList.toggle('bm-sorted', isSorted);
            const icon = header.querySelector('.bm-sort-icon');
            if (icon) icon.textContent = isSorted ? (sortDirection === 'asc' ? '↑' : '↓') : '↕';
        });
    }

    function refreshTableBody() {
        if (!container) return;
        const bodyEl = container.querySelector('#ct-table-body');
        if (!bodyEl) return;
        bodyEl.innerHTML = renderTableRows();
        bindRowClicks();
        if (window.lucide) lucide.createIcons({ nodes: [bodyEl] });
    }

    function updateRowSelection() {
        if (!container) return;
        container.querySelectorAll('.ct-task-row').forEach(row => {
            row.classList.toggle('ct-selected', row.dataset.taskId === selectedTaskId);
        });
    }

    function scrollToRow(taskId) {
        container?.querySelector(`.ct-task-row[data-task-id="${taskId}"]`)
            ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function navigateToTask(taskId) {
        const target = tasks.find(t => t.id === taskId);
        if (target?.parent_task_id && !showChildren) {
            showChildren = true;
        }
        selectedTaskId = taskId;
        renderLayout();
        scrollToRow(taskId);
    }

    // ─── WebSocket event handler ───

    function handleTaskEvent() {
        if (!container) return;
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => refreshSilent(), 500);
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
            subtaskCountMap = buildSubtaskCounts();
            if (selectedTaskId && !tasks.find(t => t.id === selectedTaskId)) {
                selectedTaskId = null;
            }
            renderLayout();
        } catch {
            // Silent failure — keep existing data
        }
    }

    // ─── Cleanup ───

    function destroy() {
        clearTimeout(searchTimer);
        clearTimeout(refreshTimer);
        if (splitInstance) { splitInstance.destroy(); splitInstance = null; }
        CompanyTaskDetail.destroy();
        container = null;
        tasks = [];
        agents = [];
        subtaskCountMap = new Map();
        statusFilter = 'all';
        agentFilter = null;
        searchQuery = '';
        selectedTaskId = null;
        sortColumn = null;
        sortDirection = 'asc';
        showChildren = false;
        initialized = false;
    }

    return { render, handleTaskEvent, destroy };
})();
