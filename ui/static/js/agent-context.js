/**
 * BossMod AI — Agent context controller.
 *
 * Manages the left panel state: when an agent is selected on the canvas,
 * the panel switches from empty/activity to agent-focused mode with
 * sub-views: Chat, Edit, Tasks.
 */

const AgentContext = (() => {
    const SHOW_SYSTEM_RECEIPTS_KEY = 'bossmod.chat.showSystemReceipts';
    let selectedAgent = null;
    let creatingAgent = false;
    let activeTopTab = 'focus';
    let activeSubview = 'chat';
    const interactedAgents = new Map(); // agentId → agentData (session-only)
    let activeDeskPath = '/me';
    let activeMeetingSessionId = null;
    let folderOpenerModalEl = null;
    const chatCache = new Map();
    const deskCache = new Map();
    let activeChatLoadId = 0;

    function mergeAgentSnapshot(agentDetails, runtimeSnapshot = null) {
        if (!agentDetails) return null;
        if (!runtimeSnapshot) return { ...agentDetails };
        const runtime = BossModUtils.normalizeAgent(runtimeSnapshot);
        return { ...agentDetails, ...runtime };
    }

    function updateSelectedAgentRuntimeDisplay() {
        if (!selectedAgent) return;
        const pill = document.getElementById('agent-runtime-status-pill');
        const dot = document.getElementById('agent-runtime-status-dot');
        const label = document.getElementById('agent-runtime-status-label');
        if (!pill || !dot || !label) return;

        pill.className = `inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${
            BossModUtils.getStatusClasses(selectedAgent.status || 'idle', selectedAgent.currentActivityKind)
        }`;
        dot.className = `w-1.5 h-1.5 rounded-full ${
            BossModUtils.getStatusDot(selectedAgent.status || 'idle', selectedAgent.currentActivityKind)
        }`;
        label.textContent = BossModUtils.getStatusLabel(selectedAgent.status || 'idle', selectedAgent.currentActivityKind);
    }

    // ─── Select / Deselect ───

    async function selectAgent(agentData) {
        creatingAgent = false;
        // Fetch full agent details
        try {
            const res = await fetch(`/api/agents/${agentData.id}`);
            if (!res.ok) return;
            selectedAgent = mergeAgentSnapshot(await res.json(), agentData);
        } catch {
            return;
        }

        // Track this agent in session chip bar
        interactedAgents.set(selectedAgent.id, selectedAgent);

        activeDeskPath = '/me';
        activeMeetingSessionId = null;
        activeTopTab = 'focus';
        activeSubview = 'chat';
        updateTabs();
        switchTopTab('focus');
    }

    function deselectAgent() {
        selectedAgent = null;
        creatingAgent = false;
        activeTopTab = 'focus';
        activeSubview = 'chat';
        activeDeskPath = '/me';
        activeMeetingSessionId = null;
        if (typeof DiagnosticsView !== 'undefined') DiagnosticsView.closeDetail();
        updateTabs();
        switchTopTab('focus');
    }

    function startCreateAgent() {
        selectedAgent = null;
        creatingAgent = true;
        activeTopTab = 'focus';
        activeSubview = 'edit';
        activeDeskPath = '/me';
        activeMeetingSessionId = null;
        updateTabs();
        switchTopTab('focus');
    }

    function getSelectedAgent() {
        return selectedAgent;
    }

    function handleWorldUpdate(agents) {
        if (typeof CompanyView !== 'undefined') {
            CompanyView.handleWorldUpdate(agents);
        }
        if (!selectedAgent) return;
        const previousActivityKind = selectedAgent.currentActivityKind || null;
        const runtimeSnapshot = agents.find(agent => agent.id === selectedAgent.id);
        if (!runtimeSnapshot) return;
        selectedAgent = mergeAgentSnapshot(selectedAgent, runtimeSnapshot);
        updateSelectedAgentRuntimeDisplay();
        if (activeSubview === 'meeting') {
            const nextActivityKind = selectedAgent.currentActivityKind || null;
            if (previousActivityKind !== nextActivityKind || nextActivityKind === 'meeting') {
                void renderMeeting();
            }
        }
    }

    function getCachedChat(agentId) {
        return chatCache.get(String(agentId)) || null;
    }

    function setCachedChat(agentId, messages) {
        chatCache.set(String(agentId), Array.isArray(messages) ? [...messages] : []);
    }

    function getDeskCacheKey(agentId, path) {
        return `${String(agentId)}:${String(path || '/')}`;
    }

    function getCachedDesk(agentId, path) {
        return deskCache.get(getDeskCacheKey(agentId, path)) || null;
    }

    function setCachedDesk(agentId, path, payload) {
        if (!payload) return;
        deskCache.set(getDeskCacheKey(agentId, path), payload);
    }

    function clearDeskCacheForAgent(agentId) {
        const prefix = `${String(agentId)}:`;
        for (const key of Array.from(deskCache.keys())) {
            if (key.startsWith(prefix)) {
                deskCache.delete(key);
            }
        }
    }

    function deskInvalidationPaths(path) {
        const normalized = String(path || '/');
        const parts = normalized.split('/').filter(Boolean);
        const paths = new Set(['/']);
        let current = '';
        for (const part of parts) {
            current += `/${part}`;
            paths.add(current);
        }
        if (normalized !== '/') {
            paths.add(normalized);
        }
        return paths;
    }

    function invalidateDeskCachesForPath(agentId, path) {
        const invalidated = deskInvalidationPaths(path);
        const normalized = String(path || '/');
        if (normalized === '/projects' || normalized.startsWith('/projects/')) {
            for (const key of Array.from(deskCache.keys())) {
                const separator = key.indexOf(':');
                const cachedPath = separator >= 0 ? key.slice(separator + 1) : '/';
                if (invalidated.has(cachedPath)) {
                    deskCache.delete(key);
                }
            }
            return invalidated;
        }

        for (const cachedPath of invalidated) {
            deskCache.delete(getDeskCacheKey(agentId, cachedPath));
        }
        return invalidated;
    }

    function shouldShowSystemReceipts() {
        try {
            return window.localStorage.getItem(SHOW_SYSTEM_RECEIPTS_KEY) !== 'false';
        } catch {
            return true;
        }
    }

    function setShowSystemReceipts(value) {
        try {
            window.localStorage.setItem(SHOW_SYSTEM_RECEIPTS_KEY, value ? 'true' : 'false');
        } catch {
            // Ignore storage failures and keep the in-memory render path working.
        }
    }

    function getVisibleChatMessages(messages) {
        if (shouldShowSystemReceipts()) return messages || [];
        return (messages || []).filter(msg => msg.message_type !== 'system');
    }

    // ─── Tab header management ───

    function updateTabs() {
        const tabsEl = document.getElementById('left-panel-tabs');
        tabsEl.innerHTML = `
            <button class="tab-btn flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative whitespace-nowrap overflow-hidden ${activeTopTab === 'focus' ? 'active' : ''}" data-tab="focus">
                <span class="flex items-center justify-center gap-1.5">
                    <i data-lucide="crosshair" class="w-4 h-4 shrink-0"></i>
                    <span class="toolbar-label">Focus</span>
                </span>
            </button>
            <button class="tab-btn flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative whitespace-nowrap overflow-hidden ${activeTopTab === 'directory' ? 'active' : ''}" data-tab="directory">
                <span class="flex items-center justify-center gap-1.5">
                    <i data-lucide="book-user" class="w-4 h-4 shrink-0"></i>
                    <span class="toolbar-label">Directory</span>
                </span>
            </button>
            <button class="tab-btn flex-1 px-3 py-2.5 text-sm font-medium transition-colors relative whitespace-nowrap overflow-hidden ${activeTopTab === 'channels' ? 'active' : ''}" data-tab="channels">
                <span class="flex items-center justify-center gap-1.5">
                    <i data-lucide="messages-square" class="w-4 h-4 shrink-0"></i>
                    <span class="toolbar-label">Channels</span>
                </span>
            </button>`;

        if (window.lucide) lucide.createIcons({ nodes: [tabsEl] });

        // Bind tab clicks
        tabsEl.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                switchTopTab(btn.dataset.tab);
            });
        });

        // Render agent chips and manage their visibility
        renderAgentChips();
    }

    // ─── Agent chips management ───

    function renderAgentChips() {
        const chipsEl = document.getElementById('agent-chips');
        if (!chipsEl) return;

        const isAgentContext = activeTopTab === 'focus';

        // Hide chips when in Directory/Channels or no interacted agents
        if (!isAgentContext || interactedAgents.size === 0) {
            chipsEl.classList.add('hidden');
            return;
        }

        chipsEl.classList.remove('hidden');
        const esc = BossModUtils.escapeHtml;

        chipsEl.innerHTML = Array.from(interactedAgents.values()).map(agent => {
            const isActive = selectedAgent && selectedAgent.id === agent.id;
            const color = agent.color || '#3b82f6';
            return `<div class="agent-chip flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors cursor-pointer
                            ${isActive
                                ? 'bg-bm-accent/10 text-bm-accent ring-1 ring-bm-accent/30'
                                : 'bg-bm-bg text-bm-muted hover:bg-slate-200'}"
                            data-agent-id="${agent.id}" role="button" tabindex="0">
                        <span class="w-2 h-2 rounded-full shrink-0" style="background:${color}"></span>
                        <span class="truncate max-w-[80px]">${esc(agent.name)}</span>
                        <button class="chip-dismiss ml-0.5 p-0.5 rounded-full hover:bg-black/10 transition-colors shrink-0"
                                data-dismiss-id="${agent.id}"
                                aria-label="Dismiss ${esc(agent.name)}">
                            <i data-lucide="x" class="w-3 h-3"></i>
                        </button>
                    </div>`;
        }).join('');

        if (window.lucide) lucide.createIcons({ nodes: [chipsEl] });

        // Bind chip clicks (select agent)
        chipsEl.querySelectorAll('.agent-chip').forEach(chip => {
            chip.addEventListener('click', (e) => {
                if (e.target.closest('.chip-dismiss')) return;
                const agentData = interactedAgents.get(chip.dataset.agentId);
                if (agentData) selectAgent(agentData);
            });
        });

        // Bind dismiss clicks
        chipsEl.querySelectorAll('.chip-dismiss').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                dismissAgent(btn.dataset.dismissId);
            });
        });
    }

    // ─── Agent chip dismiss ───

    function dismissAgent(agentId) {
        if (!interactedAgents.has(agentId)) return;

        const wasSelected = selectedAgent && selectedAgent.id === agentId;

        // Remove from tracked agents and clear chat cache
        interactedAgents.delete(agentId);
        chatCache.delete(String(agentId));
        clearDeskCacheForAgent(agentId);

        if (wasSelected) {
            selectedAgent = null;
            creatingAgent = false;

            // Auto-select next available agent
            if (interactedAgents.size > 0) {
                const nextAgent = interactedAgents.values().next().value;
                selectAgent(nextAgent);
                return;
            }

            // No agents left — show empty state
            activeSubview = 'chat';
            if (typeof DiagnosticsView !== 'undefined') DiagnosticsView.closeDetail();
            updateTabs();
            switchTopTab('focus');
        } else {
            // Just re-render chips, keep current selection
            renderAgentChips();
        }
    }

    // ─── Toolbar management ───

    function showToolbar() {
        const toolbar = document.getElementById('agent-toolbar');
        toolbar.classList.remove('hidden');

        const gearBtn = toolbar.querySelector('#gear-menu-btn');
        const gearDropdown = toolbar.querySelector('#gear-menu-dropdown');
        const gearSubviews = ['edit', 'diagnostics'];

        // Primary tab buttons
        toolbar.querySelectorAll('.agent-subview-btn').forEach(btn => {
            if (btn.id === 'gear-menu-btn') {
                btn.classList.toggle('active', gearSubviews.includes(activeSubview));
            } else if (btn.dataset.subview) {
                btn.classList.toggle('active', btn.dataset.subview === activeSubview);
                btn.onclick = () => switchSubview(btn.dataset.subview);
            }
        });

        // Gear dropdown toggle
        if (gearBtn && gearDropdown) {
            gearBtn.onclick = (e) => {
                e.stopPropagation();
                gearDropdown.classList.toggle('hidden');
            };

            // Gear menu items
            gearDropdown.querySelectorAll('.gear-menu-item').forEach(item => {
                item.onclick = () => {
                    gearDropdown.classList.add('hidden');
                    switchSubview(item.dataset.subview);
                };
            });
        }

        if (window.lucide) lucide.createIcons({ nodes: [toolbar] });
    }

    // Close gear dropdown on outside click
    document.addEventListener('click', () => {
        const dropdown = document.getElementById('gear-menu-dropdown');
        if (dropdown) dropdown.classList.add('hidden');
    });

    function hideToolbar() {
        document.getElementById('agent-toolbar').classList.add('hidden');
    }

    // ─── Sub-view management ───

    function hideAllSubviews() {
        document.getElementById('panel-empty-state')?.classList.add('hidden');
        document.getElementById('subview-chat').classList.add('hidden');
        document.getElementById('subview-edit').classList.add('hidden');
        document.getElementById('subview-meeting').classList.add('hidden');
        document.getElementById('subview-tasks').classList.add('hidden');
        document.getElementById('subview-desk').classList.add('hidden');
        document.getElementById('subview-diagnostics')?.classList.add('hidden');
        hideTopLevelPanel('tab-directory');
        hideTopLevelPanel('tab-channels');
    }

    function showTopLevelPanel(panelId) {
        const panel = document.getElementById(panelId);
        if (!panel) return null;
        panel.classList.remove('hidden');
        panel.classList.add('active');
        return panel;
    }

    function hideTopLevelPanel(panelId) {
        const panel = document.getElementById(panelId);
        if (!panel) return;
        panel.classList.add('hidden');
        panel.classList.remove('active');
    }

    function showEmptyState() {
        hideAllSubviews();
        hideToolbar();
        const empty = document.getElementById('panel-empty-state');
        if (empty) empty.classList.remove('hidden');
    }

    function switchTopTab(tab) {
        activeTopTab = tab || 'focus';
        updateTabs();
        hideAllSubviews();

        const chipsEl = document.getElementById('agent-chips');

        if (activeTopTab === 'directory') {
            hideToolbar();
            if (chipsEl) chipsEl.classList.add('hidden');
            const container = showTopLevelPanel('tab-directory');
            if (typeof CompanyView !== 'undefined' && container) {
                void CompanyView.render(container);
            }
            return;
        }

        if (activeTopTab === 'channels') {
            hideToolbar();
            if (chipsEl) chipsEl.classList.add('hidden');
            const container = showTopLevelPanel('tab-channels');
            if (typeof ChannelsView !== 'undefined' && container) {
                void ChannelsView.render(container);
            }
            return;
        }

        // Focus tab — show chips, toolbar if agent selected
        renderAgentChips();

        if (selectedAgent || creatingAgent) {
            if (selectedAgent) showToolbar();
            else hideToolbar();
            switchSubview(activeSubview);
            return;
        }

        // Auto-select first chip if available but none selected
        if (interactedAgents.size > 0 && !selectedAgent) {
            const firstAgent = interactedAgents.values().next().value;
            selectAgent(firstAgent);
            return;
        }

        showEmptyState();
    }

    function switchSubview(subview) {
        activeTopTab = 'focus';
        activeSubview = subview;
        updateTabs();
        hideAllSubviews();

        // Close diagnostic detail panel if switching away from diagnostics
        if (subview !== 'diagnostics' && typeof DiagnosticsView !== 'undefined') {
            DiagnosticsView.closeDetail();
        }

        // Update toolbar active state
        const gearSubviews = ['edit', 'diagnostics'];
        document.querySelectorAll('.agent-subview-btn').forEach(btn => {
            if (btn.id === 'gear-menu-btn') {
                btn.classList.toggle('active', gearSubviews.includes(subview));
            } else if (btn.dataset.subview) {
                btn.classList.toggle('active', btn.dataset.subview === subview);
            }
        });

        switch (subview) {
            case 'chat':
                document.getElementById('subview-chat').classList.remove('hidden');
                renderChat();
                break;
            case 'edit':
                document.getElementById('subview-edit').classList.remove('hidden');
                renderEdit();
                break;
            case 'meeting':
                document.getElementById('subview-meeting').classList.remove('hidden');
                renderMeeting();
                break;
            case 'tasks':
                document.getElementById('subview-tasks').classList.remove('hidden');
                renderTasks();
                break;
            case 'desk':
                document.getElementById('subview-desk').classList.remove('hidden');
                renderDesk(activeDeskPath);
                break;
            case 'diagnostics':
                document.getElementById('subview-diagnostics').classList.remove('hidden');
                if (typeof DiagnosticsView !== 'undefined') {
                    DiagnosticsView.load(selectedAgent?.id);
                }
                break;
        }
    }

    // ─── Chat sub-view ───

    async function renderChat() {
        const messagesEl = document.getElementById('chat-messages');
        if (!selectedAgent) return;

        const esc = BossModUtils.escapeHtml;
        const agentId = selectedAgent.id;
        const cached = getCachedChat(agentId);

        if (cached) {
            renderChatMessages(cached, selectedAgent.name);
            showChatSyncIndicator();
        } else {
            messagesEl.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-4">
                    <p>Loading messages...</p>
                </div>`;
        }

        bindChatSend();
        void refreshChatMessages(agentId);
    }

    async function refreshChatMessages(agentId) {
        const loadId = ++activeChatLoadId;
        let messages = [];

        try {
            const res = await fetch(`/api/agents/${agentId}/messages?limit=50`, { cache: 'no-store' });
            if (res.ok) messages = await res.json();
        } catch {
            messages = getCachedChat(agentId) || [];
        }

        setCachedChat(agentId, messages);

        if (!selectedAgent || selectedAgent.id !== agentId || activeSubview !== 'chat' || loadId !== activeChatLoadId) {
            return;
        }

        renderChatMessages(messages, selectedAgent.name);
    }

    function renderChatMessages(messages, agentName) {
        const messagesEl = document.getElementById('chat-messages');
        if (!messagesEl) return;

        const esc = BossModUtils.escapeHtml;
        messagesEl.innerHTML = '';
        renderChatControls(messagesEl);

        const visibleMessages = getVisibleChatMessages(messages);

        if (!visibleMessages || visibleMessages.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'text-bm-muted text-sm text-center mt-4';
            empty.innerHTML = `
                    <p>Chat with <strong>${esc(agentName)}</strong></p>
                    <p class="text-xs mt-1">Send a message to activate this agent.</p>`;
            messagesEl.appendChild(empty);
            return;
        }

        for (const msg of visibleMessages) {
            appendChatMessage(msg.content, msg.from, msg.message_type, msg);
        }
    }

    function renderChatControls(messagesEl) {
        const wrapper = document.createElement('div');
        wrapper.className = 'flex items-center justify-end mb-3';
        wrapper.innerHTML = `
            <label class="inline-flex items-center gap-2 text-xs text-bm-muted cursor-pointer select-none">
                <input id="chat-system-receipts-toggle"
                       type="checkbox"
                       class="rounded border-bm-border text-bm-accent focus:ring-bm-accent/30"
                       ${shouldShowSystemReceipts() ? 'checked' : ''}>
                <span>Show system notifications</span>
            </label>`;
        messagesEl.appendChild(wrapper);

        const toggle = wrapper.querySelector('#chat-system-receipts-toggle');
        if (!toggle) return;
        toggle.addEventListener('change', () => {
            setShowSystemReceipts(toggle.checked);
            if (!selectedAgent) return;
            renderChatMessages(getCachedChat(selectedAgent.id) || [], selectedAgent.name);
        });
    }

    function bindChatSend() {
        const sendBtn = document.getElementById('chat-send');
        const input = document.getElementById('chat-input');

        async function handleSend() {
            const el = document.getElementById('chat-input');
            const text = el?.value.trim();
            if (!text || !selectedAgent) return;

            el.value = '';
            el.style.height = 'auto';
            showTypingIndicator();

            try {
                const res = await fetch(`/api/agents/${selectedAgent.id}/activate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: text }),
                });
                // Always hide indicator once HTTP completes — WS events
                // arrive before this resolves, so messages are already appended.
                // If agent produced no reply (walk_to, idle), no WS event fires,
                // so we must clean up here regardless.
                hideTypingIndicator();
                if (!res.ok) {
                    appendChatMessage('Failed to reach agent.', 'agent');
                }
            } catch {
                hideTypingIndicator();
                appendChatMessage('Failed to reach agent.', 'agent');
            }
        }

        sendBtn.onclick = handleSend;
        input.onkeydown = (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
            }
        };
        // Auto-expand textarea as content grows, shrink back when cleared
        input.oninput = () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 160) + 'px';
        };
        input.focus();
    }

    function appendChatMessage(text, fromType, messageType = null, message = null) {
        if ((fromType === 'system' || messageType === 'system') && !shouldShowSystemReceipts()) {
            return;
        }
        const messagesEl = document.getElementById('chat-messages');
        // Remove empty state hint if present
        const emptyHint = messagesEl.querySelector('.text-center');
        if (emptyHint) emptyHint.remove();

        const msgDiv = document.createElement('div');
        let bubbleClass = 'from-agent';
        if (fromType === 'human') {
            bubbleClass = 'from-human';
        } else if (fromType === 'system' || messageType === 'system') {
            bubbleClass = 'from-system';
        }
        msgDiv.className = `chat-msg ${bubbleClass} mb-2`;
        const textEl = document.createElement('div');
        textEl.innerText = text;
        msgDiv.appendChild(textEl);

        const openPath = message?.desk_path || null;
        if (openPath && (fromType === 'system' || messageType === 'system')) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'mt-2 inline-flex items-center gap-1.5 px-2 py-1 rounded border border-bm-border bg-white/70 hover:bg-white text-xs font-medium transition-colors';
            btn.innerHTML = '<i data-lucide="folder-open" class="w-3 h-3"></i><span>Open in Desk</span>';
            btn.addEventListener('click', () => openDeskPath(openPath));
            msgDiv.appendChild(btn);
        }
        messagesEl.appendChild(msgDiv);
        if (window.lucide) lucide.createIcons({ nodes: [msgDiv] });
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function handleChatMessage(data) {
        const changedPath = data?.desk_path || null;
        const invalidatedPaths = changedPath ? invalidateDeskCachesForPath(data.agent_id, changedPath) : null;
        const viewingSharedProjectPath = Boolean(
            selectedAgent &&
            activeSubview === 'desk' &&
            changedPath &&
            (changedPath === '/projects' || changedPath.startsWith('/projects/')) &&
            invalidatedPaths?.has(activeDeskPath)
        );
        if (viewingSharedProjectPath) {
            void renderDesk(activeDeskPath, { forceRefresh: true });
        }
        if (!selectedAgent || data.agent_id !== selectedAgent.id) return;
        if (activeSubview === 'desk' && invalidatedPaths?.has(activeDeskPath)) {
            void renderDesk(activeDeskPath, { forceRefresh: true });
        }
        const cached = getCachedChat(data.agent_id) || [];
        cached.push({
            content: data.content,
            from: data.from,
            from_name: data.from_name,
            message_type: data.message_type,
            notification_kind: data.notification_kind,
            desk_path: data.desk_path,
            message_id: data.message_id,
            created_at: data.created_at,
        });
        setCachedChat(data.agent_id, cached);
        hideTypingIndicator();
        appendChatMessage(data.content, data.from, data.message_type, data);
    }

    async function handleChatReset(data) {
        chatCache.delete(String(data.agent_id));
        if (!selectedAgent || data.agent_id !== selectedAgent.id) return;
        hideTypingIndicator();
        if (activeSubview === 'chat') {
            await renderChat();
        }
    }

    function showTypingIndicator() {
        const messagesEl = document.getElementById('chat-messages');
        hideTypingIndicator();

        const indicator = document.createElement('div');
        indicator.id = 'chat-typing-indicator';
        indicator.className = 'chat-msg from-agent mb-2 text-bm-muted italic';
        indicator.textContent = 'Thinking...';
        messagesEl.appendChild(indicator);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function hideTypingIndicator() {
        const el = document.getElementById('chat-typing-indicator');
        if (el) el.remove();
    }

    function showChatSyncIndicator() {
        const messagesEl = document.getElementById('chat-messages');
        if (!messagesEl) return;
        let indicator = document.getElementById('chat-sync-indicator');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'chat-sync-indicator';
            indicator.className = 'text-[11px] text-bm-muted italic mb-2';
            indicator.textContent = 'Refreshing chat...';
            messagesEl.appendChild(indicator);
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ─── Meeting sub-view ───

    async function renderMeeting() {
        const container = document.getElementById('subview-meeting');
        if (!selectedAgent || !container) return;

        container.innerHTML = `
            <div class="flex-1 overflow-y-auto p-4">
                <div class="text-bm-muted text-sm text-center mt-6">
                    <p>Loading meeting...</p>
                </div>
            </div>`;

        try {
            const res = await fetch(`/api/agents/${selectedAgent.id}/meeting-session?limit=80`, { cache: 'no-store' });
            if (!res.ok) {
                throw new Error(await res.text());
            }
            const payload = await res.json();
            if (!payload.active || !payload.session) {
                activeMeetingSessionId = null;
                renderMeetingEmpty(container);
                return;
            }
            activeMeetingSessionId = payload.session.id;
            renderMeetingSession(container, payload.session);
        } catch (err) {
            activeMeetingSessionId = null;
            renderMeetingError(container);
            console.error('[AgentContext] Meeting load failed:', err);
        }
    }

    function renderMeetingEmpty(container) {
        container.innerHTML = `
            <div class="flex-1 overflow-y-auto p-4">
                <div class="text-bm-muted text-sm text-center mt-8">
                    <i data-lucide="users" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
                    <p>No active meeting for ${BossModUtils.escapeHtml(selectedAgent?.name || 'this agent')}.</p>
                    <p class="text-xs mt-1">Ask them to head to the meeting room to start a shared session.</p>
                </div>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderMeetingError(container) {
        container.innerHTML = `
            <div class="flex-1 overflow-y-auto p-4">
                <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                    Failed to load meeting session.
                </div>
            </div>`;
    }

    function renderMeetingSession(container, session) {
        const participants = Array.isArray(session.participants) ? session.participants : [];
        const messages = Array.isArray(session.messages) ? session.messages : [];
        container.innerHTML = `
            <div class="flex-1 min-h-0 flex flex-col">
                <div class="border-b border-bm-border px-4 py-3 shrink-0">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <p class="text-xs uppercase tracking-wide text-bm-muted">Meeting Session</p>
                            <h3 class="text-sm font-semibold mt-1">${BossModUtils.escapeHtml(session.title || 'Meeting')}</h3>
                            <p class="text-xs text-bm-muted mt-1">${BossModUtils.escapeHtml(session.room_name || 'Meeting Room')}</p>
                        </div>
                        <button type="button" id="meeting-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                    <div class="mt-3 flex flex-wrap gap-2">
                        ${participants.length ? participants.map(participant => `
                            <span class="inline-flex items-center gap-1.5 rounded-full border border-bm-border bg-white px-2 py-1 text-xs text-bm-text">
                                <span class="w-2 h-2 rounded-full bg-bm-accent"></span>
                                ${BossModUtils.escapeHtml(participant.name || 'Unknown')}
                            </span>
                        `).join('') : '<span class="text-xs text-bm-muted">No active participants</span>'}
                    </div>
                </div>
                <div id="meeting-messages" class="flex-1 min-h-0 overflow-y-auto p-4">
                    ${messages.length ? '' : '<div class="text-bm-muted text-sm text-center mt-8">No meeting messages yet.</div>'}
                </div>
                <div class="p-3 border-t border-bm-border shrink-0">
                    <div class="flex gap-2 items-end">
                        <textarea id="meeting-input" rows="1"
                                  placeholder="Send a message to everyone in the meeting..."
                                  class="flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent resize-none overflow-hidden"></textarea>
                        <button id="meeting-send"
                                class="px-3 py-2 bg-bm-accent text-white rounded-lg hover:bg-bm-accent-hover transition-colors shrink-0">
                            <i data-lucide="send" class="w-4 h-4"></i>
                        </button>
                    </div>
                </div>
            </div>`;

        const messagesEl = container.querySelector('#meeting-messages');
        if (messagesEl) {
            for (const message of messages) {
                appendMeetingMessage(messagesEl, message);
            }
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        bindMeetingSend(session.id);
        container.querySelector('#meeting-refresh-btn')?.addEventListener('click', () => {
            void renderMeeting();
        });
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function appendMeetingMessage(messagesEl, message) {
        if (!messagesEl || !message) return;
        const authorType = message.author_type || 'agent';
        const authorName = message.author_name || 'Unknown';
        const bubbleClass =
            authorType === 'human'
                ? 'from-human'
                : (authorType === 'system' ? 'from-system' : 'from-agent');

        const wrapper = document.createElement('div');
        wrapper.className = `chat-msg ${bubbleClass} mb-2`;

        const label = document.createElement('div');
        label.className = 'text-[11px] font-medium opacity-70 mb-1';
        label.textContent = authorName;
        wrapper.appendChild(label);

        const body = document.createElement('div');
        body.innerText = message.content || '';
        wrapper.appendChild(body);

        messagesEl.appendChild(wrapper);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function bindMeetingSend(sessionId) {
        const sendBtn = document.getElementById('meeting-send');
        const input = document.getElementById('meeting-input');
        if (!sendBtn || !input || !selectedAgent) return;

        async function handleSend() {
            const text = String(input.value || '').trim();
            if (!text || !selectedAgent || !sessionId) return;

            input.value = '';
            input.style.height = 'auto';

            try {
                const res = await fetch(`/api/agents/${selectedAgent.id}/meeting-session/messages`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: text }),
                });
                if (!res.ok) {
                    throw new Error(await res.text());
                }
            } catch (err) {
                console.error('[AgentContext] Failed to send meeting message:', err);
            }
        }

        sendBtn.onclick = handleSend;
        input.onkeydown = (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
            }
        };
        input.oninput = () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 160) + 'px';
        };
    }

    function handleMeetingMessage(data) {
        if (!selectedAgent) return;
        if (!data?.session_id) return;
        if (activeMeetingSessionId && data.session_id !== activeMeetingSessionId) return;
        if (activeSubview !== 'meeting') return;
        void renderMeeting();
    }

    function handleChannelMessage(data) {
        if (typeof ChannelsView !== 'undefined') {
            ChannelsView.handleChannelMessage(data);
        }
    }

    function handleChannelUpdated(data) {
        if (typeof ChannelsView !== 'undefined') {
            ChannelsView.handleChannelUpdated(data);
        }
    }

    function openChannel(channelId) {
        activeTopTab = 'channels';
        updateTabs();
        switchTopTab('channels');
        if (typeof ChannelsView !== 'undefined' && channelId) {
            void ChannelsView.openChannel(channelId);
        }
    }

    // ─── Edit sub-view ───

    async function renderEdit() {
        const container = document.getElementById('subview-edit');
        if (!selectedAgent && !creatingAgent) return;

        if (selectedAgent?.id) {
            try {
                const res = await fetch(`/api/agents/${selectedAgent.id}`, { cache: 'no-store' });
                if (res.ok) {
                    selectedAgent = mergeAgentSnapshot(await res.json(), selectedAgent);
                    updateTabs();
                }
            } catch {
                // Fall back to the currently selected agent snapshot.
            }
        }

        // Reuse AgentPanel's renderForm logic but target the inline container
        if (typeof AgentPanel !== 'undefined') {
            await AgentPanel.renderInline(container, selectedAgent, async (savedAgent) => {
                creatingAgent = false;

                if (savedAgent) {
                    selectedAgent = mergeAgentSnapshot(savedAgent, selectedAgent);
                    updateTabs();
                    showToolbar();
                    updateSelectedAgentRuntimeDisplay();
                    return;
                }

                if (!selectedAgent?.id) return;
                try {
                    const res = await fetch(`/api/agents/${selectedAgent.id}`, { cache: 'no-store' });
                    if (res.ok) {
                        selectedAgent = mergeAgentSnapshot(await res.json(), selectedAgent);
                        updateTabs();
                        updateSelectedAgentRuntimeDisplay();
                    }
                } catch { /* ignore */ }
            }, () => {
                // On delete: deselect
                deselectAgent();
            });
        } else {
            container.innerHTML = `
                <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                    Agent editor failed to load. Refresh the page and try again.
                </div>`;
        }
    }

    // ─── Tasks sub-view ───

    async function renderTasks() {
        const container = document.getElementById('subview-tasks');
        if (!selectedAgent) return;

        let tasks = [];
        try {
            const res = await fetch(`/api/tasks?assigned_to=${selectedAgent.id}`);
            tasks = await res.json();
        } catch { /* ignore */ }

        if (tasks.length === 0) {
            container.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-8">
                    <i data-lucide="list-todo" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
                    <p>No tasks assigned to ${BossModUtils.escapeHtml(selectedAgent.name)}</p>
                </div>`;
        } else {
            let html = `<div class="space-y-2">`;
            for (const t of tasks) {
                const statusColor = t.status === 'complete' ? 'text-emerald-600' :
                                    t.status === 'accepted' ? 'text-blue-600' :
                                    t.status === 'active' ? 'text-amber-600' :
                                    t.status === 'blocked' ? 'text-red-600' :
                                    t.status === 'declined' ? 'text-orange-600' : 'text-bm-muted';
                html += `
                    <div class="p-3 border border-bm-border rounded-lg bg-white">
                        <div class="flex items-start justify-between">
                            <div>
                                <p class="text-sm font-medium">${BossModUtils.escapeHtml(t.title)}</p>
                                ${t.description ? `<p class="text-xs text-bm-muted mt-0.5">${BossModUtils.escapeHtml(t.description.slice(0, 100))}</p>` : ''}
                            </div>
                            <span class="text-xs font-medium ${statusColor}">${t.status}</span>
                        </div>
                    </div>`;
            }
            html += '</div>';
            container.innerHTML = html;
        }

        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    // ─── Desk sub-view ───

    async function renderDesk(path = '/me', { forceRefresh = false } = {}) {
        const container = document.getElementById('subview-desk');
        if (!selectedAgent || !container) return;
        activeDeskPath = path || '/me';
        const cached = !forceRefresh ? getCachedDesk(selectedAgent.id, activeDeskPath) : null;
        if (cached) {
            renderDeskPayload(container, cached);
            return;
        }

        container.innerHTML = `
            <div class="text-bm-muted text-sm text-center mt-6">
                <p>Loading desk...</p>
            </div>`;

        try {
            const res = await fetch(`/api/agents/${selectedAgent.id}/desk?path=${encodeURIComponent(activeDeskPath)}`, { cache: 'no-store' });
            if (!res.ok) {
                throw new Error(await res.text());
            }
            const payload = await res.json();
            setCachedDesk(selectedAgent.id, activeDeskPath, payload);
            renderDeskPayload(container, payload);
        } catch (err) {
            renderDeskError(container, activeDeskPath);
            console.error('[AgentContext] Desk load failed:', err);
        }
    }

    function renderDeskPayload(container, payload) {
        if (payload.kind === 'file') {
            renderDeskFile(container, payload);
            return;
        }
        renderDeskDirectory(container, payload);
    }

    function renderDeskError(container, failedPath) {
        const safePath = failedPath || '/me';
        const parentPath = parentDeskPath(safePath);
        container.innerHTML = `
            <div class="space-y-4">
                <div class="flex items-center justify-end gap-2">
                    <button type="button" id="desk-error-back-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                        Back
                    </button>
                    <button type="button" id="desk-error-refresh-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                        Refresh
                    </button>
                </div>
                <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                    Failed to load desk contents.
                    <div class="mt-2 text-xs text-red-600">Path: ${BossModUtils.escapeHtml(safePath)}</div>
                </div>
            </div>`;

        container.querySelector('#desk-error-back-btn')?.addEventListener('click', () => {
            openDeskPath(parentPath);
        });
        container.querySelector('#desk-error-refresh-btn')?.addEventListener('click', () => {
            renderDesk(safePath);
        });
    }

    function renderDeskDirectory(container, payload) {
        const entries = Array.isArray(payload.entries) ? payload.entries : null;
        const breadcrumbs = renderDeskBreadcrumbs(payload.breadcrumbs || []);
        const path = String(payload.path || '/me');
        const canOpenFolder = Boolean(path && path !== '/');
        const canGoUp = !['/', '/me', '/projects'].includes(path);
        const deskRootTarget = path.startsWith('/projects') ? '/me' : '/projects';
        const deskRootLabel = path.startsWith('/projects') ? 'My Desk' : 'Projects';
        let html = `
            <div class="space-y-4">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <p class="text-xs uppercase tracking-wide text-bm-muted">Desk Browser</p>
                        <h3 class="text-sm font-semibold mt-1">${BossModUtils.escapeHtml(payload.name || 'Desk')}</h3>
                        <div class="text-xs text-bm-muted mt-1">${breadcrumbs}</div>
                    </div>
                    <div class="flex items-center gap-2">
                        <button type="button" id="desk-root-switch-btn" data-path="${BossModUtils.escapeHtml(deskRootTarget)}"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            ${BossModUtils.escapeHtml(deskRootLabel)}
                        </button>
                        ${canGoUp ? `
                            <button type="button" id="desk-open-parent-btn"
                                    class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                                Up
                            </button>` : ''}
                        ${canOpenFolder ? `
                            <button type="button" id="desk-open-folder-btn"
                                    class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                                Open Folder
                            </button>` : ''}
                        <button type="button" id="desk-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                </div>`;

        html += renderDeskEntryList(entries || [], { emptyLabel: 'This folder is empty.' });

        html += '</div>';
        container.innerHTML = html;
        bindDeskInteractions(container);
    }

    function renderDeskFile(container, payload) {
        const artifact = payload.artifact || null;
        const breadcrumbs = renderDeskBreadcrumbs(payload.breadcrumbs || []);
        const path = String(payload.path || '/me');
        const deskRootTarget = path.startsWith('/projects') ? '/me' : '/projects';
        const deskRootLabel = path.startsWith('/projects') ? 'My Desk' : 'Projects';
        const metadataBits = [];
        if (artifact?.category) metadataBits.push(`category: ${artifact.category}`);
        if (artifact?.updated_at) metadataBits.push(`updated: ${new Date(artifact.updated_at).toLocaleString()}`);
        const html = `
            <div class="space-y-4">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <p class="text-xs uppercase tracking-wide text-bm-muted">Desk File</p>
                        <h3 class="text-sm font-semibold mt-1">${BossModUtils.escapeHtml(payload.name || payload.path || 'File')}</h3>
                        <div class="text-xs text-bm-muted mt-1">${breadcrumbs}</div>
                        ${metadataBits.length ? `<div class="text-[11px] text-bm-muted mt-2">${BossModUtils.escapeHtml(metadataBits.join(' • '))}</div>` : ''}
                    </div>
                    <div class="flex items-center gap-2">
                        <button type="button" id="desk-root-switch-btn" data-path="${BossModUtils.escapeHtml(deskRootTarget)}"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            ${BossModUtils.escapeHtml(deskRootLabel)}
                        </button>
                        <button type="button" id="desk-open-parent-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Up
                        </button>
                        <button type="button" id="desk-open-folder-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Open Folder
                        </button>
                        <button type="button" id="desk-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                </div>
                <div class="rounded-lg border border-bm-border bg-white p-3">
                    <pre class="text-xs whitespace-pre-wrap break-words text-bm-text">${BossModUtils.escapeHtml(payload.content || '')}</pre>
                    ${payload.truncated ? '<p class="text-[11px] text-bm-muted mt-2">Preview truncated.</p>' : ''}
                </div>
            </div>`;
        container.innerHTML = html;
        bindDeskInteractions(container, payload.path);
    }

    function renderDeskEntryList(entries, { emptyLabel }) {
        if (!Array.isArray(entries) || entries.length === 0) {
            return `<div class="rounded-lg border border-dashed border-bm-border p-3 text-xs text-bm-muted">${BossModUtils.escapeHtml(emptyLabel)}</div>`;
        }
        return `
            <div class="space-y-2">
                ${entries.map(entry => `
                    <button type="button"
                            class="desk-entry w-full text-left rounded-lg border border-bm-border bg-white px-3 py-2 hover:bg-slate-50 transition-colors"
                            data-path="${BossModUtils.escapeHtml(entry.path)}">
                        <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0">
                                <div class="flex items-center gap-2">
                                    <i data-lucide="${entry.is_dir ? 'folder' : 'file-text'}" class="w-3.5 h-3.5 shrink-0"></i>
                                    <span class="text-sm font-medium truncate">${BossModUtils.escapeHtml(entry.name)}</span>
                                </div>
                                <div class="text-[11px] text-bm-muted mt-1 truncate">${BossModUtils.escapeHtml(entry.path)}</div>
                            </div>
                            <div class="text-[11px] text-bm-muted shrink-0">
                                ${BossModUtils.escapeHtml(entry.category || '')}
                            </div>
                        </div>
                    </button>
                `).join('')}
            </div>`;
    }

    function renderDeskBreadcrumbs(breadcrumbs) {
        if (!Array.isArray(breadcrumbs) || breadcrumbs.length === 0) {
            return '';
        }
        const root = breadcrumbs[0];
        let html = `<button type="button" class="desk-crumb hover:underline" data-path="${BossModUtils.escapeHtml(root.path)}">${BossModUtils.escapeHtml(root.label)}</button>`;
        for (let index = 1; index < breadcrumbs.length; index += 1) {
            const item = breadcrumbs[index];
            if (index > 1) {
                html += '<span class="text-bm-muted">/</span>';
            }
            html += `<button type="button" class="desk-crumb hover:underline" data-path="${BossModUtils.escapeHtml(item.path)}">${BossModUtils.escapeHtml(item.label)}</button>`;
        }
        return html;
    }

    function bindDeskInteractions(container, filePath = null) {
        container.querySelectorAll('.desk-entry, .desk-crumb').forEach(btn => {
            btn.addEventListener('click', () => {
                const path = btn.dataset.path;
                if (!path) return;
                openDeskPath(path);
            });
        });
        const refreshBtn = container.querySelector('#desk-refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => renderDesk(activeDeskPath, { forceRefresh: true }));
        }
        const rootSwitchBtn = container.querySelector('#desk-root-switch-btn');
        if (rootSwitchBtn) {
            rootSwitchBtn.addEventListener('click', () => {
                const path = rootSwitchBtn.dataset.path;
                openDeskPath(path || '/projects');
            });
        }
        const openFolderBtn = container.querySelector('#desk-open-folder-btn');
        if (openFolderBtn) {
            openFolderBtn.addEventListener('click', async () => {
                await openDeskFolder(filePath || activeDeskPath);
            });
        }
        const upBtn = container.querySelector('#desk-open-parent-btn');
        if (upBtn) {
            upBtn.addEventListener('click', () => {
                openDeskPath(parentDeskPath(filePath || activeDeskPath));
            });
        }
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function parentDeskPath(path) {
        if (!path || path === '/') return '/me';
        if (path === '/me') return '/me';
        if (path === '/projects') return '/projects';
        const parts = String(path).split('/').filter(Boolean);
        parts.pop();
        return parts.length ? `/${parts.join('/')}` : '/me';
    }

    function openDeskPath(path) {
        if (!selectedAgent) return;
        activeDeskPath = path || '/me';
        switchSubview('desk');
    }

    async function openDeskFolder(path) {
        if (!selectedAgent) return;
        try {
            const res = await fetch(`/api/agents/${selectedAgent.id}/desk/open-folder?path=${encodeURIComponent(path || '/me')}`, {
                method: 'POST',
            });
            if (!res.ok) {
                if (res.status === 409) {
                    const payload = await res.json();
                    const detail = payload?.detail;
                    if (detail?.code === 'desk_open_folder_handler_required' || detail?.code === 'desk_open_folder_handler_invalid') {
                        const chosen = await promptForFolderOpener(detail);
                        if (chosen) {
                            await fetch(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(chosen)}&category=advanced`, {
                                method: 'PUT',
                            });
                            await openDeskFolder(path);
                        }
                        return;
                    }
                }
                throw new Error(await res.text());
            }
        } catch (err) {
            console.error('[AgentContext] Failed to open desk folder:', err);
        }
    }

    function promptForFolderOpener(detail) {
        const options = Array.isArray(detail?.options) ? detail.options : [];
        return new Promise((resolve) => {
            closeFolderOpenerModal();

            const overlay = document.createElement('div');
            overlay.className = 'fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4';
            overlay.innerHTML = `
                <div class="w-full max-w-lg rounded-xl border border-bm-border bg-white shadow-xl">
                    <div class="px-5 py-4 border-b border-bm-border">
                        <h3 class="text-lg font-semibold">Choose Folder Opener</h3>
                        <p class="text-sm text-bm-muted mt-1">${BossModUtils.escapeHtml(detail?.message || 'Choose how BossMod should open folders on this machine.')}</p>
                    </div>
                    <div class="p-5 space-y-4">
                        <div class="space-y-2" id="folder-opener-choice-list">
                            ${options.map((option, index) => `
                                <label class="flex items-start gap-3 rounded-lg border border-bm-border p-3 hover:bg-slate-50 cursor-pointer">
                                    <input type="radio" name="folder-opener-choice" value="${BossModUtils.escapeHtml(option.value)}" ${index === 0 ? 'checked' : ''} class="mt-0.5">
                                    <span>
                                        <span class="block text-sm font-medium">${BossModUtils.escapeHtml(option.label)}</span>
                                        <span class="block text-xs text-bm-muted mt-0.5">${BossModUtils.escapeHtml(option.description || '')}</span>
                                    </span>
                                </label>
                            `).join('')}
                            <label class="flex items-start gap-3 rounded-lg border border-bm-border p-3 hover:bg-slate-50 cursor-pointer">
                                <input type="radio" name="folder-opener-choice" value="__custom__" ${options.length === 0 ? 'checked' : ''} class="mt-0.5">
                                <span class="flex-1">
                                    <span class="block text-sm font-medium">Custom executable</span>
                                    <span class="block text-xs text-bm-muted mt-0.5">Enter the file manager command available on PATH.</span>
                                    <input id="folder-opener-custom-input" type="text" placeholder="e.g. thunar"
                                           class="mt-2 w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white">
                                </span>
                            </label>
                        </div>
                    </div>
                    <div class="px-5 py-4 border-t border-bm-border flex items-center justify-end gap-2">
                        <button type="button" id="folder-opener-cancel"
                                class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">
                            Cancel
                        </button>
                        <button type="button" id="folder-opener-save"
                                class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">
                            Save
                        </button>
                    </div>
                </div>`;

            document.body.appendChild(overlay);
            folderOpenerModalEl = overlay;

            const cancel = () => {
                closeFolderOpenerModal();
                resolve(null);
            };

            overlay.querySelector('#folder-opener-cancel')?.addEventListener('click', cancel);
            overlay.addEventListener('click', (event) => {
                if (event.target === overlay) cancel();
            });
            overlay.querySelector('#folder-opener-custom-input')?.addEventListener('focus', () => {
                const customRadio = overlay.querySelector('input[name="folder-opener-choice"][value="__custom__"]');
                if (customRadio) customRadio.checked = true;
            });

            overlay.querySelector('#folder-opener-save')?.addEventListener('click', () => {
                const selected = overlay.querySelector('input[name="folder-opener-choice"]:checked');
                if (!selected) return;
                if (selected.value === '__custom__') {
                    const custom = String(overlay.querySelector('#folder-opener-custom-input')?.value || '').trim();
                    if (!custom) return;
                    closeFolderOpenerModal();
                    resolve(custom);
                    return;
                }
                closeFolderOpenerModal();
                resolve(selected.value);
            });
        });
    }

    function closeFolderOpenerModal() {
        if (folderOpenerModalEl) {
            folderOpenerModalEl.remove();
            folderOpenerModalEl = null;
        }
    }

    // ─── Init ───

    function init() {
        if (typeof CompanyView !== 'undefined') {
            CompanyView.setCallbacks({
                onSelectAgent: async (agent) => {
                    await selectAgent(agent);
                },
                onOpenChannel: (channelId) => {
                    openChannel(channelId);
                },
            });
        }
        updateTabs();
    }

    return {
        init,
        selectAgent,
        startCreateAgent,
        deselectAgent,
        getSelectedAgent,
        handleChatMessage,
        handleChatReset,
        handleMeetingMessage,
        handleChannelMessage,
        handleChannelUpdated,
        handleWorldUpdate,
        openChannel,
    };
})();

document.addEventListener('DOMContentLoaded', AgentContext.init);
