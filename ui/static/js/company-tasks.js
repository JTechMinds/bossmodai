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
    let rosterAgents = [];
    let assignFormOpen = false;
    let assignTitle = '';
    let assignDescription = '';
    let assignAgentId = '';
    let assignSubmitting = false;
    let assignResult = null;

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
            await loadRosterAgents();
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
                    <button type="button" id="ct-assign-toggle"
                            class="inline-flex items-center gap-1 px-2 py-1 rounded border border-bm-accent/40 bg-bm-accent/10 text-xs font-medium text-bm-accent hover:bg-bm-accent/15 transition-colors"
                            title="Assign a task">
                        <i data-lucide="plus" class="w-3 h-3"></i>
                        Assign Task
                    </button>
                    <button type="button" id="ct-refresh-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors" title="Refresh">
                        <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                    </button>
                </div>
            </div>`;
        html += renderAssignPanel();

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
        bindAssignForm();
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

    // ─── Assign Task form ───

    async function loadRosterAgents() {
        try {
            const res = await fetch('/api/agents', { cache: 'no-store' });
            if (!res.ok) return;
            const listed = await res.json();
            rosterAgents = Array.isArray(listed)
                ? listed.slice().sort((a, b) => (a.name || '').localeCompare(b.name || ''))
                : [];
        } catch {
            // Keep the last successful roster; the form still works with an empty list.
        }
    }

    function renderAssignPanel() {
        const escape = BossModUtils.escapeHtml;
        if (!assignFormOpen) return '';

        const agentOptions = rosterAgents.map(agent => (
            `<option value="${escape(agent.id)}" ${assignAgentId === agent.id ? 'selected' : ''}>${escape(agent.name)}${agent.role ? ` — ${escape(agent.role)}` : ''}</option>`
        )).join('');

        return `
            <div id="ct-assign-panel" class="px-4 py-3 border-b border-bm-border bg-white shrink-0">
                <form id="ct-assign-form" class="space-y-2">
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        <label class="block min-w-0">
                            <span class="block text-[11px] font-medium text-bm-muted mb-1">Title</span>
                            <input type="text" id="ct-assign-title" required maxlength="200"
                                   value="${escape(assignTitle)}"
                                   placeholder="What should they work on?"
                                   class="w-full px-2.5 py-1.5 text-sm border border-bm-border rounded-lg bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent">
                        </label>
                        <label class="block min-w-0">
                            <span class="block text-[11px] font-medium text-bm-muted mb-1">Assignee</span>
                            <select id="ct-assign-agent"
                                    class="w-full px-2.5 py-1.5 text-sm border border-bm-border rounded-lg bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent">
                                <option value="">Unassigned backlog</option>
                                ${agentOptions}
                            </select>
                        </label>
                    </div>
                    <label class="block">
                        <span class="block text-[11px] font-medium text-bm-muted mb-1">Description <span class="font-normal">(optional)</span></span>
                        <textarea id="ct-assign-description" rows="2" maxlength="4000"
                                  placeholder="Context, constraints, or the expected deliverable"
                                  class="w-full px-2.5 py-1.5 text-sm border border-bm-border rounded-lg bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent resize-y">${escape(assignDescription)}</textarea>
                    </label>
                    <div class="flex items-center justify-between gap-2">
                        <p class="text-[11px] text-bm-muted">Same title + assignee reuses an open workstream instead of creating a duplicate.</p>
                        <div class="flex items-center gap-2 shrink-0">
                            <button type="button" id="ct-assign-cancel"
                                    class="px-2.5 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">Cancel</button>
                            <button type="submit" id="ct-assign-submit" ${assignSubmitting ? 'disabled' : ''}
                                    class="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-bm-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-colors">
                                <i data-lucide="send" class="w-3 h-3"></i>
                                ${assignSubmitting ? 'Assigning…' : 'Assign'}
                            </button>
                        </div>
                    </div>
                </form>
                ${renderAssignResult()}
            </div>`;
    }

    function resolveAssigneeName(task) {
        if (!task) return '';
        if (task.assigned_to_name) return task.assigned_to_name;
        const match = rosterAgents.find(agent => agent.id === task.assigned_to);
        return match ? match.name : '';
    }

    function renderAssignResult() {
        const escape = BossModUtils.escapeHtml;
        if (!assignResult) return '';

        if (assignResult.error) {
            return `
                <div class="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                    ${escape(assignResult.error)}
                </div>`;
        }

        if (assignResult.outcome === 'create_new_task') {
            const task = assignResult.task || {};
            const assignee = resolveAssigneeName(task) || (task.assigned_to ? 'the assignee' : 'the unassigned backlog');
            return `
                <div class="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
                    Created <span class="font-semibold">${escape(task.title || 'task')}</span> and added it to ${escape(assignee)}.
                    ${task.assigned_to ? ' The assignee was notified.' : ''}
                </div>`;
        }

        if (assignResult.outcome === 'bind_existing_task') {
            const task = assignResult.task || {};
            return `
                <div class="mt-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800">
                    Reused the existing open task <span class="font-semibold">${escape(task.title || 'task')}</span>
                    instead of creating a duplicate.
                    ${task.assigned_to ? ' The assignee was notified again.' : ''}
                </div>`;
        }

        if (assignResult.outcome === 'clarify_ambiguous_match') {
            const candidates = Array.isArray(assignResult.candidates) ? assignResult.candidates : [];
            const reason = assignResult.reason || 'Multiple open tasks match this title.';
            const rows = candidates.length === 0
                ? `<p class="text-[11px] text-amber-800">No candidate IDs were returned. Change the title to create a new workstream.</p>`
                : candidates.map(candidate => `
                    <div class="flex items-center justify-between gap-2 py-1.5 border-t border-amber-200 first:border-t-0">
                        <button type="button" class="ct-clarify-view text-left min-w-0"
                                data-task-id="${escape(candidate.id)}">
                            <span class="block text-xs font-medium text-amber-950 truncate">${escape(candidate.title || 'Untitled')}</span>
                            <span class="block text-[11px] text-amber-800 truncate">
                                ${escape(candidate.assigned_to_name || 'Unassigned')} · ${escape(candidate.status || '')} · ${escape(candidate.id)}
                            </span>
                        </button>
                        <button type="button" class="ct-clarify-reuse shrink-0 px-2 py-1 rounded border border-amber-300 bg-white text-[11px] font-medium text-amber-900 hover:bg-amber-100"
                                data-task-id="${escape(candidate.id)}">Reuse this</button>
                    </div>`).join('');
            return `
                <div class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                    <p class="text-xs font-medium text-amber-950 mb-1">Need a clarification — no new task was created</p>
                    <p class="text-[11px] text-amber-800 mb-1">${escape(reason)} Pick one to reuse, or change the title to create a distinct workstream.</p>
                    ${rows}
                </div>`;
        }

        return `
            <div class="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-bm-muted">
                Unexpected outcome: ${escape(assignResult.outcome || 'unknown')}
            </div>`;
    }

    function bindAssignForm() {
        if (!container) return;

        container.querySelector('#ct-assign-toggle')?.addEventListener('click', () => {
            assignFormOpen = !assignFormOpen;
            if (!assignFormOpen) assignResult = null;
            renderLayout();
        });

        const titleInput = container.querySelector('#ct-assign-title');
        const descInput = container.querySelector('#ct-assign-description');
        const agentSelect = container.querySelector('#ct-assign-agent');
        titleInput?.addEventListener('input', (e) => { assignTitle = e.target.value; });
        descInput?.addEventListener('input', (e) => { assignDescription = e.target.value; });
        agentSelect?.addEventListener('change', (e) => { assignAgentId = e.target.value; });

        container.querySelector('#ct-assign-cancel')?.addEventListener('click', () => {
            assignFormOpen = false;
            assignResult = null;
            renderLayout();
        });

        container.querySelector('#ct-assign-form')?.addEventListener('submit', (e) => {
            e.preventDefault();
            submitAssignForm();
        });

        container.querySelectorAll('.ct-clarify-view').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.dataset.taskId) navigateToTask(btn.dataset.taskId);
            });
        });
        container.querySelectorAll('.ct-clarify-reuse').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.dataset.taskId) submitAssignForm({ bindTaskId: btn.dataset.taskId });
            });
        });
    }

    async function submitAssignForm({ bindTaskId } = {}) {
        const title = (assignTitle || '').trim();
        if (!title) {
            assignResult = { error: 'Title is required.' };
            renderLayout();
            container?.querySelector('#ct-assign-title')?.focus();
            return;
        }

        assignSubmitting = true;
        assignResult = null;
        renderLayout();

        const payload = {
            title,
            description: (assignDescription || '').trim() || null,
        };
        if (assignAgentId) payload.assigned_to = assignAgentId;
        if (bindTaskId) payload.bind_task_id = bindTaskId;

        try {
            const res = await fetch('/api/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let body = null;
            try { body = await res.json(); } catch { body = null; }
            if (!body || typeof body !== 'object') {
                assignResult = { error: res.ok ? 'The server returned an empty response.' : `Assign failed (${res.status}).` };
                return;
            }
            if (body.detail && !body.outcome) {
                const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
                assignResult = { error: detail };
                return;
            }
            assignResult = {
                outcome: body.outcome,
                task: body.task,
                candidates: body.candidates || [],
                reason: body.reason || null,
            };
            if (body.task && body.task.id && body.outcome !== 'clarify_ambiguous_match') {
                selectedTaskId = body.task.id;
                await refreshSilent();
                return;
            }
        } catch (err) {
            assignResult = { error: err && err.message ? err.message : 'Assign failed.' };
        } finally {
            assignSubmitting = false;
            if (container) renderLayout();
        }
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
            await loadRosterAgents();
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
        rosterAgents = [];
        assignFormOpen = false;
        assignTitle = '';
        assignDescription = '';
        assignAgentId = '';
        assignSubmitting = false;
        assignResult = null;
    }

    return { render, handleTaskEvent, destroy };
})();
