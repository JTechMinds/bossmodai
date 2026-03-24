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
    let activeSubview = 'chat';
    let activeDeskPath = '/me';
    let folderOpenerModalEl = null;
    const chatCache = new Map();
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

        activeDeskPath = '/me';
        activeSubview = 'chat';
        updateTabs();
        showToolbar();
        switchSubview('chat');
    }

    function deselectAgent() {
        selectedAgent = null;
        creatingAgent = false;
        activeSubview = 'chat';
        activeDeskPath = '/me';
        if (typeof DiagnosticsView !== 'undefined') DiagnosticsView.closeDetail();
        updateTabs();
        hideToolbar();
        showEmptyState();
    }

    function startCreateAgent() {
        selectedAgent = null;
        creatingAgent = true;
        activeSubview = 'edit';
        activeDeskPath = '/me';
        updateTabs();
        hideToolbar();
        switchSubview('edit');
    }

    function getSelectedAgent() {
        return selectedAgent;
    }

    function handleWorldUpdate(agents) {
        if (!selectedAgent) return;
        const runtimeSnapshot = agents.find(agent => agent.id === selectedAgent.id);
        if (!runtimeSnapshot) return;
        selectedAgent = mergeAgentSnapshot(selectedAgent, runtimeSnapshot);
        updateSelectedAgentRuntimeDisplay();
    }

    function getCachedChat(agentId) {
        return chatCache.get(String(agentId)) || null;
    }

    function setCachedChat(agentId, messages) {
        chatCache.set(String(agentId), Array.isArray(messages) ? [...messages] : []);
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
        const esc = BossModUtils.escapeHtml;

        if (selectedAgent || creatingAgent) {
            const tabColor = selectedAgent?.color || '#3b82f6';
            const tabLabel = selectedAgent ? esc(selectedAgent.name) : 'New Agent';
            tabsEl.innerHTML = `
                <button class="tab-btn active flex-1 px-4 py-2.5 text-sm font-medium
                               transition-colors relative" data-tab="agent">
                    <span class="flex items-center justify-center gap-1.5">
                        <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${tabColor}"></span>
                        <span class="truncate">${tabLabel}</span>
                        <button id="btn-deselect-agent" class="ml-1 p-0.5 rounded hover:bg-slate-200 transition-colors"
                                title="Deselect agent">
                            <i data-lucide="x" class="w-3.5 h-3.5"></i>
                        </button>
                    </span>
                </button>
                <button class="tab-btn flex-1 px-4 py-2.5 text-sm font-medium
                               transition-colors relative" data-tab="activity">
                    <span class="flex items-center justify-center gap-1.5">
                        <i data-lucide="activity" class="w-4 h-4"></i>
                        Activity
                    </span>
                </button>`;
        } else {
            tabsEl.innerHTML = `
                <button class="tab-btn active flex-1 px-4 py-2.5 text-sm font-medium
                               transition-colors relative" data-tab="chat">
                    <span class="flex items-center justify-center gap-1.5">
                        <i data-lucide="message-circle" class="w-4 h-4"></i>
                        Chat
                    </span>
                </button>
                <button class="tab-btn flex-1 px-4 py-2.5 text-sm font-medium
                               transition-colors relative" data-tab="activity">
                    <span class="flex items-center justify-center gap-1.5">
                        <i data-lucide="activity" class="w-4 h-4"></i>
                        Activity
                    </span>
                </button>`;
        }

        if (window.lucide) lucide.createIcons({ nodes: [tabsEl] });

        // Bind tab clicks
        tabsEl.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                // Don't trigger tab switch when clicking the × button
                if (e.target.closest('#btn-deselect-agent')) return;

                const tab = btn.dataset.tab;
                tabsEl.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');

                if (tab === 'activity') {
                    hideAllSubviews();
                    document.getElementById('agent-toolbar')?.classList.add('hidden');
                    document.getElementById('tab-activity').classList.add('active');
                    document.getElementById('panel-empty-state')?.classList.add('hidden');
                } else {
                    document.getElementById('tab-activity').classList.remove('active');
                    if (selectedAgent || creatingAgent) {
                        if (selectedAgent) showToolbar();
                        else hideToolbar();
                        switchSubview(activeSubview);
                    } else {
                        showEmptyState();
                    }
                }
            });
        });

        // Bind deselect
        const deselectBtn = document.getElementById('btn-deselect-agent');
        if (deselectBtn) {
            deselectBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                deselectAgent();
            });
        }
    }

    // ─── Toolbar management ───

    function showToolbar() {
        const toolbar = document.getElementById('agent-toolbar');
        toolbar.classList.remove('hidden');

        toolbar.querySelectorAll('.agent-subview-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.subview === activeSubview);
            // Re-bind since toolbar HTML is static
            btn.onclick = () => switchSubview(btn.dataset.subview);
        });

        if (window.lucide) lucide.createIcons({ nodes: [toolbar] });
    }

    function hideToolbar() {
        document.getElementById('agent-toolbar').classList.add('hidden');
    }

    // ─── Sub-view management ───

    function hideAllSubviews() {
        document.getElementById('panel-empty-state')?.classList.add('hidden');
        document.getElementById('subview-chat').classList.add('hidden');
        document.getElementById('subview-edit').classList.add('hidden');
        document.getElementById('subview-tasks').classList.add('hidden');
        document.getElementById('subview-desk').classList.add('hidden');
        document.getElementById('subview-diagnostics')?.classList.add('hidden');
        document.getElementById('tab-activity').classList.remove('active');
    }

    function showEmptyState() {
        hideAllSubviews();
        hideToolbar();
        const empty = document.getElementById('panel-empty-state');
        if (empty) empty.classList.remove('hidden');
    }

    function switchSubview(subview) {
        activeSubview = subview;
        hideAllSubviews();

        // Close diagnostic detail panel if switching away from diagnostics
        if (subview !== 'diagnostics' && typeof DiagnosticsView !== 'undefined') {
            DiagnosticsView.closeDetail();
        }

        // Update toolbar active state
        document.querySelectorAll('.agent-subview-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.subview === subview);
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
        if (!selectedAgent || data.agent_id !== selectedAgent.id) return;
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

    async function renderDesk(path = '/me') {
        const container = document.getElementById('subview-desk');
        if (!selectedAgent || !container) return;
        activeDeskPath = path || '/me';
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
            if (payload.kind === 'file') {
                renderDeskFile(container, payload);
                return;
            }
            renderDeskDirectory(container, payload);
        } catch (err) {
            container.innerHTML = `
                <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                    Failed to load desk contents.
                </div>`;
            console.error('[AgentContext] Desk load failed:', err);
        }
    }

    function renderDeskDirectory(container, payload) {
        const sections = Array.isArray(payload.sections) ? payload.sections : null;
        const entries = Array.isArray(payload.entries) ? payload.entries : null;
        const breadcrumbs = renderDeskBreadcrumbs(payload.breadcrumbs || []);
        let html = `
            <div class="space-y-4">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <p class="text-xs uppercase tracking-wide text-bm-muted">Desk Browser</p>
                        <h3 class="text-sm font-semibold mt-1">${BossModUtils.escapeHtml(payload.name || 'Desk')}</h3>
                        <div class="text-xs text-bm-muted mt-1">${breadcrumbs}</div>
                    </div>
                    <div class="flex items-center gap-2">
                        <button type="button" id="desk-open-folder-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Open Folder
                        </button>
                        <button type="button" id="desk-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                </div>`;

        if (sections) {
            html += sections.map(section => `
                <div class="space-y-2">
                    <h4 class="text-xs font-semibold uppercase tracking-wide text-bm-muted">${BossModUtils.escapeHtml(section.title)}</h4>
                    ${renderDeskEntryList(section.entries || [], { emptyLabel: `No ${String(section.title || '').toLowerCase()} yet.` })}
                </div>
            `).join('');
        } else {
            html += renderDeskEntryList(entries || [], { emptyLabel: 'This folder is empty.' });
        }

        html += '</div>';
        container.innerHTML = html;
        bindDeskInteractions(container);
    }

    function renderDeskFile(container, payload) {
        const artifact = payload.artifact || null;
        const breadcrumbs = renderDeskBreadcrumbs(payload.breadcrumbs || []);
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
        return breadcrumbs.map(item => `
            <button type="button" class="desk-crumb hover:underline" data-path="${BossModUtils.escapeHtml(item.path)}">
                ${BossModUtils.escapeHtml(item.label)}
            </button>
        `).join(' / ');
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
            refreshBtn.addEventListener('click', () => renderDesk(activeDeskPath));
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
        if (!path || path === '/me' || path === '/projects') return '/me';
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
        handleWorldUpdate,
    };
})();

document.addEventListener('DOMContentLoaded', AgentContext.init);
