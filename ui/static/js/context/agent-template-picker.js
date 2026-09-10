/**
 * BossMod AI — step 1 of Add agent: pick a template, or start blank.
 *
 * The create dialog's first step. Its whole input is the LOCAL template
 * library, read through context/agent-templates-api.js in one indexed query,
 * so it knows nothing about GitHub, refs, catalogs or trust; that is the
 * marketplace's job, and this offers the door to it rather than a copy of it.
 *
 * Blank is the FIRST cell, in its own column, so it reads as a peer of the
 * templates instead of the fallback under them. Clicking any cell is the
 * choice — there is no confirm step, because picking again is free: the
 * dialog keeps the draft when the same cell is picked twice.
 *
 * Built with BossModDom.h. Titles, specialties and descriptions are remote,
 * pack-authored text that arrived here through an install; none of it may
 * reach a markup-string path.
 */
const BossModAgentTemplatePicker = (() => {
    const { h, clear } = BossModDom;
    const API = BossModAgentTemplatesApi;

    const COPY = Object.freeze({
        blank: 'Blank agent',
        blankHint: 'Start from an empty form.',
        findLabel: 'Find a template',
        findHint: 'Filter by name, specialty or description',
        reading: 'Reading your template library…',
        empty: 'No templates installed yet.',
        browse: 'Browse marketplace',
        failed: 'Couldn’t read your template library.',
        retry: 'Try again',
        noMatch: (query) => `No template matches “${query}”.`,
    });

    // "code-review" -> "Code Review". The slug is the pack's, so the label is
    // derived rather than kept in a map that would go stale behind it.
    function categoryLabel(slug) {
        return String(slug || '').split('-').filter(Boolean)
            .map((part) => part.replace(/^[a-z]/, (letter) => letter.toUpperCase()))
            .join(' ');
    }

    // Title, specialty and description — the same three fields the
    // marketplace filters on, so a template found there is findable here.
    function haystack(row) {
        return `${row.title || ''} ${row.specialty || ''} ${row.description || ''}`
            .toLowerCase();
    }

    /**
     * Build the picker.
     *
     * @param {object} deps
     * @param {(template: object|null) => void} deps.onPick  Called with the
     *   chosen `AgentTemplate` row, or NULL for Blank. Null is a real answer,
     *   not an absent one — the dialog builds a different form for it.
     * @param {() => void} deps.onBrowse  Opens the marketplace. Reached from
     *   the empty state, which is the only place a first-run operator can see.
     * @returns {{element: HTMLElement, refresh: () => Promise<void>,
     *   focus: () => void}} `refresh` re-reads the library and repaints;
     *   `focus` puts the keyboard on the Find box, or on the Blank cell when
     *   there is nothing to filter — the dialog owes focus a home on every
     *   step swap, and an input that is not rendered cannot take it.
     * @throws {Error} When either callback is missing. A picker whose cells
     *   answer to nobody is a dead end, and a silent one.
     */
    function createPicker(deps) {
        const { onPick, onBrowse } = deps || {};
        if (typeof onPick !== 'function') throw new Error('[picker] onPick is required');
        if (typeof onBrowse !== 'function') throw new Error('[picker] onBrowse is required');

        // 'loading' is a state, not a spinner: the read is one local query and
        // is normally done before the operator looks, but it can FAIL, and a
        // library that could not be read must never render as an empty one.
        let status = 'loading';
        let templates = [];
        let query = '';

        const blankBtn = h('button', {
            class: 'picker-cell picker-blank', type: 'button', id: 'agent-pick-blank',
            onclick: () => onPick(null),
        },
            h('span', { class: 'picker-cell-title' }, COPY.blank),
            h('span', { class: 'picker-cell-sub' }, COPY.blankHint));

        const findInput = h('input', {
            class: 'picker-find', id: 'agent-template-find', type: 'search',
            placeholder: COPY.findHint,
            oninput: (event) => { query = event.target.value; renderGroups(); },
        });
        const findRow = h('div', { class: 'picker-find-row' },
            h('label', { class: 'picker-find-label', for: 'agent-template-find' },
                COPY.findLabel),
            findInput);

        const groupsEl = h('div', { class: 'picker-groups' });
        const element = h('div', { class: 'picker-body' },
            h('div', { class: 'picker-blank-col' }, blankBtn),
            h('div', { class: 'picker-list' }, findRow, groupsEl));

        /** One cell per template, grouped under its category. */
        function templateCell(row) {
            return h('button', {
                class: 'picker-cell picker-card', type: 'button',
                'data-template-id': row.id,
                onclick: () => onPick(row),
            },
                h('span', { class: 'picker-cell-title' }, row.title || ''),
                h('span', { class: 'picker-cell-sub' }, row.specialty || ''));
        }

        /** Category -> rows, in category then title order, after the filter. */
        function groupsFor(rows) {
            const byCategory = new Map();
            rows.forEach((row) => {
                const key = row.category || '';
                if (!byCategory.has(key)) byCategory.set(key, []);
                byCategory.get(key).push(row);
            });
            return Array.from(byCategory.entries())
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([slug, items]) => ({
                    slug,
                    items: items.slice().sort((a, b) => String(a.title || '')
                        .localeCompare(String(b.title || ''))),
                }));
        }

        function statusLine(text, role) {
            return h('p', { class: 'picker-status', role }, text);
        }

        function renderGroups() {
            clear(groupsEl);
            // The Find box has nothing to filter unless the library both read
            // and holds something, so it is hidden rather than offered empty.
            findRow.hidden = !(status === 'ready' && templates.length > 0);
            if (status === 'loading') {
                groupsEl.append(statusLine(COPY.reading, 'status'));
                return;
            }
            if (status === 'failed') {
                groupsEl.append(
                    statusLine(COPY.failed, 'alert'),
                    h('button', {
                        class: 'picker-action', type: 'button', id: 'agent-template-retry',
                        // refresh() repaints over this very button, so the
                        // retry places focus after it: a control the operator
                        // activated must never hand the keyboard to <body>.
                        onclick: () => { void refresh().then(focusAfterRepaint); },
                    }, COPY.retry));
                return;
            }
            if (!templates.length) {
                groupsEl.append(
                    statusLine(COPY.empty, 'status'),
                    h('button', {
                        class: 'picker-action', type: 'button', id: 'agent-template-browse',
                        onclick: () => onBrowse(),
                    }, COPY.browse));
                return;
            }
            const needle = query.trim().toLowerCase();
            const matching = needle
                ? templates.filter((row) => haystack(row).includes(needle))
                : templates;
            if (!matching.length) {
                groupsEl.append(statusLine(COPY.noMatch(query.trim()), 'status'));
                return;
            }
            // A category with no match left in it is dropped whole: an empty
            // heading reads as a section that failed to load.
            groupsFor(matching).forEach((group) => {
                groupsEl.append(h('section', { class: 'picker-category' },
                    h('h3', { class: 'picker-category-title' }, categoryLabel(group.slug)),
                    h('div', { class: 'picker-cards' }, group.items.map(templateCell))));
            });
        }

        /**
         * Re-read the library and repaint.
         *
         * @returns {Promise<void>} Never rejects: a failed read is a rendered
         *   state with a retry, not an exception the dialog has to catch.
         */
        async function refresh() {
            status = 'loading';
            renderGroups();
            try {
                const rows = await API.listTemplates();
                // A body that is not a list is a broken read, not an empty
                // library, and must not be painted as one.
                if (!Array.isArray(rows)) throw new Error('the library did not answer with a list');
                templates = rows;
                status = 'ready';
            } catch (err) {
                console.error('[picker] the template library could not be read', err);
                status = 'failed';
            }
            renderGroups();
        }

        function focus() {
            if (!findRow.hidden) findInput.focus();
            else blankBtn.focus();
        }

        /**
         * Where focus goes once a repaint has removed what was holding it.
         *
         * The panel the retry rebuilt has one of three shapes, and each has its
         * own first control: another retry if the read failed again, `Browse
         * marketplace` if it succeeded onto an empty library, and the Find box
         * if it succeeded onto a full one. Both buttons carry `.picker-action`,
         * which is why one lookup answers for both.
         *
         * @returns {void}
         */
        function focusAfterRepaint() {
            const action = groupsEl.querySelector('.picker-action');
            if (action) action.focus();
            else focus();
        }

        renderGroups();
        return { element, refresh, focus };
    }

    return { COPY, createPicker };
})();
