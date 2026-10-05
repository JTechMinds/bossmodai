/**
 * BossMod AI — Settings → Advanced System Settings, the Retention card.
 *
 * How much history is kept: the diagnostics row limit, the three age limits
 * the task watchdog prunes by, and how often it prunes. Split out of
 * settings-advanced.js, which renders the card into its grid and calls
 * mount() once the markup is in the document. Loaded before it.
 */

const BossModRetentionSettings = (() => {
    /**
     * One entry per control. Values are whole numbers within [min, max]; the
     * server reads them with require_int / require_positive_int, so this
     * check only stops a value the server would refuse at use from being saved.
     */
    const FIELDS = [
        {
            key: 'diagnostics_retention_limit', id: 'diag-retention-limit', min: 100, max: 50000, step: 100,
            label: 'Diagnostics Retention Limit',
            hint: 'Maximum diagnostic entries before auto-purge. Oldest entries are deleted first.',
        },
        {
            key: 'diagnostics_retention_days', id: 'diag-retention-days', min: 1,
            label: 'Diagnostics Retention (days)',
            hint: 'Diagnostic entries older than this are deleted at each history prune, whatever the limit above. Default 7.',
        },
        {
            key: 'trigger_retention_days', id: 'trigger-retention-days', min: 1,
            label: 'Finished Wake-up Retention (days)',
            hint: 'Completed and failed agent wake-ups older than this are deleted at each history prune. Waiting or running ones, and any a task or approval still refers to, are kept. Default 7.',
        },
        {
            key: 'activity_log_retention_days', id: 'activity-log-retention-days', min: 1,
            label: 'Activity Log Retention (days)',
            hint: 'Activity log entries older than this are deleted at each history prune. Default 30.',
        },
        {
            key: 'history_prune_interval_minutes', id: 'history-prune-interval', min: 1,
            label: 'History Prune Interval (minutes)',
            hint: 'How often old history is pruned by the limits above. The first prune runs shortly after the runtime starts. Default 60.',
        },
    ];

    /**
     * @param {string} raw  The input's text.
     * @param {{min: number, max?: number}} field
     * @returns {number|null} The text as a whole number in the field's range, else null.
     */
    function parse(raw, field) {
        const text = String(raw).trim();
        if (!/^\d+$/.test(text)) return null;
        const value = Number(text);
        return value >= field.min && value <= (field.max ?? Infinity) ? value : null;
    }

    /** The range the error line names, e.g. "1 or more" or "100 to 50000". */
    function rangeText(field) {
        return field.max ? `${field.min} to ${field.max}` : `${field.min} or more`;
    }

    function fieldHtml(field, setting) {
        const id = BossModFormat.escapeAttribute(field.id);
        const max = field.max ? `max="${field.max}"` : '';
        return `<div>
                        <label for="${id}" class="block text-sm font-medium mb-1">${BossModFormat.escapeHtml(field.label)}</label>
                        <p id="${id}-hint" class="text-xs text-bm-muted mb-1.5">${BossModFormat.escapeHtml(field.hint)}</p>
                        <input type="number" id="${id}" aria-describedby="${id}-hint ${id}-error"
                               value="${BossModFormat.escapeAttribute(setting ? setting.value : '')}" ${setting ? '' : 'disabled'}
                               min="${field.min}" ${max} step="${field.step ?? 1}"
                               class="w-32 px-3 py-2 text-sm border border-bm-border rounded-lg bg-bm-bg">
                        <p id="${id}-error" role="alert" class="text-xs text-red-600 mt-1">${setting ? '' : 'Not configured. Reset Seed Settings restores it.'}</p>
                    </div>`;
    }

    /**
     * The card's markup. A setting the server did not return is shown
     * disabled with a "not configured" line, never with a made-up default.
     *
     * @param {Array<{key: string, value: string}>} settings  The advanced rows.
     * @returns {string}
     */
    function html(settings) {
        const fields = FIELDS.map(field => fieldHtml(field, settings.find(s => s.key === field.key))).join('');
        return `<section class="border border-bm-border rounded-lg p-4 bg-white xl:col-span-2" aria-labelledby="retention-heading">
                    <h3 id="retention-heading" class="text-sm font-semibold">Retention</h3>
                    <p class="text-xs text-bm-muted mt-0.5 mb-3">How much history is kept. Older entries are deleted automatically.</p>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        ${fields}
                    </div>
                </section>`;
    }

    function flash(input, cls) {
        input.classList.add(cls);
        setTimeout(() => input.classList.remove(cls), 1000);
    }

    /**
     * Wire each rendered control: validate, then save on change. An invalid
     * value is reported under its field (aria-invalid) and never sent; a
     * refused save shows the server's reason there too.
     *
     * @param {Array<{key: string, value: string}>} settings  The advanced rows.
     * @returns {void}
     */
    function mount(settings) {
        for (const field of FIELDS.filter(f => settings.some(s => s.key === f.key))) {
            const input = document.getElementById(field.id);
            const error = document.getElementById(`${field.id}-error`);
            input.addEventListener('change', async () => {
                const value = parse(input.value, field);
                input.setAttribute('aria-invalid', String(value === null));
                if (value === null) {
                    error.textContent = `Enter a whole number, ${rangeText(field)}.`;
                    return;
                }
                try {
                    await apiFetchOk(`/api/settings/${encodeURIComponent(field.key)}?value=${encodeURIComponent(value)}&category=advanced`, { method: 'PUT' });
                    error.textContent = '';
                    BossModOperatorInvalidate.notifyLocal(['advanced-system']);
                    flash(input, 'border-emerald-400');
                } catch (err) {
                    error.textContent = String((err && err.message) || err);
                    flash(input, 'border-red-400');
                }
            });
        }
    }

    return { html, mount };
})();
