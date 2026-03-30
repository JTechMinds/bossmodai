/**
 * BossMod AI — Company File Operations.
 * CRUD dialogs and API calls for create, rename, delete, move, copy.
 */
const CompanyFileOps = (() => {
    const esc = BossModUtils.escapeHtml;

    // ─── API helpers ───

    async function apiPost(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const text = await res.text();
            throw new Error(text);
        }
        return res.json();
    }

    async function apiPatch(url, body) {
        const res = await fetch(url, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function apiDelete(url, body) {
        const res = await fetch(url, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    // ─── Create File / Folder ───

    function showCreateDialog(parentPath, kind, { onComplete } = {}) {
        const kindLabel = kind === 'folder' ? 'Folder' : 'File';
        const modal = BossModUtils.createModal({ maxWidth: 'max-w-md' });

        modal.panel.innerHTML = `
            <div class="px-5 py-4 border-b border-bm-border">
                <h3 class="text-sm font-semibold">New ${esc(kindLabel)}</h3>
                <p class="text-xs text-bm-muted mt-1">Create in: ${esc(parentPath)}</p>
            </div>
            <div class="p-5">
                <input id="cfo-create-name" type="text" placeholder="${esc(kindLabel)} name..."
                       class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent"
                       autofocus>
                <p id="cfo-create-error" class="text-xs text-red-600 mt-2 hidden"></p>
            </div>
            <div class="px-5 py-4 border-t border-bm-border flex items-center justify-end gap-2">
                <button type="button" id="cfo-create-cancel"
                        class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">
                    Cancel
                </button>
                <button type="button" id="cfo-create-confirm"
                        class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">
                    Create
                </button>
            </div>`;

        const nameInput = modal.panel.querySelector('#cfo-create-name');
        const errorEl = modal.panel.querySelector('#cfo-create-error');
        const confirmBtn = modal.panel.querySelector('#cfo-create-confirm');

        modal.panel.querySelector('#cfo-create-cancel').addEventListener('click', modal.close);

        async function doCreate() {
            const name = nameInput.value.trim();
            if (!name) return;
            errorEl.classList.add('hidden');
            confirmBtn.textContent = 'Creating...';
            confirmBtn.disabled = true;
            try {
                await apiPost('/api/company/files/create', { path: parentPath, name, kind });
                modal.close();
                if (onComplete) onComplete();
            } catch (err) {
                errorEl.textContent = err.message || 'Failed to create';
                errorEl.classList.remove('hidden');
            } finally {
                confirmBtn.textContent = 'Create';
                confirmBtn.disabled = false;
            }
        }

        confirmBtn.addEventListener('click', doCreate);
        nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doCreate(); });
        setTimeout(() => nameInput.focus(), 50);
    }

    // ─── Rename ───

    function showRenameDialog(path, currentName, { onComplete } = {}) {
        const modal = BossModUtils.createModal({ maxWidth: 'max-w-md' });

        modal.panel.innerHTML = `
            <div class="px-5 py-4 border-b border-bm-border">
                <h3 class="text-sm font-semibold">Rename</h3>
                <p class="text-xs text-bm-muted mt-1">${esc(currentName)}</p>
            </div>
            <div class="p-5">
                <input id="cfo-rename-input" type="text" value="${esc(currentName)}"
                       class="w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent">
                <p id="cfo-rename-error" class="text-xs text-red-600 mt-2 hidden"></p>
            </div>
            <div class="px-5 py-4 border-t border-bm-border flex items-center justify-end gap-2">
                <button type="button" id="cfo-rename-cancel"
                        class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">
                    Cancel
                </button>
                <button type="button" id="cfo-rename-confirm"
                        class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">
                    Rename
                </button>
            </div>`;

        const input = modal.panel.querySelector('#cfo-rename-input');
        const errorEl = modal.panel.querySelector('#cfo-rename-error');
        const confirmBtn = modal.panel.querySelector('#cfo-rename-confirm');

        modal.panel.querySelector('#cfo-rename-cancel').addEventListener('click', modal.close);

        async function doRename() {
            const newName = input.value.trim();
            if (!newName || newName === currentName) return;
            errorEl.classList.add('hidden');
            confirmBtn.textContent = 'Renaming...';
            confirmBtn.disabled = true;
            try {
                await apiPatch('/api/company/files/rename', { path, new_name: newName });
                modal.close();
                if (onComplete) onComplete();
            } catch (err) {
                errorEl.textContent = err.message || 'Failed to rename';
                errorEl.classList.remove('hidden');
            } finally {
                confirmBtn.textContent = 'Rename';
                confirmBtn.disabled = false;
            }
        }

        confirmBtn.addEventListener('click', doRename);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doRename(); });
        setTimeout(() => { input.focus(); input.select(); }, 50);
    }

    // ─── Delete ───

    function showDeleteDialog(path, name, { onComplete } = {}) {
        const modal = BossModUtils.createModal({ maxWidth: 'max-w-sm' });

        modal.panel.innerHTML = `
            <div class="px-5 py-4 border-b border-bm-border">
                <h3 class="text-sm font-semibold">Delete</h3>
            </div>
            <div class="p-5">
                <p class="text-sm">Delete <strong>${esc(name)}</strong>?</p>
                <p class="text-xs text-bm-muted mt-1">This cannot be undone.</p>
                <p id="cfo-delete-error" class="text-xs text-red-600 mt-2 hidden"></p>
            </div>
            <div class="px-5 py-4 border-t border-bm-border flex items-center justify-end gap-2">
                <button type="button" id="cfo-delete-cancel"
                        class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">
                    Cancel
                </button>
                <button type="button" id="cfo-delete-confirm"
                        class="px-3 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 transition-colors">
                    Delete
                </button>
            </div>`;

        const errorEl = modal.panel.querySelector('#cfo-delete-error');
        const confirmBtn = modal.panel.querySelector('#cfo-delete-confirm');

        modal.panel.querySelector('#cfo-delete-cancel').addEventListener('click', modal.close);

        confirmBtn.addEventListener('click', async () => {
            errorEl.classList.add('hidden');
            confirmBtn.textContent = 'Deleting...';
            confirmBtn.disabled = true;
            try {
                await apiDelete('/api/company/files', { path });
                modal.close();
                if (onComplete) onComplete();
            } catch (err) {
                errorEl.textContent = err.message || 'Failed to delete';
                errorEl.classList.remove('hidden');
                confirmBtn.textContent = 'Delete';
                confirmBtn.disabled = false;
            }
        });
    }

    // ─── Move / Copy ───

    function showMoveOrCopyDialog(sourcePath, sourceName, action, { onComplete } = {}) {
        const label = action === 'move' ? 'Move' : 'Copy';
        const modal = BossModUtils.createModal({ maxWidth: 'max-w-lg' });
        let navPath = '/';

        modal.panel.innerHTML = `
            <div class="px-5 py-4 border-b border-bm-border">
                <h3 class="text-sm font-semibold">${label}: ${esc(sourceName)}</h3>
                <p class="text-xs text-bm-muted mt-1">Navigate to the destination folder</p>
            </div>
            <div class="flex-1 overflow-y-auto" style="max-height: 50vh;">
                <div id="cfo-nav-breadcrumbs" class="flex items-center gap-1 px-5 py-2 text-xs text-bm-muted border-b border-bm-border bg-slate-50/50 overflow-x-auto whitespace-nowrap"></div>
                <div id="cfo-nav-entries" class="px-5 py-3 space-y-1"></div>
            </div>
            <div class="px-5 py-4 border-t border-bm-border">
                <p id="cfo-nav-error" class="text-xs text-red-600 mb-2 hidden"></p>
                <div class="flex items-center justify-end gap-2">
                    <button type="button" id="cfo-nav-cancel"
                            class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">
                        Cancel
                    </button>
                    <button type="button" id="cfo-nav-confirm"
                            class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">
                        ${label} here
                    </button>
                </div>
            </div>`;

        modal.panel.querySelector('#cfo-nav-cancel').addEventListener('click', modal.close);

        const confirmBtn = modal.panel.querySelector('#cfo-nav-confirm');
        const errorEl = modal.panel.querySelector('#cfo-nav-error');

        async function loadFolder(path) {
            navPath = path;
            const breadEl = modal.panel.querySelector('#cfo-nav-breadcrumbs');
            const listEl = modal.panel.querySelector('#cfo-nav-entries');
            listEl.innerHTML = '<p class="text-xs text-bm-muted">Loading...</p>';
            try {
                const res = await fetch(`/api/company/files?path=${encodeURIComponent(path)}`, { cache: 'no-store' });
                if (!res.ok) throw new Error(await res.text());
                const payload = await res.json();
                const crumbs = payload.breadcrumbs || [];
                breadEl.innerHTML = crumbs.map((c, i) => {
                    const sep = i > 0 ? '<span class="text-bm-muted mx-0.5">/</span>' : '';
                    return `${sep}<button type="button" class="cfo-nav-crumb hover:underline hover:text-bm-accent" data-path="${esc(c.path)}">${esc(c.label)}</button>`;
                }).join('');

                const dirs = (payload.entries || []).filter(e => e.is_dir);
                if (dirs.length === 0) {
                    listEl.innerHTML = '<p class="text-xs text-bm-muted py-2">No subfolders</p>';
                } else {
                    listEl.innerHTML = dirs.map(d => `
                        <button type="button"
                                class="cfo-nav-entry w-full text-left rounded-lg border border-bm-border bg-white px-3 py-2 hover:bg-slate-50 transition-colors flex items-center gap-2"
                                data-path="${esc(d.path)}">
                            <i data-lucide="folder" class="w-3.5 h-3.5 text-amber-500 shrink-0"></i>
                            <span class="text-sm font-medium truncate">${esc(d.name)}/</span>
                            ${d.agent_name ? `<span class="text-[11px] text-bm-muted shrink-0">${esc(d.agent_name)}</span>` : ''}
                        </button>`).join('');
                }

                if (window.lucide) lucide.createIcons({ nodes: [listEl] });

                // Bind navigation
                modal.panel.querySelectorAll('.cfo-nav-crumb').forEach(btn => {
                    btn.addEventListener('click', () => loadFolder(btn.dataset.path));
                });
                modal.panel.querySelectorAll('.cfo-nav-entry').forEach(btn => {
                    btn.addEventListener('click', () => loadFolder(btn.dataset.path));
                });
            } catch (err) {
                listEl.innerHTML = `<p class="text-xs text-red-600">Failed to load: ${esc(err.message)}</p>`;
            }
        }

        confirmBtn.addEventListener('click', async () => {
            errorEl.classList.add('hidden');
            confirmBtn.textContent = `${label === 'Move' ? 'Moving' : 'Copying'}...`;
            confirmBtn.disabled = true;
            const endpoint = action === 'move' ? '/api/company/files/move' : '/api/company/files/copy';
            try {
                await apiPost(endpoint, { source: sourcePath, destination: navPath });
                modal.close();
                if (onComplete) onComplete();
            } catch (err) {
                errorEl.textContent = err.message || `Failed to ${action}`;
                errorEl.classList.remove('hidden');
            } finally {
                confirmBtn.textContent = `${label} here`;
                confirmBtn.disabled = false;
            }
        });

        loadFolder('/');
    }

    // ─── Copy Path to clipboard ───

    async function copyPath(path) {
        try {
            await navigator.clipboard.writeText(path);
        } catch (_) {
            // Fallback for non-secure contexts
            const ta = document.createElement('textarea');
            ta.value = path;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        }
    }

    // ─── Public API ───

    return {
        showCreateDialog,
        showRenameDialog,
        showDeleteDialog,
        showMoveOrCopyDialog,
        copyPath,
    };
})();
