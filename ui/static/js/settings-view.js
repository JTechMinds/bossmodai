/**
 * BossMod AI — Full-screen settings shell (HA-STRUCT-P1-04).
 *
 * Left nav + right content. Section IIFEs live in sibling files and
 * expose `.render(container)` on window-level consts.
 */
const SettingsView = (() => {
    let activeSection = 'connections';
    let isOpen = false;
    let sectionOptions = null;

    const NAV_ITEMS = [
        { id: 'connections',   label: 'AI Connections',  icon: 'plug' },
        { id: 'personalities', label: 'AI Personalities', icon: 'brain' },
        { id: 'system',       label: 'System Settings',  icon: 'sliders' },
        { id: 'cli-policy',   label: 'CLI Policy',       icon: 'terminal' },
        { id: 'telegram',     label: 'Telegram',         icon: 'send' },
    ];

    const ADVANCED_ITEMS = [
        { id: 'advanced-system', label: 'Advanced System Settings', icon: 'shield' },
        { id: 'prompt-template', label: 'System Prompt Template', icon: 'file-code' },
        { id: 'runtime-contracts', label: 'Runtime Contracts', icon: 'braces' },
    ];

    // ─── Open / Close ───

    function open(sectionId, options) {
        if (typeof sectionId === 'string' && sectionId) {
            activeSection = sectionId;
        }
        sectionOptions = options && typeof options === 'object' ? options : null;
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
        if (typeof BossModApp !== 'undefined' && typeof BossModApp.refreshModelAvailability === 'function') {
            void BossModApp.refreshModelAvailability();
        }
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
        const pendingOptions = sectionOptions;
        sectionOptions = null;
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
            case 'cli-policy':
                CliPolicySection.render(content, pendingOptions);
                break;
            case 'telegram':
                TelegramSection.render(content);
                break;
            case 'advanced-system':
                AdvancedSystemSection.render(content);
                break;
            case 'prompt-template':
                PromptTemplateSection.render(content);
                break;
            case 'runtime-contracts':
                RuntimeContractsSection.render(content);
                break;
        }
    }

    return { open, close, isOpen: () => isOpen };
})();
