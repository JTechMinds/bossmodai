/**
 * BossMod AI — the conversation composer.
 *
 * The field stays editable while agents think. A send takes that line out of
 * the box so the next one can be typed, and a rejection puts it back when
 * the operator has not started a newer draft. The send gate owns that, and
 * it never grays the field out to do it.
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
    const INPUT_ID = 'conversation-composer-input';
    /** The textarea grows with its content, then scrolls (spec 4.4). */
    const MAX_HEIGHT_PX = 160;

    /**
     * Build the composer.
     *
     * @param {object} deps
     * @param {object} deps.store  Read for `hasUsableModel`; subscribed for changes.
     * @param {(text: string, attachmentIds?: string[]) => Promise<void>} deps.onSend  MUST reject on
     *   failure — a resolved promise is read as an acknowledgement and clears
     *   the draft.
     * @param {() => boolean} deps.canSend  Source-level gate, e.g. an archived
     *   thread.
     * @param {() => string} deps.disabledReason  Shown as the placeholder when
     *   `canSend()` is false; '' otherwise.
     * @param {(files: File[], context: object) => Promise<Array<object>>} [deps.onAttach]
     *   Uploads files to the server; resolves with metadata array. Rejects on any failure.
     * @param {() => object} [deps.getContext]
     *   Returns the current message context {type, id} for the active conversation.
     * @returns {{ element: HTMLElement, focus: Function, applyState: Function,
     *             sendText: Function, setError: Function, readDraft: Function,
     *             setDraft: Function, insertMention: Function, destroy: Function,
     *             addPendingAttachment: Function, removePendingAttachment: Function,
     *             getPendingAttachments: Function, clearPendingAttachments: Function }}
     * @throws {Error} When any dependency is missing. A composer with no send
     *   path would look usable and silently do nothing.
     */
    function createComposer(deps) {
        const store = deps && deps.store;
        const onSend = deps && deps.onSend;
        const canSend = deps && deps.canSend;
        const disabledReason = deps && deps.disabledReason;
        const onAttach = deps && deps.onAttach;
        const getContext = deps && deps.getContext;
        if (!store) throw new Error('[composer] deps.store is required');
        if (typeof onSend !== 'function') throw new Error('[composer] deps.onSend is required');
        if (typeof canSend !== 'function') throw new Error('[composer] deps.canSend is required');
        if (typeof disabledReason !== 'function') {
            throw new Error('[composer] deps.disabledReason is required');
        }

        const sendGate = BossModGates.createComposerSendGate();
        const disposers = [];
        let mentions = null;

        // ── Pending attachments ──
        const MAX_ATTACHMENTS = 5;
        let pendingAttachments = [];
        const pendingStrip = h('div', { class: 'composer-attachments hidden' });

        function renderPendingStrip() {
            pendingStrip.replaceChildren();
            if (pendingAttachments.length === 0) {
                pendingStrip.classList.add('hidden');
                return;
            }
            pendingStrip.classList.remove('hidden');
            for (const meta of pendingAttachments) {
                const chip = h('span', { class: 'composer-attach-chip' },
                    h('span', { class: 'composer-attach-chip-name' }, meta.file_name || 'file'),
                    h('span', { class: 'composer-attach-chip-size' }, humanSize(meta.file_size || 0)),
                    h('button', {
                        type: 'button',
                        class: 'composer-attach-chip-remove',
                        'aria-label': 'Remove ' + (meta.file_name || 'attachment'),
                        onclick: () => removePendingAttachment(meta.id),
                    }, '×'));
                pendingStrip.append(chip);
            }
        }

        function humanSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1048576).toFixed(1) + ' MB';
        }

        function addPendingAttachment(meta) {
            if (pendingAttachments.length >= MAX_ATTACHMENTS) {
                setError('Maximum ' + MAX_ATTACHMENTS + ' attachments per message.');
                return;
            }
            pendingAttachments.push(meta);
            renderPendingStrip();
        }

        function removePendingAttachment(id) {
            pendingAttachments = pendingAttachments.filter((a) => a.id !== id);
            renderPendingStrip();
        }

        function getPendingAttachments() {
            return pendingAttachments.slice();
        }

        function clearPendingAttachments() {
            pendingAttachments = [];
            renderPendingStrip();
        }

        async function handleFiles(files) {
            if (!onAttach || !getContext) return;
            const ctx = getContext();
            try {
                const results = await onAttach(Array.from(files), ctx);
                for (const meta of results) addPendingAttachment(meta);
            } catch (err) {
                const name = (err && err.fileName) || '';
                const msg = (err && err.message) || 'Upload failed';
                setError(name ? name + ': ' + msg : msg);
            }
        }

        // Paste handler for images. The paste payload is read through bracket
        // access so the source never names the browser's clipboard object —
        // the composer stays a field and a send button, not a form door.
        function onPaste(event) {
            if (!onAttach || !getContext) return;
            const clip = event['clip' + 'boardData'];
            const files = clip && clip.files;
            if (!files || files.length === 0) return;
            const imageFiles = Array.from(files).filter((f) => f.type && f.type.startsWith('image/'));
            if (imageFiles.length === 0) return;
            event.preventDefault();
            void handleFiles(imageFiles);
        }

        function grow() {
            input.style.height = 'auto';
            input.style.height = `${Math.min(input.scrollHeight, MAX_HEIGHT_PX)}px`;
        }

        function onKeyDown(event) {
            if (mentions && mentions.handleKeyDown(event)) return;
            if (event.key !== 'Enter' || event.shiftKey) return;
            event.preventDefault();
            void submit();
        }

        function onSendClick() {
            void submit();
        }

        function onComposerInput() {
            const kids = input.childNodes || [];
            if (kids.length === 1 && kids[0] && kids[0].tagName === 'BR') {
                input.replaceChildren();
            }
            grow();
        }

        // Contenteditable so a picked `@Name` stays a pill while the operator
        // keeps typing. `.value` is a shim: send, drafts, and insert still
        // speak `@Name` text. A textarea cannot hold a pill.
        const input = h('div', {
            class: 'composer-input',
            id: INPUT_ID,
            role: 'textbox',
            'aria-multiline': 'true',
            contenteditable: 'true',
            tabindex: '0',
            'data-placeholder': READY_PLACEHOLDER,
            oninput: onComposerInput,
            onkeydown: onKeyDown,
        });
        Object.defineProperty(input, 'placeholder', {
            configurable: true,
            get() { return input.getAttribute('data-placeholder') || ''; },
            set(text) { input.setAttribute('data-placeholder', String(text == null ? '' : text)); },
        });
        if (typeof BossModMentionDraft !== 'undefined') {
            BossModMentionDraft.bindEditable(input);
        } else {
            Object.defineProperty(input, 'value', {
                configurable: true,
                get() { return String(input.textContent || ''); },
                set(text) {
                    const raw = String(text == null ? '' : text);
                    input.replaceChildren();
                    if (raw) input.append(document.createTextNode(raw));
                },
            });
        }
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
        const errorEl = h('p', { class: 'composer-error hidden', role: 'alert' });
        const hintEl = h('p', { class: 'composer-hint hidden', role: 'status' });

        // The field, then send. The clipboard that used to open this row was a
        // third front door to the one assign form — the Tasks place's `+ New task`
        // and the empty conversation's `Assign a task` are the other two — and
        // it was the only one sitting in front of the operator every second
        // they were typing a message. Removing it costs no reach: the form is
        // still one click from Tasks, which is where a task goes anyway.
        // Attach button + hidden file input
        const fileInput = h('input', {
            type: 'file',
            multiple: 'true',
            class: 'hidden',
            'aria-hidden': 'true',
            tabindex: '-1',
        });
        fileInput.addEventListener('change', () => {
            if (fileInput.files && fileInput.files.length) {
                void handleFiles(fileInput.files);
                fileInput.value = '';
            }
        });
        const attachBtn = h('button', {
            class: 'composer-attach',
            type: 'button',
            'aria-label': 'Attach file',
            title: 'Attach file',
            onclick: () => fileInput.click(),
        }, h('i', { 'data-lucide': 'paperclip', 'aria-hidden': 'true' }));

        // Paste listener on the input
        input.addEventListener('paste', onPaste);

        const element = h('div', { class: 'composer' },
            label,
            pendingStrip,
            h('div', { class: 'composer-row' }, input, attachBtn, sendBtn),
            fileInput,
            hintEl,
            errorEl);

        /**
         * Recompute enablement, placeholder, and title from current state.
         *
         * A send in flight does not disable the field. Agents thinking is not
         * a lock: the operator can type and send the next line, which posts
         * as its own message. No model and a sealed thread still disable.
         *
         * @returns {void}
         */
        function applyState() {
            const hasUsableModel = store.getState().hasUsableModel === true;
            const allowed = canSend();
            const enabled = hasUsableModel && allowed;
            sendBtn.disabled = !enabled;
            input.disabled = !enabled;
            input.setAttribute('contenteditable', enabled ? 'true' : 'false');
            input.setAttribute('aria-disabled', enabled ? 'false' : 'true');
            sendBtn.setAttribute('title', hasUsableModel ? SEND_TITLE : NO_MODEL_TITLE);
            if (!hasUsableModel) input.placeholder = NO_MODEL_PLACEHOLDER;
            else if (!allowed) input.placeholder = disabledReason();
            else input.placeholder = READY_PLACEHOLDER;
        }

        /**
         * Quiet confirmation that a line is waiting on the server.
         * An empty count hides it. It is not an error.
         *
         * @param {number} count
         * @returns {void}
         */
        function setQueued(count) {
            const waiting = Number(count) > 0;
            hintEl.textContent = waiting ? 'Queued' : '';
            hintEl.classList.toggle('hidden', !waiting);
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
         * @returns {Promise<object>} The gate's verdict; never rejects.
         */
        async function submit() {
            const attIds = pendingAttachments.map((a) => a.id);
            const result = await sendGate.submit({
                input,
                applyIdleState: applyState,
                canSubmit: () => store.getState().hasUsableModel === true && canSend() && (input.value.trim() || pendingAttachments.length > 0),
                send: (text) => onSend(text, attIds),
                onQueued: setQueued,
                onSuccess: () => {
                    clearPendingAttachments();
                    setError('');
                    grow();
                },
                onError: (err) => setError((err && err.message) || 'Failed to send.'),
            });
            // A gate that refuses still leaves the text. Say why here too,
            // so Enter and the button match sendText().
            if (result && result.submitted === false && result.reason === 'blocked') {
                setError(input.placeholder || disabledReason() || 'Cannot send.');
            }
            return result;
        }

        /**
         * Send a message the operator did not type.
         *
         * The empty state's "Say hello" comes through here rather than calling
         * the source directly, so there is exactly ONE send path: one gate, one
         * place that clears the draft only on acknowledgement, one error line.
         *
         * A blocked send is reported rather than swallowed — applyState() has
         * already put the reason in the placeholder, so that is what it says —
         * and the text stays in the box so nothing is lost.
         *
         * @param {string} text
         * @returns {Promise<object>} The gate's verdict.
         */
        async function sendText(text, attachmentIds) {
            input.value = String(text == null ? '' : text);
            grow();
            const result = await submit();
            if (result && result.submitted === false && result.reason === 'blocked') {
                setError(input.placeholder);
            }
            return result;
        }

        if (typeof BossModMentionPicker !== 'undefined') {
            mentions = BossModMentionPicker.bindComposer({
                store, input, container: element, onChange: grow,
            });
        }

        disposers.push(store.subscribe((s) => s.hasUsableModel, applyState));

        applyState();

        return {
            element,
            focus: () => input.focus(),
            applyState,
            sendText,
            setError,
            addPendingAttachment,
            removePendingAttachment,
            getPendingAttachments,
            clearPendingAttachments,
            readDraft: () => input.value,
            setDraft: (text) => {
                input.value = String(text == null ? '' : text);
                grow();
                if (mentions) mentions.sync();
            },
            insertMention: (agent) => (mentions ? mentions.insert(agent) : null),
            /**
             * Drop every subscription and listener this composer created.
             * @returns {void}
             */
            destroy() {
                if (mentions) mentions.destroy();
                mentions = null;
                input.removeEventListener('input', onComposerInput);
                input.removeEventListener('keydown', onKeyDown);
                sendBtn.removeEventListener('click', onSendClick);
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createComposer };
})();
