/**
 * BossMod AI — Full-screen settings view.
 *
 * Replaces the main office view with a 2-column settings layout:
 * left nav + right content area. Sections: AI Connections,
 * AI Personalities, System Settings, System Prompt Template.
 */

const SettingsView = (() => {
    let activeSection = 'connections';
    let isOpen = false;

    const NAV_ITEMS = [
        { id: 'connections',   label: 'AI Connections',  icon: 'plug' },
        { id: 'personalities', label: 'AI Personalities', icon: 'brain' },
        { id: 'system',       label: 'System Settings',  icon: 'sliders' },
    ];

    const ADVANCED_ITEMS = [
        { id: 'advanced-system', label: 'Advanced System Settings', icon: 'shield' },
        { id: 'prompt-template', label: 'System Prompt Template', icon: 'file-code' },
        { id: 'action-contract', label: 'Action Contract', icon: 'braces' },
    ];

    // ─── Open / Close ───

    function open() {
        const mainLayout = document.getElementById('main-layout');
        const settingsLayout = document.getElementById('settings-layout');
        const mobileSheet = document.getElementById('mobile-sheet');

        mainLayout.classList.add('hidden');
        settingsLayout.classList.remove('hidden');
        if (mobileSheet) mobileSheet.classList.add('hidden');
        isOpen = true;

        renderNav();
        switchSection(activeSection);
    }

    function close() {
        const mainLayout = document.getElementById('main-layout');
        const settingsLayout = document.getElementById('settings-layout');
        const mobileSheet = document.getElementById('mobile-sheet');

        settingsLayout.classList.add('hidden');
        mainLayout.classList.remove('hidden');
        if (mobileSheet) mobileSheet.classList.remove('hidden');
        isOpen = false;
    }

    // ─── Nav rendering ───

    function renderNav() {
        const nav = document.getElementById('settings-nav');

        let html = '';
        for (const item of NAV_ITEMS) {
            html += navButton(item);
        }
        html += '<div class="mt-4 mb-2 px-3 text-xs font-semibold text-bm-muted uppercase tracking-wider">Advanced</div>';
        for (const item of ADVANCED_ITEMS) {
            html += navButton(item);
        }
        nav.innerHTML = html;

        nav.querySelectorAll('[data-section]').forEach(btn => {
            btn.addEventListener('click', () => switchSection(btn.dataset.section));
        });

        if (window.lucide) lucide.createIcons({ nodes: [nav] });
    }

    function navButton(item) {
        const active = activeSection === item.id;
        return `<button data-section="${item.id}"
                    class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                           transition-colors text-left
                           ${active ? 'bg-bm-accent/10 text-bm-accent' : 'text-bm-text hover:bg-slate-100'}">
                <i data-lucide="${item.icon}" class="w-4 h-4 shrink-0"></i>
                ${item.label}
            </button>`;
    }

    // ─── Section switching ───

    function switchSection(sectionId) {
        activeSection = sectionId;
        renderNav();

        const content = document.getElementById('settings-content');

        switch (sectionId) {
            case 'connections':
                ConnectionsSection.render(content);
                break;
            case 'personalities':
                PersonalitiesSection.render(content);
                break;
            case 'system':
                SystemSection.render(content);
                break;
            case 'advanced-system':
                AdvancedSystemSection.render(content);
                break;
            case 'prompt-template':
                PromptTemplateSection.render(content);
                break;
            case 'action-contract':
                ActionContractSection.render(content);
                break;
        }
    }

    return { open, close, isOpen: () => isOpen };
})();


// ═══════════════════════════════════════════════════════════════
// AI Connections Section
// ═══════════════════════════════════════════════════════════════

