/**
 * BossMod AI — Agent panel overlay.
 *
 * Handles the slide-in panel for viewing, creating, editing,
 * and deleting agents. Communicates with /api/agents endpoints.
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

    // ─── API calls ───

    async function fetchAgents() {
        const res = await fetch('/api/agents');
        return res.json();
    }

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

    // ─── Render panel body ───

    function renderForm(agent = null) {
        isCreating = !agent;
        currentAgentId = agent?.id || null;

        const title = document.getElementById('agent-panel-title');
        title.textContent = agent ? agent.name : 'New Agent';

        const body = document.querySelector('#agent-panel .flex-1.overflow-y-auto');

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

        body.innerHTML = `
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

            <!-- Prompt Template -->
            <div>
                <label class="block text-sm font-medium mb-1">Prompt Template</label>
                <textarea name="prompt_template" rows="4"
                          placeholder="System prompt for this agent..."
                          class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                 bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                 focus:border-bm-accent resize-y">${BossModUtils.escapeHtml(agent?.prompt_template || '')}</textarea>
            </div>

            <!-- Model Matrix -->
            <div>
                <label class="block text-sm font-medium mb-2">Model Matrix</label>
                <div class="space-y-2">
                    ${renderModelField('Social (cheap)', 'model_social', agent?.model_social)}
                    ${renderModelField('Work (routine)', 'model_work', agent?.model_work)}
                    ${renderModelField('Reasoning (deep)', 'model_reasoning', agent?.model_reasoning)}
                    ${renderModelField('Extraction', 'model_extraction', agent?.model_extraction)}
                    ${renderModelField('Self-queue', 'model_self_queue', agent?.model_self_queue)}
                </div>
            </div>

            <!-- Status (read-only for existing agents) -->
            ${agent ? `
            <div class="pt-2 border-t border-bm-border">
                <div class="flex items-center justify-between text-sm">
                    <span class="text-bm-muted">Status</span>
                    <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
                                 ${getStatusClasses(agent.status || 'idle')}">
                        <span class="w-1.5 h-1.5 rounded-full ${getStatusDot(agent.status || 'idle')}"></span>
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

        // Bind events
        document.getElementById('agent-form').addEventListener('submit', handleSubmit);
        const deleteBtn = document.getElementById('btn-delete-agent');
        if (deleteBtn) deleteBtn.addEventListener('click', handleDelete);
    }

    function renderModelField(label, name, value) {
        return `
        <div class="flex items-center gap-2">
            <span class="text-xs text-bm-muted w-24 shrink-0">${label}</span>
            <input type="text" name="${name}"
                   value="${BossModUtils.escapeHtml(value || '')}"
                   placeholder="e.g. gpt-4o, claude-sonnet, ollama/llama3"
                   class="flex-1 px-2 py-1 text-xs border border-bm-border rounded
                          bg-bm-bg focus:outline-none focus:ring-1 focus:ring-bm-accent/30">
        </div>`;
    }

    function getStatusClasses(status) {
        return BossModUtils.getStatusClasses(status);
    }

    function getStatusDot(status) {
        return BossModUtils.getStatusDot(status);
    }

    // ─── Event handlers ───

    async function handleSubmit(e) {
        e.preventDefault();
        const form = e.target;
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

        try {
            if (isCreating) {
                const agent = await apiCreateAgent(data);
                currentAgentId = agent.id;
                isCreating = false;
            } else {
                // Include model matrix and prompt for updates
                data.prompt_template = formData.get('prompt_template') || null;
                data.model_social = formData.get('model_social') || null;
                data.model_work = formData.get('model_work') || null;
                data.model_reasoning = formData.get('model_reasoning') || null;
                data.model_extraction = formData.get('model_extraction') || null;
                data.model_self_queue = formData.get('model_self_queue') || null;
                await apiUpdateAgent(currentAgentId, data);
            }
            await refreshCanvas();
            BossModApp.closeAgentPanel();
        } catch (err) {
            console.error('[AgentPanel] Save failed:', err);
        }
    }

    async function handleDelete() {
        if (!currentAgentId) return;
        if (!confirm('Delete this agent? This cannot be undone.')) return;

        try {
            await apiDeleteAgent(currentAgentId);
            currentAgentId = null;
            await refreshCanvas();
            BossModApp.closeAgentPanel();
        } catch (err) {
            console.error('[AgentPanel] Delete failed:', err);
        }
    }

    // ─── Refresh canvas agents from API ───

    async function refreshCanvas() {
        try {
            const world = await (await fetch('/api/world')).json();
            const agents = world.map(BossModUtils.normalizeAgent);
            OfficeCanvas.updateAgents(agents);
        } catch (err) {
            console.error('[AgentPanel] Failed to refresh canvas:', err);
        }
    }

    // ─── Public API ───

    function openForAgent(agentData) {
        renderForm(agentData);
    }

    function openForCreate() {
        renderForm(null);
    }

    async function openForAgentId(agentId) {
        const agent = await fetchAgent(agentId);
        if (agent) renderForm(agent);
    }

    return {
        openForAgent,
        openForCreate,
        openForAgentId,
        refreshCanvas,
    };
})();
