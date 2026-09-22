/**
 * BossMod AI — the agent form's Advanced disclosure.
 *
 * The sixth field group, split from context/agent-form-fields.js because on
 * its own it is 120 lines of markup: what "done" looks like, the runtime-core
 * preview, personality, desk assignment, and the AI-history policy. Everything
 * here is optional — the disclosure is collapsed by default, which is the
 * point of the section.
 *
 * A RECREATED AGENT'S PROMPT MAY MATCH NO PERSONALITY. The dropdown is matched
 * by `prompt_template` text, and a snapshot can carry a prompt no configured
 * personality holds any more — edited, deleted, or never one of them. Shown as
 * "No personality" it would be dropped on save, so it gets one extra, selected
 * option, `Kept from <name>`, and a hidden input carrying the text itself,
 * which context/agent-submit.js sends. Recreate only: an edit that saves no
 * personality leaves the agent's prompt as it was.
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
     * @param {object[]} view.personalities
     * @param {object[]} view.roster                 Peers, for desk occupancy.
     * @param {object} view.promptHistoryPolicy      Merged over the defaults by
     *   the caller, so every field here has a value to show.
     * @param {{label: string, text: string}|null} [view.keptPrompt]  A
     *   recreated prompt no personality matches: rendered as the selected
     *   `Kept from <name>` option plus the hidden `prompt_template_kept`
     *   input. Offered even when Settings holds no personality at all, since
     *   the prompt would otherwise have nowhere on the form to live.
     * @returns {string}
     */
    function advancedSection(values, view) {
        const { personalities, roster, promptHistoryPolicy } = view;
        const kept = view.keptPrompt || null;
        const DEFAULT_PROMPT_HISTORY_POLICY = BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY;
        const { selectedDesk, noFreeDesk, deskOptions } = BossModAgentFields.deskChoice(values, roster);
        // Personality dropdown — match by prompt_template since agents store
        // the template text, not the personality ID.
        const personalityOptions = personalities.map(p => {
            const selected = values?.prompt_template && values.prompt_template === p.prompt_template;
            return `<option value="${BossModFormat.escapeAttribute(p.id)}" ${selected ? 'selected' : ''}>${BossModFormat.escapeHtml(p.name)}</option>`;
        }).join('');
        const keptOption = kept
            ? `<option value="${BossModFormat.escapeAttribute(BossModAgentFields.KEPT_PERSONALITY)}" selected>${BossModFormat.escapeHtml(kept.label)}</option>`
            : '';
        const keptInput = kept
            ? `<input type="hidden" name="prompt_template_kept" value="${BossModFormat.escapeAttribute(kept.text)}">`
            : '';
        const noPersonalities = personalities.length === 0 && !kept;
        const earliestAllowedValue = promptHistoryPolicy.earliest_ts_allowed
            ? new Date(promptHistoryPolicy.earliest_ts_allowed).toISOString().slice(0, 16)
            : '';
        return `
        <section class="form-section">
            <button type="button" id="advanced-toggle" class="advanced-toggle">
                <span>
                    <span class="advanced-title">Advanced</span>
                    <span class="field-hint">
                        Optional: what done looks like, runtime core, prompt template, and desk.
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
                    <input type="text" name="done_fail_bar" id="agent-done-fail-bar"
                           value="${BossModFormat.escapeAttribute(values?.done_fail_bar || '')}"
                           placeholder="Suggested from specialty. Editable."
                           maxlength="500"
                           class="field-input">
                    <p class="field-hint">
                        Optional. We’ll suggest one from the specialty; edit anytime.
                    </p>
                </div>
                ${communicationFields(values)}
                <div class="field">
                    <span class="field-label">Runtime core</span>
                    <pre id="runtime-core-preview" class="runtime-core-preview"></pre>
                    <p class="field-hint">
                        Shared every turn. Not a hire novel — specialty quality bars stay in Description.
                    </p>
                </div>
                <div class="field">
                    <label class="field-label" for="agent-personality">Personality</label>
                    ${noPersonalities
                        ? `<p class="field-hint">No personalities configured.
                             <button type="button" id="btn-goto-personalities" class="btn-link">Add one in Settings</button></p>`
                        : `<p class="field-hint">Optional. Copies a prompt template into this agent; leave empty to keep the default.</p>
                           <select name="personality_id" id="agent-personality" class="field-select">
                               <option value="">No personality</option>
                               ${personalityOptions}
                               ${keptOption}
                           </select>
                           ${keptInput}`
                    }
                </div>
                <div class="field">
                    <label class="field-label" for="agent-desk">Desk Assignment</label>
                    <select name="desk" id="agent-desk" class="field-select">
                        <option value="" ${selectedDesk ? '' : 'selected'}>Unassigned</option>
                        ${deskOptions}
                    </select>
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

    function communicationFields(values) {
        const comm = BossModCommunication.resolve(values?.communication, values?.role || '');
        const select = (key) => {
            const options = BossModCommunication.ENUMS[key].map((value) => {
                const selected = value === comm[key] ? 'selected' : '';
                return `<option value="${BossModFormat.escapeAttribute(value)}" ${selected}>${BossModFormat.escapeHtml(value)}</option>`;
            }).join('');
            const field = BossModFormat.escapeAttribute(key);
            const label = BossModFormat.escapeHtml(BossModCommunication.LABELS[key]);
            return `
                <div class="field">
                    <label class="field-label field-label-sm" for="agent-communication-${field}">${label}</label>
                    <select name="communication_${field}" id="agent-communication-${field}" class="field-select">
                        ${options}
                    </select>
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
