/**
 * BossMod AI — Modular slot shell (v1).
 *
 * Three named slots (left / center / right). Any pane can be assigned to
 * any slot. Multiple panes in a slot become tabs. Maximize solos a pane in
 * its slot (sibling tabs collapse; click again restores). A single-pane
 * slot already fills, so the control is omitted. Tab drag inserts at an
 * index in the target tab bar — never a floating window over the map.
 */

const DockManager = (() => {
    const SLOT_IDS = ['left', 'center', 'right'];
    const CORE_PANE_IDS = ['focus', 'map', 'activity'];
    const SHELL_PANE_IDS = ['directory', 'channels'];
    const COMPANY_PANE_IDS = ['files', 'tasks', 'metrics', 'org'];
    const PANE_IDS = CORE_PANE_IDS.concat(SHELL_PANE_IDS, COMPANY_PANE_IDS);
    const DOCK_IDS = COMPANY_PANE_IDS;
    const MOUNT_PANE_IDS = SHELL_PANE_IDS.concat(COMPANY_PANE_IDS);
    const HOME_SLOT = {
        focus: 'left',
        directory: 'left',
        channels: 'left',
        map: 'center',
        activity: 'right',
        files: 'center',
        tasks: 'center',
        metrics: 'center',
        org: 'center',
    };
    const PANE_META = {
        focus: { label: 'Focus', icon: 'message-circle' },
        directory: { label: 'Directory', icon: 'book-user' },
        channels: { label: 'Channels', icon: 'messages-square' },
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
            const panes = slot.panes.slice();
            out.slots[slotId] = {
                panes,
                active: slot.active || null,
            };
            const solo = soloId(slot);
            if (solo) out.slots[slotId].solo = solo;
        });
        return out;
    }

    function soloId(slot) {
        if (!slot || typeof slot.solo !== 'string') return null;
        if (slot.panes.indexOf(slot.solo) === -1 || slot.panes.length < 2) return null;
        return slot.solo;
    }

    function scrubSolo(slot) {
        if (!slot) return slot;
        const solo = soloId(slot);
        if (solo) slot.solo = solo;
        else delete slot.solo;
        return slot;
    }

    function dropIndexFromRects(rects, clientX) {
        if (!rects || !rects.length) return 0;
        for (let i = 0; i < rects.length; i += 1) {
            const rect = rects[i];
            if (clientX < rect.left + (rect.width / 2)) return i;
        }
        return rects.length;
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
            scrubSolo(slot);
        });
        return layout;
    }

    function placePane(layout, paneId, slotId, index) {
        if (PANE_IDS.indexOf(paneId) === -1 || SLOT_IDS.indexOf(slotId) === -1) {
            return cloneLayout(layout);
        }
        const next = cloneLayout(layout);
        const fromSlotId = slotOf(next, paneId);
        const fromIdx = fromSlotId ? next.slots[fromSlotId].panes.indexOf(paneId) : -1;
        const target = next.slots[slotId];
        let dest = (typeof index === 'number' && index === index)
            ? Math.max(0, Math.min(Math.floor(index), target.panes.length))
            : target.panes.length;
        if (fromSlotId === slotId && fromIdx !== -1 && fromIdx < dest) dest -= 1;
        removePane(next, paneId);
        SLOT_IDS.forEach((id) => { scrubSolo(next.slots[id]); });
        delete target.solo;
        dest = Math.max(0, Math.min(dest, target.panes.length));
        if (target.panes.indexOf(paneId) === -1) target.panes.splice(dest, 0, paneId);
        target.active = paneId;
        return next;
    }

    function assignPane(layout, paneId, slotId) {
        return placePane(layout, paneId, slotId, null);
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
        if (PANE_IDS.indexOf(paneId) === -1) return cloneLayout(layout);
        const next = cloneLayout(layout);
        removePane(next, paneId);
        return next;
    }

    function togglePane(layout, paneId) {
        if (slotOf(layout, paneId)) return closePane(layout, paneId);
        return openPane(layout, paneId);
    }

    function closeAllPanes(layout) {
        let next = cloneLayout(layout);
        COMPANY_PANE_IDS.forEach((id) => {
            next = closePane(next, id);
        });
        return next;
    }

    function canMaximizePane(layout, paneId) {
        const current = slotOf(layout, paneId);
        if (!current) return false;
        return layout.slots[current].panes.length > 1;
    }

    function maximizePane(layout, paneId) {
        if (PANE_IDS.indexOf(paneId) === -1) return cloneLayout(layout);
        const current = slotOf(layout, paneId);
        const target = current || HOME_SLOT[paneId] || 'center';
        const next = current ? cloneLayout(layout) : openPane(layout, paneId, target);
        const slot = next.slots[target];
        slot.active = paneId;
        if (slot.panes.length < 2) {
            delete slot.solo;
            return next;
        }
        if (soloId(slot) === paneId) delete slot.solo;
        else slot.solo = paneId;
        return next;
    }

    function activatePane(layout, paneId) {
        const current = slotOf(layout, paneId);
        if (!current) return cloneLayout(layout);
        const next = cloneLayout(layout);
        next.slots[current].active = paneId;
        if (next.slots[current].solo && next.slots[current].solo !== paneId) {
            delete next.slots[current].solo;
        }
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
            if (typeof src.solo === 'string') {
                next.slots[slotId].solo = src.solo;
                scrubSolo(next.slots[slotId]);
                if (next.slots[slotId].solo) next.slots[slotId].active = next.slots[slotId].solo;
            }
        });

        const hasAny = SLOT_IDS.some((slotId) => next.slots[slotId].panes.length > 0);
        if (!hasAny) return defaultLayout();

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

    function mountableIds(layout) {
        return MOUNT_PANE_IDS.filter((id) => slotOf(layout, id));
    }

    function renderDock(id) {
        if (id === 'directory' && typeof CompanyView !== 'undefined' && typeof CompanyView.render === 'function') {
            const body = document.getElementById('dock-directory-body');
            if (body) void CompanyView.render(body);
            return;
        }
        if (id === 'channels' && typeof ChannelsView !== 'undefined' && typeof ChannelsView.render === 'function') {
            const body = document.getElementById('dock-channels-body');
            if (body) void ChannelsView.render(body);
            return;
        }
        if (COMPANY_PANE_IDS.indexOf(id) === -1) return;
        if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.switchTab === 'function') {
            CompanyDashboard.switchTab(id);
        }
    }

    function unmountDock(id) {
        if (id === 'directory' || id === 'channels') {
            const body = document.getElementById(`dock-${id}-body`);
            if (body) body.innerHTML = '';
            return;
        }
        if (COMPANY_PANE_IDS.indexOf(id) === -1) return;
        if (typeof CompanyDashboard !== 'undefined' && typeof CompanyDashboard.unmount === 'function') {
            CompanyDashboard.unmount(id);
        }
    }

    function scheduleShownResize() {
        const fire = () => window.dispatchEvent(new Event('panel-resize'));
        if (typeof requestAnimationFrame !== 'function') {
            fire();
            return;
        }
        requestAnimationFrame(() => {
            fire();
            requestAnimationFrame(fire);
        });
    }

    function commit(next, options) {
        const opts = options || {};
        const prevOpen = mountableIds(state);
        state = next;
        applyAll();
        if (!opts.silentPersist) persist(snapshot());
        window.dispatchEvent(new CustomEvent('dock-change', {
            detail: { focused: getFocused(), layout: snapshot() },
        }));
        const nowOpen = mountableIds(state);
        prevOpen.forEach((id) => {
            if (nowOpen.indexOf(id) === -1) unmountDock(id);
        });
        nowOpen.forEach((id) => {
            if (prevOpen.indexOf(id) === -1) renderDock(id);
        });
        scheduleShownResize();
    }

    function renderTab(paneId, slot) {
        const meta = PANE_META[paneId] || { label: paneId, icon: 'square' };
        const active = slot.active === paneId;
        const soloed = soloId(slot) === paneId;
        const canMax = slot.panes.length > 1;
        const maxTitle = soloed ? 'Restore sibling tabs' : 'Maximize in slot';
        const maxIcon = soloed ? 'minimize-2' : 'maximize-2';
        const tab = document.createElement('div');
        tab.className = `dock-tab${active ? ' is-active' : ''}${soloed ? ' is-solo' : ''}`;
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
                ${canMax ? `<button type="button" class="dock-tab-btn" data-dock-action="maximize" title="${maxTitle}" aria-label="${maxTitle}" aria-pressed="${soloed ? 'true' : 'false'}">
                    <i data-lucide="${maxIcon}" class="w-3 h-3"></i>
                </button>` : ''}
                <button type="button" class="dock-tab-btn" data-dock-action="close" title="Close" aria-label="Close ${meta.label}">
                    <i data-lucide="x" class="w-3 h-3"></i>
                </button>
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
            if (event.target.closest('[data-dock-action]')) {
                event.preventDefault();
                return;
            }
            event.dataTransfer.setData('text/plain', paneId);
            event.dataTransfer.setData('application/x-bossmod-pane', paneId);
            event.dataTransfer.effectAllowed = 'move';
            tab.classList.add('is-dragging');
        });
        tab.addEventListener('dragend', () => {
            tab.classList.remove('is-dragging');
            clearDropTargets();
        });
        tab.querySelectorAll('[data-dock-action]').forEach((btn) => {
            btn.setAttribute('draggable', 'false');
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

        const solo = soloId(slot);
        host.classList.toggle('is-empty', slot.panes.length === 0);
        host.classList.toggle('is-solo', Boolean(solo));
        tabbar.innerHTML = '';
        const visibleTabs = solo ? [solo] : slot.panes;
        visibleTabs.forEach((paneId) => {
            const tab = renderTab(paneId, slot);
            bindTab(tab, paneId);
            tabbar.appendChild(tab);
        });
        if (window.lucide) lucide.createIcons({ nodes: [tabbar] });

        const emptyHint = body.querySelector('[data-slot-empty]');
        if (emptyHint) emptyHint.remove();

        const shown = solo || slot.active;
        slot.panes.forEach((paneId) => {
            const el = paneEl(paneId);
            if (!el) return;
            if (el.parentElement !== body) body.appendChild(el);
            const active = shown === paneId;
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
            if (el) {
                el.classList.remove('is-drop-target');
                el.removeAttribute('data-drop-index');
            }
            const tabbar = tabbarEl(slotId);
            if (!tabbar) return;
            const caret = tabbar.querySelector('[data-insert-caret]');
            if (caret) caret.remove();
        });
    }

    function tabRects(tabbar) {
        return Array.prototype.map.call(
            tabbar.querySelectorAll('[data-pane-tab]'),
            (node) => node.getBoundingClientRect()
        );
    }

    function isOverTabbar(event, tabbar) {
        if (!tabbar) return false;
        const rect = tabbar.getBoundingClientRect();
        return event.clientY >= rect.top && event.clientY <= rect.bottom
            && event.clientX >= rect.left && event.clientX <= rect.right;
    }

    function showInsertCaret(tabbar, index) {
        let caret = tabbar.querySelector('[data-insert-caret]');
        if (!caret) {
            caret = document.createElement('div');
            caret.className = 'dock-insert-caret';
            caret.setAttribute('data-insert-caret', 'true');
            tabbar.appendChild(caret);
        }
        const tabs = tabbar.querySelectorAll('[data-pane-tab]');
        const barRect = tabbar.getBoundingClientRect();
        let x = 4;
        if (tabs.length) {
            if (index >= tabs.length) {
                x = tabs[tabs.length - 1].getBoundingClientRect().right - barRect.left;
            } else {
                x = tabs[index].getBoundingClientRect().left - barRect.left;
            }
        }
        caret.style.left = `${Math.max(0, x + tabbar.scrollLeft)}px`;
    }

    function paneIdFromTransfer(event) {
        const typed = event.dataTransfer.getData('application/x-bossmod-pane');
        return typed || event.dataTransfer.getData('text/plain');
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
                const tabbar = tabbarEl(slotId);
                const overBar = isOverTabbar(event, tabbar);
                const index = overBar ? dropIndexFromRects(tabRects(tabbar), event.clientX) : null;
                clearDropTargets();
                el.classList.add('is-drop-target');
                if (overBar && tabbar) {
                    el.setAttribute('data-drop-index', String(index));
                    showInsertCaret(tabbar, index);
                }
            });
            el.addEventListener('dragleave', (event) => {
                if (el.contains(event.relatedTarget)) return;
                el.classList.remove('is-drop-target');
                clearDropTargets();
            });
            el.addEventListener('drop', (event) => {
                event.preventDefault();
                const paneId = paneIdFromTransfer(event);
                const tabbar = tabbarEl(slotId);
                const index = isOverTabbar(event, tabbar)
                    ? dropIndexFromRects(tabRects(tabbar), event.clientX)
                    : null;
                clearDropTargets();
                if (paneId) place(paneId, slotId, index);
            });
        });
    }

    function isOpen(id) {
        return Boolean(slotOf(state, id));
    }

    function open(id) {
        if (COMPANY_PANE_IDS.indexOf(id) !== -1) lastCompany = id;
        commit(openPane(state, id));
    }

    function close(id) {
        commit(closePane(state, id));
    }

    function toggle(id) {
        if (COMPANY_PANE_IDS.indexOf(id) !== -1 && !isOpen(id)) lastCompany = id;
        commit(togglePane(state, id));
    }

    function closeAll() {
        const next = closeAllPanes(state);
        const mapSlot = slotOf(next, 'map');
        if (mapSlot) next.slots[mapSlot].active = 'map';
        lastCompany = null;
        commit(next);
    }

    function assign(id, slotId) {
        place(id, slotId, null);
    }

    function place(id, slotId, index) {
        if (COMPANY_PANE_IDS.indexOf(id) !== -1) lastCompany = id;
        commit(placePane(state, id, slotId, index));
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
        mountableIds(state).forEach(renderDock);
        persist(snapshot());
        scheduleShownResize();
    }

    return {
        init,
        open,
        close,
        toggle,
        closeAll,
        assign,
        place,
        activate,
        focus: activate,
        maximize,
        fill: maximize,
        hasOpen,
        isOpen,
        getFocused,
        snapshot,
        defaultLayout,
        normalizeLayout,
        assignPane,
        placePane,
        openPane,
        closePane,
        togglePane,
        closeAllPanes,
        maximizePane,
        canMaximizePane,
        dropIndexFromRects,
        activatePane,
        slotOf,
        companyOpenIds,
        scheduleShownResize,
        SLOT_IDS,
        PANE_IDS,
        CORE_PANE_IDS,
        SHELL_PANE_IDS,
        COMPANY_PANE_IDS,
        DOCK_IDS,
        HOME_SLOT,
    };
})();

window.DockManager = DockManager;
