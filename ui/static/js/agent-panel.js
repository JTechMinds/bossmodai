/**
 * BossMod AI — Agent panel overlay.
 *
 * Handles the slide-in panel for viewing, creating, editing,
 * and deleting agents. Uses AI Connections and Personalities
 * from Settings for model and prompt selection.
 */

const AgentPanel = (() => {
    // ─── State ───
    let currentAgentId = null;
    let isCreating = false;

    // ─── Available colors for agents ───
    const AGENT_COLORS = [
        { name: 'Blue',    value: '#3b82f6' },
        { name: 'Amber',   value: '#f59e0b' },
        { name: 'Emerald', value: '#10b981' },
        { name: 'Rose',    value: '#f43f5e' },
        { name: 'Purple',  value: '#8b5cf6' },
        { name: 'Cyan',    value: '#06b6d4' },
        { name: 'Orange',  value: '#f97316' },
        { name: 'Pink',    value: '#ec4899' },
    ];

    // ─── Desk assignment options (from tilemap) ───
    const DESK_OPTIONS = [
        { id: 'desk_1', x: 3,  y: 4,  label: 'Desk 1 — Main NW' },
        { id: 'desk_2', x: 7,  y: 4,  label: 'Desk 2 — Main N' },
        { id: 'desk_3', x: 11, y: 4,  label: 'Desk 3 — Main NE' },
        { id: 'desk_4', x: 3,  y: 6,  label: 'Desk 4 — Main SW' },
        { id: 'desk_5', x: 7,  y: 6,  label: 'Desk 5 — Main S' },
        { id: 'desk_6', x: 3,  y: 15, label: 'Desk 6 — South NW' },
        { id: 'desk_7', x: 7,  y: 15, label: 'Desk 7 — South N' },
        { id: 'desk_8', x: 11, y: 15, label: 'Desk 8 — South NE' },
    ];

    // Model assignment types
    const MODEL_TYPES = [
        { key: 'model_social',     label: 'Social (cheap)' },
        { key: 'model_work',       label: 'Work (routine)' },
        { key: 'model_reasoning',  label: 'Reasoning (deep)' },
        { key: 'model_extraction', label: 'Extraction' },
        { key: 'model_self_queue', label: 'Self-queue' },
    ];

    const DEFAULT_PROMPT_HISTORY_POLICY = {
        last_n_histories: 30,
        max_allowed_history_tokens: 2000,
        earliest_ts_allowed: null,
        include_notifications: true,
    };

    // ─── API calls ───

    async function fetchAgent(id) {
        const res = await fetch(`/api/agents/${id}`);
        if (!res.ok) return null;
        return res.json();
    }

    async function apiCreateAgent(data) {
        const res = await fetch('/api/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function apiUpdateAgent(id, data) {
        const res = await fetch(`/api/agents/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function apiDeleteAgent(id) {
        const res = await fetch(`/api/agents/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await res.text());
    }

    async function fetchPromptHistoryPolicy(id) {
        const res = await fetch(`/api/agents/${id}/prompt-history-policy`, { cache: 'no-store' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function apiUpdatePromptHistoryPolicy(id, data) {
        const res = await fetch(`/api/agents/${id}/prompt-history-policy`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function apiClearChatHistory(id) {
        const res = await fetch(`/api/agents/${id}/chat-history`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function apiResetRuntime(id) {
        const res = await fetch(`/api/agents/${id}/reset-runtime`, { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    // ─── Build form HTML into a container ───

    async function buildFormHTML(container, agent = null) {

        // Fetch connections and personalities for dropdowns
        let connections = [];
        let personalities = [];
        let promptHistoryPolicy = { ...DEFAULT_PROMPT_HISTORY_POLICY };
        try {
            const requests = [
                fetch('/api/connections'),
                fetch('/api/personalities'),
            ];
            if (agent?.id) {
                requests.push(fetchPromptHistoryPolicy(agent.id));
            }
            const [connRes, persRes, policyRes] = await Promise.all(requests);
            connections = await connRes.json();
            personalities = await persRes.json();
            if (policyRes) {
                promptHistoryPolicy = { ...DEFAULT_PROMPT_HISTORY_POLICY, ...policyRes };
            }
        } catch (err) {
            console.error('[AgentPanel] Failed to load agent editor dependencies:', err);
        }

        const colorOptions = AGENT_COLORS.map(c => {
            const selected = (agent?.color || '#3b82f6') === c.value;
            return `<label class="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="agent-color" value="${c.value}"
                       ${selected ? 'checked' : ''}
                       class="hidden peer">
                <span class="w-6 h-6 rounded-full border-2 peer-checked:border-slate-800 border-transparent
                             transition-all" style="background:${c.value}"></span>
                <span class="text-sm">${c.name}</span>
            </label>`;
        }).join('');

        const deskOptions = DESK_OPTIONS.map(d => {
            const selected = agent?.desk_x === d.x && agent?.desk_y === d.y;
            return `<option value="${d.x},${d.y}" ${selected ? 'selected' : ''}>${d.label}</option>`;
        }).join('');

        // Personality dropdown
        const personalityOptions = personalities.map(p =>
            `<option value="${p.id}">${BossModUtils.escapeHtml(p.name)}</option>`
        ).join('');

        // Connection dropdown builder (for each model type)
        function connectionSelect(modelKey, currentValue) {
            const opts = connections.map(c => {
                const label = c.model
                    ? `${c.name} (${c.model})`
                    : c.name;
                // Match by combining connection fields into what would have been stored
                const selected = currentValue && (
                    currentValue === c.model ||
                    currentValue === c.name
                );
                return `<option value="${c.id}" ${selected ? 'selected' : ''}>${BossModUtils.escapeHtml(label)}</option>`;
            }).join('');
            return `<select name="${modelKey}"
                        class="flex-1 px-2 py-1.5 text-xs border border-bm-border rounded
                               bg-bm-bg focus:outline-none focus:ring-1 focus:ring-bm-accent/30">
                    <option value="">None</option>
                    ${opts}
                </select>`;
        }

        const noConnections = connections.length === 0;
        const noPersonalities = personalities.length === 0;
        const earliestAllowedValue = promptHistoryPolicy.earliest_ts_allowed
            ? new Date(promptHistoryPolicy.earliest_ts_allowed).toISOString().slice(0, 16)
            : '';

        container.innerHTML = `
        <form id="agent-form" class="space-y-4">
            <!-- Name -->
            <div>
                <label class="block text-sm font-medium mb-1">Name</label>
                <input type="text" name="name" required
                       value="${BossModUtils.escapeHtml(agent?.name || '')}"
                       placeholder="e.g. PM Agent"
                       class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                              bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                              focus:border-bm-accent">
            </div>

            <!-- Role -->
            <div>
                <label class="block text-sm font-medium mb-1">Role</label>
                <input type="text" name="role"
                       value="${BossModUtils.escapeHtml(agent?.role || '')}"
                       placeholder="e.g. Product Manager"
                       class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                              bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                              focus:border-bm-accent">
            </div>

            <!-- Personality -->
            <div>
                <label class="block text-sm font-medium mb-1">Personality</label>
                ${noPersonalities
                    ? `<p class="text-xs text-bm-muted mb-1.5">No personalities configured.
                         <button type="button" id="btn-goto-personalities" class="text-bm-accent hover:underline">Add one in Settings</button></p>`
                    : `<p class="text-xs text-bm-muted mb-1.5">Copies the prompt template into this agent.</p>
                       <select name="personality_id"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                           <option value="">No personality</option>
                           ${personalityOptions}
                       </select>`
                }
            </div>

            <!-- Color -->
            <div>
                <label class="block text-sm font-medium mb-1">Color</label>
                <div class="flex flex-wrap gap-3 mt-1">${colorOptions}</div>
            </div>

            <!-- Desk -->
            <div>
                <label class="block text-sm font-medium mb-1">Desk Assignment</label>
                <select name="desk"
                        class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                               bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                               focus:border-bm-accent">
                    <option value="">Unassigned</option>
                    ${deskOptions}
                </select>
            </div>

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
                                       return `<option value="${c.id}">${BossModUtils.escapeHtml(label)}</option>`;
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
            </div>

            <div class="border border-bm-border rounded-lg p-3 bg-white">
                <button type="button" id="ai-history-toggle"
                        class="w-full flex items-center justify-between text-left">
                    <div>
                        <h3 class="text-sm font-semibold">AI History</h3>
                        <p class="text-xs text-bm-muted mt-1">
                            Controls the backend view used for model-visible conversation history.
                        </p>
                    </div>
                    <i data-lucide="chevron-right" class="w-4 h-4 text-bm-muted shrink-0 transition-transform" id="ai-history-chevron"></i>
                </button>
                <div id="ai-history-content" class="hidden mt-3 space-y-3">
                    <div class="grid grid-cols-1 gap-3">
                        <div>
                            <label class="block text-xs font-medium mb-1">Last N History Items</label>
                            <input type="number"
                                   min="0"
                                   max="500"
                                   name="prompt_history_last_n"
                                   value="${BossModUtils.escapeHtml(String(promptHistoryPolicy.last_n_histories ?? DEFAULT_PROMPT_HISTORY_POLICY.last_n_histories))}"
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
                                   value="${BossModUtils.escapeHtml(String(promptHistoryPolicy.max_allowed_history_tokens ?? DEFAULT_PROMPT_HISTORY_POLICY.max_allowed_history_tokens))}"
                                   class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                          bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                          focus:border-bm-accent">
                        </div>
                        <div>
                            <label class="block text-xs font-medium mb-1">Earliest Allowed Timestamp</label>
                            <input type="datetime-local"
                                   name="prompt_history_earliest_ts"
                                   value="${BossModUtils.escapeHtml(earliestAllowedValue)}"
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
            </div>

            <!-- Status (read-only for existing agents) -->
            ${agent ? `
            <div class="pt-2 border-t border-bm-border">
                <div class="flex items-center justify-between text-sm">
                    <span class="text-bm-muted">Status</span>
                    <span id="agent-runtime-status-pill" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
                                 ${BossModUtils.getStatusClasses(agent.status || 'idle', agent.currentActivityKind)}">
                        <span id="agent-runtime-status-dot" class="w-1.5 h-1.5 rounded-full ${BossModUtils.getStatusDot(agent.status || 'idle', agent.currentActivityKind)}"></span>
                        <span id="agent-runtime-status-label">${BossModUtils.getStatusLabel(agent.status || 'idle', agent.currentActivityKind)}</span>
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
            </div>` : ''}

            <!-- Actions -->
            <div class="flex gap-2 pt-2">
                <button type="submit"
                        class="flex-1 px-4 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    ${agent ? 'Save Changes' : 'Create Agent'}
                </button>
                ${agent ? `
                <button type="button" id="btn-delete-agent"
                        class="px-4 py-2 border border-red-300 text-red-600 rounded-lg
                               hover:bg-red-50 transition-colors text-sm font-medium">
                    Delete
                </button>` : ''}
            </div>
        </form>
        `;

        // Settings navigation links
        const gotoSettings = () => {
            if (typeof SettingsView !== 'undefined') {
                SettingsView.open();
                BossModApp.updateNavForSettings(true);
            }
        };
        const gotoConn = container.querySelector('#btn-goto-connections');
        if (gotoConn) gotoConn.addEventListener('click', gotoSettings);
        const gotoPers = container.querySelector('#btn-goto-personalities');
        if (gotoPers) gotoPers.addEventListener('click', gotoSettings);

        // "Set All" connection convenience dropdown
        const setAllSelect = container.querySelector('select[name="model_all"]');
        if (setAllSelect) {
            setAllSelect.addEventListener('change', () => {
                if (!setAllSelect.value) return;
                MODEL_TYPES.forEach(t => {
                    const sel = container.querySelector(`select[name="${t.key}"]`);
                    if (sel) sel.value = setAllSelect.value;
                });
            });
        }

        // AI History collapsible accordion
        const historyToggle = container.querySelector('#ai-history-toggle');
        const historyContent = container.querySelector('#ai-history-content');
        const historyChevron = container.querySelector('#ai-history-chevron');
        if (historyToggle && historyContent && historyChevron) {
            historyToggle.addEventListener('click', () => {
                historyContent.classList.toggle('hidden');
                historyChevron.style.transform = historyContent.classList.contains('hidden') ? '' : 'rotate(90deg)';
            });
            if (window.lucide) lucide.createIcons({ nodes: [historyToggle] });
        }
    }

    // ─── Build submit data from form ───

    async function buildSubmitData(form, connections) {
        const formData = new FormData(form);

        const deskValue = formData.get('desk');
        let desk_x = null, desk_y = null;
        if (deskValue) {
            [desk_x, desk_y] = deskValue.split(',').map(Number);
        }

        const agentData = {
            name: formData.get('name'),
            role: formData.get('role') || null,
            color: formData.get('agent-color') || '#3b82f6',
            desk_x,
            desk_y,
        };

        // Resolve personality → copy prompt_template
        const personalityId = formData.get('personality_id');
        if (personalityId) {
            try {
                const res = await fetch(`/api/personalities/${personalityId}`);
                if (res.ok) {
                    const personality = await res.json();
                    agentData.prompt_template = personality.prompt_template;
                }
            } catch { /* use null */ }
        }

        // Resolve connection IDs → copy api_base_url, api_key, model into agent fields
        const connMap = {};
        for (const c of connections) connMap[c.id] = c;

        for (const t of MODEL_TYPES) {
            const connId = formData.get(t.key);
            if (connId && connMap[connId]) {
                const conn = connMap[connId];
                const runtimeModel = (conn.model || '').trim();
                if (!runtimeModel) {
                    throw new Error(`Connection "${conn.name}" is missing an explicit model identifier`);
                }
                agentData[t.key] = runtimeModel;
                if (!agentData.api_base_url) {
                    agentData.api_base_url = conn.api_base_url;
                    agentData.api_key = conn.api_key || null;
                    agentData.extra_body = conn.extra_body || null;
                }
            } else {
                agentData[t.key] = null;
            }
        }

        const earliestTsRaw = String(formData.get('prompt_history_earliest_ts') || '').trim();
        const promptHistoryPolicy = {
            last_n_histories: Number(formData.get('prompt_history_last_n') || DEFAULT_PROMPT_HISTORY_POLICY.last_n_histories),
            max_allowed_history_tokens: Number(formData.get('prompt_history_max_tokens') || DEFAULT_PROMPT_HISTORY_POLICY.max_allowed_history_tokens),
            earliest_ts_allowed: earliestTsRaw ? new Date(earliestTsRaw).toISOString() : null,
            include_notifications: formData.get('prompt_history_include_notifications') === 'on',
        };

        return { agentData, promptHistoryPolicy };
    }

    // ─── Refresh canvas agents from API ───

    async function refreshCanvas() {
        try {
            const world = await (await fetch('/api/world')).json();
            const agents = world.map(BossModUtils.normalizeAgent);
            if (typeof OfficeCanvas !== 'undefined') {
                OfficeCanvas.updateAgents(agents);
            }
        } catch (err) {
            console.error('[AgentPanel] Failed to refresh canvas:', err);
        }
    }

    // ─── Render inline (for left panel Edit sub-view) ───

    async function renderInline(container, agent, onSave, onDelete) {
        isCreating = !agent;
        currentAgentId = agent?.id || null;

        await buildFormHTML(container, agent);

        const form = container.querySelector('#agent-form');
        const deleteBtn = container.querySelector('#btn-delete-agent');
        const clearChatBtn = container.querySelector('#btn-clear-chat-history');
        const resetRuntimeBtn = container.querySelector('#btn-reset-runtime');

        // Fetch connections for submit resolution
        let connections = [];
        try {
            const res = await fetch('/api/connections');
            connections = await res.json();
        } catch { /* empty */ }

        // Add feedback element after the form actions
        const feedbackEl = document.createElement('div');
        feedbackEl.id = 'agent-save-feedback';
        feedbackEl.className = 'hidden mt-3 p-3 rounded-lg text-sm';
        form.appendChild(feedbackEl);

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
            feedbackEl.textContent = 'Saving...';

                const { agentData, promptHistoryPolicy } = await buildSubmitData(form, connections);
                try {
                    let savedAgent = null;
                    if (isCreating) {
                        savedAgent = await apiCreateAgent(agentData);
                    } else {
                        savedAgent = await apiUpdateAgent(currentAgentId, agentData);
                    }

                    try {
                        await apiUpdatePromptHistoryPolicy(savedAgent.id, promptHistoryPolicy);
                    } catch (policyErr) {
                        console.error('[AgentPanel] Prompt history policy save failed:', policyErr);
                        await refreshCanvas();
                        feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-amber-50 border border-amber-200 text-amber-800';
                        feedbackEl.textContent = 'Agent saved, but AI history settings failed to save.';
                        if (onSave) onSave(savedAgent);
                        return;
                    }

                    await refreshCanvas();
                    feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';
                    feedbackEl.textContent = 'Saved successfully';
                setTimeout(() => { feedbackEl.className = 'hidden'; }, 3000);
                if (onSave) onSave(savedAgent);
            } catch (err) {
                console.error('[AgentPanel] Save failed:', err);
                feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                feedbackEl.textContent = err?.message || 'Save failed — check console for details';
            }
        });

        if (deleteBtn) {
            deleteBtn.addEventListener('click', async () => {
                if (!currentAgentId) return;
                if (!confirm('Delete this agent? This cannot be undone.')) return;
                try {
                    await apiDeleteAgent(currentAgentId);
                    await refreshCanvas();
                    if (onDelete) onDelete();
                } catch (err) {
                    console.error('[AgentPanel] Delete failed:', err);
                }
            });
        }

        if (clearChatBtn) {
            clearChatBtn.addEventListener('click', async () => {
                if (!currentAgentId) return;
                if (!confirm("This permanently deletes this agent's direct chat history with the human operator. Completed work, artifacts, and diagnostics are preserved. Continue?")) return;
                feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
                feedbackEl.textContent = 'Clearing chat history...';
                try {
                    const result = await apiClearChatHistory(currentAgentId);
                    feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';
                    feedbackEl.textContent = `Cleared ${result.deleted_messages} chat message${result.deleted_messages === 1 ? '' : 's'}.`;
                    if (onSave) onSave();
                } catch (err) {
                    console.error('[AgentPanel] Clear chat failed:', err);
                    feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    feedbackEl.textContent = 'Clear chat failed — check console for details';
                }
            });
        }

        if (resetRuntimeBtn) {
            resetRuntimeBtn.addEventListener('click', async () => {
                if (!currentAgentId) return;
                if (!confirm('This forcibly resets the agent runtime, clears queued triggers, and may block the active task. Completed work history is preserved. Continue?')) return;
                feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
                feedbackEl.textContent = 'Resetting runtime...';
                try {
                    const result = await apiResetRuntime(currentAgentId);
                    await refreshCanvas();
                    feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';
                    feedbackEl.textContent = `Runtime reset. Cleared ${result.deleted_triggers} open trigger${result.deleted_triggers === 1 ? '' : 's'}.`;
                    if (onSave) onSave();
                } catch (err) {
                    console.error('[AgentPanel] Reset runtime failed:', err);
                    feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    feedbackEl.textContent = 'Runtime reset failed — check console for details';
                }
            });
        }
    }

    // ─── Public API ───

    return {
        renderInline,
        refreshCanvas,
    };
})();