const ConnectionsSection = (() => {
    let container = null;

    async function render(el) {
        container = el;
        await renderList();
    }

    async function renderList() {
        let connections = [];
        try {
            const res = await fetch('/api/connections');
            connections = await res.json();
        } catch (err) {
            container.innerHTML = '<p class="text-red-500 text-sm">Failed to load connections.</p>';
            return;
        }

        let html = `
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-lg font-semibold">AI Connections</h2>
                    <p class="text-sm text-bm-muted mt-0.5">Manage your LLM provider API connections.</p>
                </div>
                <button id="btn-add-connection"
                        class="flex items-center gap-2 px-3 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    <i data-lucide="plus" class="w-4 h-4"></i> Add Connection
                </button>
            </div>`;

        if (connections.length === 0) {
            html += `<div class="text-center py-12 text-bm-muted">
                <i data-lucide="plug" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                <p class="text-sm">No connections yet. Add your first AI provider.</p>
            </div>`;
        } else {
            html += '<div class="space-y-3">';
            for (const conn of connections) {
                const maskedKey = conn.api_key
                    ? conn.api_key.slice(0, 4) + '••••' + conn.api_key.slice(-2)
                    : 'No API key';
                html += `
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-start justify-between">
                        <div class="min-w-0">
                            <div class="flex items-center gap-2">
                                <h3 class="font-medium">${BossModUtils.escapeHtml(conn.name)}</h3>
                                ${conn.model ? `<span class="text-xs px-2 py-0.5 bg-slate-100 rounded-full text-bm-muted">${BossModUtils.escapeHtml(conn.model)}</span>` : ''}
                            </div>
                            <p class="text-sm text-bm-muted mt-1">${BossModUtils.escapeHtml(conn.api_base_url)}</p>
                            <p class="text-xs text-bm-muted mt-0.5 font-mono">${BossModUtils.escapeHtml(maskedKey)}</p>
                        </div>
                        <div class="flex items-center gap-1 shrink-0 ml-4">
                            <button data-edit-conn="${conn.id}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                                    title="Edit">
                                <i data-lucide="pencil" class="w-4 h-4 text-bm-muted"></i>
                            </button>
                            <button data-delete-conn="${conn.id}"
                                    class="p-1.5 rounded-lg hover:bg-red-50 transition-colors"
                                    title="Delete">
                                <i data-lucide="trash-2" class="w-4 h-4 text-red-400"></i>
                            </button>
                        </div>
                    </div>
                </div>`;
            }
            html += '</div>';
        }

        container.innerHTML = html;
        if (window.lucide) lucide.createIcons({ nodes: [container] });

        // Bind events
        const addBtn = document.getElementById('btn-add-connection');
        if (addBtn) addBtn.addEventListener('click', () => renderForm(null));

        container.querySelectorAll('[data-edit-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const res = await fetch(`/api/connections/${btn.dataset.editConn}`);
                if (res.ok) renderForm(await res.json());
            });
        });

        container.querySelectorAll('[data-delete-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this connection?')) return;
                await fetch(`/api/connections/${btn.dataset.deleteConn}`, { method: 'DELETE' });
                await renderList();
            });
        });
    }

    function renderForm(conn) {
        const isEdit = !!conn;
        container.innerHTML = `
            <div class="max-w-lg">
                <h2 class="text-lg font-semibold mb-1">${isEdit ? 'Edit Connection' : 'New Connection'}</h2>
                <p class="text-sm text-bm-muted mb-6">${isEdit ? 'Update this connection.' : 'Add a new LLM provider connection.'}</p>
                <form id="connection-form" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Connection Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">This is what you'll see when selecting a connection for an agent.</p>
                        <input type="text" name="name" required
                               value="${BossModUtils.escapeHtml(conn?.name || '')}"
                               placeholder="e.g. OpenAI Production"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">API Base URL</label>
                        <p class="text-xs text-bm-muted mb-1.5">The OpenAI-compatible endpoint URL.</p>
                        <input type="url" name="api_base_url" required
                               value="${BossModUtils.escapeHtml(conn?.api_base_url || '')}"
                               placeholder="https://api.openai.com/v1"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">API Key</label>
                        <p class="text-xs text-bm-muted mb-1.5">Optional. Leave blank for local models like Ollama.</p>
                        <input type="password" name="api_key"
                               value="${BossModUtils.escapeHtml(conn?.api_key || '')}"
                               placeholder="sk-..."
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Model Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">Optional. Helps you remember which model this connection uses.</p>
                        <input type="text" name="model"
                               value="${BossModUtils.escapeHtml(conn?.model || '')}"
                               placeholder="e.g. gpt-4o, claude-sonnet-4-5-20250514, llama3"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Extra Body Params</label>
                        <p class="text-xs text-bm-muted mb-1.5">Optional JSON merged into every request body. For provider-specific fields.</p>
                        <textarea name="extra_body" rows="3"
                                  placeholder='e.g. {"stream": false, "thinking": {"type": "disabled"}}'
                                  class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                         bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                         focus:border-bm-accent font-mono">${BossModUtils.escapeHtml(conn?.extra_body || '')}</textarea>
                    </div>
                    <div id="test-conn-result" class="hidden p-3 rounded-lg text-sm"></div>
                    <div class="flex gap-2 pt-2">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            ${isEdit ? 'Save Changes' : 'Create Connection'}
                        </button>
                        <button type="button" id="btn-test-conn"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Test Connection
                        </button>
                        <button type="button" id="btn-cancel-conn"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        document.getElementById('btn-cancel-conn').addEventListener('click', renderList);

        document.getElementById('btn-test-conn').addEventListener('click', async () => {
            const form = document.getElementById('connection-form');
            const fd = new FormData(form);
            const resultEl = document.getElementById('test-conn-result');

            resultEl.className = 'p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
            resultEl.textContent = 'Testing connection...';
            resultEl.classList.remove('hidden');

            try {
                const resp = await fetch('/api/connections/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_base_url: fd.get('api_base_url'),
                        api_key: fd.get('api_key') || null,
                        model: fd.get('model') || null,
                    }),
                });
                const result = await resp.json();

                if (!result.ok) {
                    resultEl.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    resultEl.textContent = result.error;
                } else {
                    const isWarning = !!result.warning;
                    resultEl.className = isWarning
                        ? 'p-3 rounded-lg text-sm bg-amber-50 border border-amber-200 text-amber-700'
                        : 'p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';

                    const msg = isWarning
                        ? result.warning
                        : `Connected — ${result.models_count} model${result.models_count !== 1 ? 's' : ''} available`;

                    let html = `<p class="font-medium">${BossModUtils.escapeHtml(msg)}</p>`;
                    if (result.models && result.models.length > 0) {
                        html += `<p class="mt-2 mb-1 text-xs font-semibold opacity-70 uppercase tracking-wide">Available models</p>`;
                        html += `<div class="flex flex-wrap gap-1.5">`;
                        for (const m of result.models) {
                            html += `<span class="px-2 py-0.5 rounded text-xs font-mono ${isWarning ? 'bg-amber-100' : 'bg-emerald-100'}">${BossModUtils.escapeHtml(m)}</span>`;
                        }
                        if (result.models_count > result.models.length) {
                            html += `<span class="px-2 py-0.5 text-xs opacity-60">+${result.models_count - result.models.length} more</span>`;
                        }
                        html += `</div>`;
                    }
                    resultEl.innerHTML = html;
                }
            } catch {
                resultEl.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                resultEl.textContent = 'Request failed — check your network';
            }
        });

        document.getElementById('connection-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const data = {
                name: fd.get('name'),
                api_base_url: fd.get('api_base_url'),
                api_key: fd.get('api_key') || null,
                model: fd.get('model') || null,
                extra_body: fd.get('extra_body')?.trim() || null,
            };
            try {
                if (isEdit) {
                    await fetch(`/api/connections/${conn.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await fetch('/api/connections', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                await renderList();
            } catch (err) {
                console.error('[Connections] Save failed:', err);
            }
        });
    }

    return { render };
})();


