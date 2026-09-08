/**
 * BossMod AI — the Notes section of an agent's desk.
 *
 * Spec 7, resolved 2026-09-07 with no schema work at all. "Notes" was carried
 * for two phases as a requirement no column could satisfy, and Phase 2B put
 * the done/fail bar in the slot as a stand-in. The operator's answer was that
 * agents already write their notes as markdown in their own workspace, and the
 * engine already models exactly that: `/me/notes/` is a real folder, the desk
 * payload already tags what it finds there `category: 'note'`, and the agent
 * CLI already writes into it. So Notes is a surfacing problem — no column, no
 * migration, no endpoint, no write UI, and no second file viewer.
 *
 * Absent is normal and is NOT an error. A new agent has written nothing, so
 * the folder does not exist and the desk returns 404. That renders the empty
 * state. A 500 or a dead socket renders the error state with a retry. The
 * whole point of separating them is that the operator can tell "nothing here
 * yet" from "we could not look".
 */
const BossModDeskNotes = (() => {
    const { h, clear } = BossModDom;

    /** Where agents keep their notes. The engine's path, not a preference. */
    const NOTES_PATH = '/me/notes';
    const EMPTY_TITLE = 'No notes yet';
    const EMPTY_HINT = 'Agents write notes as markdown into /me/notes in their own workspace. Anything saved there shows up here.';
    const ERROR_COPY = 'Notes could not be loaded.';

    /**
     * A note's display title: the artifact title the agent gave it, falling
     * back to the file name on disk. Both are agent output, so both go through
     * h() as text and are never interpolated into markup.
     *
     * @param {object} entry  One desk listing entry.
     * @returns {string}
     */
    function noteTitle(entry) {
        const artifact = entry && entry.artifact;
        const title = artifact && artifact.title ? String(artifact.title).trim() : '';
        return title || String((entry && entry.name) || 'Untitled note');
    }

    /**
     * Newest first, by the time the file was last written. An entry with no
     * timestamp sorts last rather than being dropped — an unreadable mtime is
     * not a reason to hide a note the agent wrote.
     *
     * @param {object[]} entries
     * @returns {object[]}  A new array; the caller's is not reordered.
     */
    function newestFirst(entries) {
        return entries.slice().sort((a, b) => {
            const left = Date.parse(a.updated_at || '');
            const right = Date.parse(b.updated_at || '');
            if (Number.isNaN(left) && Number.isNaN(right)) return 0;
            if (Number.isNaN(left)) return 1;
            if (Number.isNaN(right)) return -1;
            return right - left;
        });
    }

    /**
     * Build the Notes section.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.agentId
     * @param {(path: string) => void} deps.onOpenFolder  A subfolder of
     *   /me/notes is handed to the desk browser rather than opened here.
     *   Listing folders and doing nothing with them, or omitting them, would
     *   both hide notes the agent filed one level down.
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>,
     *             destroy: () => void }}
     * @throws {Error} When any dependency is missing.
     */
    function createDeskNotes(deps) {
        const { api, agentId, onOpenFolder } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-notes] deps.api is required');
        if (!agentId) throw new Error('[desk-notes] deps.agentId is required');
        if (typeof onOpenFolder !== 'function') {
            throw new Error('[desk-notes] deps.onOpenFolder is required');
        }

        const load = BossModGates.createLoadGeneration();
        const listEl = h('div', { class: 'desk-notes' });
        const element = h('section', { class: 'desk-section' },
            h('p', { class: 'desk-section-title' }, 'Notes'),
            listEl);
        let destroyed = false;

        function deskUrl(path) {
            return `/api/agents/${agentId}/desk?path=${encodeURIComponent(path)}`;
        }

        function renderLoading() {
            clear(listEl);
            listEl.append(h('p', { class: 'context-skeleton' }, 'Loading notes…'));
        }

        function renderEmpty() {
            clear(listEl);
            listEl.append(
                h('p', { class: 'context-empty' }, EMPTY_TITLE),
                h('p', { class: 'context-hint' }, EMPTY_HINT));
        }

        function renderError(message) {
            clear(listEl);
            listEl.append(
                h('p', { class: 'context-error', role: 'alert' }, message || ERROR_COPY),
                h('button', {
                    class: 'desk-files-btn',
                    id: 'desk-notes-retry-btn',
                    type: 'button',
                    onclick: () => { void refresh(); },
                }, 'Try again'));
        }

        function renderNotes(entries) {
            clear(listEl);
            newestFirst(entries).forEach((entry) => {
                const isDir = entry.is_dir === true;
                const path = String(entry.path || '');
                listEl.append(h('button', {
                    class: 'desk-note',
                    type: 'button',
                    'data-path': path,
                    'data-is-dir': isDir ? '1' : '0',
                    onclick: () => {
                        if (isDir) {
                            onOpenFolder(path);
                            return;
                        }
                        // The one viewer, the same one desk-files.js opens.
                        void BossModFileViewer.open(path, { api, apiUrl: deskUrl(path) })
                            .catch(() => renderError('That note could not be opened.'));
                    },
                },
                    h('span', { class: 'desk-note-title' }, noteTitle(entry)),
                    h('span', { class: 'desk-note-meta' },
                        BossModFormat.formatRelativeTime(entry.updated_at) || 'Not saved yet')));
            });
        }

        /**
         * Re-read the notes folder.
         *
         * @returns {Promise<void>} Never rejects; every outcome is one of the
         *   four states.
         */
        async function refresh() {
            const loadId = load.next();
            renderLoading();

            let payload;
            try {
                const res = await api(deskUrl(NOTES_PATH), { cache: 'no-store' });
                if (!load.isCurrent(loadId) || destroyed) return;
                // 404 is the folder not existing yet, which is what a brand
                // new agent looks like. It is the empty state, never an error.
                if (res.status === 404) {
                    renderEmpty();
                    return;
                }
                if (!res.ok) {
                    renderError(`${ERROR_COPY} (HTTP ${res.status})`);
                    return;
                }
                payload = await res.json();
            } catch (err) {
                if (!load.isCurrent(loadId) || destroyed) return;
                console.error('[desk-notes] could not read the notes folder', err);
                renderError(ERROR_COPY);
                return;
            }
            if (!load.isCurrent(loadId) || destroyed) return;

            const entries = Array.isArray(payload.entries) ? payload.entries : [];
            if (!entries.length) {
                renderEmpty();
                return;
            }
            renderNotes(entries);
        }

        void refresh();

        return {
            element,
            refresh,

            /**
             * Stop painting. An in-flight response for a desk the operator has
             * left is dropped rather than painted over the one they opened.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
            },
        };
    }

    return { createDeskNotes, NOTES_PATH };
})();
