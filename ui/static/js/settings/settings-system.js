/**
 * BossMod AI — Settings → System Settings (HA-STRUCT-P1-04).
 *
 * Every tab is a grid of standard setting rows, except Context Window, which
 * lays the same rows out around its budget numbers
 * (settings-context-panel.js, loaded first).
 */

const SystemSection = (() => {
    const { CATEGORIES, CATEGORY_DESCRIPTIONS, SETTING_META } = BossModSystemSettingsMeta;
    let container = null;
    let activeCategory = 'simulation';

    const INPUT_CLASS = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white';

    /**
     * A select-type setting's options. A stored value no option holds gets
     * its own row, so the dropdown shows what is stored rather than a choice
     * that was never made.
     *
     * @param {{value: string}} setting
     * @param {{options?: Array<{value: string, label: string}>}} meta
     * @returns {Array<{value: string, label: string}>}
     */
    function selectOptions(setting, meta) {
        const options = (meta.options || []).slice();
        if (setting.value && !options.some(opt => opt.value === setting.value)) {
            options.push({ value: setting.value, label: setting.value });
        }
        return options;
    }

    /** A BossModMenuSelect is mounted here after the HTML is in the DOM. */
    function selectControl(setting) {
        return `<div data-setting-select="${BossModFormat.escapeAttribute(setting.key)}"
                     data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"></div>`;
    }

    function settingControl(setting) {
        const meta = SETTING_META[setting.key] || {};
        if (meta.control === 'select') return selectControl(setting);
        // A BossModSwitch is mounted here after the HTML is in the DOM.
        if (meta.control === 'switch') {
            return `<div data-setting-switch="${BossModFormat.escapeAttribute(setting.key)}"
                         data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"></div>`;
        }
        return `<input type="text" id="${BossModFormat.escapeAttribute(fieldId(setting.key))}"
                        data-setting-key="${BossModFormat.escapeAttribute(setting.key)}"
                        data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"
                        value="${BossModFormat.escapeAttribute(setting.value)}"
                        class="${INPUT_CLASS}">`;
    }

    /** The text field's id, which its row's label points at. */
    function fieldId(key) {
        return `setting-field-${key}`;
    }

    /**
     * One setting's standard row: its label, description, control and error line.
     *
     * Shared by the generic grid and the Context Window slots, so a field
     * looks, saves and reports a refusal the same way on every tab.
     *
     * @param {{key: string, value: string, category: string}} setting
     * @returns {string} The row's markup, every value escaped.
     */
    function settingRow(setting) {
        const meta = SETTING_META[setting.key] || {};
        const label = BossModFormat.escapeHtml(meta.label || setting.key);
        const description = meta.description || 'System setting.';
        // A switch row carries the label as its accessible name; a second heading would repeat it.
        let heading = '';
        if (meta.control === 'select') heading = `<label class="block text-sm font-medium mb-1">${label}</label>`;
        else if (meta.control !== 'switch') {
            heading = `<label for="${BossModFormat.escapeAttribute(fieldId(setting.key))}" class="block text-sm font-medium mb-1">${label}</label>`;
        }
        return `
                    <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                        ${heading}
                        <p class="text-xs text-bm-muted mb-1.5">${BossModFormat.escapeHtml(description)}</p>
                        ${settingControl(setting)}
                        <p role="alert" class="text-xs text-red-600 mt-1" data-setting-error="${BossModFormat.escapeAttribute(setting.key)}"></p>
                    </div>`;
    }

    /**
     * Fill each Context Window slot with its setting's standard row. A slot
     * whose setting has no row says so instead of leaving a silent gap.
     *
     * @param {HTMLElement} el
     * @param {Array<{key: string}>} items  The tab's settings.
     */
    function fillSlots(el, items) {
        el.querySelectorAll('[data-setting-slot]').forEach(slot => {
            const key = slot.dataset.settingSlot;
            const setting = items.find(item => item.key === key);
            if (setting) {
                slot.innerHTML = settingRow(setting);
                return;
            }
            const label = (SETTING_META[key] && SETTING_META[key].label) || key;
            slot.innerHTML = `<p role="alert" class="text-xs text-red-600">${BossModFormat.escapeHtml(label)} is not configured. Reset Seed Settings restores it.</p>`;
        });
    }

    /**
     * GET /api/settings/context-budget, as `{budget}` or `{error}`: a failure
     * here leaves the other tabs working and is shown on the Context Window tab.
     */
    async function loadBudget() {
        const res = await apiFetch('/api/settings/context-budget');
        if (res.ok) return { budget: await res.json() };
        // A body that is not JSON has no reason in it; the status still says what happened.
        const payload = await res.json().catch(() => null);
        const detail = payload && typeof payload.detail === 'string' && payload.detail.trim()
            ? payload.detail
            : `The server answered ${res.status}.`;
        return { error: `Couldn't load the context window numbers. ${detail}` };
    }

    /**
     * The per-row error line for one setting, or null when the row is not on screen.
     * @param {string} key
     * @returns {HTMLElement|null}
     */
    function errorLine(key) {
        if (!container) return null;
        return Array.from(container.querySelectorAll('[data-setting-error]'))
            .find(node => node.dataset.settingError === key) || null;
    }

    /**
     * Save one setting through the settings PUT and show the outcome on its row.
     *
     * Success flashes the control green and clears the row's error line.
     * Failure flashes it red and writes the server's message into the row's
     * `role="alert"` line, so a refused value is never silent.
     *
     * @param {string} key  Setting key.
     * @param {string} category  The row's stored category; the PUT keeps it.
     * @param {string} value  Raw value to store.
     * @param {HTMLElement} target  The input, dropdown, or switch element to flash.
     * @returns {Promise<boolean>} Whether the server accepted the value.
     */
    async function saveSetting(key, category, value, target) {
        const error = errorLine(key);
        try {
            await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`, {
                method: 'PUT',
            });
            BossModOperatorInvalidate.notifyLocal(['system']);
            if (error) error.textContent = '';
            target.classList.add('border-emerald-400');
            setTimeout(() => target.classList.remove('border-emerald-400'), 1000);
            return true;
        } catch (err) {
            if (error) error.textContent = String((err && err.message) || err);
            target.classList.add('border-red-400');
            setTimeout(() => target.classList.remove('border-red-400'), 1000);
            return false;
        }
    }

    /**
     * Render System Settings into `el`.
     *
     * @param {HTMLElement} el
     * @param {{tab?: string, key?: string}} [options]  From SettingsView.open,
     *   used once: the tab to show and the setting to focus. Repaints
     *   (bindRepaint) and tab clicks pass none, so a repaint never moves focus.
     * @returns {Promise<void>}
     */
    async function render(el, options) {
        if (options && options.tab) activeCategory = options.tab;
        container = el;
        SettingsView.bindRepaint('system', () => render(container));
        const wantsBudget = activeCategory === 'context';
        // A repaint of the tab keeps its numbers on screen until the new ones arrive.
        if (wantsBudget && !el.querySelector('[data-ctx-budget]')) {
            (el.querySelector('[data-system-section-body]') || el).innerHTML = '<p class="text-sm text-bm-muted" role="status">Loading…</p>';
        }
        let settings = [];
        let loaded = null;
        try {
            const [res, budgetLoad] = await Promise.all([
                apiFetch('/api/settings'),
                wantsBudget ? loadBudget() : Promise.resolve(null),
            ]);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            settings = await res.json();
            loaded = budgetLoad;
        } catch (err) {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load settings.</p>';
            return;
        }

        // Group by tab (meta.tab, else the stored category); only show non-advanced tabs.
        const tabOf = s => SETTING_META[s.key]?.tab || s.category;
        const groups = {};
        const shownCats = new Set(CATEGORIES.map(c => c.key));
        for (const s of settings) {
            if (!shownCats.has(tabOf(s))) continue;
            if (!SETTING_META[s.key]) continue;
            if (s.key === 'steps_per_tick') continue;
            if (!groups[tabOf(s)]) groups[tabOf(s)] = [];
            groups[tabOf(s)].push(s);
        }

        for (const key of Object.keys(groups)) {
            groups[key].sort((a, b) => {
                const aOrder = SETTING_META[a.key]?.order ?? 999;
                const bOrder = SETTING_META[b.key]?.order ?? 999;
                if (aOrder !== bOrder) return aOrder - bOrder;
                return a.key.localeCompare(b.key);
            });
        }

        const availableCategories = CATEGORIES.filter(cat => (groups[cat.key] || []).length > 0);
        if (!availableCategories.some(cat => cat.key === activeCategory)) {
            activeCategory = availableCategories[0]?.key || 'simulation';
        }
        if (activeCategory === 'context' && !loaded) {
            // Landed on Context Window only now (the asked-for tab had no settings).
            try {
                loaded = await loadBudget();
            } catch (err) {
                el.innerHTML = '<p class="text-red-500 text-sm">Failed to load settings.</p>';
                return;
            }
        }

        const activeItems = groups[activeCategory] || [];
        const activeCategoryMeta = CATEGORIES.find(cat => cat.key === activeCategory);

        let html = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">System Settings</h2>
                <p class="text-sm text-bm-muted mt-0.5">Configure runtime behavior, model output limits, and Desk browsing.</p>
            </div>
            <div class="max-w-7xl">
                <div class="mb-5 flex flex-wrap gap-2">`;

        for (const cat of availableCategories) {
            const active = cat.key === activeCategory;
            html += `
                    <button
                        type="button"
                        data-system-category="${BossModFormat.escapeAttribute(cat.key)}"
                        class="system-category-tab px-4 py-2 rounded-lg text-sm font-medium border transition-colors
                               ${active ? 'bg-bm-accent text-white border-bm-accent shadow-sm' : 'bg-white text-bm-text border-bm-border hover:bg-slate-50'}">
                        ${BossModFormat.escapeHtml(cat.label)}
                    </button>`;
        }

        html += `
                </div>
                <section class="border border-bm-border rounded-xl bg-white p-5 shadow-sm">
                    <div class="mb-4">
                        <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide">${BossModFormat.escapeHtml(activeCategoryMeta?.label || 'Settings')}</h3>
                        <p class="text-xs text-bm-muted mt-1">${BossModFormat.escapeHtml(CATEGORY_DESCRIPTIONS[activeCategory] || '')}</p>
                    </div>
                    <div data-system-section-body>`;

        const budget = activeCategory === 'context' ? loaded.budget : null;
        if (activeCategory === 'context') {
            html += budget
                ? BossModContextPanel.layout(budget)
                : `<p role="alert" class="text-sm text-red-600">${BossModFormat.escapeHtml(loaded.error)}</p>`;
        } else {
            html += `<div class="grid grid-cols-1 2xl:grid-cols-2 gap-4">${activeItems.map(settingRow).join('')}</div>`;
        }

        html += `
                    </div>
                </section>
            </div>`;
        el.innerHTML = html;
        if (budget) {
            fillSlots(el, activeItems);
            BossModContextPanel.wire(el, { budget, api: apiFetch, onApplied: () => render(container) });
        }

        el.querySelectorAll('[data-system-category]').forEach(btn => {
            btn.addEventListener('click', () => {
                activeCategory = btn.dataset.systemCategory;
                render(container);
            });
        });

        el.querySelectorAll('[data-setting-switch]').forEach(mount => {
            const key = mount.dataset.settingSwitch;
            const category = mount.dataset.settingCategory;
            const setting = activeItems.find(item => item.key === key);
            const toggle = BossModSwitch.create({
                label: SETTING_META[key].label,
                pressed: setting.value === 'true',
                onChange: async (pressed) => {
                    const saved = await saveSetting(key, category, pressed ? 'true' : 'false', toggle.element);
                    // Never show a state the server refused.
                    if (!saved) toggle.set(!pressed);
                },
            });
            mount.append(toggle.element);
        });

        el.querySelectorAll('[data-setting-select]').forEach(mount => {
            const key = mount.dataset.settingSelect;
            const category = mount.dataset.settingCategory;
            const setting = activeItems.find(item => item.key === key);
            let saved = setting.value;
            const menu = BossModMenuSelect.create({
                label: SETTING_META[key].label,
                options: selectOptions(setting, SETTING_META[key]),
                value: setting.value,
                variant: 'field',
                onChange: async (value) => {
                    // Never show a state the server refused.
                    if (await saveSetting(key, category, value, menu.element)) saved = value;
                    else menu.setValue(saved);
                },
            });
            mount.append(menu.element);
            BossModIcons.paint(mount, 'settings-system');
        });

        el.querySelectorAll('.setting-input').forEach(input => {
            input.addEventListener('change', (e) => saveSetting(
                e.target.dataset.settingKey,
                e.target.dataset.settingCategory,
                e.target.value,
                e.target,
            ));
        });
        if (options && options.key) focusSetting(options.key);
    }

    /**
     * Bring one setting's field into view, focused with its value selected.
     * The work-context settings a need opens are text inputs; on Context
     * Window they sit in a collapsed fine-tuning section, which is opened.
     *
     * @param {string} key  Setting key.
     * @returns {void}
     */
    function focusSetting(key) {
        const control = Array.from(container.querySelectorAll('[data-setting-key]'))
            .find(node => node.dataset.settingKey === key);
        if (!control) {
            console.error('[settings-system] no field for setting', key);
            return;
        }
        const fold = control.closest('details');
        if (fold) fold.open = true;
        control.scrollIntoView({ block: 'center' });
        control.focus();
        control.select();
    }

    return { render };
})();

