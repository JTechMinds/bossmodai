/**
 * BossMod AI — the required files in the task detail's Edit mode.
 *
 * Moved out of task-edit-mode.js on the seam between the task's file
 * requirements and the rest of its edit state. It owns the one Deliverables
 * section Edit mode shows: the task's own rows (editable), the add-row, and
 * the subtasks' files read-only beneath them, counted in one head.
 *
 * A path is never typed. "Add a required file" and each row's path open the
 * folder browser (task-file-picker.js) as the DRAFT's assignee sees their
 * files — the agent the server normalizes deliverable paths against on save —
 * so a row exists only once a path has been picked. With no assignee in the
 * draft there is no file view to browse, and the section says so.
 */
const BossModTaskEditFiles = (() => {
    const { h } = BossModDom;

    /** The edit-mode field look: text at rest, the edit hairline while editing. */
    const FIELD = 'edit-field';
    /** Shared outputs are the common case, so a first browse opens there. */
    const START_FOLDER = '/projects';
    const NO_AGENT_HINT = 'Pick an assignee to browse their files.';

    /**
     * The file deliverables a task lists now, as plain rows.
     *
     * @param {object} task
     * @returns {Array<{path: string, description: (string|null)}>}
     */
    function currentFiles(task) {
        const contract = task.work_contract;
        const items = contract && Array.isArray(contract.deliverables) ? contract.deliverables : [];
        return items
            .filter((item) => item && item.type === 'file')
            .map((item) => ({ path: item.path, description: item.description || null }));
    }

    /** A row's name, derived from its path as renderDeliverable derives it. */
    function fileName(path) {
        return path.split('/').pop() || path;
    }

    /** The folder a file path sits in; `/` for a top-level one. */
    function folderOf(path) {
        const cut = path.lastIndexOf('/');
        return cut > 0 ? path.slice(0, cut) : '/';
    }

    /**
     * Build the Deliverables editor for one Edit mode session.
     *
     * @param {object} deps
     * @param {object} deps.task  The listed row; `restore()` rebuilds from it.
     * @param {{nodes: HTMLElement[], count: number}} deps.childGroups  Subtask
     *   files (childDeliverableGroups), read-only after the add-row and counted.
     * @param {Function} deps.api  Authenticated fetch helper, for the picker.
     * @param {() => string} deps.getAgentId  The draft's assignee now; '' for
     *   the unassigned backlog, which has no files to browse.
     * @param {() => void} deps.onChange  A row was added, removed or repicked,
     *   or a description was typed.
     * @returns {{section: HTMLElement,
     *   files: () => Array<{type: string, path: string, description: (string|null)}>,
     *   restore: () => void, setEditing: (on: boolean) => void,
     *   syncAgent: () => void}} `files` is the rows on screen, in order.
     *   `syncAgent` re-reads `getAgentId` to allow or refuse browsing; call it
     *   after every assignee change.
     * @throws {Error} When a dependency is missing.
     */
    function create(deps) {
        const { task, childGroups, api, getAgentId, onChange } = deps || {};
        if (!task || !task.id) throw new Error('[task-edit-files] deps.task is required');
        const groupsOk = childGroups && Array.isArray(childGroups.nodes) && typeof childGroups.count === 'number';
        if (!groupsOk) throw new Error('[task-edit-files] deps.childGroups must be {nodes, count}');
        if (typeof api !== 'function') throw new Error('[task-edit-files] deps.api is required');
        if (typeof getAgentId !== 'function') throw new Error('[task-edit-files] deps.getAgentId is required');
        if (typeof onChange !== 'function') throw new Error('[task-edit-files] deps.onChange is required');

        /** The rows on screen, in order: `{row, path, note, pathButton}`. */
        let files = [];
        let editing = false;
        /** The folder of the last pick in this Edit mode, where the next browse opens. */
        let lastFolder = null;

        const count = h('span', { class: 'task-detail-count' }, '0');
        const hint = h('p', { class: 'task-detail-meta task-detail-files-hint' }, NO_AGENT_HINT);
        const addRow = h('button', {
            class: 'task-detail-file task-detail-file-add', id: 'ct-edit-add-file', type: 'button',
            onclick: () => browse(lastFolder || START_FOLDER, (path) => {
                fileRow({ path, description: null });
                onChange();
            }),
        },
            h('i', { 'data-lucide': 'plus', 'aria-hidden': 'true' }),
            h('span', { class: 'task-detail-file-name' }, 'Add a required file'));
        const section = h('section', { class: 'task-detail-section' },
            h('div', { class: 'task-detail-section-head' }, h('span', {}, 'Deliverables'), count),
            hint, addRow, ...childGroups.nodes);

        /** The head's count: the task's own rows on screen plus the subtasks'. */
        const recount = () => { count.textContent = String(files.length + childGroups.count); };

        /**
         * Open the picker as the draft's assignee.
         * @param {string} startPath
         * @param {(path: string) => void} onPick
         * @returns {void}
         * @throws {Error} With no assignee: the controls that call this are
         *   disabled then, so reaching it means that guard broke.
         */
        function browse(startPath, onPick) {
            const agentId = getAgentId();
            if (!agentId) throw new Error('[task-edit-files] there is no assignee to browse as');
            BossModTaskFilePicker.open({
                api,
                agentId,
                startPath,
                onPick: (path) => {
                    lastFolder = folderOf(path);
                    onPick(path);
                },
            });
        }

        /** Browsing needs Edit mode and an assignee; the hint says why not. */
        function syncAgent() {
            const agentId = getAgentId();
            hint.hidden = Boolean(agentId);
            const refused = !editing || !agentId;
            addRow.disabled = refused;
            files.forEach((entry) => { entry.pathButton.disabled = refused; });
        }

        /**
         * One editable deliverable row, in the view row's look and order:
         * glyph, name, what it is, path — then its ✕. The path is a button
         * that reopens the picker at its folder.
         * @param {{path: string, description: (string|null)}} file
         * @returns {{row: HTMLElement, path: string, note: HTMLInputElement,
         *   pathButton: HTMLButtonElement}}
         */
        function fileRow(file) {
            const entry = { path: file.path };
            const name = h('span', { class: 'task-detail-file-name' }, fileName(file.path));
            entry.note = h('input', {
                class: `task-detail-file-desc ${FIELD}`, type: 'text', 'data-field': 'description',
                'aria-label': 'What the file is', placeholder: 'What the file is',
                oninput: () => onChange(),
            });
            entry.note.value = file.description || '';
            entry.note.readOnly = !editing;
            entry.pathButton = h('button', {
                class: `task-detail-file-path ${FIELD}`, type: 'button', 'data-field': 'path',
                'aria-label': `Change file: ${file.path}`,
                onclick: () => browse(folderOf(entry.path), (next) => {
                    entry.path = next;
                    entry.pathButton.textContent = next;
                    entry.pathButton.setAttribute('aria-label', `Change file: ${next}`);
                    name.textContent = fileName(next);
                    onChange();
                }),
            }, file.path);
            entry.row = h('div', { class: 'task-detail-file', 'data-file-row': '' },
                h('i', { 'data-lucide': 'file-text', 'aria-hidden': 'true' }),
                name, entry.note, entry.pathButton,
                h('button', {
                    class: 'btn btn-sm conversation-action task-detail-file-remove', type: 'button',
                    'aria-label': 'Remove this file', 'data-tooltip': 'Remove this file',
                    onclick: () => {
                        entry.row.remove();
                        files = files.filter((item) => item !== entry);
                        recount();
                        addRow.focus();
                        onChange();
                    },
                }, h('i', { 'data-lucide': 'x', 'aria-hidden': 'true' })));
            files.push(entry);
            section.insertBefore(entry.row, addRow);
            recount();
            syncAgent();
            BossModIcons.paint(entry.row, 'task-edit-files');
            return entry;
        }

        return {
            section,
            /** @returns {Array<{type: string, path: string, description: (string|null)}>} */
            files: () => files.map((entry) => ({
                type: 'file', path: entry.path, description: entry.note.value.trim() || null,
            })),
            /** Put the rows back to the task's; the draft's rows are dropped. */
            restore() {
                files.forEach((entry) => entry.row.remove());
                files = [];
                currentFiles(task).forEach((file) => fileRow(file));
                recount();
                syncAgent();
            },
            /** Descriptions read as text, and browsing is refused, outside Edit mode. */
            setEditing(on) {
                editing = on;
                files.forEach((entry) => { entry.note.readOnly = !on; });
                syncAgent();
            },
            syncAgent,
        };
    }

    return { create, currentFiles };
})();
