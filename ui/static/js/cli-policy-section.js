/**
 * BossMod AI — CLI Policy settings section.
 *
 * Sub-tabbed IIFE module with five views: Rules, Virtual Commands, Settings, Simulator, Approvals.
 * Renders into the settings content pane via CliPolicySection.render(container).
 */

const CliPolicySection = (() => {
    let container = null;
    let activeTab = 'rules';
    let rulesCache = [];
    let agentsCache = [];
    let agentsFetched = false;
    let simulationResults = [];

    // ─── Helpers ───

    const esc = BossModUtils.escapeHtml;

    function icons(root) {
        if (window.lucide) lucide.createIcons({ nodes: [root || container] });
    }

    async function fetchAgents() {
        if (agentsFetched) return agentsCache;
        try {
            const res = await fetch('/api/agents');
            agentsCache = await res.json();
        } catch {
            agentsCache = [];
        }
        agentsFetched = true;
        return agentsCache;
    }

    function agentName(agentId) {
        if (!agentId) return 'Global';
        const agent = agentsCache.find(a => a.id === agentId);
        return agent ? agent.name : agentId;
    }

    function tierBadge(tier) {
        const map = {
            never_allowed:    'bg-red-500/10 text-red-400',
            always_allowed:   'bg-emerald-500/10 text-emerald-400',
            approval_required: 'bg-amber-500/10 text-amber-400',
        };
        const cls = map[tier] || 'bg-slate-500/10 text-slate-400';
        const label = tier.replace(/_/g, ' ');
        return `<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}">${esc(label)}</span>`;
    }

    function statusBadge(status) {
        const map = {
            pending:  'bg-amber-500/10 text-amber-400',
            approved: 'bg-emerald-500/10 text-emerald-400',
            rejected: 'bg-red-500/10 text-red-400',
            expired:  'bg-slate-500/10 text-slate-400',
        };
        const cls = map[status] || 'bg-slate-500/10 text-slate-400';
        return `<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}">${esc(status)}</span>`;
    }

    function flashBorder(el, success) {
        const cls = success ? 'border-emerald-400' : 'border-red-400';
        el.classList.add(cls);
        setTimeout(() => el.classList.remove(cls), 1000);
    }

    // ─── Main render ───

    async function render(el) {
        container = el;
        await fetchAgents();
        renderShell();
    }

    function renderShell() {
        const tabs = [
            { id: 'rules',            label: 'Rules',            icon: 'list' },
            { id: 'virtual-commands', label: 'Virtual Commands',  icon: 'cpu' },
            { id: 'settings',         label: 'Settings',          icon: 'settings' },
            { id: 'simulator',        label: 'Simulator',         icon: 'terminal' },
            { id: 'approvals',        label: 'Approvals',         icon: 'shield-check' },
        ];

        let html = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">CLI Policy</h2>
                <p class="text-sm text-bm-muted mt-0.5">Manage command execution rules, test policies, and review approval requests.</p>
            </div>
            <div class="mb-5 flex flex-wrap gap-2">`;

        for (const tab of tabs) {
            const active = tab.id === activeTab;
            html += `
                <button type="button" data-cli-tab="${tab.id}"
                        class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border transition-colors
                               ${active ? 'bg-bm-accent text-white border-bm-accent shadow-sm' : 'bg-white text-bm-text border-bm-border hover:bg-slate-50'}">
                    <i data-lucide="${tab.icon}" class="w-4 h-4"></i>
                    ${tab.label}
                    ${tab.id === 'approvals' ? '<span id="cli-approval-count-badge" class="hidden ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-red-500 text-white leading-none"></span>' : ''}
                </button>`;
        }

        html += `</div><div id="cli-tab-content"></div>`;
        container.innerHTML = html;
        icons(container);

        container.querySelectorAll('[data-cli-tab]').forEach(btn => {
            btn.addEventListener('click', () => {
                activeTab = btn.dataset.cliTab;
                renderShell();
            });
        });

        const content = document.getElementById('cli-tab-content');
        switch (activeTab) {
            case 'rules':            renderRulesTab(content);            break;
            case 'virtual-commands': renderVirtualCommandsTab(content);  break;
            case 'settings':         renderSettingsTab(content);         break;
            case 'simulator':        renderSimulatorTab(content);        break;
            case 'approvals':        renderApprovalsTab(content);        break;
        }

        refreshApprovalBadge();
    }

    async function refreshApprovalBadge() {
        try {
            const res = await fetch('/api/cli-policy/approvals?status=pending&limit=200');
            const items = await res.json();
            const badge = document.getElementById('cli-approval-count-badge');
            if (!badge) return;
            if (items.length > 0) {
                badge.textContent = items.length;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        } catch { /* silent */ }
    }


    // ═══════════════════════════════════════════════════════════════
    //  RULES TAB
    // ═══════════════════════════════════════════════════════════════

    async function renderRulesTab(el) {
        try {
            const res = await fetch('/api/cli-policy/rules');
            rulesCache = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load rules.</p>';
            return;
        }

        let html = `
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2">
                    <button id="btn-add-rule"
                            class="flex items-center gap-2 px-3 py-2 bg-bm-accent text-white rounded-lg
                                   hover:opacity-90 transition-colors text-sm font-medium">
                        <i data-lucide="plus" class="w-4 h-4"></i> Add Rule
                    </button>
                    <button id="btn-seed-defaults"
                            class="flex items-center gap-2 px-3 py-2 border border-bm-border rounded-lg
                                   hover:bg-slate-50 transition-colors text-sm font-medium">
                        <i data-lucide="database" class="w-4 h-4"></i> Seed Defaults
                    </button>
                </div>
                <span class="text-xs text-bm-muted">${rulesCache.length} rule${rulesCache.length !== 1 ? 's' : ''}</span>
            </div>
            <div id="cli-rule-form-slot"></div>`;

        if (rulesCache.length === 0) {
            html += `
                <div class="text-center py-12 text-bm-muted">
                    <i data-lucide="shield-off" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                    <p class="text-sm">No rules configured. Add a rule or seed defaults to get started.</p>
                </div>`;
        } else {
            // Sort: by tier order, then priority descending
            const tierOrder = { never_allowed: 0, approval_required: 1, always_allowed: 2 };
            const sorted = [...rulesCache].sort((a, b) => {
                const td = (tierOrder[a.tier] ?? 9) - (tierOrder[b.tier] ?? 9);
                if (td !== 0) return td;
                return b.priority - a.priority;
            });

            html += '<div class="space-y-2">';
            for (const rule of sorted) {
                const scope = rule.agent_id ? esc(agentName(rule.agent_id)) : 'Global';
                html += `
                    <div class="border border-bm-border rounded-lg p-3 bg-white flex items-center gap-3 flex-wrap" data-rule-row="${rule.id}">
                        <div class="shrink-0">${tierBadge(rule.tier)}</div>
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2 flex-wrap">
                                <code class="text-sm font-mono bg-bm-bg px-2 py-0.5 rounded">${esc(rule.pattern)}</code>
                                <span class="text-xs text-bm-muted border border-bm-border rounded px-1.5 py-0.5">${esc(rule.match_mode)}</span>
                                <span class="text-xs text-blue-400 bg-blue-500/10 rounded px-1.5 py-0.5">${esc(rule.category)}</span>
                                <span class="text-xs text-bm-muted">${esc(scope)}</span>
                            </div>
                            ${rule.description ? `<p class="text-xs text-bm-muted mt-1">${esc(rule.description)}</p>` : ''}
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <span class="text-[10px] text-bm-muted">pri ${rule.priority}</span>
                            <button data-toggle-enabled="${rule.id}"
                                    class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors
                                           ${rule.enabled ? 'bg-bm-accent' : 'bg-slate-300'}"
                                    role="switch" aria-checked="${rule.enabled}" title="${rule.enabled ? 'Enabled' : 'Disabled'}">
                                <span class="inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform
                                             ${rule.enabled ? 'translate-x-4' : 'translate-x-0.5'}"></span>
                            </button>
                            <button data-edit-rule="${rule.id}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors" title="Edit">
                                <i data-lucide="pencil" class="w-3.5 h-3.5 text-bm-muted"></i>
                            </button>
                            <button data-delete-rule="${rule.id}"
                                    class="p-1.5 rounded-lg hover:bg-red-50 transition-colors" title="Delete">
                                <i data-lucide="trash-2" class="w-3.5 h-3.5 text-red-400"></i>
                            </button>
                        </div>
                    </div>`;
            }
            html += '</div>';
        }

        el.innerHTML = html;
        icons(el);

        // Add rule
        document.getElementById('btn-add-rule').addEventListener('click', () => {
            showRuleForm(null);
        });

        // Seed defaults
        document.getElementById('btn-seed-defaults').addEventListener('click', async () => {
            if (!confirm('This will delete ALL existing rules and replace them with the defaults. Continue?')) return;
            try {
                await fetch('/api/cli-policy/rules/seed-defaults', { method: 'POST' });
                renderRulesTab(el);
            } catch {
                alert('Failed to seed defaults.');
            }
        });

        // Toggle enabled
        el.querySelectorAll('[data-toggle-enabled]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const ruleId = btn.dataset.toggleEnabled;
                const rule = rulesCache.find(r => r.id === ruleId);
                if (!rule) return;
                try {
                    await fetch(`/api/cli-policy/rules/${ruleId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled: !rule.enabled }),
                    });
                    renderRulesTab(el);
                } catch { /* silent */ }
            });
        });

        // Edit rule
        el.querySelectorAll('[data-edit-rule]').forEach(btn => {
            btn.addEventListener('click', () => {
                const rule = rulesCache.find(r => r.id === btn.dataset.editRule);
                if (rule) showRuleForm(rule);
            });
        });

        // Delete rule
        el.querySelectorAll('[data-delete-rule]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this rule?')) return;
                try {
                    await fetch(`/api/cli-policy/rules/${btn.dataset.deleteRule}`, { method: 'DELETE' });
                    renderRulesTab(el);
                } catch {
                    alert('Failed to delete rule.');
                }
            });
        });
    }

    function showRuleForm(rule) {
        const isEdit = !!rule;
        const slot = document.getElementById('cli-rule-form-slot');
        if (!slot) return;

        const agentOptions = agentsCache.map(a =>
            `<option value="${esc(a.id)}" ${rule?.agent_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`
        ).join('');

        slot.innerHTML = `
            <div class="border border-bm-accent/30 rounded-xl p-4 bg-bm-accent/5 mb-4">
                <h3 class="text-sm font-semibold mb-3">${isEdit ? 'Edit Rule' : 'New Rule'}</h3>
                <form id="cli-rule-form" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-medium mb-1">Tier</label>
                        <select name="tier" required
                                class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                            <option value="never_allowed" ${rule?.tier === 'never_allowed' ? 'selected' : ''}>Never Allowed</option>
                            <option value="always_allowed" ${rule?.tier === 'always_allowed' ? 'selected' : ''}>Always Allowed</option>
                            <option value="approval_required" ${rule?.tier === 'approval_required' ? 'selected' : ''}>Approval Required</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Match Mode</label>
                        <select name="match_mode" required
                                class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                            <option value="prefix" ${rule?.match_mode === 'prefix' ? 'selected' : ''}>Prefix</option>
                            <option value="exact" ${rule?.match_mode === 'exact' ? 'selected' : ''}>Exact</option>
                            <option value="glob" ${rule?.match_mode === 'glob' ? 'selected' : ''}>Glob</option>
                        </select>
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Pattern</label>
                        <input type="text" name="pattern" required
                               value="${esc(rule?.pattern || '')}"
                               placeholder="e.g. rm -rf, git push --force"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Applies To</label>
                        <select name="agent_id"
                                class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                            <option value="" ${!rule?.agent_id ? 'selected' : ''}>All Agents</option>
                            ${agentOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Priority</label>
                        <input type="number" name="priority"
                               value="${rule?.priority ?? 0}" min="0" max="9999"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Description</label>
                        <input type="text" name="description"
                               value="${esc(rule?.description || '')}"
                               placeholder="What this rule does"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Category</label>
                        <input type="text" name="category"
                               value="${esc(rule?.category || 'general')}"
                               placeholder="e.g. filesystem, network, packages"
                               list="cli-category-suggestions"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                        <datalist id="cli-category-suggestions">
                            <option value="general">
                            <option value="filesystem">
                            <option value="network">
                            <option value="packages">
                            <option value="development">
                            <option value="git">
                            <option value="system">
                        </datalist>
                    </div>
                    <div>
                        <label class="block text-xs font-medium mb-1">Usage Syntax</label>
                        <input type="text" name="usage_syntax"
                               value="${esc(rule?.usage_syntax || '')}"
                               placeholder="e.g. curl [options] <url>"
                               class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs font-medium mb-1">Help Text</label>
                        <textarea name="help_text" rows="4"
                                  placeholder="Detailed help shown when agents type: learn commandname"
                                  class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono">${esc(rule?.help_text || '')}</textarea>
                    </div>
                    <div class="md:col-span-2 flex items-center gap-4">
                        <label class="flex items-center gap-2 text-sm">
                            <input type="checkbox" name="enabled" ${rule?.enabled !== false ? 'checked' : ''}
                                   class="rounded border-bm-border text-bm-accent focus:ring-bm-accent/30">
                            Enabled
                        </label>
                    </div>
                    <div class="md:col-span-2 flex gap-2 pt-1">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg text-sm font-medium hover:opacity-90">
                            ${isEdit ? 'Save Changes' : 'Create Rule'}
                        </button>
                        <button type="button" id="btn-cancel-rule-form"
                                class="px-4 py-2 border border-bm-border rounded-lg text-sm font-medium hover:bg-slate-50">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        icons(slot);

        document.getElementById('btn-cancel-rule-form').addEventListener('click', () => {
            slot.innerHTML = '';
        });

        document.getElementById('cli-rule-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const data = {
                tier: fd.get('tier'),
                pattern: fd.get('pattern'),
                match_mode: fd.get('match_mode'),
                agent_id: fd.get('agent_id') || null,
                description: fd.get('description') || null,
                enabled: fd.has('enabled'),
                priority: parseInt(fd.get('priority') || '0', 10),
                category: fd.get('category') || 'general',
                usage_syntax: fd.get('usage_syntax') || null,
                help_text: fd.get('help_text') || null,
            };

            try {
                if (isEdit) {
                    await fetch(`/api/cli-policy/rules/${rule.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await fetch('/api/cli-policy/rules', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                const tabContent = document.getElementById('cli-tab-content');
                if (tabContent) renderRulesTab(tabContent);
            } catch {
                alert('Failed to save rule.');
            }
        });
    }


    // ═══════════════════════════════════════════════════════════════
    //  SETTINGS TAB
    // ═══════════════════════════════════════════════════════════════

    const SETTINGS_META = {
        cli_shell_enabled: {
            order: 10,
            label: 'Shell Executor',
            description: 'Enable or disable the real shell executor. When disabled, all commands run in the virtual CLI only.',
            type: 'toggle',
        },
        cli_shell_timeout_seconds: {
            order: 20,
            label: 'Shell Timeout (seconds)',
            description: 'Maximum time a single shell command is allowed to execute before being killed.',
            type: 'number',
        },
        cli_shell_max_output_bytes: {
            order: 30,
            label: 'Max Output Size (bytes)',
            description: 'Maximum bytes of stdout/stderr captured from a shell command. Output beyond this limit is truncated.',
            type: 'number',
        },
        cli_approval_timeout_minutes: {
            order: 40,
            label: 'Approval Timeout (minutes)',
            description: 'How long a pending approval request remains valid before auto-expiring.',
            type: 'number',
        },
        cli_default_policy: {
            order: 50,
            label: 'Default Policy',
            description: 'What happens when a command does not match any rule. "deny" blocks the command, "approval_required" pauses for human approval.',
            type: 'select',
            options: [
                { value: 'deny', label: 'Deny' },
                { value: 'approval_required', label: 'Approval Required' },
            ],
        },
    };

    async function renderSettingsTab(el) {
        let settings = [];
        try {
            const res = await fetch('/api/settings?category=cli_policy');
            settings = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load CLI policy settings.</p>';
            return;
        }

        // Filter to known settings and sort by order
        const known = settings.filter(s => SETTINGS_META[s.key]);
        known.sort((a, b) => (SETTINGS_META[a.key]?.order ?? 999) - (SETTINGS_META[b.key]?.order ?? 999));

        let html = '<div class="max-w-3xl space-y-4">';

        for (const s of known) {
            const meta = SETTINGS_META[s.key];
            html += `<div class="border border-bm-border rounded-xl p-4 bg-white transition-colors" data-setting-card="${s.key}">`;

            if (meta.type === 'toggle') {
                const isOn = s.value === 'true';
                html += `
                    <div class="flex items-center justify-between">
                        <div class="flex-1 min-w-0 mr-4">
                            <h3 class="text-sm font-semibold">${esc(meta.label)}</h3>
                            <p class="text-xs text-bm-muted mt-0.5">${esc(meta.description)}</p>
                        </div>
                        <button data-cli-setting-toggle="${s.key}"
                                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors shrink-0
                                       ${isOn ? 'bg-bm-accent' : 'bg-slate-300'}"
                                role="switch" aria-checked="${isOn}">
                            <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                                         ${isOn ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </button>
                    </div>`;
            } else if (meta.type === 'select') {
                const options = meta.options.map(opt =>
                    `<option value="${esc(opt.value)}" ${s.value === opt.value ? 'selected' : ''}>${esc(opt.label)}</option>`
                ).join('');
                html += `
                    <label class="block text-sm font-semibold mb-1">${esc(meta.label)}</label>
                    <p class="text-xs text-bm-muted mb-2">${esc(meta.description)}</p>
                    <select data-cli-setting-input="${s.key}"
                            class="w-full max-w-xs px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                        ${options}
                    </select>`;
            } else {
                html += `
                    <label class="block text-sm font-semibold mb-1">${esc(meta.label)}</label>
                    <p class="text-xs text-bm-muted mb-2">${esc(meta.description)}</p>
                    <input type="number" data-cli-setting-input="${s.key}"
                           value="${esc(s.value)}"
                           class="w-full max-w-xs px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">`;
            }

            html += '</div>';
        }

        if (known.length === 0) {
            html += `
                <div class="text-center py-12 text-bm-muted">
                    <i data-lucide="settings" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                    <p class="text-sm">No CLI policy settings found. They will appear once the system seeds them.</p>
                </div>`;
        }

        html += '</div>';
        el.innerHTML = html;
        icons(el);

        // Toggle switches
        el.querySelectorAll('[data-cli-setting-toggle]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const key = btn.dataset.cliSettingToggle;
                const current = btn.getAttribute('aria-checked') === 'true';
                const newVal = (!current).toString();
                const card = el.querySelector(`[data-setting-card="${key}"]`);
                try {
                    await fetch(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(newVal)}&category=cli_policy`, {
                        method: 'PUT',
                    });
                    if (card) flashBorder(card, true);
                    renderSettingsTab(el);
                } catch {
                    if (card) flashBorder(card, false);
                }
            });
        });

        // Number/select inputs
        el.querySelectorAll('[data-cli-setting-input]').forEach(input => {
            input.addEventListener('change', async (e) => {
                const key = e.target.dataset.cliSettingInput;
                const value = e.target.value;
                const card = el.querySelector(`[data-setting-card="${key}"]`);
                try {
                    await fetch(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=cli_policy`, {
                        method: 'PUT',
                    });
                    if (card) flashBorder(card, true);
                } catch {
                    if (card) flashBorder(card, false);
                }
            });
        });
    }


    // ═══════════════════════════════════════════════════════════════
    //  SIMULATOR TAB
    // ═══════════════════════════════════════════════════════════════

    // ── Simulator state ──
    let simHistory = [];
    let simRunning = false;
    let simCommandHistory = [];
    let simHistoryIdx = -1;
    let simShellEnabled = false;
    let simDefaultPolicy = 'deny';

    async function _fetchSimStatus() {
        try {
            const res = await fetch('/api/settings?category=cli_policy');
            const settings = await res.json();
            for (const s of settings) {
                if (s.key === 'cli_shell_enabled') simShellEnabled = s.value === 'true';
                if (s.key === 'cli_default_policy') simDefaultPolicy = s.value || 'deny';
            }
        } catch { /* use defaults */ }
    }

    async function renderSimulatorTab(el) {
        await _fetchSimStatus();

        // Pre-fetch rules for matched rule hints
        if (rulesCache.length === 0) {
            try {
                const res = await fetch('/api/cli-policy/rules');
                rulesCache = await res.json();
            } catch { /* non-critical */ }
        }

        if (agentsCache.length === 0) {
            el.innerHTML = `
                <div class="text-center py-16 text-bm-muted">
                    <i data-lucide="bot" class="w-12 h-12 mx-auto mb-4 opacity-30"></i>
                    <p class="text-sm font-medium mb-1">No agents found</p>
                    <p class="text-xs">Create an agent first — the simulator runs commands as a specific agent to test their permissions.</p>
                </div>`;
            icons(el);
            return;
        }

        const agentOptions = agentsCache.map(a =>
            `<option value="${esc(a.id)}">${esc(a.name)}</option>`
        ).join('');

        const shellBadge = simShellEnabled
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">SHELL ON</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-500/15 text-red-400 border border-red-500/20">SHELL OFF</span>';

        const policyBadge = simDefaultPolicy === 'approval_required'
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/20">DEFAULT: APPROVAL</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-500/15 text-slate-400 border border-slate-500/20">DEFAULT: DENY</span>';

        el.innerHTML = `
            <div class="flex flex-col h-full max-h-[calc(100vh-12rem)]">
                <!-- Controls bar -->
                <div class="flex items-center gap-3 mb-3 shrink-0 flex-wrap">
                    <div class="flex items-center gap-2">
                        <i data-lucide="user" class="w-4 h-4 text-bm-muted"></i>
                        <label class="text-xs font-semibold whitespace-nowrap">Run as</label>
                        <select id="cli-sim-agent"
                                class="px-3 py-1.5 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-medium min-w-[200px]">
                            ${agentOptions}
                        </select>
                    </div>
                    <div class="flex items-center gap-2">
                        ${shellBadge}
                        ${policyBadge}
                    </div>
                    <button id="btn-sim-clear"
                            class="ml-auto text-xs text-bm-muted hover:text-bm-text flex items-center gap-1">
                        <i data-lucide="trash-2" class="w-3 h-3"></i> Clear
                    </button>
                </div>

                <!-- Terminal -->
                <div id="cli-sim-terminal"
                     class="flex-1 bg-gray-950 rounded-xl border border-gray-800 overflow-hidden flex flex-col font-mono text-[15px] leading-relaxed min-h-[400px] shadow-lg">
                    <!-- Title bar -->
                    <div class="flex items-center gap-2 px-4 py-2 bg-gray-900/80 border-b border-gray-800 shrink-0">
                        <div class="w-3 h-3 rounded-full bg-red-500/80"></div>
                        <div class="w-3 h-3 rounded-full bg-yellow-500/80"></div>
                        <div class="w-3 h-3 rounded-full bg-green-500/80"></div>
                        <span class="ml-2 text-gray-500 text-xs" id="cli-sim-title">BossMod CLI Simulator</span>
                    </div>
                    <!-- Output -->
                    <div id="cli-sim-output"
                         class="flex-1 overflow-y-auto p-4 text-gray-300 space-y-0.5 min-h-0">
                    </div>
                    <!-- Input bar -->
                    <div class="flex items-center gap-0 px-4 py-2.5 bg-gray-900/80 border-t border-gray-800 shrink-0">
                        <span class="text-emerald-400 mr-2 select-none font-semibold" id="cli-sim-prompt">$</span>
                        <input type="text" id="cli-sim-input"
                               class="flex-1 bg-transparent text-gray-200 outline-none text-[15px] font-mono"
                               placeholder="Type a command..."
                               autocomplete="off" spellcheck="false">
                        <span class="text-gray-600 text-xs ml-2 select-none hidden sm:inline" id="cli-sim-hint">
                            &uarr;&darr; history
                        </span>
                    </div>
                </div>
            </div>`;

        icons(el);
        simHistory = [];
        simCommandHistory = [];
        simHistoryIdx = -1;
        _updateSimPrompt();
        _printWelcomeBanner();

        const input = document.getElementById('cli-sim-input');

        input.focus();

        // Focus input when clicking terminal
        document.getElementById('cli-sim-terminal').addEventListener('click', (e) => {
            if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') input.focus();
        });

        // Agent change updates prompt
        document.getElementById('cli-sim-agent').addEventListener('change', () => {
            _updateSimPrompt();
            const select = document.getElementById('cli-sim-agent');
            const name = select?.selectedOptions[0]?.text || 'agent';
            _simLine('dim', `Switched to ${name}.`);
            _simBlank();
        });

        // Clear
        document.getElementById('btn-sim-clear').addEventListener('click', () => {
            const out = document.getElementById('cli-sim-output');
            if (out) out.innerHTML = '';
            _simLine('dim', 'Terminal cleared. Type <span class="text-cyan-400">help</span> for commands.');
            _simBlank();
        });

        // Handle input
        input.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const cmd = input.value.trim();
                input.value = '';
                if (!cmd) return;

                simCommandHistory.push(cmd);
                simHistoryIdx = simCommandHistory.length;

                await _executeSimCommand(cmd);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (simHistoryIdx > 0) {
                    simHistoryIdx--;
                    input.value = simCommandHistory[simHistoryIdx] || '';
                }
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (simHistoryIdx < simCommandHistory.length - 1) {
                    simHistoryIdx++;
                    input.value = simCommandHistory[simHistoryIdx] || '';
                } else {
                    simHistoryIdx = simCommandHistory.length;
                    input.value = '';
                }
            }
        });
    }

    // ── Simulator output helpers ──

    function _simLine(type, html) {
        const cls = {
            dim:     'text-gray-400',
            text:    'text-gray-200',
            bright:  'text-gray-100',
            cyan:    'text-cyan-400',
            green:   'text-emerald-400',
            red:     'text-red-400',
            amber:   'text-amber-400',
            header:  'text-gray-400 text-xs uppercase tracking-wider font-semibold mt-2',
        }[type] || 'text-gray-200';
        _appendOutput(cls, html);
    }

    function _simBlank() {
        _appendOutput('text-gray-500', '&nbsp;');
    }

    function _printWelcomeBanner() {
        const shellStatus = simShellEnabled
            ? '<span class="text-emerald-400">enabled</span> — commands go through policy check, then execute on the host'
            : '<span class="text-red-400">disabled</span> — only built-in virtual commands are available';

        _simLine('cyan',   '┌───────────────────────────────────────────────┐');
        _simLine('cyan',   '│             BossMod CLI Simulator             │');
        _simLine('cyan',   '└───────────────────────────────────────────────┘');
        _simBlank();
        _simLine('dim',    `Shell executor: ${shellStatus}`);
        _simLine('dim',    `Default policy: <span class="text-gray-300">${esc(simDefaultPolicy)}</span> (when no rule matches)`);
        _simBlank();
        _simLine('dim',    'This terminal runs commands exactly as the selected agent would.');
        _simLine('dim',    'Every command goes through the full BM_CLI pipeline:');
        _simLine('dim',    '  policy check → execute → result');
        _simBlank();
        _simLine('dim',    'Try these:');
        _simLine('text',   '  <span class="text-cyan-400">help</span>             — discover available commands');
        _simLine('text',   '  <span class="text-cyan-400">categories</span>       — browse commands by category');
        _simLine('text',   '  <span class="text-cyan-400">fsearch network</span>  — search for commands by keyword');
        _simLine('text',   '  <span class="text-cyan-400">learn cat</span>        — detailed usage for a command');
        _simLine('text',   '  <span class="text-cyan-400">pwd</span>             — built-in command, always works');
        _simLine('text',   '  <span class="text-cyan-400">echo hello</span>      — requires shell enabled');
        _simBlank();
    }

    function _updateSimPrompt() {
        const select = document.getElementById('cli-sim-agent');
        const prompt = document.getElementById('cli-sim-prompt');
        const title = document.getElementById('cli-sim-title');
        if (!select || !prompt) return;
        const name = select.selectedOptions[0]?.text || 'agent';
        prompt.textContent = `${name} $`;
        if (title) title.textContent = `BossMod CLI — ${name}`;
    }

    function _appendOutput(cls, html) {
        const out = document.getElementById('cli-sim-output');
        if (!out) return;
        const div = document.createElement('div');
        div.className = cls;
        div.innerHTML = html;
        out.appendChild(div);
        out.scrollTop = out.scrollHeight;
    }

    function _appendCommandEcho(cmd) {
        const select = document.getElementById('cli-sim-agent');
        const name = select?.selectedOptions[0]?.text || 'agent';
        _appendOutput('text-gray-200', `<span class="text-emerald-400 font-semibold">${esc(name)} $</span> ${esc(cmd)}`);
    }

    async function _executeSimCommand(cmd) {
        // Unwrap bm_cli("...") / bm_cli('...') wrapper if the user types it
        const wrapMatch = cmd.match(/^bm_cli\s*\(\s*["'](.+?)["']\s*\)$/);
        if (wrapMatch) cmd = wrapMatch[1];

        const agentId = document.getElementById('cli-sim-agent')?.value;
        _appendCommandEcho(cmd);

        if (!agentId) {
            _simLine('red', 'No agent selected. Choose an agent from the dropdown above.');
            _simBlank();
            return;
        }

        // Every command goes through the real BM_CLI pipeline — no shortcuts
        simRunning = true;
        const loadingEl = document.createElement('div');
        loadingEl.className = 'text-gray-600 animate-pulse';
        loadingEl.textContent = 'executing...';
        const out = document.getElementById('cli-sim-output');
        if (out) { out.appendChild(loadingEl); out.scrollTop = out.scrollHeight; }

        try {
            const res = await fetch('/api/cli-policy/simulator/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd, agent_id: agentId }),
            });
            const data = await res.json();

            // Remove the loading indicator
            if (loadingEl.parentNode) loadingEl.remove();

            if (!res.ok) {
                _simLine('red', `Error: ${esc(data.detail || 'Request failed')}`);
                _simBlank();
                return;
            }

            // Render result based on kind
            _renderExecutionResult(data);
        } catch (err) {
            if (loadingEl.parentNode) loadingEl.remove();
            _simLine('red', `Network error: ${esc(err.message)}`);
        }
        simRunning = false;
        _simBlank();
    }

    function _renderExecutionResult(data) {
        // ── Status banner ──
        if (data.ok) {
            _appendOutput(
                'bg-emerald-500/10 text-emerald-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-emerald-500/20',
                `&#10003; OK — ${esc(data.kind)} (${esc(data.executor)})`
            );
        } else if (data.approval_required) {
            _appendOutput(
                'bg-amber-500/10 text-amber-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-amber-500/20',
                `&#9888; APPROVAL REQUIRED — this command is gated behind human approval`
            );
        } else {
            _appendOutput(
                'bg-red-500/10 text-red-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-red-500/20',
                `&#10007; BLOCKED — exit ${data.exit_code} (${esc(data.kind)})`
            );
        }

        // ── Approval detail ──
        if (data.approval_required) {
            _simLine('amber', `In a real agent turn, the turn would pause here until you approve or reject.`);
            if (data.approval_request_id) {
                _simLine('dim', `Approval request: ${esc(data.approval_request_id)}`);
            }
            _simLine('dim', `Go to the Approvals tab to manage pending requests.`);
            return;
        }

        // ── Detail message (for errors/blocks) ──
        if (!data.ok && data.detail) {
            _simLine('red', esc(data.detail));
        }

        // ── Output content ──
        const output = data.output || '';
        if (output.trim()) {
            const lines = output.split('\n');
            for (const line of lines) {
                // Skip BossMod wrapper header lines
                if (line.startsWith('BOSSMOD CLI RESULT') || line.startsWith('command:')) continue;
                if (!line.trim()) continue;

                if (line.match(/^[A-Z][A-Z ]+:$/)) {
                    // Section header (STDOUT:, STDERR:, ERROR:, etc.)
                    const label = line.replace(/:$/, '');
                    const labelColor = label === 'ERROR' || label === 'STDERR'
                        ? 'text-red-400' : 'text-gray-400';
                    _appendOutput(`${labelColor} text-xs uppercase tracking-wider font-semibold mt-2`, label);
                } else {
                    _simLine('text', esc(line));
                }
            }
        }

        // ── Matched rule hint ──
        if (data.matched_rule_id) {
            const rule = rulesCache.find(r => r.id === data.matched_rule_id);
            if (rule) {
                _simLine('dim', `matched rule: "${esc(rule.pattern)}" (${esc(rule.match_mode)}) — ${esc(rule.description || rule.tier)}`);
            }
        }

        // ── CWD ──
        if (data.cwd) {
            _simLine('dim', `cwd: ${esc(data.cwd)}`);
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  VIRTUAL COMMANDS TAB
    // ═══════════════════════════════════════════════════════════════

    async function renderVirtualCommandsTab(el) {
        let data = { commands: [], categories: [] };
        try {
            const res = await fetch('/api/cli-policy/virtual-commands');
            data = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load virtual commands.</p>';
            return;
        }

        const byCategory = {};
        for (const cmd of data.commands) {
            if (!byCategory[cmd.category]) byCategory[cmd.category] = [];
            byCategory[cmd.category].push(cmd);
        }

        const catOrder = data.categories.map(c => c.name);
        const catDescriptions = {};
        for (const c of data.categories) catDescriptions[c.name] = c.description;

        let html = `
            <div class="mb-5">
                <div class="flex items-start gap-3 p-4 bg-blue-500/5 border border-blue-500/20 rounded-xl">
                    <i data-lucide="info" class="w-5 h-5 text-blue-400 shrink-0 mt-0.5"></i>
                    <div class="text-sm">
                        <p class="font-medium mb-1">Built-in virtual commands</p>
                        <p class="text-xs text-bm-muted">Always available to all agents. These run inside the BossMod virtual environment — no shell access or policy rules required.</p>
                    </div>
                </div>
            </div>`;

        for (const cat of catOrder) {
            const cmds = byCategory[cat];
            if (!cmds || cmds.length === 0) continue;
            const desc = catDescriptions[cat] || '';

            html += `
                <div class="mb-5">
                    <h3 class="text-sm font-semibold capitalize mb-2 flex items-center gap-2">
                        ${esc(cat)}
                        <span class="text-xs text-bm-muted font-normal">${esc(desc)}</span>
                    </h3>
                    <div class="space-y-1.5">`;

            for (const cmd of cmds) {
                html += `
                        <details class="group border border-bm-border rounded-lg bg-white overflow-hidden">
                            <summary class="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-slate-50 transition-colors">
                                <code class="text-sm font-mono font-semibold text-bm-accent">${esc(cmd.name)}</code>
                                <span class="text-xs text-bm-muted flex-1">${esc(cmd.description)}</span>
                                <code class="text-xs text-bm-muted font-mono hidden sm:inline">${esc(cmd.usage_syntax)}</code>
                                <i data-lucide="chevron-down" class="w-4 h-4 text-bm-muted transition-transform group-open:rotate-180"></i>
                            </summary>
                            <div class="px-4 py-3 border-t border-bm-border bg-bm-bg/50">
                                <div class="text-xs mb-2">
                                    <span class="font-semibold">Usage:</span>
                                    <code class="ml-1 font-mono">${esc(cmd.usage_syntax)}</code>
                                </div>
                                <pre class="text-xs text-bm-muted whitespace-pre-wrap font-mono leading-relaxed">${esc(cmd.help_text)}</pre>
                            </div>
                        </details>`;
            }

            html += '</div></div>';
        }

        el.innerHTML = html;
        icons(el);
    }


    // ═══════════════════════════════════════════════════════════════
    //  APPROVALS TAB
    // ═══════════════════════════════════════════════════════════════

    async function renderApprovalsTab(el) {
        let approvals = [];
        try {
            const res = await fetch('/api/cli-policy/approvals?limit=50');
            approvals = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load approvals.</p>';
            return;
        }

        // Partition into pending and resolved
        const pending = approvals.filter(a => a.status === 'pending');
        const resolved = approvals.filter(a => a.status !== 'pending');

        let html = '';

        // Pending section
        html += `<h3 class="text-sm font-semibold mb-3">Pending Requests <span class="text-bm-muted font-normal">(${pending.length})</span></h3>`;

        if (pending.length === 0) {
            html += `
                <div class="text-center py-8 text-bm-muted border border-bm-border rounded-xl bg-white mb-6">
                    <i data-lucide="check-circle-2" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
                    <p class="text-sm">No pending approvals.</p>
                </div>`;
        } else {
            html += '<div class="space-y-3 mb-6">';
            for (const req of pending) {
                html += renderApprovalCard(req, true);
            }
            html += '</div>';
        }

        // Resolved section
        if (resolved.length > 0) {
            html += `<h3 class="text-sm font-semibold mb-3">Recent Decisions <span class="text-bm-muted font-normal">(${resolved.length})</span></h3>`;
            html += '<div class="space-y-2">';
            for (const req of resolved) {
                html += renderApprovalCard(req, false);
            }
            html += '</div>';
        }

        el.innerHTML = html;
        icons(el);

        // Approve buttons
        el.querySelectorAll('[data-approve]').forEach(btn => {
            btn.addEventListener('click', async () => {
                try {
                    await fetch(`/api/cli-policy/approvals/${btn.dataset.approve}/approve`, { method: 'POST' });
                    renderApprovalsTab(el);
                    refreshApprovalBadge();
                } catch {
                    alert('Failed to approve.');
                }
            });
        });

        // Reject buttons — show inline note input
        el.querySelectorAll('[data-reject-show]').forEach(btn => {
            btn.addEventListener('click', () => {
                const noteRow = document.getElementById(`reject-note-${btn.dataset.rejectShow}`);
                if (noteRow) noteRow.classList.toggle('hidden');
            });
        });

        // Reject confirm
        el.querySelectorAll('[data-reject-confirm]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const reqId = btn.dataset.rejectConfirm;
                const noteInput = document.getElementById(`reject-note-input-${reqId}`);
                const note = noteInput ? noteInput.value.trim() : '';
                try {
                    await fetch(`/api/cli-policy/approvals/${reqId}/reject`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ decision_note: note || null }),
                    });
                    renderApprovalsTab(el);
                    refreshApprovalBadge();
                } catch {
                    alert('Failed to reject.');
                }
            });
        });
    }

    function renderApprovalCard(req, isPending) {
        const createdAt = new Date(req.created_at);
        const timeStr = createdAt.toLocaleString();
        const agent = agentName(req.agent_id);

        let html = `
            <div class="border border-bm-border rounded-xl p-4 bg-white">
                <div class="flex items-start justify-between gap-3">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap mb-1">
                            <span class="text-sm font-medium">${esc(agent)}</span>
                            ${statusBadge(req.status)}
                            <span class="text-[10px] text-bm-muted">${esc(timeStr)}</span>
                        </div>
                        <code class="block text-sm font-mono bg-bm-bg px-2 py-1 rounded mt-1 truncate">${esc(req.command)}</code>
                        ${req.cwd ? `<p class="text-[10px] text-bm-muted mt-1">cwd: ${esc(req.cwd)}</p>` : ''}
                        ${req.decision_note ? `<p class="text-xs text-bm-muted mt-1 italic">Note: ${esc(req.decision_note)}</p>` : ''}
                    </div>`;

        if (isPending) {
            html += `
                    <div class="flex items-center gap-2 shrink-0">
                        <button data-approve="${req.id}"
                                class="px-3 py-1.5 bg-emerald-500 text-white rounded-lg text-xs font-medium hover:opacity-90">
                            Approve
                        </button>
                        <button data-reject-show="${req.id}"
                                class="px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:opacity-90">
                            Reject
                        </button>
                    </div>`;
        }

        html += `
                </div>`;

        if (isPending) {
            html += `
                <div id="reject-note-${req.id}" class="hidden mt-3 pt-3 border-t border-bm-border">
                    <div class="flex items-center gap-2">
                        <input type="text" id="reject-note-input-${req.id}"
                               placeholder="Rejection note (optional)"
                               class="flex-1 px-3 py-1.5 bg-bm-bg border border-bm-border rounded-lg text-xs text-bm-text">
                        <button data-reject-confirm="${req.id}"
                                class="px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:opacity-90">
                            Confirm Reject
                        </button>
                    </div>
                </div>`;
        }

        html += '</div>';
        return html;
    }


    return { render };
})();
