/**
 * BossMod AI — CLI Policy rules table.
 *
 * The row view of the rule list: which rules are shown, in what order, and the
 * markup for the `<tbody>` and the row count. Ported from
 * cli-policy-section.js unchanged.
 *
 * The rule list itself belongs to `rules-tab.js`, which loads it and mutates it
 * on toggle and delete; this module is handed the array on every paint rather
 * than holding its own copy, so there is no second cache to fall out of date.
 * What it does own is the *view* state — the sort column and direction, the
 * tier filter, and the search text — because that is what the table is, and the
 * toolbar sets it through the setters below rather than by sharing a closure.
 */
const BossModCliPolicyRulesTable = (() => {
    const esc = BossModFormat.escapeHtml;
    const escAttr = BossModFormat.escapeAttribute;
    const { icons, agentName } = BossModCliPolicyShared;

    const TIER_ORDER  = { never_allowed: 0, approval_required: 1, always_allowed: 2 };
    const TIER_SHORT  = { never_allowed: 'Never', approval_required: 'Approval', always_allowed: 'Allowed' };
    const TIER_CSS    = { never_allowed: 'tier-never', approval_required: 'tier-approval', always_allowed: 'tier-allowed' };

    let sortCol = null;
    let sortDir = 'asc';
    let filterTier = null;
    let searchText = '';

    /**
     * Apply the current filter, search and sort to a rule list.
     *
     * @param {object[]} rules  The full rule list.
     * @returns {object[]} A new array; the input is never reordered in place.
     */
    function getFilteredSorted(rules) {
        let rows = [...rules];
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

    /**
     * Repaint `#cli-rules-tbody` and the `#cli-rules-count` line.
     *
     * @param {object[]} rules  The full rule list; the empty-state copy
     *   distinguishes "no rules at all" from "none match the filters", so it
     *   needs the unfiltered total as well as the filtered rows.
     * @returns {void} A no-op when the tab is not on screen.
     */
    function renderTableBody(rules) {
        const tbody = document.getElementById('cli-rules-tbody');
        const countEl = document.getElementById('cli-rules-count');
        if (!tbody) return;

        const rows = getFilteredSorted(rules);

        if (rows.length === 0) {
            tbody.innerHTML = `
                <tr><td colspan="7" class="text-center py-10 text-bm-muted text-sm">
                    ${rules.length === 0
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
                <tr class="bm-rule-row" data-rule-id="${escAttr(rule.id)}">
                    <td class="bm-rule-cmd">${esc(rule.pattern)}</td>
                    <td><span class="bm-tier-label ${escAttr(tierCss)}">${esc(tierShort)}</span></td>
                    <td style="color:#475569;font-size:12.5px">${esc(rule.description || '')}</td>
                    <td style="color:#475569;font-size:12.5px">${esc(rule.category || '')}</td>
                    <td style="color:#475569;font-size:12.5px">${esc(rule.match_mode)}</td>
                    <td>
                        <button class="bm-toggle" role="switch" aria-checked="${checked ? 'true' : 'false'}"
                                data-toggle-rule="${escAttr(rule.id)}" title="${checked ? 'Enabled' : 'Disabled'}">
                            <span class="bm-toggle-knob"></span>
                        </button>
                    </td>
                    <td>
                        <div class="bm-rule-actions">
                            <button class="bm-action-btn" data-edit-rule="${escAttr(rule.id)}" title="Edit">
                                <i data-lucide="pencil" class="w-3.5 h-3.5 text-bm-muted"></i>
                            </button>
                            <button class="bm-action-btn bm-delete" data-delete-rule="${escAttr(rule.id)}" title="Delete">
                                <i data-lucide="trash-2" class="w-3.5 h-3.5 text-red-400"></i>
                            </button>
                        </div>
                    </td>
                </tr>`;
            }
            tbody.innerHTML = html;
        }

        if (countEl) {
            const total = rules.length;
            const shown = rows.length;
            countEl.textContent = shown === total
                ? `${total} rule${total !== 1 ? 's' : ''}`
                : `${shown} of ${total} rules`;
        }

        icons(tbody);
    }

    /**
     * Advance one column through unsorted → ascending → descending, repaint the
     * header indicators, and repaint the body.
     *
     * @param {string} col  The `data-sort` value of the header clicked.
     * @param {object[]} rules  The full rule list, for the repaint.
     * @returns {void}
     */
    function handleSort(col, rules) {
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
        renderTableBody(rules);
    }

    /**
     * @param {string|null} value  A tier, or null for "All".
     * @returns {void}
     */
    function setTierFilter(value) {
        filterTier = value;
    }

    /** @returns {string|null} The active tier filter, for the chip's state. */
    function getTierFilter() {
        return filterTier;
    }

    /**
     * @param {string} value  Free text matched against every visible column.
     * @returns {void}
     */
    function setSearch(value) {
        searchText = value;
    }

    /** @returns {string} The live search text, for the input's value. */
    function getSearchText() {
        return searchText;
    }

    return {
        getFilteredSorted,
        renderTableBody,
        handleSort,
        setTierFilter,
        getTierFilter,
        setSearch,
        getSearchText,
    };
})();
