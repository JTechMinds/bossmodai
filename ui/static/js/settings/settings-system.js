/**
 * BossMod AI — Settings → System Settings (HA-STRUCT-P1-04).
 */

const SystemSection = (() => {
    const { CATEGORIES, CATEGORY_DESCRIPTIONS, SETTING_META } = BossModSystemSettingsMeta;
    let container = null;
    let activeCategory = 'simulation';

    const INPUT_CLASS = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white';

    function selectControl(setting, meta) {
        const options = meta.options || [];
        const known = options.some(opt => opt.value === setting.value);
        let html = options.map(opt => {
            const selected = setting.value === opt.value ? ' selected' : '';
            return `<option value="${BossModFormat.escapeAttribute(opt.value)}"${selected}>${BossModFormat.escapeHtml(opt.label)}</option>`;
        }).join('');
        if (setting.value && !known) {
            html += `<option value="${BossModFormat.escapeAttribute(setting.value)}" selected>${BossModFormat.escapeHtml(setting.value)}</option>`;
        }
        return `<select data-setting-key="${BossModFormat.escapeAttribute(setting.key)}"
                        data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"
                        class="${INPUT_CLASS}">${html}</select>`;
    }

    function settingControl(setting) {
        const meta = SETTING_META[setting.key] || {};
        if (meta.control === 'select') return selectControl(setting, meta);
        // A BossModSwitch is mounted here after the HTML is in the DOM.
        if (meta.control === 'switch') {
            return `<div data-setting-switch="${BossModFormat.escapeAttribute(setting.key)}"
                         data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"></div>`;
        }
        return `<input type="text"
                        data-setting-key="${BossModFormat.escapeAttribute(setting.key)}"
                        data-setting-category="${BossModFormat.escapeAttribute(setting.category)}"
                        value="${BossModFormat.escapeAttribute(setting.value)}"
                        class="${INPUT_CLASS}">`;
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
     * @param {HTMLElement} target  The input, select, or switch element to flash.
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

    async function render(el) {
        container = el;
        SettingsView.bindRepaint('system', () => render(container));
        let settings = [];
        try {
            const res = await apiFetch('/api/settings');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            settings = await res.json();
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
                    <div class="grid grid-cols-1 2xl:grid-cols-2 gap-4">`;

        for (const s of activeItems) {
            const meta = SETTING_META[s.key] || {};
            const label = meta.label || s.key;
            const description = meta.description || 'System setting.';
            // A switch row carries the label as its accessible name; a second heading would repeat it.
            const heading = meta.control === 'switch'
                ? ''
                : `<label class="block text-sm font-medium mb-1">${BossModFormat.escapeHtml(label)}</label>`;
            html += `
                    <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                        ${heading}
                        <p class="text-xs text-bm-muted mb-1.5">${BossModFormat.escapeHtml(description)}</p>
                        ${settingControl(s)}
                        <p role="alert" class="text-xs text-red-600 mt-1" data-setting-error="${BossModFormat.escapeAttribute(s.key)}"></p>
                    </div>`;
        }

        html += `
                    </div>
                </section>
            </div>`;
        el.innerHTML = html;

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

        el.querySelectorAll('.setting-input').forEach(input => {
            input.addEventListener('change', (e) => saveSetting(
                e.target.dataset.settingKey,
                e.target.dataset.settingCategory,
                e.target.value,
                e.target,
            ));
        });
    }

    return { render };
})();

