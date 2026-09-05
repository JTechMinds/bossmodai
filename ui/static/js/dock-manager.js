/**
 * BossMod AI — Center dock manager.
 *
 * Company views (Files / Tasks / Metrics / Org) open as floating windows
 * inside the map workspace. They are not flex siblings of the office map,
 * so they cannot become a squeezed third column beside Activity.
 */

const DockManager = (() => {
    const DOCK_IDS = ['files', 'tasks', 'metrics', 'org'];
    const DEFAULTS = {
        files: { x: 0.08, y: 0.07, w: 0.70, h: 0.78 },
        tasks: { x: 0.14, y: 0.12, w: 0.70, h: 0.78 },
        metrics: { x: 0.20, y: 0.17, w: 0.70, h: 0.78 },
        org: { x: 0.26, y: 0.22, w: 0.70, h: 0.78 },
    };
    const FILL_RECT = { x: 0.02, y: 0.02, w: 0.96, h: 0.96 };
    const MIN_FRAC_W = 0.28;
    const MIN_FRAC_H = 0.28;

    let state = emptyLayout();
    let zCounter = 20;
    let focusedId = null;
    let persist = () => {};
    let drag = null;

    function emptyLayout() {
        const layout = {};
        DOCK_IDS.forEach((id) => {
            layout[id] = { ...DEFAULTS[id], open: false, z: 1, filled: false, prev: null };
        });
        return layout;
    }

    function clamp01(n, min, max) {
        const lo = min == null ? 0 : min;
        const hi = max == null ? 1 : max;
        return Math.min(hi, Math.max(lo, n));
    }

    function clampRect(rect) {
        const w = clamp01(Number(rect.w) || MIN_FRAC_W, MIN_FRAC_W, 1);
        const h = clamp01(Number(rect.h) || MIN_FRAC_H, MIN_FRAC_H, 1);
        const x = clamp01(Number(rect.x) || 0, 0, 1 - w);
        const y = clamp01(Number(rect.y) || 0, 0, 1 - h);
        return { x, y, w, h };
    }

    function normalizeLayout(raw) {
        const layout = emptyLayout();
        if (!raw || typeof raw !== 'object') return layout;
        DOCK_IDS.forEach((id) => {
            const src = raw[id];
            if (!src || typeof src !== 'object') return;
            const geom = clampRect(src);
            layout[id] = {
                ...geom,
                open: Boolean(src.open),
                z: Number.isFinite(src.z) ? src.z : 1,
                filled: Boolean(src.filled),
                prev: src.prev && typeof src.prev === 'object' ? clampRect(src.prev) : null,
            };
            zCounter = Math.max(zCounter, layout[id].z);
        });
        return layout;
    }

    function snapshot() {
        const out = {};
        DOCK_IDS.forEach((id) => {
            out[id] = { ...state[id], prev: state[id].prev ? { ...state[id].prev } : null };
        });
        return out;
    }

    function hostEl() {
        return document.getElementById('dock-layer');
    }

    function windowEl(id) {
        return document.getElementById(`dock-${id}`);
    }

    function applyGeom(id) {
        const win = windowEl(id);
        const rec = state[id];
        if (!win || !rec) return;
        const geom = clampRect(rec);
        rec.x = geom.x;
        rec.y = geom.y;
        rec.w = geom.w;
        rec.h = geom.h;
        win.style.left = `${(geom.x * 100).toFixed(2)}%`;
        win.style.top = `${(geom.y * 100).toFixed(2)}%`;
        win.style.width = `${(geom.w * 100).toFixed(2)}%`;
        win.style.height = `${(geom.h * 100).toFixed(2)}%`;
        win.style.zIndex = String(rec.z || 1);
        win.classList.toggle('hidden', !rec.open);
        win.classList.toggle('is-active', rec.open && focusedId === id);
        win.classList.toggle('is-filled', Boolean(rec.filled));
    }

    function applyAll() {
        DOCK_IDS.forEach(applyGeom);
    }

    function emitChange() {
        persist(snapshot());
        window.dispatchEvent(new CustomEvent('dock-change', { detail: { focused: focusedId, layout: snapshot() } }));
    }

    function focus(id) {
        if (!state[id] || !state[id].open) return;
        focusedId = id;
        state[id].z = ++zCounter;
        DOCK_IDS.forEach(applyGeom);
    }

    function renderDock(id) {
        if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.switchTab === 'function') {
            CompanyDashboard.switchTab(id);
        }
    }

    function open(id) {
        if (!DOCK_IDS.includes(id)) return;
        const firstOpen = !state[id].open;
        state[id].open = true;
        focus(id);
        if (firstOpen) renderDock(id);
        emitChange();
    }

    function close(id) {
        if (!state[id] || !state[id].open) return;
        state[id].open = false;
        state[id].filled = false;
        if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.unmount === 'function') {
            CompanyDashboard.unmount(id);
        }
        if (focusedId === id) {
            focusedId = DOCK_IDS.filter((other) => state[other].open).sort((a, b) => state[b].z - state[a].z)[0] || null;
        }
        applyGeom(id);
        if (focusedId) applyGeom(focusedId);
        emitChange();
    }

    function closeAll() {
        let changed = false;
        DOCK_IDS.forEach((id) => {
            if (!state[id].open) return;
            state[id].open = false;
            state[id].filled = false;
            if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.unmount === 'function') {
                CompanyDashboard.unmount(id);
            }
            applyGeom(id);
            changed = true;
        });
        focusedId = null;
        if (changed) emitChange();
    }

    function fill(id) {
        if (!state[id] || !state[id].open) return;
        if (state[id].filled) {
            const prev = state[id].prev || DEFAULTS[id];
            Object.assign(state[id], clampRect(prev), { filled: false, prev: null });
        } else {
            state[id].prev = { x: state[id].x, y: state[id].y, w: state[id].w, h: state[id].h };
            Object.assign(state[id], FILL_RECT, { filled: true });
        }
        applyGeom(id);
        emitChange();
    }

    function hasOpen() {
        return DOCK_IDS.some((id) => state[id] && state[id].open);
    }

    function getFocused() {
        return focusedId;
    }

    function maybeSnap(id) {
        const rec = state[id];
        if (!rec) return;
        if (rec.x <= 0.02) {
            rec.prev = rec.prev || { x: rec.x, y: rec.y, w: rec.w, h: rec.h };
            Object.assign(rec, { x: 0, y: 0, w: 0.5, h: 1, filled: false });
        } else if (rec.x + rec.w >= 0.98) {
            rec.prev = rec.prev || { x: rec.x, y: rec.y, w: rec.w, h: rec.h };
            Object.assign(rec, { x: 0.5, y: 0, w: 0.5, h: 1, filled: false });
        }
    }

    function bindWindows() {
        DOCK_IDS.forEach((id) => {
            const win = windowEl(id);
            if (!win) return;
            win.addEventListener('pointerdown', () => {
                if (state[id].open) focus(id);
            });
            const bar = win.querySelector('[data-dock-drag]');
            if (bar) {
                bar.addEventListener('pointerdown', (event) => {
                    if (event.target.closest('[data-dock-action]')) return;
                    beginDrag(id, 'move', event);
                });
            }
            const handle = win.querySelector('[data-dock-resize]');
            if (handle) {
                handle.addEventListener('pointerdown', (event) => {
                    beginDrag(id, 'resize', event);
                });
            }
            win.querySelectorAll('[data-dock-action]').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    const action = btn.getAttribute('data-dock-action');
                    if (action === 'close') close(id);
                    if (action === 'fill') fill(id);
                });
            });
        });

        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp);
        window.addEventListener('pointercancel', onPointerUp);
    }

    function beginDrag(id, mode, event) {
        const host = hostEl();
        if (!host) return;
        event.preventDefault();
        const rec = state[id];
        drag = {
            id,
            mode,
            startX: event.clientX,
            startY: event.clientY,
            hostW: host.clientWidth || 1,
            hostH: host.clientHeight || 1,
            orig: { x: rec.x, y: rec.y, w: rec.w, h: rec.h },
        };
        rec.filled = false;
        focus(id);
        document.body.classList.add('dock-dragging');
    }

    function onPointerMove(event) {
        if (!drag) return;
        const rec = state[drag.id];
        const dx = (event.clientX - drag.startX) / drag.hostW;
        const dy = (event.clientY - drag.startY) / drag.hostH;
        if (drag.mode === 'move') {
            Object.assign(rec, clampRect({
                x: drag.orig.x + dx,
                y: drag.orig.y + dy,
                w: drag.orig.w,
                h: drag.orig.h,
            }));
        } else {
            Object.assign(rec, clampRect({
                x: drag.orig.x,
                y: drag.orig.y,
                w: drag.orig.w + dx,
                h: drag.orig.h + dy,
            }));
        }
        applyGeom(drag.id);
    }

    function onPointerUp() {
        if (!drag) return;
        if (drag.mode === 'move') maybeSnap(drag.id);
        applyGeom(drag.id);
        drag = null;
        document.body.classList.remove('dock-dragging');
        emitChange();
    }

    function init(options) {
        persist = typeof options.onPersist === 'function' ? options.onPersist : persist;
        state = normalizeLayout(options.layout);
        bindWindows();
        applyAll();
        DOCK_IDS.forEach((id) => {
            if (state[id].open) {
                focusedId = id;
                renderDock(id);
            }
        });
        const openIds = DOCK_IDS.filter((id) => state[id].open).sort((a, b) => state[a].z - state[b].z);
        if (openIds.length) focusedId = openIds[openIds.length - 1];
        applyAll();
    }

    return {
        init,
        open,
        close,
        closeAll,
        fill,
        focus,
        hasOpen,
        getFocused,
        snapshot,
        clampRect,
        normalizeLayout,
        DOCK_IDS,
    };
})();
