/**
 * BossMod AI — Company Task Detail Panel.
 * Renders the right-side detail view when a task is selected in the table.
 * All shared dependencies (statusColors, escapeHtml, etc.) are injected via init().
 */
const CompanyTaskDetail = (() => {
    let config = null;
    let panelEl = null;
    let currentTaskId = null;
    let events = [];
    let eventsLoading = false;
    let eventsFailed = false;
    let navigateCallback = null;
    let cancelCallback = null;

    const EVENT_TYPE_STYLES = {
        comment:       { badge: 'bg-blue-100 text-blue-700',      icon: 'message-circle' },
        clarification: { badge: 'bg-sky-100 text-sky-700',        icon: 'help-circle' },
        answer:        { badge: 'bg-sky-100 text-sky-700',        icon: 'message-square' },
        status_update: { badge: 'bg-amber-100 text-amber-700',    icon: 'refresh-cw' },
        blocker:       { badge: 'bg-red-100 text-red-700',        icon: 'alert-triangle' },
        completion:    { badge: 'bg-emerald-100 text-emerald-700', icon: 'check-circle' },
        assignment:    { badge: 'bg-purple-100 text-purple-700',  icon: 'user-check' },
        reprioritized: { badge: 'bg-amber-100 text-amber-700',    icon: 'arrow-up-down' },
        system:        { badge: 'bg-gray-100 text-gray-600',      icon: 'settings' },
    };

    const DEFAULT_EVENT_STYLE = { badge: 'bg-gray-100 text-gray-600', icon: 'info' };

    function isAgentDeskPath(virtualPath) {
        return virtualPath === '/me' || String(virtualPath || '').startsWith('/me/');
    }

    function renderDeliverableCard(d, task) {
        const esc = config.escapeHtml;
        const fileName = (d.path || '').split('/').pop() || d.path;
        const agentId = task && task.assigned_to ? task.assigned_to : '';
        return `
            <div class="ct-file-open flex items-center gap-2 px-2 py-1.5 rounded border border-slate-200 bg-white hover:border-blue-300 hover:bg-blue-50/30 cursor-pointer transition-colors text-xs"
                 data-path="${esc(d.path || '')}"
                 data-agent-id="${esc(agentId)}">
                <i data-lucide="file-text" class="w-3.5 h-3.5 text-blue-500 shrink-0"></i>
                <div class="flex-1 min-w-0">
                    <span class="font-medium text-blue-600 truncate block">${esc(fileName)}</span>
                    ${d.description ? `<span class="text-bm-muted block">${esc(d.description)}</span>` : ''}
                    <span class="text-[10px] text-bm-muted block truncate">${esc(d.path)}</span>
                </div>
                <i data-lucide="external-link" class="w-3 h-3 text-blue-400 shrink-0"></i>
            </div>`;
    }

    async function openDeliverablePath(path, agentId) {
        const target = String(path || '').trim();
        if (!target) return;
        if (isAgentDeskPath(target) && agentId && typeof CompanyFileViewer !== 'undefined') {
            await CompanyFileViewer.open(target, {
                apiUrl: `/api/agents/${encodeURIComponent(agentId)}/desk?path=${encodeURIComponent(target)}`,
            });
            return;
        }
        const res = await apiFetch(`/api/company/files?path=${encodeURIComponent(target)}`, { cache: 'no-store' });
        if (!res.ok) {
            throw new Error(await (res.text() || Promise.resolve('Could not open that path')));
        }
        const payload = await res.json();
        if (payload.kind === 'file') {
            if (typeof CompanyFileViewer !== 'undefined') {
                await CompanyFileViewer.open(payload.path || target);
            }
            return;
        }
        await apiFetch('/api/company/files/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: payload.path || target }),
        });
    }

    // ─── Public API ───

    function init(cfg) {
        config = cfg;
    }

    function setNavigateCallback(fn) {
        navigateCallback = fn;
    }

    function setCancelCallback(fn) {
        cancelCallback = fn;
    }

    function renderEmpty(el) {
        panelEl = el;
        currentTaskId = null;
        events = [];
        el.innerHTML = `
            <div class="flex flex-col items-center justify-center h-full text-bm-muted gap-2">
                <i data-lucide="clipboard-list" class="w-10 h-10 opacity-20"></i>
                <p class="text-sm">Select a task to view details</p>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [el] });
    }

    function renderDetail(el, task, allTasks) {
        panelEl = el;
        const esc = config.escapeHtml;
        const frt = config.formatRelativeTime;
        const colors = config.statusColors[task.status] || { dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600' };
        const children = allTasks.filter(t => t.parent_task_id === task.id);

        const isNewTask = currentTaskId !== task.id;
        currentTaskId = task.id;

        let html = `<div class="flex flex-col h-full overflow-y-auto">`;

        // ── Header ──
        html += `<div class="ct-detail-section">`;
        html += `<div class="flex items-start justify-between gap-2 mb-2">`;
        html += `<h2 class="text-base font-semibold text-bm-text leading-snug">${esc(task.title)}</h2>`;
        html += `<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold shrink-0 ${colors.badge}">${esc(task.status)}</span>`;
        html += `</div>`;

        const terminal = config.terminalStatuses instanceof Set
            ? config.terminalStatuses.has(task.status)
            : ['complete', 'cancelled', 'declined', 'abandoned'].includes(task.status);
        if (!terminal) {
            html += `<div class="mt-2">`;
            html += `<button type="button" id="ct-cancel-task-btn"
                    class="inline-flex items-center gap-1 px-2 py-1 rounded border border-rose-300 bg-rose-50 text-xs font-medium text-rose-800 hover:bg-rose-100 transition-colors">Cancel task</button>`;
            html += `</div>`;
        }

        // Metadata chips
        html += `<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-bm-muted">`;
        if (task.assigned_to_name || task.assigned_to) {
            html += `<span><span class="font-medium text-bm-text">Assigned:</span> ${esc(task.assigned_to_name || 'Unassigned')}</span>`;
        }
        if (task.owner_name || task.owner_id) {
            html += `<span><span class="font-medium text-bm-text">Owner:</span> ${esc(task.owner_name || 'Unknown')}</span>`;
        }
        if (task.requester_name || task.requester_id) {
            const rName = task.requester_name === '__human__' ? 'You' : (task.requester_name || 'Unknown');
            html += `<span><span class="font-medium text-bm-text">Requester:</span> ${esc(rName)}</span>`;
        }
        html += `</div>`;
        html += `</div>`;

        html += renderRoleContractSection(task);

        // ── Timestamps ──
        html += `<div class="ct-detail-section py-2">`;
        html += `<div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-bm-muted">`;
        if (task.created_at) {
            html += `<span>Created: ${esc(new Date(task.created_at).toLocaleString())}</span>`;
        }
        if (task.last_activity) {
            html += `<span>Updated: ${esc(frt(task.last_activity))} (${esc(new Date(task.last_activity).toLocaleString())})</span>`;
        }
        html += `</div></div>`;

        // ── Description / Completion ──
        if (task.description || task.completion_summary || task.status_note) {
            html += `<div class="ct-detail-section">`;
            if (task.description) {
                html += `<p class="text-sm text-bm-text whitespace-pre-wrap break-words leading-relaxed">${esc(task.description)}</p>`;
            }
            if (task.completion_summary) {
                html += `
                    <div class="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                        <div class="flex items-center gap-1.5 text-xs font-semibold text-emerald-700 mb-1">
                            <i data-lucide="check-circle-2" class="w-3.5 h-3.5"></i> Completion Summary
                        </div>
                        <p class="text-sm text-emerald-800 whitespace-pre-wrap break-words">${esc(task.completion_summary)}</p>
                    </div>`;
            }
            if (task.status_note) {
                html += `
                    <div class="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div class="text-[11px] font-semibold text-bm-muted mb-0.5">Status Note</div>
                        <p class="text-sm text-bm-text whitespace-pre-wrap break-words">${esc(task.status_note)}</p>
                    </div>`;
            }
            html += `</div>`;
        }

        // ── Deliverables (file links) — rolls up child deliverables into parent ──
        const deliverables = task.work_contract?.deliverables || [];
        const childDeliverableCount = children.reduce((sum, c) => sum + (c.work_contract?.deliverables || []).length, 0);
        const totalDeliverableCount = deliverables.length + childDeliverableCount;
        if (totalDeliverableCount > 0) {
            html += `<div class="ct-detail-section">`;
            html += `<div class="flex items-center gap-1.5 mb-2">`;
            html += `<i data-lucide="package" class="w-3.5 h-3.5 text-bm-muted"></i>`;
            html += `<span class="text-xs font-semibold text-bm-text">Deliverables (${totalDeliverableCount})</span>`;
            html += `</div>`;
            html += `<div class="space-y-1.5">`;
            if (deliverables.length > 0 && childDeliverableCount > 0) {
                html += `<div class="text-[11px] font-semibold text-bm-muted mb-1">This task</div>`;
            }
            for (const d of deliverables) {
                html += renderDeliverableCard(d, task);
            }
            for (const child of children) {
                const cDeliverables = child.work_contract?.deliverables || [];
                if (cDeliverables.length === 0) continue;
                html += `<div class="text-[11px] font-semibold text-bm-muted mt-3 mb-1">${esc(child.title)}</div>`;
                for (const d of cDeliverables) {
                    html += renderDeliverableCard(d, child);
                }
            }
            html += `</div></div>`;
        }

        // ── Low-priority fields ──
        const lowFields = [];
        if (task.project) lowFields.push({ label: 'Project', value: task.project });
        if (task.cost_ceiling != null) lowFields.push({ label: 'Cost Ceiling', value: String(task.cost_ceiling) });
        if (lowFields.length > 0) {
            html += `<div class="ct-detail-section py-2">`;
            html += `<div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-bm-muted">`;
            for (const f of lowFields) {
                html += `<span><span class="font-medium">${esc(f.label)}:</span> ${esc(f.value)}</span>`;
            }
            html += `</div></div>`;
        }

        // ── Parent link ──
        if (task.parent_task_id) {
            const parent = allTasks.find(t => t.id === task.parent_task_id);
            html += `<div class="ct-detail-section py-2">`;
            if (parent) {
                html += `<span class="text-xs text-bm-muted">Parent: </span>`;
                html += `<a class="ct-nav-link text-xs font-medium text-blue-600 hover:underline cursor-pointer" data-task-id="${esc(parent.id)}">${esc(parent.title)}</a>`;
            } else {
                html += `<span class="text-xs text-bm-muted">Parent task (not in current view)</span>`;
            }
            html += `</div>`;
        }

        // ── Subtasks ──
        if (children.length > 0) {
            html += `<div class="ct-detail-section">`;
            html += `<div class="flex items-center gap-1.5 mb-2">`;
            html += `<i data-lucide="git-branch" class="w-3.5 h-3.5 text-bm-muted"></i>`;
            html += `<span class="text-xs font-semibold text-bm-text">Subtasks (${children.length})</span>`;
            html += `</div>`;
            html += `<div class="space-y-1">`;
            for (const child of children) {
                const cc = config.statusColors[child.status] || { badge: 'bg-gray-100 text-gray-600' };
                html += `
                    <div class="ct-nav-link flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-50 cursor-pointer transition-colors" data-task-id="${esc(child.id)}">
                        <span class="text-sm truncate flex-1">${esc(child.title)}</span>
                        <span class="px-1.5 py-0.5 rounded-full text-[10px] font-medium shrink-0 ${cc.badge}">${esc(child.status)}</span>
                        ${child.assigned_to_name ? `<span class="text-[11px] text-bm-muted shrink-0">${esc(child.assigned_to_name)}</span>` : ''}
                    </div>`;
            }
            html += `</div></div>`;
        }

        // ── Activity thread placeholder ──
        html += `<div class="ct-detail-section flex-1">`;
        html += `<div class="flex items-center gap-1.5 mb-2">`;
        html += `<i data-lucide="message-square" class="w-3.5 h-3.5 text-bm-muted"></i>`;
        html += `<span class="text-xs font-semibold text-bm-text">Activity</span>`;
        html += `</div>`;
        html += `<div id="ct-activity-thread">`;
        html += renderActivityPlaceholder();
        html += `</div></div>`;

        html += `</div>`;
        el.innerHTML = html;
        bindNavLinks(el);
        bindCancelTask(el, task);
        if (window.lucide) lucide.createIcons({ nodes: [el] });

        if (isNewTask) {
            events = [];
            fetchEvents(task.id);
        } else {
            renderActivityContent();
        }
    }

    function destroy() {
        config = null;
        panelEl = null;
        currentTaskId = null;
        events = [];
        eventsLoading = false;
        eventsFailed = false;
        navigateCallback = null;
        cancelCallback = null;
    }

    // ─── Internal ───

    function resolveDoneClaim(task) {
        if (task.done_claim && typeof task.done_claim === 'object') return task.done_claim;
        const latest = task.latest_event;
        if (!latest || latest.event_type !== 'completion') return null;
        const content = latest.content || '';
        const marker = ' Claim: ';
        const markerAt = content.indexOf(marker);
        if (markerAt < 0) return null;
        const tail = content.slice(markerAt + marker.length).trim();
        if (!tail) return null;
        const parts = tail.split('—').map(part => part.trim()).filter(Boolean);
        const type = (parts[0] || '').toLowerCase();
        const claim = { type: ['artifact', 'tests', 'proof'].includes(type) ? type : 'proof' };
        if (parts[1] && (claim.type === 'artifact' || parts[1].startsWith('/'))) claim.path = parts[1];
        else if (parts[1]) claim.evidence = parts[1];
        if (parts[2]) claim.evidence = parts[2];
        return claim;
    }

    function renderRoleContractSection(task) {
        const esc = config.escapeHtml;
        const specialty = task.assigned_to_role || '';
        const doneBar = task.assigned_to_done_fail_bar || '';
        const guidance = BossModUtils.doneClaimGuidance(task);
        const claim = resolveDoneClaim(task);
        const claimLabel = BossModUtils.formatDoneClaim(claim);
        const showOpenGuidance = task.status !== 'complete' && Boolean(task.assigned_to);
        const showCompleteClaim = task.status === 'complete' && Boolean(claimLabel);
        if (!specialty && !doneBar && !showOpenGuidance && !showCompleteClaim) return '';

        let html = `<div class="ct-detail-section">`;
        html += `<div class="flex items-center gap-1.5 mb-2">`;
        html += `<i data-lucide="shield-check" class="w-3.5 h-3.5 text-bm-muted"></i>`;
        html += `<span class="text-xs font-semibold text-bm-text">Role contract</span>`;
        html += `</div>`;
        if (specialty || doneBar) {
            html += `<div class="text-xs text-bm-muted space-y-0.5 mb-2">`;
            if (specialty) {
                html += `<p><span class="font-medium text-bm-text">Specialty:</span> ${esc(specialty)}</p>`;
            }
            if (doneBar) {
                html += `<p><span class="font-medium text-bm-text">What done looks like:</span> ${esc(doneBar)}</p>`;
            }
            html += `</div>`;
        }
        if (showOpenGuidance) {
            html += `
                <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                    <div class="flex items-center gap-1.5 text-xs font-semibold text-amber-950 mb-1">
                        <i data-lucide="ban" class="w-3.5 h-3.5"></i> Blocked — checkable claim missing
                    </div>
                    <p class="text-[11px] text-amber-800 leading-relaxed">${esc(guidance)}</p>
                    <p class="text-[11px] text-amber-800 mt-1">What’s needed: tests evidence, an artifact path that exists, or an allow/deny proof. Empty done is rejected.</p>
                </div>`;
        } else if (showCompleteClaim) {
            html += `
                <div class="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                    <div class="flex items-center gap-1.5 text-xs font-semibold text-emerald-700 mb-1">
                        <i data-lucide="badge-check" class="w-3.5 h-3.5"></i> Done claim
                    </div>
                    <p class="text-sm text-emerald-800">${esc(claimLabel || 'Completed with a checkable claim.')}</p>
                </div>`;
        }
        html += `</div>`;
        return html;
    }

    function bindCancelTask(el, task) {
        el.querySelector('#ct-cancel-task-btn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            if (cancelCallback) cancelCallback(task);
        });
    }

    function bindNavLinks(el) {
        el.querySelectorAll('.ct-nav-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.stopPropagation();
                const taskId = link.dataset.taskId;
                if (taskId && navigateCallback) navigateCallback(taskId);
            });
        });
        el.querySelectorAll('.ct-file-open').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                btn.classList.remove('border-red-300');
                try {
                    await openDeliverablePath(btn.dataset.path, btn.dataset.agentId);
                } catch (err) {
                    btn.classList.add('border-red-300');
                    btn.title = (err && err.message) || 'Could not open that path';
                }
            });
        });
    }

    function renderActivityPlaceholder() {
        return `<div class="flex items-center gap-2 text-bm-muted py-3">
            <i data-lucide="loader" class="w-4 h-4 animate-spin opacity-40"></i>
            <span class="text-xs">Loading activity...</span>
        </div>`;
    }

    async function fetchEvents(taskId) {
        eventsLoading = true;
        eventsFailed = false;
        renderActivityContent();
        try {
            const res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/events`, { cache: 'no-store' });
            if (currentTaskId !== taskId) return;
            if (!res.ok) throw new Error(res.statusText);
            events = await res.json();
            if (!Array.isArray(events)) events = [];
            events.sort((a, b) => {
                const aAt = Date.parse(a?.created_at || '') || 0;
                const bAt = Date.parse(b?.created_at || '') || 0;
                if (bAt !== aAt) return bAt - aAt;
                return String(b?.id || '').localeCompare(String(a?.id || ''));
            });
        } catch {
            if (currentTaskId !== taskId) return;
            events = [];
            eventsFailed = true;
        }
        eventsLoading = false;
        renderActivityContent();
    }

    function renderActivityContent() {
        if (!panelEl) return;
        const threadEl = panelEl.querySelector('#ct-activity-thread');
        if (!threadEl) return;
        const esc = config.escapeHtml;
        const frt = config.formatRelativeTime;

        if (eventsLoading) {
            threadEl.innerHTML = renderActivityPlaceholder();
            if (window.lucide) lucide.createIcons({ nodes: [threadEl] });
            return;
        }

        if (eventsFailed) {
            threadEl.innerHTML = `
                <div class="flex items-center gap-2 text-xs text-red-500 py-2">
                    <i data-lucide="alert-circle" class="w-3.5 h-3.5 shrink-0"></i>
                    <span>Failed to load activity.</span>
                    <button type="button" class="ct-retry-events text-blue-600 hover:underline font-medium">Retry</button>
                </div>`;
            threadEl.querySelector('.ct-retry-events')?.addEventListener('click', () => fetchEvents(currentTaskId));
            if (window.lucide) lucide.createIcons({ nodes: [threadEl] });
            return;
        }

        if (events.length === 0) {
            threadEl.innerHTML = `<p class="text-xs text-bm-muted italic py-2">No activity recorded yet.</p>`;
            return;
        }

        let html = '';
        for (const evt of events) {
            const style = EVENT_TYPE_STYLES[evt.event_type] || DEFAULT_EVENT_STYLE;
            const typeLabel = (evt.event_type || 'event').replace(/_/g, ' ');
            html += `
                <div class="ct-event-entry">
                    <div class="flex items-center justify-between gap-2 mb-0.5">
                        <div class="flex items-center gap-1.5 min-w-0">
                            <i data-lucide="${style.icon}" class="w-3 h-3 shrink-0 opacity-60"></i>
                            <span class="text-xs font-medium text-bm-text truncate">${esc(evt.author_name || 'System')}</span>
                            <span class="px-1.5 py-0.5 rounded-full text-[9px] font-medium ${style.badge}">${esc(typeLabel)}</span>
                        </div>
                        ${evt.created_at ? `<span class="text-[10px] text-bm-muted shrink-0">${esc(frt(evt.created_at))}</span>` : ''}
                    </div>
                    <p class="text-xs text-bm-text/80 whitespace-pre-wrap break-words leading-relaxed pl-[18px]">${esc(evt.content)}</p>
                </div>`;
        }
        threadEl.innerHTML = html;
        if (window.lucide) lucide.createIcons({ nodes: [threadEl] });
    }

    return { init, renderDetail, renderEmpty, setNavigateCallback, setCancelCallback, destroy };
})();
