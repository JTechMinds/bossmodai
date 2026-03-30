/**
 * BossMod AI — Main application controller.
 *
 * Handles: tab switching, Split.js panel resizing, agent panel overlay,
 * mobile bottom sheet, WebSocket connection, localStorage preferences,
 * center panel mode switching (office / company), and footer status.
 */

const BossModApp = (() => {
    // ─── State ───
    const STORAGE_KEY = 'bossmod_prefs';
    let prefs = loadPrefs();
    let ws = null;
    let wsReconnectTimer = null;
    const WS_RECONNECT_DELAY = 3000;
    let runtimePaused = false;
    let centerMode = 'office';
    let activeCompanyTab = 'metrics';
    let runtimeStartedAt = null;
    let uptimeInterval = null;

    // ─── Preferences ───

    function loadPrefs() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored) {
                const parsed = JSON.parse(stored);
                return {
                    activeTab: parsed.activeTab || 'chat',
                    splitSizes: parsed.splitSizes || [25, 50, 25],
                    centerMode: parsed.centerMode || 'office',
                    activeCompanyTab: parsed.activeCompanyTab || 'files',
                };
            }
        } catch { /* ignore corrupt data */ }
        return { activeTab: 'chat', splitSizes: [25, 50, 25], centerMode: 'office', activeCompanyTab: 'files' };
    }

    function savePrefs() {
        try {
            prefs.centerMode = centerMode;
            prefs.activeCompanyTab = activeCompanyTab;
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
        const activityPanel = document.getElementById('panel-activity');

        if (!leftPanel || !mapPanel || !activityPanel) return;

        // Migrate old 2-panel prefs to 3-panel
        if (prefs.splitSizes.length !== 3) {
            prefs.splitSizes = [25, 50, 25];
            savePrefs();
        }

        Split(['#panel-left', '#panel-map', '#panel-activity'], {
            sizes: prefs.splitSizes,
            minSize: [200, 400, 180],
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

    // ─── Panel responsive observer ───

    function initPanelObserver() {
        const panel = document.getElementById('panel-left');
        if (!panel || typeof ResizeObserver === 'undefined') return;

        const COMPACT_THRESHOLD = 280;
        const observer = new ResizeObserver(entries => {
            for (const entry of entries) {
                const width = entry.contentRect.width;
                panel.classList.toggle('panel-compact', width < COMPACT_THRESHOLD);
            }
        });
        observer.observe(panel);
    }

    // ─── Agent selection (via canvas click) ───

    function selectAgent(agentData) {
        if (typeof AgentContext !== 'undefined') {
            AgentContext.selectAgent(agentData);
        }
    }

    // ─── Center panel mode switching ───

    function switchCenterMode(mode) {
        const canvasContainer = document.getElementById('canvas-container');
        const companyDashboard = document.getElementById('company-dashboard');
        const diagnosticPanel = document.getElementById('diagnostic-detail-panel');

        if (mode === 'office') {
            if (companyDashboard) companyDashboard.classList.add('hidden');
            if (canvasContainer) canvasContainer.classList.remove('hidden');
            requestAnimationFrame(() => window.dispatchEvent(new Event('panel-resize')));
        } else if (mode === 'company') {
            if (canvasContainer) canvasContainer.classList.add('hidden');
            if (diagnosticPanel) diagnosticPanel.classList.add('hidden');
            if (companyDashboard) companyDashboard.classList.remove('hidden');
            if (typeof CompanyDashboard !== 'undefined') {
                CompanyDashboard.switchTab(activeCompanyTab);
            }
        }

        centerMode = mode;
        updateCenterModeToggle();
        savePrefs();
    }

    function switchCompanyTab(tab) {
        activeCompanyTab = tab;
        updateCompanyToggleLabel();

        if (typeof CompanyDashboard !== 'undefined') {
            CompanyDashboard.switchTab(tab);
        }

        if (centerMode !== 'company') {
            switchCenterMode('company');
        }

        closeCompanyDropdown();
        savePrefs();
    }

    function updateCenterModeToggle() {
        document.querySelectorAll('.center-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === centerMode);
        });
        updateCompanyToggleLabel();
        updateCompanySubtabHighlight();
    }

    const TAB_DISPLAY_NAMES = { files: 'Files', tasks: 'Tasks', metrics: 'Metrics', org: 'Org Chart' };

    function updateCompanyToggleLabel() {
        const label = document.getElementById('company-toggle-label');
        if (!label) return;
        if (centerMode === 'company') {
            label.textContent = `Company: ${TAB_DISPLAY_NAMES[activeCompanyTab] || activeCompanyTab}`;
        } else {
            label.textContent = 'Company';
        }
    }

    function updateCompanySubtabHighlight() {
        document.querySelectorAll('.company-subtab-item').forEach(item => {
            item.classList.toggle('active', item.dataset.subtab === activeCompanyTab);
        });
    }

    function closeCompanyDropdown() {
        const dropdown = document.getElementById('company-subtab-dropdown');
        if (dropdown) dropdown.classList.add('hidden');
    }

    function getCenterMode() {
        return centerMode;
    }

    // ─── Footer management ───

    function updateFooterStatus(state) {
        const dot = document.getElementById('footer-status-dot');
        const label = document.getElementById('footer-status-label');
        if (!dot || !label) return;

        if (state === 'connected') {
            dot.className = 'w-1.5 h-1.5 rounded-full bg-emerald-500';
            label.textContent = 'Connected';
        } else if (state === 'paused') {
            dot.className = 'w-1.5 h-1.5 rounded-full bg-amber-500';
            label.textContent = 'Paused';
        } else if (state === 'disconnected') {
            dot.className = 'w-1.5 h-1.5 rounded-full bg-red-500';
            label.textContent = 'Disconnected';
        } else {
            dot.className = 'w-1.5 h-1.5 rounded-full bg-slate-400';
            label.textContent = 'Connecting...';
        }
    }

    function updateFooterAgentCount(count) {
        const el = document.getElementById('footer-agent-count');
        if (!el) return;
        el.textContent = `${count} agent${count !== 1 ? 's' : ''}`;
    }

    function updateFooterUptime() {
        const el = document.getElementById('footer-uptime');
        if (!el) return;

        if (!runtimeStartedAt) {
            el.textContent = '--';
            return;
        }

        const now = Date.now();
        const diffMs = now - runtimeStartedAt;
        const totalSeconds = Math.floor(diffMs / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        const pad = (n) => String(n).padStart(2, '0');

        if (hours > 0) {
            el.textContent = `${hours}h ${pad(minutes)}m`;
        } else if (minutes > 0) {
            el.textContent = `${minutes}m ${pad(seconds)}s`;
        } else {
            el.textContent = `${seconds}s`;
        }
    }

    function startUptimeInterval() {
        if (uptimeInterval) clearInterval(uptimeInterval);
        uptimeInterval = setInterval(updateFooterUptime, 1000);
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
            updateFooterStatus(runtimePaused ? 'paused' : 'connected');
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
            updateFooterStatus('disconnected');
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
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.handleWorldUpdate(agents);
                }
                if (typeof ActivityLog !== 'undefined') {
                    ActivityLog.updateAgentList(agents);
                }
                updateFooterAgentCount(agents.length);
                if (typeof CompanyDashboard !== 'undefined') {
                    CompanyDashboard.handleWorldUpdate(agents);
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
                if (msg.data && typeof CompanyTasks !== 'undefined' && typeof CompanyTasks.handleTaskEvent === 'function') {
                    CompanyTasks.handleTaskEvent(msg.data);
                }
                break;

            case 'activity_update':
                if (typeof ActivityLog !== 'undefined') {
                    ActivityLog.updateEntry(msg.data);
                }
                break;

            case 'unified_feed':
                if (typeof ActivityLog !== 'undefined') {
                    ActivityLog.loadFeed(msg.data);
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

            case 'meeting_message':
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.handleMeetingMessage(msg.data);
                }
                break;

            case 'channel_message':
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.handleChannelMessage(msg.data);
                }
                break;

            case 'channel_updated':
                if (typeof AgentContext !== 'undefined') {
                    AgentContext.handleChannelUpdated(msg.data);
                }
                break;

            case 'diagnostic':
                if (typeof DiagnosticsView !== 'undefined') {
                    DiagnosticsView.addEntry(msg.data);
                }
                break;

            case 'agent_thought':
                if (typeof OfficeCanvas !== 'undefined') {
                    OfficeCanvas.showThought(msg.data.agent_id, msg.data.thought);
                }
                break;

            case 'runtime_state':
                applyRuntimeState(msg.data);
                break;
        }
    }

    function applyRuntimeState(payload) {
        runtimePaused = !!payload?.paused;

        const button = document.getElementById('btn-runtime-kill-switch');
        const label = document.getElementById('runtime-kill-switch-label');
        const banner = document.getElementById('runtime-pause-banner');

        if (button) {
            button.disabled = false;
            button.className = runtimePaused
                ? 'flex items-center gap-2 px-3 py-1.5 border border-emerald-300 bg-emerald-50 text-emerald-700 rounded-lg hover:bg-emerald-100 transition-colors text-sm font-medium'
                : 'flex items-center gap-2 px-3 py-1.5 border border-red-300 bg-red-50 text-red-700 rounded-lg hover:bg-red-100 transition-colors text-sm font-medium';
            button.setAttribute('aria-label', runtimePaused ? 'Resume AI runtime' : 'Emergency pause AI runtime');
        }
        if (label) {
            label.textContent = runtimePaused ? 'Resume AI' : 'Emergency Pause';
        }
        if (banner) {
            banner.classList.toggle('hidden', !runtimePaused);
        }

        // Update footer status
        if (runtimePaused) {
            updateFooterStatus('paused');
        } else if (ws && ws.readyState === WebSocket.OPEN) {
            updateFooterStatus('connected');
        }

        // Extract runtime started_at for uptime tracking
        if (payload?.started_at) {
            runtimeStartedAt = new Date(payload.started_at).getTime();
        } else if (payload?.worker?.started_at) {
            runtimeStartedAt = new Date(payload.worker.started_at).getTime();
        }
        updateFooterUptime();

        if (window.lucide) {
            lucide.createIcons();
        }
    }

    async function fetchRuntimeState() {
        try {
            const res = await fetch('/api/runtime/state', { cache: 'no-store' });
            if (!res.ok) {
                throw new Error(await res.text());
            }
            applyRuntimeState(await res.json());
        } catch (err) {
            console.error('[BossMod] Failed to load runtime state:', err);
        }
    }

    async function toggleRuntimeState() {
        const nextPaused = !runtimePaused;
        const message = nextPaused
            ? 'Pause all AI runtime services now? Active turns will be cancelled.'
            : 'Resume all AI runtime services now?';
        if (!window.confirm(message)) {
            return;
        }

        const button = document.getElementById('btn-runtime-kill-switch');
        if (button) {
            button.disabled = true;
        }

        try {
            const res = await fetch('/api/runtime/state', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paused: nextPaused }),
            });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(payload.detail || 'Failed to update runtime state');
            }
            applyRuntimeState(payload);
        } catch (err) {
            console.error('[BossMod] Failed to update runtime state:', err);
            window.alert(err.message || 'Failed to update runtime state');
            if (button) {
                button.disabled = false;
            }
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

    // ─── Nav button initialization ───

    function initNavButtons() {
        const settingsBtn = document.getElementById('btn-settings');
        const backBtn = document.getElementById('btn-back-to-office');
        const newAgentBtn = document.getElementById('btn-new-agent');
        const killSwitchBtn = document.getElementById('btn-runtime-kill-switch');

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

        if (killSwitchBtn) {
            killSwitchBtn.addEventListener('click', () => {
                void toggleRuntimeState();
            });
        }

        // Center mode toggle segment buttons
        const officeBtn = document.querySelector('.center-mode-btn[data-mode="office"]');
        const companyBtn = document.querySelector('.center-mode-btn[data-mode="company"]');
        const toggleEl = document.getElementById('center-mode-toggle');
        const dropdownEl = document.getElementById('company-subtab-dropdown');

        if (officeBtn) {
            officeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeCompanyDropdown();
                switchCenterMode('office');
            });
        }

        if (companyBtn) {
            // Click switches to company mode (current tab), no dropdown
            companyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeCompanyDropdown();
                if (centerMode !== 'company') {
                    switchCenterMode('company');
                }
            });
        }

        // Show dropdown on hover over the toggle (company side)
        if (toggleEl && dropdownEl) {
            let hoverTimeout = null;
            toggleEl.addEventListener('mouseenter', () => {
                clearTimeout(hoverTimeout);
                dropdownEl.classList.remove('hidden');
            });
            toggleEl.addEventListener('mouseleave', () => {
                hoverTimeout = setTimeout(() => {
                    dropdownEl.classList.add('hidden');
                }, 200);
            });
            dropdownEl.addEventListener('mouseenter', () => {
                clearTimeout(hoverTimeout);
            });
            dropdownEl.addEventListener('mouseleave', () => {
                hoverTimeout = setTimeout(() => {
                    dropdownEl.classList.add('hidden');
                }, 200);
            });
        }

        // Company sub-tab dropdown items
        document.querySelectorAll('.company-subtab-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                switchCompanyTab(item.dataset.subtab);
            });
        });
    }

    // Close company dropdown on outside click
    function initOutsideClickHandlers() {
        document.addEventListener('click', (e) => {
            const toggle = document.getElementById('center-mode-toggle');
            const dropdown = document.getElementById('company-subtab-dropdown');
            if (dropdown && toggle && !toggle.contains(e.target)) {
                dropdown.classList.add('hidden');
            }
        });
    }

    function updateNavForSettings(inSettings) {
        const backBtn = document.getElementById('btn-back-to-office');
        const settingsBtn = document.getElementById('btn-settings');
        const newAgentBtn = document.getElementById('btn-new-agent');
        const modeToggle = document.getElementById('center-mode-toggle');

        if (inSettings) {
            backBtn.classList.remove('hidden');
            backBtn.classList.add('flex');
            settingsBtn.classList.add('hidden');
            newAgentBtn.classList.add('hidden');
            if (modeToggle) modeToggle.classList.add('hidden');
        } else {
            backBtn.classList.add('hidden');
            backBtn.classList.remove('flex');
            settingsBtn.classList.remove('hidden');
            newAgentBtn.classList.remove('hidden');
            newAgentBtn.classList.add('sm:flex');
            if (modeToggle) {
                modeToggle.classList.remove('hidden');
                modeToggle.classList.add('sm:flex');
            }
        }

        if (window.lucide) lucide.createIcons();
    }

    function init() {
        initIcons();
        initTabs();
        initSplit();
        initPanelObserver();
        initNavButtons();
        initOutsideClickHandlers();
        initMobileSheet();
        initResize();
        initWebSocket();
        void fetchRuntimeState();

        // Restore center mode from prefs
        centerMode = prefs.centerMode || 'office';
        activeCompanyTab = prefs.activeCompanyTab || 'metrics';
        if (centerMode !== 'office') {
            switchCenterMode(centerMode);
        } else {
            updateCenterModeToggle();
        }

        // Start uptime interval
        startUptimeInterval();

        console.log('[BossMod] App initialized');
    }

    return {
        init,
        selectAgent,
        switchTab,
        switchCenterMode,
        switchCompanyTab,
        getCenterMode,
        updateNavForSettings,
    };
})();

// Boot on DOM ready
document.addEventListener('DOMContentLoaded', BossModApp.init);