// ═══════════════════════════════════════════════════════════════
// AI Personalities Section
// ═══════════════════════════════════════════════════════════════

const PersonalitiesSection = (() => {
    let container = null;

    async function render(el) {
        container = el;
        await renderList();
    }

    async function renderList() {
        let personalities = [];
        try {
            const res = await fetch('/api/personalities');
            personalities = await res.json();
        } catch (err) {
            container.innerHTML = '<p class="text-red-500 text-sm">Failed to load personalities.</p>';
            return;
        }

        let html = `
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-lg font-semibold">AI Personalities</h2>
                    <p class="text-sm text-bm-muted mt-0.5">Define reusable prompt templates for agent roles.</p>
                </div>
                <button id="btn-add-personality"
                        class="flex items-center gap-2 px-3 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    <i data-lucide="plus" class="w-4 h-4"></i> Add Personality
                </button>
            </div>`;

        if (personalities.length === 0) {
            html += `<div class="text-center py-12 text-bm-muted">
                <i data-lucide="brain" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                <p class="text-sm">No personalities yet. Create your first agent role template.</p>
            </div>`;
        } else {
            html += '<div class="space-y-3">';
            for (const p of personalities) {
                const preview = p.prompt_template.length > 120
                    ? p.prompt_template.slice(0, 120) + '...'
                    : p.prompt_template;
                html += `
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-start justify-between">
                        <div class="min-w-0 flex-1">
                            <h3 class="font-medium">${BossModUtils.escapeHtml(p.name)}</h3>
                            <p class="text-sm text-bm-muted mt-1 line-clamp-2">${BossModUtils.escapeHtml(preview)}</p>
                        </div>
                        <div class="flex items-center gap-1 shrink-0 ml-4">
                            <button data-edit-pers="${p.id}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                                    title="Edit">
                                <i data-lucide="pencil" class="w-4 h-4 text-bm-muted"></i>
                            </button>
                            <button data-delete-pers="${p.id}"
                                    class="p-1.5 rounded-lg hover:bg-red-50 transition-colors"
                                    title="Delete">
                                <i data-lucide="trash-2" class="w-4 h-4 text-red-400"></i>
                            </button>
                        </div>
                    </div>
                </div>`;
            }
            html += '</div>';
        }

        container.innerHTML = html;
        if (window.lucide) lucide.createIcons({ nodes: [container] });

        const addBtn = document.getElementById('btn-add-personality');
        if (addBtn) addBtn.addEventListener('click', () => renderForm(null));

        container.querySelectorAll('[data-edit-pers]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const res = await fetch(`/api/personalities/${btn.dataset.editPers}`);
                if (res.ok) renderForm(await res.json());
            });
        });

        container.querySelectorAll('[data-delete-pers]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this personality?')) return;
                await fetch(`/api/personalities/${btn.dataset.deletePers}`, { method: 'DELETE' });
                await renderList();
            });
        });
    }

    function renderForm(p) {
        const isEdit = !!p;
        container.innerHTML = `
            <div class="max-w-2xl">
                <h2 class="text-lg font-semibold mb-1">${isEdit ? 'Edit Personality' : 'New Personality'}</h2>
                <p class="text-sm text-bm-muted mb-6">${isEdit ? 'Update this personality template.' : 'Create a reusable prompt template for agent roles.'}</p>
                <form id="personality-form" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">A short label used in dropdowns and the settings list so people can recognize this personality at a glance.</p>
                        <input type="text" name="name" required
                               value="${BossModUtils.escapeHtml(p?.name || '')}"
                               placeholder="e.g. Product Manager, Code Reviewer"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Prompt Template</label>
                        <p class="text-xs text-bm-muted mb-1.5">The system prompt that defines this personality's behavior.</p>
                        <textarea name="prompt_template" required rows="12"
                                  placeholder="You are a senior product manager focused on clarity, prioritization, and stakeholder communication..."
                                  class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                         bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                         focus:border-bm-accent resize-y font-mono">${BossModUtils.escapeHtml(p?.prompt_template || '')}</textarea>
                    </div>
                    <div class="flex gap-2 pt-2">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            ${isEdit ? 'Save Changes' : 'Create Personality'}
                        </button>
                        <button type="button" id="btn-cancel-pers"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        document.getElementById('btn-cancel-pers').addEventListener('click', renderList);
        document.getElementById('personality-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const data = {
                name: fd.get('name'),
                prompt_template: fd.get('prompt_template'),
            };
            try {
                if (isEdit) {
                    await fetch(`/api/personalities/${p.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await fetch('/api/personalities', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                await renderList();
            } catch (err) {
                console.error('[Personalities] Save failed:', err);
            }
        });
    }

    return { render };
})();


