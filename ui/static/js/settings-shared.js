/**
 * BossMod AI — Shared settings helpers (HA-STRUCT-P1-04).
 */

function initResizeHandle(handle, panel, { min = 160, max = 480 } = {}) {
    let startX, startW;
    function onMove(e) {
        const dx = (e.clientX || e.touches[0].clientX) - startX;
        panel.style.width = Math.min(max, Math.max(min, startW + dx)) + 'px';
    }
    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onUp);
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
    }
    handle.addEventListener('mousedown', e => {
        e.preventDefault();
        startX = e.clientX;
        startW = panel.offsetWidth;
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'col-resize';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
    handle.addEventListener('touchstart', e => {
        startX = e.touches[0].clientX;
        startW = panel.offsetWidth;
        document.addEventListener('touchmove', onMove, { passive: false });
        document.addEventListener('touchend', onUp);
    }, { passive: true });
}
