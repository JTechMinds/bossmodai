/**
 * BossMod AI — the bell's queue.
 *
 * Everything waiting on the operator, grouped by kind, with the actions the
 * SERVER described. The client never builds a resolution URL: that is the whole
 * point of the endpoint describing them (spec 5.2), and it is why a fifth need
 * kind will need no change here.
 *
 * The bell is the one guaranteed path and is never suppressible. The footer
 * toggle below turns off the composer bar only (spec 5.5).
 */
const BossModNeedsPopover = (() => {
    const { h, clear } = BossModDom;

    const EMPTY_COPY = 'Nothing needs you right now.';
    const BAR_TOGGLE_LABEL = 'Show these above the composer';
    const BAR_TOGGLE_HINT = 'The bell always lists everything, whatever this is set to.';
    const FOCUSABLE = 'button, input';

    /** Rendering order. Decisions the operator owes come before reports. */
    const KIND_ORDER = Object.freeze(['consent', 'approval', 'blocked', 'error']);
    const KIND_LABELS = Object.freeze({
        consent: 'Folder access',
        approval: 'Command approval',
        blocked: 'Blocked work',
        error: 'Errors',
    });

    /**
     * The dialog's accessible name. The badge is decorative, so a screen-reader
     * user gets the count from here or nowhere.
     *
     * @param {number} count
     * @returns {string}
     */
    function dialogLabel(count) {
        if (count === 0) return 'Nothing needs you';
        if (count === 1) return '1 thing needs you';
        return `${count} things need you`;
    }


    /**
     * Open the needs popover.
     *
     * @param {object} deps
     * @param {object} deps.store       Application store.
     * @param {object} deps.needs       From createNeedsStore; owns resolve().
     * @param {HTMLElement} deps.anchor The bell. Focus returns here on close.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @param {() => void} [deps.onClose]  Notified however the popover closed —
     *   Esc, a "Show me", or the bell again — so the bell can toggle rather
     *   than stack a second panel. Same contract as overlays.createModal.
     * @returns {{ close: () => void, element: HTMLElement }}
     * @throws {Error} When any dependency is missing. A bell that opened an
     *   inert panel would be worse than one that failed loudly.
     */
    function openPopover(deps) {
        const { store, needs, anchor, navigate, onClose } = deps || {};
        if (!store) throw new Error('[needs-popover] deps.store is required');
        if (!needs) throw new Error('[needs-popover] deps.needs is required');
        if (!anchor) throw new Error('[needs-popover] deps.anchor is required');
        if (typeof navigate !== 'function') throw new Error('[needs-popover] deps.navigate is required');

        const disposers = [];
        /** Need ids with a resolution in flight; their buttons stay disabled. */
        const inFlight = new Set();
        let refreshing = true;
        let closed = false;

        const heading = h('h2', { class: 'popover-title', tabindex: '-1' });
        const statusEl = h('p', { class: 'popover-status', role: 'status' });
        const errorEl = h('p', { class: 'popover-error', role: 'alert' });
        const listEl = h('div', { class: 'popover-list' });

        const barToggle = h('input', {
            type: 'checkbox',
            id: 'needs-bar-toggle',
            class: 'popover-toggle-input',
            onchange: () => { store.setState({ needsBarEnabled: barToggle.checked === true }); },
        });
        barToggle.checked = store.getState().needsBarEnabled === true;

        const element = h('div', {
            class: 'popover needs-popover',
            role: 'dialog',
            'aria-modal': 'false',
            id: 'needs-popover',
        },
            heading,
            statusEl,
            errorEl,
            listEl,
            h('div', { class: 'popover-footer' },
                h('label', { class: 'popover-toggle', for: 'needs-bar-toggle' },
                    barToggle,
                    h('span', {}, BAR_TOGGLE_LABEL)),
                h('p', { class: 'popover-footer-hint' }, BAR_TOGGLE_HINT)));

        // ─── Rendering ───

        function renderAction(need, action) {
            const button = h('button', {
                class: `popover-action ${action.tone}`,
                type: 'button',
                onclick: () => { void run(need, action); },
            }, action.label);
            button.disabled = inFlight.has(need.id);
            return button;
        }

        /**
         * Take the operator to where a need can be dealt with.
         *
         * The destination is `need.target`, built by the ONE mapping table in
         * need-shape.js. Nothing here branches on kind, which is what stops the
         * bell, the bar and the toast forming three opinions about where a
         * blocked task lives.
         *
         * @param {object} target  A NeedTarget.
         * @returns {void}
         */
        function goTo(target) {
            if (target.conversationId) {
                store.setState({
                    conversationId: target.conversationId,
                    conversationKind: target.conversationKind,
                });
            }
            // Chat is the one destination with an unsent draft, a transcript
            // cache and a caret to lose, and its target carries no params — so
            // arriving there is a store change, and a navigation only when the
            // operator is somewhere else. Board and Log carry the thing to show
            // IN their params, so they are navigated to either way.
            const alreadyThere = store.getState().place === target.place;
            // Closed before navigating, so focus ends on the new place's
            // heading rather than being pulled back here.
            close();
            if (alreadyThere && target.place === 'chat') return;
            navigate(target.place, target.params);
        }

        function renderNeed(need) {
            const showMe = need.target
                ? h('button', {
                    class: 'popover-show-me',
                    type: 'button',
                    onclick: () => goTo(need.target),
                }, 'Show me')
                : null;

            return h('article', { class: 'popover-need', 'data-need-id': need.id },
                h('p', { class: 'popover-need-title' }, need.title),
                h('p', { class: 'popover-need-sub' }, need.sub),
                h('p', { class: 'popover-need-time' },
                    BossModFormat.formatRelativeTime(need.createdAt)),
                need.error
                    ? h('p', { class: 'popover-need-error', role: 'alert' }, need.error)
                    : null,
                h('div', { class: 'popover-need-actions' },
                    need.actions.map((action) => renderAction(need, action)),
                    showMe));
        }

        function render() {
            const list = store.getState().needs;
            heading.textContent = dialogLabel(list.length);
            element.setAttribute('aria-label', dialogLabel(list.length));

            statusEl.textContent = refreshing ? 'Refreshing…' : '';
            errorEl.textContent = needs.getError();

            clear(listEl);
            if (list.length === 0) {
                // Not an error and not a blank panel: the empty state says so.
                listEl.append(h('p', { class: 'popover-empty' }, EMPTY_COPY));
                return;
            }
            KIND_ORDER.forEach((kind) => {
                // store.needs is already newest-first, so filtering preserves it.
                const group = list.filter((need) => need.kind === kind);
                if (group.length === 0) return;
                listEl.append(h('section', { class: 'popover-group' },
                    h('h3', { class: 'popover-group-title' },
                        `${KIND_LABELS[kind]} (${group.length})`),
                    group.map(renderNeed)));
            });
        }

        /**
         * Run one action, keeping the entry explained if it fails.
         *
         * @param {object} need
         * @param {object} action
         * @returns {Promise<void>}
         */
        async function run(need, action) {
            if (inFlight.has(need.id)) return;
            inFlight.add(need.id);
            render();
            try {
                await needs.resolve(need, action);
            } catch (err) {
                // needs.resolve already logged it and put the need back with
                // its message; re-rendering is what makes that visible.
                inFlight.delete(need.id);
                render();
                refocus();
                return;
            }
            inFlight.delete(need.id);
            render();
            refocus();
        }

        /** Keep keyboard focus inside the popover after a re-render. */
        function refocus() {
            if (closed) return;
            if (element.contains(document.activeElement)) return;
            heading.focus();
        }

        // ─── Keyboard ───

        function focusables() {
            return Array.from(element.querySelectorAll(FOCUSABLE))
                .filter((node) => node.disabled !== true);
        }

        function onKeydown(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                close();
                return;
            }
            if (event.key !== 'Tab') return;
            const nodes = focusables();
            if (nodes.length === 0) return;
            const index = nodes.indexOf(document.activeElement);
            event.preventDefault();
            const step = event.shiftKey ? -1 : 1;
            const next = index === -1
                ? (event.shiftKey ? nodes.length - 1 : 0)
                : (index + step + nodes.length) % nodes.length;
            nodes[next].focus();
        }

        function close() {
            if (closed) return;
            closed = true;
            document.removeEventListener('keydown', onKeydown);
            disposers.splice(0).forEach((off) => off());
            element.remove();
            if (anchor.focus) anchor.focus();
            if (onClose) onClose();
        }

        disposers.push(store.subscribe((s) => s.needs, () => { render(); refocus(); }));
        disposers.push(needs.subscribeError(() => { render(); }));

        render();
        document.addEventListener('keydown', onKeydown);
        document.body.append(element);
        heading.focus();

        // Opening re-reads the queue, so the bell is authoritative rather than
        // however stale the last broadcast left it.
        void needs.refresh().then(() => {
            if (closed) return;
            refreshing = false;
            render();
        });

        return { close, element };
    }

    return { openPopover };
})();
