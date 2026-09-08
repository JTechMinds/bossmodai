/**
 * BossMod AI — the agent form's Advanced disclosure.
 *
 * The sixth field group, split from context/agent-form-fields.js because on
 * its own it is 120 lines of markup: what "done" looks like, the runtime-core
 * preview, personality, desk assignment, and the AI-history policy. Everything
 * here is optional — the disclosure is collapsed by default, which is the
 * point of the section.
 *
 * MARKUP EXEMPTION: see the block comment in context/agent-form-fields.js.
 * The same reasoning covers this file, and test_ui_index.py names both.
 */
const BossModAgentFormAdvanced = (() => {

    /**
     * Build the Advanced block.
     *
     * @param {object|null} agent
     * @param {object} view
     * @param {object[]} view.personalities
     * @param {object[]} view.roster                 Peers, for desk occupancy.
     * @param {object} view.promptHistoryPolicy      Merged over the defaults by
     *   the caller, so every field here has a value to show.
     * @returns {string}
     */
    function advancedSection(agent, view) {
        const { personalities, roster, promptHistoryPolicy } = view;
        const DEFAULT_PROMPT_HISTORY_POLICY = BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY;
        const { selectedDesk, noFreeDesk, deskOptions } = BossModAgentFields.deskChoice(agent, roster);
        // Personality dropdown — match by prompt_template since agents store
        // the template text, not the personality ID.
        const personalityOptions = personalities.map(p => {
            const selected = agent?.prompt_template && agent.prompt_template === p.prompt_template;
            return `<option value="${p.id}" ${selected ? 'selected' : ''}>${BossModFormat.escapeHtml(p.name)}</option>`;
        }).join('');
        const noPersonalities = personalities.length === 0;
        const earliestAllowedValue = promptHistoryPolicy.earliest_ts_allowed
            ? new Date(promptHistoryPolicy.earliest_ts_allowed).toISOString().slice(0, 16)
            : '';
        return `
        <div class="border border-bm-border rounded-lg p-3 bg-white">
            <button type="button" id="advanced-toggle"
                    class="w-full flex items-center justify-between text-left">
                <div>
                    <h3 class="text-sm font-semibold">Advanced</h3>
                    <p class="text-xs text-bm-muted mt-1">
                        Optional: what done looks like, runtime core, prompt template, and desk.
                    </p>
                </div>
                <i data-lucide="chevron-right" class="w-4 h-4 text-bm-muted shrink-0 transition-transform" id="advanced-chevron"></i>
            </button>
            <div id="advanced-content" class="hidden mt-3 space-y-3">
                <div>
                    <div class="flex items-center justify-between gap-2 mb-1">
                        <label class="block text-sm font-medium">What “done” looks like</label>
                        <button type="button" id="btn-suggest-finish-line"
                                class="text-xs text-bm-accent hover:underline shrink-0">
                            Suggest
                        </button>
                    </div>
                    <input type="text" name="done_fail_bar"
                           value="${BossModFormat.escapeAttribute(agent?.done_fail_bar || '')}"
                           placeholder="Suggested from specialty. Editable."
                           maxlength="500"
                           class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent">
                    <p class="text-xs text-bm-muted mt-1">
                        Optional. We’ll suggest one from the specialty; edit anytime.
                    </p>
                </div>
                <div>
                    <label class="block text-sm font-medium mb-1">Runtime core</label>
                    <pre id="runtime-core-preview"
                         class="runtime-core-preview text-xs whitespace-pre-wrap font-mono
                                px-3 py-2 border border-bm-border rounded-lg bg-bm-bg text-bm-text"></pre>
                    <p class="text-xs text-bm-muted mt-1">
                        Shared every turn. Not a hire novel — specialty quality bars stay in Description.
                    </p>
                </div>
                <div>
                    <label class="block text-sm font-medium mb-1">Personality</label>
                    ${noPersonalities
                        ? `<p class="text-xs text-bm-muted mb-1.5">No personalities configured.
                             <button type="button" id="btn-goto-personalities" class="text-bm-accent hover:underline">Add one in Settings</button></p>`
                        : `<p class="text-xs text-bm-muted mb-1.5">Optional. Copies a prompt template into this agent; leave empty to keep the default.</p>
                           <select name="personality_id"
                                   class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                          bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                          focus:border-bm-accent">
                               <option value="">No personality</option>
                               ${personalityOptions}
                           </select>`
                    }
                </div>
                <div>
                    <label class="block text-sm font-medium mb-1">Desk Assignment</label>
                    <select name="desk"
                            class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                   bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                   focus:border-bm-accent">
                        <option value="" ${selectedDesk ? '' : 'selected'}>Unassigned</option>
                        ${deskOptions}
                    </select>
                    ${noFreeDesk
                        ? `<p class="text-xs text-amber-800 mt-1">No empty desk is free. This agent will stay unassigned.</p>`
                        : `<p class="text-xs text-bm-muted mt-1">An empty desk is selected when one is free.</p>`}
                </div>
                <div>
                    <h4 class="text-xs font-semibold text-bm-text">AI History</h4>
                    <p class="text-xs text-bm-muted mt-1 mb-2">
                        Controls the backend view used for model-visible conversation history.
                    </p>
                </div>
                <div class="grid grid-cols-1 gap-3">
                    <div>
                        <label class="block text-xs font-medium mb-1">Last N History Items</label>
                        <input type="number"
                               min="0"
                               max="500"
                               name="prompt_history_last_n"
                               value="${BossModFormat.escapeAttribute(String(promptHistoryPolicy.last_n_histories ?? DEFAULT_PROMPT_HISTORY_POLICY.last_n_histories))}"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Max History Tokens</label>
                        <input type="number"
                               min="0"
                               max="50000"
                               name="prompt_history_max_tokens"
                               value="${BossModFormat.escapeAttribute(String(promptHistoryPolicy.max_allowed_history_tokens ?? DEFAULT_PROMPT_HISTORY_POLICY.max_allowed_history_tokens))}"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Earliest Allowed Timestamp</label>
                        <input type="datetime-local"
                               name="prompt_history_earliest_ts"
                               value="${BossModFormat.escapeAttribute(earliestAllowedValue)}"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                        <p class="text-[11px] text-bm-muted mt-1">
                            Leave empty to allow older history. Set this to make the agent ignore anything before a cutoff.
                        </p>
                    </div>
                    <label class="inline-flex items-center gap-2 text-sm text-bm-text cursor-pointer">
                        <input type="checkbox"
                               name="prompt_history_include_notifications"
                               class="rounded border-bm-border text-bm-accent focus:ring-bm-accent/30"
                               ${promptHistoryPolicy.include_notifications ? 'checked' : ''}>
                        <span>Include prompt-visible runtime notifications</span>
                    </label>
                </div>
            </div>
        </div>`;
    }

    return { advancedSection };
})();
