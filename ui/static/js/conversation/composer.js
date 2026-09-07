/**
 * BossMod AI — the conversation composer.
 *
 * One rule dominates: the draft is never cleared before the server
 * acknowledges. A composer that empties itself on a failed request destroys
 * work the operator cannot get back, so clearing is the send gate's job and
 * happens only after `onSend` resolves.
 *
 * The composer is built once per Chat mount and reused across conversations.
 * Rebuilding it on every switch would kill the draft and the caret, which is
 * why `open()` reconfigures it instead of replacing it.
 */
const BossModComposer = (() => {
    const { h } = BossModDom;

    const READY_PLACEHOLDER = 'Type a message... (Shift+Enter for new line)';
    const NO_MODEL_PLACEHOLDER = 'Connect a model in Settings to send messages';
    const NO_MODEL_TITLE = 'Connect a model in Settings to send';
    const SEND_TITLE = 'Send message';
    const ASSIGN_TITLE = 'Assign a task';
    const INPUT_ID = 'conversation-composer-input';
    /** The textarea grows with its content, then scrolls (spec 4.4). */
    const MAX_HEIGHT_PX = 160;

    /**
     * Build the composer.
     *
     * @param {object} deps
     * @param {object} deps.store  Read for `hasUsableModel`; subscribed for changes.
     * @param {(text: string) => Promise<void>} deps.onSend  MUST reject on
     *   failure — a resolved promise is read as an acknowledgement and clears
     *   the draft.
     * @param {() => boolean} deps.canSend  Source-level gate, e.g. an archived
     *   thread.
     * @param {() => string} deps.disabledReason  Shown as the placeholder when
     *   `canSend()` is false; '' otherwise.
     * @param {() => void} deps.onAssign  Opens the assign sheet (spec 4.4).
     *   Injected rather than reached for: the composer must not know which
     *   module owns the form, which is what keeps there being exactly one.
     * @returns {{ element: HTMLElement, focus: Function, applyState: Function,
     *             setError: Function, readDraft: Function, setDraft: Function,
     *             destroy: Function }}
     * @throws {Error} When any dependency is missing. A composer with no send
     *   path would look usable and silently do nothing.
     */
    function createComposer(deps) {
        const store = deps && deps.store;
        const onSend = deps && deps.onSend;
        const canSend = deps && deps.canSend;
        const disabledReason = deps && deps.disabledReason;
        const onAssign = deps && deps.onAssign;
        if (!store) throw new Error('[composer] deps.store is required');
        if (typeof onSend !== 'function') throw new Error('[composer] deps.onSend is required');
        if (typeof canSend !== 'function') throw new Error('[composer] deps.canSend is required');
        if (typeof disabledReason !== 'function') {
            throw new Error('[composer] deps.disabledReason is required');
        }
        // Required, not optional: a clipboard button that rendered and did
        // nothing would be worse than one that is absent, and this composer has
        // exactly one builder.
        if (typeof onAssign !== 'function') {
            throw new Error('[composer] deps.onAssign is required');
        }

        const sendGate = BossModGates.createComposerSendGate();
        const disposers = [];

        function grow() {
            input.style.height = 'auto';
            input.style.height = `${Math.min(input.scrollHeight, MAX_HEIGHT_PX)}px`;
        }

        function onKeyDown(event) {
            if (event.key !== 'Enter' || event.shiftKey) return;
            event.preventDefault();
            void submit();
        }

        function onSendClick() {
            void submit();
        }

        function onAssignClick() {
            onAssign();
        }

        const input = h('textarea', {
            class: 'composer-input',
            id: INPUT_ID,
            rows: '1',
            placeholder: READY_PLACEHOLDER,
            oninput: grow,
            onkeydown: onKeyDown,
        });
        // Icon-only send, so the control is named twice over: for the label
        // association and for the button itself.
        const label = h('label', { class: 'visually-hidden', for: INPUT_ID }, 'Message');
        const sendBtn = h('button', {
            class: 'composer-send',
            type: 'button',
            'aria-label': SEND_TITLE,
            title: SEND_TITLE,
            onclick: onSendClick,
        }, h('i', { 'data-lucide': 'send', 'aria-hidden': 'true' }));
        // Icon-only, so it carries its own name; the icon is decorative.
        const assignBtn = h('button', {
            class: 'composer-assign',
            type: 'button',
            'aria-label': ASSIGN_TITLE,
            title: ASSIGN_TITLE,
            onclick: onAssignClick,
        }, h('i', { 'data-lucide': 'clipboard', 'aria-hidden': 'true' }));
        const errorEl = h('p', { class: 'composer-error hidden', role: 'alert' });

        const element = h('div', { class: 'composer' },
            label,
            h('div', { class: 'composer-row' }, input, assignBtn, sendBtn),
            errorEl);

        /**
         * Recompute enablement, placeholder, and title from current state.
         *
         * Called from the send gate's `finally`, so a failed request can never
         * strand the composer disabled.
         *
         * @returns {void}
         */
        function applyState() {
            const hasUsableModel = store.getState().hasUsableModel === true;
            const allowed = canSend();
            const enabled = hasUsableModel && allowed && !sendGate.busy();
            sendBtn.disabled = !enabled;
            // Same gates as Send: with no model connected, or in a conversation
            // that cannot be posted to, a new task could not be worked either.
            assignBtn.disabled = !enabled;
            input.disabled = !enabled;
            input.setAttribute('aria-disabled', enabled ? 'false' : 'true');
            sendBtn.setAttribute('title', hasUsableModel ? SEND_TITLE : NO_MODEL_TITLE);
            if (!hasUsableModel) input.placeholder = NO_MODEL_PLACEHOLDER;
            else if (!allowed) input.placeholder = disabledReason();
            else input.placeholder = READY_PLACEHOLDER;
        }

        /**
         * Show or clear the inline error line.
         * @param {string} message  '' hides it.
         * @returns {void}
         */
        function setError(message) {
            BossModGates.setComposerError(errorEl, message);
        }

        /**
         * Send the current draft, if there is one and the gates allow it.
         * @returns {Promise<void>} Resolves once the gate has settled; never rejects.
         */
        async function submit() {
            await sendGate.submit({
                input,
                sendBtn,
                applyIdleState: applyState,
                canSubmit: () => store.getState().hasUsableModel === true && canSend(),
                send: (text) => onSend(text),
                onSuccess: () => {
                    setError('');
                    grow();
                },
                onError: (err) => setError((err && err.message) || 'Failed to send.'),
            });
        }

        disposers.push(store.subscribe((s) => s.hasUsableModel, applyState));

        applyState();

        return {
            element,
            focus: () => input.focus(),
            applyState,
            setError,
            readDraft: () => input.value,
            setDraft: (text) => {
                input.value = String(text == null ? '' : text);
                grow();
            },
            /**
             * Drop every subscription and listener this composer created.
             * @returns {void}
             */
            destroy() {
                input.removeEventListener('input', grow);
                input.removeEventListener('keydown', onKeyDown);
                sendBtn.removeEventListener('click', onSendClick);
                assignBtn.removeEventListener('click', onAssignClick);
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createComposer };
})();
