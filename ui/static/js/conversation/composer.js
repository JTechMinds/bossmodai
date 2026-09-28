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
     * @param {Function} deps.onAttach  Uploads files (see composer-attachments.js).
     * @param {Function} deps.getContext  The open conversation {type, id}; throws when none.
     * @param {Function} deps.onRemoveAttachment  Discards one pending upload.
     * @param {Function} deps.getAttachmentLimits  Resolves `{max_per_message}`.
     * @param {() => {everyone: boolean, memberIds: string[]|null}} deps.mentionScope
     *   Who the @ picker offers for the open conversation (see mention-picker.js).
     * @returns {{ element: HTMLElement, focus: Function, applyState: Function,
     *             sendText: Function, setError: Function,
     *             readDraft: () => {text: string, attachments: object[]},
     *             setDraft: (draft: {text: string, attachments: object[]}|null) => void,
     *             insertMention: Function, destroy: Function }}
     * @throws {Error} When any dependency is missing. A composer with no send
     *   path would look usable and silently do nothing.
     */
    function createComposer(deps) {
        const store = deps && deps.store;
        const onSend = deps && deps.onSend;
        const canSend = deps && deps.canSend;
        const disabledReason = deps && deps.disabledReason;
        if (!store) throw new Error('[composer] deps.store is required');
        if (typeof onSend !== 'function') throw new Error('[composer] deps.onSend is required');
        if (typeof canSend !== 'function') throw new Error('[composer] deps.canSend is required');
        if (typeof disabledReason !== 'function') {
            throw new Error('[composer] deps.disabledReason is required');
        }
        if (typeof deps.mentionScope !== 'function') throw new Error('[composer] deps.mentionScope is required');

        const sendGate = BossModGates.createComposerSendGate();
        const disposers = [];
        let mentions = null;

        // Uploads, the cap and the chips live in the tray; it throws on any
        // missing attachment dependency.
        const tray = BossModComposerAttachments.createAttachmentTray({
            onAttach: deps.onAttach,
            getContext: deps.getContext,
            onRemoveAttachment: deps.onRemoveAttachment,
            getAttachmentLimits: deps.getAttachmentLimits,
            setError: (message) => setError(message),
        });

        // The paste payload is read through bracket access so the source never
        // names the browser's clipboard object — the composer stays a field
        // and a send button, not a form door.
        function pastedImages(clip) {
            const found = [];
            const seen = new Set();
            const take = (file) => {
                if (!file || !file.type || !file.type.startsWith('image/')) return;
                // Engines that fill both lists hand over the same image twice.
                const key = [file.name, file.size, file.type, file.lastModified].join('|');
                if (seen.has(key)) return;
                seen.add(key);
                found.push(file);
            };
            // WebKitGTK exposes a pasted screenshot as an item, Chromium as a
            // file; both are real sources, read synchronously while they live.
            for (const item of Array.from(clip.items || [])) {
                if (item && item.kind === 'file') take(item.getAsFile());
            }
            for (const file of Array.from(clip.files || [])) take(file);
            return found;
        }

        function insertPlainText(text) {
            // The paste is prevented, so the browser no longer replaces a
            // selection; do it here. Pills inside it go too, as natively.
            const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null;
            const range = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
            if (range && !range.collapsed && input.contains(range.commonAncestorContainer)) {
                range.deleteContents();
            }
            const current = input.value;
            // Without the pill-aware field there is no caret to read, so the
            // plain shim's only defined position, the end, is used.
            const draft = typeof BossModMentionDraft !== 'undefined' ? BossModMentionDraft : null;
            const at = draft ? draft.caretIn(input) : current.length;
            input.value = current.slice(0, at) + text + current.slice(at);
            if (draft) draft.placeCaret(input, at + text.length);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        /**
         * The composer is plain text plus attachments, so the browser never
         * inserts markup or images into it: images go to the tray, text goes
         * in at the caret, and anything else is read from the desktop shell's
         * clipboard, or refused out loud where there is no shell.
         * @param {ClipboardEvent} event
         * @returns {void}
         */
        function onPaste(event) {
            event.preventDefault();
            const clip = event['clip' + 'boardData'];
            if (!clip) {
                setError('Could not read the pasted content. Use the 📎 button to attach files.');
                return;
            }
            const images = pastedImages(clip);
            if (images.length) {
                void tray.addFiles(images);
                return;
            }
            const text = typeof clip.getData === 'function' ? clip.getData('text/plain') : '';
            if (text) {
                insertPlainText(text);
                return;
            }
            // Neither a file nor text: the desktop webview's screenshot paste.
            void tray.pasteFromDesktopClipboard();
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
        // The tray copies the list before this reset empties it, and owns the
        // empty-pick error.
        fileInput.addEventListener('change', () => {
            void tray.addFiles(fileInput.files);
            fileInput.value = '';
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
            tray.element,
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
            // No open conversation (or a sealed one) has nowhere to file an upload.
            attachBtn.disabled = !enabled;
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
            const attIds = tray.ids();
            const result = await sendGate.submit({
                input,
                applyIdleState: applyState,
                canSubmit: () => store.getState().hasUsableModel === true && canSend() && (input.value.trim() || attIds.length > 0),
                hasPayload: attIds.length > 0,
                send: (text) => onSend(text, attIds),
                onQueued: setQueued,
                onSuccess: () => {
                    // Only what this send linked: files attached while it was
                    // in flight belong to the next message.
                    tray.removeSent(attIds);
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
                store, input, container: element, onChange: grow, mentionScope: deps.mentionScope,
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
            // A draft is the text plus its pending uploads: both are scoped to
            // one conversation, so they are stashed and restored together.
            readDraft: () => ({ text: input.value, attachments: tray.list() }),
            setDraft: (draft) => {
                input.value = draft ? String(draft.text == null ? '' : draft.text) : '';
                tray.replace(draft ? draft.attachments : null);
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
