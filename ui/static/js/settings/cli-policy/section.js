/**
 * BossMod AI — CLI Policy section.
 *
 * The router, not a container. It owns three things and nothing else: the
 * shell around the tabs, the tab bar, and the pending-approval badge on it.
 * Every tab is rendered by its own module in this directory and reached
 * through `TABS` below, so adding a tab is a new entry rather than an edit to
 * a switch, and no tab's markup lives here.
 *
 * The registry is also where the tabs are wired to each other: the Rules tab
 * is told how the rule form opens, the Settings tab is handed the pending
 * focus key, and Approvals is handed the badge refresh. None of them reaches
 * for another module.
 */

const CliPolicySection = (() => {
    let container = null;
    let activeTab = 'rules';

    // Shared badge / roster helpers live in cli-policy/shared.js — several
    // tabs use them and they get exactly one home.
    const { icons, fetchAgents } = BossModCliPolicyShared;

    let pendingFocusKey = null;

    /**
     * Open the rule form, and re-read the Rules tab once it saves.
     *
     * @param {object|null} rule  The rule to edit, or null for a new rule.
     * @returns {void}
     */
    function openRuleForm(rule) {
        BossModCliPolicyRuleForm.showRuleForm(rule, {
            onSaved: () => {
                const tabContent = document.getElementById('cli-tab-content');
                if (tabContent) renderTab('rules', tabContent);
            },
        });
    }

    /**
     * Hand the pending focus key to the Settings tab, once.
     *
     * @returns {string|null} The key the section was opened with, cleared so a
     *   later render of the same tab does not scroll the operator again.
     */
    function takeFocusKey() {
        const key = pendingFocusKey;
        pendingFocusKey = null;
        return key;
    }

    /** The tab bar and the dispatch table, in one list. */
    const TABS = [
        {
            id: 'rules', label: 'Rules', icon: 'list',
            render: (content) => BossModCliPolicyRules.renderRulesTab(content, { onEditRule: openRuleForm }),
        },
        {
            id: 'virtual-commands', label: 'Virtual Commands', icon: 'cpu',
            render: (content) => BossModCliPolicyVirtual.renderVirtualCommandsTab(content),
        },
        {
            id: 'settings', label: 'Settings', icon: 'settings',
            render: (content) => BossModCliPolicySettings.renderSettingsTab(content, { takeFocusKey }),
        },
        {
            id: 'simulator', label: 'Simulator', icon: 'terminal',
            render: (content) => CliPolicySimulator.render(content),
        },
        {
            id: 'approvals', label: 'Approvals', icon: 'shield-check',
            render: (content) => BossModCliPolicyApprovals.renderApprovalsTab(content, {
                onResolved: refreshApprovalBadge,
            }),
        },
    ];

    /**
     * Render one tab into the content element.
     *
     * @param {string} tabId
     * @param {Element} content
     * @returns {void} Nothing is rendered for an id the registry does not
     *   carry — the behaviour of the switch this replaced.
     */
    function renderTab(tabId, content) {
        const tab = TABS.find(entry => entry.id === tabId);
        if (tab) tab.render(content);
    }

    /**
     * Open the CLI Policy section.
     *
     * @param {Element} el  The settings content element.
     * @param {object} [options]
     * @param {string} [options.tab]  Tab id to open on.
     * @param {string} [options.focusKey]  A `cli_policy` setting key to scroll
     *   to and ring once the Settings tab renders (Company Files deep-links to
     *   `workspace_host_roots` this way).
     * @returns {Promise<void>}
     */
    async function render(el, options) {
        container = el;
        if (options && typeof options.tab === 'string') {
            activeTab = options.tab;
        }
        pendingFocusKey = options && typeof options.focusKey === 'string' ? options.focusKey : null;
        await fetchAgents();
        renderShell();
    }

    function renderShell() {
        let html = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">CLI Policy</h2>
                <p class="text-sm text-bm-muted mt-0.5">Manage command execution rules, test policies, and review approval requests.</p>
            </div>
            <div class="mb-5 flex flex-wrap gap-2">`;

        for (const tab of TABS) {
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
        renderTab(activeTab, content);

        refreshApprovalBadge();
    }

    /**
     * Re-read the pending-approval count onto the Approvals tab button.
     *
     * @returns {Promise<void>} Silent on failure: a badge that cannot be read
     *   stays as it is rather than claiming zero.
     */
    async function refreshApprovalBadge() {
        try {
            const res = await apiFetch('/api/cli-policy/approvals?status=pending&limit=200');
            // The docstring above promised the badge stays as it is on a
            // failure; without this check a 4xx body parsed as the list, its
            // `.length` read undefined, and the badge was HIDDEN — pending
            // approvals disappearing from the nav is the opposite of leaving
            // it alone.
            if (!res.ok) return;
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


    return { render };
})();
