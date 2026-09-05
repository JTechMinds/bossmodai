/**
 * BossMod AI — Settings → Runtime Contracts (HA-STRUCT-P1-04).
 */

const RuntimeContractsSection = (() => {
    const TEXTAREA_CLS = 'w-full h-full px-4 py-3 text-sm border border-bm-border rounded-lg '
        + 'bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 '
        + 'focus:border-bm-accent resize-none font-mono leading-relaxed';
    const SELECT_CLS = 'px-3 py-2 text-sm border border-bm-border rounded-lg '
        + 'bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent';

    function collectTemplateValues() {
        return {
            decision: document.getElementById('runtime-decision-contract')?.value || '',
            execution: document.getElementById('runtime-execution-contract')?.value || '',
            trigger_event: document.getElementById('runtime-trigger-event-contract')?.value || '',
            conversation_envelope: document.getElementById('runtime-conversation-envelope-contract')?.value || '',
            file_deliverable_guidance: document.getElementById('runtime-file-guidance-contract')?.value || '',
            communication_snapshot: document.getElementById('runtime-communication-snapshot-contract')?.value || '',
        };
    }

    function renderPromptHealth(container, health) {
        if (!container) return;
        const status = health?.status || 'clean';
        const issues = Array.isArray(health?.issues) ? health.issues : [];
        const tones = {
            clean: {
                panel: 'bg-emerald-50 border-emerald-200',
                badge: 'bg-emerald-100 text-emerald-700',
                title: 'Prompt surface is clean.',
                detail: 'No contradictory prompt-contract instructions were detected across the editable and hidden runtime prompt layers.',
            },
            warning: {
                panel: 'bg-amber-50 border-amber-200',
                badge: 'bg-amber-100 text-amber-700',
                title: 'Prompt warnings detected.',
                detail: 'The current prompt surface is usable, but some instructions are ambiguous or overly broad.',
            },
            error: {
                panel: 'bg-red-50 border-red-200',
                badge: 'bg-red-100 text-red-700',
                title: 'Prompt issues detected.',
                detail: 'The current prompt surface includes conflicting or invalid contract language that should be corrected before relying on it.',
            },
        };
        const tone = tones[status] || tones.clean;
        const issuesHtml = issues.length
            ? `<ul class="mt-3 space-y-2 text-sm text-bm-text">${issues.map(issue => `
                <li class="rounded-lg border border-white/70 bg-white/70 px-3 py-2">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${issue.severity === 'error' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}">${BossModUtils.escapeHtml(issue.severity || 'warning')}</span>
                        <span class="text-sm font-medium">${BossModUtils.escapeHtml(issue.surface_label || issue.surface_key || 'Prompt Surface')}</span>
                    </div>
                    <div class="mt-1 text-sm text-bm-text">${BossModUtils.escapeHtml(issue.message || '')}</div>
                </li>
            `).join('')}</ul>`
            : '';
        container.innerHTML = `
            <div class="p-3 border rounded-lg ${tone.panel}">
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${tone.badge}">${BossModUtils.escapeHtml(status)}</span>
                    <p class="text-sm font-medium text-bm-text">${BossModUtils.escapeHtml(tone.title)}</p>
                </div>
                <p class="mt-1 text-sm text-bm-muted">${BossModUtils.escapeHtml(tone.detail)}</p>
                ${issuesHtml}
            </div>`;
    }

    function insertAtCursor(textarea, text) {
        if (!textarea) return;
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const before = textarea.value.substring(0, start);
        const after = textarea.value.substring(end);
        textarea.value = before + text + after;
        const cursorPos = start + text.length;
        textarea.selectionStart = cursorPos;
        textarea.selectionEnd = cursorPos;
        textarea.focus();
    }

    async function render(el) {
        let payload = null;
        try {
            const res = await apiFetch('/api/runtime/contracts');
            payload = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load runtime contracts.</p>';
            return;
        }

        const decisionContract = payload?.decision || '';
        const executionContract = payload?.execution || '';
        const triggerEvent = payload?.trigger_event || '';
        const conversationEnvelope = payload?.conversation_envelope || '';
        const fileGuidance = payload?.file_deliverable_guidance || '';
        const communicationSnapshot = payload?.communication_snapshot || '';
        const allowedVariables = payload?.allowed_variables || [];
        const syntaxExamples = payload?.template_syntax || [];
        const previewTriggers = payload?.preview_triggers || [];
        const promptHealth = payload?.prompt_health || { status: 'clean', issues: [] };

        let activeTab = 'decision';

        el.innerHTML = `
            <div class="mb-4">
                <h2 class="text-lg font-semibold">Runtime Contracts</h2>
                <p class="text-sm text-bm-muted mt-0.5">Edit the runtime contracts and runtime-owned prompt blocks appended to turns. Changes apply to newly built turns immediately after save, and prompt health checks also cover the hidden internal follow-up prompts the runtime injects.</p>
            </div>
            <div id="runtime-prompt-health" class="mb-4"></div>
            <div class="mb-4 p-3 bg-slate-50 border border-bm-border rounded-lg">
                <p class="text-xs font-semibold text-bm-muted uppercase tracking-wide mb-2">Template Syntax</p>
                <div class="space-y-1 text-xs font-mono text-bm-muted">
                    ${syntaxExamples.map(ex => `<div>${BossModUtils.escapeHtml(ex)}</div>`).join('')}
                </div>
            </div>
            <div class="flex gap-0 flex-1 min-h-0" style="height: calc(100vh - 320px); min-height: 400px;">
                <!-- Variables panel -->
                <div id="rc-vars-panel" class="shrink-0 overflow-y-auto border border-bm-border rounded-l-lg bg-slate-50 p-3"
                     style="width: 230px;">
                    <p class="text-xs font-semibold text-bm-muted uppercase tracking-wide mb-2">Variables</p>
                    <div class="space-y-1">
                        ${allowedVariables.map(item => {
                            const isSubProp = item.name.includes('.');
                            return `<button type="button" data-var="${BossModUtils.escapeHtml(item.name)}"
                                class="rc-var-btn w-full text-left px-2 py-1.5 rounded hover:bg-white
                                       transition-colors cursor-pointer group ${isSubProp ? 'pl-5' : ''}">
                                <div class="text-xs font-mono text-bm-accent group-hover:text-bm-accent-hover">{{${BossModUtils.escapeHtml(item.name)}}}</div>
                                <div class="text-[11px] text-bm-muted leading-tight">${BossModUtils.escapeHtml(item.description)}</div>
                            </button>`;
                        }).join('')}
                    </div>
                </div>
                <!-- Resize handle -->
                <div id="rc-resize-handle" class="shrink-0 w-1.5 cursor-col-resize bg-bm-border hover:bg-bm-accent/40 transition-colors"></div>
                <!-- Editor panel -->
                <div class="flex-1 flex flex-col min-w-0 border border-l-0 border-bm-border rounded-r-lg bg-white">
                    <!-- Tab bar -->
                    <div class="flex border-b border-bm-border shrink-0">
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative active" data-tab="decision">Decision</button>
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative" data-tab="execution">Execution</button>
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative" data-tab="trigger-event">Trigger</button>
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative" data-tab="conversation-envelope">Envelope</button>
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative" data-tab="file-guidance">File Guidance</button>
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative" data-tab="communication-snapshot">Snapshot</button>
                        <button class="tab-btn rc-tab flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative" data-tab="preview">Preview</button>
                    </div>
                    <!-- Tab content -->
                    <div class="flex-1 flex flex-col min-h-0 p-4">
                        <div id="rc-tab-decision" class="rc-tab-pane flex-1 flex flex-col min-h-0">
                            <textarea id="runtime-decision-contract" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(decisionContract)}</textarea>
                        </div>
                        <div id="rc-tab-execution" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-execution-contract" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(executionContract)}</textarea>
                        </div>
                        <div id="rc-tab-trigger-event" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-trigger-event-contract" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(triggerEvent)}</textarea>
                        </div>
                        <div id="rc-tab-conversation-envelope" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-conversation-envelope-contract" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(conversationEnvelope)}</textarea>
                        </div>
                        <div id="rc-tab-file-guidance" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-file-guidance-contract" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(fileGuidance)}</textarea>
                        </div>
                        <div id="rc-tab-communication-snapshot" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-communication-snapshot-contract" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(communicationSnapshot)}</textarea>
                        </div>
                        <div id="rc-tab-preview" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <div class="flex items-center gap-2 mb-3 flex-wrap">
                                <select id="runtime-preview-trigger" class="${SELECT_CLS}">
                                    ${previewTriggers.map(t => `<option value="${BossModUtils.escapeHtml(t)}">${BossModUtils.escapeHtml(t)}</option>`).join('')}
                                </select>
                                <select id="runtime-preview-kind" class="${SELECT_CLS}">
                                    <option value="decision">Decision</option>
                                    <option value="execution">Execution</option>
                                </select>
                                <button id="btn-render-preview"
                                        class="px-3 py-2 bg-bm-accent text-white rounded-lg
                                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                                    Render Full Prompt
                                </button>
                            </div>
                            <pre id="runtime-contract-preview-output"
                                 class="flex-1 w-full px-4 py-3 text-sm border border-bm-border rounded-lg
                                        bg-slate-50 overflow-auto whitespace-pre-wrap font-mono leading-relaxed">Choose a trigger and turn kind, then click Render Full Prompt.</pre>
                        </div>
                    </div>
                    <!-- Bottom bar -->
                    <div class="flex items-center gap-3 px-4 pb-4 shrink-0">
                        <button id="btn-save-runtime-contracts"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            Save Contracts
                        </button>
                        <button id="btn-reset-runtime-contracts"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Reset to Defaults
                        </button>
                        <button id="btn-refresh-runtime-contracts"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Refresh
                        </button>
                        <span id="runtime-contract-save-status" class="text-sm text-bm-muted"></span>
                    </div>
                </div>
            </div>`;

        renderPromptHealth(document.getElementById('runtime-prompt-health'), promptHealth);

        // ─── Resizable vars panel ───
        initResizeHandle(
            document.getElementById('rc-resize-handle'),
            document.getElementById('rc-vars-panel'),
        );

        // ─── Tab switching ───
        function switchTab(tab) {
            activeTab = tab;
            el.querySelectorAll('.rc-tab').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === tab);
            });
            el.querySelectorAll('.rc-tab-pane').forEach(pane => {
                pane.classList.toggle('hidden', pane.id !== `rc-tab-${tab}`);
            });
            el.querySelectorAll('.rc-var-btn').forEach(btn => {
                btn.style.opacity = tab === 'preview' ? '0.5' : '';
                btn.style.cursor = tab === 'preview' ? 'default' : 'pointer';
            });
        }

        el.querySelectorAll('.rc-tab').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });

        // ─── Click-to-insert variables ───
        el.querySelectorAll('.rc-var-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                if (activeTab === 'preview') return;
                const varName = btn.dataset.var;
                const textarea = document.getElementById(`runtime-${activeTab}-contract`);
                insertAtCursor(textarea, `{{${varName}}}`);
            });
        });

        // ─── Save ───
        document.getElementById('btn-save-runtime-contracts').addEventListener('click', async () => {
            const status = document.getElementById('runtime-contract-save-status');
            const templates = collectTemplateValues();
            try {
                const res = await apiFetch('/api/runtime/contracts', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(templates),
                });
                const payload = await res.json().catch(() => ({}));
                if (!res.ok) {
                    throw new Error(payload.detail || 'Save failed');
                }
                renderPromptHealth(document.getElementById('runtime-prompt-health'), payload.prompt_health);
                status.textContent = 'Saved';
                status.className = 'text-sm text-emerald-600';
                setTimeout(() => { status.textContent = ''; }, 2000);
            } catch (err) {
                status.textContent = err.message || 'Save failed';
                status.className = 'text-sm text-red-600';
            }
        });

        document.getElementById('btn-reset-runtime-contracts').addEventListener('click', async () => {
            if (!confirm('Reset both runtime contracts to their seeded defaults?')) return;
            const status = document.getElementById('runtime-contract-save-status');
            try {
                const res = await apiFetch('/api/runtime/contracts/reset', {
                    method: 'POST',
                });
                const payload = await res.json();
                if (!res.ok) {
                    throw new Error(payload.detail || 'Reset failed');
                }
                document.getElementById('runtime-decision-contract').value = payload.decision || '';
                document.getElementById('runtime-execution-contract').value = payload.execution || '';
                document.getElementById('runtime-trigger-event-contract').value = payload.trigger_event || '';
                document.getElementById('runtime-conversation-envelope-contract').value = payload.conversation_envelope || '';
                document.getElementById('runtime-file-guidance-contract').value = payload.file_deliverable_guidance || '';
                document.getElementById('runtime-communication-snapshot-contract').value = payload.communication_snapshot || '';
                renderPromptHealth(document.getElementById('runtime-prompt-health'), payload.prompt_health);
                status.textContent = 'Reset to defaults';
                status.className = 'text-sm text-emerald-600';
                setTimeout(() => { status.textContent = ''; }, 2000);
            } catch (err) {
                status.textContent = err.message || 'Reset failed';
                status.className = 'text-sm text-red-600';
            }
        });

        // ─── Refresh ───
        document.getElementById('btn-refresh-runtime-contracts').addEventListener('click', () => {
            render(el);
        });

        // ─── Preview ───
        document.getElementById('btn-render-preview').addEventListener('click', async () => {
            const triggerType = document.getElementById('runtime-preview-trigger').value;
            const contractKind = document.getElementById('runtime-preview-kind').value;
            const templates = collectTemplateValues();
            const output = document.getElementById('runtime-contract-preview-output');
            output.textContent = 'Rendering full prompt bundle\u2026';
            try {
                const res = await apiFetch('/api/runtime/contracts/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        contract_kind: contractKind,
                        trigger_type: triggerType,
                        scope: 'bundle',
                        templates,
                    }),
                });
                const preview = await res.json();
                if (!res.ok) throw new Error(preview.detail || 'Preview failed');
                output.textContent = preview.rendered || '';
                renderPromptHealth(document.getElementById('runtime-prompt-health'), preview.prompt_health);
            } catch (err) {
                output.textContent = err.message || 'Preview failed';
            }
        });
    }

    return { render };
})();
