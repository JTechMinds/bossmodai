/**
 * BossMod AI — In-place thread pane updates.
 *
 * Keeps the Threads shell mounted across switches and live appends.
 * Cache is last-loaded transcript per thread so a re-click is not cold.
 */

const ChannelThreadDom = (() => {
    function isMounted(container) {
        return Boolean(
            container
            && container.querySelector('#channel-messages')
            && container.querySelector('#channel-input')
        );
    }

    function messageKey(message) {
        return String(message?.id || message?.message_id || '').trim();
    }

    function createCache() {
        const items = new Map();

        function remember(channelId, payload) {
            const id = String(channelId || '').trim();
            if (!id || !payload) return null;
            const messages = Array.isArray(payload.messages) ? payload.messages.slice() : [];
            const entry = { channel: payload.channel || null, messages };
            items.set(id, entry);
            return entry;
        }

        function recall(channelId) {
            const id = String(channelId || '').trim();
            if (!id) return null;
            const entry = items.get(id);
            if (!entry) return null;
            return {
                channel: entry.channel,
                messages: entry.messages.slice(),
            };
        }

        function forget(channelId) {
            const id = String(channelId || '').trim();
            if (!id) return false;
            return items.delete(id);
        }

        function append(channelId, message) {
            const id = String(channelId || '').trim();
            if (!id || !message) return false;
            const entry = items.get(id);
            if (!entry) return false;
            const key = messageKey(message);
            if (key && entry.messages.some((item) => messageKey(item) === key)) {
                return false;
            }
            entry.messages.push(message);
            return true;
        }

        return { remember, recall, forget, append };
    }

    function updateChrome(container, channel) {
        if (!container || !channel) return;
        container.dataset.channelId = channel.id || '';
        const shell = container.querySelector('[data-channel-id]');
        if (shell) shell.dataset.channelId = channel.id || '';
        const title = container.querySelector('#channel-title');
        if (title) title.textContent = channel.name || 'Thread';
        const members = Array.isArray(channel.members) ? channel.members : [];
        const count = container.querySelector('#channel-member-count');
        if (count) count.textContent = `${members.length} participants`;
        const archived = channel.status === 'archived';
        container.dataset.threadStatus = archived ? 'archived' : 'active';
        setHidden(container.querySelector('#channel-archive-btn'), archived);
        setHidden(container.querySelector('#channel-reopen-btn'), !archived);
        const input = container.querySelector('#channel-input');
        const sendBtn = container.querySelector('#channel-send');
        if (input) {
            input.disabled = archived;
            input.placeholder = archived
                ? 'Archived — reopen to post again.'
                : 'Send a message to everyone in this thread...';
        }
        if (sendBtn) sendBtn.disabled = archived;
        return members;
    }

    function setHidden(el, hide) {
        if (!el) return;
        el.hidden = Boolean(hide);
        const classes = String(el.className || '').split(/\s+/).filter(Boolean).filter((cls) => cls !== 'hidden');
        if (hide) classes.push('hidden');
        el.className = classes.join(' ');
    }

    function replaceTranscript(messagesEl) {
        if (!messagesEl) return;
        if (typeof messagesEl.replaceChildren === 'function') {
            messagesEl.replaceChildren();
        } else {
            messagesEl.innerHTML = '';
        }
    }

    function showEmptyTranscript(messagesEl) {
        if (!messagesEl) return;
        messagesEl.innerHTML = '<div class="text-bm-muted text-sm text-center mt-8">No thread messages yet.</div>';
    }

    return {
        isMounted,
        messageKey,
        createCache,
        updateChrome,
        replaceTranscript,
        showEmptyTranscript,
    };
})();
