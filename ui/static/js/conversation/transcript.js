/**
 * BossMod AI — the message list, and the only module allowed to touch scroll.
 *
 * Two rules shape everything here. A message whose key is already on screen is
 * dropped, so a WebSocket echo of a message painted from the REST load does
 * not double-render — but a message with NO key always appends, because
 * collapsing keyless messages onto one node would silently eat transcript
 * content. And an operator who has scrolled up is never yanked back down: the
 * message lands silently and a counted, announced affordance offers the jump.
 *
 * The list is role="log" and deliberately carries no aria-live. Agents talk
 * continuously; narrating every message forever with no way to stop is worse
 * for screen-reader users than announcing nothing (spec 4.2).
 */
const BossModTranscript = (() => {
    const { h, clear } = BossModDom;

    /**
     * How close to the bottom still counts as "reading the newest" (spec 4.2).
     * The rule and its threshold live in core/dom.js so the Log, which watches
     * the opposite edge, cannot end up with a second opinion about it.
     */
    const { isNearEdge } = BossModDom;

    /**
     * How long an active turn must run before the presence row trades
     * "is thinking..." for a duration. One minute, because that is the exact
     * point `formatDuration` starts returning a number instead of `< 1m` —
     * below it the switch would cost the operator information rather than add
     * any.
     */
    const LONG_TURN_MS = 60 * 1000;

    /** The duration only moves in whole minutes, so the clock repaints on their boundaries. */
    const MINUTE_MS = 60 * 1000;

    /**
     * A message's dedupe key, normalised. `''` means the backend gave none.
     * @param {object} message
     * @returns {string}
     */
    function messageKey(message) {
        return String((message && message.key) || '').trim();
    }

    /**
     * Build the transcript view.
     *
     * @param {object} deps
     * @param {(m: object) => HTMLElement} deps.renderMessage  For kind 'message'.
     * @param {(m: object) => HTMLElement} deps.renderEventCard  For every other kind.
     * @returns {{ element: HTMLElement, setMessages: Function, append: Function,
     *             renderPresence: Function, setStatus: Function,
     *             isNearBottom: Function, scrollToBottom: Function,
     *             messageCount: Function, destroy: Function }}
     *   `destroy` stops the presence clock; the owner calls it when it goes.
     * @throws {Error} When any dependency is missing. A transcript that cannot
     *   render half its message kinds must fail at construction, not paint a
     *   partial conversation.
     */
    function createTranscript(deps) {
        const renderMessage = deps && deps.renderMessage;
        const renderEventCard = deps && deps.renderEventCard;
        if (typeof renderMessage !== 'function') {
            throw new Error('[transcript] deps.renderMessage is required');
        }
        if (typeof renderEventCard !== 'function') {
            throw new Error('[transcript] deps.renderEventCard is required');
        }

        const renderedKeys = new Set();
        let messages = 0;
        let statusEl = null;
        let statusKind = 'ready';
        let newCount = 0;
        /** The pending repaint that moves a long turn's duration on; null when none is due. */
        let presenceTimer = null;

        const listEl = h('div', { class: 'transcript-list' });
        const presenceEl = h('div', { class: 'transcript-presence' });
        const scroller = h('div', {
            class: 'transcript',
            role: 'log',
            'data-transcript': true,
        }, listEl, presenceEl);

        const jumpBtn = h('button', {
            class: 'transcript-jump',
            type: 'button',
            'aria-label': 'Jump to the newest message',
            onclick: () => { scrollToBottom(); resetNewCount(); },
        });
        jumpBtn.hidden = true;
        // Outside the scroller so it does not travel with the content, and its
        // own live region so the COUNT is announced — the list never is.
        const jumpLive = h('div', { class: 'transcript-jump-live', 'aria-live': 'polite' }, jumpBtn);

        const element = h('div', { class: 'transcript-wrap' }, scroller, jumpLive);

        /** Is the operator already looking at the newest message? @returns {boolean} */
        function isNearBottom() {
            return isNearEdge(scroller, 'bottom');
        }

        /** Pin the view to the newest message. @returns {void} */
        function scrollToBottom() {
            scroller.scrollTop = scroller.scrollHeight;
        }

        function resetNewCount() {
            newCount = 0;
            jumpBtn.textContent = '';
            jumpBtn.hidden = true;
        }

        function bumpNewCount() {
            newCount += 1;
            jumpBtn.textContent = newCount === 1 ? '1 new message' : `${newCount} new messages`;
            jumpBtn.hidden = false;
        }

        function nodeFor(message) {
            const node = message.kind === 'message' ? renderMessage(message) : renderEventCard(message);
            const key = messageKey(message);
            if (key && !node.getAttribute('data-message-key')) node.setAttribute('data-message-key', key);
            return node;
        }

        function removeByKey(key) {
            const node = key ? listEl.querySelector(`[data-message-key="${key}"]`) : null;
            if (!node) return false;
            const group = node.parentNode && node.parentNode.closest
                ? node.parentNode.closest('.note-group') : null;
            // A grouped note leaves through its group, which may dissolve into
            // plain notes (they keep their keys, so renderedKeys is unaffected).
            if (group) BossModNoteGroup.removeFromGroup(group, key);
            else node.remove();
            renderedKeys.delete(key);
            messages = Math.max(0, messages - 1);
            return true;
        }

        /** Replace the list. `incoming` IS the transcript now. */
        function setMessages(incoming) {
            clear(listEl);
            renderedKeys.clear();
            messages = 0;
            const rows = Array.isArray(incoming) ? incoming : [];
            // Consecutive action receipts are buffered so a long run can be
            // wrapped in one collapsible group instead of flooding the log.
            let buffer = [];
            const flushBuffer = () => {
                if (buffer.length >= BossModNoteGroup.NOTE_GROUP_THRESHOLD) {
                    listEl.append(BossModNoteGroup.buildNoteGroup(buffer));
                } else {
                    for (const entry of buffer) listEl.append(entry.node);
                }
                buffer = [];
            };
            for (const message of rows) {
                const key = messageKey(message);
                if (key) renderedKeys.add(key);
                const node = nodeFor(message);
                messages += 1;
                if (BossModNoteGroup.isGroupable(node)) {
                    buffer.push({ node, key });
                } else {
                    flushBuffer();
                    listEl.append(node);
                }
            }
            flushBuffer();
            keepPresenceLast();
            resetNewCount();
            scrollToBottom();
        }

        /**
         * Append, or replace/remove a live queue-visibility line by key.
         * @returns {boolean} false when a non-live key is already on screen.
         */
        function append(message) {
            const key = messageKey(message);
            if (message.cleared && key) return removeByKey(key);
            if (key && renderedKeys.has(key) && message.live) {
                const node = listEl.querySelector(`[data-message-key="${key}"]`);
                if (node && typeof node.replaceWith === 'function') {
                    node.replaceWith(nodeFor(message));
                    return true;
                }
                if (node) removeByKey(key);
            }
            if (key && renderedKeys.has(key)) return false;
            const stick = isNearBottom();
            const node = nodeFor(message);
            // A run of actions is one thing to catch up on, so only its first
            // note counts toward "N new messages" — unless nothing is counted
            // yet (the head arrived while at the bottom), so a run always shows.
            const tail = listEl.children[listEl.children.length - 1];
            const continuesRun = BossModNoteGroup.isGroupable(node)
                && Boolean(tail && (BossModNoteGroup.isGroupable(tail)
                    || tail.classList.contains('note-group')));
            listEl.append(node);
            if (key) renderedKeys.add(key);
            messages += 1;
            groupAppended(node, key);
            if (statusKind === 'empty') setStatus('ready');
            keepPresenceLast();
            if (stick) scrollToBottom();
            else if (!continuesRun || newCount === 0) bumpNewCount();
            return true;
        }

        /**
         * Fold a just-appended receipt into the group before it, or promote the
         * trailing run of receipts into a new group once it is long enough.
         */
        function groupAppended(node, key) {
            if (!BossModNoteGroup.isGroupable(node)) return;
            const kids = listEl.children;
            const prev = kids[kids.length - 2];
            if (prev && prev.classList.contains('note-group')) {
                BossModNoteGroup.extendGroup(prev, { node, key });
                return;
            }
            const run = [];
            for (let i = kids.length - 1; i >= 0 && BossModNoteGroup.isGroupable(kids[i]); i -= 1) {
                run.unshift(kids[i]);
            }
            if (run.length >= BossModNoteGroup.NOTE_GROUP_THRESHOLD) {
                BossModNoteGroup.promoteToGroup(listEl, run);
            }
        }

        function keepPresenceLast() {
            scroller.append(presenceEl);
        }

        /**
         * How long a thinking row's turn has run, from its `since`, clamped at
         * 0. A `since` that does not parse breaks the presence contract (the
         * dispatcher stamps every thinking turn), so it is reported on the
         * console and the row keeps its copy without a duration.
         *
         * @param {{since: string|null}} row  A thinking row.
         * @returns {number|null} Milliseconds, or null when unknown.
         */
        function elapsedMsOf(row) {
            const startedAt = Date.parse(row.since);
            if (Number.isNaN(startedAt)) {
                console.error('[transcript] a thinking presence row has no parseable since', row);
                return null;
            }
            return Math.max(0, Date.now() - startedAt);
        }

        /**
         * What one presence row says.
         *
         * Under the threshold the copy is unchanged, because that is the
         * honest reading: `formatDuration` says `< 1m` below a minute, which
         * tells the operator nothing `is thinking...` did not already. Past it
         * the turn is long enough to be worth a number, so the row switches to
         * the duration (spec 12, carried items — this is where the retired
         * `progress` card's information went).
         *
         * @param {{name: string, phase: string, ahead: number}} row
         * @param {number|null} elapsedMs  A thinking row's run time; null when
         *   unknown or queued.
         * @returns {string}
         */
        function presenceText(row, elapsedMs) {
            if (row.phase === 'queued') {
                const ahead = Math.max(0, Number(row.ahead) || 0);
                return `Queued (${ahead} ahead)`;
            }
            if (elapsedMs !== null && elapsedMs >= LONG_TURN_MS) {
                const elapsed = BossModFormat.formatDuration(Math.floor(elapsedMs / 1000));
                return `${row.name} is working · ${elapsed}`;
            }
            return `${row.name} is thinking...`;
        }

        function clearPresenceTimer() {
            if (presenceTimer === null) return;
            clearTimeout(presenceTimer);
            presenceTimer = null;
        }

        /**
         * Repaint the "someone is thinking" rows, and keep their durations
         * moving: while any thinking row is shown, the same rows repaint at the
         * soonest minute boundary across them, which also makes the first
         * switch at one minute. A new call replaces the pending repaint.
         *
         * @param {object[]} rows  The presence rows to paint (core/presence.js
         *   shape), already selected for this conversation.
         * @returns {void} An empty list clears the slot completely.
         */
        function renderPresence(rows) {
            const stick = isNearBottom();
            clear(presenceEl);
            let nextRepaintMs = null;
            for (const row of rows) {
                const elapsedMs = row.phase === 'thinking' ? elapsedMsOf(row) : null;
                presenceEl.append(h('div', {
                    class: 'transcript-presence-row',
                    'data-agent-id': row.agentId,
                }, presenceText(row, elapsedMs)));
                if (elapsedMs !== null) {
                    const untilBoundary = MINUTE_MS - (elapsedMs % MINUTE_MS);
                    nextRepaintMs = nextRepaintMs === null ? untilBoundary : Math.min(nextRepaintMs, untilBoundary);
                }
            }
            keepPresenceLast();
            if (stick) scrollToBottom();
            clearPresenceTimer();
            if (nextRepaintMs !== null) presenceTimer = setTimeout(() => renderPresence(rows), nextRepaintMs);
        }

        /**
         * Show exactly one of the four surface states.
         * @param {'loading'|'empty'|'error'|'ready'} status
         * @param {object} [payload]  `{title, hint}` for empty, `{message,
         *   onRetry}` for error.
         * @returns {void}
         * @throws {Error} On an unknown status — a typo must not blank the view.
         */
        function setStatus(status, payload) {
            if (statusEl) {
                statusEl.remove();
                statusEl = null;
            }
            statusKind = status;
            if (status === 'ready') return;
            const options = payload || {};
            if (status === 'loading') {
                statusEl = h('div', { class: 'transcript-status is-loading', 'data-status': 'loading' },
                    h('span', { class: 'visually-hidden' }, 'Loading the conversation'),
                    h('div', { class: 'transcript-skeleton', 'aria-hidden': 'true' }),
                    h('div', { class: 'transcript-skeleton', 'aria-hidden': 'true' }),
                    h('div', { class: 'transcript-skeleton', 'aria-hidden': 'true' }));
            } else if (status === 'empty') {
                // The one status with an identity and controls rather than just
                // words; conversation/empty-state.js owns it.
                statusEl = BossModEmptyState.render(options);
            } else if (status === 'error') {
                statusEl = h('div', {
                    class: 'transcript-status is-error',
                    'data-status': 'error',
                    role: 'alert',
                },
                    h('p', { class: 'transcript-status-title' },
                        options.message || 'This conversation could not be loaded.'),
                    typeof options.onRetry === 'function'
                        ? h('button', {
                            class: 'transcript-retry',
                            type: 'button',
                            onclick: () => options.onRetry(),
                        }, 'Try again')
                        : null);
            } else {
                statusKind = 'ready';
                throw new Error(`[transcript] unknown status "${status}"`);
            }
            // The status belongs above the messages it is describing, and the
            // presence rows stay last.
            scroller.replaceChildren(statusEl, listEl, presenceEl);
        }

        return {
            element,
            setMessages,
            append,
            renderPresence,
            setStatus,
            isNearBottom,
            scrollToBottom,
            messageCount: () => messages,
            destroy: clearPresenceTimer,
        };
    }

    return { createTranscript, messageKey };
})();
