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
        { id: 'prompt-template', label: 'System Prompt Template', icon: 'file-code' },
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
            case 'prompt-template':
                PromptTemplateSection.render(content);
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
    const CATEGORIES = [
        { key: 'simulation', label: 'Simulation' },
        { key: 'social',     label: 'Social Triggers' },
        { key: 'context',    label: 'Context Window' },
    ];

    const LABELS = {
        tick_interval:                'Tick Interval (seconds)',
        steps_per_tick:               'Steps Per Tick',
        social_idle_threshold_minutes: 'Idle Threshold (minutes)',
        social_cooldown_minutes:      'Cooldown (minutes)',
        social_proximity_tiles:       'Proximity (tiles)',
        context_window_messages:      'Message Window Size',
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
            if (!groups[s.category]) groups[s.category] = [];
            groups[s.category].push(s);
        }

        let html = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">System Settings</h2>
                <p class="text-sm text-bm-muted mt-0.5">Configure simulation behavior and social triggers.</p>
            </div>
            <div class="max-w-lg space-y-8">`;

        for (const cat of CATEGORIES) {
            const items = groups[cat.key];
            if (!items || items.length === 0) continue;

            html += `<div>
                <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide mb-3">${cat.label}</h3>
                <div class="space-y-3">`;

            for (const s of items) {
                const label = LABELS[s.key] || s.key;
                html += `
                <div>
                    <label class="block text-sm font-medium mb-1">${BossModUtils.escapeHtml(label)}</label>
                    <input type="text"
                           data-setting-key="${BossModUtils.escapeHtml(s.key)}"
                           data-setting-category="${BossModUtils.escapeHtml(s.category)}"
                           value="${BossModUtils.escapeHtml(s.value)}"
                           class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent max-w-xs">
                </div>`;
            }

            html += '</div></div>';
        }

        html += '</div>';
        el.innerHTML = html;

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
                    <span>{{memory}}</span><span>Knowledge graph context</span>
                    <span>{{location}}</span><span>Current position & nearby agents</span>
                    <span>{{task}}</span><span>Current task details</span>
                    <span>{{available_actions}}</span><span>JSON action schema</span>
                </div>
            </div>
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
