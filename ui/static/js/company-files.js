/**
 * BossMod AI — Company Files tab.
 * Company-wide file browser rooted at the organization workspace level.
 * Supports navigation, breadcrumbs, search, file viewing/editing, and
 * opening folders in the host file manager.
 */
const CompanyFiles = (() => {
    let container = null;
    let currentPath = '/';
    let searchQuery = '';
    let entries = [];
    let breadcrumbs = [];
    let searchTimer = null;
    let folderOpenerModalEl = null;
    let fileViewerModalEl = null;

    // ─── Helpers ───

    function formatFileSize(bytes) {
        if (bytes == null || bytes < 0) return '';
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const value = bytes / Math.pow(1024, exponent);
        return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`;
    }

    const formatRelativeTime = BossModUtils.formatRelativeTime;

    function filteredEntries() {
        if (!searchQuery) return entries;
        const q = searchQuery.toLowerCase();
        return entries.filter(e => e.name.toLowerCase().includes(q));
    }

    function renderMarkdown(raw) {
        if (window.marked) {
            try { return marked.parse(raw, { breaks: true, gfm: true }); } catch (_) { /* fall through */ }
        }
        return `<pre class="text-xs whitespace-pre-wrap break-words">${BossModUtils.escapeHtml(raw)}</pre>`;
    }

    function renderBreadcrumbs(crumbList, { clickable = true } = {}) {
        if (!crumbList || !crumbList.length) return '';
        const esc = BossModUtils.escapeHtml;
        return crumbList.map((crumb, index) => {
            const isLast = index === crumbList.length - 1;
            const separator = index > 0 ? '<span class="text-bm-muted mx-0.5">/</span>' : '';
            const label = esc(crumb.label || crumb.name);
            const agentTag = crumb.agent_name
                ? ` <span class="text-[10px] text-bm-muted">(${esc(crumb.agent_name)})</span>`
                : '';
            if (!clickable || isLast) {
                return `${separator}<span class="${isLast ? 'font-medium text-bm-text' : ''}">${label}${agentTag}</span>`;
            }
            return `${separator}<button type="button" class="cf-crumb hover:underline hover:text-bm-accent" data-path="${esc(crumb.path)}">${label}${agentTag}</button>`;
        }).join('');
    }

    // ─── Rendering ───

    function render(el) {
        container = el;
        fetchAndRender();
    }

    async function fetchAndRender() {
        if (!container) return;
        container.innerHTML = `
            <div class="p-6 text-center text-bm-muted">
                <i data-lucide="loader" class="w-6 h-6 mx-auto mb-2 opacity-40 animate-spin"></i>
                <p class="text-sm">Loading files...</p>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });

        try {
            const res = await fetch(`/api/company/files?path=${encodeURIComponent(currentPath)}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(await res.text());
            const payload = await res.json();
            entries = Array.isArray(payload.entries) ? payload.entries : [];
            breadcrumbs = Array.isArray(payload.breadcrumbs) ? payload.breadcrumbs : [];
            renderDirectory(payload);
        } catch (err) {
            console.error('[CompanyFiles] Load failed:', err);
            renderError();
        }
    }

    function renderDirectory(payload) {
        if (!container) return;
        const esc = BossModUtils.escapeHtml;
        const filtered = filteredEntries();
        const dirs = filtered.filter(e => e.is_dir);
        const files = filtered.filter(e => !e.is_dir);
        const sorted = [...dirs, ...files];

        let html = `<div class="flex flex-col h-full">`;

        // Header bar
        html += `
            <div class="flex items-center justify-between gap-3 px-4 py-3 border-b border-bm-border">
                <div class="flex items-center gap-2 min-w-0">
                    <i data-lucide="folder-open" class="w-4 h-4 text-bm-accent shrink-0"></i>
                    <h3 class="text-sm font-semibold truncate">Company Files</h3>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <div class="relative">
                        <input type="text" id="cf-search-input" placeholder="Search files..."
                               value="${esc(searchQuery)}"
                               class="w-40 pl-7 pr-2 py-1 text-xs border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent">
                        <i data-lucide="search" class="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-bm-muted pointer-events-none"></i>
                    </div>
                    <button type="button" id="cf-open-explorer-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors flex items-center gap-1"
                            title="Open in file manager">
                        <i data-lucide="external-link" class="w-3 h-3"></i>
                        <span class="hidden sm:inline">Open Folder</span>
                    </button>
                    <button type="button" id="cf-refresh-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors"
                            title="Refresh">
                        <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                    </button>
                </div>
            </div>`;

        // Breadcrumb bar
        html += `
            <div class="flex items-center gap-1 px-4 py-2 text-xs text-bm-muted border-b border-bm-border bg-slate-50/50 overflow-x-auto whitespace-nowrap">
                ${renderBreadcrumbs(breadcrumbs)}
            </div>`;

        // Entry list
        html += `<div class="flex-1 overflow-y-auto px-4 py-3">`;
        if (sorted.length === 0) {
            const message = searchQuery ? 'No files match your search.' : 'No files in this directory.';
            html += `
                <div class="text-center py-8 text-bm-muted">
                    <i data-lucide="folder-x" class="w-8 h-8 mx-auto mb-2 opacity-30"></i>
                    <p class="text-sm">${esc(message)}</p>
                </div>`;
        } else {
            html += `<div class="space-y-1">`;
            for (const entry of sorted) {
                const icon = entry.is_dir ? 'folder' : 'file-text';
                const nameDisplay = entry.is_dir ? `${esc(entry.name)}/` : esc(entry.name);
                const nameWeight = entry.is_dir ? 'font-semibold' : 'font-medium';
                const sizeText = entry.is_dir ? '' : formatFileSize(entry.size_bytes);
                const timeText = formatRelativeTime(entry.updated_at);
                const agentText = entry.agent_name ? esc(entry.agent_name) : '';

                html += `
                    <button type="button"
                            class="cf-entry w-full text-left rounded-lg border border-bm-border bg-white px-3 py-2 hover:bg-slate-50 transition-colors"
                            data-path="${esc(entry.path)}" data-is-dir="${entry.is_dir ? '1' : '0'}">
                        <div class="flex items-center justify-between gap-3">
                            <div class="flex items-center gap-2 min-w-0">
                                <i data-lucide="${icon}" class="w-3.5 h-3.5 shrink-0 ${entry.is_dir ? 'text-amber-500' : 'text-slate-400'}"></i>
                                <span class="text-sm ${nameWeight} truncate">${nameDisplay}</span>
                                ${agentText ? `<span class="text-[11px] text-bm-muted shrink-0">${agentText}</span>` : ''}
                            </div>
                            <div class="flex items-center gap-3 shrink-0 text-[11px] text-bm-muted">
                                ${sizeText ? `<span>${esc(sizeText)}</span>` : ''}
                                ${timeText ? `<span>${esc(timeText)}</span>` : ''}
                            </div>
                        </div>
                    </button>`;
            }
            html += `</div>`;
        }
        html += `</div>`;

        // Footer summary
        const totalSize = entries.filter(e => !e.is_dir).reduce((sum, e) => sum + (e.size_bytes || 0), 0);
        html += `
            <div class="px-4 py-2 border-t border-bm-border text-[11px] text-bm-muted bg-slate-50/50">
                ${dirs.length} folder${dirs.length !== 1 ? 's' : ''}, ${files.length} file${files.length !== 1 ? 's' : ''}
                ${totalSize > 0 ? ` &middot; Total: ${formatFileSize(totalSize)}` : ''}
            </div>`;

        html += `</div>`;
        container.innerHTML = html;
        bindInteractions();
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    function renderError() {
        if (!container) return;
        const esc = BossModUtils.escapeHtml;
        container.innerHTML = `
            <div class="p-4">
                <div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    <div class="flex items-center gap-2 mb-2">
                        <i data-lucide="alert-circle" class="w-4 h-4 shrink-0"></i>
                        <span class="font-medium">Failed to load files</span>
                    </div>
                    <p class="text-xs text-red-600">Path: ${esc(currentPath)}</p>
                    <button type="button" id="cf-retry-btn"
                            class="mt-3 px-3 py-1.5 rounded border border-red-300 text-xs font-medium hover:bg-red-100 transition-colors">
                        Retry
                    </button>
                </div>
            </div>`;
        container.querySelector('#cf-retry-btn')?.addEventListener('click', () => fetchAndRender());
        if (window.lucide) lucide.createIcons({ nodes: [container] });
    }

    // ─── Interaction binding ───

    function bindInteractions() {
        if (!container) return;

        // Search input with debounce
        const searchInput = container.querySelector('#cf-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    searchQuery = e.target.value.trim();
                    renderDirectory({ entries, breadcrumbs });
                    const newInput = container?.querySelector('#cf-search-input');
                    if (newInput) {
                        newInput.focus();
                        newInput.setSelectionRange(newInput.value.length, newInput.value.length);
                    }
                }, 200);
            });
        }

        // Directory and file entry clicks
        container.querySelectorAll('.cf-entry').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.dataset.isDir === '1') {
                    navigateTo(btn.dataset.path);
                } else {
                    openFileViewer(btn.dataset.path);
                }
            });
        });

        // Breadcrumb clicks
        container.querySelectorAll('.cf-crumb').forEach(btn => {
            btn.addEventListener('click', () => {
                navigateTo(btn.dataset.path);
            });
        });

        // Open in Explorer
        container.querySelector('#cf-open-explorer-btn')?.addEventListener('click', () => {
            openFolder(currentPath);
        });

        // Refresh
        container.querySelector('#cf-refresh-btn')?.addEventListener('click', () => {
            fetchAndRender();
        });
    }

    // ─── Navigation ───

    function navigateTo(path) {
        currentPath = path || '/';
        searchQuery = '';
        fetchAndRender();
    }

    // ─── File viewer overlay ───

    async function openFileViewer(path) {
        closeFileViewer();
        try {
            const res = await fetch(`/api/company/files?path=${encodeURIComponent(path)}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(await res.text());
            const payload = await res.json();
            renderFileViewerModal(payload);
        } catch (err) {
            console.error('[CompanyFiles] Failed to load file:', err);
        }
    }

    function renderFileViewerModal(payload) {
        const esc = BossModUtils.escapeHtml;
        const isMarkdown = payload.name && payload.name.toLowerCase().endsWith('.md');
        const isBinary = !!payload.binary;
        const sizeText = formatFileSize(payload.size_bytes);
        const timeText = payload.updated_at ? new Date(payload.updated_at).toLocaleString() : '';
        const breadcrumbHtml = renderBreadcrumbs(payload.breadcrumbs || [], { clickable: false });

        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4';

        let contentHtml;
        if (isBinary) {
            contentHtml = `
                <div class="text-center py-12 text-bm-muted">
                    <i data-lucide="file-warning" class="w-10 h-10 mx-auto mb-3 opacity-30"></i>
                    <p class="text-sm font-medium">Binary file</p>
                    <p class="text-xs mt-1">Preview not available for this file type.</p>
                </div>`;
        } else if (isMarkdown) {
            contentHtml = `
                <div id="cf-viewer-rendered" class="prose prose-sm max-w-none">${renderMarkdown(payload.content || '')}</div>
                <textarea id="cf-viewer-editor" class="hidden w-full h-full min-h-[300px] text-xs font-mono whitespace-pre-wrap p-3 border border-bm-border rounded-lg bg-white resize-y focus:outline-none focus:border-bm-accent">${esc(payload.content || '')}</textarea>`;
        } else {
            contentHtml = `<pre class="text-xs whitespace-pre-wrap break-words text-bm-text">${esc(payload.content || '')}</pre>`;
        }

        overlay.innerHTML = `
            <div class="w-full max-w-4xl max-h-[85vh] flex flex-col rounded-xl border border-bm-border bg-white shadow-xl">
                <div class="flex items-center justify-between gap-3 px-5 py-4 border-b border-bm-border shrink-0">
                    <div class="min-w-0">
                        <h3 class="text-sm font-semibold truncate">${esc(payload.name)}</h3>
                        <div class="text-xs text-bm-muted mt-1">${breadcrumbHtml}</div>
                        <div class="text-[11px] text-bm-muted mt-1">
                            ${sizeText ? `<span>${esc(sizeText)}</span>` : ''}
                            ${sizeText && timeText ? ' &middot; ' : ''}
                            ${timeText ? `<span>${esc(timeText)}</span>` : ''}
                        </div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        ${isMarkdown ? `
                            <div class="flex rounded-lg border border-bm-border overflow-hidden text-xs" id="cf-viewer-toggle">
                                <button type="button" id="cf-viewer-mode-view"
                                        class="px-3 py-1.5 font-medium bg-bm-accent text-white transition-colors">View</button>
                                <button type="button" id="cf-viewer-mode-edit"
                                        class="px-3 py-1.5 font-medium hover:bg-slate-50 transition-colors">Edit</button>
                            </div>
                            <button type="button" id="cf-viewer-save"
                                    class="hidden px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-medium hover:bg-emerald-700 transition-colors">
                                Save
                            </button>` : ''}
                        <button type="button" id="cf-viewer-close"
                                class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors" title="Close">
                            <i data-lucide="x" class="w-4 h-4"></i>
                        </button>
                    </div>
                </div>
                <div class="flex-1 overflow-y-auto p-5" id="cf-viewer-content">
                    ${contentHtml}
                    ${payload.truncated ? '<p class="text-[11px] text-bm-muted mt-3">Preview truncated — file exceeds display limit.</p>' : ''}
                </div>
            </div>`;

        document.body.appendChild(overlay);
        fileViewerModalEl = overlay;
        if (window.lucide) lucide.createIcons({ nodes: [overlay] });

        // Close handlers
        overlay.querySelector('#cf-viewer-close')?.addEventListener('click', closeFileViewer);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) closeFileViewer(); });
        document.addEventListener('keydown', handleViewerEscape);

        // Markdown view/edit toggle + save
        if (isMarkdown) {
            bindMarkdownToggle(overlay, payload);
        }
    }

    function bindMarkdownToggle(overlay, payload) {
        const viewBtn = overlay.querySelector('#cf-viewer-mode-view');
        const editBtn = overlay.querySelector('#cf-viewer-mode-edit');
        const saveBtn = overlay.querySelector('#cf-viewer-save');
        const rendered = overlay.querySelector('#cf-viewer-rendered');
        const editor = overlay.querySelector('#cf-viewer-editor');
        if (!viewBtn || !editBtn || !saveBtn || !rendered || !editor) return;

        let currentContent = payload.content || '';

        const activeCls = 'px-3 py-1.5 font-medium bg-bm-accent text-white transition-colors';
        const inactiveCls = 'px-3 py-1.5 font-medium hover:bg-slate-50 transition-colors';

        viewBtn.addEventListener('click', () => {
            rendered.innerHTML = renderMarkdown(currentContent);
            rendered.classList.remove('hidden');
            editor.classList.add('hidden');
            saveBtn.classList.add('hidden');
            viewBtn.className = activeCls;
            editBtn.className = inactiveCls;
        });

        editBtn.addEventListener('click', () => {
            editor.value = currentContent;
            rendered.classList.add('hidden');
            editor.classList.remove('hidden');
            saveBtn.classList.remove('hidden');
            editBtn.className = activeCls;
            viewBtn.className = inactiveCls;
        });

        saveBtn.addEventListener('click', async () => {
            const newContent = editor.value;
            saveBtn.textContent = 'Saving...';
            saveBtn.disabled = true;
            try {
                const res = await fetch('/api/company/files', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: payload.path, content: newContent }),
                });
                if (!res.ok) throw new Error(await res.text());
                currentContent = newContent;
                rendered.innerHTML = renderMarkdown(currentContent);
                rendered.classList.remove('hidden');
                editor.classList.add('hidden');
                saveBtn.classList.add('hidden');
                viewBtn.className = activeCls;
                editBtn.className = inactiveCls;
                saveBtn.textContent = 'Save';
                saveBtn.disabled = false;
            } catch (err) {
                console.error('[CompanyFiles] Save failed:', err);
                saveBtn.textContent = 'Save';
                saveBtn.disabled = false;
            }
        });
    }

    function handleViewerEscape(e) {
        if (e.key === 'Escape' && fileViewerModalEl) {
            closeFileViewer();
        }
    }

    function closeFileViewer() {
        if (fileViewerModalEl) {
            fileViewerModalEl.remove();
            fileViewerModalEl = null;
            document.removeEventListener('keydown', handleViewerEscape);
        }
    }

    // ─── Open in file manager ───

    async function openFolder(path) {
        try {
            const res = await fetch('/api/company/files/open-folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path || '/' }),
            });
            if (!res.ok) {
                if (res.status === 409) {
                    const payload = await res.json();
                    const detail = payload?.detail;
                    if (detail?.code === 'desk_open_folder_handler_required' || detail?.code === 'desk_open_folder_handler_invalid') {
                        const chosen = await promptForFolderOpener(detail);
                        if (chosen) {
                            await fetch(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(chosen)}&category=advanced`, {
                                method: 'PUT',
                            });
                            await openFolder(path);
                        }
                        return;
                    }
                }
                throw new Error(await res.text());
            }
        } catch (err) {
            console.error('[CompanyFiles] Failed to open folder:', err);
        }
    }

    // ─── Folder opener modal (mirrors pattern from agent-context.js) ───

    function promptForFolderOpener(detail) {
        const options = Array.isArray(detail?.options) ? detail.options : [];
        return new Promise((resolve) => {
            closeFolderOpenerModal();

            const overlay = document.createElement('div');
            overlay.className = 'fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4';
            overlay.innerHTML = `
                <div class="w-full max-w-lg rounded-xl border border-bm-border bg-white shadow-xl">
                    <div class="px-5 py-4 border-b border-bm-border">
                        <h3 class="text-lg font-semibold">Choose Folder Opener</h3>
                        <p class="text-sm text-bm-muted mt-1">${BossModUtils.escapeHtml(detail?.message || 'Choose how BossMod should open folders on this machine.')}</p>
                    </div>
                    <div class="p-5 space-y-4">
                        <div class="space-y-2" id="cf-folder-opener-list">
                            ${options.map((option, index) => `
                                <label class="flex items-start gap-3 rounded-lg border border-bm-border p-3 hover:bg-slate-50 cursor-pointer">
                                    <input type="radio" name="cf-folder-opener-choice" value="${BossModUtils.escapeHtml(option.value)}" ${index === 0 ? 'checked' : ''} class="mt-0.5">
                                    <span>
                                        <span class="block text-sm font-medium">${BossModUtils.escapeHtml(option.label)}</span>
                                        <span class="block text-xs text-bm-muted mt-0.5">${BossModUtils.escapeHtml(option.description || '')}</span>
                                    </span>
                                </label>
                            `).join('')}
                            <label class="flex items-start gap-3 rounded-lg border border-bm-border p-3 hover:bg-slate-50 cursor-pointer">
                                <input type="radio" name="cf-folder-opener-choice" value="__custom__" ${options.length === 0 ? 'checked' : ''} class="mt-0.5">
                                <span class="flex-1">
                                    <span class="block text-sm font-medium">Custom executable</span>
                                    <span class="block text-xs text-bm-muted mt-0.5">Enter the file manager command available on PATH.</span>
                                    <input id="cf-folder-opener-custom" type="text" placeholder="e.g. thunar"
                                           class="mt-2 w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white">
                                </span>
                            </label>
                        </div>
                    </div>
                    <div class="px-5 py-4 border-t border-bm-border flex items-center justify-end gap-2">
                        <button type="button" id="cf-opener-cancel"
                                class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">
                            Cancel
                        </button>
                        <button type="button" id="cf-opener-save"
                                class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">
                            Save
                        </button>
                    </div>
                </div>`;

            document.body.appendChild(overlay);
            folderOpenerModalEl = overlay;

            const cancel = () => {
                closeFolderOpenerModal();
                resolve(null);
            };

            overlay.querySelector('#cf-opener-cancel')?.addEventListener('click', cancel);
            overlay.addEventListener('click', (event) => {
                if (event.target === overlay) cancel();
            });
            overlay.querySelector('#cf-folder-opener-custom')?.addEventListener('focus', () => {
                const customRadio = overlay.querySelector('input[name="cf-folder-opener-choice"][value="__custom__"]');
                if (customRadio) customRadio.checked = true;
            });
            overlay.querySelector('#cf-opener-save')?.addEventListener('click', () => {
                const selected = overlay.querySelector('input[name="cf-folder-opener-choice"]:checked');
                if (!selected) return;
                if (selected.value === '__custom__') {
                    const custom = String(overlay.querySelector('#cf-folder-opener-custom')?.value || '').trim();
                    if (!custom) return;
                    closeFolderOpenerModal();
                    resolve(custom);
                    return;
                }
                closeFolderOpenerModal();
                resolve(selected.value);
            });
        });
    }

    function closeFolderOpenerModal() {
        if (folderOpenerModalEl) {
            folderOpenerModalEl.remove();
            folderOpenerModalEl = null;
        }
    }

    // ─── Cleanup ───

    function destroy() {
        clearTimeout(searchTimer);
        closeFolderOpenerModal();
        closeFileViewer();
        container = null;
        entries = [];
        breadcrumbs = [];
        searchQuery = '';
        currentPath = '/';
    }

    return { render, destroy };
})();
