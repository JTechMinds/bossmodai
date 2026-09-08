/**
 * BossMod AI — the agent form's field groups, minus the Advanced disclosure.
 *
 * One of the modules agent-panel.js became, whose `buildFormHTML` was a single
 * ~380-line function. Each export renders one section, in the order the
 * operator reads them: name, the role contract (specialty, description,
 * colour), the connection matrix, the runtime status and recovery tools, and
 * the actions row. The Advanced disclosure is the sixth and lives in
 * context/agent-form-advanced.js — on its own it is 120 lines of markup, which
 * is why it is not here. The field vocabulary all of them share is
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
                   value="${BossModFormat.escapeHtml(agent?.name || '')}"
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
        const colorOptions = FIELDS.AGENT_COLORS.map(c => {
            const selected = defaultColor === c.value;
            return `<label class="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="agent-color" value="${c.value}"
                       ${selected ? 'checked' : ''}
                       class="hidden peer">
                <span class="w-6 h-6 rounded-full border-2 peer-checked:border-slate-800 border-transparent
                             transition-all" style="background:${c.value}"></span>
                <span class="text-sm">${c.name}</span>
            </label>`;
        }).join('');
        return `
        <div id="role-contract-card" class="space-y-3">
            <div>
                <label class="block text-sm font-medium mb-1">Specialty</label>
                <input type="text" name="role"
                       value="${BossModFormat.escapeHtml(agent?.role || '')}"
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
     * One connection dropdown for one activation type.
     *
     * @param {object[]} connections
     * @param {string} modelKey
     * @param {string|null} currentValue  The model name stored on the agent.
     * @returns {string}
     */
    function connectionSelect(connections, modelKey, currentValue) {
        const opts = connections.map(c => {
            const label = c.model
                ? `${c.name} (${c.model})`
                : c.name;
            // Match by combining connection fields into what would have been stored
            const selected = currentValue && (
                currentValue === c.model ||
                currentValue === c.name
            );
            return `<option value="${c.id}" ${selected ? 'selected' : ''}>${BossModFormat.escapeHtml(label)}</option>`;
        }).join('');
        return `<select name="${modelKey}"
                    class="flex-1 px-2 py-1.5 text-xs border border-bm-border rounded
                           bg-bm-bg focus:outline-none focus:ring-1 focus:ring-bm-accent/30">
                <option value="">None</option>
                ${opts}
            </select>`;
    }

    /**
     * The model matrix: one connection per activation type, plus "Set All".
     *
     * @param {object|null} agent
     * @param {object[]} connections
     * @returns {string} With no connections configured, a link to Settings —
     *   an empty matrix would look like a broken form.
     */
    function connectionsSection(agent, connections) {
        const noConnections = connections.length === 0;
        const MODEL_TYPES = FIELDS.MODEL_TYPES;
        return `
        <!-- AI Connections (Model Matrix) -->
        <div>
            <label class="block text-sm font-medium mb-2">AI Connections</label>
            ${noConnections
                ? `<p class="text-xs text-bm-muted">No connections configured.
                     <button type="button" id="btn-goto-connections" class="text-bm-accent hover:underline">Add one in Settings</button></p>`
                : `<p class="text-xs text-bm-muted mb-2">Assign an AI connection to each activation type.</p>
                   <div class="space-y-2">
                       <div class="flex items-center gap-2">
                           <span class="text-xs font-medium text-bm-text w-28 shrink-0">Set All</span>
                           <select name="model_all"
                               class="flex-1 px-2 py-1.5 text-xs border border-bm-border rounded
                                      bg-bm-bg focus:outline-none focus:ring-1 focus:ring-bm-accent/30">
                               <option value="">\u2014 Set all connections \u2014</option>
                               ${connections.map(c => {
                                   const label = c.model ? `${c.name} (${c.model})` : c.name;
                                   return `<option value="${c.id}">${BossModFormat.escapeHtml(label)}</option>`;
                               }).join('')}
                           </select>
                       </div>
                       <hr class="border-bm-border">
                       ${MODEL_TYPES.map(t => `
                           <div class="flex items-center gap-2">
                               <span class="text-xs text-bm-muted w-28 shrink-0">${t.label}</span>
                               ${connectionSelect(t.key, agent?.[t.key])}
                           </div>
                       `).join('')}
                   </div>`
            }
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
                             ${BossModAgentStatus.getStatusClasses(agent.status || 'idle', agent.currentActivityKind)}">
                    <span id="agent-runtime-status-dot" class="w-1.5 h-1.5 rounded-full ${BossModAgentStatus.getStatusDot(agent.status || 'idle', agent.currentActivityKind)}"></span>
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
     * Submit and, for an existing agent, Delete.
     *
     * @param {object|null} agent
     * @returns {string}
     */
    function actionsRow(agent) {
        return `
        <!-- Actions -->
        <div class="flex gap-2 pt-2">
            <button type="submit" id="agent-form-submit"
                    class="flex-1 px-4 py-2 bg-bm-accent text-white rounded-lg
                           hover:bg-bm-accent-hover transition-colors text-sm font-medium
                           disabled:opacity-50 disabled:pointer-events-none">
                ${agent ? 'Save Changes' : 'Create Agent'}
            </button>
            ${agent ? `
            <button type="button" id="btn-delete-agent"
                    class="px-4 py-2 border border-red-300 text-red-600 rounded-lg
                           hover:bg-red-50 transition-colors text-sm font-medium">
                Delete
            </button>` : ''}
        </div>`;
    }

    return {
        nameField,
        roleContractCard,
        connectionSelect,
        connectionsSection,
        statusAndRecovery,
        actionsRow,
    };
})();
