/**
 * BossMod AI — the agent form's field groups, minus two that outgrew it.
 *
 * One of the modules agent-panel.js became, whose `buildFormHTML` was a single
 * ~380-line function. Each export renders one section, in the order the
 * operator reads them: name, the role contract (specialty, description,
 * colour), the runtime status and recovery tools, and the actions row. Two of
 * the six live elsewhere for the same reason — a fieldset that reaches ~120
 * lines of markup is a module rather than a paragraph: the Advanced disclosure
 * is context/agent-form-advanced.js, and the AI Connections matrix became
 * context/agent-form-connections.js when round five's three-column grid took
 * it past the line. The field vocabulary all of them share is
 * context/agent-fields.js.
 *
 * MARKUP EXEMPTION, declared rather than assumed (Phase 4, Task 4 Step 2).
 * This module builds template strings instead of BossModDom.h, and
 * test_ui_index.py names it explicitly alongside settings/. The h() rule
 * exists for escaping safety and its priority follows the data (spec 6.7):
 * task titles, file names and agent OUTPUT flow through places/, while every
 * value interpolated here is operator-entered configuration — an agent name, a
 * specialty, a connection or personality the operator created in Settings —
 * and each one already passes through BossModFormat.escapeHtml. Converting
 * ~380 lines of working, heavily asserted form markup for a data class the
 * rule was not written for would be scope creep with real regression risk, and
 * would force rewriting every markup assertion in test_hire_ui_poke.py and
 * test_role_contracts.py, which is where a re-point quietly becomes a
 * weakening. The exemption is bounded by name, not by directory prefix.
 */
