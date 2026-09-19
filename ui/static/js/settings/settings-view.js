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
        { id: 'nest-git',     label: 'Nest git',         icon: 'git-branch' },
        { id: 'telegram',     label: 'Telegram',         icon: 'send' },
    ];

    const ADVANCED_ITEMS = [
        { id: 'advanced-system', label: 'Advanced System Settings', icon: 'shield' },
        { id: 'prompt-template', label: 'System Prompt Template', icon: 'file-code' },
        { id: 'runtime-contracts', label: 'Runtime Contracts', icon: 'braces' },
    ];

    // ─── Open / Close ───

    const viewListeners = [];

    /**
     * Subscribe to the takeover opening and closing.
     *
     * The header's gear announces the state it toggles, and it cannot learn it
     * from the click: two other modules open a section directly. Notifying
     * from `setView` is what makes the answer true for every door — the gear,
     * Escape, the bar's exit, and both direct openers — rather than for the
     * one that happens to run.
     *
     * @param {(open: boolean) => void} fn
     * @returns {() => void} disposer
     */
    function onViewChange(fn) {
        viewListeners.push(fn);
        return () => {
            const at = viewListeners.indexOf(fn);
            if (at !== -1) viewListeners.splice(at, 1);
        };
    }

    /**
     * Which frame is on screen is one fact with one owner: `data-view` on <body>.
     * shell.css reads it; nothing in JS touches `display` or the panels' classes.
     *
     * @param {'app'|'settings'} view
     */
    function setView(view) {
        document.body.dataset.view = view;
        const open = view === 'settings';
        for (const listener of viewListeners) listener(open);
    }

    function open(sectionId, options) {
        if (typeof sectionId === 'string' && sectionId) {
            activeSection = sectionId;
        }
        sectionOptions = options && typeof options === 'object' ? options : null;
        setView('settings');
        isOpen = true;

        renderNav();
        switchSection(activeSection);
    }

    function close() {
        setView('app');
        isOpen = false;
        // Model availability moved from app.js to shell/banners.js; connecting
        // or removing a model here is the one change the banner cannot learn
        // about from the WebSocket.
        void BossModBanners.refreshModelAvailability();
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

        BossModIcons.paint(nav, 'settings-view.renderNav');
    }

    function navButton(item) {
        const active = activeSection === item.id;
        return `<button data-section="${BossModFormat.escapeAttribute(item.id)}"
                    class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                           transition-colors text-left
                           ${active ? 'bg-bm-accent/10 text-bm-accent' : 'text-bm-text hover:bg-slate-100'}">
                <i data-lucide="${BossModFormat.escapeAttribute(item.icon)}" class="w-4 h-4 shrink-0"></i>
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
            case 'nest-git':
                NestGitSection.render(content, pendingOptions);
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

    return { open, close, isOpen: () => isOpen, onViewChange };
})();
