/**
 * BossMod AI — Unified Activity Feed.
 *
 * Merges activity_log, runtime activities, and notifications into a single
 * chronological, filterable, expandable feed. Receives real-time updates
 * via WebSocket and supports paginated history via REST.
 */

const ActivityLog = (() => {

    // ─── State ───

    let feedContainer = null;
    let filtersContainer = null;

    const state = {
        entries: [],
        filters: { search: '', categories: [], agentName: null },
        expandedIds: new Set(),
        offset: 0,
        hasMore: false,
        loading: false,
        agents: [],
    };

    const PAGE_SIZE = 50;
    const DEBOUNCE_MS = 300;

    // ─── Icon / color mappings ───

    const CATEGORY_DEFAULTS = {
        agent:  { icon: 'users',        color: 'text-emerald-500' },
        task:   { icon: 'check-circle',  color: 'text-blue-500' },
        error:  { icon: 'alert-circle',  color: 'text-red-500' },
        system: { icon: 'info',          color: 'text-bm-muted' },
    };

    const EVENT_ICONS = {
        agent_created:  { icon: 'user-plus',      color: 'text-emerald-500' },
        agent_updated:  { icon: 'edit-3',          color: 'text-blue-500' },
        agent_deleted:  { icon: 'user-minus',      color: 'text-red-500' },
        agent_moved:    { icon: 'move',            color: 'text-amber-500' },
        status_changed: { icon: 'activity',        color: 'text-purple-500' },
        task_created:   { icon: 'plus-circle',     color: 'text-emerald-500' },
        task_updated:   { icon: 'check-circle',    color: 'text-blue-500' },
        task_stalled:   { icon: 'alert-triangle',  color: 'text-amber-500' },
        message_sent:   { icon: 'message-circle',  color: 'text-blue-500' },
        connected:      { icon: 'wifi',            color: 'text-emerald-500' },
        disconnected:   { icon: 'wifi-off',        color: 'text-red-500' },
        work:           { icon: 'briefcase',       color: 'text-blue-500' },
        assignment:     { icon: 'clipboard',       color: 'text-blue-500' },
        meeting:        { icon: 'users',           color: 'text-purple-500' },
        conversation:   { icon: 'message-square',  color: 'text-indigo-500' },
        movement:       { icon: 'navigation',      color: 'text-amber-500' },
        social:         { icon: 'coffee',          color: 'text-pink-500' },
        break:          { icon: 'pause-circle',    color: 'text-bm-muted' },
        completion:     { icon: 'check-circle',    color: 'text-emerald-500' },
        handoff:        { icon: 'share-2',         color: 'text-blue-500' },
        blocked:        { icon: 'x-circle',        color: 'text-red-500' },
        abandoned:      { icon: 'trash-2',         color: 'text-red-500' },
        receipt:        { icon: 'inbox',           color: 'text-bm-muted' },
        application_reseeded: { icon: 'database',  color: 'text-purple-500' },
        world_feedback: { icon: 'alert-triangle', color: 'text-amber-500' },
    };

    // ─── Initialization ───

    function init() {
        filtersContainer = document.getElementById('activity-filters');
        feedContainer = document.getElementById('activity-feed');
        if (!feedContainer) return;
        renderFilters();
        renderEmptyState();
        console.log('[ActivityLog] Initialized');
    }

    // ─── Filter UI ───

    function renderFilters() {
        if (!filtersContainer) return;

        const categories = ['agent', 'task', 'error', 'system'];
        const chipLabels = { agent: 'Agents', task: 'Tasks', error: 'Errors', system: 'System' };

        filtersContainer.innerHTML = `
            <div class="mb-1.5">
                <input id="activity-search" type="text" placeholder="Search activity..."
                    class="w-full px-2.5 py-1.5 text-xs border border-bm-border rounded-md
                           bg-bm-bg text-bm-text placeholder-bm-muted focus:outline-none
                           focus:ring-1 focus:ring-blue-500">
            </div>
            <div class="flex items-center gap-1.5 flex-wrap">
                <div id="activity-chips" class="flex gap-1 flex-wrap">
                    ${categories.map(c => `
                        <button class="activity-chip" data-category="${c}">${chipLabels[c]}</button>
                    `).join('')}
                </div>
                <select id="activity-agent-filter"
                    class="ml-auto text-xs border border-bm-border rounded-md px-1.5 py-1
                           bg-bm-bg text-bm-text focus:outline-none">
                    <option value="">All agents</option>
                </select>
            </div>
        `;

        // Bind search
        const searchInput = document.getElementById('activity-search');
        searchInput.addEventListener('input', debounce((e) => {
            state.filters.search = e.target.value.trim();
            fetchFeed(false);
        }, DEBOUNCE_MS));

        // Bind chips
        filtersContainer.querySelectorAll('.activity-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const cat = chip.dataset.category;
                const idx = state.filters.categories.indexOf(cat);
                if (idx >= 0) {
                    state.filters.categories.splice(idx, 1);
                    chip.classList.remove('active');
                } else {
                    state.filters.categories.push(cat);
                    chip.classList.add('active');
                }
                fetchFeed(false);
            });
        });

        // Bind agent dropdown
        const agentSelect = document.getElementById('activity-agent-filter');
        agentSelect.addEventListener('change', (e) => {
            state.filters.agentName = e.target.value || null;
            fetchFeed(false);
        });
    }

    function updateAgentList(agents) {
        state.agents = agents || [];
        const select = document.getElementById('activity-agent-filter');
        if (!select) return;
        const current = select.value;
        select.innerHTML = '<option value="">All agents</option>';
        for (const agent of state.agents) {
            const name = agent.name || agent.agent_name;
            if (!name) continue;
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        }
        select.value = current;
    }

    // ─── Public API ───

    function loadFeed(data) {
        if (!feedContainer) return;
        state.entries = data.entries || [];
        state.hasMore = data.has_more || false;
        state.offset = state.entries.length;
        state.expandedIds.clear();
        renderFeed();
    }

    function addEntry(entry) {
        if (!feedContainer || !entry) return;
        if (!matchesFilters(entry)) return;

        // Remove empty state if present
        const emptyEl = feedContainer.querySelector('.activity-empty-state');
        if (emptyEl) emptyEl.remove();

        if (entry.is_active) {
            state.entries.unshift(entry);
        } else {
            // Insert after any active entries, at the top of inactive
            const firstInactiveIdx = state.entries.findIndex(e => !e.is_active);
            if (firstInactiveIdx === -1) {
                state.entries.push(entry);
            } else {
                state.entries.splice(firstInactiveIdx, 0, entry);
            }
        }

        renderFeed();
    }

    function updateEntry(entry) {
        if (!feedContainer || !entry) return;
        const idx = state.entries.findIndex(
            e => e.id === entry.id && e.source === entry.source,
        );
        if (idx === -1) {
            addEntry(entry);
            return;
        }

        // Replace and re-sort if active status changed
        const wasActive = state.entries[idx].is_active;
        state.entries[idx] = entry;

        if (wasActive && !entry.is_active) {
            // Re-sort: remove from current position and re-insert chronologically
            state.entries.splice(idx, 1);
            const firstInactiveIdx = state.entries.findIndex(e => !e.is_active);
            const insertIdx = firstInactiveIdx === -1 ? state.entries.length : firstInactiveIdx;
            state.entries.splice(insertIdx, 0, entry);
        }

        renderFeed();
    }

    // ─── Data fetching ───

    async function fetchFeed(append = false) {
        if (state.loading) return;
        state.loading = true;

        if (!append) {
            state.offset = 0;
            state.entries = [];
        }

        const params = new URLSearchParams();
        params.set('limit', PAGE_SIZE);
        params.set('offset', state.offset);
        if (state.filters.search) params.set('search', state.filters.search);
        if (state.filters.agentName) params.set('agent_name', state.filters.agentName);
        // Category filtering: if only one active, pass it server-side; otherwise filter client-side
        if (state.filters.categories.length === 1) {
            params.set('category', state.filters.categories[0]);
        }

        try {
            const res = await apiFetch(`/api/activity/feed?${params}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            let entries = data.entries || [];
            // Client-side multi-category filter
            if (state.filters.categories.length > 1) {
                entries = entries.filter(e => state.filters.categories.includes(e.category));
            }

            if (append) {
                state.entries.push(...entries);
            } else {
                state.entries = entries;
            }
            state.hasMore = data.has_more || false;
            state.offset = state.entries.length;
        } catch (err) {
            console.error('[ActivityLog] Fetch failed:', err);
        } finally {
            state.loading = false;
            renderFeed();
        }
    }

    // ─── Rendering ───

    function renderFeed() {
        if (!feedContainer) return;
        feedContainer.innerHTML = '';

        if (!state.entries.length) {
            renderEmptyState();
            return;
        }

        for (const entry of state.entries) {
            feedContainer.appendChild(renderEntry(entry));
        }

        if (state.hasMore) {
            const loadMore = document.createElement('button');
            loadMore.className = 'w-full py-2 mt-2 text-xs text-blue-500 hover:text-blue-600 font-medium';
            loadMore.textContent = 'Load more...';
            loadMore.addEventListener('click', () => fetchFeed(true));
            feedContainer.appendChild(loadMore);
        }

        if (window.lucide) lucide.createIcons({ nodes: [feedContainer] });
    }

    function renderEmptyState() {
        if (!feedContainer) return;
        feedContainer.innerHTML = `
            <div class="activity-empty-state text-bm-muted text-sm text-center mt-8">
                <i data-lucide="activity" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
                <p>Agent activity will appear here</p>
            </div>
        `;
        if (window.lucide) lucide.createIcons({ nodes: [feedContainer] });
    }

    function renderEntry(entry) {
        const { icon, color } = getEntryIcon(entry);
        const time = formatTime(entry.timestamp);
        const agentLabel = entry.agent_name || 'System';
        const isExpanded = state.expandedIds.has(entryKey(entry));

        const el = document.createElement('div');
        el.className = `activity-entry${isExpanded ? ' expanded' : ''}`;
        el.dataset.id = entry.id;
        el.dataset.source = entry.source;

        el.innerHTML = `
            <div class="flex items-start gap-2">
                <div class="shrink-0 mt-0.5">
                    <i data-lucide="${icon}" class="w-4 h-4 ${color}"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <p class="text-sm leading-snug">${BossModUtils.escapeHtml(entry.title || '')}</p>
                    <div class="flex items-center gap-1.5 text-xs text-bm-muted mt-0.5">
                        <span>${BossModUtils.escapeHtml(agentLabel)}</span>
                        <span>&middot;</span>
                        <span>${time}</span>
                        ${entry.is_active ? '<span class="activity-pulse">Active</span>' : ''}
                    </div>
                </div>
                <div class="shrink-0 mt-1">
                    <i data-lucide="chevron-right" class="w-3 h-3 text-bm-muted expand-icon"></i>
                </div>
            </div>
            ${isExpanded ? renderDetail(entry) : ''}
        `;

        // Click to toggle expand
        el.addEventListener('click', (e) => {
            if (e.target.closest('a, button, pre')) return;
            toggleExpand(entry, el);
        });

        return el;
    }

    function toggleExpand(entry, el) {
        const key = entryKey(entry);
        if (state.expandedIds.has(key)) {
            state.expandedIds.delete(key);
            el.classList.remove('expanded');
            const detail = el.querySelector('.activity-detail');
            if (detail) detail.remove();
            const expandIcon = el.querySelector('.expand-icon');
            if (expandIcon) expandIcon.style.transform = '';
        } else {
            state.expandedIds.add(key);
            el.classList.add('expanded');
            const detailHtml = renderDetail(entry);
            el.insertAdjacentHTML('beforeend', detailHtml);
            const expandIcon = el.querySelector('.expand-icon');
            if (expandIcon) expandIcon.style.transform = 'rotate(90deg)';
            if (window.lucide) lucide.createIcons({ nodes: [el] });
        }
    }

    function renderDetail(entry) {
        const fields = [];

        fields.push(['Source', sourceLabel(entry.source)]);
        if (entry.event) fields.push(['Type', entry.event]);
        if (entry.category) fields.push(['Category', entry.category]);
        if (entry.detail) fields.push(['Detail', entry.detail]);
        if (entry.task_id) fields.push(['Task ID', entry.task_id]);

        const meta = entry.metadata;
        if (meta) {
            if (meta.source_channel) fields.push(['Channel', meta.source_channel]);
            if (meta.status) fields.push(['Status', meta.status]);
            if (meta.destination) fields.push(['Destination', meta.destination]);
            if (meta.kind && meta.kind !== entry.event) fields.push(['Kind', meta.kind]);
            if (meta.policy && meta.policy !== 'none') fields.push(['Policy', meta.policy]);
            if (meta.ended_at) fields.push(['Ended', formatTime(meta.ended_at)]);
            if (meta.target_path) fields.push(['Path', meta.target_path]);
        }

        const dlRows = fields.map(([label, value]) => `
            <dt class="text-bm-muted">${BossModUtils.escapeHtml(label)}</dt>
            <dd>${BossModUtils.escapeHtml(String(value))}</dd>
        `).join('');

        const metaBlock = (meta && hasVisibleMeta(meta))
            ? `<pre class="mt-2 p-2 bg-bm-bg rounded text-xs overflow-x-auto max-h-40 text-bm-muted">${BossModUtils.escapeHtml(JSON.stringify(meta, null, 2))}</pre>`
            : '';

        return `
            <div class="activity-detail mt-2 pt-2 border-t border-bm-border">
                <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                    ${dlRows}
                </dl>
                ${metaBlock}
            </div>
        `;
    }

    // ─── Helpers ───

    function getEntryIcon(entry) {
        return EVENT_ICONS[entry.event]
            || CATEGORY_DEFAULTS[entry.category]
            || CATEGORY_DEFAULTS.system;
    }

    function entryKey(entry) {
        return `${entry.source}:${entry.id}`;
    }

    function matchesFilters(entry) {
        const { search, categories, agentName } = state.filters;
        if (search && !(entry.title || '').toLowerCase().includes(search.toLowerCase())) {
            return false;
        }
        if (categories.length && !categories.includes(entry.category)) {
            return false;
        }
        if (agentName && entry.agent_name !== agentName) {
            return false;
        }
        return true;
    }

    function sourceLabel(source) {
        const labels = {
            activity_log: 'Activity Log',
            activity: 'Runtime Activity',
            notification: 'Notification',
        };
        return labels[source] || source;
    }

    function hasVisibleMeta(meta) {
        if (!meta || typeof meta !== 'object') return false;
        const keys = Object.keys(meta).filter(k => meta[k] != null && meta[k] !== '');
        return keys.length > 0;
    }

    function formatTime(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            return date.toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            });
        } catch {
            return '';
        }
    }

    function debounce(fn, ms) {
        let timer;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), ms);
        };
    }

    // ─── Public API ───

    return {
        init,
        loadFeed,
        addEntry,
        updateEntry,
        updateAgentList,
    };
})();

document.addEventListener('DOMContentLoaded', ActivityLog.init);
