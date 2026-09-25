/**
 * BossMod AI — Settings → AI Connections, the connection list.
 *
 * Reads `/api/connections` and renders one card per provider connection, with
 * the edit, duplicate and delete actions. Creating and editing one is
 * settings-connections-form.js: this file reads, that file writes, which is
 * the seam the file was split at in Phase 3C.
 *
 * A saved API key is never returned by the backend after save — only its last
 * four digits — so the card shows a mask and says so, rather than an empty
 * field that would read as "no key".
 */

const ConnectionsSection = (() => {
    let container = null;

    const SYSTEM_AI_COPY = 'Choose the AI used for system processes (compaction, channel router, etc.).';
    const INPUT_CLASS = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white';

    function connectionLabel(conn) {
        return conn.model ? `${conn.name} (${conn.model})` : conn.name;
    }

    /**
     * System AI dropdown. Options are the current connections, labeled
     * `name (model)`. A saved id that is still in the list stays selected.
     * An unset id, or one that is no longer listed, shows the first
     * connection. This render does not write the setting.
     *
     * @param {string} savedId
     * @param {object[]} connections
     * @param {boolean} connectionsFailed
     * @returns {string}
     */
    function systemAiControl(savedId, connections, connectionsFailed) {
        const current = savedId || '';
        const known = connections.some(conn => conn.id === current);
        const fallback = !connectionsFailed && connections.length ? connections[0].id : '';
        const selectedId = known ? current : (connectionsFailed ? current : fallback);
        let options = '';
        if (connectionsFailed) {
            if (current) {
                const missing = 'Saved connection unavailable';
                options += `<option value="${BossModFormat.escapeAttribute(current)}" title="${BossModFormat.escapeAttribute(missing)}" selected>${BossModFormat.escapeHtml(missing)}</option>`;
            }
        } else {
            for (const conn of connections) {
                const label = connectionLabel(conn);
                const selected = selectedId === conn.id ? ' selected' : '';
                options += `<option value="${BossModFormat.escapeAttribute(conn.id)}" title="${BossModFormat.escapeAttribute(label)}"${selected}>${BossModFormat.escapeHtml(label)}</option>`;
            }
            if (!connections.length && current) {
                const missing = 'Saved connection unavailable';
                options += `<option value="${BossModFormat.escapeAttribute(current)}" title="${BossModFormat.escapeAttribute(missing)}" selected>${BossModFormat.escapeHtml(missing)}</option>`;
            }
        }
        let hint = '';
        if (connectionsFailed) {
            hint = '<p class="text-xs text-bm-muted mt-1.5">AI connections could not be loaded.</p>';
        } else if (!connections.length) {
            hint = '<p class="text-xs text-bm-muted mt-1.5">No AI connections yet.</p>';
        }
        const disabled = options ? '' : ' disabled';
        return `<select data-setting-key="system_ai_connection"
                        data-setting-category="llm"
                        class="${INPUT_CLASS}"${disabled}>${options}</select>${hint}`;
    }

    /**
     * @param {{savedId: string, connections: object[], connectionsFailed: boolean, settingsFailed: boolean}} state
     * @returns {string}
     */
    function systemAiBlock(state) {
        const body = state.settingsFailed
            ? '<p class="text-xs text-bm-muted">System AI could not be loaded.</p>'
            : systemAiControl(state.savedId, state.connections, state.connectionsFailed);
        return `
            <div class="mt-3 max-w-2xl" data-system-ai>
                <label class="block text-sm font-medium mb-1">System AI</label>
                <p class="text-xs text-bm-muted mb-1.5">${BossModFormat.escapeHtml(SYSTEM_AI_COPY)}</p>
                ${body}
            </div>`;
    }

    async function render(el) {
        container = el;
        SettingsView.bindRepaint('connections', () => renderList());
        await renderList();
    }

    async function renderList() {
        const state = {
            connections: [],
            connectionsFailed: false,
            savedId: '',
            settingsFailed: false,
        };
        try {
            const res = await apiFetch('/api/connections');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const body = await res.json();
            state.connections = Array.isArray(body) ? body : [];
        } catch (err) {
            state.connectionsFailed = true;
        }
        try {
            const res = await apiFetch('/api/settings');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const settings = await res.json();
            const row = Array.isArray(settings)
                ? settings.find(item => item.key === 'system_ai_connection')
                : null;
            state.savedId = row && row.value ? String(row.value) : '';
        } catch (err) {
            state.settingsFailed = true;
        }

        const connections = state.connections;
        let html = `
            <div class="flex items-start justify-between gap-4 mb-6">
                <div class="min-w-0 flex-1">
                    <h2 class="text-lg font-semibold">AI Connections</h2>
                    ${systemAiBlock(state)}
                    <p class="text-sm text-bm-muted mt-4">Manage your LLM provider API connections.</p>
                </div>
                <button id="btn-add-connection"
                        class="flex items-center gap-2 px-3 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium shrink-0">
                    <i data-lucide="plus" class="w-4 h-4"></i> Add Connection
                </button>
            </div>`;

        if (state.connectionsFailed) {
            html += '<p class="text-red-500 text-sm">Failed to load connections.</p>';
        } else if (connections.length === 0) {
            html += `<div class="text-center py-12 text-bm-muted">
                <i data-lucide="plug" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                <p class="text-sm">No connections yet. Add your first AI provider.</p>
            </div>`;
        } else {
            html += '<div class="space-y-3">';
            for (const conn of connections) {
                const maskedKey = conn.has_api_key
                    ? `••••${BossModFormat.escapeHtml(conn.api_key_last4 || '')}`
                    : 'No API key';
                html += `
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-start justify-between">
                        <div class="min-w-0">
                            <div class="flex items-center gap-2">
                                <h3 class="font-medium">${BossModFormat.escapeHtml(conn.name)}</h3>
                                ${conn.model ? `<span class="text-xs px-2 py-0.5 bg-slate-100 rounded-full text-bm-muted">${BossModFormat.escapeHtml(conn.model)}</span>` : ''}
                            </div>
                            <p class="text-sm text-bm-muted mt-1">${BossModFormat.escapeHtml(conn.api_base_url)}</p>
                            <div class="mt-1.5">
                                <p class="text-xs font-mono text-bm-muted">${maskedKey}</p>
                                <p class="text-[11px] text-bm-muted mt-1">Full API keys are never returned after save. Re-enter a key only when rotating it.</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-1 shrink-0 ml-4">
                            <button data-edit-conn="${BossModFormat.escapeAttribute(conn.id)}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                                    title="Edit">
                                <i data-lucide="pencil" class="w-4 h-4 text-bm-muted"></i>
                            </button>
                            <button data-duplicate-conn="${BossModFormat.escapeAttribute(conn.id)}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                                    title="Duplicate">
                                <i data-lucide="copy" class="w-4 h-4 text-bm-muted"></i>
                            </button>
                            <button data-delete-conn="${BossModFormat.escapeAttribute(conn.id)}"
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
        BossModIcons.paint(container, 'settings-connections');

        // Bind events. The System AI select saves only after the operator
        // changes it. Painting a fallback does not write the stored pick.
        const addBtn = document.getElementById('btn-add-connection');
        if (addBtn) addBtn.addEventListener('click', () => openForm(null));

        container.querySelectorAll('.setting-input').forEach(input => {
            input.addEventListener('change', async (e) => {
                const key = e.target.dataset.settingKey;
                const category = e.target.dataset.settingCategory;
                const value = e.target.value;
                try {
                    await apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`, {
                        method: 'PUT',
                    });
                    BossModOperatorInvalidate.notifyLocal(['connections']);
                    e.target.classList.add('border-emerald-400');
                    setTimeout(() => e.target.classList.remove('border-emerald-400'), 1000);
                } catch {
                    e.target.classList.add('border-red-400');
                    showRowError(container, 'System AI could not be saved.');
                    setTimeout(() => e.target.classList.remove('border-red-400'), 1000);
                }
            });
        });

        container.querySelectorAll('[data-edit-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const res = await apiFetch(`/api/connections/${btn.dataset.editConn}`);
                // A detected failure that does nothing is the same defect as
                // an undetected one: the operator clicks Edit and the app sits
                // there. Same fix, same reason as the load paths above.
                if (!res.ok) {
                    showRowError(container, 'This connection could not be opened.');
                    return;
                }
                openForm(await res.json());
            });
        });

        container.querySelectorAll('[data-duplicate-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                // apiFetchOk for the mutation, as every other write in the
                // settings tree does; the catch is what turns its throw into
                // the same answer Edit gives above, because a detected failure
                // that does nothing leaves the operator clicking a button that
                // never replies. Parsing happens past the catch so a bad body
                // cannot be reported as a failed duplicate.
                let res;
                try {
                    res = await apiFetchOk(
                        `/api/connections/${btn.dataset.duplicateConn}/duplicate`,
                        { method: 'POST' },
                    );
                } catch {
                    showRowError(container, 'This connection could not be duplicated.');
                    return;
                }
                const created = await res.json();
                // The copy is identical to its source, so the list alone would
                // leave the operator hunting for which row is new. Repaint so
                // the copy exists behind the form, then open it: the rename and
                // the one field they came to change are the point of the copy.
                await renderList();
                openForm(created);
                // No refreshModelAvailability() here, unlike Delete: a copy of a
                // connection cannot change whether a usable model exists.
            });
        });

        container.querySelectorAll('[data-delete-conn]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this connection?')) return;
                try {
                    await apiFetchOk(`/api/connections/${btn.dataset.deleteConn}`, { method: 'DELETE' });
                    await renderList();
                    // BossModApp died with the dock shell; the banner moved to
                    // shell/banners.js. The old typeof guard silently swallowed
                    // this call, so the no-model banner outlived the change
                    // that fixed it. Unguarded on purpose: a missing module is
                    // a defect, not a condition to tiptoe around.
                    void BossModBanners.refreshModelAvailability();
                } catch (err) {
                    alert(err.message || 'Failed to delete connection.');
                }
            });
        });
    }

    /**
     * Open the connection form over the list.
     *
     * @param {object|null} conn  The connection to edit, or null to create.
     * @returns {void}
     */
    function openForm(conn) {
        BossModConnectionForm.renderForm(conn, { container, onDone: renderList });
    }

    return { render };
})();
