/**
 * BossMod AI — Main application controller.
 *
 * Handles: tab switching, Split.js panel resizing, agent panel overlay,
 * mobile bottom sheet, WebSocket connection, localStorage preferences.
 */

const BossModApp = (() => {
    // ─── State ───
    const STORAGE_KEY = 'bossmod_prefs';
    let prefs = loadPrefs();
    let ws = null;
    let wsReconnectTimer = null;
    const WS_RECONNECT_DELAY = 3000;

    // ─── Preferences ───

    function loadPrefs() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored) return JSON.parse(stored);
        } catch { /* ignore corrupt data */ }
        return { activeTab: 'chat', splitSizes: [30, 70] };
    }

    function savePrefs() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
        } catch { /* storage full or unavailable */ }
    }

    // ─── Tab switching ───

    function initTabs() {
        // Desktop tabs
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab, 'desktop'));
        });

        // Mobile tabs
        document.querySelectorAll('.mobile-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab, 'mobile'));
        });

        // Restore saved tab
        switchTab(prefs.activeTab, 'both');
    }

    function switchTab(tabName, scope) {
        prefs.activeTab = tabName;
        savePrefs();

        if (scope === 'desktop' || scope === 'both') {
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === tabName);
            });
            document.querySelectorAll('.tab-content').forEach(panel => {
                panel.classList.toggle('active', panel.id === `tab-${tabName}`);
            });
        }

        if (scope === 'mobile' || scope === 'both') {
            document.querySelectorAll('.mobile-tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === tabName);
            });
        }
    }

    // ─── Split.js (resizable panels) ───

    function initSplit() {
        // Only on desktop (sm breakpoint = 640px)
        if (window.innerWidth < 640) return;

        const leftPanel = document.getElementById('panel-left');
        const mapPanel = document.getElementById('panel-map');

        if (!leftPanel || !mapPanel) return;

        Split(['#panel-left', '#panel-map'], {
            sizes: prefs.splitSizes,
            minSize: [200, 400],
            gutterSize: 6,
            cursor: 'col-resize',
            onDragEnd: (sizes) => {
                prefs.splitSizes = sizes;
                savePrefs();
                // Notify canvas to resize
                window.dispatchEvent(new Event('panel-resize'));
            },
        });
    }

    // ─── Agent panel overlay ───

    function initAgentPanel() {
        const overlay = document.getElementById('agent-panel-overlay');
        const backdrop = document.getElementById('agent-panel-backdrop');
        const closeBtn = document.getElementById('agent-panel-close');

        if (!overlay) return;

        // Close on backdrop click
        backdrop.addEventListener('click', closeAgentPanel);

        // Close on button click
        closeBtn.addEventListener('click', closeAgentPanel);

        // Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && overlay.classList.contains('open')) {
                closeAgentPanel();
            }
        });
    }

    function openAgentPanel(agentData) {
        BossModUtils.openOverlay('agent-panel-overlay');

        if (agentData && agentData.id) {
            AgentPanel.openForAgentId(agentData.id);
        } else {
            AgentPanel.openForCreate();
        }
    }

    function closeAgentPanel() {
        BossModUtils.closeOverlay('agent-panel-overlay', 'agent-panel');
    }

    // ─── Mobile bottom sheet ───

    function initMobileSheet() {
        const sheet = document.getElementById('mobile-sheet');
        const handle = document.getElementById('sheet-handle');

        if (!sheet || !handle) return;

        let startY = 0;
        let expanded = false;

        handle.addEventListener('touchstart', (e) => {
            startY = e.touches[0].clientY;
        }, { passive: true });

        handle.addEventListener('touchend', (e) => {
            const endY = e.changedTouches[0].clientY;
            const diff = startY - endY;

            if (diff > 50 && !expanded) {
                // Swipe up — expand
                sheet.classList.add('expanded');
                sheet.classList.remove('peek');
                expanded = true;
            } else if (diff < -50 && expanded) {
                // Swipe down — collapse
                sheet.classList.remove('expanded');
                sheet.classList.add('peek');
                expanded = false;
            }
        }, { passive: true });

        // Also toggle on click
        handle.addEventListener('click', () => {
            expanded = !expanded;
            sheet.classList.toggle('expanded', expanded);
            sheet.classList.toggle('peek', !expanded);
        });
    }

    // ─── WebSocket connection ───

    function initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log('[BossMod] WebSocket connected');
            clearTimeout(wsReconnectTimer);
        };

        ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                handleWSMessage(msg);
            } catch (err) {
                console.error('[BossMod] Failed to parse WS message:', err);
            }
        };

        ws.onclose = () => {
            console.log('[BossMod] WebSocket disconnected, reconnecting...');
            wsReconnectTimer = setTimeout(initWebSocket, WS_RECONNECT_DELAY);
        };

        ws.onerror = (err) => {
            console.error('[BossMod] WebSocket error:', err);
            ws.close();
        };

        window.addEventListener('beforeunload', () => {
            if (ws) ws.close();
        });
    }

    function handleWSMessage(msg) {
        switch (msg.type) {
            case 'world_update': {
                const agents = msg.data.map(BossModUtils.normalizeAgent);
                if (typeof OfficeCanvas !== 'undefined') {
                    OfficeCanvas.updateAgents(agents);
                }
                break;
            }

            case 'activity':
                if (typeof ActivityLog !== 'undefined') {
                    ActivityLog.addEntry(msg.data);
                }
                break;

            case 'activity_log':
                if (typeof ActivityLog !== 'undefined') {
                    ActivityLog.loadHistory(msg.data);
                }
                break;
        }
    }

    // ─── Lucide icons ───

    function initIcons() {
        if (window.lucide) {
            lucide.createIcons();
        }
    }

    // ─── Window resize handler ───

    function initResize() {
        let resizeTimeout;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                window.dispatchEvent(new Event('panel-resize'));
            }, 150);
        });
    }

    // ─── Public API ───

    function initNavButtons() {
        const settingsBtn = document.getElementById('btn-settings');
        if (settingsBtn) {
            settingsBtn.addEventListener('click', () => {
                if (typeof SettingsPanel !== 'undefined') {
                    SettingsPanel.open();
                }
            });
        }

        const newAgentBtn = document.getElementById('btn-new-agent');
        if (newAgentBtn) {
            newAgentBtn.addEventListener('click', () => openAgentPanel({}));
        }
    }

    function init() {
        initIcons();
        initTabs();
        initSplit();
        initAgentPanel();
        initNavButtons();
        initMobileSheet();
        initResize();
        initWebSocket();

        console.log('[BossMod] App initialized');
    }

    return {
        init,
        openAgentPanel,
        closeAgentPanel,
        switchTab,
    };
})();

// Boot on DOM ready
document.addEventListener('DOMContentLoaded', BossModApp.init);
