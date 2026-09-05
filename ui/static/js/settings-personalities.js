/**
 * BossMod AI — Settings → AI Personalities (HA-STRUCT-P1-04).
 */

const PersonalitiesSection = (() => {
    let container = null;

    async function render(el) {
        container = el;
        await renderList();
    }

    async function renderList() {
        let personalities = [];
        try {
            const res = await apiFetch('/api/personalities');
            personalities = await res.json();
        } catch (err) {
            container.innerHTML = '<p class="text-red-500 text-sm">Failed to load personalities.</p>';
            return;
        }

        let html = `
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-lg font-semibold">AI Personalities</h2>
                    <p class="text-sm text-bm-muted mt-0.5">Define reusable prompt templates for agent roles.</p>
                </div>
                <button id="btn-add-personality"
                        class="flex items-center gap-2 px-3 py-2 bg-bm-accent text-white rounded-lg
                               hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                    <i data-lucide="plus" class="w-4 h-4"></i> Add Personality
                </button>
            </div>`;

        if (personalities.length === 0) {
            html += `<div class="text-center py-12 text-bm-muted">
                <i data-lucide="brain" class="w-10 h-10 mx-auto mb-3 opacity-40"></i>
                <p class="text-sm">No personalities yet. Create your first agent role template.</p>
            </div>`;
        } else {
            html += '<div class="space-y-3">';
            for (const p of personalities) {
                const preview = p.prompt_template.length > 120
                    ? p.prompt_template.slice(0, 120) + '...'
                    : p.prompt_template;
                html += `
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-start justify-between">
                        <div class="min-w-0 flex-1">
                            <h3 class="font-medium">${BossModUtils.escapeHtml(p.name)}</h3>
                            <p class="text-sm text-bm-muted mt-1 line-clamp-2">${BossModUtils.escapeHtml(preview)}</p>
                        </div>
                        <div class="flex items-center gap-1 shrink-0 ml-4">
                            <button data-edit-pers="${p.id}"
                                    class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                                    title="Edit">
                                <i data-lucide="pencil" class="w-4 h-4 text-bm-muted"></i>
                            </button>
                            <button data-delete-pers="${p.id}"
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

        const addBtn = document.getElementById('btn-add-personality');
        if (addBtn) addBtn.addEventListener('click', () => renderForm(null));

        container.querySelectorAll('[data-edit-pers]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const res = await apiFetch(`/api/personalities/${btn.dataset.editPers}`);
                if (res.ok) renderForm(await res.json());
            });
        });

        container.querySelectorAll('[data-delete-pers]').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Delete this personality?')) return;
                await apiFetch(`/api/personalities/${btn.dataset.deletePers}`, { method: 'DELETE' });
                await renderList();
            });
        });
    }

    function renderForm(p) {
        const isEdit = !!p;
        container.innerHTML = `
            <div class="max-w-2xl">
                <h2 class="text-lg font-semibold mb-1">${isEdit ? 'Edit Personality' : 'New Personality'}</h2>
                <p class="text-sm text-bm-muted mb-6">${isEdit ? 'Update this personality template.' : 'Create a reusable prompt template for agent roles.'}</p>
                <form id="personality-form" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Name</label>
                        <p class="text-xs text-bm-muted mb-1.5">A short label used in dropdowns and the settings list so people can recognize this personality at a glance.</p>
                        <input type="text" name="name" required
                               value="${BossModUtils.escapeHtml(p?.name || '')}"
                               placeholder="e.g. Product Manager, Code Reviewer"
                               class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                      bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                      focus:border-bm-accent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Prompt Template</label>
                        <p class="text-xs text-bm-muted mb-1.5">The system prompt that defines this personality's behavior. Supports the same conditional template syntax used by authored prompt templates.</p>
                        <textarea name="prompt_template" required rows="12"
                                  placeholder="You are a senior product manager focused on clarity, prioritization, and stakeholder communication..."
                                  class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg
                                         bg-bm-bg focus:outline-none focus:ring-2 focus:ring-bm-accent/30
                                         focus:border-bm-accent resize-y font-mono">${BossModUtils.escapeHtml(p?.prompt_template || '')}</textarea>
                    </div>
                    <div id="personality-save-status" class="hidden p-3 rounded-lg text-sm"></div>
                    <div class="flex gap-2 pt-2">
                        <button type="submit"
                                class="px-4 py-2 bg-bm-accent text-white rounded-lg
                                       hover:bg-bm-accent-hover transition-colors text-sm font-medium">
                            ${isEdit ? 'Save Changes' : 'Create Personality'}
                        </button>
                        <button type="button" id="btn-cancel-pers"
                                class="px-4 py-2 border border-bm-border rounded-lg
                                       hover:bg-slate-50 transition-colors text-sm font-medium">
                            Cancel
                        </button>
                    </div>
                </form>
            </div>`;

        document.getElementById('btn-cancel-pers').addEventListener('click', renderList);
        document.getElementById('personality-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const data = {
                name: fd.get('name'),
                prompt_template: fd.get('prompt_template'),
            };
            const status = document.getElementById('personality-save-status');
            status.className = 'p-3 rounded-lg text-sm bg-slate-50 border border-bm-border text-bm-muted';
            status.textContent = 'Saving...';
            try {
                let res;
                if (isEdit) {
                    res = await apiFetch(`/api/personalities/${p.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                } else {
                    res = await apiFetch('/api/personalities', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    });
                }
                if (!res.ok) {
                    const payload = await res.json().catch(() => ({}));
                    throw new Error(payload.detail || 'Save failed');
                }
                await renderList();
            } catch (err) {
                console.error('[Personalities] Save failed:', err);
                status.className = 'p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-700';
                status.textContent = err.message || 'Save failed';
            }
        });
    }

    return { render };
})();
