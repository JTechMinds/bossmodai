/**
 * BossMod AI — Activity log panel.
 *
 * Receives activity events via WebSocket and displays them
 * in the Activity tab. Events are prepended (newest first).
 */

const ActivityLog = (() => {
    let container = null;

    // ─── Event type → Lucide icon + color ───

    const EVENT_ICONS = {
        agent_created:  { icon: 'user-plus',    color: 'text-emerald-500' },
        agent_updated:  { icon: 'edit-3',       color: 'text-blue-500' },
        agent_deleted:  { icon: 'user-minus',   color: 'text-red-500' },
        agent_moved:    { icon: 'move',         color: 'text-amber-500' },
        status_changed: { icon: 'activity',     color: 'text-purple-500' },
        task_created:   { icon: 'plus-circle',  color: 'text-emerald-500' },
        task_updated:   { icon: 'check-circle', color: 'text-blue-500' },
        message_sent:   { icon: 'message-circle', color: 'text-blue-500' },
        connected:      { icon: 'wifi',         color: 'text-emerald-500' },
        disconnected:   { icon: 'wifi-off',     color: 'text-red-500' },
    };

    const DEFAULT_ICON = { icon: 'info', color: 'text-bm-muted' };

    // ─── Initialization ───

    function init() {
        container = document.querySelector('#tab-activity .overflow-y-auto');
        if (!container) return;
        console.log('[ActivityLog] Initialized');
    }

    // ─── Add a single event entry ───

    function addEntry(event) {
        if (!container) return;

        // Remove empty state placeholder if present
        const emptyEl = container.querySelector('.text-center');
        if (emptyEl) emptyEl.remove();

        const { icon, color } = EVENT_ICONS[event.event] || DEFAULT_ICON;
        const time = formatTime(event.timestamp);

        const entry = document.createElement('div');
        entry.className = 'activity-entry';
        entry.innerHTML = `
            <div class="flex items-start gap-2">
                <div class="shrink-0 mt-0.5">
                    <i data-lucide="${icon}" class="w-4 h-4 ${color}"></i>
                </div>
                <div class="flex-1 min-w-0">
                    <p class="text-sm">${BossModUtils.escapeHtml(event.detail)}</p>
                    <p class="text-xs text-bm-muted mt-0.5">${time}</p>
                </div>
            </div>
        `;

        container.prepend(entry);

        // Cap at 100 visible entries
        while (container.children.length > 100) {
            container.removeChild(container.lastChild);
        }

        // Render new Lucide icons in this entry
        if (window.lucide) lucide.createIcons({ nodes: [entry] });
    }

    // ─── Load historical events on connect ───

    function loadHistory(events) {
        if (!container || !events || !events.length) return;

        container.innerHTML = '';

        // events come oldest-first from server; reverse to show newest first
        const reversed = [...events].reverse();
        for (const event of reversed) {
            addEntry(event);
        }
    }

    // ─── Helpers ───

    function formatTime(isoString) {
        if (!isoString) return '';
        try {
            const date = new Date(isoString);
            return date.toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            });
        } catch {
            return '';
        }
    }

    // ─── Public API ───

    return {
        init,
        addEntry,
        loadHistory,
    };
})();

document.addEventListener('DOMContentLoaded', ActivityLog.init);
