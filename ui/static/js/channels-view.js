/**
 * BossMod AI — Shared channels view.
 *
 * Displays channel list, transcript, participants, and compose box.
 */

const ChannelsView = (() => {
    let channels = [];
    let selectedChannelId = null;
    let activeContainer = null;
    const presence = BossModUtils.createChannelPresenceController();
    const threadCache = ChannelThreadDom.createCache();
    const drafts = new Map();
    const detailLoad = BossModUtils.createLoadGeneration();

    async function loadChannels() {
        const res = await apiFetch('/api/channels', { cache: 'no-store' });
        if (!res.ok) {
            throw new Error(await res.text());
        }
        channels = await res.json();
        if (!selectedChannelId && channels.length) {
            selectedChannelId = channels[0].id;
        }
        if (selectedChannelId && !channels.find(item => item.id === selectedChannelId)) {
            selectedChannelId = channels[0]?.id || null;
        }
        return channels;
    }

    async function loadChannelDetail(channelId) {
        const res = await apiFetch(`/api/channels/${channelId}`, { cache: 'no-store' });
        if (!res.ok) {
            throw new Error(await res.text());
        }
        return res.json();
    }

    async function render(container) {
        activeContainer = container;
        if (!container) return;

        container.innerHTML = `
            <div class="h-full flex flex-col">
                <div class="p-4 border-b border-bm-border shrink-0">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <p class="text-xs uppercase tracking-wide text-bm-muted">Threads</p>
                            <h3 class="text-sm font-semibold mt-1">Shared Threads</h3>
                            <p class="text-xs text-bm-muted mt-1">Broadcast to selected agents and let them reply in order.</p>
                        </div>
                        <button id="channels-refresh-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Refresh
                        </button>
                    </div>
                </div>
                <div class="flex-1 min-h-0 flex flex-col">
                    <div id="channels-list" class="p-3 border-b border-bm-border shrink-0 max-h-56 overflow-y-auto"></div>
                    <div id="channel-detail" class="flex-1 min-h-0 overflow-y-auto"></div>
                </div>
            </div>`;

        container.querySelector('#channels-refresh-btn')?.addEventListener('click', () => {
            void render(container);
        });

        try {
            await loadChannels();
            renderChannelList(container.querySelector('#channels-list'));
            await renderSelectedChannel(container.querySelector('#channel-detail'));
        } catch (err) {
            console.error('[ChannelsView] Failed to load channels:', err);
            const detailEl = container.querySelector('#channel-detail');
            if (detailEl) {
                detailEl.innerHTML = `
                    <div class="p-4">
                        <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                            Failed to load channels.
                        </div>
                    </div>`;
            }
        }

        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderChannelList(listEl) {
        if (!listEl) return;
        if (!channels.length) {
            listEl.innerHTML = `
                <div class="text-sm text-bm-muted text-center py-6">
                    <p>Tick agents in Directory, then Create Thread to start broadcasting.</p>
                    <button type="button" id="channels-create-channel-btn"
                            class="mt-3 px-3 py-2 rounded-lg bg-bm-accent text-white text-xs font-medium hover:bg-bm-accent-hover transition-colors">
                        Create thread
                    </button>
                </div>`;
            listEl.querySelector('#channels-create-channel-btn')?.addEventListener('click', () => {
                if (typeof DockManager !== 'undefined' && typeof DockManager.open === 'function') {
                    DockManager.open('directory');
                }
            });
            return;
        }

        listEl.innerHTML = channels.map(channel => {
            const active = channel.id === selectedChannelId;
            const latest = channel.latest_message?.content || '';
            const updatedAt = channel.latest_message?.created_at || channel.updated_at || '';
            return `
                <button type="button"
                        class="channels-list-item w-full text-left rounded-xl border ${active ? 'border-bm-accent bg-blue-50/50' : 'border-bm-border bg-white'} p-3 mb-2 hover:bg-slate-50 transition-colors"
                        data-channel-id="${BossModUtils.escapeHtml(channel.id)}">
                    <div class="flex items-center justify-between gap-2">
                        <span class="font-medium truncate">${BossModUtils.escapeHtml(channel.name || 'Thread')}</span>
                        <span class="text-[11px] text-bm-muted shrink-0">${channel.member_count || 0} agents</span>
                    </div>
                    <p class="text-xs text-bm-muted mt-1 line-clamp-2">${BossModUtils.escapeHtml(latest || 'No messages yet')}</p>
                    <p class="text-[11px] text-bm-muted mt-2">${formatTimestamp(updatedAt)}</p>
                </button>`;
        }).join('');

        listEl.querySelectorAll('.channels-list-item').forEach(button => {
            button.addEventListener('click', async () => {
                const channelId = button.dataset.channelId;
                if (!channelId) return;
                selectedChannelId = channelId;
                renderChannelList(listEl);
                await renderSelectedChannel(activeContainer?.querySelector('#channel-detail'));
            });
        });
    }

    async function renderSelectedChannel(detailEl) {
        if (!detailEl) return;
        if (!selectedChannelId) {
            delete detailEl.dataset.channelId;
            detailEl.innerHTML = `
                <div class="p-4 text-sm text-bm-muted text-center mt-8">
                    No shared thread selected.
                </div>`;
            return;
        }

        const loadId = detailLoad.next();
        const cached = threadCache.recall(selectedChannelId);
        const summary = channels.find(item => item.id === selectedChannelId);
        const keepShell = ChannelThreadDom.isMounted(detailEl);
        if (keepShell || cached) {
            const channel = cached?.channel || summary;
            if (channel) {
                applyThreadDetail(detailEl, channel, cached?.messages || [], { keepShell });
            }
            await refreshThreadDetail(detailEl, selectedChannelId, loadId);
            return;
        }

        delete detailEl.dataset.channelId;
        detailEl.innerHTML = `
            <div class="p-4 text-sm text-bm-muted text-center mt-8">
                Loading thread...
            </div>`;
        await refreshThreadDetail(detailEl, selectedChannelId, loadId);
    }

    async function refreshThreadDetail(detailEl, channelId, loadId) {
        try {
            const payload = await loadChannelDetail(channelId);
            if (!detailLoad.isCurrent(loadId) || selectedChannelId !== channelId) return;
            const messages = payload.messages || [];
            threadCache.remember(channelId, { channel: payload.channel, messages });
            applyThreadDetail(detailEl, payload.channel, messages, {
                keepShell: ChannelThreadDom.isMounted(detailEl),
            });
        } catch (err) {
            if (!detailLoad.isCurrent(loadId) || selectedChannelId !== channelId) return;
            console.error('[ChannelsView] Failed to load channel detail:', err);
            if (ChannelThreadDom.isMounted(detailEl)) return;
            detailEl.innerHTML = `
                <div class="p-4">
                    <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                        Failed to load thread contents.
                    </div>
                </div>`;
        }
    }

    function applyThreadDetail(detailEl, channel, messages, { keepShell } = {}) {
        if (!detailEl || !channel) return;
        const sameThread = keepShell && ChannelThreadDom.isMounted(detailEl)
            && (detailEl.dataset.channelId || '') === (channel.id || '');
        if (keepShell && ChannelThreadDom.isMounted(detailEl)) {
            if (!sameThread) stashDraft(detailEl);
            ChannelThreadDom.updateChrome(detailEl, channel);
            paintMemberChips(detailEl, channel);
            const messagesEl = detailEl.querySelector('#channel-messages');
            if (sameThread) {
                syncThreadMessages(messagesEl, messages);
            } else {
                paintTranscript(messagesEl, messages);
                restoreDraft(detailEl, channel.id);
            }
            renderChannelThinking(messagesEl, channel.id);
            bindSend(channel.id);
            bindArchive(channel.id);
            if (window.lucide) lucide.createIcons({ nodes: [detailEl] });
            return;
        }
        renderChannelDetail(detailEl, channel, messages);
    }

    function paintTranscript(messagesEl, messages) {
        if (!messagesEl) return;
        ChannelThreadDom.replaceTranscript(messagesEl);
        const rows = Array.isArray(messages) ? messages : [];
        if (!rows.length) {
            ChannelThreadDom.showEmptyTranscript(messagesEl);
            return;
        }
        for (const message of rows) {
            appendChannelMessage(messagesEl, message);
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function syncThreadMessages(messagesEl, messages) {
        if (!messagesEl) return;
        let appended = false;
        for (const message of Array.isArray(messages) ? messages : []) {
            if (appendChannelMessage(messagesEl, message, { skipIfPresent: true })) {
                appended = true;
            }
        }
        if (appended) messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function stashDraft(detailEl) {
        const input = detailEl?.querySelector('#channel-input');
        const channelId = detailEl?.dataset.channelId;
        if (!input || !channelId) return;
        drafts.set(channelId, input.value);
    }

    function restoreDraft(detailEl, channelId) {
        const input = detailEl?.querySelector('#channel-input');
        if (!input) return;
        input.value = drafts.get(channelId) || '';
        input.style.height = 'auto';
    }

    function paintMemberChips(detailEl, channel) {
        const chips = detailEl?.querySelector('#channel-members');
        if (!chips) return;
        chips.innerHTML = renderMemberChips(channel);
    }

    function renderMemberChips(channel) {
        const members = Array.isArray(channel?.members) ? channel.members : [];
        if (!members.length) {
            return '<span class="text-xs text-bm-muted">No members</span>';
        }
        return members.map(member => {
            const thinking = presence.has(channel.id, member.id);
            return `
                            <span class="inline-flex items-center gap-1.5 rounded-full border ${thinking ? 'border-amber-300 bg-amber-50' : 'border-bm-border bg-white'} px-2 py-1 text-xs text-bm-text"
                                  data-member-id="${BossModUtils.escapeHtml(member.id || '')}">
                                <span class="w-2 h-2 rounded-full ${thinking ? 'bg-amber-500' : BossModUtils.getStatusDot(member.status || 'idle', member.currentActivityKind)}"></span>
                                ${BossModUtils.escapeHtml(member.name || 'Unknown')}
                                ${thinking ? '<span class="channel-member-thinking text-[11px] text-amber-800 italic">thinking</span>' : ''}
                            </span>`;
        }).join('');
    }

    function renderChannelDetail(detailEl, channel, messages) {
        const members = Array.isArray(channel.members) ? channel.members : [];
        detailEl.dataset.channelId = channel.id || '';
        detailEl.innerHTML = `
            <div class="h-full flex flex-col" data-channel-id="${BossModUtils.escapeHtml(channel.id || '')}">
                <div class="p-4 border-b border-bm-border shrink-0">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <h3 id="channel-title" class="text-sm font-semibold">${BossModUtils.escapeHtml(channel.name || 'Thread')}</h3>
                            <p id="channel-member-count" class="text-xs text-bm-muted mt-1">${members.length} participants</p>
                        </div>
                        <button type="button" id="channel-archive-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors">
                            Archive
                        </button>
                    </div>
                    <div id="channel-members" class="mt-3 flex flex-wrap gap-2">
                        ${renderMemberChips(channel)}
                    </div>
                </div>
                <div id="channel-messages" class="flex-1 min-h-0 overflow-y-auto p-4">
                    ${messages.length ? '' : '<div class="text-bm-muted text-sm text-center mt-8">No thread messages yet.</div>'}
                </div>
                <div class="p-3 border-t border-bm-border shrink-0">
                    <div class="flex gap-2 items-end">
                        <textarea id="channel-input" rows="1"
                                  placeholder="Send a message to everyone in this thread..."
                                  class="flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30 focus:border-bm-accent resize-none overflow-hidden"></textarea>
                        <button id="channel-send"
                                class="px-3 py-2 bg-bm-accent text-white rounded-lg hover:bg-bm-accent-hover transition-colors shrink-0">
                            <i data-lucide="send" class="w-4 h-4"></i>
                        </button>
                    </div>
                </div>
            </div>`;

        const messagesEl = detailEl.querySelector('#channel-messages');
        if (messagesEl) {
            for (const message of messages) {
                appendChannelMessage(messagesEl, message);
            }
            renderChannelThinking(messagesEl, channel.id);
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        bindSend(channel.id);
        bindArchive(channel.id);
        threadCache.remember(channel.id, { channel, messages: Array.isArray(messages) ? messages : [] });
        restoreDraft(detailEl, channel.id);
        if (window.lucide) lucide.createIcons({ nodes: [detailEl] });
    }

    function channelMessageKey(message) {
        return ChannelThreadDom.messageKey(message);
    }

    function isChannelDetailMounted(detailEl, channelId) {
        if (!detailEl || !channelId || !ChannelThreadDom.isMounted(detailEl)) return false;
        const mountedId = detailEl.dataset.channelId || detailEl.querySelector('[data-channel-id]')?.dataset.channelId;
        return mountedId === channelId;
    }

    function normalizeLiveMessage(data) {
        return {
            id: data.message_id || data.id,
            author_type: data.author_type,
            author_name: data.author_name,
            content: data.content,
            notification_kind: data.notification_kind,
            host_path_consent: data.host_path_consent,
        };
    }

    function appendLiveChannelMessage(messagesEl, data) {
        if (!messagesEl || !data) return false;
        const message = normalizeLiveMessage(data);
        if (!appendChannelMessage(messagesEl, message, { skipIfPresent: true })) {
            return false;
        }
        renderChannelThinking(messagesEl, data.channel_id);
        return true;
    }

    function appendChannelMessage(messagesEl, message, options = {}) {
        if (!messagesEl || !message) return false;
        const key = channelMessageKey(message);
        if (options.skipIfPresent && key && messagesEl.querySelector(`[data-message-id="${CSS.escape(key)}"]`)) {
            return false;
        }
        const empty = messagesEl.querySelector('.text-center');
        if (empty) empty.remove();
        const consent = BossModUtils.isHostPathConsentMessage(message) && message.host_path_consent;
        const authorType = message.author_type || 'agent';
        const bubbleClass =
            authorType === 'human'
                ? 'from-human'
                : (authorType === 'system' ? 'from-system' : 'from-agent');

        const wrapper = document.createElement('div');
        wrapper.className = consent
            ? 'chat-msg host-path-consent-card mb-2'
            : `chat-msg ${bubbleClass} mb-2`;
        if (key) wrapper.dataset.messageId = key;
        if (consent) {
            wrapper.id = `host-path-consent-${message.host_path_consent.id}`;
        }

        const label = document.createElement('div');
        label.className = 'text-[11px] font-medium opacity-70 mb-1';
        label.textContent = message.author_name || 'Unknown';
        wrapper.appendChild(label);

        if (consent) {
            BossModUtils.renderHostPathConsentCard(wrapper, message.host_path_consent);
        } else {
            const body = document.createElement('div');
            body.innerText = message.content || '';
            wrapper.appendChild(body);
        }

        messagesEl.appendChild(wrapper);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return true;
    }

    function renderChannelThinking(messagesEl, channelId) {
        if (!messagesEl) return;
        const existing = messagesEl.querySelector('#channel-thinking-indicators');
        if (existing) existing.remove();
        const working = presence.list(channelId);
        if (!working.length) return;
        const wrap = document.createElement('div');
        wrap.id = 'channel-thinking-indicators';
        wrap.className = 'space-y-1 mt-2';
        for (const member of working) {
            const row = document.createElement('div');
            row.className = 'chat-msg from-agent mb-1 text-bm-muted italic';
            row.dataset.agentId = member.agentId;
            row.textContent = `${member.name} is thinking...`;
            wrap.appendChild(row);
        }
        messagesEl.appendChild(wrap);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function bindSend(channelId) {
        const sendBtn = document.getElementById('channel-send');
        const input = document.getElementById('channel-input');
        if (!sendBtn || !input || !channelId) return;

        async function handleSend() {
            const text = String(input.value || '').trim();
            if (!text) return;
            input.value = '';
            input.style.height = 'auto';

            const channel = channels.find(item => item.id === channelId);
            const members = Array.isArray(channel?.members) ? channel.members : [];
            for (const member of members) {
                if (member?.id) presence.start(channelId, member.id, member.name);
            }
            paintPresence(channelId);

            try {
                const res = await apiFetch(`/api/channels/${channelId}/messages`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: text }),
                });
                if (!res.ok) {
                    throw new Error(await res.text());
                }
            } catch (err) {
                console.error('[ChannelsView] Failed to send channel message:', err);
                for (const member of members) {
                    if (member?.id) presence.stop(channelId, member.id);
                }
                paintPresence(channelId);
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

    function bindArchive(channelId) {
        const archiveBtn = document.getElementById('channel-archive-btn');
        if (!archiveBtn || !channelId) return;
        archiveBtn.disabled = false;
        archiveBtn.onclick = async () => {
            if (!window.confirm('Archive this thread? It will leave the active list.')) return;
            archiveBtn.disabled = true;
            try {
                const res = await apiFetch(`/api/channels/${channelId}`, { method: 'DELETE' });
                if (!res.ok) {
                    throw new Error(await res.text());
                }
                const summary = await res.json();
                handleChannelUpdated(summary);
            } catch (err) {
                console.error('[ChannelsView] Failed to archive thread:', err);
                archiveBtn.disabled = false;
            }
        };
    }

    function handleChannelMessage(data) {
        if (!data?.channel_id) return;
        threadCache.append(data.channel_id, normalizeLiveMessage(data));
        const channel = channels.find(item => item.id === data.channel_id);
        if (channel) {
            channel.latest_message = {
                content: data.content,
                author_name: data.author_name,
                created_at: data.created_at,
            };
            channel.updated_at = data.created_at || channel.updated_at;
        }
        if (activeContainer) {
            renderChannelList(activeContainer.querySelector('#channels-list'));
        }
        if (data.author_type === 'agent' && data.author_agent_id) {
            presence.stop(data.channel_id, data.author_agent_id);
        }
        if (selectedChannelId === data.channel_id && activeContainer) {
            const detailEl = activeContainer.querySelector('#channel-detail');
            if (isChannelDetailMounted(detailEl, data.channel_id)) {
                appendLiveChannelMessage(detailEl.querySelector('#channel-messages'), data);
                return;
            }
            void renderSelectedChannel(detailEl);
        }
    }

    function handleChannelPresence(data) {
        if (!data?.channel_id || !data?.agent_id) return;
        if (data.phase === 'thinking') {
            presence.start(data.channel_id, data.agent_id, data.agent_name);
        } else {
            presence.stop(data.channel_id, data.agent_id);
        }
        paintPresence(data.channel_id);
    }

    function paintPresence(channelId) {
        if (!channelId || selectedChannelId !== channelId || !activeContainer) return;
        const detailEl = activeContainer.querySelector('#channel-detail');
        if (!detailEl) return;
        detailEl.querySelectorAll('[data-member-id]').forEach((chip) => {
            const thinking = presence.has(channelId, chip.dataset.memberId);
            chip.classList.toggle('border-amber-300', thinking);
            chip.classList.toggle('bg-amber-50', thinking);
            chip.classList.toggle('border-bm-border', !thinking);
            chip.classList.toggle('bg-white', !thinking);
            let label = chip.querySelector('.channel-member-thinking');
            if (thinking && !label) {
                label = document.createElement('span');
                label.className = 'channel-member-thinking text-[11px] text-amber-800 italic';
                label.textContent = 'thinking';
                chip.appendChild(label);
            } else if (!thinking && label) {
                label.remove();
            }
        });
        renderChannelThinking(detailEl.querySelector('#channel-messages'), channelId);
    }

    function handleChannelUpdated(channelSummary) {
        if (!channelSummary?.id) return;
        const archived = channelSummary.status === 'archived';
        const existingIndex = channels.findIndex(item => item.id === channelSummary.id);
        if (archived) {
            threadCache.forget(channelSummary.id);
            drafts.delete(channelSummary.id);
            if (existingIndex >= 0) channels.splice(existingIndex, 1);
        } else if (existingIndex >= 0) {
            channels.splice(existingIndex, 1, channelSummary);
        } else {
            channels.unshift(channelSummary);
        }
        if (archived && selectedChannelId === channelSummary.id) {
            selectedChannelId = channels[0]?.id || null;
            if (activeContainer) {
                renderChannelList(activeContainer.querySelector('#channels-list'));
                void renderSelectedChannel(activeContainer.querySelector('#channel-detail'));
            }
            return;
        }
        if (!selectedChannelId && !archived) {
            selectedChannelId = channelSummary.id;
        }
        if (activeContainer) {
            renderChannelList(activeContainer.querySelector('#channels-list'));
        }
    }

    async function openChannel(channelId) {
        selectedChannelId = channelId;
        if (activeContainer) {
            await render(activeContainer);
        }
    }

    function formatTimestamp(value) {
        if (!value) return '';
        try {
            return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch {
            return '';
        }
    }

    return {
        render,
        openChannel,
        handleChannelMessage,
        handleChannelUpdated,
        handleChannelPresence,
    };
})();
