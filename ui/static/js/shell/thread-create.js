/**
 * BossMod AI — making a thread, from the rail.
 *
 * Split out of shell/roster-threads.js the way roster-people.js was split out
 * of roster.js: the same seam, one level down. Threads READS the list the
 * server holds; this MAKES a new one. They share a section and nothing else —
 * this never reads `store.threads`, and Threads never posts.
 *
 * Creation is two states, not one permanent button. The `+` on the THREADS
 * header row opens select mode; `Create with N` and `Cancel` close it. The old
 * always-on Create Thread button was disabled most of the time, which made a
 * permanent row of checkboxes the only way to understand what it wanted.
 *
 * The `+` sits on the section header rather than in a row of its own: it is
 * always visible, costs no vertical space, and does not scroll away as the
 * thread list grows. Being icon-only it carries its own accessible name, and it
 * reports `aria-expanded` rather than going dead once the mode it opens is
 * open — a disabled control would also drop keyboard focus at the moment the
 * operator activated it.
 *
 * The hint is instructions for select mode, so it is attached WITH select mode.
 * Standing under the list permanently, it explained a mode nobody was in.
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
     * @returns {{ action: HTMLElement, element: HTMLElement,
     *             applySelection: () => void }}
     *   `action` belongs on the section header row; `element` is the block that
     *   appears while select mode is on.
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
        // the accessible name rather than text nobody sees.
        const action = h('button', {
            class: 'roster-section-action',
            id: 'roster-new-thread',
            type: 'button',
            'aria-label': 'New thread',
            'aria-expanded': 'false',
            onclick: () => onEnterSelect(),
        }, h('i', { 'data-lucide': 'plus', 'aria-hidden': 'true' }));

        // One button, one listener. Rebinding a fresh handler on every repaint
        // would stack listeners on a node the operator is already pointing at.
        const createThread = h('button', {
            class: 'btn btn-quiet roster-create-thread',
            type: 'button',
            onclick: () => { void createThreadFromSelection(); },
        });
        const cancelSelect = h('button', {
            class: 'btn btn-quiet roster-thread-cancel',
            type: 'button',
            onclick: () => onExitSelect(),
        }, 'Cancel');
        const selectHint = h('p', { class: 'roster-thread-hint' }, THREAD_HINT);

        // Built once and ATTACHED with the mode, never hidden with CSS: a
        // hidden Cancel is still a tab stop for a mode nobody is in, and a
        // hint nobody is meant to read is still read.
        const element = h('div', { class: 'roster-select-actions' });
        // Tracked here rather than read back off the DOM: "is it attached" is
        // this module's own bookkeeping, and asking the node makes the answer
        // depend on which parent property the host happens to expose.
        let attached = false;

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
            action,
            element,

            /**
             * Re-read the People selection and repaint the controls.
             *
             * Out of select mode there are none: the `+` is the whole of the
             * invitation. In it, the hint explains the mode, the button counts
             * what is picked, and Cancel is the way out. The floor is one
             * teammate, which is what POST /api/channels accepts — a thread of
             * one is a real thing the operator can already make.
             *
             * @returns {void}
             */
            applySelection() {
                const selecting = isSelecting();
                const count = getSelection().length;
                action.setAttribute('aria-expanded', String(selecting));
                clear(createThread);
                createThread.append(`Create with ${count}`);
                createThread.disabled = count === 0;
                if (selecting && !attached) {
                    element.append(selectHint, createThread, cancelSelect);
                    attached = true;
                } else if (!selecting && attached) {
                    clear(element);
                    attached = false;
                }
            },
        };
    }

    return { createThreadControls };
})();
