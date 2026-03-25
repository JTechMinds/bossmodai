/**
 * BossMod AI — Shared channels view.
 *
 * Displays channel list, transcript, participants, and compose box.
 */

const ChannelsView = (() => {
    let channels = [];
    let selectedChannelId = null;
    let activeContainer = null;

    async function loadChannels() {
        const res = await fetch('/api/channels', { cache: 'no-store' });
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
        const res = await fetch(`/api/channels/${channelId}`, { cache: 'no-store' });
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
                            <p class="text-xs uppercase tracking-wide text-bm-muted">Channels</p>
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
                    Create a channel from the Company tab to start broadcasting.
                </div>`;
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
                        <span class="font-medium truncate">${BossModUtils.escapeHtml(channel.name || 'Channel')}</span>
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
            detailEl.innerHTML = `
                <div class="p-4 text-sm text-bm-muted text-center mt-8">
                    No shared channel selected.
                </div>`;
            return;
        }

        detailEl.innerHTML = `
            <div class="p-4 text-sm text-bm-muted text-center mt-8">
                Loading channel...
            </div>`;

        try {
            const payload = await loadChannelDetail(selectedChannelId);
            renderChannelDetail(detailEl, payload.channel, payload.messages || []);
        } catch (err) {
            console.error('[ChannelsView] Failed to load channel detail:', err);
            detailEl.innerHTML = `
                <div class="p-4">
                    <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                        Failed to load channel contents.
                    </div>
                </div>`;
        }
    }

    function renderChannelDetail(detailEl, channel, messages) {
        const members = Array.isArray(channel.members) ? channel.members : [];
        detailEl.innerHTML = `
            <div class="h-full flex flex-col">
                <div class="p-4 border-b border-bm-border shrink-0">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <h3 class="text-sm font-semibold">${BossModUtils.escapeHtml(channel.name || 'Channel')}</h3>
                            <p class="text-xs text-bm-muted mt-1">${members.length} participants</p>
                        </div>
                    </div>
                    <div class="mt-3 flex flex-wrap gap-2">
                        ${members.map(member => `
                            <span class="inline-flex items-center gap-1.5 rounded-full border border-bm-border bg-white px-2 py-1 text-xs text-bm-text">
                                <span class="w-2 h-2 rounded-full ${BossModUtils.getStatusDot(member.status || 'idle', member.currentActivityKind)}"></span>
                                ${BossModUtils.escapeHtml(member.name || 'Unknown')}
                            </span>
                        `).join('') || '<span class="text-xs text-bm-muted">No members</span>'}
                    </div>
                </div>
                <div id="channel-messages" class="flex-1 min-h-0 overflow-y-auto p-4">
                    ${messages.length ? '' : '<div class="text-bm-muted text-sm text-center mt-8">No channel messages yet.</div>'}
                </div>
                <div class="p-3 border-t border-bm-border shrink-0">
                    <div class="flex gap-2 items-end">
                        <textarea id="channel-input" rows="1"
                                  placeholder="Send a message to everyone in this channel..."
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
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        bindSend(channel.id);
        if (window.lucide) lucide.createIcons({ nodes: [detailEl] });
    }

    function appendChannelMessage(messagesEl, message) {
        if (!messagesEl || !message) return;
        const authorType = message.author_type || 'agent';
        const bubbleClass =
            authorType === 'human'
                ? 'from-human'
                : (authorType === 'system' ? 'from-system' : 'from-agent');

        const wrapper = document.createElement('div');
        wrapper.className = `chat-msg ${bubbleClass} mb-2`;

        const label = document.createElement('div');
        label.className = 'text-[11px] font-medium opacity-70 mb-1';
        label.textContent = message.author_name || 'Unknown';
        wrapper.appendChild(label);

        const body = document.createElement('div');
        body.innerText = message.content || '';
        wrapper.appendChild(body);

        messagesEl.appendChild(wrapper);
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

            try {
                const res = await fetch(`/api/channels/${channelId}/messages`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: text }),
                });
                if (!res.ok) {
                    throw new Error(await res.text());
                }
            } catch (err) {
                console.error('[ChannelsView] Failed to send channel message:', err);
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

    function handleChannelMessage(data) {
        if (!data?.channel_id) return;
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
        if (selectedChannelId === data.channel_id && activeContainer) {
            void renderSelectedChannel(activeContainer.querySelector('#channel-detail'));
        }
    }

    function handleChannelUpdated(channelSummary) {
        if (!channelSummary?.id) return;
        const existingIndex = channels.findIndex(item => item.id === channelSummary.id);
        if (existingIndex >= 0) {
            channels.splice(existingIndex, 1, channelSummary);
        } else {
            channels.unshift(channelSummary);
        }
        if (!selectedChannelId) {
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
    };
})();
