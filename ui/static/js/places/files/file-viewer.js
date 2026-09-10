/**
 * BossMod AI — the shared file viewer.
 *
 * Ported from company-file-viewer.js. Three things changed and all three are
 * deliberate.
 *
 * The DOM is built with BossModDom.h. File names, breadcrumb labels and file
 * contents are all attacker-influenced strings — an agent chooses them — and
 * h() escapes by construction where the template literals it replaces did not.
 * Deciding a file's kind and turning its bytes into nodes is file-content.js.
 *
 * The hand-rolled overlay became BossModOverlays.slideOver, so the viewer traps
 * focus, answers Esc, and returns focus to whatever opened it. It is a panel of
 * arbitrary content rather than a question with buttons, which is the seam
 * between the module's two overlays; createModal traps only across its own
 * action row and would have left Tab walking out of the editor.
 *
 * The authenticated fetch arrives through `deps.api`. An image element pointed
 * straight at an /api path cannot carry the X-BossMod-Token header, so image
 * previews go through a blob URL and the token never lands in an attribute.
 * That path is a security property and is asserted by test_health_ops_ui.py.
 */
const BossModFileViewer = (() => {
    const { h, clear } = BossModDom;
    const CONTENT = BossModFileContent;

    const IMAGE_LOADING_COPY = 'Loading preview…';
    const IMAGE_FAILED_COPY = 'Could not load image preview (authentication required).';
    const TRUNCATED_COPY = 'Preview truncated — file exceeds display limit.';
    const BINARY_COPY = 'Binary file — preview not available for this file type.';

    let sheet = null;
    let objectUrl = null;

    function breadcrumbs(crumbs) {
        const row = h('div', { class: 'file-view-crumbs' });
        (Array.isArray(crumbs) ? crumbs : []).forEach((crumb, index) => {
            if (index > 0) row.append(h('span', { class: 'file-crumb-sep' }, '/'));
            row.append(h('span', { class: 'file-crumb' }, String(crumb.label || crumb.name || '')));
            if (crumb.agent_name) {
                row.append(h('span', { class: 'file-crumb-agent' }, `(${crumb.agent_name})`));
            }
        });
        return row;
    }

    // ─── Authenticated image preview ───

    /**
     * Fetch through the injected helper and hand back an object URL.
     *
     * The same mechanism as api-client.js's `apiFetchBlobUrl`, reached through
     * `deps.api` instead of the global so this module names no dependency it
     * was not given.
     *
     * @param {Function} api
     * @param {string} url
     * @returns {Promise<string>}
     * @throws {Error} On a non-OK response, so the caller can say so.
     */
    async function fetchBlobUrl(api, url) {
        const res = await api(url, { cache: 'no-store' });
        if (!res.ok) throw new Error((await res.text()) || `Request failed (${res.status})`);
        return URL.createObjectURL(await res.blob());
    }

    function revokeImageObjectUrl() {
        if (!objectUrl) return;
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
    }

    /**
     * Point an <img> at an authenticated blob URL.
     *
     * @param {object} deps
     * @param {Function} deps.api
     * @param {HTMLElement} deps.imgEl
     * @param {HTMLElement} deps.statusEl
     * @param {string} deps.path
     * @returns {Promise<void>} Never rejects; a failure replaces the status
     *   line, because a preview that silently never appears tells the operator
     *   nothing about why.
     */
    async function loadAuthenticatedImage({ api, imgEl, statusEl, path }) {
        const rawUrl = `/api/company/files/raw?path=${encodeURIComponent(path)}`;
        const opened = sheet;
        try {
            revokeImageObjectUrl();
            const url = await fetchBlobUrl(api, rawUrl);
            if (sheet !== opened) {
                // The operator closed or replaced the panel while this was in
                // flight; the URL would otherwise leak for the page's lifetime.
                URL.revokeObjectURL(url);
                return;
            }
            objectUrl = url;
            imgEl.setAttribute('src', url);
            imgEl.hidden = false;
            statusEl.hidden = true;
        } catch (err) {
            console.error('[file-viewer] image preview failed', err);
            statusEl.textContent = IMAGE_FAILED_COPY;
            statusEl.classList.add('file-view-error');
        }
    }

    // ─── Panel ───

    function render(payload, api) {
        const name = String(payload.name || 'File');
        const image = CONTENT.isImage(name);
        const binary = payload.binary === true && !image;
        const editable = !binary && !image;
        let content = String(payload.content || '');

        // `md` is the shared prose vocabulary in css/markdown.css. Rendered
        // markdown had none until it existed: this pane painted headings and
        // tables at browser defaults, which is a different document from the
        // one the transcript shows for the same file.
        const rendered = h('div', { class: 'file-view-rendered md' });
        const editor = h('textarea', { class: 'file-view-editor', hidden: true });
        const status = h('p', { class: 'file-view-status', role: 'status' });
        const size = BossModFormat.formatFileSize(payload.size_bytes);
        const updated = payload.updated_at ? new Date(payload.updated_at).toLocaleString() : '';

        const save = h('button', {
            class: 'btn', type: 'button', hidden: true, onclick: () => { void persist(); },
        }, 'Save');
        const view = h('button', {
            class: 'file-view-tab', type: 'button', 'aria-pressed': 'true',
            onclick: () => setMode('view'),
        }, 'View');
        const edit = h('button', {
            class: 'file-view-tab', type: 'button', 'aria-pressed': 'false',
            onclick: () => setMode('edit'),
        }, 'Edit');

        function setMode(mode) {
            const editing = mode === 'edit';
            if (editing) editor.value = content;
            else CONTENT.renderInto(rendered, name, content);
            rendered.hidden = editing;
            editor.hidden = !editing;
            save.hidden = !editing;
            view.setAttribute('aria-pressed', String(!editing));
            edit.setAttribute('aria-pressed', String(editing));
        }

        /**
         * Write the editor back.
         * @returns {Promise<void>} Never rejects; a failure stays on screen
         *   with the draft intact, because a save that silently failed is the
         *   worst outcome this panel can produce.
         */
        async function persist() {
            const next = editor.value;
            save.disabled = true;
            save.textContent = 'Saving…';
            status.textContent = '';
            status.classList.remove('file-view-error');
            try {
                const res = await api('/api/company/files', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: payload.path, content: next }),
                });
                if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));
                content = next;
                setMode('view');
                status.textContent = 'Saved.';
            } catch (err) {
                console.error('[file-viewer] save failed', err);
                status.textContent = `Could not save: ${(err && err.message) || 'the request failed.'}`;
                status.classList.add('file-view-error');
            } finally {
                save.disabled = false;
                save.textContent = 'Save';
            }
        }

        const controls = h('div', { class: 'file-view-controls' },
            h('button', {
                class: 'file-view-tab', type: 'button', onclick: () => window.print(),
            }, 'Print'));
        if (editable) controls.append(view, edit, save);

        const body = h('div', { class: 'file-view' },
            breadcrumbs(payload.breadcrumbs),
            h('p', { class: 'file-view-meta' }, [size, updated].filter(Boolean).join(' · ')),
            controls,
            status);

        if (image) {
            const img = h('img', { class: 'file-view-image', alt: name, hidden: true });
            const loading = h('p', { class: 'file-view-status' }, IMAGE_LOADING_COPY);
            const dims = h('p', { class: 'file-view-meta' });
            img.addEventListener('load', () => {
                dims.textContent = `${img.naturalWidth} × ${img.naturalHeight} px`;
            });
            body.append(h('div', { class: 'file-view-figure' }, img, loading, dims));
            openSheet(name, body);
            void loadAuthenticatedImage({ api, imgEl: img, statusEl: loading, path: payload.path });
            return;
        }

        if (binary) {
            body.append(h('p', { class: 'place-empty-hint' }, BINARY_COPY));
        } else {
            CONTENT.renderInto(rendered, name, content);
            body.append(rendered, editor);
        }
        if (payload.truncated) body.append(h('p', { class: 'file-view-meta' }, TRUNCATED_COPY));
        openSheet(name, body);
    }

    function openSheet(title, body) {
        sheet = BossModOverlays.slideOver({
            title,
            body,
            onClose: () => { revokeImageObjectUrl(); sheet = null; },
        });
        sheet.element.classList.add('file-view-panel');
    }

    // ─── Open / close ───

    /**
     * Open one file.
     *
     * @param {string} path  Virtual path.
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper, from ctx.
     * @param {string} [deps.apiUrl]  A different read endpoint — the desk
     *   browser reads an agent's desk rather than the company workspace.
     * @returns {Promise<void>}
     * @throws {Error} When the file cannot be read. The caller decides what to
     *   say; swallowing it would leave a click that does nothing.
     */
    async function open(path, deps) {
        const { api, apiUrl } = deps || {};
        if (typeof api !== 'function') throw new Error('[file-viewer] deps.api is required');
        close();
        const url = apiUrl || `/api/company/files?path=${encodeURIComponent(path)}`;
        let payload;
        try {
            const res = await api(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));
            payload = await res.json();
        } catch (err) {
            console.error('[file-viewer] could not load the file', err);
            throw err;
        }
        render(payload, api);
    }

    /**
     * Close the viewer, if it is open.
     * @returns {void}
     */
    function close() {
        if (sheet) sheet.close();
        // slideOver's onClose has already run for that path; this covers a
        // close() with no sheet and keeps the revoke unconditional.
        revokeImageObjectUrl();
        sheet = null;
    }

    return { open, close };
})();
