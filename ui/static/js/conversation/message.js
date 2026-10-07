/**
 * BossMod AI — one ordinary message node.
 *
 * Renders `kind === 'message'` and nothing else; every other kind belongs to
 * event-cards.js. It reads only the normalised Message shape (spec 4.1), never
 * a backend field, which is the whole point of the source adapters: three wire
 * formats used to mean three renderers.
 *
 * Message bodies are agent output and are therefore untrusted. They are also
 * markdown — agents write lists, fences and emphasis whether or not anything
 * renders them — so they go through core/markdown.js, which parses inertly and
 * sanitises against an allowlist before a node reaches the page. This module
 * still builds no markup of its own and still never touches innerHTML.
 *
 * Every author renders the same way, the operator's own turns included: one
 * transcript that formatted `**x**` for one speaker and not the other would be
 * two renderers again, and a pasted log is worth a fence whoever pasted it.
 *
 * The name is chrome, not prose. It sits above the paragraph bubble as
 * quiet agent-colored text — the same ink family as the initial, regular
 * weight, no chip fill, no border. A color-coded initial sits lower-left
 * beside the bubble, bottom-aligned with it, so a labelled turn reads as a
 * name over `[face] [bubble]` rather than a face stacked on the name row.
 * Every agent turn that paints a face also paints `.msg-author`;
 * `showAuthor` must not leave an orphan initial.
 */