const BossModAgentFormFields = (() => {
    const FIELDS = BossModAgentFields;

    /**
     * The name field and its duplicate-name warning slot.
     *
     * @param {object|null} agent
     * @returns {string}
     */
    function nameField(agent) {
        return `
        <!-- Name -->
        <div>
            <label class="block text-sm font-medium mb-1">Name</label>
            <input type="text" name="name" required
                   value="${BossModFormat.escapeAttribute(agent?.name || '')}"
                   placeholder="e.g. PM Agent"
                   class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                          bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                          focus:border-bm-accent">
            <p id="agent-name-duplicate-warn" class="hidden text-xs text-amber-800 mt-1">
                An agent with this name already exists. You can still create another.
            </p>
        </div>`;
    }

    /**
     * Specialty, description, and colour — the role contract card.
     *
     * @param {object|null} agent
     * @param {object[]} roster  Peers, so a new agent gets an unused colour.
     * @returns {string}
     */
    function roleContractCard(agent, roster) {
        const defaultColor = agent?.color
            || BossModAgentStatus.nextUnusedAgentColor(roster, { excludeId: agent?.id })
            || '#3b82f6';
        // The swatch IS the avatar: same classes, same derived tint/ink pair,
        // same initial. A raw colour chip was an honest preview of nothing —
        // it showed the seed, while every surface renders the pale tint that
        // core/avatar.js derives from it. Showing the derived pair means a
        // custom hex previews its true rendered appearance before it is saved.
        const previewInitial = BossModFormat.escapeHtml(BossModAvatar.initial(agent?.name));
        // An agent hired before the palette changed holds a colour no swatch
        // offers. Without a swatch to match it no radio is checked, and
        // agent-submit.js's `formData.get('agent-color') || FALLBACK_COLOR`
        // then rewrites that agent to the fallback the moment anything else on
        // this form is saved — silently discarding a colour the operator never
        // touched. Offering the stored colour as its own swatch is what keeps
        // the round trip lossless.
        const known = FIELDS.AGENT_COLORS.some(c => c.value === defaultColor);
        const swatches = known
            ? FIELDS.AGENT_COLORS
            : [{ value: defaultColor, name: 'Current' }, ...FIELDS.AGENT_COLORS];
        const colorOptions = swatches.map(c => {
            const selected = defaultColor === c.value;
            const tint = BossModAvatar.tintFor(c.value);
            return `<label class="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="agent-color" value="${BossModFormat.escapeAttribute(c.value)}"
                       ${selected ? 'checked' : ''}
                       class="hidden peer">
                <span class="avatar avatar-md border-2 peer-checked:border-slate-800 border-transparent
                             transition-all" aria-hidden="true"
                      style="background:${BossModFormat.escapeAttribute(tint.bg)};color:${BossModFormat.escapeAttribute(tint.ink)}">${previewInitial}</span>
                <span class="text-sm">${c.name}</span>
            </label>`;
        }).join('');
        return `
        <div id="role-contract-card" class="space-y-3">
            <div>
                <label class="block text-sm font-medium mb-1">Specialty</label>
                <input type="text" name="role"
                       value="${BossModFormat.escapeAttribute(agent?.role || '')}"
                       placeholder="e.g. Writer, Auditor, Engineer"
                       maxlength="120"
                       class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                              bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                              focus:border-bm-accent">
            </div>
            <div>
                <label class="block text-sm font-medium mb-1">Description</label>
                <textarea name="description" rows="3" maxlength="1000"
                          placeholder="e.g. Writes first drafts and short status notes."
                          class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                 bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                 focus:border-bm-accent">${BossModFormat.escapeHtml(agent?.description || '')}</textarea>
                <p class="text-xs text-bm-muted mt-1">
                    What this agent does. We’ll suggest what done looks like from the specialty.
                </p>
            </div>
            <div>
                <label class="block text-sm font-medium mb-1">Color</label>
                <div class="flex flex-wrap gap-3 mt-1">${colorOptions}</div>
            </div>
        </div>`;
    }

    /**
     * The read-only runtime status pill and the recovery tools.
     *
     * @param {object|null} agent
     * @returns {string} '' while hiring: there is no runtime to reset yet.
     */
    function statusAndRecovery(agent) {
        return `
        <!-- Status (read-only for existing agents) -->
        ${agent ? `
        <div class="pt-2 border-t border-bm-border">
            <div class="flex items-center justify-between text-sm">
                <span class="text-bm-muted">Status</span>
                <span id="agent-runtime-status-pill" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
                             ${BossModFormat.escapeAttribute(BossModAgentStatus.getStatusClasses(agent.status || 'idle', agent.currentActivityKind))}">
                    <span id="agent-runtime-status-dot" class="w-1.5 h-1.5 rounded-full ${BossModFormat.escapeAttribute(BossModAgentStatus.getStatusDot(agent.status || 'idle', agent.currentActivityKind))}"></span>
                    <span id="agent-runtime-status-label">${BossModAgentStatus.getStatusLabel(agent.status || 'idle', agent.currentActivityKind)}</span>
                </span>
            </div>
        </div>

        <div class="border border-amber-200 bg-amber-50 rounded-lg p-3 space-y-3">
            <div>
                <h3 class="text-sm font-semibold text-amber-900">Recovery Tools</h3>
                <p class="text-xs text-amber-800 mt-1">These actions are destructive and cannot be undone.</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button type="button" id="btn-clear-chat-history"
                        class="px-3 py-1.5 border border-amber-300 text-amber-900 rounded-lg
                               hover:bg-amber-100 transition-colors text-sm font-medium">
                    Clear Chat History
                </button>
                <button type="button" id="btn-reset-runtime"
                        class="px-3 py-1.5 border border-red-300 text-red-700 rounded-lg
                               hover:bg-red-50 transition-colors text-sm font-medium">
                    Reset Runtime
                </button>
            </div>
            <div class="text-xs text-amber-900 space-y-1">
                <p><strong>Clear Chat History</strong> deletes only the direct human chat thread for this agent.</p>
                <p><strong>Reset Runtime</strong> cancels active work, clears queued triggers, resets the agent to idle, and may block the active task.</p>
            </div>
        </div>` : ''}`;
    }

    /**
     * The form's own action row: Delete, for an existing agent.
     *
     * The PRIMARY is not here. It was, at the bottom of a form tall enough to
     * scroll, while the dialog's Cancel sat pinned and obvious — and the
     * operator could not find it. It is now `#agent-form-submit` in the
     * dialog's pinned row (context/agent-edit.js), submitting this form from
     * outside it through the HTML `form` attribute, so the form's validation
     * and its submit handler are unchanged.
     *
     * Delete stays. It is destructive and belongs away from the primary, not
     * beside it, which is why it did not travel with it.
     *
     * @param {object|null} agent
     * @returns {string} '' while hiring: there is nothing to delete yet.
     */
    function actionsRow(agent) {
        if (!agent) return '';
        return `
        <!-- Actions -->
        <div class="flex gap-2 pt-2">
            <button type="button" id="btn-delete-agent"
                    class="px-4 py-2 border border-red-300 text-red-600 rounded-lg
                           hover:bg-red-50 transition-colors text-sm font-medium">
                Delete
            </button>
        </div>`;
    }

    return {
        nameField,
        roleContractCard,
        statusAndRecovery,
        actionsRow,
    };
})();
