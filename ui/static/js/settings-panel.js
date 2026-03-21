/**
 * BossMod AI — Settings panel overlay.
 *
 * Fetches all settings from /api/settings, groups by category,
 * and renders editable fields. Changes are saved via PUT /api/settings/{key}.
 */

const SettingsPanel = (() => {
    // Category display names and order
    const CATEGORIES = [
        { key: 'llm',        label: 'LLM Models' },
        { key: 'simulation', label: 'Simulation' },
        { key: 'social',     label: 'Social Triggers' },
        { key: 'context',    label: 'Context Window' },
        { key: 'general',    label: 'General' },
    ];

    // Setting key → human-readable label
    const LABELS = {
        default_model_social:     'Default Model — Social',
        default_model_work:       'Default Model — Work',
        default_model_reasoning:  'Default Model — Reasoning',
        default_model_extraction: 'Default Model — Extraction',
        default_model_self_queue: 'Default Model — Self Queue',
        default_temperature:      'Temperature',
        default_max_tokens:       'Max Tokens',
        tick_interval:            'Tick Interval (seconds)',
        steps_per_tick:           'Steps Per Tick',
        social_idle_threshold_minutes: 'Idle Threshold (minutes)',
        social_cooldown_minutes:  'Cooldown (minutes)',
        social_proximity_tiles:   'Proximity (tiles)',
        context_window_messages:  'Message Window Size',
    };

    // Setting key → placeholder hint
    const PLACEHOLDERS = {
        default_model_social:     'e.g. gpt-4o-mini, claude-haiku-4-5-20251001, ollama/llama3',
        default_model_work:       'e.g. gpt-4o, claude-sonnet-4-5-20250514',
        default_model_reasoning:  'e.g. gpt-4o, claude-sonnet-4-5-20250514',
        default_model_extraction: 'e.g. gpt-4o-mini, claude-haiku-4-5-20251001',
        default_model_self_queue: 'e.g. gpt-4o-mini, claude-haiku-4-5-20251001',
    };

    // ─── Panel open/close ───

    function init() {
        const closeBtn = document.getElementById('settings-panel-close');
        const backdrop = document.getElementById('settings-panel-backdrop');

        if (closeBtn) closeBtn.addEventListener('click', close);
        if (backdrop) backdrop.addEventListener('click', close);

        document.addEventListener('keydown', (e) => {
            const overlay = document.getElementById('settings-panel-overlay');
            if (e.key === 'Escape' && overlay && overlay.classList.contains('open')) {
                close();
            }
        });
    }

    async function open() {
        BossModUtils.openOverlay('settings-panel-overlay');
        await loadSettings();
    }

    function close() {
        BossModUtils.closeOverlay('settings-panel-overlay', 'settings-panel');
    }

    // ─── Load and render settings ───

    async function loadSettings() {
        const body = document.getElementById('settings-panel-body');

        try {
            const res = await fetch('/api/settings');
            const settings = await res.json();

            // Group by category
            const groups = {};
            for (const s of settings) {
                const cat = s.category || 'general';
                if (!groups[cat]) groups[cat] = [];
                groups[cat].push(s);
            }

            // Render
            let html = '';
            for (const cat of CATEGORIES) {
                const items = groups[cat.key];
                if (!items || items.length === 0) continue;

                html += `<div class="mb-6">
                    <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide mb-3">${cat.label}</h3>
                    <div class="space-y-3">`;

                for (const s of items) {
                    const label = LABELS[s.key] || s.key;
                    const placeholder = PLACEHOLDERS[s.key] || '';
                    html += `
                    <div>
                        <label class="block text-sm font-medium mb-1">${BossModUtils.escapeHtml(label)}</label>
                        <input type="text"
                               data-setting-key="${BossModUtils.escapeHtml(s.key)}"
                               data-setting-category="${BossModUtils.escapeHtml(s.category)}"
                               value="${BossModUtils.escapeHtml(s.value)}"
                               placeholder="${BossModUtils.escapeHtml(placeholder)}"
                               class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>`;
                }

                html += `</div></div>`;
            }

            // Render any uncategorized settings
            const knownCats = new Set(CATEGORIES.map(c => c.key));
            for (const [cat, items] of Object.entries(groups)) {
                if (knownCats.has(cat)) continue;
                html += `<div class="mb-6">
                    <h3 class="text-sm font-semibold text-bm-muted uppercase tracking-wide mb-3">${BossModUtils.escapeHtml(cat)}</h3>
                    <div class="space-y-3">`;
                for (const s of items) {
                    html += `
                    <div>
                        <label class="block text-sm font-medium mb-1">${BossModUtils.escapeHtml(s.key)}</label>
                        <input type="text"
                               data-setting-key="${BossModUtils.escapeHtml(s.key)}"
                               data-setting-category="${BossModUtils.escapeHtml(s.category)}"
                               value="${BossModUtils.escapeHtml(s.value)}"
                               class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>`;
                }
                html += `</div></div>`;
            }

            body.innerHTML = html;

            // Bind change events — save on blur
            body.querySelectorAll('.setting-input').forEach(input => {
                input.addEventListener('change', handleSettingChange);
            });

        } catch (err) {
            body.textContent = '';
            const errDiv = document.createElement('div');
            errDiv.className = 'text-red-500 text-sm text-center mt-8';
            const p1 = document.createElement('p');
            p1.textContent = 'Failed to load settings';
            const p2 = document.createElement('p');
            p2.className = 'text-xs mt-1';
            p2.textContent = String(err);
            errDiv.appendChild(p1);
            errDiv.appendChild(p2);
            body.appendChild(errDiv);
        }
    }

    // ─── Save setting on change ───

    async function handleSettingChange(e) {
        const input = e.target;
        const key = input.dataset.settingKey;
        const category = input.dataset.settingCategory;
        const value = input.value;

        try {
            await fetch(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`, {
                method: 'PUT',
            });

            // Flash green border to confirm save
            input.classList.add('border-emerald-400');
            setTimeout(() => input.classList.remove('border-emerald-400'), 1000);
        } catch (err) {
            input.classList.add('border-red-400');
            setTimeout(() => input.classList.remove('border-red-400'), 1000);
            console.error('[Settings] Save failed:', err);
        }
    }

    return { init, open, close };
})();

document.addEventListener('DOMContentLoaded', SettingsPanel.init);
