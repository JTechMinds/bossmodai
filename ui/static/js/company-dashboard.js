/**
 * BossMod AI — Company Dashboard controller.
 *
 * Renders Files / Tasks / Metrics / Org into dock window bodies.
 */

const CompanyDashboard = (() => {
    let activeTab = 'files';

    const TAB_MODULES = {
        files: () => typeof CompanyFiles !== 'undefined' ? CompanyFiles : null,
        tasks: () => typeof CompanyTasks !== 'undefined' ? CompanyTasks : null,
        metrics: () => typeof CompanyMetrics !== 'undefined' ? CompanyMetrics : null,
        org: () => typeof CompanyOrg !== 'undefined' ? CompanyOrg : null,
    };

    function getContentEl(tab) {
        const id = tab || activeTab;
        return document.getElementById(`dock-${id}-body`);
    }

    function switchTab(tab) {
        if (!TAB_MODULES[tab]) return;
        activeTab = tab;

        const contentEl = getContentEl(tab);
        if (!contentEl) return;
        contentEl.innerHTML = '';
        contentEl.dataset.rendered = tab;

        const mod = TAB_MODULES[tab]();
        if (mod && typeof mod.render === 'function') {
            mod.render(contentEl);
        } else {
            contentEl.innerHTML = `<div class="p-6 text-bm-muted text-sm">Unable to load ${BossModUtils.escapeHtml(tab)} tab.</div>`;
        }

        if (window.lucide) lucide.createIcons({ nodes: [contentEl] });
    }

    function unmount(tab) {
        const contentEl = getContentEl(tab);
        const mod = TAB_MODULES[tab] ? TAB_MODULES[tab]() : null;
        if (mod && typeof mod.destroy === 'function') {
            mod.destroy();
        }
        if (contentEl) {
            contentEl.innerHTML = '';
            delete contentEl.dataset.rendered;
        }
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

    return { switchTab, unmount, handleWorldUpdate, getActiveTab, getContentEl };
})();
