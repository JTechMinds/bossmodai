/**
 * BossMod AI — making a thread, from the rail.
 *
 * Split out of shell/roster-threads.js the way roster-people.js was split out
 * of roster.js: the same seam, one level down. Threads READS the list the
 * server holds; this MAKES a new one. They share a section and nothing else —
 * this never reads `store.threads`, and Threads never posts.
 *
 * Creation is two states of ONE row, not a row that grows. The section header
 * has three slots: the `THREADS` label, a middle slot, and an action group at
 * the right. Idle, the middle slot invites — "Select teammates and start a
 * shared thread." — and the group holds the `+`. Selecting, the middle slot
 * counts what is picked and the group holds Cancel and the confirm.
 *
 * The control that STARTS the mode is the control that ends it. Cancel used to
 * sit in a block of its own below the filters, so the operator entered the
 * mode from the header and had to hunt somewhere else for the way out; the
 * hint and the count each cost a line of rail besides. Both rows are gone and
 * nothing moved further than one slot.
 *
 * Every control here is icon-only, so each carries its own accessible name.
 * Swapping the group also moves keyboard focus onto the group's new first
 * control when the departing one held it — a mode entered from the keyboard
 * must not drop focus on the body.
 */
const BossModThreadCreate = (() => {
    const { h, clear } = BossModDom;

    const THREAD_HINT = 'Select teammates and start a shared thread.';

    /**
     * Build the creation controls.
     *
     * @param {object} deps
     * @param {(url: string, init: object, failure: string) => Promise<any|null>}
     *   deps.readJson  The rail's shared request helper. It resolves null and
     *   reports on the rail's one error line when the request fails, so every
     *   part of the rail surfaces a failure the same way.
     * @param {() => string[]} deps.getSelection  Selected agent ids, from People.
     * @param {() => boolean} deps.isSelecting  Whether People is showing its
     *   checkboxes. Read rather than mirrored: two copies of one mode is how
     *   the button and the rows end up disagreeing about which state they are in.
     * @param {() => void} deps.onEnterSelect  Ask People to show the checkboxes.
     * @param {() => void} deps.onExitSelect   Ask it to hide them and forget.
     * @param {() => void} deps.onConsumed  Called once a selection has become a
     *   thread. The selection belongs to People, so clearing the checkboxes is
     *   People's job and this is the only way to ask for it.
     * @param {(channel: object) => Promise<void>|void} deps.onCreated  The new
     *   channel summary. Showing it is the list's job, not this module's.
     * @returns {{ actions: HTMLElement, middle: HTMLElement,
     *             applySelection: () => void, destroy: () => void }}
     *   Both nodes belong on the section header row, in that order after the
     *   title.
     * @throws {Error} When any dependency is missing — a half-wired control
     *   would render a `+` whose clicks go nowhere.
     */
    function createThreadControls(deps) {
        const {
            readJson, getSelection, isSelecting,
            onEnterSelect, onExitSelect, onConsumed, onCreated,
        } = deps || {};
        if (typeof readJson !== 'function') throw new Error('[thread-create] deps.readJson is required');
        if (typeof getSelection !== 'function') {
            throw new Error('[thread-create] deps.getSelection is required');
        }
        if (typeof isSelecting !== 'function') {
            throw new Error('[thread-create] deps.isSelecting is required');
        }
        if (typeof onEnterSelect !== 'function') {
            throw new Error('[thread-create] deps.onEnterSelect is required');
        }
        if (typeof onExitSelect !== 'function') {
            throw new Error('[thread-create] deps.onExitSelect is required');
        }
        if (typeof onConsumed !== 'function') {
            throw new Error('[thread-create] deps.onConsumed is required');
        }
        if (typeof onCreated !== 'function') {
            throw new Error('[thread-create] deps.onCreated is required');
        }

        // Right-aligned on the THREADS title row. Icon-only, so the label is
        // the accessible name rather than text nobody sees. It reports the mode
        // it opens rather than going dead in it.
        const newThread = h('button', {
            class: 'roster-section-action',
            id: 'roster-new-thread',
            type: 'button',
            'aria-label': 'New thread',
            'aria-expanded': 'false',
            onclick: () => onEnterSelect(),
        }, h('i', { 'data-lucide': 'plus', 'aria-hidden': 'true' }));

        // One button, one listener. Rebinding a fresh handler on every repaint
        // would stack listeners on a node the operator is already pointing at.
        const cancelSelect = h('button', {
            class: 'roster-section-action',
            id: 'roster-cancel-select',
            type: 'button',
            'aria-label': 'Cancel',
            onclick: () => onExitSelect(),
        }, h('i', { 'data-lucide': 'x', 'aria-hidden': 'true' }));

        const createThread = h('button', {
            class: 'roster-section-action roster-confirm-thread',
            id: 'roster-create-thread',
            type: 'button',
            'aria-label': 'Create thread',
            onclick: () => { void createThreadFromSelection(); },
        }, h('i', { 'data-lucide': 'check', 'aria-hidden': 'true' }));

        const actions = h('div', { class: 'roster-section-actions' });
        // The middle slot. One line that already exists, so neither the hint
        // nor the count costs the rail any height.
        const middle = h('p', { class: 'roster-section-hint' });
        /** Which group is attached, so an unchanged repaint moves no nodes. */
        let attachedSelecting = null;

        /**
         * Leave select mode on Escape, the way every other dismissible state
         * in the shell does.
         *
         * The dialog check is shell/shortcuts.js's rule, applied here for the
         * same reason: a modal binds its own Escape handler AFTER this one and
         * is still in the document while this runs, so Escape belongs to the
         * innermost surface first. Without it, Escape inside a confirm dialog
         * would also cancel the selection behind it.
         *
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        function onDocumentKeydown(event) {
            if (event.key !== 'Escape') return;
            if (!isSelecting()) return;
            if (document.querySelector('[role="dialog"]')) return;
            onExitSelect();
        }
        document.addEventListener('keydown', onDocumentKeydown);

        /**
         * Show the group the mode calls for, keeping the keyboard with it.
         *
         * @param {boolean} selecting
         * @returns {void}
         */
        function swapActions(selecting) {
            if (attachedSelecting === selecting) return;
            const active = document.activeElement;
            const hadFocus = Boolean(active) && actions.contains(active);
            clear(actions);
            if (selecting) actions.append(cancelSelect, createThread);
            else actions.append(newThread);
            attachedSelecting = selecting;
            // The departing control is gone from the document; leaving focus
            // where it was would drop the keyboard on the body.
            if (hadFocus) (selecting ? cancelSelect : newThread).focus();
            lucide.createIcons();
        }

        /**
         * Turn the current selection into a thread.
         *
         * @returns {Promise<void>} Never rejects; readJson reports a failure on
         *   the rail's one error line and resolves null, and the mode stays
         *   open so the operator can try again with the same picks.
         */
        async function createThreadFromSelection() {
            const agentIds = getSelection();
            if (agentIds.length === 0) return;
            const channel = await readJson('/api/channels', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_ids: agentIds }),
            }, 'Could not create the thread.');
            if (channel === null) return;
            onConsumed();
            await onCreated(channel);
        }

        return {
            actions,
            middle,

            /**
             * Re-read the People selection and repaint the header row.
             *
             * Out of select mode the `+` is the whole of the invitation and
             * the middle slot says what it is for. In it, the middle slot
             * counts and the group offers the two ways out. The floor is one
             * teammate, which is what POST /api/channels accepts — a thread of
             * one is a real thing the operator can already make.
             *
             * @returns {void}
             */
            applySelection() {
                const selecting = isSelecting();
                const count = getSelection().length;
                newThread.setAttribute('aria-expanded', String(selecting));
                createThread.disabled = count === 0;
                clear(middle);
                middle.append(selecting
                    ? `${count} selected`
                    : THREAD_HINT);
                swapActions(selecting);
            },

            /**
             * Drop the document listener this control owns.
             * @returns {void}
             */
            destroy() {
                document.removeEventListener('keydown', onDocumentKeydown);
            },
        };
    }

    return { createThreadControls };
})();
