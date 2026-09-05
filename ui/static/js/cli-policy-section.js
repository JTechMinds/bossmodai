/**
 * BossMod AI — CLI Policy settings section (HA-STRUCT-P1-04).
 *
 * Shell + Rules / Virtual Commands / Settings / Approvals tabs.
 * Simulator lives in cli-policy-simulator.js (CliPolicySimulator.render).
 */

const CliPolicySection = (() => {
    let container = null;
    let activeTab = 'rules';
    let rulesCache = [];
    let agentsCache = [];
    let agentsFetched = false;


    // ─── Helpers ───

    const esc = BossModUtils.escapeHtml;

    function icons(root) {
        if (window.lucide) lucide.createIcons({ nodes: [root || container] });
    }

    async function fetchAgents() {
        if (agentsFetched) return agentsCache;
        try {
            const res = await apiFetch('/api/agents');
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

    function applySettingSaveResult(card, ok, message) {
        if (!card) return;
        flashBorder(card, ok);
        let node = card.querySelector('[data-setting-status]');
        if (!message) {
            if (node) node.textContent = '';
            return;
        }
        if (!node) {
            node = document.createElement('p');
            node.setAttribute('data-setting-status', '');
            card.appendChild(node);
        }
        node.textContent = message;
        node.className = ok ? 'text-xs mt-2 text-emerald-600' : 'text-xs mt-2 text-red-500';
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
            case 'simulator':        CliPolicySimulator.render(content); break;
            case 'approvals':        renderApprovalsTab(content);        break;
        }

        refreshApprovalBadge();
    }

    async function refreshApprovalBadge() {
        try {
            const res = await apiFetch('/api/cli-policy/approvals?status=pending&limit=200');
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

    const TIER_ORDER  = { never_allowed: 0, approval_required: 1, always_allowed: 2 };
    const TIER_BORDER = { never_allowed: '#ef4444', approval_required: '#f59e0b', always_allowed: '#10b981' };
    const TIER_LABEL  = { never_allowed: 'Never Allowed', approval_required: 'Approval Required', always_allowed: 'Always Allowed' };
    const TIER_SHORT  = { never_allowed: 'Never', approval_required: 'Approval', always_allowed: 'Allowed' };
    const TIER_CSS    = { never_allowed: 'tier-never', approval_required: 'tier-approval', always_allowed: 'tier-allowed' };

    let sortCol = null;
    let sortDir = 'asc';
    let filterTier = null;
    let searchText = '';
    let _rulesTabEl = null;

    function getFilteredSorted() {
        let rows = [...rulesCache];
        if (filterTier) rows = rows.filter(r => r.tier === filterTier);
        if (searchText) {
            const q = searchText.toLowerCase();
            rows = rows.filter(r =>
                r.pattern.toLowerCase().includes(q) ||
                (r.description || '').toLowerCase().includes(q) ||
                (r.category || '').toLowerCase().includes(q) ||
                r.match_mode.toLowerCase().includes(q) ||
                r.tier.replace(/_/g, ' ').includes(q) ||
                agentName(r.agent_id).toLowerCase().includes(q) ||
                String(r.priority).includes(q)
            );
        }
        if (sortCol) {
            rows.sort((a, b) => {
                let va, vb;
                if (sortCol === 'tier') {
                    va = TIER_ORDER[a.tier] ?? 9;
                    vb = TIER_ORDER[b.tier] ?? 9;
                } else {
                    va = (a[sortCol] ?? '');
                    vb = (b[sortCol] ?? '');
                    if (typeof va === 'string') { va = va.toLowerCase(); vb = vb.toLowerCase(); }
                }
                const cmp = va < vb ? -1 : va > vb ? 1 : 0;
                return sortDir === 'asc' ? cmp : -cmp;
            });
        } else {
            rows.sort((a, b) => {
                const td = (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9);
                return td !== 0 ? td : b.priority - a.priority;
            });
        }
        return rows;
    }

    function renderTableBody() {
        const tbody = document.getElementById('cli-rules-tbody');
        const countEl = document.getElementById('cli-rules-count');
        if (!tbody) return;

        const rows = getFilteredSorted();

        if (rows.length === 0) {
            tbody.innerHTML = `
                <tr><td colspan="7" class="text-center py-10 text-bm-muted text-sm">
                    ${rulesCache.length === 0
                        ? 'No rules configured. Add a rule or seed defaults to get started.'
                        : 'No rules match the current filters.'}
                </td></tr>`;
        } else {
            let html = '';
            for (const rule of rows) {
                const checked = rule.enabled !== false;
                const tierCss = TIER_CSS[rule.tier] || '';
                const tierShort = TIER_SHORT[rule.tier] || '';
                html += `
                <tr class="bm-rule-row" data-rule-id="${rule.id}">
                    <td class="bm-rule-cmd">${esc(rule.pattern)}</td>
                    <td><span class="bm-tier-label ${tierCss}">${esc(tierShort)}</span></td>
                    <td style="color:#475569;font-size:12.5px">${esc(rule.description || '')}</td>
                    <td style="color:#475569;font-size:12.5px">${esc(rule.category || '')}</td>
                    <td style="color:#475569;font-size:12.5px">${esc(rule.match_mode)}</td>
                    <td>
                        <button class="bm-toggle" role="switch" aria-checked="${checked}"
                                data-toggle-rule="${rule.id}" title="${checked ? 'Enabled' : 'Disabled'}">
                            <span class="bm-toggle-knob"></span>
                        </button>
                    </td>
                    <td>
                        <div class="bm-rule-actions">
                            <button class="bm-action-btn" data-edit-rule="${rule.id}" title="Edit">
                                <i data-lucide="pencil" class="w-3.5 h-3.5 text-bm-muted"></i>
                            </button>
                            <button class="bm-action-btn bm-delete" data-delete-rule="${rule.id}" title="Delete">
                                <i data-lucide="trash-2" class="w-3.5 h-3.5 text-red-400"></i>
                            </button>
                        </div>
                    </td>
                </tr>`;
            }
            tbody.innerHTML = html;
        }

        if (countEl) {
            const total = rulesCache.length;
            const shown = rows.length;
            countEl.textContent = shown === total
                ? `${total} rule${total !== 1 ? 's' : ''}`
                : `${shown} of ${total} rules`;
        }

        icons(tbody);
    }

    function handleSort(col) {
        if (sortCol === col) {
            if (sortDir === 'asc') { sortDir = 'desc'; }
            else { sortCol = null; sortDir = 'asc'; }
        } else {
            sortCol = col;
            sortDir = 'asc';
        }
        // Update header indicators
        document.querySelectorAll('.bm-th').forEach(th => {
            const icon = th.querySelector('.bm-sort-icon');
            if (!icon) return;
            if (th.dataset.sort === sortCol) {
                th.classList.add('bm-sorted');
                icon.textContent = sortDir === 'asc' ? '\u25B4' : '\u25BE';
            } else {
                th.classList.remove('bm-sorted');
                icon.textContent = '\u25BE';
            }
        });
        renderTableBody();
    }

    async function renderRulesTab(el) {
        _rulesTabEl = el;
        try {
            const res = await apiFetch('/api/cli-policy/rules');
            rulesCache = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load rules.</p>';
            return;
        }

        const tierChip = (value, label) => {
            const active = filterTier === value;
            return `<button type="button" data-tier-filter="${value || ''}"
                            class="bm-tier-chip ${active ? 'active' : ''}">${label}</button>`;
        };

        el.innerHTML = `
            <div class="flex items-center justify-between mb-4 flex-wrap gap-3">
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
                <div class="flex items-center gap-2">
                    ${tierChip(null, 'All')}
                    ${tierChip('never_allowed', 'Never Allowed')}
                    ${tierChip('approval_required', 'Approval Required')}
                    ${tierChip('always_allowed', 'Always Allowed')}
                </div>
                <div class="flex items-center gap-3">
                    <div class="relative">
                        <i data-lucide="search" class="w-3.5 h-3.5 text-bm-muted absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"></i>
                        <input id="cli-rules-search" type="text" placeholder="Search rules..."
                               value="${esc(searchText)}"
                               class="pl-8 pr-3 py-1.5 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text w-52
                                      focus:outline-none focus:border-bm-accent focus:ring-1 focus:ring-bm-accent/30">
                    </div>
                    <span id="cli-rules-count" class="text-xs text-bm-muted whitespace-nowrap"></span>
                </div>
            </div>
            <div id="cli-rule-form-slot"></div>
            <div class="bm-rules-table-wrap">
                <table class="w-full text-sm">
                    <thead>
                        <tr>
                            <th class="bm-th" data-sort="pattern" style="width:16%">
                                Command <span class="bm-sort-icon">\u25BE</span>
                            </th>
                            <th class="bm-th" data-sort="tier" style="width:11%">
                                Tier <span class="bm-sort-icon">\u25BE</span>
                            </th>
                            <th class="bm-th" data-sort="description" style="width:35%">
                                Description <span class="bm-sort-icon">\u25BE</span>
                            </th>
                            <th class="bm-th" data-sort="category" style="width:11%">
                                Category <span class="bm-sort-icon">\u25BE</span>
                            </th>
                            <th class="bm-th" data-sort="match_mode" style="width:7%">
                                Mode <span class="bm-sort-icon">\u25BE</span>
                            </th>
                            <th class="bm-th-nosort" style="width:70px">Enabled</th>
                            <th class="bm-th-nosort" style="width:70px"></th>
                        </tr>
                    </thead>
                    <tbody id="cli-rules-tbody"></tbody>
                </table>
            </div>`;

        icons(el);
        renderTableBody();

        // ── Sort headers ──
        el.querySelectorAll('.bm-th[data-sort]').forEach(th => {
            th.addEventListener('click', () => handleSort(th.dataset.sort));
        });

        // ── Tier filter chips ──
        el.querySelectorAll('[data-tier-filter]').forEach(btn => {
            btn.addEventListener('click', () => {
                const val = btn.dataset.tierFilter;
                filterTier = val || null;
                el.querySelectorAll('.bm-tier-chip').forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                renderTableBody();
            });
        });

        // ── Search ──
        document.getElementById('cli-rules-search').addEventListener('input', (e) => {
            searchText = e.target.value;
            renderTableBody();
        });

        // ── Delegated click handlers on tbody ──
        document.getElementById('cli-rules-tbody').addEventListener('click', async (e) => {
            const toggle = e.target.closest('[data-toggle-rule]');
            const edit = e.target.closest('[data-edit-rule]');
            const del = e.target.closest('[data-delete-rule]');

            if (toggle) {
                const rule = rulesCache.find(r => r.id === toggle.dataset.toggleRule);
                if (!rule) return;
                try {
                    await apiFetchOk(`/api/cli-policy/rules/${rule.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled: !rule.enabled }),
                    });
                    rule.enabled = !rule.enabled;
                    renderTableBody();
                } catch (err) {
                    alert(err.message || 'Failed to update rule.');
                }
            }

            if (edit) {
                const rule = rulesCache.find(r => r.id === edit.dataset.editRule);
                if (rule) showRuleForm(rule);
            }

            if (del) {
                if (!confirm('Delete this rule?')) return;
                try {
                    await apiFetchOk(`/api/cli-policy/rules/${del.dataset.deleteRule}`, { method: 'DELETE' });
                    rulesCache = rulesCache.filter(r => r.id !== del.dataset.deleteRule);
                    renderTableBody();
                } catch (err) {
                    alert(err.message || 'Failed to delete rule.');
                }
            }
        });

        // ── Add rule ──
        document.getElementById('btn-add-rule').addEventListener('click', () => showRuleForm(null));

        // ── Seed defaults ──
        document.getElementById('btn-seed-defaults').addEventListener('click', async () => {
            if (!confirm('This will delete ALL existing rules and replace them with the defaults. Continue?')) return;
            try {
                await apiFetchOk('/api/cli-policy/rules/seed-defaults', { method: 'POST' });
                renderRulesTab(el);
            } catch (err) {
                alert(err.message || 'Failed to seed defaults.');
            }
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
                    await apiFetchOk(`/api/cli-policy/rules/${rule.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await apiFetchOk('/api/cli-policy/rules', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                const tabContent = document.getElementById('cli-tab-content');
                if (tabContent) renderRulesTab(tabContent);
            } catch (err) {
                alert(err.message || 'Failed to save rule.');
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
        workspace_host_roots: {
            order: 60,
            label: 'Host workspace roots',
            description: 'Optional extra directories a named absolute path may open, read, or edit. One absolute directory per line. Empty means no extra host access — Company Files stays artifacts/projects. This is not a full host mount. / , /etc, /proc, /sys, /dev, and /root are rejected.',
            type: 'textarea',
        },
    };

    async function renderSettingsTab(el) {
        let settings = [];
        try {
            const res = await apiFetch('/api/settings?category=cli_policy');
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
            } else if (meta.type === 'textarea') {
                html += `
                    <label class="block text-sm font-semibold mb-1">${esc(meta.label)}</label>
                    <p class="text-xs text-bm-muted mb-2">${esc(meta.description)}</p>
                    <textarea data-cli-setting-input="${s.key}" rows="4"
                              class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono"
                              placeholder="/home/you/src">${esc(s.value || '')}</textarea>`;
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
                    await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(newVal)}&category=cli_policy`, {
                        method: 'PUT',
                    });
                    applySettingSaveResult(card, true, '');
                    renderSettingsTab(el);
                } catch (err) {
                    applySettingSaveResult(card, false, err.message || 'Save failed');
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
                    await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=cli_policy`, {
                        method: 'PUT',
                    });
                    applySettingSaveResult(card, true, '');
                } catch (err) {
                    applySettingSaveResult(card, false, err.message || 'Save failed');
                }
            });
        });
    }


    // ═══════════════════════════════════════════════════════════════
    //  VIRTUAL COMMANDS TAB
    // ═══════════════════════════════════════════════════════════════

    async function renderVirtualCommandsTab(el) {
        let data = { commands: [], categories: [] };
        try {
            const res = await apiFetch('/api/cli-policy/virtual-commands');
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
            const res = await apiFetch('/api/cli-policy/approvals?limit=50');
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
                    await apiFetchOk(`/api/cli-policy/approvals/${btn.dataset.approve}/approve`, { method: 'POST' });
                    renderApprovalsTab(el);
                    refreshApprovalBadge();
                } catch (err) {
                    alert(err.message || 'Failed to approve.');
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
                    await apiFetchOk(`/api/cli-policy/approvals/${reqId}/reject`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ decision_note: note || null }),
                    });
                    renderApprovalsTab(el);
                    refreshApprovalBadge();
                } catch (err) {
                    alert(err.message || 'Failed to reject.');
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
