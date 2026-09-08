/**
 * BossMod AI — Settings → Runtime Contracts, saving and previewing.
 *
 * The four things the editor's bottom bar does — Save, Reset to Defaults,
 * Refresh, and Render Full Prompt — plus the two pieces every one of them
 * needs: reading the six editors back out, and reporting the outcome on the
 * prompt-health panel. Split out of settings-runtime-contracts.js in Phase 3C
 * at the editor-versus-persistence seam.
 *
 * The health panel lives here rather than with the editor because every action
 * ends by repainting it — save, reset and preview all return a fresh
 * `prompt_health` — and it gets exactly one home. The editor calls it once, on
 * load, through this module.
 */
const BossModRuntimeContractActions = (() => {

    /**
     * Read the six contract editors back out.
     *
     * @returns {object} The template payload, keyed as the API expects. Read
     *   from the DOM at call time so Save and Preview always send what is on
     *   screen rather than what was loaded.
     */
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

    /**
     * Paint the prompt-health panel.
     *
     * @param {Element|null} container  `#runtime-prompt-health`.
     * @param {object|null} health  `{ status, issues }` from the backend;
     *   a missing one reads as clean, which is what the load path relies on.
     * @returns {void}
     */
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
                        <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${issue.severity === 'error' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}">${BossModFormat.escapeHtml(issue.severity || 'warning')}</span>
                        <span class="text-sm font-medium">${BossModFormat.escapeHtml(issue.surface_label || issue.surface_key || 'Prompt Surface')}</span>
                    </div>
                    <div class="mt-1 text-sm text-bm-text">${BossModFormat.escapeHtml(issue.message || '')}</div>
                </li>
            `).join('')}</ul>`
            : '';
        container.innerHTML = `
            <div class="p-3 border rounded-lg ${BossModFormat.escapeAttribute(tone.panel)}">
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${BossModFormat.escapeAttribute(tone.badge)}">${BossModFormat.escapeHtml(status)}</span>
                    <p class="text-sm font-medium text-bm-text">${BossModFormat.escapeHtml(tone.title)}</p>
                </div>
                <p class="mt-1 text-sm text-bm-muted">${BossModFormat.escapeHtml(tone.detail)}</p>
                ${issuesHtml}
            </div>`;
    }

    /**
     * Bind the bottom bar's four actions.
     *
     * @param {object} options
     * @param {() => void} options.onRefresh  Re-render the whole section. The
     *   editor owns its own render, so Refresh is injected rather than reached
     *   for.
     * @returns {void} Every control is looked up by id, the way the section it
     *   came from did — there is nothing to scope to.
     */
    function bindActions({ onRefresh }) {
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
            onRefresh();
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

    return { collectTemplateValues, renderPromptHealth, bindActions };
})();
