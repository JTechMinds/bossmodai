/**
 * BossMod AI — Agent context controller.
 *
 * Manages the left panel state: when an agent is selected on the canvas,
 * the panel switches from empty/activity to agent-focused mode with
 * sub-views: Chat, Edit, Tasks.
 */

const AgentContext = (() => {
    let selectedAgent = null;
    let creatingAgent = false;
    let activeSubview = 'chat';

    // ─── Select / Deselect ───

    async function selectAgent(agentData) {
        creatingAgent = false;
        // Fetch full agent details
        try {
            const res = await fetch(`/api/agents/${agentData.id}`);
            if (!res.ok) return;
            selectedAgent = await res.json();
        } catch {
            return;
        }

        activeSubview = 'chat';
        updateTabs();
        showToolbar();
        switchSubview('chat');
    }

    function deselectAgent() {
        selectedAgent = null;
        creatingAgent = false;
        activeSubview = 'chat';
        updateTabs();
        hideToolbar();
        showEmptyState();
    }

    function startCreateAgent() {
        selectedAgent = null;
        creatingAgent = true;
        activeSubview = 'edit';
        updateTabs();
        hideToolbar();
        switchSubview('edit');
    }

    function getSelectedAgent() {
        return selectedAgent;
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

        // Show loading state
        messagesEl.innerHTML = `
            <div class="text-bm-muted text-sm text-center mt-4">
                <p>Loading messages...</p>
            </div>`;

        // Fetch chat history from backend
        let messages = [];
        try {
            const res = await fetch(`/api/agents/${selectedAgent.id}/messages?limit=50`);
            if (res.ok) messages = await res.json();
        } catch { /* fall through to empty state */ }

        messagesEl.innerHTML = '';

        if (messages.length === 0) {
            messagesEl.innerHTML = `
                <div class="text-bm-muted text-sm text-center mt-4">
                    <p>Chat with <strong>${esc(selectedAgent.name)}</strong></p>
                    <p class="text-xs mt-1">Send a message to activate this agent.</p>
                </div>`;
        } else {
            for (const msg of messages) {
                appendChatMessage(msg.content, msg.from);
            }
        }

        bindChatSend();
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

    function appendChatMessage(text, fromType) {
        const messagesEl = document.getElementById('chat-messages');
        // Remove empty state hint if present
        const emptyHint = messagesEl.querySelector('.text-center');
        if (emptyHint) emptyHint.remove();

        const msgDiv = document.createElement('div');
        msgDiv.className = `chat-msg ${fromType === 'human' ? 'from-human' : 'from-agent'} mb-2`;
        // Preserve newlines and whitespace formatting
        msgDiv.innerText = text;
        messagesEl.appendChild(msgDiv);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function handleChatMessage(data) {
        if (!selectedAgent || data.agent_id !== selectedAgent.id) return;
        hideTypingIndicator();
        appendChatMessage(data.content, data.from);
    }

    async function handleChatReset(data) {
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

    // ─── Edit sub-view ───

    async function renderEdit() {
        const container = document.getElementById('subview-edit');
        if (!selectedAgent && !creatingAgent) return;

        if (selectedAgent?.id) {
            try {
                const res = await fetch(`/api/agents/${selectedAgent.id}`, { cache: 'no-store' });
                if (res.ok) {
                    selectedAgent = await res.json();
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
                    selectedAgent = savedAgent;
                    updateTabs();
                    showToolbar();
                    return;
                }

                if (!selectedAgent?.id) return;
                try {
                    const res = await fetch(`/api/agents/${selectedAgent.id}`, { cache: 'no-store' });
                    if (res.ok) {
                        selectedAgent = await res.json();
                        updateTabs();
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
                                    t.status === 'active' ? 'text-blue-600' :
                                    t.status === 'blocked' ? 'text-red-600' : 'text-bm-muted';
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
    };
})();

document.addEventListener('DOMContentLoaded', AgentContext.init);
