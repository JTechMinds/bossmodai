/**
 * BossMod AI — Shared frontend utilities.
 *
 * Common helpers used across multiple modules to avoid duplication.
 */

const BossModUtils = (() => {

    // ─── HTML escaping ───

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ─── Agent data normalization ───

    function normalizeAgent(w) {
        return {
            id: w.id,
            name: w.name,
            x: w.x ?? 0,
            y: w.y ?? 0,
            color: w.color || '#3b82f6',
            status: w.status || 'idle',
            currentActivityKind: w.currentActivityKind || null,
            boundTaskId: w.boundTaskId || null,
            idle_since: w.idle_since || null,
        };
    }

    // ─── Status color mappings ───

    const STATUS_CONFIG = {
        work_active:   { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500' },
        social_active: { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
        in_transit:    { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500' },
        idle:          { hex: '#94a3b8', classes: 'bg-slate-50 text-slate-600',     dot: 'bg-slate-400' },
    };

    const ACTIVITY_CONFIG = {
        assignment:   { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500', label: 'assignment' },
        break:        { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'break' },
        conversation: { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'conversation' },
        meeting:      { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500', label: 'meeting' },
        movement:     { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500', label: 'moving' },
        social:       { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'social' },
        work:         { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500', label: 'working' },
    };

    const DEFAULT_STATUS = STATUS_CONFIG.idle;

    function getDisplayState(status, currentActivityKind = null) {
        return ACTIVITY_CONFIG[currentActivityKind] || STATUS_CONFIG[status] || DEFAULT_STATUS;
    }

    function getStatusColor(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).hex;
    }

    function getStatusClasses(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).classes;
    }

    function getStatusDot(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).dot;
    }

    function getStatusLabel(status, currentActivityKind = null) {
        if (currentActivityKind && ACTIVITY_CONFIG[currentActivityKind]) {
            return ACTIVITY_CONFIG[currentActivityKind].label;
        }
        return status || 'idle';
    }

    // ─── Formatting helpers ───

    function formatRelativeTime(isoString) {
        if (!isoString) return '';
        const now = Date.now();
        const then = new Date(isoString).getTime();
        if (isNaN(then)) return '';
        const diffMs = now - then;
        if (diffMs < 0) return 'just now';
        const seconds = Math.floor(diffMs / 1000);
        if (seconds < 60) return 'just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `${days}d ago`;
        const months = Math.floor(days / 30);
        if (months < 12) return `${months}mo ago`;
        return `${Math.floor(months / 12)}y ago`;
    }

    function formatNumber(n) {
        if (n == null || isNaN(n)) return '0';
        const num = Number(n);
        if (num >= 1_000_000) return (num / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (num >= 1_000) return (num / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
        return String(num);
    }

    // ─── Modal factory ───

    function createModal({ maxWidth = 'max-w-lg', onClose = null } = {}) {
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4';

        const panel = document.createElement('div');
        panel.className = `w-full ${maxWidth} max-h-[85vh] flex flex-col rounded-xl border border-bm-border bg-white shadow-xl`;
        overlay.appendChild(panel);

        function close() {
            overlay.remove();
            document.removeEventListener('keydown', onKeyDown);
            if (typeof onClose === 'function') onClose();
        }

        function onKeyDown(e) {
            if (e.key === 'Escape') close();
        }

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });

        document.addEventListener('keydown', onKeyDown);
        document.body.appendChild(overlay);

        return { overlay, panel, close };
    }

    // ─── Overlay panel open/close ───

    function openOverlay(overlayId) {
        const overlay = document.getElementById(overlayId);
        if (!overlay) return;
        overlay.classList.remove('hidden');
        overlay.offsetHeight; // force reflow
        overlay.classList.add('open');
    }

    function closeOverlay(overlayId, panelId) {
        const overlay = document.getElementById(overlayId);
        const panel = document.getElementById(panelId);
        if (!overlay || !panel) return;

        overlay.classList.remove('open');

        let hidden = false;
        const hide = () => {
            if (hidden) return;
            hidden = true;
            if (!overlay.classList.contains('open')) {
                overlay.classList.add('hidden');
            }
        };
        panel.addEventListener('transitionend', hide, { once: true });
        setTimeout(hide, 400);
    }

    return {
        escapeHtml,
        normalizeAgent,
        getStatusColor,
        getStatusClasses,
        getStatusDot,
        getStatusLabel,
        formatRelativeTime,
        formatNumber,
        createModal,
        openOverlay,
        closeOverlay,
    };
})();
