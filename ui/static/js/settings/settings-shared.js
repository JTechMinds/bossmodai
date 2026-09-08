/**
 * BossMod AI — Shared settings helpers (HA-STRUCT-P1-04).
 */

/**
 * Show one failure above a settings list, without destroying the list.
 *
 * Phase 4 added this for the two Edit buttons that checked `res.ok` and then
 * did nothing with the answer: the operator clicked and the app sat there.
 * Re-rendering the whole list as an error would throw away the rows they can
 * still use, so the message goes above them and replaces itself rather than
 * stacking.
 *
 * @param {Element} container  The list container.
 * @param {string} message
 * @returns {void}
 */
function showRowError(container, message) {
    if (!container) return;
    let node = container.querySelector('[data-settings-row-error]');
    if (!node) {
        node = document.createElement('p');
        node.setAttribute('data-settings-row-error', '');
        node.setAttribute('role', 'alert');
        node.className = 'text-red-500 text-sm mb-4';
        container.prepend(node);
    }
    node.textContent = message;
}

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
