/**
 * BossMod AI — CLI Policy Settings tab.
 *
 * The `cli_policy` settings category: the shell executor switch, its timeout
 * and output ceiling, the approval timeout, the default policy, and the host
 * workspace roots. Ported from cli-policy-section.js unchanged.
 *
 * `SETTINGS_META` is the whole tab — the backend returns a flat key/value list
 * and this decides which keys are shown, in what order, as what control, and
 * with what description. A key the backend adds but this does not name stays
 * hidden rather than rendering as an untyped text box.
 */
const BossModCliPolicySettings = (() => {
    const esc = BossModFormat.escapeHtml;
    const escAttr = BossModFormat.escapeAttribute;
    const { icons, applySettingSaveResult } = BossModCliPolicyShared;

    const SETTINGS_META = {
        cli_shell_enabled: {
            order: 10,
            label: 'Shell Executor',
            description: 'Enable or disable the real shell executor. When disabled, all commands run in the virtual CLI only.',
            type: 'toggle',
        },
        cli_shell_timeout_seconds: {
            order: 20,
            label: 'Shell Timeout (seconds)',
            description: 'Maximum time a single shell command is allowed to execute before being killed.',
            type: 'number',
        },
        cli_shell_max_output_bytes: {
            order: 30,
            label: 'Max Output Size (bytes)',
            description: 'Maximum bytes of stdout/stderr captured from a shell command. Output beyond this limit is truncated.',
            type: 'number',
        },
        cli_approval_timeout_minutes: {
            order: 40,
            label: 'Approval Timeout (minutes)',
            description: 'How long a pending approval request remains valid before auto-expiring.',
            type: 'number',
        },
        cli_default_policy: {
            order: 50,
            label: 'Default Policy',
            description: 'What happens when a command does not match any rule. "deny" blocks the command, "approval_required" pauses for human approval.',
            type: 'select',
            options: [
                { value: 'deny', label: 'Deny' },
                { value: 'approval_required', label: 'Approval Required' },
            ],
        },
        workspace_host_roots: {
            order: 60,
            label: 'Host workspace roots',
            description: 'Optional extra directories a named absolute path may open, read, or edit. One absolute directory per line. Empty means no extra host access — Company Files stays artifacts/projects. This is not a full host mount. / , /etc, /proc, /sys, /dev, and /root are rejected.',
            type: 'textarea',
        },
    };

    /**
     * Render the Settings tab and bind every control to its own save.
     *
     * @param {Element} el  The tab content element.
     * @param {object} options
     * @param {() => string|null} options.takeFocusKey  Read-and-clear the
     *   pending `focusKey` the section was opened with (Company Files deep-links
     *   to `workspace_host_roots`). Read-and-clear rather than a plain value so
     *   a failed load leaves the key pending for the next visit, and so the
     *   re-render after a toggle save does not scroll the operator again.
     * @returns {Promise<void>} A failed load leaves an error line and binds
     *   nothing.
     */
    async function renderSettingsTab(el, { takeFocusKey }) {
        let settings = [];
        try {
            const res = await apiFetch('/api/settings?category=cli_policy');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            settings = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load CLI policy settings.</p>';
            return;
        }

        // Filter to known settings and sort by order
        const known = settings.filter(s => SETTINGS_META[s.key]);
        known.sort((a, b) => (SETTINGS_META[a.key]?.order ?? 999) - (SETTINGS_META[b.key]?.order ?? 999));

        let html = '<div class="max-w-3xl space-y-4">';
        html += `
            <p class="text-xs text-bm-muted">
                Host workspace roots can also be added from Company Files. Both edit the same allowlist — this is not a full host mount.
            </p>`;

        for (const s of known) {
            const meta = SETTINGS_META[s.key];
            html += `<div class="border border-bm-border rounded-xl p-4 bg-white transition-colors" data-setting-card="${s.key}">`;

            if (meta.type === 'toggle') {
                const isOn = s.value === 'true';
                html += `
                    <div class="flex items-center justify-between">
                        <div class="flex-1 min-w-0 mr-4">
                            <h3 class="text-sm font-semibold">${esc(meta.label)}</h3>
                            <p class="text-xs text-bm-muted mt-0.5">${esc(meta.description)}</p>
                        </div>
                        <button data-cli-setting-toggle="${s.key}"
                                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors shrink-0
                                       ${isOn ? 'bg-bm-accent' : 'bg-slate-300'}"
                                role="switch" aria-checked="${isOn}">
                            <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                                         ${isOn ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </button>
                    </div>`;
            } else if (meta.type === 'select') {
                const options = meta.options.map(opt =>
                    `<option value="${escAttr(opt.value)}" ${s.value === opt.value ? 'selected' : ''}>${esc(opt.label)}</option>`
                ).join('');
                html += `
                    <label class="block text-sm font-semibold mb-1">${esc(meta.label)}</label>
                    <p class="text-xs text-bm-muted mb-2">${esc(meta.description)}</p>
                    <select data-cli-setting-input="${s.key}"
                            class="w-full max-w-xs px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">
                        ${options}
                    </select>`;
            } else if (meta.type === 'textarea') {
                html += `
                    <label class="block text-sm font-semibold mb-1">${esc(meta.label)}</label>
                    <p class="text-xs text-bm-muted mb-2">${esc(meta.description)}</p>
                    <textarea data-cli-setting-input="${s.key}" rows="4"
                              class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono"
                              placeholder="/home/you/src">${esc(s.value || '')}</textarea>`;
            } else {
                html += `
                    <label class="block text-sm font-semibold mb-1">${esc(meta.label)}</label>
                    <p class="text-xs text-bm-muted mb-2">${esc(meta.description)}</p>
                    <input type="number" data-cli-setting-input="${s.key}"
                           value="${escAttr(s.value)}"
                           class="w-full max-w-xs px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text">`;
            }

            html += '</div>';
        }

        if (known.length === 0) {
            html += `
                <div class="text-center py-12 text-bm-muted">
                    <i data-lucide="settings" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                    <p class="text-sm">No CLI policy settings found. They will appear once the system seeds them.</p>
                </div>`;
        }

        html += '</div>';
        el.innerHTML = html;
        icons(el);

        // Toggle switches
        el.querySelectorAll('[data-cli-setting-toggle]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const key = btn.dataset.cliSettingToggle;
                const current = btn.getAttribute('aria-checked') === 'true';
                const newVal = (!current).toString();
                const card = el.querySelector(`[data-setting-card="${key}"]`);
                try {
                    await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(newVal)}&category=cli_policy`, {
                        method: 'PUT',
                    });
                    applySettingSaveResult(card, true, '');
                    renderSettingsTab(el, { takeFocusKey });
                } catch (err) {
                    applySettingSaveResult(card, false, err.message || 'Save failed');
                }
            });
        });

        // Number/select inputs
        el.querySelectorAll('[data-cli-setting-input]').forEach(input => {
            input.addEventListener('change', async (e) => {
                const key = e.target.dataset.cliSettingInput;
                const value = e.target.value;
                const card = el.querySelector(`[data-setting-card="${key}"]`);
                try {
                    await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=cli_policy`, {
                        method: 'PUT',
                    });
                    applySettingSaveResult(card, true, '');
                } catch (err) {
                    applySettingSaveResult(card, false, err.message || 'Save failed');
                }
            });
        });

        const focusKey = takeFocusKey();
        if (focusKey) {
            const card = el.querySelector(`[data-setting-card="${focusKey}"]`);
            if (card) {
                card.scrollIntoView({ block: 'center' });
                card.classList.add('ring-2', 'ring-bm-accent/40');
            }
        }
    }

    return { renderSettingsTab };
})();
