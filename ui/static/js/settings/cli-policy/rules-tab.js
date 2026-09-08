/**
 * BossMod AI — CLI Policy Rules tab.
 *
 * The tab around the table: it loads the rule list, paints the toolbar and the
 * table chrome, and wires every control. Ported from cli-policy-section.js
 * unchanged.
 *
 * It owns `rulesCache` because it is the only module that changes it — the
 * load, the enable toggle and the delete all write here, and `rules-table.js`
 * is handed the array to paint. How the rule form opens is injected as
 * `onEditRule`, so the tab never names the form module; that is the seam the
 * router wires.
 */
const BossModCliPolicyRules = (() => {
    const esc = BossModFormat.escapeHtml;
    const escAttr = BossModFormat.escapeAttribute;
    const { icons } = BossModCliPolicyShared;
    const table = BossModCliPolicyRulesTable;

    let rulesCache = [];

    /**
     * Render the Rules tab into a container and bind its controls.
     *
     * @param {Element} el  The tab content element.
     * @param {object} options
     * @param {(rule: object|null) => void} options.onEditRule  Open the rule
     *   form; `null` means a new rule. Injected so the table does not know how
     *   the form opens.
     * @returns {Promise<void>} Resolves once the list has loaded and rendered.
     *   A failed load leaves an error line in `el` and binds nothing.
     */
    async function renderRulesTab(el, { onEditRule }) {
        try {
            const res = await apiFetch('/api/cli-policy/rules');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            rulesCache = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load rules.</p>';
            return;
        }

        const tierChip = (value, label) => {
            const active = table.getTierFilter() === value;
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
                               value="${escAttr(table.getSearchText())}"
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
        table.renderTableBody(rulesCache);

        // ── Sort headers ──
        el.querySelectorAll('.bm-th[data-sort]').forEach(th => {
            th.addEventListener('click', () => table.handleSort(th.dataset.sort, rulesCache));
        });

        // ── Tier filter chips ──
        el.querySelectorAll('[data-tier-filter]').forEach(btn => {
            btn.addEventListener('click', () => {
                const val = btn.dataset.tierFilter;
                table.setTierFilter(val || null);
                el.querySelectorAll('.bm-tier-chip').forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                table.renderTableBody(rulesCache);
            });
        });

        // ── Search ──
        document.getElementById('cli-rules-search').addEventListener('input', (e) => {
            table.setSearch(e.target.value);
            table.renderTableBody(rulesCache);
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
                    table.renderTableBody(rulesCache);
                } catch (err) {
                    alert(err.message || 'Failed to update rule.');
                }
            }

            if (edit) {
                const rule = rulesCache.find(r => r.id === edit.dataset.editRule);
                if (rule) onEditRule(rule);
            }

            if (del) {
                if (!confirm('Delete this rule?')) return;
                try {
                    await apiFetchOk(`/api/cli-policy/rules/${del.dataset.deleteRule}`, { method: 'DELETE' });
                    rulesCache = rulesCache.filter(r => r.id !== del.dataset.deleteRule);
                    table.renderTableBody(rulesCache);
                } catch (err) {
                    alert(err.message || 'Failed to delete rule.');
                }
            }
        });

        // ── Add rule ──
        document.getElementById('btn-add-rule').addEventListener('click', () => onEditRule(null));

        // ── Seed defaults ──
        document.getElementById('btn-seed-defaults').addEventListener('click', async () => {
            if (!confirm('This will delete ALL existing rules and replace them with the defaults. Continue?')) return;
            try {
                await apiFetchOk('/api/cli-policy/rules/seed-defaults', { method: 'POST' });
                renderRulesTab(el, { onEditRule });
            } catch (err) {
                alert(err.message || 'Failed to seed defaults.');
            }
        });
    }

    return { renderRulesTab };
})();
