/**
 * BossMod AI — Company File Viewer overlay.
 * Handles viewing, editing, syntax highlighting, image preview, and printing
 * for files opened from the Company Files browser.
 */
const CompanyFileViewer = (() => {
    let modalRef = null;
    let currentPayload = null;
    let currentContent = '';

    const _EXT_LANG_MAP = {
        '.py': 'python', '.js': 'javascript', '.jsx': 'javascript',
        '.ts': 'typescript', '.tsx': 'typescript', '.json': 'json',
        '.yaml': 'yaml', '.yml': 'yaml', '.sql': 'sql',
        '.html': 'html', '.css': 'css', '.sh': 'bash', '.bash': 'bash',
        '.xml': 'xml', '.svg': 'xml', '.toml': 'ini', '.md': 'markdown',
        '.ini': 'ini', '.cfg': 'ini', '.log': 'plaintext',
        '.txt': 'plaintext', '.csv': 'plaintext', '.env': 'bash',
        '.graphql': 'graphql', '.rst': 'plaintext', '.tex': 'latex',
    };

    const _IMAGE_EXTENSIONS = new Set([
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
    ]);

    // ─── Helpers ───

    function getLanguage(filename) {
        if (!filename) return null;
        const dot = filename.lastIndexOf('.');
        if (dot === -1) return null;
        return _EXT_LANG_MAP[filename.slice(dot).toLowerCase()] || null;
    }

    function isImage(filename) {
        if (!filename) return false;
        const dot = filename.lastIndexOf('.');
        if (dot === -1) return false;
        return _IMAGE_EXTENSIONS.has(filename.slice(dot).toLowerCase());
    }

    function isMarkdown(filename) {
        return filename && filename.toLowerCase().endsWith('.md');
    }

    function isJson(filename) {
        return filename && filename.toLowerCase().endsWith('.json');
    }

    function highlightCode(code, lang) {
        if (!window.hljs) return BossModUtils.escapeHtml(code);
        try {
            if (lang && hljs.getLanguage(lang)) {
                return hljs.highlight(code, { language: lang }).value;
            }
            return hljs.highlightAuto(code).value;
        } catch (_) {
            return BossModUtils.escapeHtml(code);
        }
    }

    function renderMarkdown(raw) {
        if (!window.marked) return `<pre class="text-xs whitespace-pre-wrap break-words">${BossModUtils.escapeHtml(raw)}</pre>`;
        try {
            return marked.parse(raw, { breaks: true, gfm: true });
        } catch (_) {
            return `<pre class="text-xs whitespace-pre-wrap break-words">${BossModUtils.escapeHtml(raw)}</pre>`;
        }
    }

    function formatFileSize(bytes) {
        if (bytes == null || bytes < 0) return '';
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const val = bytes / Math.pow(1024, exp);
        return `${exp === 0 ? val : val.toFixed(1)} ${units[exp]}`;
    }

    function renderBreadcrumbsStatic(crumbs) {
        if (!crumbs || !crumbs.length) return '';
        const esc = BossModUtils.escapeHtml;
        return crumbs.map((c, i) => {
            const sep = i > 0 ? '<span class="text-bm-muted mx-0.5">/</span>' : '';
            const agent = c.agent_name ? ` <span class="text-[10px] text-bm-muted">(${esc(c.agent_name)})</span>` : '';
            return `${sep}<span>${esc(c.label || c.name)}${agent}</span>`;
        }).join('');
    }

    // ─── Configure marked + highlight.js integration ───

    function configureMarked() {
        if (!window.marked || !window.hljs) return;
        marked.setOptions({
            breaks: true,
            gfm: true,
            highlight: (code, lang) => highlightCode(code, lang),
        });
    }

    // ─── Open / Close ───

    async function open(path, { apiUrl } = {}) {
        close();
        const url = apiUrl || `/api/company/files?path=${encodeURIComponent(path)}`;
        try {
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(await res.text());
            const payload = await res.json();
            currentPayload = payload;
            currentContent = payload.content || '';
            configureMarked();
            renderModal(payload);
        } catch (err) {
            console.error('[CompanyFileViewer] Failed to load file:', err);
        }
    }

    function close() {
        if (modalRef) {
            modalRef.close();
            modalRef = null;
        }
        currentPayload = null;
        currentContent = '';
    }

    // ─── Modal Rendering ───

    function renderModal(payload) {
        const esc = BossModUtils.escapeHtml;
        const md = isMarkdown(payload.name);
        const img = isImage(payload.name);
        const binary = !!payload.binary && !img;
        const textFile = !payload.binary && !md;
        const lang = getLanguage(payload.name);
        const sizeText = formatFileSize(payload.size_bytes);
        const timeText = payload.updated_at ? new Date(payload.updated_at).toLocaleString() : '';

        const modal = BossModUtils.createModal({ maxWidth: 'max-w-4xl', onClose: () => { modalRef = null; } });
        modalRef = modal;

        // Build header
        const headerHtml = `
            <div class="flex items-center justify-between gap-3 px-5 py-4 border-b border-bm-border shrink-0">
                <div class="min-w-0">
                    <h3 class="text-sm font-semibold truncate">${esc(payload.name)}</h3>
                    <div class="text-xs text-bm-muted mt-1">${renderBreadcrumbsStatic(payload.breadcrumbs || [])}</div>
                    <div class="text-[11px] text-bm-muted mt-1">
                        ${sizeText ? `<span>${esc(sizeText)}</span>` : ''}
                        ${sizeText && timeText ? ' &middot; ' : ''}
                        ${timeText ? `<span>${esc(timeText)}</span>` : ''}
                    </div>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    ${(!binary && !img) ? `
                        <div class="flex rounded-lg border border-bm-border overflow-hidden text-xs" id="cfv-toggle">
                            <button type="button" id="cfv-mode-view"
                                    class="cfv-toggle-btn px-3 py-1.5 font-medium bg-bm-accent text-white transition-colors">View</button>
                            <button type="button" id="cfv-mode-edit"
                                    class="cfv-toggle-btn px-3 py-1.5 font-medium hover:bg-slate-50 transition-colors">Edit</button>
                        </div>
                        <button type="button" id="cfv-save"
                                class="hidden px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-medium hover:bg-emerald-700 transition-colors">
                            Save
                        </button>` : ''}
                    <button type="button" id="cfv-print"
                            class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors" title="Print">
                        <i data-lucide="printer" class="w-4 h-4"></i>
                    </button>
                    <button type="button" id="cfv-close"
                            class="p-1.5 rounded-lg hover:bg-slate-100 transition-colors" title="Close">
                        <i data-lucide="x" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>`;

        // Build content
        let contentHtml;
        if (img) {
            contentHtml = `
                <div class="flex flex-col items-center gap-3 py-6">
                    <img src="/api/company/files/raw?path=${encodeURIComponent(payload.path)}"
                         alt="${esc(payload.name)}"
                         class="max-w-full max-h-[60vh] rounded-lg border border-bm-border"
                         id="cfv-image" />
                    <p id="cfv-image-dims" class="text-[11px] text-bm-muted"></p>
                </div>`;
        } else if (binary) {
            contentHtml = `
                <div class="text-center py-12 text-bm-muted">
                    <i data-lucide="file-warning" class="w-10 h-10 mx-auto mb-3 opacity-30"></i>
                    <p class="text-sm font-medium">Binary file</p>
                    <p class="text-xs mt-1">Preview not available for this file type.</p>
                </div>`;
        } else if (md) {
            contentHtml = `
                <div id="cfv-rendered" class="prose prose-sm max-w-none">${renderMarkdown(currentContent)}</div>
                <textarea id="cfv-editor" class="hidden w-full flex-1 min-h-0 text-xs font-mono whitespace-pre p-3 border border-bm-border rounded-lg bg-white resize-none focus:outline-none focus:border-bm-accent">${esc(currentContent)}</textarea>`;
        } else {
            // Text file with syntax highlighting
            const highlighted = highlightCode(isJson(payload.name) ? prettyJson(currentContent) : currentContent, lang);
            contentHtml = `
                <div id="cfv-rendered"><pre class="text-xs leading-relaxed"><code class="hljs">${highlighted}</code></pre></div>
                <textarea id="cfv-editor" class="hidden w-full flex-1 min-h-0 text-xs font-mono whitespace-pre p-3 border border-bm-border rounded-lg bg-white resize-none focus:outline-none focus:border-bm-accent">${esc(currentContent)}</textarea>`;
        }

        modal.panel.innerHTML = `
            ${headerHtml}
            <div class="flex-1 flex flex-col overflow-y-auto p-5" id="cfv-content">
                ${contentHtml}
                ${payload.truncated ? '<p class="text-[11px] text-bm-muted mt-3">Preview truncated — file exceeds display limit.</p>' : ''}
            </div>`;

        if (window.lucide) lucide.createIcons({ nodes: [modal.panel] });

        // Close button
        modal.panel.querySelector('#cfv-close')?.addEventListener('click', close);

        // Print button
        modal.panel.querySelector('#cfv-print')?.addEventListener('click', handlePrint);

        // Image dimensions
        const imgEl = modal.panel.querySelector('#cfv-image');
        if (imgEl) {
            imgEl.addEventListener('load', () => {
                const dimsEl = modal.panel.querySelector('#cfv-image-dims');
                if (dimsEl) dimsEl.textContent = `${imgEl.naturalWidth} × ${imgEl.naturalHeight} px`;
            });
        }

        // View/Edit toggle + Save
        if (!binary && !img) {
            bindToggle(modal.panel, payload);
        }
    }

    // ─── View/Edit Toggle ───

    function bindToggle(panel, payload) {
        const viewBtn = panel.querySelector('#cfv-mode-view');
        const editBtn = panel.querySelector('#cfv-mode-edit');
        const saveBtn = panel.querySelector('#cfv-save');
        const rendered = panel.querySelector('#cfv-rendered');
        const editor = panel.querySelector('#cfv-editor');
        if (!viewBtn || !editBtn || !saveBtn || !rendered || !editor) return;

        const activeCls = 'cfv-toggle-btn px-3 py-1.5 font-medium bg-bm-accent text-white transition-colors';
        const inactiveCls = 'cfv-toggle-btn px-3 py-1.5 font-medium hover:bg-slate-50 transition-colors';

        viewBtn.addEventListener('click', () => {
            if (isMarkdown(payload.name)) {
                rendered.innerHTML = renderMarkdown(currentContent);
            } else {
                const lang = getLanguage(payload.name);
                const code = isJson(payload.name) ? prettyJson(currentContent) : currentContent;
                rendered.innerHTML = `<pre class="text-xs leading-relaxed"><code class="hljs">${highlightCode(code, lang)}</code></pre>`;
            }
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
                // Switch back to view mode
                viewBtn.click();
            } catch (err) {
                console.error('[CompanyFileViewer] Save failed:', err);
            } finally {
                saveBtn.textContent = 'Save';
                saveBtn.disabled = false;
            }
        });
    }

    // ─── Print ───

    function handlePrint() {
        window.print();
    }

    // ─── JSON Pretty-Print ───

    function prettyJson(raw) {
        try {
            return JSON.stringify(JSON.parse(raw), null, 2);
        } catch (_) {
            return raw;
        }
    }

    return { open, close };
})();
