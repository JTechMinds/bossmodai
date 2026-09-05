/**
 * BossMod AI — Settings → System Prompt Template (HA-STRUCT-P1-04).
 */

const PromptTemplateSection = (() => {
    const TEXTAREA_CLS = 'w-full h-full px-4 py-3 text-sm border border-bm-border rounded-lg '
        + 'bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 '
        + 'focus:border-bm-accent resize-none font-mono leading-relaxed';

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
        let settings = [];
        let runtimeMeta = { allowed_variables: [], template_syntax: [] };
        try {
            const [settingsRes, runtimeRes] = await Promise.all([
                apiFetch('/api/settings?category=advanced'),
                apiFetch('/api/runtime/contracts'),
            ]);
            settings = await settingsRes.json();
            runtimeMeta = await runtimeRes.json();
        } catch (err) {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load template.</p>';
            return;
        }

        const templateSetting = settings.find(s => s.key === 'system_prompt_template');
        const templateValue = templateSetting?.value || '';
        const allowedVariables = runtimeMeta?.allowed_variables || [];
        const syntaxExamples = runtimeMeta?.template_syntax || [];

        el.innerHTML = `
            <div class="mb-4">
                <h2 class="text-lg font-semibold">System Prompt Template</h2>
                <p class="text-sm text-bm-muted mt-0.5">The master wrapper around each agent's role and context. Runtime contracts are edited separately in the Runtime Contracts tab.</p>
            </div>
            <div class="mb-4 p-3 bg-slate-50 border border-bm-border rounded-lg">
                <p class="text-xs font-semibold text-bm-muted uppercase tracking-wide mb-2">Template Syntax</p>
                <div class="space-y-1 text-xs font-mono text-bm-muted">
                    ${syntaxExamples.map(ex => `<div>${BossModUtils.escapeHtml(ex)}</div>`).join('')}
                </div>
            </div>
            <div class="flex gap-0 flex-1 min-h-0" style="height: calc(100vh - 300px); min-height: 400px;">
                <!-- Variables panel -->
                <div id="spt-vars-panel" class="shrink-0 overflow-y-auto border border-bm-border rounded-l-lg bg-slate-50 p-3"
                     style="width: 230px;">
                    <p class="text-xs font-semibold text-bm-muted uppercase tracking-wide mb-2">Variables</p>
                    <div class="space-y-1">
                        ${allowedVariables.map(item => {
                            const isSubProp = item.name.includes('.');
                            return `<button type="button" data-var="${BossModUtils.escapeHtml(item.name)}"
                                class="spt-var-btn w-full text-left px-2 py-1.5 rounded hover:bg-white
                                       transition-colors cursor-pointer group ${isSubProp ? 'pl-5' : ''}">
                                <div class="text-xs font-mono text-bm-accent group-hover:text-bm-accent-hover">{{${BossModUtils.escapeHtml(item.name)}}}</div>
                                <div class="text-[11px] text-bm-muted leading-tight">${BossModUtils.escapeHtml(item.description)}</div>
                            </button>`;
                        }).join('')}
                    </div>
                </div>
                <!-- Resize handle -->
                <div id="spt-resize-handle" class="shrink-0 w-1.5 cursor-col-resize bg-bm-border hover:bg-bm-accent/40 transition-colors"></div>
                <!-- Editor panel -->
                <div class="flex-1 flex flex-col min-w-0 border border-l-0 border-bm-border rounded-r-lg bg-white">
                    <div class="px-4 pt-4 shrink-0">
                        <p class="text-xs text-bm-muted">Full wrapper prompt sent before every turn. Controls role framing, context layout, and the rules the model sees.</p>
                    </div>
                    <div class="flex-1 min-h-0 p-4">
                        <textarea id="system-prompt-textarea" class="${TEXTAREA_CLS}">${BossModUtils.escapeHtml(templateValue)}</textarea>
                    </div>
                    <div class="flex items-center gap-3 px-4 pb-4 shrink-0">
                        <button id="btn-save-template"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            Save Template
                        </button>
                        <button id="btn-reset-template"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Reset to Default
                        </button>
                        <span id="template-save-status" class="text-sm text-bm-muted"></span>
                    </div>
                </div>
            </div>`;

        // ─── Resizable vars panel ───
        initResizeHandle(
            document.getElementById('spt-resize-handle'),
            document.getElementById('spt-vars-panel'),
        );

        // ─── Click-to-insert variables ───
        el.querySelectorAll('.spt-var-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const varName = btn.dataset.var;
                const textarea = document.getElementById('system-prompt-textarea');
                insertAtCursor(textarea, `{{${varName}}}`);
            });
        });

        // ─── Save ───
        document.getElementById('btn-save-template').addEventListener('click', async () => {
            const value = document.getElementById('system-prompt-textarea').value;
            const status = document.getElementById('template-save-status');
            try {
                const res = await apiFetch(`/api/settings/system_prompt_template?value=${encodeURIComponent(value)}&category=advanced`, {
                    method: 'PUT',
                });
                if (!res.ok) {
                    const payload = await res.json().catch(() => ({}));
                    throw new Error(payload.detail || 'Save failed');
                }
                status.textContent = 'Saved';
                status.className = 'text-sm text-emerald-600';
                setTimeout(() => { status.textContent = ''; }, 2000);
            } catch (err) {
                status.textContent = err.message || 'Save failed';
                status.className = 'text-sm text-red-600';
            }
        });

        document.getElementById('btn-reset-template').addEventListener('click', async () => {
            if (!confirm('Reset the system prompt template to the seeded default?')) return;
            const textarea = document.getElementById('system-prompt-textarea');
            const status = document.getElementById('template-save-status');
            try {
                const res = await apiFetch('/api/settings/system_prompt_template/reset', {
                    method: 'POST',
                });
                if (!res.ok) {
                    const payload = await res.json().catch(() => ({}));
                    throw new Error(payload.detail || 'Reset failed');
                }
                const setting = await res.json();
                textarea.value = setting.value || '';
                status.textContent = 'Reset to default';
                status.className = 'text-sm text-emerald-600';
                setTimeout(() => { status.textContent = ''; }, 2000);
            } catch (err) {
                status.textContent = err.message || 'Reset failed';
                status.className = 'text-sm text-red-600';
            }
        });
    }

    return { render };
})();
