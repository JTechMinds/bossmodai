/**
 * BossMod AI — Settings → AI Connections (HA-STRUCT-P1-04).
 */

const ConnectionsSection = (() => {
    let container = null;

    async function copyApiKey(value, statusEl = null) {
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            if (statusEl) {
                statusEl.textContent = 'Copied';
                setTimeout(() => {
                    if (statusEl.textContent === 'Copied') statusEl.textContent = '';
                }, 1500);
            }
        } catch {
            if (statusEl) statusEl.textContent = 'Copy failed';
        }
    }

    function bindApiKeyFieldControls(root = document) {
        root.querySelectorAll('[data-toggle-api-key]').forEach(btn => {
            btn.addEventListener('click', () => {
                const targetId = btn.dataset.toggleApiKey;
                const input = document.getElementById(targetId);
                if (!input) return;
                const isHidden = input.type === 'password';
                input.type = isHidden ? 'text' : 'password';
                btn.textContent = isHidden ? 'Hide' : 'Show';
            });
        });

        root.querySelectorAll('[data-copy-api-key]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const targetId = btn.dataset.copyApiKey;
                const source = document.getElementById(targetId);
                const statusEl = btn.dataset.copyStatus ? document.getElementById(btn.dataset.copyStatus) : null;
                if (!source) return;
                await copyApiKey(source.value, statusEl);
            });
        });
    }

    async function render(el) {
        container = el;
        await renderList();
    }

    async function renderList() {
        let connections = [];
        try {
            const res = await apiFetch('/api/connections');
            connections = await res.json();
        } catch (err) {
            container.innerHTML = '<p class="text-red-500 text-sm">Failed to load connections.</p>';
            return;
        }

        let html = `
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-lg font-semibold">AI Connections</h2>
                    <p class="text-sm text-bm-muted mt-0.5">Manage your LLM provider API connections.</p>
                </div>
                <button id="btn-add-connection"
                        class="flex items-center gap-2 px-3 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    <i data-lucide="plus" class="w-4 h-4"></i> Add Connection
                </button>
            </div>`;

        if (connections.length === 0) {
            html += `<div class="text-center py-12 text-bm-muted">
                <i data-lucide="plug" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                <p class="text-sm">No connections yet. Add your first AI provider.</p>
            </div>`;
        } else {
            html += '<div class="space-y-3">';
            for (const conn of connections) {
                const maskedKey = conn.has_api_key
                    ? `••••${BossModUtils.escapeHtml(conn.api_key_last4 || '')}`
                    : 'No API key';
                html += `
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-start justify-between">
                        <div class="min-w-0">
                            <div class="flex items-center gap-2">
                                <h3 class="font-medium">${BossModUtils.escapeHtml(conn.name)}</h3>
                                ${conn.model ? `<span class="text-xs px-2 py-0.5 bg-slate-100 rounded-full text-bm-muted">${BossModUtils.escapeHtml(conn.model)}</span>` : ''}
                            </div>
                            <p class="text-sm text-bm-muted mt-1">${BossModUtils.escapeHtml(conn.api_base_url)}</p>
                            <div class="mt-1.5">
                                <p class="text-xs font-mono text-bm-muted">${maskedKey}</p>
                                <p class="text-[11px] text-bm-muted mt-1">Full API keys are never returned after save. Re-enter a key only when rotating it.</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-1 shrink-0 ml-4">
                            <button data-edit-conn="${conn.id}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                                    title="Edit">
                                <i data-lucide="pencil" class="w-4 h-4 text-bm-muted"></i>
                            </button>
                            <button data-delete-conn="${conn.id}"
                                    class="p-1.5 rounded-lg hover:bg-red-50 transition-colors"
                                    title="Delete">
                                <i data-lucide="trash-2" class="w-4 h-4 text-red-400"></i>
                            </button>
                        </div>
                    </div>
                </div>`;
            }
            html += '</div>';
        }

        container.innerHTML = html;
        if (window.lucide) lucide.createIcons({ nodes: [container] });
        bindApiKeyFieldControls(container);

        // Bind events
        const addBtn = document.getElementById('btn-add-connection');
        if (addBtn) addBtn.addEventListener('click', () => renderForm(null));

        container.querySelectorAll('[data-edit-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const res = await apiFetch(`/api/connections/${btn.dataset.editConn}`);
                if (res.ok) renderForm(await res.json());
            });
        });

        container.querySelectorAll('[data-delete-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this connection?')) return;
                try {
                    await apiFetchOk(`/api/connections/${btn.dataset.deleteConn}`, { method: 'DELETE' });
                    await renderList();
                    if (typeof BossModApp !== 'undefined' && typeof BossModApp.refreshModelAvailability === 'function') {
                        void BossModApp.refreshModelAvailability();
                    }
                } catch (err) {
                    alert(err.message || 'Failed to delete connection.');
                }
            });
        });
    }

    function renderForm(conn) {
        const isEdit = !!conn;
        container.innerHTML = `
            <div class="max-w-lg">
                <h2 class="text-lg font-semibold mb-1">${isEdit ? 'Edit Connection' : 'New Connection'}</h2>
                <p class="text-sm text-bm-muted mb-6">${isEdit ? 'Update this connection.' : 'Add a new LLM provider connection.'}</p>
                <form id="connection-form" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Connection Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">This is what you'll see when selecting a connection for an agent.</p>
                        <input type="text" name="name" required
                               value="${BossModUtils.escapeHtml(conn?.name || '')}"
                               placeholder="e.g. OpenAI Production"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">API Base URL</label>
                        <p class="text-xs text-bm-muted mb-1.5">The exact provider base URL. Use something like <code>https://api.openai.com/v1</code>, not <code>/chat/completions</code>.</p>
                        <input type="url" name="api_base_url" required
                               value="${BossModUtils.escapeHtml(conn?.api_base_url || '')}"
                               placeholder="https://api.openai.com/v1"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">API Key</label>
                        <p class="text-xs text-bm-muted mb-1.5">${isEdit && conn?.has_api_key
                            ? `A key is saved (last 4: ${BossModUtils.escapeHtml(conn.api_key_last4 || '')}). Leave blank to keep it, or enter a new key to rotate.`
                            : 'Optional. Leave blank for local OpenAI-compatible servers. The runtime supplies a harmless transport placeholder when the upstream library requires one.'}</p>
                        <div class="flex gap-2">
                            <input id="connection-api-key-input" type="password" name="api_key"
                                   value=""
                                   placeholder="${isEdit && conn?.has_api_key ? '••••' + BossModUtils.escapeHtml(conn.api_key_last4 || '') : 'sk-...'}"
                                   class="flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg
                                          bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                          focus:border-bm-accent">
                            <button type="button"
                                    data-toggle-api-key="connection-api-key-input"
                                    class="px-3 py-2 border border-bm-border rounded-lg hover:bg-slate-50 transition-colors text-sm font-medium">
                                Show
                            </button>
                            <button type="button"
                                    data-copy-api-key="connection-api-key-input"
                                    data-copy-status="connection-api-key-status"
                                    class="px-3 py-2 border border-bm-border rounded-lg hover:bg-slate-50 transition-colors text-sm font-medium">
                                Copy
                            </button>
                        </div>
                        <p id="connection-api-key-status" class="text-[11px] text-bm-muted mt-1"></p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Model Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">Model name exposed by the server. Raw names like <code>llama3</code> work for local OpenAI-compatible endpoints; provider-prefixed names also work.</p>
                        <input type="text" name="model"
                               value="${BossModUtils.escapeHtml(conn?.model || '')}"
                               placeholder="e.g. llama3 or openai/gpt-4.1-mini"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Extra Body Params</label>
                        <p class="text-xs text-bm-muted mb-1.5">Optional JSON merged into every request body. For provider-specific fields.</p>
                        <textarea name="extra_body" rows="3"
                                  placeholder='e.g. {"stream": false, "thinking": {"type": "disabled"}}'
                                  class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                         bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                         focus:border-bm-accent font-mono">${BossModUtils.escapeHtml(conn?.extra_body || '')}</textarea>
                    </div>
                    <div id="connection-save-status" class="hidden p-3 rounded-lg text-sm"></div>
                    <div id="test-conn-result" class="hidden p-3 rounded-lg text-sm"></div>
                    <div class="flex gap-2 pt-2">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            ${isEdit ? 'Save Changes' : 'Create Connection'}
                        </button>
                        <button type="button" id="btn-test-conn"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Test Connection
                        </button>
                        <button type="button" id="btn-cancel-conn"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        document.getElementById('btn-cancel-conn').addEventListener('click', renderList);
        bindApiKeyFieldControls(container);

        document.getElementById('btn-test-conn').addEventListener('click', async () => {
            const form = document.getElementById('connection-form');
            const fd = new FormData(form);
            const resultEl = document.getElementById('test-conn-result');

            resultEl.className = 'p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
            resultEl.textContent = 'Testing connection...';
            resultEl.classList.remove('hidden');

            try {
                const resp = await apiFetch('/api/connections/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_base_url: fd.get('api_base_url'),
                        api_key: fd.get('api_key') || null,
                        model: fd.get('model') || null,
                        connection_id: isEdit ? conn.id : null,
                    }),
                });
                const result = await resp.json().catch(() => ({}));

                if (!resp.ok || !result.ok) {
                    const formatError = window.BossModApi && window.BossModApi.formatError;
                    const message = (formatError
                        ? formatError(result, resp.status)
                        : (result.error || result.detail || `Test failed (${resp.status})`));
                    resultEl.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    resultEl.textContent = message;
                } else {
                    const isWarning = !!result.warning;
                    resultEl.className = isWarning
                        ? 'p-3 rounded-lg text-sm bg-amber-50 border border-amber-200 text-amber-700'
                        : 'p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-700';

                    const msg = isWarning
                        ? result.warning
                        : `Connected — ${result.models_count} model${result.models_count !== 1 ? 's' : ''} available`;

                    let html = `<p class="font-medium">${BossModUtils.escapeHtml(msg)}</p>`;
                    if (result.models && result.models.length > 0) {
                        html += `<p class="mt-2 mb-1 text-xs font-semibold opacity-70 uppercase tracking-wide">Available models</p>`;
                        html += `<div class="flex flex-wrap gap-1.5">`;
                        for (const m of result.models) {
                            html += `<span class="px-2 py-0.5 rounded text-xs font-mono ${isWarning ? 'bg-amber-100' : 'bg-emerald-100'}">${BossModUtils.escapeHtml(m)}</span>`;
                        }
                        if (result.models_count > result.models.length) {
                            html += `<span class="px-2 py-0.5 text-xs opacity-60">+${result.models_count - result.models.length} more</span>`;
                        }
                        html += `</div>`;
                    }
                    resultEl.innerHTML = html;
                }
            } catch {
                resultEl.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                resultEl.textContent = 'Request failed — check your network';
            }
        });

        document.getElementById('connection-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const data = {
                name: fd.get('name'),
                api_base_url: fd.get('api_base_url'),
                model: fd.get('model') || null,
                extra_body: fd.get('extra_body')?.trim() || null,
            };
            const enteredKey = fd.get('api_key');
            if (enteredKey) {
                data.api_key = enteredKey;
            } else if (!isEdit) {
                data.api_key = null;
            }
            const status = document.getElementById('connection-save-status');
            if (status) {
                status.className = 'p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
                status.textContent = 'Saving...';
                status.classList.remove('hidden');
            }
            try {
                if (isEdit) {
                    await apiFetchOk(`/api/connections/${conn.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    await apiFetchOk('/api/connections', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                await renderList();
                if (typeof BossModApp !== 'undefined' && typeof BossModApp.refreshModelAvailability === 'function') {
                    void BossModApp.refreshModelAvailability();
                }
            } catch (err) {
                console.error('[Connections] Save failed:', err);
                if (status) {
                    status.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                    status.textContent = err.message || 'Save failed';
                    status.classList.remove('hidden');
                }
            }
        });
    }

    return { render };
})();

