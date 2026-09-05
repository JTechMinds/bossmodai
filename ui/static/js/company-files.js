/**
 * BossMod AI — Company Files tab.
 * Company-wide file browser with navigation, search, context menu,
 * and integration with CompanyFileViewer and CompanyFileOps modules.
 */
const CompanyFiles = (() => {
    let container = null;
    let currentPath = '/';
    let searchQuery = '';
    let searchMode = 'local'; // 'local' | 'global'
    let entries = [];
    let breadcrumbs = [];
    let workspaceNote = '';
    let hostRoots = [];
    let actionError = '';
    let searchTimer = null;
    let folderOpenerModalEl = null;
    let hostRootsModalEl = null;
    let contextMenuEl = null;
    const filesLoad = BossModUtils.createLoadGeneration();

    function onDocumentClickCloseNewMenu(event) {
        if (!container) return;
        const wrap = container.querySelector('#cf-new-dropdown-wrap');
        const dropdown = container.querySelector('#cf-new-dropdown');
        if (!dropdown || dropdown.classList.contains('hidden')) return;
        if (wrap && wrap.contains(event.target)) return;
        dropdown.classList.add('hidden');
    }

    document.addEventListener('click', onDocumentClickCloseNewMenu);

    // ─── Helpers ───

    function formatFileSize(bytes) {
        if (bytes == null || bytes < 0) return '';
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const val = bytes / Math.pow(1024, exp);
        return `${exp === 0 ? val : val.toFixed(1)} ${units[exp]}`;
    }

    const formatRelativeTime = BossModUtils.formatRelativeTime;

    function parentVirtualPath(path) {
        const cleaned = String(path || '/').replace(/\\/g, '/');
        if (!cleaned || cleaned === '/') return '/';
        const trimmed = cleaned.endsWith('/') ? cleaned.slice(0, -1) : cleaned;
        const idx = trimmed.lastIndexOf('/');
        if (idx <= 0) return '/';
        return trimmed.slice(0, idx) || '/';
    }

    async function readApiError(res, fallback) {
        const fallbackText = fallback || `Request failed (${res.status})`;
        if (window.BossModApi && typeof window.BossModApi.formatError === 'function') {
            const payload = await res.json().catch(() => ({}));
            return window.BossModApi.formatError(payload, res.status) || fallbackText;
        }
        const text = await res.text().catch(() => '');
        return text || fallbackText;
    }

    function setActionError(message) {
        actionError = message || '';
        const banner = container?.querySelector('#cf-action-error');
        if (!banner) return;
        banner.textContent = actionError;
        banner.classList.toggle('hidden', !actionError);
    }

    function applyDirectoryPayload(payload, requestedPath) {
        entries = Array.isArray(payload.entries) ? payload.entries : [];
        breadcrumbs = Array.isArray(payload.breadcrumbs) ? payload.breadcrumbs : [];
        workspaceNote = typeof payload.workspace_note === 'string' ? payload.workspace_note : '';
        hostRoots = Array.isArray(payload.host_roots) ? payload.host_roots : [];
        currentPath = payload.path || requestedPath || '/';
        searchMode = 'local';
        searchQuery = '';
        renderDirectory();
    }

    function filteredEntries() {
        if (!searchQuery || searchMode === 'global') return entries;
        const q = searchQuery.toLowerCase();
        return entries.filter(e => e.name.toLowerCase().includes(q));
    }

    function renderBreadcrumbs(crumbList, { clickable = true } = {}) {
        if (!crumbList || !crumbList.length) return '';
        const esc = BossModUtils.escapeHtml;
        return crumbList.map((crumb, index) => {
            const isLast = index === crumbList.length - 1;
            const sep = index > 0 ? '<span class="text-bm-muted mx-0.5">/</span>' : '';
            const label = esc(crumb.label || crumb.name);
            const agentTag = crumb.agent_name
                ? ` <span class="text-[10px] text-bm-muted">(${esc(crumb.agent_name)})</span>`
                : '';
            if (!clickable || isLast) {
                return `${sep}<span class="${isLast ? 'font-medium text-bm-text' : ''}">${label}${agentTag}</span>`;
            }
            return `${sep}<button type="button" class="cf-crumb hover:underline hover:text-bm-accent" data-path="${esc(crumb.path)}">${label}${agentTag}</button>`;
        }).join('');
    }

    // ─── Rendering ───

    function render(el) {
        container = el;
        fetchAndRender();
    }

    async function fetchAndRender() {
        if (!container) return;
        const loadId = filesLoad.next();
        const requestedPath = currentPath;
        container.innerHTML = `
            <div class="p-6 text-center text-bm-muted">
                <i data-lucide="loader" class="w-6 h-6 mx-auto mb-2 opacity-40 animate-spin"></i>
                <p class="text-sm">Loading files...</p>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [container] });

        try {
            const res = await apiFetch(`/api/company/files?path=${encodeURIComponent(requestedPath)}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(await readApiError(res, 'Failed to load files'));
            const payload = await res.json();
            if (!filesLoad.isCurrent(loadId) || currentPath !== requestedPath) return;
            if (payload.kind === 'file') {
                CompanyFileViewer.open(payload.path || requestedPath);
                const parent = parentVirtualPath(payload.path || requestedPath);
                if (parent !== requestedPath) {
                    currentPath = parent;
                    return fetchAndRender();
                }
                return;
            }
            actionError = '';
            applyDirectoryPayload(payload, requestedPath);
        } catch (err) {
            if (!filesLoad.isCurrent(loadId) || currentPath !== requestedPath) return;
            console.error('[CompanyFiles] Load failed:', err);
            renderError(err.message || 'Failed to load files');
        }
    }

    function renderDirectory() {
        if (!container) return;
        const esc = BossModUtils.escapeHtml;
        const filtered = filteredEntries();
        const dirs = filtered.filter(e => e.is_dir);
        const files = filtered.filter(e => !e.is_dir);
        const sorted = [...dirs, ...files];
        const isGlobal = searchMode === 'global';

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
                        <input type="text" id="cf-path-input" placeholder="Open named path…"
                               class="w-56 pl-7 pr-2 py-1 text-xs border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent font-mono"
                               title="Paste an absolute path under a configured host root, or a company-relative path">
                        <i data-lucide="terminal" class="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-bm-muted pointer-events-none"></i>
                    </div>
                    <div class="relative">
                        <input type="text" id="cf-search-input" placeholder="Search files..."
                               value="${esc(searchQuery)}"
                               class="w-44 pl-7 pr-2 py-1 text-xs border border-bm-border rounded-lg bg-white focus:outline-none focus:border-bm-accent">
                        <i data-lucide="search" class="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-bm-muted pointer-events-none"></i>
                    </div>
                    <div class="relative" id="cf-new-dropdown-wrap">
                        <button type="button" id="cf-new-btn"
                                class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors flex items-center gap-1"
                                title="Create new">
                            <i data-lucide="plus" class="w-3 h-3"></i>
                            <span class="hidden sm:inline">New</span>
                        </button>
                        <div id="cf-new-dropdown" class="hidden absolute right-0 top-full mt-1 w-36 bg-white border border-bm-border rounded-lg shadow-lg z-20 py-1">
                            <button type="button" id="cf-new-file" class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                                <i data-lucide="file-plus" class="w-3 h-3"></i> New File
                            </button>
                            <button type="button" id="cf-new-folder" class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                                <i data-lucide="folder-plus" class="w-3 h-3"></i> New Folder
                            </button>
                        </div>
                    </div>
                    <button type="button" id="cf-host-roots-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors flex items-center gap-1"
                            title="Add or edit allowlisted host folders">
                        <i data-lucide="hard-drive" class="w-3 h-3"></i>
                        <span class="hidden sm:inline">Host folders</span>
                    </button>
                    <button type="button" id="cf-open-explorer-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors flex items-center gap-1"
                            title="Open in file manager">
                        <i data-lucide="external-link" class="w-3 h-3"></i>
                        <span class="hidden sm:inline">Open</span>
                    </button>
                    <button type="button" id="cf-refresh-btn"
                            class="px-2 py-1 rounded border border-bm-border text-xs font-medium hover:bg-slate-50 transition-colors"
                            title="Refresh">
                        <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                    </button>
                </div>
            </div>`;

        // Breadcrumb / search indicator bar
        if (isGlobal) {
            html += `
                <div class="flex items-center justify-between gap-2 px-4 py-2 text-xs text-bm-muted border-b border-bm-border bg-blue-50/50">
                    <span><i data-lucide="search" class="w-3 h-3 inline -mt-0.5"></i> Searching all files for "${esc(searchQuery)}"</span>
                    <button type="button" id="cf-clear-search" class="text-bm-accent hover:underline">Clear</button>
                </div>`;
        } else {
            html += `
                <div class="flex items-center gap-1 px-4 py-2 text-xs text-bm-muted border-b border-bm-border bg-slate-50/50 overflow-x-auto whitespace-nowrap">
                    ${renderBreadcrumbs(breadcrumbs)}
                </div>`;
            if (workspaceNote || hostRoots.length) {
                const rootsLabel = hostRoots.length
                    ? hostRoots.map((root) => esc(root)).join(', ')
                    : '';
                html += `
                    <div class="px-4 py-1.5 text-[11px] text-bm-muted border-b border-bm-border bg-amber-50/60 flex items-start justify-between gap-3">
                        <div class="min-w-0">
                            <p>${esc(workspaceNote)}</p>
                            ${rootsLabel ? `<p class="mt-1 font-mono text-[10px] truncate" title="${rootsLabel}">Host folders: ${rootsLabel}</p>` : ''}
                        </div>
                        <button type="button" id="cf-add-host-folder"
                                class="shrink-0 px-2 py-1 rounded border border-amber-300 bg-white text-[11px] font-medium text-bm-text hover:bg-amber-50">
                            ${hostRoots.length ? 'Manage host folders' : 'Add host folder'}
                        </button>
                    </div>`;
            }
        }
        html += `
            <div id="cf-action-error"
                 class="${actionError ? '' : 'hidden'} px-4 py-2 text-xs text-red-700 bg-red-50 border-b border-red-200">
                ${esc(actionError)}
            </div>`;

        // Entry list
        html += `<div class="flex-1 overflow-y-auto px-4 py-3">`;
        if (sorted.length === 0) {
            const message = searchQuery
                ? (isGlobal ? 'No files found.' : 'No files match your search.')
                : 'No files in this directory.';
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
                const pathHint = isGlobal ? `<div class="text-[10px] text-bm-muted truncate mt-0.5">${esc(entry.path)}</div>` : '';

                html += `
                    <button type="button"
                            class="cf-entry w-full text-left rounded-lg border border-bm-border bg-white px-3 py-2 hover:bg-slate-50 transition-colors"
                            data-path="${esc(entry.path)}" data-name="${esc(entry.name)}" data-is-dir="${entry.is_dir ? '1' : '0'}">
                        <div class="flex items-center justify-between gap-3">
                            <div class="flex items-center gap-2 min-w-0">
                                <i data-lucide="${icon}" class="w-3.5 h-3.5 shrink-0 ${entry.is_dir ? 'text-amber-500' : 'text-slate-400'}"></i>
                                <div class="min-w-0">
                                    <span class="text-sm ${nameWeight} truncate block">${nameDisplay}</span>
                                    ${pathHint}
                                </div>
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

        // Footer
        const totalSize = (searchMode === 'local' ? entries : sorted).filter(e => !e.is_dir).reduce((sum, e) => sum + (e.size_bytes || 0), 0);
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

    function renderError(message) {
        if (!container) return;
        const esc = BossModUtils.escapeHtml;
        const detail = message || 'Failed to load files';
        container.innerHTML = `
            <div class="p-4">
                <div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    <div class="flex items-center gap-2 mb-2">
                        <i data-lucide="alert-circle" class="w-4 h-4 shrink-0"></i>
                        <span class="font-medium">Failed to load files</span>
                    </div>
                    <p class="text-xs text-red-600">${esc(detail)}</p>
                    <p class="text-xs text-red-600 mt-1">Path: ${esc(currentPath)}</p>
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

        const pathInput = container.querySelector('#cf-path-input');
        if (pathInput) {
            pathInput.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter') return;
                const named = e.target.value.trim();
                if (!named) return;
                openNamedPath(named);
            });
        }

        // Search
        const searchInput = container.querySelector('#cf-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    const q = e.target.value.trim();
                    searchQuery = q;
                    if (q.length >= 3) {
                        performGlobalSearch(q);
                    } else {
                        filesLoad.next();
                        searchMode = 'local';
                        renderDirectory();
                        restoreSearchFocus();
                    }
                }, 300);
            });
        }

        // Clear search
        container.querySelector('#cf-clear-search')?.addEventListener('click', () => {
            searchQuery = '';
            searchMode = 'local';
            fetchAndRender();
        });

        // Entry clicks (left click)
        container.querySelectorAll('.cf-entry').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.dataset.isDir === '1') {
                    navigateTo(btn.dataset.path);
                } else {
                    CompanyFileViewer.open(btn.dataset.path);
                }
            });
            // Context menu (right click)
            btn.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                showContextMenu(e.clientX, e.clientY, {
                    path: btn.dataset.path,
                    name: btn.dataset.name,
                    isDir: btn.dataset.isDir === '1',
                });
            });
        });

        // Breadcrumb clicks
        container.querySelectorAll('.cf-crumb').forEach(btn => {
            btn.addEventListener('click', () => navigateTo(btn.dataset.path));
        });

        // New dropdown
        const newBtn = container.querySelector('#cf-new-btn');
        const newDropdown = container.querySelector('#cf-new-dropdown');
        if (newBtn && newDropdown) {
            newBtn.addEventListener('click', () => newDropdown.classList.toggle('hidden'));
            container.querySelector('#cf-new-file')?.addEventListener('click', () => {
                newDropdown.classList.add('hidden');
                CompanyFileOps.showCreateDialog(currentPath, 'file', { onComplete: fetchAndRender });
            });
            container.querySelector('#cf-new-folder')?.addEventListener('click', () => {
                newDropdown.classList.add('hidden');
                CompanyFileOps.showCreateDialog(currentPath, 'folder', { onComplete: fetchAndRender });
            });
        }

        // Open in Explorer
        container.querySelector('#cf-open-explorer-btn')?.addEventListener('click', () => openFolder(currentPath));

        container.querySelector('#cf-host-roots-btn')?.addEventListener('click', () => openHostRootsModal());
        container.querySelector('#cf-add-host-folder')?.addEventListener('click', () => openHostRootsModal());

        // Refresh
        container.querySelector('#cf-refresh-btn')?.addEventListener('click', () => fetchAndRender());
    }

    // ─── Search ───

    async function performGlobalSearch(query) {
        const loadId = filesLoad.next();
        const requestedQuery = query;
        try {
            const res = await apiFetch(`/api/company/files/search?q=${encodeURIComponent(requestedQuery)}`, { cache: 'no-store' });
            if (!res.ok) throw new Error(await res.text());
            const results = await res.json();
            if (!filesLoad.isCurrent(loadId)) return;
            entries = Array.isArray(results) ? results : [];
            breadcrumbs = [];
            searchMode = 'global';
            actionError = '';
            renderDirectory();
            restoreSearchFocus();
        } catch (err) {
            if (!filesLoad.isCurrent(loadId)) return;
            console.error('[CompanyFiles] Search failed:', err);
            setActionError(err.message || 'Search failed');
        }
    }

    function restoreSearchFocus() {
        const input = container?.querySelector('#cf-search-input');
        if (input) {
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        }
    }

    // ─── Context Menu ───

    function showContextMenu(x, y, entry) {
        dismissContextMenu();
        const esc = BossModUtils.escapeHtml;
        const menu = document.createElement('div');
        menu.className = 'cf-context-menu fixed z-[60] bg-white border border-bm-border rounded-lg shadow-lg py-1 min-w-[180px]';
        menu.style.left = `${Math.min(x, window.innerWidth - 200)}px`;
        menu.style.top = `${Math.min(y, window.innerHeight - 280)}px`;

        menu.innerHTML = `
            <button type="button" class="cf-ctx-item w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2" data-action="rename">
                <i data-lucide="pencil" class="w-3 h-3"></i> Rename
            </button>
            <button type="button" class="cf-ctx-item w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2 text-red-600" data-action="delete">
                <i data-lucide="trash-2" class="w-3 h-3"></i> Delete
            </button>
            <div class="border-t border-bm-border my-1"></div>
            <button type="button" class="cf-ctx-item w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2" data-action="copy-path">
                <i data-lucide="clipboard" class="w-3 h-3"></i> Copy Path
            </button>
            <button type="button" class="cf-ctx-item w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2" data-action="open-explorer">
                <i data-lucide="folder-open" class="w-3 h-3"></i> Open in Explorer
            </button>
            <div class="border-t border-bm-border my-1"></div>
            <button type="button" class="cf-ctx-item w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2" data-action="move">
                <i data-lucide="arrow-right" class="w-3 h-3"></i> Move to...
            </button>
            <button type="button" class="cf-ctx-item w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2" data-action="copy">
                <i data-lucide="copy" class="w-3 h-3"></i> Copy to...
            </button>`;

        document.body.appendChild(menu);
        contextMenuEl = menu;
        if (window.lucide) lucide.createIcons({ nodes: [menu] });

        menu.querySelectorAll('.cf-ctx-item').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                dismissContextMenu();
                handleContextAction(action, entry);
            });
        });

        // Dismiss on outside click or Escape
        setTimeout(() => {
            document.addEventListener('click', dismissContextMenu, { once: true });
            document.addEventListener('keydown', handleCtxEscape);
        }, 0);
    }

    function handleCtxEscape(e) {
        if (e.key === 'Escape') dismissContextMenu();
    }

    function dismissContextMenu() {
        if (contextMenuEl) {
            contextMenuEl.remove();
            contextMenuEl = null;
            document.removeEventListener('keydown', handleCtxEscape);
        }
    }

    function handleContextAction(action, entry) {
        switch (action) {
            case 'rename':
                CompanyFileOps.showRenameDialog(entry.path, entry.name, { onComplete: fetchAndRender });
                break;
            case 'delete':
                CompanyFileOps.showDeleteDialog(entry.path, entry.name, { onComplete: fetchAndRender });
                break;
            case 'copy-path':
                CompanyFileOps.copyPath(entry.path);
                break;
            case 'open-explorer':
                openFolder(entry.isDir ? entry.path : entry.path.replace(/\/[^/]+$/, '') || '/');
                break;
            case 'move':
                CompanyFileOps.showMoveOrCopyDialog(entry.path, entry.name, 'move', { onComplete: fetchAndRender });
                break;
            case 'copy':
                CompanyFileOps.showMoveOrCopyDialog(entry.path, entry.name, 'copy', { onComplete: fetchAndRender });
                break;
        }
    }

    // ─── Navigation ───

    function navigateTo(path) {
        currentPath = path || '/';
        searchQuery = '';
        searchMode = 'local';
        actionError = '';
        fetchAndRender();
    }

    async function openNamedPath(named) {
        setActionError('');
        try {
            const res = await apiFetch(`/api/company/files?path=${encodeURIComponent(named)}`, { cache: 'no-store' });
            if (!res.ok) {
                setActionError(await readApiError(res, 'Could not open that path'));
                return;
            }
            const payload = await res.json();
            if (payload.kind === 'file') {
                CompanyFileViewer.open(payload.path || named);
                return;
            }
            applyDirectoryPayload(payload, named);
        } catch (err) {
            console.error('[CompanyFiles] Named path open failed:', err);
            setActionError(err.message || 'Could not open that path');
        }
    }

    // ─── Open in file manager ───

    async function openFolder(path) {
        setActionError('');
        try {
            const res = await apiFetch('/api/company/files/open-folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path || '/' }),
            });
            if (!res.ok) {
                const payload = await res.json().catch(() => ({}));
                const detail = payload?.detail;
                if (res.status === 409 && (detail?.code === 'desk_open_folder_handler_required' || detail?.code === 'desk_open_folder_handler_invalid')) {
                    const chosen = await promptForFolderOpener(detail);
                    if (chosen) {
                        await apiFetchOk(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(chosen)}&category=advanced`, {
                            method: 'PUT',
                        });
                        await openFolder(path);
                    }
                    return;
                }
                const formatted = window.BossModApi && typeof window.BossModApi.formatError === 'function'
                    ? window.BossModApi.formatError(payload, res.status)
                    : (typeof detail === 'string' ? detail : `Request failed (${res.status})`);
                throw new Error(formatted);
            }
        } catch (err) {
            console.error('[CompanyFiles] Failed to open folder:', err);
            setActionError(err.message || 'Failed to open folder');
        }
    }

    // ─── Host workspace roots (same workspace_host_roots setting) ───

    async function openHostRootsModal() {
        closeHostRootsModal();
        const modal = BossModUtils.createModal({
            maxWidth: 'max-w-lg',
            onClose: () => { hostRootsModalEl = null; },
        });
        hostRootsModalEl = modal;

        let currentValue = hostRoots.join('\n');
        try {
            const res = await apiFetch('/api/settings?category=cli_policy');
            if (res.ok) {
                const settings = await res.json();
                const row = Array.isArray(settings)
                    ? settings.find((item) => item.key === 'workspace_host_roots')
                    : null;
                if (row && typeof row.value === 'string') currentValue = row.value;
            }
        } catch {
            /* keep the list already shown in Company Files */
        }

        modal.panel.innerHTML = `
            <div class="px-5 py-4 border-b border-bm-border">
                <h3 class="text-lg font-semibold">Host folders</h3>
                <p class="text-sm text-bm-muted mt-1">Optional extra directories a named absolute path may open, read, or edit. Writes the same allowlist as Settings → CLI Policy → Host workspace roots. This is not a full host mount. /, /etc, /proc, /sys, /dev, and /root are rejected.</p>
            </div>
            <div class="p-5 space-y-3">
                <label class="block text-sm font-semibold" for="cf-host-roots-input">Allowlisted host folders</label>
                <textarea id="cf-host-roots-input" rows="5"
                          class="w-full px-3 py-2 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-mono"
                          placeholder="/home/you/src">${BossModUtils.escapeHtml(currentValue)}</textarea>
                <p id="cf-host-roots-status" class="hidden text-xs text-red-500"></p>
            </div>
            <div class="px-5 py-4 border-t border-bm-border flex items-center justify-between gap-2">
                <button type="button" id="cf-host-roots-settings"
                        class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">Open in Settings</button>
                <div class="flex items-center gap-2">
                    <button type="button" id="cf-host-roots-cancel"
                            class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">Cancel</button>
                    <button type="button" id="cf-host-roots-save"
                            class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">Save</button>
                </div>
            </div>`;

        const status = modal.panel.querySelector('#cf-host-roots-status');
        modal.panel.querySelector('#cf-host-roots-cancel')?.addEventListener('click', () => modal.close());
        modal.panel.querySelector('#cf-host-roots-settings')?.addEventListener('click', () => {
            modal.close();
            if (typeof SettingsView !== 'undefined' && typeof SettingsView.open === 'function') {
                SettingsView.open('cli-policy', { tab: 'settings', focusKey: 'workspace_host_roots' });
            }
        });
        modal.panel.querySelector('#cf-host-roots-save')?.addEventListener('click', async () => {
            const value = String(modal.panel.querySelector('#cf-host-roots-input')?.value || '');
            if (status) {
                status.textContent = '';
                status.classList.add('hidden');
            }
            try {
                await apiFetchOk(`/api/settings/workspace_host_roots?value=${encodeURIComponent(value)}&category=cli_policy`, {
                    method: 'PUT',
                });
                modal.close();
                fetchAndRender();
            } catch (err) {
                if (status) {
                    status.textContent = err.message || 'Save failed';
                    status.classList.remove('hidden');
                } else {
                    setActionError(err.message || 'Save failed');
                }
            }
        });
    }

    function closeHostRootsModal() {
        if (hostRootsModalEl) {
            hostRootsModalEl.close();
            hostRootsModalEl = null;
        }
    }

    // ─── Folder opener modal ───

    function promptForFolderOpener(detail) {
        const options = Array.isArray(detail?.options) ? detail.options : [];
        return new Promise((resolve) => {
            closeFolderOpenerModal();
            const modal = BossModUtils.createModal({ maxWidth: 'max-w-lg', onClose: () => resolve(null) });
            folderOpenerModalEl = modal;

            modal.panel.innerHTML = `
                <div class="px-5 py-4 border-b border-bm-border">
                    <h3 class="text-lg font-semibold">Choose Folder Opener</h3>
                    <p class="text-sm text-bm-muted mt-1">${BossModUtils.escapeHtml(detail?.message || 'Choose how BossMod should open folders on this machine.')}</p>
                </div>
                <div class="p-5 space-y-4">
                    <div class="space-y-2">
                        ${options.map((opt, i) => `
                            <label class="flex items-start gap-3 rounded-lg border border-bm-border p-3 hover:bg-slate-50 cursor-pointer">
                                <input type="radio" name="cf-folder-opener-choice" value="${BossModUtils.escapeHtml(opt.value)}" ${i === 0 ? 'checked' : ''} class="mt-0.5">
                                <span>
                                    <span class="block text-sm font-medium">${BossModUtils.escapeHtml(opt.label)}</span>
                                    <span class="block text-xs text-bm-muted mt-0.5">${BossModUtils.escapeHtml(opt.description || '')}</span>
                                </span>
                            </label>`).join('')}
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
                            class="px-3 py-2 rounded-lg border border-bm-border text-sm font-medium hover:bg-slate-50 transition-colors">Cancel</button>
                    <button type="button" id="cf-opener-save"
                            class="px-3 py-2 rounded-lg bg-bm-accent text-white text-sm font-medium hover:bg-bm-accent-hover transition-colors">Save</button>
                </div>`;

            modal.panel.querySelector('#cf-opener-cancel')?.addEventListener('click', () => { resolve(null); modal.close(); });
            modal.panel.querySelector('#cf-folder-opener-custom')?.addEventListener('focus', () => {
                const r = modal.panel.querySelector('input[name="cf-folder-opener-choice"][value="__custom__"]');
                if (r) r.checked = true;
            });
            modal.panel.querySelector('#cf-opener-save')?.addEventListener('click', () => {
                const sel = modal.panel.querySelector('input[name="cf-folder-opener-choice"]:checked');
                if (!sel) return;
                if (sel.value === '__custom__') {
                    const c = String(modal.panel.querySelector('#cf-folder-opener-custom')?.value || '').trim();
                    if (!c) return;
                    resolve(c);
                    modal.close();
                    return;
                }
                resolve(sel.value);
                modal.close();
            });
        });
    }

    function closeFolderOpenerModal() {
        if (folderOpenerModalEl) {
            folderOpenerModalEl.close();
            folderOpenerModalEl = null;
        }
    }

    // ─── Cleanup ───

    function destroy() {
        filesLoad.next();
        clearTimeout(searchTimer);
        closeFolderOpenerModal();
        closeHostRootsModal();
        dismissContextMenu();
        CompanyFileViewer.close();
        container = null;
        entries = [];
        breadcrumbs = [];
        hostRoots = [];
        actionError = '';
        searchQuery = '';
        searchMode = 'local';
        currentPath = '/';
    }

    return { render, destroy, openNamedPath };
})();
