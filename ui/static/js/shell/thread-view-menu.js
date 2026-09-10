/**
 * BossMod AI — which thread list the rail is showing.
 *
 * The third owner of the THREADS section header, beside shell/roster-threads.js
 * (the list) and shell/thread-create.js (making one). Same seam, one level
 * along: reading a list, making a list, and choosing WHICH list are three jobs,
 * and this is the third. It was split out when the Threads half crossed the
 * 400-line cap — the same reason roster-people.js came out of roster.js and
 * thread-create.js came out of roster-threads.js.
 *
 * It owns the `⋯`, the panel behind it, and the segment inside that. It owns no
 * STATE: which list is showing belongs to the half that fetches it, so this
 * asks (`getStatus`) and reports (`onSelect`) and never keeps a copy. Two
 * copies of one mode is how a control and the list under it end up disagreeing.
 *
 * ONE GLYPH FOR A MENU, across the app. This control was a gear for a day and
 * it was the third different mark for the same idea in one window — `⋯` on the
 * conversation header, sliders here, the application gear in the app header.
 * The rule that replaced them: `⋯` opens a menu of things you can do to the
 * thing beside it, and a gear means application settings and appears once.
 *
 * WHAT IS IN THE PANEL is a labelled segment — `Thread view: Active |
 * Archived` — and it got there by way of two wrong answers. A switch reading
 * `Show archived threads` was wrong about the data: it implied the archived
 * ones would join what was already on screen, and the two lists are exclusive,
 * one request and one status. A row that renamed itself `View archives` /
 * `View active threads` fixed that and was too quiet — a control whose only
 * statement of the current state is which words it happens to be wearing makes
 * you read it to find out where you are. A segment says both things at once:
 * the two lists are the two options, and the filled one is where you are.
 *
 * It is only affordable in a panel. A permanent pair of pills spent a row of a
 * 220px rail answering a question that is `Active` almost every time it is
 * asked; behind the `⋯` it costs no height at all, and once the operator has
 * opened the menu to change it, it should be unmissable.
 */
const BossModThreadViewMenu = (() => {
    const { h } = BossModDom;

    /** The `⋯`'s accessible name and its tooltip: one string, never two. */
    const MENU_LABEL = 'Thread list options';
    /** The segment's caption, and the id the group is labelled by. */
    const SEGMENT_LABEL = 'Thread view';
    const SEGMENT_ID = 'roster-thread-view-label';

    /** The two lists, in the order the segment shows them. */
    const OPTIONS = Object.freeze([
        { status: 'active', label: 'Active', id: 'channels-filter-active' },
        { status: 'archived', label: 'Archived', id: 'channels-filter-archived' },
    ]);

    /**
     * Build the view menu.
     *
     * @param {object} deps
     * @param {() => HTMLElement} deps.getContainer  What the panel is
     *   positioned against — the section header row. A THUNK rather than the
     *   element, because that row cannot be built until this control exists to
     *   go in it; resolving at open time is what breaks the cycle without
     *   either module reaching for the other's DOM.
     * @param {() => 'active'|'archived'} deps.getStatus  Which list is showing.
     *   Read rather than mirrored: the half that fetches the list owns that
     *   fact, and a second copy here is how the pills and the list drift apart.
     * @param {(status: 'active'|'archived') => void} deps.onSelect  Called with
     *   the list the operator picked. Picking the one already showing is passed
     *   on unchanged — deciding that is a no-op belongs to whoever would run
     *   the request, not to the control that reports the click.
     * @returns {{ button: HTMLElement, applyStatus: () => void,
     *             close: () => void, destroy: () => void }}
     * @throws {Error} When any dependency is missing — a `⋯` whose panel has
     *   nowhere to hang, nothing to report, or no one to report to would render
     *   and then do nothing.
     */
    function createThreadViewMenu(deps) {
        const { getContainer, getStatus, onSelect } = deps || {};
        if (typeof getContainer !== 'function') {
            throw new Error('[thread-view-menu] deps.getContainer is required');
        }
        if (typeof getStatus !== 'function') {
            throw new Error('[thread-view-menu] deps.getStatus is required');
        }
        if (typeof onSelect !== 'function') {
            throw new Error('[thread-view-menu] deps.onSelect is required');
        }

        // Built once, because they live inside the panel while it is open and
        // rebuilding them per open would swap a node under the pointer that is
        // already on it.
        //
        // Picking one does NOT close the panel. The filled pill moving is the
        // confirmation, and a menu that vanished as it answered would take the
        // answer with it — the same reason the conversation's receipts
        // preference stays put when it is toggled.
        const options = OPTIONS.map((option) => h('button', {
            class: 'menu-segment-option',
            id: option.id,
            type: 'button',
            onclick: () => onSelect(option.status),
        }, option.label));

        // The caption is a real heading with an id rather than an aria-label on
        // the group: one string, on screen and in the accessibility tree, so
        // the two cannot drift apart.
        const group = h('div', { class: 'menu-group' },
            h('p', { class: 'menu-label', id: SEGMENT_ID }, SEGMENT_LABEL),
            h('div', {
                class: 'menu-segment',
                role: 'group',
                'aria-labelledby': SEGMENT_ID,
            }, options));

        const button = h('button', {
            class: 'roster-section-action roster-thread-view',
            id: 'roster-thread-view',
            type: 'button',
            'aria-label': MENU_LABEL,
            'data-tooltip': MENU_LABEL,
            // dialog, not menu: core/overlays.js's panel is a role="dialog" and
            // its children are ordinary buttons rather than menuitems. Same
            // call the conversation chrome's `⋯` makes, for the same reason.
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));

        /** The open panel, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;

        /** @returns {void} */
        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the options, or put them away again.
         *
         * The panel is core/overlays.js's — it already owns the focus trap,
         * Esc, the press-outside dismiss and returning focus to the `⋯`. A
         * second popover implementation is exactly the duplication the
         * primitives exist to remove.
         *
         * @returns {void}
         */
        function toggle() {
            if (menu) {
                close();
                return;
            }
            menu = BossModOverlays.createMenu({
                anchor: button,
                label: MENU_LABEL,
                items: [group],
                container: getContainer(),
                onClose: () => {
                    menu = null;
                    button.setAttribute('aria-expanded', 'false');
                },
            });
            // The rail is exactly as wide as the menu's own minimum, so the
            // panel takes the header row's width instead. See overlays.css.
            menu.element.setAttribute('data-menu', 'thread-view');
            // A menu panel is DETACHED while it is closed, so the document
            // sweep that paints the rail at mount can never reach inside one —
            // which is the bug that rendered the conversation's Archive row as
            // a bare word. The segment carries no glyph today; this is what
            // keeps that from becoming true again the first time one is added,
            // and it costs nothing when there is nothing to paint.
            BossModIcons.paint(menu.element, 'thread-view-menu');
            button.setAttribute('aria-expanded', 'true');
        }

        return {
            button,

            /**
             * Fill whichever pill the list is currently showing.
             *
             * The status changes from three directions — the segment itself, a
             * fresh thread landing, and a live `channel_updated` pulling the
             * rail back to Active — so the pills are written FROM the status
             * rather than the status being read off the pills.
             *
             * @returns {void}
             */
            applyStatus() {
                const status = getStatus();
                options.forEach((option, index) => {
                    option.setAttribute('aria-pressed', String(OPTIONS[index].status === status));
                });
            },

            close,

            /**
             * Put the panel away. A panel left open would outlive the rail it
             * hangs off, and its press-outside listener would outlive both.
             * @returns {void}
             */
            destroy() {
                close();
            },
        };
    }

    return { createThreadViewMenu };
})();
