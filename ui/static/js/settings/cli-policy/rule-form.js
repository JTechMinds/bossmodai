/**
 * BossMod AI — CLI Policy rule form.
 *
 * The only module that writes a rule. It renders into `#cli-rule-form-slot`,
 * which the Rules tab emits, and reports success through the injected
 * `onSaved` callback rather than re-rendering the tab itself.
 *
 * Ported from cli-policy-section.js unchanged. Every label, option and button
 * string here is a security surface: it is what tells the operator what the
 * rule they are about to save will permit.
 */
const BossModCliPolicyRuleForm = (() => {
    const esc = BossModFormat.escapeHtml;
    const { icons, getAgents } = BossModCliPolicyShared;

    /**
     * Open the rule form in the Rules tab's slot.
     *
     * @param {object|null} rule  The rule to edit, or null for a new rule.
     * @param {object} options
     * @param {() => void} options.onSaved  Called after a successful create or
     *   update, so the caller can re-read the list. The form never re-renders
     *   the tab itself.
     * @returns {void} A no-op when the slot is not on screen.
     */
    function showRuleForm(rule, { onSaved }) {
        const isEdit = !!rule;
        const slot = document.getElementById('cli-rule-form-slot');
        if (!slot) return;

        const agentOptions = getAgents().map(a =>
            `<option value="${esc(a.id)}" ${rule?.agent_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`
        ).join('');

        slot.innerHTML = `
            <div class="border border-bm-accent/30 rounded-xl p-4 bg-bm-accent/5 mb-4">
                <h3 class="text-sm font-semibold mb-3">${isEdit ? 'Edit Rule' : 'New Rule'}</h3>
                <form id="cli-rule-form" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-medium mb-1">Tier</label>
                        <select name="tier" required
                                class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                            <option value="never_allowed" ${rule?.tier === 'never_allowed' ? 'selected' : ''}>Never Allowed</option>
                            <option value="always_allowed" ${rule?.tier === 'always_allowed' ? 'selected' : ''}>Always Allowed</option>
                            <option value="approval_required" ${rule?.tier === 'approval_required' ? 'selected' : ''}>Approval Required</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Match Mode</label>
                        <select name="match_mode" required
                                class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                            <option value="prefix" ${rule?.match_mode === 'prefix' ? 'selected' : ''}>Prefix</option>
                            <option value="exact" ${rule?.match_mode === 'exact' ? 'selected' : ''}>Exact</option>
                            <option value="glob" ${rule?.match_mode === 'glob' ? 'selected' : ''}>Glob</option>
                        </select>
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Pattern</label>
                        <input type="text" name="pattern" required
                               value="${esc(rule?.pattern || '')}"
                               placeholder="e.g. rm -rf, git push --force"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Applies To</label>
                        <select name="agent_id"
                                class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                            <option value="" ${!rule?.agent_id ? 'selected' : ''}>All Agents</option>
                            ${agentOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Priority</label>
                        <input type="number" name="priority"
                               value="${rule?.priority ?? 0}" min="0" max="9999"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Description</label>
                        <input type="text" name="description"
                               value="${esc(rule?.description || '')}"
                               placeholder="What this rule does"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Category</label>
                        <input type="text" name="category"
                               value="${esc(rule?.category || 'general')}"
                               placeholder="e.g. filesystem, network, packages"
                               list="cli-category-suggestions"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                        <datalist id="cli-category-suggestions">
                            <option value="general">
                            <option value="filesystem">
                            <option value="network">
                            <option value="packages">
                            <option value="development">
                            <option value="git">
                            <option value="system">
                        </datalist>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Usage Syntax</label>
                        <input type="text" name="usage_syntax"
                               value="${esc(rule?.usage_syntax || '')}"
                               placeholder="e.g. curl [options] <url>"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Help Text</label>
                        <textarea name="help_text" rows="4"
                                  placeholder="Detailed help shown when agents type: learn commandname"
                                  class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono">${esc(rule?.help_text || '')}</textarea>
                    </div>
                    <div class="md:col-span-2 flex items-center gap-4">
                        <label class="flex items-center gap-2 text-sm">
                            <input type="checkbox" name="enabled" ${rule?.enabled !== false ? 'checked' : ''}
                                   class="rounded border-bm-border text-bm-accent focus:ring-bm-accent/30">
                            Enabled
                        </label>
                    </div>
                    <div class="md:col-span-2 flex gap-2 pt-1">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg text-sm font-medium hover:opacity-90">
                            ${isEdit ? 'Save Changes' : 'Create Rule'}
                        </button>
                        <button type="button" id="btn-cancel-rule-form"
                                class="px-4 py-2 border border-bm-border rounded-lg text-sm font-medium hover:bg-slate-50">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        icons(slot);

        document.getElementById('btn-cancel-rule-form').addEventListener('click', () => {
            slot.innerHTML = '';
        });

        document.getElementById('cli-rule-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const data = {
                tier: fd.get('tier'),
                pattern: fd.get('pattern'),
                match_mode: fd.get('match_mode'),
                agent_id: fd.get('agent_id') || null,
                description: fd.get('description') || null,
                enabled: fd.has('enabled'),
                priority: parseInt(fd.get('priority') || '0', 10),
                category: fd.get('category') || 'general',
                usage_syntax: fd.get('usage_syntax') || null,
                help_text: fd.get('help_text') || null,
            };

            try {
                if (isEdit) {
                    await apiFetchOk(`/api/cli-policy/rules/${rule.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await apiFetchOk('/api/cli-policy/rules', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                onSaved();
            } catch (err) {
                alert(err.message || 'Failed to save rule.');
            }
        });
    }

    return { showRuleForm };
})();
