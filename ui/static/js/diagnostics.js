/**
 * BossMod AI — Diagnostics tab controller.
 *
 * Left panel: compact card list with per-entry expand buttons.
 * Right panel: full-width detail view (replaces the canvas) with
 * collapsible sections and copy-to-clipboard buttons.
 */

const DiagnosticsView = (() => {
    let entries = [];
    let container = null;
    let selectedDiagId = null;

    function getContainer() {
        if (!container) container = document.getElementById('subview-diagnostics');
        return container;
    }

    /** Map mode string to display icon. */
    function modeIcon(mode) {
        return mode === 'social' ? '\uD83D\uDCAC' : '\u26A1';
    }

    /** Coerce ID to string for safe comparison (API may return int or string). */
    function idStr(val) {
        return val == null ? '' : String(val);
    }

    function triggerLabel(triggerType) {
        switch (triggerType) {
            case 'human_chat': return 'Human Chat';
            case 'peer_message': return 'Peer Message';
            case 'watchdog_status_ping': return 'Watchdog';
            case 'task_resumed': return 'Task Resumed';
            case 'task_attention_required': return 'Task Attention';
            case 'task_assigned': return 'Task Assigned';
            case 'social': return 'Social';
            default: return triggerType || 'Trigger';
        }
    }

    function triggerPreview(entry) {
        let parsed = null;
        try {
            parsed = typeof entry.trigger_data === 'string'
                ? JSON.parse(entry.trigger_data)
                : entry.trigger_data;
        } catch {
            parsed = null;
        }

        const preview =
            parsed?.content ||
            parsed?.task_title ||
            parsed?.task_description ||
            '';

        if (!preview) return '';
        return preview.length > 120 ? `${preview.slice(0, 117)}...` : preview;
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
            if (!res.ok) {
                console.error(`[Diagnostics] load failed: ${res.status} ${res.statusText}`);
                entries = [];
            } else {
                entries = await res.json();
            }
        } catch (err) {
            console.error('[Diagnostics] load error:', err);
            entries = [];
        }

        render();
    }

    // ─── Render card list (left panel) ───

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

        const selectedStr = idStr(selectedDiagId);

        let html = '<div class="space-y-2 p-4">';
        for (const entry of entries) {
            const entryIdStr = idStr(entry.id);
            const isError = entry.status === 'error' || entry.status === 'skipped';
            const isSelected = entryIdStr === selectedStr;
            const actionLabel = entry.action_name || entry.status;
            const tokens = entry.total_tokens || 0;
            const ms = entry.duration_ms || 0;
            const time = entry.created_at
                ? new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                : '';
            const agentLabel = entry.agent_name ? esc(entry.agent_name) : '';
            const triggerBadge = esc(triggerLabel(entry.trigger_type));
            const preview = esc(triggerPreview(entry));

            html += `
            <div class="diagnostic-card ${isError ? 'has-error' : ''} ${isSelected ? 'selected' : ''}" data-diag-id="${esc(entryIdStr)}">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 min-w-0">
                        <span class="text-xs">${modeIcon(entry.mode)}</span>
                        <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-bm-muted font-medium">${triggerBadge}</span>
                        <span class="text-xs font-semibold truncate">${esc(actionLabel)}</span>
                        ${agentLabel ? `<span class="text-xs text-bm-muted truncate">[${agentLabel}]</span>` : ''}
                    </div>
                    <div class="flex items-center gap-1.5 shrink-0">
                        <span class="text-xs text-bm-muted">${tokens} tok</span>
                        <button class="diag-expand-btn p-1 rounded hover:bg-slate-100 transition-colors"
                                data-diag-id="${esc(entryIdStr)}" title="View detail">
                            <i data-lucide="arrow-right" class="w-3.5 h-3.5 text-bm-muted"></i>
                        </button>
                    </div>
                </div>
                ${preview ? `<p class="mt-1 text-xs text-bm-text/80 line-clamp-2">${preview}</p>` : ''}
                <div class="flex items-center gap-2 mt-0.5 text-xs text-bm-muted">
                    <span>${esc(entry.model || 'no model')}</span>
                    <span>\u2022</span>
                    <span>${ms}ms</span>
                    <span>\u2022</span>
                    <span>${time}</span>
                    ${entry.error ? `<span class="text-red-500 truncate ml-1">\u2014 ${esc(entry.error.slice(0, 60))}</span>` : ''}
                </div>
            </div>`;
        }
        html += '</div>';
        el.innerHTML = html;

        // Bind expand buttons only (not the whole card)
        el.querySelectorAll('.diag-expand-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                showDetail(btn.dataset.diagId);
            });
        });

        if (window.lucide) lucide.createIcons({ nodes: [el] });
    }

    // ─── Show detail in right panel ───

    async function showDetail(id) {
        selectedDiagId = id;

        const entry = entries.find(e => idStr(e.id) === idStr(id));
        const canvasContainer = document.getElementById('canvas-container');
        const detailPanel = document.getElementById('diagnostic-detail-panel');
        if (!canvasContainer || !detailPanel) return;

        canvasContainer.classList.add('hidden');
        detailPanel.classList.remove('hidden');

        // Render header summary
        const summaryEl = document.getElementById('diag-detail-summary');
        const esc = BossModUtils.escapeHtml;
        if (entry) {
            summaryEl.innerHTML = `
                <span class="text-sm">${modeIcon(entry.mode)}</span>
                <span class="text-sm font-medium">${esc(entry.mode || '-')}</span>
                <span class="text-sm text-bm-muted">\u2192</span>
                <span class="text-sm font-semibold">${esc(entry.action_name || entry.status)}</span>
                ${entry.agent_name ? `<span class="text-sm text-bm-muted">[${esc(entry.agent_name)}]</span>` : ''}
                <span class="text-xs text-bm-muted">${entry.total_tokens || 0} tok \u2022 ${entry.duration_ms || 0}ms</span>
            `;
        }

        // Bind close button
        document.getElementById('diag-detail-close').onclick = closeDetail;

        // Show loading in body
        const bodyEl = document.getElementById('diag-detail-body');
        bodyEl.innerHTML = '<p class="text-sm text-bm-muted">Loading detail\u2026</p>';

        if (window.lucide) lucide.createIcons({ nodes: [detailPanel] });

        // Highlight card in left panel
        highlightCard(id);

        // Fetch full detail
        try {
            const res = await fetch(`/api/diagnostics/${encodeURIComponent(id)}`);
            if (!res.ok) {
                console.error(`[Diagnostics] detail fetch failed: ${res.status} ${res.statusText}`);
                bodyEl.innerHTML = '<p class="text-sm text-red-500">Failed to load details.</p>';
                return;
            }
            // Guard: if user closed or switched before fetch returned
            if (selectedDiagId !== id) return;

            const data = await res.json();
            bodyEl.innerHTML = renderDetailPanel(data);
            bindSectionToggles(bodyEl);
            bindCopyButtons(bodyEl);
            if (window.lucide) lucide.createIcons({ nodes: [bodyEl] });
        } catch (err) {
            console.error('[Diagnostics] detail fetch error:', err);
            if (selectedDiagId === id) {
                bodyEl.innerHTML = '<p class="text-sm text-red-500">Failed to load details.</p>';
            }
        }
    }

    // ─── Close detail, restore canvas ───

    function closeDetail() {
        selectedDiagId = null;

        const canvasContainer = document.getElementById('canvas-container');
        const detailPanel = document.getElementById('diagnostic-detail-panel');

        if (detailPanel) detailPanel.classList.add('hidden');
        if (canvasContainer) canvasContainer.classList.remove('hidden');

        highlightCard(null);

        // Trigger canvas re-render since it was hidden
        window.dispatchEvent(new Event('panel-resize'));
    }

    // ─── Highlight selected card ───

    function highlightCard(id) {
        const el = getContainer();
        if (!el) return;
        const target = idStr(id);
        el.querySelectorAll('.diagnostic-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.diagId === target);
        });
    }

    // ─── Render full detail panel content ───

    function renderDetailPanel(data) {
        const esc = BossModUtils.escapeHtml;

        // Metadata grid
        const meta = `
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6 p-4 bg-bm-surface rounded-lg border border-bm-border">
                <div>
                    <p class="text-xs text-bm-muted">Model</p>
                    <p class="text-sm font-medium">${esc(data.model || '-')}</p>
                </div>
                <div>
                    <p class="text-xs text-bm-muted">Source</p>
                    <p class="text-sm font-medium">${esc(data.model_source || '-')}</p>
                </div>
                <div>
                    <p class="text-xs text-bm-muted">Tokens</p>
                    <p class="text-sm font-medium">${data.prompt_tokens || 0} / ${data.completion_tokens || 0} / ${data.total_tokens || 0}</p>
                </div>
                <div>
                    <p class="text-xs text-bm-muted">Duration</p>
                    <p class="text-sm font-medium">${data.duration_ms || 0}ms</p>
                </div>
            </div>`;

        // Collapsible sections
        const sections = [];
        if (data.trigger_data) {
            sections.push(detailSection('Trigger', formatJson(data.trigger_data)));
        }
        if (Array.isArray(data.steps) && data.steps.length) {
            sections.push(renderExecutionTraceSection(data.steps));
        }
        if (data.context) {
            sections.push(detailSection('Context Sent', formatJson(data.context)));
        }
        if (data.raw_response) {
            sections.push(detailSection('Raw Response', esc(data.raw_response)));
        }
        if (data.parsed_action) {
            sections.push(detailSection('Parsed Action', formatJson(data.parsed_action)));
        }
        if (data.result) {
            sections.push(detailSection('Execution Result', formatJson(data.result)));
        }
        if (data.error) {
            sections.push(detailSection('Error', esc(data.error), true));
        }

        return meta + (sections.join('') || '<p class="text-sm text-bm-muted">No detail data available.</p>');
    }

    function renderExecutionTraceSection(steps) {
        const cards = steps.map((step) => renderTraceStep(step)).join('');
        return `
            <div class="diag-section mb-4">
                <div class="flex items-center gap-1 mb-1">
                    <button class="diag-section-toggle flex items-center gap-2 py-1.5 px-2 hover:bg-slate-50 rounded transition-colors"
                            aria-expanded="true">
                        <i data-lucide="minus" class="w-4 h-4 text-bm-muted diag-toggle-icon"></i>
                        <span class="text-sm font-semibold text-bm-text">Execution Trace</span>
                    </button>
                </div>
                <div class="diag-section-content">
                    <div class="space-y-3">${cards}</div>
                </div>
            </div>`;
    }

    function renderTraceStep(step) {
        const esc = BossModUtils.escapeHtml;
        const actionLabel = esc(step.action_name || 'no action');
        const statusBadge = step.error
            ? '<span class="text-[11px] px-2 py-0.5 rounded-full bg-red-50 text-red-600 font-medium">Error</span>'
            : '<span class="text-[11px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">OK</span>';

        const blocks = [];
        if (step.context_snapshot) {
            blocks.push(renderTraceBlock('Prompt Delta', formatJson(step.context_snapshot)));
        }
        if (step.raw_response) {
            blocks.push(renderTraceBlock('Raw Response', esc(step.raw_response)));
        }
        if (step.parsed_action) {
            blocks.push(renderTraceBlock('Parsed Action', formatJson(step.parsed_action)));
        }
        if (step.result) {
            blocks.push(renderTraceBlock('Execution Result', formatJson(step.result)));
        }
        if (step.error) {
            blocks.push(renderTraceBlock('Error', esc(step.error), true));
        }

        return `
            <div class="rounded-lg border border-bm-border bg-white p-3">
                <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div class="flex items-center gap-2 min-w-0">
                        <span class="text-xs font-semibold text-bm-muted">Step ${Number(step.step_index || 0)}</span>
                        <span class="text-sm font-semibold truncate">${actionLabel}</span>
                        ${statusBadge}
                    </div>
                    <div class="flex flex-wrap items-center gap-2 text-xs text-bm-muted">
                        <span>${step.prompt_tokens || 0} / ${step.completion_tokens || 0} / ${step.total_tokens || 0} tok</span>
                        <span>&bull;</span>
                        <span>${step.duration_ms || 0}ms</span>
                    </div>
                </div>
                <div class="space-y-3">${blocks.join('')}</div>
            </div>`;
    }

    function renderTraceBlock(label, content, isError = false) {
        const labelClass = isError ? 'text-red-600' : 'text-bm-text';
        const preClass = isError ? 'diagnostic-pre-full error-pre' : 'diagnostic-pre-full';
        return `
            <div>
                <p class="text-xs font-semibold ${labelClass} mb-1">${label}</p>
                <pre class="${preClass}">${content}</pre>
            </div>`;
    }

    function detailSection(label, content, isError = false) {
        const preClass = isError ? 'diagnostic-pre-full error-pre' : 'diagnostic-pre-full';
        const labelColor = isError ? 'text-red-600' : 'text-bm-text';
        return `
            <div class="diag-section mb-4">
                <div class="flex items-center gap-1 mb-1">
                    <button class="diag-section-toggle flex items-center gap-2 py-1.5 px-2 hover:bg-slate-50 rounded transition-colors"
                            aria-expanded="true">
                        <i data-lucide="minus" class="w-4 h-4 text-bm-muted diag-toggle-icon"></i>
                        <span class="text-sm font-semibold ${labelColor}">${label}</span>
                    </button>
                    <button class="diag-copy-btn p-1 rounded hover:bg-slate-100" title="Copy to clipboard">
                        <i data-lucide="copy" class="w-3.5 h-3.5 text-bm-muted"></i>
                    </button>
                </div>
                <div class="diag-section-content">
                    <pre class="${preClass}">${content}</pre>
                </div>
            </div>`;
    }

    // ─── Section toggle bindings ───

    function bindSectionToggles(container) {
        container.querySelectorAll('.diag-section-toggle').forEach(btn => {
            btn.addEventListener('click', () => {
                const section = btn.closest('.diag-section');
                const content = section.querySelector('.diag-section-content');
                const icon = btn.querySelector('.diag-toggle-icon');
                const isHidden = content.classList.toggle('hidden');
                btn.setAttribute('aria-expanded', String(!isHidden));
                icon.setAttribute('data-lucide', isHidden ? 'plus' : 'minus');
                if (window.lucide) lucide.createIcons({ nodes: [icon.parentElement] });
            });
        });
    }

    // ─── Copy-to-clipboard bindings ───

    function bindCopyButtons(container) {
        container.querySelectorAll('.diag-copy-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const section = btn.closest('.diag-section');
                const pre = section.querySelector('.diag-section-content pre');
                if (!pre) return;

                try {
                    await navigator.clipboard.writeText(pre.textContent);
                } catch (err) {
                    console.warn('[Diagnostics] clipboard API unavailable, selecting text instead:', err);
                    // Fallback: select the text so user can Ctrl+C
                    const range = document.createRange();
                    range.selectNodeContents(pre);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    return;
                }

                // Visual feedback: swap to check icon briefly
                const icon = btn.querySelector('i');
                if (!icon) return;
                icon.setAttribute('data-lucide', 'check');
                icon.classList.remove('text-bm-muted');
                icon.classList.add('text-emerald-500');
                if (window.lucide) lucide.createIcons({ nodes: [btn] });

                setTimeout(() => {
                    if (!icon.isConnected) return;
                    icon.setAttribute('data-lucide', 'copy');
                    icon.classList.remove('text-emerald-500');
                    icon.classList.add('text-bm-muted');
                    if (window.lucide) lucide.createIcons({ nodes: [btn] });
                }, 1500);
            });
        });
    }

    // ─── JSON formatting helper ───

    function formatJson(raw) {
        try {
            const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
            // Unescape \n and \t within JSON string values so they render
            // as real whitespace inside <pre> instead of literal backslash-n.
            const pretty = JSON.stringify(parsed, null, 2)
                .replace(/\\n/g, '\n')
                .replace(/\\t/g, '\t');
            return BossModUtils.escapeHtml(pretty);
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

    return { load, addEntry, closeDetail };
})();
