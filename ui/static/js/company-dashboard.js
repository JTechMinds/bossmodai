/**
 * BossMod AI — Company Dashboard controller.
 *
 * Manages sub-tab switching within the company dashboard view.
 * Delegates rendering to individual tab modules.
 */

const CompanyDashboard = (() => {
    let activeTab = 'files';
    let initialized = false;

    const TAB_MODULES = {
        files: () => typeof CompanyFiles !== 'undefined' ? CompanyFiles : null,
        tasks: () => typeof CompanyTasks !== 'undefined' ? CompanyTasks : null,
        metrics: () => typeof CompanyMetrics !== 'undefined' ? CompanyMetrics : null,
        org: () => typeof CompanyOrg !== 'undefined' ? CompanyOrg : null,
    };

    function getContentEl() {
        return document.getElementById('company-dashboard-content');
    }

    function updateTabBar() {
        document.querySelectorAll('.company-tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === activeTab);
        });
    }

    function bindTabClicks() {
        if (initialized) return;
        document.querySelectorAll('.company-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                if (typeof BossModApp !== 'undefined') {
                    BossModApp.switchCompanyTab(btn.dataset.tab);
                }
            });
        });
        initialized = true;
    }

    function switchTab(tab) {
        if (!TAB_MODULES[tab]) return;
        activeTab = tab;
        updateTabBar();
        bindTabClicks();

        const contentEl = getContentEl();
        if (!contentEl) return;
        contentEl.innerHTML = '';

        const mod = TAB_MODULES[tab]();
        if (mod && typeof mod.render === 'function') {
            mod.render(contentEl);
        } else {
            contentEl.innerHTML = `<div class="p-6 text-bm-muted text-sm">Unable to load ${BossModUtils.escapeHtml(tab)} tab.</div>`;
        }

        if (window.lucide) lucide.createIcons({ nodes: [contentEl] });
    }

    function handleWorldUpdate(agents) {
        const orgMod = TAB_MODULES.org();
        if (orgMod && typeof orgMod.handleWorldUpdate === 'function') {
            orgMod.handleWorldUpdate(agents);
        }
    }

    function getActiveTab() {
        return activeTab;
    }

    return { switchTab, handleWorldUpdate, getActiveTab };
})();
