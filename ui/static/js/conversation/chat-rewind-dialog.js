/**
 * BossMod AI — the Rewind dialog for an agent DM.
 *
 * Presentation only: it fetches nothing and knows no store or composer. The
 * flow that opens it, loads its rows and acts on the choice is
 * conversation/chat-rewind.js. Every string the operator reads before a
 * destructive cut is here, so the promise the dialog makes and the dialog
 * cannot drift apart.
 *
 * States: loading, a load error, empty, ready (a radio group, newest first),
 * busy while the cut runs, an inline error when it fails, and a done state
 * when it succeeded with a warning the operator must read.
 */
const BossModChatRewindDialog = (() => {
    const { h, clear } = BossModDom;

    /** What a rewind does and does not undo, said before every cut. */
    const HONESTY_COPY = (name) => `Removes the selected message and everything after it from this chat `
        + `and from what ${name} sees. If ${name} is replying right now, that reply stops. Anything `
        + `${name} already did (tasks, files, memory, approvals, messages sent elsewhere) stays. `
        + 'Attachments on removed messages are deleted.';
    /** Shown while the selected row is the operator's own. */
    const RESTORE_COPY = 'Your message goes back into the composer so you can edit and resend it. '
        + 'Attachments are not restored.';
    const EMPTY_COPY = 'Nothing to rewind yet.';
    const LOADING_COPY = 'Loading messages…';
    const REMOVED_MARK = 'Will be removed';
    /** One line of a long message is enough to recognise it by. */
    const EXCERPT_CHARS = 140;

    const CONFIRM_ID = 'chat-rewind-confirm';
    const CANCEL_ID = 'chat-rewind-cancel';

    /** Radio groups need a document-unique name; two dialogs never share one. */
    let groupCount = 0;

    /**
     * A row's one-line text: whitespace collapsed and cut at EXCERPT_CHARS.
     * @param {object} point  A Message.
     * @returns {string}
     */
    function excerpt(point) {
        const text = String(point.text || '').replace(/\s+/g, ' ').trim();
        if (!text) return Array.isArray(point.attachments) && point.attachments.length ? 'Attachments only' : '(empty)';
        return text.length > EXCERPT_CHARS ? `${text.slice(0, EXCERPT_CHARS - 1)}…` : text;
    }

    /**
     * Open the dialog in its loading state.
     *
     * @param {object} options
     * @param {string} options.agentName  Who the DM is with; the copy names them.
     * @param {boolean} [options.preselectLatestHuman=false]  Select the newest
     *   operator row once the rows arrive (the error card's entry point).
     * @param {(point: object) => Promise<{warning?: string}|void>} options.onConfirm
     *   Runs the cut. While it is pending Rewind shows busy; a rejection shows
     *   its message inline and keeps the dialog open; a non-empty `warning`
     *   switches to the done state with a single Close; anything else closes.
     * @returns {{setPoints: (points: object[]) => void,
     *            setLoadError: (message: string) => void, close: () => void}}
     *   `setPoints` takes Messages oldest first, as the source returns them.
     * @throws {Error} When agentName or onConfirm is missing.
     */
    function open({ agentName, preselectLatestHuman, onConfirm } = {}) {
        if (!agentName) throw new Error('[chat-rewind-dialog] agentName is required');
        if (typeof onConfirm !== 'function') throw new Error('[chat-rewind-dialog] onConfirm is required');

        groupCount += 1;
        const groupName = `chat-rewind-${groupCount}`;
        /** Rendered rows, newest first: `{point, input, row, mark}`. */
        let rows = [];
        let selected = null;
        let busy = false;
        let closed = false;

        const statusEl = h('p', { class: 'chat-rewind-status', role: 'status' }, LOADING_COPY);
        const listEl = h('div', {
            class: 'chat-rewind-list',
            role: 'radiogroup',
            'aria-label': 'Rewind from this message',
            onkeydown: onListKeydown,
        });
        listEl.hidden = true;
        const restoreEl = h('p', { class: 'chat-rewind-note' }, RESTORE_COPY);
        restoreEl.hidden = true;
        const errorEl = h('p', { class: 'chat-rewind-error', role: 'alert' });
        const contentEl = h('div', { class: 'chat-rewind' },
            h('p', { class: 'chat-rewind-note' }, HONESTY_COPY(agentName)),
            statusEl, listEl, restoreEl, errorEl);

        // Rewind first and Cancel last: with nothing focusable in the loading
        // body, createModal focuses the LAST action, and the safe one should
        // take the keyboard on a destructive dialog (thread-archive.js too).
        const modal = BossModOverlays.createModal({
            title: 'Rewind chat',
            subtitle: agentName,
            body: contentEl,
            actions: [
                { id: CONFIRM_ID, label: 'Rewind', tone: 'danger', keepOpen: true, onSelect: () => { void confirm(); } },
                { id: CANCEL_ID, label: 'Cancel', tone: 'quiet' },
            ],
            onClose: () => { closed = true; },
        });
        paintConfirm();

        /** The Rewind button's enabled, busy and label state. */
        function paintConfirm() {
            const button = modal.element.querySelector(`#${CONFIRM_ID}`);
            if (!button) return;  // the done state replaced the row
            button.disabled = busy || !selected;
            button.textContent = busy ? 'Rewinding…' : 'Rewind';
            if (busy) button.setAttribute('aria-busy', 'true');
            else button.removeAttribute('aria-busy');
        }

        /** Mark the chosen row and every newer one, and say what returns. */
        function paintSelection() {
            const at = rows.findIndex((entry) => entry.point === selected);
            rows.forEach((entry, index) => {
                const removed = at !== -1 && index <= at;
                entry.row.classList.toggle('is-removed', removed);
                entry.row.classList.toggle('is-selected', index === at);
                entry.mark.hidden = !removed;
                entry.input.checked = index === at;
            });
            restoreEl.hidden = !(selected && selected.author === 'human');
            paintConfirm();
        }

        function choose(point) {
            if (busy) return;
            selected = point;
            errorEl.textContent = '';
            paintSelection();
        }

        // Arrow keys move through native radios on their own; Enter is the
        // one key a radio group does not already give meaning to.
        function onListKeydown(event) {
            if (event.key !== 'Enter' || !selected || busy) return;
            event.preventDefault();
            void confirm();
        }

        function buildRow(point) {
            const who = point.author === 'human' ? 'You' : (point.authorName || agentName);
            const input = h('input', {
                type: 'radio',
                name: groupName,
                class: 'chat-rewind-radio',
                value: point.key,
                onchange: () => choose(point),
            });
            const mark = h('span', { class: 'chat-rewind-mark' }, REMOVED_MARK);
            mark.hidden = true;
            const when = BossModFormat.formatActivityTime(point.createdAt);
            const row = h('label', { class: 'chat-rewind-row' },
                input,
                h('span', { class: 'chat-rewind-text' },
                    h('span', { class: 'chat-rewind-meta' },
                        h('span', { class: 'chat-rewind-who' }, who),
                        when ? h('time', { class: 'chat-rewind-time', datetime: point.createdAt }, when) : null,
                        mark),
                    h('span', { class: 'chat-rewind-excerpt' }, excerpt(point))));
            return { point, input, row, mark };
        }

        /**
         * Show the rows, newest first.
         * @param {object[]} points  Messages, oldest first.
         * @returns {void}
         * @throws {Error} When `points` is not a list.
         */
        function setPoints(points) {
            if (!Array.isArray(points)) throw new Error('[chat-rewind-dialog] setPoints takes a list');
            if (closed) return;
            rows = points.slice().reverse().map(buildRow);
            clear(listEl);
            rows.forEach((entry) => listEl.append(entry.row));
            listEl.hidden = rows.length === 0;
            statusEl.textContent = rows.length ? '' : EMPTY_COPY;
            statusEl.hidden = rows.length > 0;
            const latestHuman = preselectLatestHuman
                ? rows.find((entry) => entry.point.author === 'human')
                : null;
            selected = latestHuman ? latestHuman.point : null;
            paintSelection();
            // The rows are what the operator came for: the keyboard moves
            // into the group (onto the preselected row, else the newest).
            const target = latestHuman || rows[0];
            if (target) target.input.focus();
        }

        /**
         * The rows could not be read. Rewind stays disabled.
         * @param {string} message
         * @returns {void}
         */
        function setLoadError(message) {
            if (closed) return;
            statusEl.hidden = true;
            listEl.hidden = true;
            errorEl.textContent = message || 'Could not load this conversation.';
        }

        /** Success that the operator still has to read: one Close. */
        function showDone(warning) {
            listEl.hidden = true;
            restoreEl.hidden = true;
            errorEl.textContent = '';
            statusEl.hidden = false;
            statusEl.textContent = warning;
            statusEl.classList.add('is-warning');
            modal.setActions([{ id: 'chat-rewind-done', label: 'Close', tone: 'quiet' }]);
            modal.element.querySelector('#chat-rewind-done').focus();
        }

        async function confirm() {
            if (busy || !selected || closed) return;
            busy = true;
            errorEl.textContent = '';
            rows.forEach((entry) => { entry.input.disabled = true; });
            paintConfirm();
            let result;
            try {
                result = await onConfirm(selected);
            } catch (err) {
                busy = false;
                rows.forEach((entry) => { entry.input.disabled = false; });
                if (closed) return;
                errorEl.textContent = (err && err.message) || 'Could not rewind this chat.';
                paintConfirm();
                return;
            }
            busy = false;
            if (closed) return;
            const warning = result && typeof result.warning === 'string' ? result.warning.trim() : '';
            if (warning) showDone(warning);
            else modal.close();
        }

        return { setPoints, setLoadError, close: () => modal.close() };
    }

    return { open, HONESTY_COPY, RESTORE_COPY, EMPTY_COPY };
})();
