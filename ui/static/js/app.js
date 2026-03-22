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

    // ─── Tab switching (mobile only — desktop tabs managed by AgentContext) ───

    function initTabs() {
        document.querySelectorAll('.mobile-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.mobile-tab-btn').forEach(b =>
                    b.classList.toggle('active', b.dataset.tab === btn.dataset.tab)
                );
            });
        });
    }

    function switchTab() { /* managed by AgentContext */ }

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

    // ─── Agent selection (via canvas click) ───

    function selectAgent(agentData) {
        if (typeof AgentContext !== 'undefined') {
            AgentContext.selectAgent(agentData);
        }
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
                if (typeof OfficeCanvas !== 'undefined') {
                    OfficeCanvas.handleActivity(msg.data);
                }
                break;

            case 'activity_log':
                if (typeof ActivityLog !== 'undefined') {
                    ActivityLog.loadHistory(msg.data);
                }
                break;

            case 'chat_message':
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.handleChatMessage(msg.data);
                }
                break;

            case 'chat_reset':
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.handleChatReset(msg.data);
                }
                break;

            case 'diagnostic':
                if (typeof DiagnosticsView !== 'undefined') {
                    DiagnosticsView.addEntry(msg.data);
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
        const backBtn = document.getElementById('btn-back-to-office');
        const newAgentBtn = document.getElementById('btn-new-agent');

        if (settingsBtn) {
            settingsBtn.addEventListener('click', () => {
                if (typeof SettingsView !== 'undefined') {
                    SettingsView.open();
                    updateNavForSettings(true);
                }
            });
        }

        if (backBtn) {
            backBtn.addEventListener('click', () => {
                if (typeof SettingsView !== 'undefined') {
                    SettingsView.close();
                    updateNavForSettings(false);
                    // Re-render canvas after returning
                    if (typeof OfficeCanvas !== 'undefined') {
                        window.dispatchEvent(new Event('panel-resize'));
                    }
                }
            });
        }

        if (newAgentBtn) {
            newAgentBtn.addEventListener('click', () => {
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.startCreateAgent();
                }
            });
        }
    }

    function updateNavForSettings(inSettings) {
        const backBtn = document.getElementById('btn-back-to-office');
        const settingsBtn = document.getElementById('btn-settings');
        const newAgentBtn = document.getElementById('btn-new-agent');

        if (inSettings) {
            backBtn.classList.remove('hidden');
            backBtn.classList.add('flex');
            settingsBtn.classList.add('hidden');
            newAgentBtn.classList.add('hidden');
        } else {
            backBtn.classList.add('hidden');
            backBtn.classList.remove('flex');
            settingsBtn.classList.remove('hidden');
            newAgentBtn.classList.remove('hidden');
            newAgentBtn.classList.add('sm:flex');
        }

        if (window.lucide) lucide.createIcons();
    }

    function init() {
        initIcons();
        initTabs();
        initSplit();
        initNavButtons();
        initMobileSheet();
        initResize();
        initWebSocket();

        console.log('[BossMod] App initialized');
    }

    return {
        init,
        selectAgent,
        switchTab,
        updateNavForSettings,
    };
})();

// Boot on DOM ready
document.addEventListener('DOMContentLoaded', BossModApp.init);
