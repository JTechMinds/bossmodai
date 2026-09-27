/**
 * BossMod AI — the composer's pending-attachment tray.
 *
 * Upload, the per-message cap, and the chip lifecycle, split from the
 * composer at the seam between "files waiting to be sent" and text entry
 * plus send gating. The composer keeps the attach button, the file input
 * and the paste handler, and hands every picked file to `addFiles`.
 *
 * Pending uploads are scoped to one conversation on the server, so the
 * conversation stashes them with its text draft (`list()` / `replace()`).
 * A draft the conversation forgets drops its uploads from view only: the
 * server rows stay pending and the app-start sweep
 * (`bossmod.attach.pending_ttl_hours`) removes them.
 */
const BossModComposerAttachments = (() => {
    const { h } = BossModDom;

    function humanSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    /**
     * Build the tray.
     *
     * @param {object} deps
     * @param {(files: File[], context: object) => Promise<Array<object>>} deps.onAttach
     *   Uploads files; resolves with their metadata. Rejects on any failure.
     * @param {() => {type: string, id: string}} deps.getContext  The open
     *   conversation. Throws when none is open.
     * @param {(id: string) => Promise<void>} deps.onRemoveAttachment  Discards
     *   one pending upload on the server.
     * @param {() => Promise<{max_per_message: number}>} deps.getAttachmentLimits
     *   The operator-set limits, read once per tray.
     * @param {(message: string) => void} deps.setError  The composer's error line.
     * @returns {{ element: HTMLElement, addFiles: (files: FileList|File[]) => Promise<void>,
     *             list: () => object[], ids: () => string[],
     *             replace: (metas: object[]|null) => void,
     *             removeSent: (ids: string[]) => void, clear: () => void }}
     * @throws {Error} When any dependency is missing: a tray that cannot
     *   upload, discard, or know its cap would look usable and lie.
     */
    function createAttachmentTray(deps) {
        const opts = deps || {};
        for (const name of ['onAttach', 'getContext', 'onRemoveAttachment', 'getAttachmentLimits', 'setError']) {
            if (typeof opts[name] !== 'function') throw new Error(`[composer-attachments] deps.${name} is required`);
        }
        const { onAttach, getContext, onRemoveAttachment, getAttachmentLimits, setError } = opts;

        let pending = [];
        // Bumped on every replace(): an upload that finishes after the
        // operator switched conversations belongs to the one it started in.
        let generation = 0;
        // The per-message cap is an operator setting. Read it on first use and
        // keep it; a failed read is retried next time.
        let maxPerMessage = null;
        const element = h('div', { class: 'composer-attachments hidden' });

        function render() {
            element.replaceChildren();
            element.classList.toggle('hidden', pending.length === 0);
            for (const meta of pending) {
                element.append(h('span', { class: 'composer-attach-chip' },
                    h('span', { class: 'composer-attach-chip-name' }, meta.file_name || 'file'),
                    h('span', { class: 'composer-attach-chip-size' }, humanSize(meta.file_size || 0)),
                    h('button', {
                        type: 'button',
                        class: 'composer-attach-chip-remove',
                        'aria-label': 'Remove ' + (meta.file_name || 'attachment'),
                        onclick: () => { void remove(meta.id); },
                    }, '×')));
            }
        }

        /**
         * The per-message attachment cap, read once from the server.
         * @returns {Promise<number>}
         * @throws {Error} When the limits cannot be read.
         */
        async function attachmentCap() {
            if (maxPerMessage === null) {
                const limits = await getAttachmentLimits();
                maxPerMessage = limits.max_per_message;
            }
            return maxPerMessage;
        }

        /**
         * Upload picked or pasted files for the open conversation.
         *
         * Over the cap is refused before anything is uploaded. Any failure is
         * shown on the error line, never thrown.
         *
         * @param {FileList|File[]} files
         * @returns {Promise<void>}
         */
        async function addFiles(files) {
            const started = generation;
            try {
                const ctx = getContext();
                const cap = await attachmentCap();
                const picked = Array.from(files);
                if (pending.length + picked.length > cap) {
                    setError('Maximum ' + cap + ' attachments per message.');
                    return;
                }
                const results = await onAttach(picked, ctx);
                if (generation !== started) {
                    setError('The upload finished after you switched conversations. Attach it again here.');
                    return;
                }
                pending = pending.concat(results);
                render();
            } catch (err) {
                const name = (err && err.fileName) || '';
                const msg = (err && err.message) || 'Upload failed';
                setError(name ? name + ': ' + msg : msg);
            }
        }

        /**
         * Discard one pending upload on the server, then drop its chip.
         *
         * A refused delete keeps the chip and says why: a chip that vanished
         * while its upload stayed would look discarded and leave an orphan.
         *
         * @param {string} id
         * @returns {Promise<void>}
         */
        async function remove(id) {
            try {
                await onRemoveAttachment(id);
            } catch (err) {
                setError((err && err.message) || 'Could not remove the attachment.');
                return;
            }
            pending = pending.filter((a) => a.id !== id);
            render();
        }

        return {
            element,
            addFiles,
            /** @returns {object[]} A copy of the pending uploads' metadata. */
            list: () => pending.slice(),
            /** @returns {string[]} The pending upload ids, in attach order. */
            ids: () => pending.map((a) => a.id),
            /**
             * Show another conversation's stashed uploads (null for none).
             * @param {object[]|null} metas
             * @returns {void}
             */
            replace(metas) {
                generation += 1;
                pending = Array.isArray(metas) ? metas.slice() : [];
                render();
            },
            /**
             * Drop only the uploads a send just linked; newer ones stay.
             * @param {string[]} ids
             * @returns {void}
             */
            removeSent(ids) {
                const sent = new Set(ids);
                pending = pending.filter((a) => !sent.has(a.id));
                render();
            },
            /** @returns {void} */
            clear() {
                pending = [];
                render();
            },
        };
    }

    return { createAttachmentTray };
})();
