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

    // ─── Build form HTML into a container ───

    async function buildFormHTML(container, agent = null) {

        // Fetch connections and personalities for dropdowns
        let connections = [];
        let personalities = [];
        try {
            const [connRes, persRes] = await Promise.all([
                fetch('/api/connections'),
                fetch('/api/personalities'),
            ]);
            connections = await connRes.json();
            personalities = await persRes.json();
        } catch (err) {
            console.error('[AgentPanel] Failed to load connections/personalities:', err);
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
                           ${MODEL_TYPES.map(t => `
                               <div class="flex items-center gap-2">
                                   <span class="text-xs text-bm-muted w-28 shrink-0">${t.label}</span>
                                   ${connectionSelect(t.key, agent?.[t.key])}
                               </div>
                           `).join('')}
                       </div>`
                }
            </div>

            <!-- Status (read-only for existing agents) -->
            ${agent ? `
            <div class="pt-2 border-t border-bm-border">
                <div class="flex items-center justify-between text-sm">
                    <span class="text-bm-muted">Status</span>
                    <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
                                 ${BossModUtils.getStatusClasses(agent.status || 'idle')}">
                        <span class="w-1.5 h-1.5 rounded-full ${BossModUtils.getStatusDot(agent.status || 'idle')}"></span>
                        ${agent.status || 'idle'}
                    </span>
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
    }

    // ─── Build submit data from form ───

    async function buildSubmitData(form, connections) {
        const formData = new FormData(form);

        const deskValue = formData.get('desk');
        let desk_x = null, desk_y = null;
        if (deskValue) {
            [desk_x, desk_y] = deskValue.split(',').map(Number);
        }

        const data = {
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
                    data.prompt_template = personality.prompt_template;
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
                data[t.key] = conn.model || conn.name;
                if (!data.api_base_url) {
                    data.api_base_url = conn.api_base_url;
                    data.api_key = conn.api_key || null;
                    data.extra_body = conn.extra_body || null;
                }
            } else {
                data[t.key] = null;
            }
        }

        return data;
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

            const data = await buildSubmitData(form, connections);
            try {
                if (isCreating) {
                    await apiCreateAgent(data);
                } else {
                    await apiUpdateAgent(currentAgentId, data);
                }
                await refreshCanvas();
                feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';
                feedbackEl.textContent = 'Saved successfully';
                setTimeout(() => { feedbackEl.className = 'hidden'; }, 3000);
                if (onSave) onSave();
            } catch (err) {
                console.error('[AgentPanel] Save failed:', err);
                feedbackEl.className = 'mt-3 p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                feedbackEl.textContent = 'Save failed — check console for details';
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
    }

    // ─── Public API ───

    return {
        renderInline,
        refreshCanvas,
    };
})();
