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
        <div class="field">
            <label class="field-label" for="agent-name">Agent Name</label>
            <input type="text" name="name" id="agent-name" required
                   value="${BossModFormat.escapeAttribute(agent?.name || '')}"
                   placeholder="e.g. PM Agent"
                   class="field-input">
            <p id="agent-name-duplicate-warn" class="hidden field-warn">
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
        // EMPTY while the agent has no name, and never `?`. BossModAvatar's
        // `?` is right on a roster row — a nameless agent still has to be
        // identifiable there — and wrong here, where eight swatches rendered
        // eight question marks on every create and read as a broken control
        // rather than as a colour preview. The glyph is filled in live from the
        // Name field by bindColorSwatchInitial (context/agent-form-bindings.js).
        const previewInitial = agent?.name
            ? BossModFormat.escapeHtml(BossModAvatar.initial(agent.name)) : '';
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
        // `.visually-hidden`, NOT `display: none`. The radio used to carry
        // Tailwind's `hidden`, which takes an input out of the tab order
        // entirely — so the colour picker could not be reached or operated by
        // keyboard at all, and no arrow key moved through the group. base.css's
        // helper hides it from sight while leaving it focusable, which is also
        // what makes the `:checked +` rule below able to paint the swatch.
        const colorOptions = swatches.map(c => {
            const selected = defaultColor === c.value;
            const tint = BossModAvatar.tintFor(c.value);
            return `<label class="color-choice">
                <input type="radio" name="agent-color" value="${BossModFormat.escapeAttribute(c.value)}"
                       ${selected ? 'checked' : ''}
                       class="visually-hidden">
                <span class="avatar avatar-md" aria-hidden="true"
                      style="background:${BossModFormat.escapeAttribute(tint.bg)};color:${BossModFormat.escapeAttribute(tint.ink)}">${previewInitial}</span>
                <span class="color-choice-name">${c.name}</span>
            </label>`;
        }).join('');
        return `
        <div id="role-contract-card" class="role-contract">
            <div class="field">
                <label class="field-label" for="agent-role">Specialty</label>
                <input type="text" name="role" id="agent-role"
                       value="${BossModFormat.escapeAttribute(agent?.role || '')}"
                       placeholder="e.g. Writer, Auditor, Engineer"
                       maxlength="120"
                       class="field-input">
            </div>
            <div class="field">
                <label class="field-label" for="agent-description">Description</label>
                <textarea name="description" id="agent-description" rows="3" maxlength="1000"
                          placeholder="e.g. Writes first drafts and short status notes."
                          class="field-textarea">${BossModFormat.escapeHtml(agent?.description || '')}</textarea>
                <p class="field-hint">
                    What this agent does. We’ll suggest what done looks like from the specialty.
                </p>
            </div>
            <fieldset class="field color-field">
                <legend class="field-label">Color</legend>
                <div class="color-choices">${colorOptions}</div>
            </fieldset>
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
        <div class="agent-runtime-row">
            <span class="field-hint">Status</span>
            <span id="agent-runtime-status-pill" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
                         ${BossModFormat.escapeAttribute(BossModAgentStatus.getStatusClasses(agent.status || 'idle', agent.currentActivityKind))}">
                <span id="agent-runtime-status-dot" class="w-1.5 h-1.5 rounded-full ${BossModFormat.escapeAttribute(BossModAgentStatus.getStatusDot(agent.status || 'idle', agent.currentActivityKind))}"></span>
                <span id="agent-runtime-status-label">${BossModAgentStatus.getStatusLabel(agent.status || 'idle', agent.currentActivityKind)}</span>
            </span>
        </div>

        <section class="form-section danger-section">
            <div>
                <h3 class="danger-title">Recovery Tools</h3>
                <p class="danger-hint">These actions are destructive and cannot be undone.</p>
            </div>
            <div class="danger-actions">
                <button type="button" id="btn-clear-chat-history" class="btn btn-sm">
                    Clear Chat History
                </button>
                <button type="button" id="btn-reset-runtime" class="btn btn-sm btn-danger">
                    Reset Runtime
                </button>
            </div>
            <div class="danger-notes">
                <p><strong>Clear Chat History</strong> deletes only the direct human chat thread for this agent.</p>
                <p><strong>Reset Runtime</strong> cancels active work, clears queued triggers, resets the agent to idle, and may block the active task.</p>
            </div>
        </section>` : ''}`;
    }

    /**
     * The form's own action row: Delete, for an existing agent.
     *
     * The PRIMARY is not here. It was, at the bottom of a form tall enough to
     * scroll, while the dialog's Cancel sat pinned and obvious — and the
     * operator could not find it. It is now `#agent-form-submit` in the
     * dialog's pinned row (context/agent-dialog-footer.js), submitting this
     * form from outside it through the HTML `form` attribute, so the form's
     * validation and its submit handler are unchanged.
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
        <div class="agent-form-actions">
            <button type="button" id="btn-delete-agent" class="btn btn-danger">
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