// ═══════════════════════════════════════════════════════════════
// System Settings Section (existing key-value settings)
// ═══════════════════════════════════════════════════════════════

const SystemSection = (() => {
    let activeCategory = 'simulation';

    const CATEGORIES = [
        { key: 'simulation', label: 'Simulation' },
        { key: 'social',     label: 'Social Triggers' },
        { key: 'context',    label: 'Context Window' },
    ];

    const CATEGORY_DESCRIPTIONS = {
        simulation: 'Movement speed, simulation cadence, and recovery behavior for the office runtime.',
        social: 'Controls when idle agents may start optional social behavior based on time and proximity.',
        context: 'Controls how much recent conversation and work history is included in each agent turn.',
    };

    const SETTING_META = {
        tick_interval: {
            order: 20,
            label: 'Tick Interval (seconds)',
            description: 'How often the world simulation updates. Lower values make movement and presence updates feel smoother, but increase backend and UI update frequency.',
        },
        movement_tiles_per_second: {
            order: 10,
            label: 'Movement Speed (tiles/sec)',
            description: 'How fast agents physically travel across the office. This affects arrival timing and should be tuned separately from tick interval.',
        },
        sim_error_threshold: {
            order: 30,
            label: 'Simulation Error Threshold',
            description: 'How many consecutive simulation loop failures are allowed before the engine pauses and enters backoff.',
        },
        sim_error_backoff_seconds: {
            order: 40,
            label: 'Simulation Error Backoff (seconds)',
            description: 'How long the simulation waits after repeated failures before it tries ticking again.',
        },
        watchdog_check_interval_seconds: {
            order: 50,
            label: 'Watchdog Check Interval (seconds)',
            description: 'How often the watchdog scans active tasks for silence or stalls.',
        },
        watchdog_soft_ping_minutes: {
            order: 60,
            label: 'Watchdog Soft Ping (minutes)',
            description: 'How long an active task can stay quiet before the system asks the agent for a status update.',
        },
        watchdog_escalation_minutes: {
            order: 70,
            label: 'Watchdog Escalation Delay (minutes)',
            description: 'How much additional quiet time is allowed after a soft ping before the task is marked stalled.',
        },
        social_idle_threshold_minutes: {
            order: 10,
            label: 'Idle Threshold (minutes)',
            description: 'How long an agent must stay idle before the system considers starting optional social behavior.',
        },
        social_cooldown_minutes: {
            order: 20,
            label: 'Social Cooldown (minutes)',
            description: 'Minimum time between automatic social prompts for the same agent.',
        },
        social_proximity_tiles: {
            order: 30,
            label: 'Proximity Radius (tiles)',
            description: 'How close agents must be on the map to count as nearby for social triggers.',
        },
        context_window_messages: {
            order: 10,
            label: 'Message Window Size',
            description: 'How many recent direct conversation messages are included in the agent prompt for a turn.',
        },
        context_recent_work_artifacts: {
            order: 20,
            label: 'Recent Work Artifacts',
            description: 'How many recent work outputs or artifacts are included as reference material in the prompt.',
        },
        context_recent_completed_tasks: {
            order: 30,
            label: 'Recent Completed Tasks',
            description: 'How many recently completed task summaries are included as reference material in the prompt.',
        },
    };

    async function render(el) {
        let settings = [];
        try {
            const res = await fetch('/api/settings');
            settings = await res.json();
        } catch (err) {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load settings.</p>';
            return;
        }

        // Group by category, only show non-advanced categories
        const groups = {};
        const shownCats = new Set(CATEGORIES.map(c => c.key));
        for (const s of settings) {
            if (!shownCats.has(s.category)) continue;
            if (s.key === 'steps_per_tick') continue;
            if (!groups[s.category]) groups[s.category] = [];
            groups[s.category].push(s);
        }

        for (const key of Object.keys(groups)) {
            groups[key].sort((a, b) => {
                const aOrder = SETTING_META[a.key]?.order ?? 999;
                const bOrder = SETTING_META[b.key]?.order ?? 999;
                if (aOrder !== bOrder) return aOrder - bOrder;
                return a.key.localeCompare(b.key);
            });
        }

        const availableCategories = CATEGORIES.filter(cat => (groups[cat.key] || []).length > 0);
        if (!availableCategories.some(cat => cat.key === activeCategory)) {
            activeCategory = availableCategories[0]?.key || 'simulation';
        }

        const activeItems = groups[activeCategory] || [];
        const activeCategoryMeta = CATEGORIES.find(cat => cat.key === activeCategory);

        let html = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">System Settings</h2>
                <p class="text-sm text-bm-muted mt-0.5">Configure simulation behavior and social triggers.</p>
            </div>
            <div class="max-w-7xl">
                <div class="mb-5 flex flex-wrap gap-2">`;

        for (const cat of availableCategories) {
            const active = cat.key === activeCategory;
            html += `
                    <button
                        type="button"
                        data-system-category="${BossModUtils.escapeHtml(cat.key)}"
                        class="system-category-tab px-4 py-2 rounded-lg text-sm font-medium border transition-colors
                               ${active ? 'bg-bm-accent text-white border-bm-accent shadow-sm' : 'bg-white text-bm-text border-bm-border hover:bg-slate-50'}">
                        ${BossModUtils.escapeHtml(cat.label)}
                    </button>`;
        }

        html += `
                </div>
                <section class="border border-bm-border rounded-xl bg-white p-5 shadow-sm">
                    <div class="mb-4">
                        <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide">${BossModUtils.escapeHtml(activeCategoryMeta?.label || 'Settings')}</h3>
                        <p class="text-xs text-bm-muted mt-1">${BossModUtils.escapeHtml(CATEGORY_DESCRIPTIONS[activeCategory] || '')}</p>
                    </div>
                    <div class="grid grid-cols-1 2xl:grid-cols-2 gap-4">`;

        for (const s of activeItems) {
            const meta = SETTING_META[s.key] || {};
            const label = meta.label || s.key;
            const description = meta.description || 'System setting.';
            html += `
                    <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                        <label class="block text-sm font-medium mb-1">${BossModUtils.escapeHtml(label)}</label>
                        <p class="text-xs text-bm-muted mb-1.5">${BossModUtils.escapeHtml(description)}</p>
                        <input type="text"
                               data-setting-key="${BossModUtils.escapeHtml(s.key)}"
                               data-setting-category="${BossModUtils.escapeHtml(s.category)}"
                               value="${BossModUtils.escapeHtml(s.value)}"
                               class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-white focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>`;
        }

        html += `
                    </div>
                </section>
            </div>`;
        el.innerHTML = html;

        el.querySelectorAll('[data-system-category]').forEach(btn => {
            btn.addEventListener('click', () => {
                activeCategory = btn.dataset.systemCategory;
                render(el);
            });
        });

        el.querySelectorAll('.setting-input').forEach(input => {
            input.addEventListener('change', async (e) => {
                const key = e.target.dataset.settingKey;
                const category = e.target.dataset.settingCategory;
                const value = e.target.value;
                try {
                    await fetch(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`, {
                        method: 'PUT',
                    });
                    e.target.classList.add('border-emerald-400');
                    setTimeout(() => e.target.classList.remove('border-emerald-400'), 1000);
                } catch {
                    e.target.classList.add('border-red-400');
                    setTimeout(() => e.target.classList.remove('border-red-400'), 1000);
                }
            });
        });
    }

    return { render };
})();


