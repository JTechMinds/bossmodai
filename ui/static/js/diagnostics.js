/**
 * BossMod AI — Diagnostics tab controller.
 *
 * Displays per-agent or global diagnostic traces: trigger, mode,
 * model, tokens, duration, with expandable full detail.
 */

const DiagnosticsView = (() => {
    let entries = [];
    let container = null;

    function getContainer() {
        if (!container) container = document.getElementById('subview-diagnostics');
        return container;
    }

    // ─── Load from REST API ───

    async function load(agentId) {
        const el = getContainer();
        if (!el) return;

        const url = agentId
            ? `/api/diagnostics?agent_id=${agentId}&limit=50`
            : '/api/diagnostics?limit=50';

        try {
            const res = await fetch(url);
            if (res.ok) entries = await res.json();
        } catch { /* ignore */ }

        render();
    }

    // ─── Render entry list ───

    function render() {
        const el = getContainer();
        if (!el) return;
        const esc = BossModUtils.escapeHtml;

        if (entries.length === 0) {
            el.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-8">
                    <i data-lucide="activity" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
                    <p>No diagnostic entries yet.</p>
                    <p class="text-xs mt-1">Agent turns will appear here automatically.</p>
                </div>`;
            if (window.lucide) lucide.createIcons({ nodes: [el] });
            return;
        }

        let html = '<div class="space-y-2 p-4">';
        for (const entry of entries) {
            const isError = entry.status === 'error' || entry.status === 'skipped';
            const modeIcon = entry.mode === 'social' ? '\uD83D\uDCAC' : '\u26A1';
            const actionLabel = entry.action_name || entry.status;
            const tokens = entry.total_tokens || 0;
            const ms = entry.duration_ms || 0;
            const time = entry.created_at
                ? new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                : '';
            const agentLabel = entry.agent_name ? esc(entry.agent_name) : '';

            html += `
            <div class="diagnostic-card ${isError ? 'has-error' : ''}" data-diag-id="${entry.id}">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 min-w-0">
                        <span class="text-xs">${modeIcon}</span>
                        <span class="text-xs font-medium">${esc(entry.mode || '-')}</span>
                        <span class="text-xs text-bm-muted">\u2192</span>
                        <span class="text-xs font-semibold truncate">${esc(actionLabel)}</span>
                        ${agentLabel ? `<span class="text-xs text-bm-muted truncate">[${agentLabel}]</span>` : ''}
                    </div>
                    <span class="text-xs text-bm-muted shrink-0">${tokens} tok</span>
                </div>
                <div class="flex items-center gap-2 mt-0.5 text-xs text-bm-muted">
                    <span>${esc(entry.model || 'no model')}</span>
                    <span>\u2022</span>
                    <span>${ms}ms</span>
                    <span>\u2022</span>
                    <span>${time}</span>
                    ${entry.error ? `<span class="text-red-500 truncate ml-1">\u2014 ${esc(entry.error.slice(0, 60))}</span>` : ''}
                </div>
                <div class="diagnostic-expanded hidden mt-3" id="diag-detail-${entry.id}">
                    <p class="text-xs text-bm-muted italic">Loading...</p>
                </div>
            </div>`;
        }
        html += '</div>';
        el.innerHTML = html;

        // Bind expand/collapse
        el.querySelectorAll('.diagnostic-card').forEach(card => {
            card.addEventListener('click', () => toggleExpand(card.dataset.diagId));
        });

        if (window.lucide) lucide.createIcons({ nodes: [el] });
    }

    // ─── Expand / collapse detail ───

    async function toggleExpand(id) {
        const detail = document.getElementById(`diag-detail-${id}`);
        if (!detail) return;

        if (!detail.classList.contains('hidden')) {
            detail.classList.add('hidden');
            return;
        }

        detail.classList.remove('hidden');

        // Fetch full detail if not already loaded
        if (detail.dataset.loaded) return;

        try {
            const res = await fetch(`/api/diagnostics/${id}`);
            if (!res.ok) {
                detail.innerHTML = '<p class="text-xs text-red-500">Failed to load details.</p>';
                return;
            }
            const data = await res.json();
            detail.dataset.loaded = 'true';
            detail.innerHTML = renderDetail(data);
        } catch {
            detail.innerHTML = '<p class="text-xs text-red-500">Failed to load details.</p>';
        }
    }

    function renderDetail(data) {
        const sections = [];

        if (data.trigger_data) {
            sections.push(detailSection('Trigger', formatJson(data.trigger_data)));
        }
        if (data.context) {
            sections.push(detailSection('Context Sent', formatJson(data.context)));
        }
        if (data.raw_response) {
            sections.push(detailSection('Raw Response', BossModUtils.escapeHtml(data.raw_response)));
        }
        if (data.parsed_action) {
            sections.push(detailSection('Parsed Action', formatJson(data.parsed_action)));
        }
        if (data.result) {
            sections.push(detailSection('Execution Result', formatJson(data.result)));
        }
        if (data.error) {
            sections.push(`
                <div class="mt-2">
                    <p class="text-xs font-semibold text-red-600 mb-1">Error</p>
                    <pre class="diagnostic-pre text-red-700 bg-red-50">${BossModUtils.escapeHtml(data.error)}</pre>
                </div>`);
        }

        return sections.join('') || '<p class="text-xs text-bm-muted">No detail data.</p>';
    }

    function detailSection(label, content) {
        return `
            <div class="mt-2">
                <p class="text-xs font-semibold text-bm-muted mb-1">${label}</p>
                <pre class="diagnostic-pre">${content}</pre>
            </div>`;
    }

    function formatJson(raw) {
        try {
            const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
            return BossModUtils.escapeHtml(JSON.stringify(parsed, null, 2));
        } catch {
            return BossModUtils.escapeHtml(String(raw));
        }
    }

    // ─── WebSocket handler ───

    function addEntry(summary) {
        entries.unshift(summary);
        if (entries.length > 100) entries.pop();
        render();
    }

    return { load, addEntry };
})();
