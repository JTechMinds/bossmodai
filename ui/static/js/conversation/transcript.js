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
     * @param {object} deps.presence  Shared presence controller instance, owned
     *   by conversation.js and keyed `conversationId::agentId`.
     * @param {(m: object) => HTMLElement} deps.renderMessage  For kind 'message'.
     * @param {(m: object) => HTMLElement} deps.renderEventCard  For every other kind.
     * @param {(agentId: string) => string|null} deps.activitySince  ISO-8601
     *   start of the member's active turn, or null when the roster does not
     *   know of one. The transcript never reads the roster itself; this is the
     *   whole of what it needs from it (spec 12, carried items).
     * @returns {{ element: HTMLElement, setMessages: Function, append: Function,
     *             renderPresence: Function, setStatus: Function,
     *             isNearBottom: Function, scrollToBottom: Function,
     *             messageCount: Function }}
     * @throws {Error} When any dependency is missing. A transcript that cannot
     *   render half its message kinds must fail at construction, not paint a
     *   partial conversation.
     */
    function createTranscript(deps) {
        const presence = deps && deps.presence;
        const renderMessage = deps && deps.renderMessage;
        const renderEventCard = deps && deps.renderEventCard;
        const activitySince = deps && deps.activitySince;
        if (!presence) throw new Error('[transcript] deps.presence is required');
        if (typeof renderMessage !== 'function') {
            throw new Error('[transcript] deps.renderMessage is required');
        }
        if (typeof renderEventCard !== 'function') {
            throw new Error('[transcript] deps.renderEventCard is required');
        }
        if (typeof activitySince !== 'function') {
            throw new Error('[transcript] deps.activitySince is required');
        }

        const renderedKeys = new Set();
        let messages = 0;
        let statusEl = null;
        let statusKind = 'ready';
        let newCount = 0;

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
            return message.kind === 'message' ? renderMessage(message) : renderEventCard(message);
        }

        /**
         * Replace the whole list and land at the bottom. Nothing is preserved
         * across the swap, dedupe set included: `incoming` IS the transcript now.
         *
         * @param {object[]} incoming
         * @returns {void}
         */
        function setMessages(incoming) {
            clear(listEl);
            renderedKeys.clear();
            messages = 0;
            const rows = Array.isArray(incoming) ? incoming : [];
            for (const message of rows) {
                const key = messageKey(message);
                if (key) renderedKeys.add(key);
                listEl.append(nodeFor(message));
                messages += 1;
            }
            keepPresenceLast();
            resetNewCount();
            scrollToBottom();
        }

        /**
         * Append one message.
         *
         * @param {object} message
         * @returns {boolean} false when the key is already on screen. A message
         *   with an empty key ALWAYS appends and always returns true.
         */
        function append(message) {
            const key = messageKey(message);
            if (key && renderedKeys.has(key)) return false;
            const stick = isNearBottom();
            listEl.append(nodeFor(message));
            if (key) renderedKeys.add(key);
            messages += 1;
            // A message arriving proves the conversation is not empty; any
            // other status is the controller's to clear.
            if (statusKind === 'empty') setStatus('ready');
            keepPresenceLast();
            if (stick) scrollToBottom();
            else bumpNewCount();
            return true;
        }

        function keepPresenceLast() {
            scroller.append(presenceEl);
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
         * @param {{agentId: string, name: string}} member
         * @returns {string}
         */
        function presenceText(member) {
            const since = activitySince(member.agentId);
            const startedAt = since ? new Date(since).getTime() : NaN;
            if (!Number.isNaN(startedAt)) {
                const elapsedMs = Date.now() - startedAt;
                if (elapsedMs >= LONG_TURN_MS) {
                    const elapsed = BossModFormat.formatDuration(Math.floor(elapsedMs / 1000));
                    return `${member.name} is working · ${elapsed}`;
                }
            }
            return `${member.name} is thinking...`;
        }

        /**
         * Repaint the "someone is thinking" rows for one conversation.
         * @param {string} conversationId
         * @returns {void} An empty presence list clears the slot completely.
         */
        function renderPresence(conversationId) {
            const stick = isNearBottom();
            clear(presenceEl);
            for (const member of presence.list(conversationId)) {
                presenceEl.append(h('div', {
                    class: 'transcript-presence-row',
                    'data-agent-id': member.agentId,
                }, presenceText(member)));
            }
            keepPresenceLast();
            if (stick) scrollToBottom();
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
        };
    }

    return { createTranscript, messageKey };
})();
