/**
 * BossMod AI — Settings → Advanced System Settings (HA-STRUCT-P1-04).
 */

const AdvancedSystemSection = (() => {
    async function render(el) {
        let settings = [];
        let folderOpenerMeta = { current: null, options: [] };
        try {
            const [settingsRes, openerRes] = await Promise.all([
                apiFetch('/api/settings?category=advanced'),
                apiFetch('/api/settings/desktop-open-folder-options'),
            ]);
            settings = await settingsRes.json();
            folderOpenerMeta = await openerRes.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load settings.</p>';
            return;
        }

        const diagEnabled = settings.find(s => s.key === 'diagnostics_enabled');
        const diagLimit = settings.find(s => s.key === 'diagnostics_retention_limit');
        const cliReadLimit = settings.find(s => s.key === 'cli_max_read_lines');
        const folderOpenerSetting = settings.find(s => s.key === 'desktop_open_folder_handler');
        const isEnabled = diagEnabled?.value === 'true';
        const folderOpenerOptions = folderOpenerMeta.options || [];
        const currentFolderOpener = folderOpenerMeta.current ?? folderOpenerSetting?.value ?? '';
        const hasBuiltInFolderOpener = folderOpenerOptions.some(option => option.value === currentFolderOpener);
        const folderOpenerMode = currentFolderOpener && !hasBuiltInFolderOpener ? 'custom' : 'preset';

        el.innerHTML = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">Advanced System Settings</h2>
                <p class="text-sm text-bm-muted mt-0.5">Developer tools and system diagnostics.</p>
            </div>
            <div class="max-w-6xl grid grid-cols-1 xl:grid-cols-2 gap-5">
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Enable Diagnostics</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Show the Diagnostics tab for full AI interaction visibility. Data is always captured regardless of this toggle.</p>
                        </div>
                        <button id="btn-toggle-diagnostics"
                                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors
                                       ${isEnabled ? 'bg-bm-accent' : 'bg-slate-300'}"
                                role="switch" aria-checked="${isEnabled}">
                            <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                                         ${isEnabled ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </button>
                    </div>
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Reset Seed Settings</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Overwrite editable seed settings back to their defaults, including system and runtime prompt templates.</p>
                        </div>
                        <button id="btn-reseed-settings"
                                class="px-3 py-1.5 border border-red-300 text-red-600 rounded-lg
                                       hover:bg-red-50 transition-colors text-sm font-medium">
                            Reseed
                        </button>
                    </div>
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Recreate Application DB</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Brand-new-app reset. Rebuild the entire database from the current schema and seed data. <strong class="text-red-600">This clears all agent Desk files (/me).</strong> Projects (/projects) are preserved.</p>
                        </div>
                        <button id="btn-reseed-application"
                                class="px-3 py-1.5 border border-red-300 text-red-600 rounded-lg
                                       hover:bg-red-50 transition-colors text-sm font-medium">
                            Recreate DB
                        </button>
                    </div>
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Delete All Agents</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Remove every agent, their DB history, and <strong class="text-red-600">all agent artifact files from disk</strong>. Settings and projects are preserved.</p>
                        </div>
                        <button id="btn-delete-all-agents"
                                class="px-3 py-1.5 border border-red-300 text-red-600 rounded-lg
                                       hover:bg-red-50 transition-colors text-sm font-medium whitespace-nowrap">
                            Delete All
                        </button>
                    </div>
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white xl:col-span-2">
                    <label class="block text-sm font-medium mb-1">Diagnostics Retention Limit</label>
                    <p class="text-xs text-bm-muted mb-1.5">Maximum diagnostic entries before auto-purge. Oldest entries are deleted first.</p>
                    <input type="number" id="diag-retention-limit"
                           value="${BossModUtils.escapeHtml(diagLimit?.value || '5000')}"
                           min="100" max="50000" step="100"
                           class="w-32 px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent">
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white xl:col-span-2">
                    <label class="block text-sm font-medium mb-1">CLI Read Range Limit (lines)</label>
                    <p class="text-xs text-bm-muted mb-1.5">Maximum number of lines one <code>read-range</code> command may return before the runtime requires smaller targeted reads.</p>
                    <input type="number" id="cli-read-range-limit"
                           value="${BossModUtils.escapeHtml(cliReadLimit?.value || '200')}"
                           min="10" max="5000" step="10"
                           class="w-32 px-3 py-2 text-sm border border-bm-border rounded-lg
                                  bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                  focus:border-bm-accent">
                </div>
                <div class="border border-bm-border rounded-lg p-4 bg-white xl:col-span-2">
                    <div class="flex items-start justify-between gap-4">
                        <div>
                            <h3 class="text-sm font-semibold">Folder Opener</h3>
                            <p class="text-xs text-bm-muted mt-0.5">Choose which app opens Desk folders. If left unset, BossMod will ask the first time you use Open Folder.</p>
                        </div>
                        <span class="text-xs text-bm-muted">${currentFolderOpener ? `Current: ${BossModUtils.escapeHtml(currentFolderOpener)}` : 'Current: ask on first use'}</span>
                    </div>
                    <div class="mt-3 space-y-3">
                        <label class="block text-sm font-medium">
                            <span class="block mb-1">Detected openers</span>
                            <select id="desktop-folder-opener-select"
                                    class="w-full max-w-sm px-3 py-2 text-sm border border-bm-border rounded-lg
                                           bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                           focus:border-bm-accent">
                                <option value="">Ask on first use</option>
                                ${folderOpenerOptions.map(option => `
                                    <option value="${BossModUtils.escapeHtml(option.value)}" ${folderOpenerMode === 'preset' && currentFolderOpener === option.value ? 'selected' : ''}>
                                        ${BossModUtils.escapeHtml(option.label)}
                                    </option>
                                `).join('')}
                                <option value="__custom__" ${folderOpenerMode === 'custom' ? 'selected' : ''}>Custom executable</option>
                            </select>
                        </label>
                        <label class="block text-sm font-medium">
                            <span class="block mb-1">Custom executable</span>
                            <input type="text" id="desktop-folder-opener-custom"
                                   value="${folderOpenerMode === 'custom' ? BossModUtils.escapeHtml(currentFolderOpener) : ''}"
                                   placeholder="e.g. thunar"
                                   class="w-full max-w-sm px-3 py-2 text-sm border border-bm-border rounded-lg
                                          bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                          focus:border-bm-accent">
                            <p class="text-xs text-bm-muted mt-1">Use this if your preferred file manager is not in the detected list.</p>
                        </label>
                        <div class="flex items-center gap-2">
                            <button id="btn-save-folder-opener"
                                    class="px-3 py-1.5 border border-bm-border rounded-lg hover:bg-bm-bg transition-colors text-sm font-medium">
                                Save Folder Opener
                            </button>
                            <button id="btn-reset-folder-opener"
                                    class="px-3 py-1.5 border border-bm-border rounded-lg hover:bg-bm-bg transition-colors text-sm font-medium">
                                Reset To Ask
                            </button>
                            <span id="folder-opener-status" class="text-xs text-bm-muted"></span>
                        </div>
                    </div>
                </div>
            </div>`;

        // Reseed handler
        document.getElementById('btn-reseed-settings').addEventListener('click', async () => {
            if (!confirm('Reset all editable seed settings to defaults? This will overwrite your saved system prompt template and other seed settings.')) return;
            try {
                await apiFetch('/api/settings/reseed', { method: 'POST' });
                render(el); // Re-render to show updated values
            } catch {
                alert('Reseed failed');
            }
        });

        document.getElementById('btn-delete-all-agents').addEventListener('click', async () => {
            if (!confirm('Delete ALL agents, their history, and artifact files from disk? Settings and projects are preserved.')) return;
            try {
                const res = await apiFetch('/api/agents', { method: 'DELETE' });
                if (!res.ok) throw new Error();
                const data = await res.json();
                alert(`Deleted ${data.deleted} agent(s) and their artifacts.`);
                window.location.reload();
            } catch {
                alert('Failed to delete agents');
            }
        });

        document.getElementById('btn-reseed-application').addEventListener('click', async () => {
            if (!confirm('Recreate the entire application database from the current schema? This deletes agents, tasks, chat history, diagnostics, runtime state, and clears agent Desk files (/me). Project files (/projects) are preserved.')) return;
            try {
                const res = await apiFetch('/api/settings/reseed-application', { method: 'POST' });
                if (!res.ok) throw new Error();
                window.location.reload();
            } catch {
                alert('Application reseed failed');
            }
        });

        // Toggle handler
        document.getElementById('btn-toggle-diagnostics').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const nowEnabled = btn.getAttribute('aria-checked') === 'true';
            const newValue = nowEnabled ? 'false' : 'true';

            await apiFetch(`/api/settings/diagnostics_enabled?value=${newValue}&category=advanced`, { method: 'PUT' });

            btn.setAttribute('aria-checked', String(!nowEnabled));
            btn.classList.toggle('bg-bm-accent', !nowEnabled);
            btn.classList.toggle('bg-slate-300', nowEnabled);
            btn.querySelector('span').classList.toggle('translate-x-6', !nowEnabled);
            btn.querySelector('span').classList.toggle('translate-x-1', nowEnabled);
        });

        // Retention limit handler
        document.getElementById('diag-retention-limit').addEventListener('change', async (e) => {
            const value = e.target.value;
            try {
                await apiFetch(`/api/settings/diagnostics_retention_limit?value=${encodeURIComponent(value)}&category=advanced`, { method: 'PUT' });
                e.target.classList.add('border-emerald-400');
                setTimeout(() => e.target.classList.remove('border-emerald-400'), 1000);
            } catch {
                e.target.classList.add('border-red-400');
                setTimeout(() => e.target.classList.remove('border-red-400'), 1000);
            }
        });

        document.getElementById('cli-read-range-limit').addEventListener('change', async (e) => {
            const value = e.target.value;
            try {
                await apiFetch(`/api/settings/cli_max_read_lines?value=${encodeURIComponent(value)}&category=advanced`, { method: 'PUT' });
                e.target.classList.add('border-emerald-400');
                setTimeout(() => e.target.classList.remove('border-emerald-400'), 1000);
            } catch {
                e.target.classList.add('border-red-400');
                setTimeout(() => e.target.classList.remove('border-red-400'), 1000);
            }
        });

        const openerSelect = document.getElementById('desktop-folder-opener-select');
        const openerCustom = document.getElementById('desktop-folder-opener-custom');
        const openerStatus = document.getElementById('folder-opener-status');
        const setFolderOpenerStatus = (text, isError = false) => {
            openerStatus.textContent = text;
            openerStatus.classList.toggle('text-red-500', isError);
            openerStatus.classList.toggle('text-bm-muted', !isError);
        };

        openerSelect.addEventListener('change', () => {
            if (openerSelect.value === '__custom__') {
                openerCustom.focus();
                return;
            }
            if (openerSelect.value === '') {
                openerCustom.value = '';
            }
        });

        openerCustom.addEventListener('input', () => {
            if (openerCustom.value.trim()) {
                openerSelect.value = '__custom__';
            }
        });

        document.getElementById('btn-save-folder-opener').addEventListener('click', async () => {
            const selected = openerSelect.value;
            const resolvedValue = selected === '__custom__' ? openerCustom.value.trim() : selected;
            try {
                await apiFetch(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(resolvedValue)}&category=advanced`, { method: 'PUT' });
                setFolderOpenerStatus(resolvedValue ? `Saved: ${resolvedValue}` : 'BossMod will ask on first use.');
            } catch {
                setFolderOpenerStatus('Failed to save folder opener.', true);
            }
        });

        document.getElementById('btn-reset-folder-opener').addEventListener('click', async () => {
            try {
                await apiFetch('/api/settings/desktop_open_folder_handler?value=&category=advanced', { method: 'PUT' });
                openerSelect.value = '';
                openerCustom.value = '';
                setFolderOpenerStatus('BossMod will ask on first use.');
            } catch {
                setFolderOpenerStatus('Failed to reset folder opener.', true);
            }
        });

    }

    return { render };
})();
