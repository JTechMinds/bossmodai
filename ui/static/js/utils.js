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
        openOverlay,
        closeOverlay,
    };
})();
