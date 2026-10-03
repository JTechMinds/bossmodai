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
    const escAttr = BossModFormat.escapeAttribute;
    const { icons, getAgents, agentName, announceApplied } = BossModCliPolicyShared;

    const TIERS = Object.freeze([
        { value: 'never_allowed', label: 'Never Allowed' },
        { value: 'always_allowed', label: 'Always Allowed' },
        { value: 'approval_required', label: 'Approval Required' },
    ]);
    const MATCH_MODES = Object.freeze([
        { value: 'prefix', label: 'Prefix' },
        { value: 'exact', label: 'Exact' },
        { value: 'glob', label: 'Glob' },
    ]);

    /**
     * Who a rule can apply to: everyone, then each agent on the roster.
     *
     * @param {object[]} agents
     * @param {string|null} current  The rule's `agent_id`. One the roster no
     *   longer lists stays offered under its raw id (as
     *   BossModCliPolicyShared.agentName names it), so opening a rule to edit
     *   never silently widens it to every agent.
     * @returns {Array<{value: string, label: string}>}
     */
    function appliesToOptions(agents, current) {
        const options = [{ value: '', label: 'All Agents' },
            ...agents.map((a) => ({ value: String(a.id), label: a.name }))];
        if (current && !options.some((option) => option.value === current)) {
            options.push({ value: current, label: agentName(current) });
        }
        return options;
    }

    /**
     * Build the form's three dropdowns into their mount points. Each carries
     * its form value (core/menu-select.js `name`), which the submit reads
     * through FormData like any other field.
     *
     * @param {HTMLElement} slot
     * @param {object|null} rule
     * @returns {void}
     * @throws {Error} When a mount point the markup renders is missing.
     */
    function mountChoices(slot, rule) {
        const place = (mountId, deps) => {
            const point = slot.querySelector(`#${mountId}`);
            if (!point) throw new Error(`[cli-rule-form] the form has no #${mountId}`);
            point.append(BossModMenuSelect.create({ ...deps, variant: 'field' }).element);
        };
        place('cli-rule-tier-mount', {
            label: 'Tier', name: 'tier', id: 'cli-rule-tier',
            options: TIERS, value: rule ? rule.tier : undefined,
        });
        place('cli-rule-match-mode-mount', {
            label: 'Match Mode', name: 'match_mode', id: 'cli-rule-match-mode',
            options: MATCH_MODES, value: rule ? rule.match_mode : undefined,
        });
        place('cli-rule-agent-mount', {
            label: 'Applies To', name: 'agent_id', id: 'cli-rule-agent',
            options: appliesToOptions(getAgents(), rule?.agent_id || null), value: rule?.agent_id || '',
        });
    }

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

        slot.innerHTML = `
            <div class="border border-bm-accent/30 rounded-xl p-4 bg-bm-accent/5 mb-4">
                <h3 class="text-sm font-semibold mb-3">${isEdit ? 'Edit Rule' : 'New Rule'}</h3>
                <form id="cli-rule-form" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-medium mb-1" for="cli-rule-tier">Tier</label>
                        <div id="cli-rule-tier-mount"></div>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1" for="cli-rule-match-mode">Match Mode</label>
                        <div id="cli-rule-match-mode-mount"></div>
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Pattern</label>
                        <input type="text" name="pattern" required
                               value="${escAttr(rule?.pattern || '')}"
                               placeholder="e.g. rm -rf, git push --force"
                               class="field-input field-mono">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1" for="cli-rule-agent">Applies To</label>
                        <div id="cli-rule-agent-mount"></div>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Priority</label>
                        <input type="number" name="priority"
                               value="${escAttr(String(rule?.priority ?? 0))}" min="0" max="9999"
                               class="field-input">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">CWD prefix</label>
                        <input type="text" name="cwd_prefix"
                               value="${escAttr(rule?.cwd_prefix || '')}"
                               placeholder="Optional. Nest Always uses /me/host-work"
                               class="field-input field-mono">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Description</label>
                        <input type="text" name="description"
                               value="${escAttr(rule?.description || '')}"
                               placeholder="What this rule does"
                               class="field-input">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Category</label>
                        <input type="text" name="category"
                               value="${escAttr(rule?.category || 'general')}"
                               placeholder="e.g. filesystem, network, packages"
                               list="cli-category-suggestions"
                               class="field-input">
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
                               value="${escAttr(rule?.usage_syntax || '')}"
                               placeholder="e.g. curl [options] <url>"
                               class="field-input field-mono">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Help Text</label>
                        <textarea name="help_text" data-autogrow
                                  placeholder="Detailed help shown when agents type: learn commandname"
                                  class="field-textarea field-mono">${esc(rule?.help_text || '')}</textarea>
                    </div>
                    <div class="md:col-span-2 flex items-center gap-4">
                        <label class="flex items-center gap-2 text-sm">
                            <input type="checkbox" name="enabled" ${rule?.enabled !== false ? 'checked' : ''}
                                   class="rounded border-bm-border text-bm-accent">
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

        mountChoices(slot, rule);
        icons(slot);
        slot.querySelectorAll('textarea[data-autogrow]').forEach(BossModAutoGrow.bind);

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
                cwd_prefix: fd.get('cwd_prefix') || null,
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
                announceApplied();
                onSaved();
            } catch (err) {
                alert(err.message || 'Failed to save rule.');
            }
        });
    }

    return { showRuleForm };
})();
