/**
 * BossMod AI — Modular slot shell (v1).
 *
 * Three named slots (left / center / right). Any pane can be assigned to
 * any slot. Multiple panes in a slot become tabs. Maximize activates a
 * pane as a tab that fills its slot — never a floating window over the map.
 */

const DockManager = (() => {
    const SLOT_IDS = ['left', 'center', 'right'];
    const CORE_PANE_IDS = ['focus', 'map', 'activity'];
    const COMPANY_PANE_IDS = ['files', 'tasks', 'metrics', 'org'];
    const PANE_IDS = CORE_PANE_IDS.concat(COMPANY_PANE_IDS);
    const DOCK_IDS = COMPANY_PANE_IDS;
    const HOME_SLOT = { focus: 'left', map: 'center', activity: 'right' };
    const PANE_META = {
        focus: { label: 'Focus', icon: 'message-circle' },
        map: { label: 'Office', icon: 'building' },
        activity: { label: 'Activity', icon: 'activity' },
        files: { label: 'Files', icon: 'folder' },
        tasks: { label: 'Tasks', icon: 'list-todo' },
        metrics: { label: 'Metrics', icon: 'bar-chart-3' },
        org: { label: 'Org Chart', icon: 'users' },
    };

    let state = defaultLayout();
    let persist = () => {};
    let bound = false;
    let lastCompany = null;

    function cloneLayout(layout) {
        const out = { version: 2, slots: {} };
        SLOT_IDS.forEach((slotId) => {
            const slot = layout.slots[slotId] || { panes: [], active: null };
            out.slots[slotId] = {
                panes: slot.panes.slice(),
                active: slot.active || null,
            };
        });
        return out;
    }

    function defaultLayout() {
        return {
            version: 2,
            slots: {
                left: { panes: ['focus'], active: 'focus' },
                center: { panes: ['map'], active: 'map' },
                right: { panes: ['activity'], active: 'activity' },
            },
        };
    }

    function slotOf(layout, paneId) {
        for (let i = 0; i < SLOT_IDS.length; i += 1) {
            const slotId = SLOT_IDS[i];
            if (layout.slots[slotId].panes.indexOf(paneId) !== -1) return slotId;
        }
        return null;
    }

    function companyOpenIds(layout) {
        return COMPANY_PANE_IDS.filter((id) => slotOf(layout, id));
    }

    function ensureActive(slot) {
        if (slot.panes.indexOf(slot.active) === -1) {
            slot.active = slot.panes[slot.panes.length - 1] || null;
        }
        return slot;
    }

    function removePane(layout, paneId) {
        SLOT_IDS.forEach((slotId) => {
            const slot = layout.slots[slotId];
            const idx = slot.panes.indexOf(paneId);
            if (idx === -1) return;
            slot.panes.splice(idx, 1);
            ensureActive(slot);
        });
        return layout;
    }

    function assignPane(layout, paneId, slotId) {
        if (PANE_IDS.indexOf(paneId) === -1 || SLOT_IDS.indexOf(slotId) === -1) {
            return cloneLayout(layout);
        }
        const next = cloneLayout(layout);
        removePane(next, paneId);
        const slot = next.slots[slotId];
        if (slot.panes.indexOf(paneId) === -1) slot.panes.push(paneId);
        slot.active = paneId;
        return next;
    }

    function openPane(layout, paneId, slotId) {
        if (PANE_IDS.indexOf(paneId) === -1) return cloneLayout(layout);
        const current = slotOf(layout, paneId);
        const target = SLOT_IDS.indexOf(slotId) !== -1
            ? slotId
            : (current || HOME_SLOT[paneId] || 'center');
        const next = assignPane(layout, paneId, target);
        next.slots[target].active = paneId;
        return next;
    }

    function closePane(layout, paneId) {
        if (CORE_PANE_IDS.indexOf(paneId) !== -1) return cloneLayout(layout);
        if (COMPANY_PANE_IDS.indexOf(paneId) === -1) return cloneLayout(layout);
        const next = cloneLayout(layout);
        removePane(next, paneId);
        return next;
    }

    function closeAllPanes(layout) {
        let next = cloneLayout(layout);
        COMPANY_PANE_IDS.forEach((id) => {
            next = closePane(next, id);
        });
        return next;
    }

    function maximizePane(layout, paneId) {
        if (PANE_IDS.indexOf(paneId) === -1) return cloneLayout(layout);
        const current = slotOf(layout, paneId);
        const target = current || HOME_SLOT[paneId] || 'center';
        const next = current ? cloneLayout(layout) : openPane(layout, paneId, target);
        next.slots[target].active = paneId;
        return next;
    }

    function activatePane(layout, paneId) {
        const current = slotOf(layout, paneId);
        if (!current) return cloneLayout(layout);
        const next = cloneLayout(layout);
        next.slots[current].active = paneId;
        return next;
    }

    function migrateFloatingLayout(raw) {
        const next = defaultLayout();
        COMPANY_PANE_IDS.forEach((id) => {
            const src = raw[id];
            if (src && typeof src === 'object' && src.open) {
                next.slots.center.panes.push(id);
                next.slots.center.active = id;
            }
        });
        return next;
    }

    function normalizeLayout(raw) {
        if (!raw || typeof raw !== 'object') return defaultLayout();

        const looksFloating = COMPANY_PANE_IDS.some((id) => raw[id] && typeof raw[id] === 'object' && ('x' in raw[id] || 'filled' in raw[id] || 'open' in raw[id]))
            && !raw.slots;
        if (looksFloating) return migrateFloatingLayout(raw);

        const source = raw.slots && typeof raw.slots === 'object' ? raw : null;
        if (!source) return defaultLayout();

        const next = {
            version: 2,
            slots: {
                left: { panes: [], active: null },
                center: { panes: [], active: null },
                right: { panes: [], active: null },
            },
        };
        const seen = {};
        SLOT_IDS.forEach((slotId) => {
            const src = source.slots[slotId];
            if (!src || typeof src !== 'object') return;
            const panes = Array.isArray(src.panes) ? src.panes : [];
            panes.forEach((id) => {
                if (PANE_IDS.indexOf(id) === -1 || seen[id]) return;
                seen[id] = true;
                next.slots[slotId].panes.push(id);
            });
            if (typeof src.active === 'string' && next.slots[slotId].panes.indexOf(src.active) !== -1) {
                next.slots[slotId].active = src.active;
            }
            ensureActive(next.slots[slotId]);
        });

        CORE_PANE_IDS.forEach((id) => {
            if (seen[id]) return;
            const home = HOME_SLOT[id];
            next.slots[home].panes.push(id);
            if (!next.slots[home].active) next.slots[home].active = id;
        });

        return next;
    }

    function snapshot() {
        return cloneLayout(state);
    }

    function slotEl(slotId) {
        return document.getElementById(`slot-${slotId}`);
    }

    function paneEl(paneId) {
        return document.querySelector(`[data-pane="${paneId}"]`);
    }

    function poolEl() {
        return document.getElementById('dock-pane-pool');
    }

    function tabbarEl(slotId) {
        const slot = slotEl(slotId);
        return slot ? slot.querySelector('[data-slot-tabs]') : null;
    }

    function bodyEl(slotId) {
        const slot = slotEl(slotId);
        return slot ? slot.querySelector('[data-slot-body]') : null;
    }

    function renderDock(id) {
        if (COMPANY_PANE_IDS.indexOf(id) === -1) return;
        if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.switchTab === 'function') {
            CompanyDashboard.switchTab(id);
        }
    }

    function unmountDock(id) {
        if (COMPANY_PANE_IDS.indexOf(id) === -1) return;
        if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.unmount === 'function') {
            CompanyDashboard.unmount(id);
        }
    }

    function commit(next, options) {
        const opts = options || {};
        const prevOpen = companyOpenIds(state);
        state = next;
        applyAll();
        if (!opts.silentPersist) persist(snapshot());
        window.dispatchEvent(new CustomEvent('dock-change', {
            detail: { focused: getFocused(), layout: snapshot() },
        }));
        const nowOpen = companyOpenIds(state);
        prevOpen.forEach((id) => {
            if (nowOpen.indexOf(id) === -1) unmountDock(id);
        });
        nowOpen.forEach((id) => {
            if (prevOpen.indexOf(id) === -1) renderDock(id);
        });
        window.dispatchEvent(new Event('panel-resize'));
    }

    function renderTab(paneId, active) {
        const meta = PANE_META[paneId] || { label: paneId, icon: 'square' };
        const closable = COMPANY_PANE_IDS.indexOf(paneId) !== -1;
        const tab = document.createElement('div');
        tab.className = `dock-tab${active ? ' is-active' : ''}`;
        tab.setAttribute('role', 'tab');
        tab.setAttribute('tabindex', '0');
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.setAttribute('data-pane-tab', paneId);
        tab.setAttribute('draggable', 'true');
        tab.innerHTML = `
            <span class="dock-tab-label">
                <i data-lucide="${meta.icon}" class="w-3.5 h-3.5"></i>
                <span>${meta.label}</span>
            </span>
            <span class="dock-tab-actions">
                <button type="button" class="dock-tab-btn" data-dock-action="maximize" title="Maximize in slot" aria-label="Maximize ${meta.label} in slot">
                    <i data-lucide="maximize-2" class="w-3 h-3"></i>
                </button>
                ${closable ? `<button type="button" class="dock-tab-btn" data-dock-action="close" title="Close" aria-label="Close ${meta.label}">
                    <i data-lucide="x" class="w-3 h-3"></i>
                </button>` : ''}
            </span>
        `;
        return tab;
    }

    function bindTab(tab, paneId) {
        tab.addEventListener('click', (event) => {
            if (event.target.closest('[data-dock-action]')) return;
            activate(paneId);
        });
        tab.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                activate(paneId);
            }
        });
        tab.addEventListener('dragstart', (event) => {
            event.dataTransfer.setData('text/plain', paneId);
            event.dataTransfer.effectAllowed = 'move';
            tab.classList.add('is-dragging');
        });
        tab.addEventListener('dragend', () => {
            tab.classList.remove('is-dragging');
            clearDropTargets();
        });
        tab.querySelectorAll('[data-dock-action]').forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const action = btn.getAttribute('data-dock-action');
                if (action === 'close') close(paneId);
                if (action === 'maximize') maximize(paneId);
            });
        });
    }

    function renderEmptyHint(body) {
        if (body.querySelector('[data-slot-empty]')) return;
        const hint = document.createElement('div');
        hint.className = 'dock-slot-empty';
        hint.setAttribute('data-slot-empty', 'true');
        hint.textContent = 'Drop a pane here';
        body.appendChild(hint);
    }

    function applySlot(slotId) {
        const slot = state.slots[slotId];
        const host = slotEl(slotId);
        const tabbar = tabbarEl(slotId);
        const body = bodyEl(slotId);
        if (!host || !tabbar || !body) return;

        host.classList.toggle('is-empty', slot.panes.length === 0);
        tabbar.innerHTML = '';
        slot.panes.forEach((paneId) => {
            const tab = renderTab(paneId, slot.active === paneId);
            bindTab(tab, paneId);
            tabbar.appendChild(tab);
        });
        if (window.lucide) lucide.createIcons({ nodes: [tabbar] });

        const emptyHint = body.querySelector('[data-slot-empty]');
        if (emptyHint) emptyHint.remove();

        slot.panes.forEach((paneId) => {
            const el = paneEl(paneId);
            if (!el) return;
            if (el.parentElement !== body) body.appendChild(el);
            const active = slot.active === paneId;
            el.classList.toggle('hidden', !active);
            el.classList.toggle('is-active', active);
            el.setAttribute('aria-hidden', active ? 'false' : 'true');
        });

        if (!slot.panes.length) renderEmptyHint(body);
    }

    function applyPool() {
        const pool = poolEl();
        if (!pool) return;
        PANE_IDS.forEach((paneId) => {
            if (slotOf(state, paneId)) return;
            const el = paneEl(paneId);
            if (!el) return;
            if (el.parentElement !== pool) pool.appendChild(el);
            el.classList.add('hidden');
            el.classList.remove('is-active');
        });
    }

    function applyAll() {
        SLOT_IDS.forEach(applySlot);
        applyPool();
    }

    function clearDropTargets() {
        SLOT_IDS.forEach((slotId) => {
            const el = slotEl(slotId);
            if (el) el.classList.remove('is-drop-target');
        });
    }

    function bindSlots() {
        if (bound) return;
        bound = true;
        SLOT_IDS.forEach((slotId) => {
            const el = slotEl(slotId);
            if (!el) return;
            el.addEventListener('dragover', (event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                clearDropTargets();
                el.classList.add('is-drop-target');
            });
            el.addEventListener('dragleave', (event) => {
                if (el.contains(event.relatedTarget)) return;
                el.classList.remove('is-drop-target');
            });
            el.addEventListener('drop', (event) => {
                event.preventDefault();
                clearDropTargets();
                const paneId = event.dataTransfer.getData('text/plain');
                if (paneId) assign(paneId, slotId);
            });
        });
    }

    function open(id) {
        if (COMPANY_PANE_IDS.indexOf(id) !== -1) lastCompany = id;
        commit(openPane(state, id));
    }

    function close(id) {
        commit(closePane(state, id));
    }

    function closeAll() {
        const next = closeAllPanes(state);
        const mapSlot = slotOf(next, 'map');
        if (mapSlot) next.slots[mapSlot].active = 'map';
        lastCompany = null;
        commit(next);
    }

    function assign(id, slotId) {
        if (COMPANY_PANE_IDS.indexOf(id) !== -1) lastCompany = id;
        commit(assignPane(state, id, slotId));
    }

    function activate(id) {
        if (!slotOf(state, id)) return;
        if (COMPANY_PANE_IDS.indexOf(id) !== -1) lastCompany = id;
        commit(activatePane(state, id));
    }

    function maximize(id) {
        if (COMPANY_PANE_IDS.indexOf(id) !== -1) lastCompany = id;
        commit(maximizePane(state, id));
    }

    function hasOpen() {
        return companyOpenIds(state).length > 0;
    }

    function getFocused() {
        for (let i = 0; i < SLOT_IDS.length; i += 1) {
            const active = state.slots[SLOT_IDS[i]].active;
            if (COMPANY_PANE_IDS.indexOf(active) !== -1) return active;
        }
        if (lastCompany && slotOf(state, lastCompany)) return lastCompany;
        const openIds = companyOpenIds(state);
        return openIds[openIds.length - 1] || null;
    }

    function init(options) {
        persist = typeof options.onPersist === 'function' ? options.onPersist : persist;
        state = normalizeLayout(options.layout);
        lastCompany = getFocused();
        bindSlots();
        applyAll();
        companyOpenIds(state).forEach(renderDock);
        persist(snapshot());
    }

    return {
        init,
        open,
        close,
        closeAll,
        assign,
        activate,
        focus: activate,
        maximize,
        fill: maximize,
        hasOpen,
        getFocused,
        snapshot,
        defaultLayout,
        normalizeLayout,
        assignPane,
        openPane,
        closePane,
        closeAllPanes,
        maximizePane,
        activatePane,
        slotOf,
        companyOpenIds,
        SLOT_IDS,
        PANE_IDS,
        CORE_PANE_IDS,
        COMPANY_PANE_IDS,
        DOCK_IDS,
    };
})();