const BossModMessage = (() => {
    const { h } = BossModDom;

    /** Author values the stylesheet has a bubble for. */
    const AUTHORS = new Set(['human', 'agent', 'system', 'other']);

    /**
     * A clock face for the transcript.
     *
     * An unparseable timestamp renders as the raw server string rather than
     * as an empty element: showing what the server sent is honest, hiding it
     * is not. The machine-readable value stays in `datetime` either way.
     *
     * @param {string} createdAt  ISO-8601.
     * @returns {string}
     */
    function timeLabel(createdAt) {
        const at = new Date(createdAt);
        if (Number.isNaN(at.getTime())) return createdAt;
        return at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    /**
     * Format a byte count as a human-readable size string.
     *
     * @param {number} bytes
     * @returns {string}
     */
    function humanSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    /**
     * Thumbnail object URLs by attachment id. `<img src>` carries no API
     * token, so previews come through the authenticated fetch as blob URLs.
     * A transcript repaints often; caching means one fetch and one URL per
     * attachment instead of one per paint. The URLs live for the page
     * session, bounded by the attachments actually shown. A failed load is
     * dropped so a later paint can try again.
     * @type {Map<string, Promise<string>>}
     */
    const previewUrls = new Map();

    function previewUrl(id) {
        if (!previewUrls.has(id)) {
            const pending = BossModApi.fetchBlobUrl(`/api/attachments/${encodeURIComponent(id)}/preview`);
            pending.catch(() => previewUrls.delete(id));
            previewUrls.set(id, pending);
        }
        return previewUrls.get(id);
    }

    /**
     * Open an attachment in the Files viewer, by its company path.
     * @param {object} att
     * @param {HTMLElement} control  Marked failed, with the reason, on error.
     * @returns {Promise<void>}
     */
    async function openAttachment(att, control) {
        control.classList.remove('is-failed');
        try {
            if (typeof BossModFileViewer === 'undefined') throw new Error('The file viewer is not loaded.');
            await BossModFileViewer.open(att.company_path, { api: BossModApi.fetch });
        } catch (err) {
            console.error('[message] could not open attachment', att.id, err);
            control.classList.add('is-failed');
            control.setAttribute('title', (err && err.message) || 'Could not open the file');
        }
    }

    /**
     * A file chip that opens the attachment. A file stored before attachments
     * lived under the floors has no company path, and says so, disabled.
     * @param {object} att
     * @param {string} [failure]  Why an image preview is showing as a chip.
     * @returns {HTMLElement}
     */
    function fileChip(att, failure) {
        const name = att.file_name || 'file';
        const tier = att.preview_tier || 'other';
        const icon = { image: '🖼️', document: '📄', text: '📝' }[tier] || '📎';
        const label = failure
            ? `${name}: ${failure}`
            : (att.company_path ? `Open ${name} (${humanSize(att.file_size || 0)})` : `${name} cannot be opened here`);
        const chip = h('button', {
            class: failure ? 'file-chip is-failed' : 'file-chip',
            type: 'button',
            'aria-label': label,
            title: label,
            disabled: !att.company_path,
            onclick: () => { void openAttachment(att, chip); },
        },
            h('span', { class: 'file-chip-icon', 'aria-hidden': 'true' }, icon),
            h('span', { class: 'file-chip-name' }, name),
            h('span', { class: 'file-chip-size' }, humanSize(att.file_size || 0)));
        return chip;
    }

    /**
     * An image thumbnail loaded through the authenticated fetch. On failure
     * it becomes the file chip, labelled with the failure, and logs it.
     * @param {object} att
     * @returns {HTMLElement}
     */
    function imageThumb(att) {
        const name = att.file_name || 'file';
        const img = h('img', { class: 'msg-att-img', alt: name, loading: 'lazy' });
        const link = h('button', {
            class: 'msg-att-img-link',
            type: 'button',
            'aria-label': `Open ${name}`,
            disabled: !att.company_path,
            onclick: () => { void openAttachment(att, link); },
        }, img);
        previewUrl(att.id).then(
            (url) => img.setAttribute('src', url),
            (err) => {
                console.error('[message] attachment preview failed', att.id, err);
                const parent = link.parentNode;
                if (parent) parent.replaceChild(fileChip(att, 'preview failed'), link);
            },
        );
        return link;
    }

    /**
     * Render the attachment row for a message.
     *
     * T1 (image): thumbnail, click opens it in the Files viewer.
     * T2/T3/T4:   file chip (name + size), click opens it in the Files viewer.
     *
     * @param {Array<object>} attachments  Normalised attachment metadata.
     * @returns {HTMLElement}
     */
    function renderAttachments(attachments) {
        const row = h('div', { class: 'msg-attachments' });
        for (const att of attachments) {
            row.append((att.preview_tier || 'other') === 'image' ? imageThumb(att) : fileChip(att));
        }
        return row;
    }

    /**
     * Build one message node.
     *
     * @param {object} message  A normalised Message. `showAuthor` decides
     *   whether the name is labelled — threads label it, agent DMs do not,
     *   because in a DM every non-human turn is the same agent.
     * @returns {HTMLElement}
     * @throws {Error} When called with a kind other than 'message'. The
     *   transcript dispatches on kind, so arriving here with another one means
     *   a source produced a shape no renderer owns.
     */
    function renderMessage(message) {
        if (!message || message.kind !== 'message') {
            throw new Error(`[message] renderMessage got kind "${message && message.kind}"`);
        }
        const author = AUTHORS.has(message.author) ? message.author : 'other';
        const key = String(message.key || '').trim();
        const createdAt = String(message.createdAt || '').trim();
        const text = String(message.text || '');
        const attachments = Array.isArray(message.attachments) ? message.attachments : null;

        const body = h('div', { class: 'msg-body md' },
            BossModMarkdown.render(text));
        if (typeof BossModMentionPills !== 'undefined') {
            BossModMentionPills.linkify(body);
        }

        const label = String(message.authorName || '').trim();
        const withFace = author === 'human' || author === 'agent';
        const showName = Boolean(message.showAuthor) || (withFace && author === 'agent');
        // The human's own lines are always "You", whatever name the row carries.
        const faceName = author === 'human' ? 'You' : (label || 'Agent');
        const color = message.authorColor || null;
        const tint = color ? BossModAvatar.tintFor(color) : null;

        // If the message has no text but has attachments, skip the empty body
        // and render only the attachment row.
        const hasText = text.trim().length > 0;
        const hasAtts = attachments && attachments.length > 0;

        const bubbleChildren = [];
        if (hasText) {
            bubbleChildren.push(body);
        }
        if (hasAtts) {
            bubbleChildren.push(renderAttachments(attachments));
        }
        if (createdAt) {
            bubbleChildren.push(h('time', { class: 'msg-time', datetime: createdAt }, timeLabel(createdAt)));
        }

        const bubble = h('div', { class: `msg msg-${author}` }, ...bubbleChildren);

        return h('div', {
            class: `msg-turn msg-turn-${author}`,
            'data-message-key': key || null,
        },
            withFace
                ? h('span', { class: 'msg-face', 'aria-hidden': 'true' },
                    BossModAvatar.create({ name: faceName, color, size: 'sm' }))
                : null,
            h('div', { class: 'msg-stack' },
                showName
                    ? h('div', {
                        class: 'msg-author',
                        style: tint ? `color:${tint.ink}` : null,
                    }, label || 'Unknown')
                    : null,
                bubble));
    }

    return { renderMessage };
})();
