/**
 * BossMod AI — Settings → AI Connections, the connection list.
 *
 * Reads `/api/connections` and renders one card per provider connection, with
 * the edit and delete actions. Creating and editing one is
 * settings-connections-form.js: this file reads, that file writes, which is
 * the seam the file was split at in Phase 3C.
 *
 * A saved API key is never returned by the backend after save — only its last
 * four digits — so the card shows a mask and says so, rather than an empty
 * field that would read as "no key".
 */

const ConnectionsSection = (() => {
    let container = null;


    async function render(el) {
        container = el;
        await renderList();
    }

    async function renderList() {
        let connections = [];
        try {
            const res = await apiFetch('/api/connections');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

        // Bind events
        const addBtn = document.getElementById('btn-add-connection');
        if (addBtn) addBtn.addEventListener('click', () => openForm(null));

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
