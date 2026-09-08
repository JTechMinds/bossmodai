/**
 * BossMod AI — Settings → Runtime Contracts, the editor.
 *
 * The six runtime-owned prompt blocks and the preview pane: it loads them,
 * renders the variables rail and the tabbed editors, and wires the resize
 * handle, the tab switching and click-to-insert.
 *
 * Saving, resetting, refreshing and previewing are
 * settings-runtime-contracts-actions.js — this file renders the editors, that
 * one persists them, which is the seam the file was split at in Phase 3C.
 */

const RuntimeContractsSection = (() => {
    const TEXTAREA_CLS = 'w-full h-full px-4 py-3 text-sm border border-bm-border rounded-lg '
        + 'bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 '
        + 'focus:border-bm-accent resize-none font-mono leading-relaxed';
    const SELECT_CLS = 'px-3 py-2 text-sm border border-bm-border rounded-lg '
        + 'bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent';


    const { renderPromptHealth } = BossModRuntimeContractActions;

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
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
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
                    ${syntaxExamples.map(ex => `<div>${BossModFormat.escapeHtml(ex)}</div>`).join('')}
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
                            return `<button type="button" data-var="${BossModFormat.escapeAttribute(item.name)}"
                                class="rc-var-btn w-full text-left px-2 py-1.5 rounded hover:bg-white
                                       transition-colors cursor-pointer group ${isSubProp ? 'pl-5' : ''}">
                                <div class="text-xs font-mono text-bm-accent group-hover:text-bm-accent-hover">{{${BossModFormat.escapeHtml(item.name)}}}</div>
                                <div class="text-[11px] text-bm-muted leading-tight">${BossModFormat.escapeHtml(item.description)}</div>
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
                            <textarea id="runtime-decision-contract" class="${TEXTAREA_CLS}">${BossModFormat.escapeHtml(decisionContract)}</textarea>
                        </div>
                        <div id="rc-tab-execution" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-execution-contract" class="${TEXTAREA_CLS}">${BossModFormat.escapeHtml(executionContract)}</textarea>
                        </div>
                        <div id="rc-tab-trigger-event" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-trigger-event-contract" class="${TEXTAREA_CLS}">${BossModFormat.escapeHtml(triggerEvent)}</textarea>
                        </div>
                        <div id="rc-tab-conversation-envelope" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-conversation-envelope-contract" class="${TEXTAREA_CLS}">${BossModFormat.escapeHtml(conversationEnvelope)}</textarea>
                        </div>
                        <div id="rc-tab-file-guidance" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-file-guidance-contract" class="${TEXTAREA_CLS}">${BossModFormat.escapeHtml(fileGuidance)}</textarea>
                        </div>
                        <div id="rc-tab-communication-snapshot" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <textarea id="runtime-communication-snapshot-contract" class="${TEXTAREA_CLS}">${BossModFormat.escapeHtml(communicationSnapshot)}</textarea>
                        </div>
                        <div id="rc-tab-preview" class="rc-tab-pane flex-1 flex flex-col min-h-0 hidden">
                            <div class="flex items-center gap-2 mb-3 flex-wrap">
                                <select id="runtime-preview-trigger" class="${SELECT_CLS}">
                                    ${previewTriggers.map(t => `<option value="${BossModFormat.escapeAttribute(t)}">${BossModFormat.escapeHtml(t)}</option>`).join('')}
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

        BossModRuntimeContractActions.bindActions({ onRefresh: () => render(el) });
    }

    return { render };
})();
