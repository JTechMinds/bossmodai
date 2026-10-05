/**
 * BossMod AI — the agent form's Advanced disclosure.
 *
 * The sixth field group, split from context/agent-form-fields.js because on
 * its own it is 120 lines of markup: what "done" looks like, the runtime-core
 * preview, desk assignment, and the AI-history policy. Everything here is
 * optional — the disclosure is collapsed by default, which is the point of the
 * section.
 *
 * The dropdowns — desk, communication — are rendered here only
 * as empty mount points; context/agent-form-choices.js builds them.
 *
 * It no longer imports a pack from a URL. That box could fill these fields and
 * then had nowhere to keep what it fetched, so a URL import was a dead end;
 * installing from a URL is the marketplace's "Install from URL", which saves a
 * template the picker can offer again.
 *
 * MARKUP EXEMPTION: see the block comment in context/agent-form-fields.js.
 * The same reasoning covers this file, and test_ui_index.py names both.
 */
const BossModAgentFormAdvanced = (() => {

    /**
     * Build the Advanced block.
     *
     * @param {object|null} values  What the fields show: the agent being
     *   edited, or the snapshot being recreated. null for a blank form.
     * @param {object} view
     * @param {object[]} view.roster                 Peers, for desk occupancy.
     * @param {object[]} view.desks                  The map's `desks`.
     * @param {string|null} view.floorId             The floor whose desk
     *   occupancy counts: the agent's own, or the hire floor.
     * @param {object} view.promptHistoryPolicy      Merged over the defaults by
     *   the caller, so every field here has a value to show.
     * @returns {string}
     */
    function advancedSection(values, view) {
        const { roster, desks, floorId, promptHistoryPolicy } = view;
        const DEFAULT_PROMPT_HISTORY_POLICY = BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY;
        const { noFreeDesk } = BossModAgentFields.deskChoice(values, roster, desks, floorId);
        const earliestAllowedValue = promptHistoryPolicy.earliest_ts_allowed
            ? new Date(promptHistoryPolicy.earliest_ts_allowed).toISOString().slice(0, 16)
            : '';
        return `
        <section class="form-section">
            <button type="button" id="advanced-toggle" class="advanced-toggle">
                <span>
                    <span class="advanced-title">Advanced</span>
                    <span class="field-hint">
                        Optional: what done looks like, runtime core, and desk.
                    </span>
                </span>
                <i data-lucide="chevron-right" class="advanced-chevron" id="advanced-chevron"></i>
            </button>
            <div id="advanced-content" class="hidden advanced-content">
                <div class="field">
                    <div class="advanced-field-head">
                        <label class="field-label" for="agent-done-fail-bar">What “done” looks like</label>
                        <button type="button" id="btn-suggest-finish-line" class="btn-link">
                            Suggest
                        </button>
                    </div>
                    <textarea name="done_fail_bar" id="agent-done-fail-bar" data-autogrow
                              placeholder="Suggested from specialty. Editable."
                              class="field-textarea">${BossModFormat.escapeHtml(values?.done_fail_bar || '')}</textarea>
                    <p class="field-hint">
                        Optional. We’ll suggest one from the specialty; edit anytime.
                    </p>
                </div>
                ${communicationFields()}
                <div class="field">
                    <span class="field-label">Runtime core</span>
                    <pre id="runtime-core-preview" class="runtime-core-preview"></pre>
                    <p class="field-hint">
                        Shared every turn. Not a hire novel — specialty quality bars stay in Description.
                    </p>
                </div>
                <div class="field">
                    <label class="field-label" for="agent-desk">Desk Assignment</label>
                    <span id="agent-desk-mount"></span>
                    ${noFreeDesk
                        ? `<p class="field-warn">No empty desk is free. This agent will stay unassigned.</p>`
                        : `<p class="field-hint">An empty desk is selected when one is free.</p>`}
                </div>
                <div>
                    <h4 class="advanced-subtitle">AI History</h4>
                    <p class="field-hint">
                        Controls the backend view used for model-visible conversation history.
                    </p>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label class="field-label field-label-sm" for="agent-history-last-n">Last N History Items</label>
                        <input type="number"
                               min="0"
                               max="500"
                               name="prompt_history_last_n"
                               id="agent-history-last-n"
                               value="${BossModFormat.escapeAttribute(String(promptHistoryPolicy.last_n_histories ?? DEFAULT_PROMPT_HISTORY_POLICY.last_n_histories))}"
                               class="field-input">
                    </div>
                    <div class="field">
                        <label class="field-label field-label-sm" for="agent-history-max-tokens">Max History Tokens</label>
                        <input type="number"
                               min="0"
                               max="50000"
                               name="prompt_history_max_tokens"
                               id="agent-history-max-tokens"
                               value="${BossModFormat.escapeAttribute(String(promptHistoryPolicy.max_allowed_history_tokens ?? DEFAULT_PROMPT_HISTORY_POLICY.max_allowed_history_tokens))}"
                               class="field-input">
                    </div>
                </div>
                <div class="field">
                    <label class="field-label field-label-sm" for="agent-history-earliest">Earliest Allowed Timestamp</label>
                    <input type="datetime-local"
                           name="prompt_history_earliest_ts"
                           id="agent-history-earliest"
                           value="${BossModFormat.escapeAttribute(earliestAllowedValue)}"
                           class="field-input">
                    <p class="field-hint">
                        Leave empty to allow older history. Set this to make the agent ignore anything before a cutoff.
                    </p>
                </div>
                <label class="advanced-check">
                    <input type="checkbox"
                           name="prompt_history_include_notifications"
                           ${promptHistoryPolicy.include_notifications ? 'checked' : ''}>
                    <span>Include prompt-visible runtime notifications</span>
                </label>
            </div>
        </section>`;
    }

    /**
     * The Communication block: a label and an empty mount point per enum.
     * The controls, and the values they start on, are
     * context/agent-form-choices.js's.
     *
     * @returns {string}
     */
    function communicationFields() {
        const select = (key) => {
            const field = BossModFormat.escapeAttribute(key);
            const label = BossModFormat.escapeHtml(BossModCommunication.LABELS[key]);
            return `
                <div class="field">
                    <label class="field-label field-label-sm" for="agent-communication-${field}">${label}</label>
                    <span id="agent-communication-${field}-mount"></span>
                </div>`;
        };
        return `
                <div class="field">
                    <span class="field-label">Communication</span>
                    <p class="field-hint">
                        Closed enums from the pack. Edit anytime; not a prose style guide.
                    </p>
                    <div class="field-row">
                        ${select('tone')}
                        ${select('density')}
                    </div>
                    <div class="field-row">
                        ${select('jargon')}
                        ${select('audience')}
                    </div>
                </div>`;
    }

    return { advancedSection };
})();