// ═══════════════════════════════════════════════════════════════
// System Prompt Template Section (Advanced)
// ═══════════════════════════════════════════════════════════════

const PromptTemplateSection = (() => {
    async function render(el) {
        let settings = [];
        try {
            const res = await fetch('/api/settings?category=advanced');
            settings = await res.json();
        } catch (err) {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load template.</p>';
            return;
        }

        const templateSetting = settings.find(s => s.key === 'system_prompt_template');
        const templateValue = templateSetting?.value || '';

        el.innerHTML = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">System Prompt Template</h2>
                <p class="text-sm text-bm-muted mt-0.5">The master template that wraps every agent's personality prompt. Most users won't need to change this.</p>
            </div>
            <div class="mb-4 p-3 bg-slate-50 border border-bm-border rounded-lg">
                <p class="text-xs font-semibold text-bm-muted uppercase tracking-wide mb-2">Available Template Variables</p>
                <div class="grid grid-cols-2 gap-1 text-xs font-mono text-bm-muted">
                    <span>{{personality}}</span><span>Agent's personality prompt</span>
                    <span>{{agent_name}}</span><span>Agent's display name</span>
                    <span>{{role}}</span><span>Agent's role title</span>
                    <span>{{memory}}</span><span>Knowledge graph context</span>
                    <span>{{worldStatus}}</span><span>Location, status, nearby agents, pending triggers</span>
                    <span>{{task}}</span><span>Current task details</span>
                    <span>{{references}}</span><span>Recent work summaries and artifacts</span>
                    <span>{{action_contract}}</span><span>Editable action contract setting</span>
                </div>
            </div>
            <p class="text-xs text-amber-700 mt-3 mb-3">Only variables present in this template will be injected. Nothing is appended behind the scenes.</p>
            <p class="text-xs text-bm-muted mb-1.5">This is the full wrapper prompt sent before every turn. Use it to control role framing, context layout, and the rules the model sees.</p>
            <textarea id="system-prompt-textarea" rows="20"
                      class="w-full px-4 py-3 text-sm border border-bm-border rounded-lg
                             bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                             focus:border-bm-accent resize-y font-mono leading-relaxed">${BossModUtils.escapeHtml(templateValue)}</textarea>
            <div class="flex items-center gap-3 mt-4">
                <button id="btn-save-template"
                        class="px-4 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    Save Template
                </button>
                <span id="template-save-status" class="text-sm text-bm-muted"></span>
            </div>`;

        document.getElementById('btn-save-template').addEventListener('click', async () => {
            const value = document.getElementById('system-prompt-textarea').value;
            const status = document.getElementById('template-save-status');
            try {
                await fetch(`/api/settings/system_prompt_template?value=${encodeURIComponent(value)}&category=advanced`, {
                    method: 'PUT',
                });
                status.textContent = 'Saved';
                status.className = 'text-sm text-emerald-600';
                setTimeout(() => { status.textContent = ''; }, 2000);
            } catch {
                status.textContent = 'Save failed';
                status.className = 'text-sm text-red-600';
            }
        });
    }

    return { render };
})();


// ═══════════════════════════════════════════════════════════════
// Advanced System Settings Section
// ═══════════════════════════════════════════════════════════════

const AdvancedSystemSection = (() => {
    async function render(el) {
        let settings = [];
        try {
            const res = await fetch('/api/settings?category=advanced');
            settings = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load settings.</p>';
            return;
        }

        const diagEnabled = settings.find(s => s.key === 'diagnostics_enabled');
        const diagLimit = settings.find(s => s.key === 'diagnostics_retention_limit');
        const isEnabled = diagEnabled?.value === 'true';

        el.innerHTML = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">Advanced System Settings</h2>
                <p class="text-sm text-bm-muted mt-0.5">Developer tools and system diagnostics.</p>
            </div>
            <div class="max-w-6xl grid grid-cols-1 xl:grid-cols-2 gap-5">
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Enable Diagnostics</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Show the Diagnostics tab for full AI interaction visibility. Data is always captured regardless of this toggle.</p>
                        </div>
                        <button id="btn-toggle-diagnostics"
                                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors
                                       ${isEnabled ? 'bg-bm-accent' : 'bg-slate-300'}"
                                role="switch" aria-checked="${isEnabled}">
                            <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                                         ${isEnabled ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </button>
                    </div>
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Reset Seed Settings</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Overwrite all seed settings back to their code defaults. Custom settings are preserved.</p>
                        </div>
                        <button id="btn-reseed-settings"
                                class="px-3 py-1.5 border border-red-300 text-red-600 rounded-lg
                                       hover:bg-red-50 transition-colors text-sm font-medium">
                            Reseed
                        </button>
                    </div>
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white xl:col-span-2">
                    <label class="block text-sm font-medium mb-1">Diagnostics Retention Limit</label>
                    <p class="text-xs text-bm-muted mb-1.5">Maximum diagnostic entries before auto-purge. Oldest entries are deleted first.</p>
                    <input type="number" id="diag-retention-limit"
                           value="${BossModUtils.escapeHtml(diagLimit?.value || '5000')}"
                           min="100" max="50000" step="100"
                           class="w-32 px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent">
                </div>
            </div>`;

        // Reseed handler
        document.getElementById('btn-reseed-settings').addEventListener('click', async () => {
            if (!confirm('Reset all seed settings to defaults? This will overwrite your changes to the system prompt template and other seed settings.')) return;
            try {
                await fetch('/api/settings/reseed', { method: 'POST' });
                render(el); // Re-render to show updated values
            } catch {
                alert('Reseed failed');
            }
        });

        // Toggle handler
        document.getElementById('btn-toggle-diagnostics').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const nowEnabled = btn.getAttribute('aria-checked') === 'true';
            const newValue = nowEnabled ? 'false' : 'true';

            await fetch(`/api/settings/diagnostics_enabled?value=${newValue}&category=advanced`, { method: 'PUT' });

            btn.setAttribute('aria-checked', String(!nowEnabled));
            btn.classList.toggle('bg-bm-accent', !nowEnabled);
            btn.classList.toggle('bg-slate-300', nowEnabled);
            btn.querySelector('span').classList.toggle('translate-x-6', !nowEnabled);
            btn.querySelector('span').classList.toggle('translate-x-1', nowEnabled);
        });

        // Retention limit handler
        document.getElementById('diag-retention-limit').addEventListener('change', async (e) => {
            const value = e.target.value;
            try {
                await fetch(`/api/settings/diagnostics_retention_limit?value=${encodeURIComponent(value)}&category=advanced`, { method: 'PUT' });
                e.target.classList.add('border-emerald-400');
                setTimeout(() => e.target.classList.remove('border-emerald-400'), 1000);
            } catch {
                e.target.classList.add('border-red-400');
                setTimeout(() => e.target.classList.remove('border-red-400'), 1000);
            }
        });

    }

    return { render };
})();


// ═══════════════════════════════════════════════════════════════
// Action Contract Section (Advanced)
// ═══════════════════════════════════════════════════════════════

const ActionContractSection = (() => {
    async function render(el) {
        let settings = [];
        try {
            const res = await fetch('/api/settings?category=advanced');
            settings = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load action contract.</p>';
            return;
        }

        const contractSetting = settings.find(s => s.key === 'action_contract_template');
        const contractValue = contractSetting?.value || '';

        el.innerHTML = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">Action Contract</h2>
                <p class="text-sm text-bm-muted mt-0.5">This is the prompt-visible action contract. It is editable. Backend validation still enforces the real runtime schema.</p>
            </div>
            <p class="text-xs text-amber-700 mb-3">This text is not hidden. Agents only see what is saved here and what your system prompt template includes via <span class="font-mono">{{action_contract}}</span>.</p>
            <p class="text-xs text-bm-muted mb-1.5">Use this textarea to define the JSON action shapes and examples the model sees. Keep it aligned with runtime behavior so the agent is nudged toward valid outputs.</p>
            <textarea id="action-contract-textarea" rows="18"
                      class="w-full px-4 py-3 text-sm border border-bm-border rounded-lg
                             bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                             focus:border-bm-accent resize-y font-mono leading-relaxed">${BossModUtils.escapeHtml(contractValue)}</textarea>
            <div class="flex items-center gap-3 mt-4">
                <button id="btn-save-action-contract"
                        class="px-4 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    Save Contract
                </button>
                <span id="action-contract-save-status" class="text-sm text-bm-muted"></span>
            </div>`;

        document.getElementById('btn-save-action-contract').addEventListener('click', async () => {
            const value = document.getElementById('action-contract-textarea').value;
            const status = document.getElementById('action-contract-save-status');
            try {
                await fetch(`/api/settings/action_contract_template?value=${encodeURIComponent(value)}&category=advanced`, {
                    method: 'PUT',
                });
                status.textContent = 'Saved';
                status.className = 'text-sm text-emerald-600';
                setTimeout(() => { status.textContent = ''; }, 2000);
            } catch {
                status.textContent = 'Save failed';
                status.className = 'text-sm text-red-600';
            }
        });
    }

    return { render };
})();
