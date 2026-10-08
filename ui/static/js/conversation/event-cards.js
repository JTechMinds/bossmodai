/**
 * BossMod AI — the visual treatment for every non-message row.
 *
 * Three kinds, two producers. A conversation SOURCE produces `request` (a
 * host-path consent or CLI approval ask) and `note` (a task-lifecycle receipt,
 * or an agent's memory change), and nothing else. `event.*` is produced by needs/needs-bar.js, which renders
 * its cards through here rather than owning a second look for them.
 *
 * That split is spec 4.3 as reconciled: one renderer, one appearance per need.
 * Putting `event.*` in the transcript AND in the composer bar would render one
 * ask twice in the same conversation, which is exactly what the suppression
 * rule in spec 5.5 exists to prevent. A source emitting `event` is therefore
 * a bug.
 *
 * `progress` was the fourth kind and is gone. It had a renderer and never had
 * a producer; the operator's answer (spec 12, carried items) was to feed the
 * turn duration through db.get_world_state() and paint it in the presence row,
 * where the operator is already looking. Deleting the renderer is half of
 * shipping that feature — a card kind nothing can emit is dead weight, and
 * keeping it would leave two places that could claim to answer "is it still
 * working?".
 *
 * An unknown kind throws. A default branch returning an empty div would turn a
 * producer bug into a silently missing row, which is the exact failure mode
 * this consolidation exists to remove.
 */
const BossModEventCards = (() => {
    const { h } = BossModDom;

    /**
     * `card.tone` to its treatment. `blocked` and `warn` share the red
     * treatment because both mean "stopped"; only the wording differs.
     */
    const EVENT_TONES = Object.freeze({
        blocked: 'alert',
        warn: 'alert',
        ask: 'amber',
        done: 'ok',
    });

    /**
     * Debra's locked status verb, after an optional `{Name} ` prefix.
     * @param {string} text
     * @returns {string}
     */
    function originStatusVerb(text) {
        const match = /^(?:[A-Z][\w.-]*(?: [A-Z][\w.-]*)? )?(Created|Accepted|Writing|Waiting|Stalled|Declined|Rerouted|Cancelled|Done|Blocked|Busy)\b/
            .exec(String(text || '').trim());
        return match ? match[1] : '';
    }

    /**
     * Created/Accepted open the bound task in Tasks. Documents stay on Done.
     * A Created line must not grow a desk path just to look clickable.
     *
     * @param {object} message
     * @returns {string}
     */
    function originTaskOpenId(message) {
        const verb = originStatusVerb(message && message.text);
        if (verb !== 'Created' && verb !== 'Accepted') return '';
        return String((message && message.taskId) || '').trim();
    }

    /**
     * Done is the only origin line that opens a document. A path on Created,
     * Accepted, or Writing is not a deliverable and must not become a doc link.
     *
     * @param {object} message
     * @returns {string}
     */
    function originFileOpenPath(message) {
        const verb = originStatusVerb(message && message.text);
        if (verb !== 'Done') return '';
        return String((message && message.deskPath) || '').trim();
    }

    /**
     * The decorative glyph on a linked note's left.
     * @param {'task'|'memory'|'file'} kind  What the link opens.
     * @returns {HTMLElement}
     */
    function originGlyph(kind) {
        if (kind === 'task') {
            return h('i', {
                class: 'note-glyph',
                'data-lucide': 'list-todo',
                'aria-hidden': 'true',
            });
        }
        if (kind === 'memory') {
            // The desk's Memory tool glyph: the line opens that layer.
            return h('i', {
                class: 'note-glyph',
                'data-lucide': 'brain',
                'aria-hidden': 'true',
            });
        }
        return h('i', {
            class: 'note-glyph',
            'data-lucide': 'file-text',
            'aria-hidden': 'true',
        });
    }

    function originLink(label, onclick) {
        return h('button', {
            class: 'note-link',
            type: 'button',
            onclick,
        }, label);
    }

    /**
     * Paint the note glyph through the one icon painter.
     * The Node harness does not load it; the placeholder is the assertion.
     *
     * @param {HTMLElement} note
     */
    function paintOriginGlyph(note) {
        if (typeof BossModIcons === 'undefined' || typeof BossModIcons.paint !== 'function') return;
        if (!note.querySelector('[data-lucide]')) return;
        BossModIcons.paint(note, 'event-cards');
    }

    /**
     * Build one event card.
     *
     * @param {object} message  A normalised Message with `kind !== 'message'`.
     * @param {object} ctx  Conversation capabilities.
     * @param {Function} ctx.api  Authenticated fetch helper; a consent card
     *   cannot resolve without it.
     * @param {(agentId: string, path?: string) => void} [ctx.openDesk]
     *   Optional (spec 4.1). The desk modal, opened on the path, as the
     *   fallback when the deliverable opener is not loaded — only for a note
     *   with an author, because a desk needs an owner.
     * @param {(path: string, agentId?: string) => (void|Promise<void>)} [ctx.openDeliverable]
     *   Same file-open path the task deliverable cards use. The Done path link
     *   renders only when this or openDesk is injected — a control that
     *   renders but does nothing is worse than one that is absent.
     * @param {(placeId: string, params?: object) => void} [ctx.navigate]
     *   Same Tasks open path blocked needs use: `navigate('tasks', { taskId })`.
     *   Created/Accepted notes render a task glyph and blue-link text only
     *   when this arrives.
     * @param {(agentId: string, memoryId: number) => void} [ctx.openMemory]
     *   The author's desk with its Memory layer on one memory. A note that
     *   carries `memoryId` (an agent saved or updated a memory) renders the
     *   brain glyph and blue-links its text only when this arrives and the
     *   note has an author; a removed memory's note carries none and stays
     *   plain text.
     * @returns {HTMLElement}
     * @throws {Error} When ctx is missing, on a kind with no renderer, on a
     *   `request` or `event` with no card, or on an `event` whose tone has no
     *   treatment — an unrecognised tone must not paint as a neutral row.
     */
    function renderEventCard(message, ctx) {
        if (!ctx) throw new Error('[event-cards] ctx is required');
        if (message.kind === 'request') {
            if (!message.card) {
                throw new Error('[event-cards] a request message carries no card payload');
            }
            const approval = BossModConsentCard.isCliApprovalCard(message.card);
            const wrapper = h('div', {
                class: 'msg host-path-consent-card',
                id: approval
                    ? `cli-approval-${message.card.id}`
                    : `host-path-consent-${message.card.id}`,
            });
            if (approval) {
                BossModConsentCard.renderCliApprovalCard(wrapper, message.card, ctx.api);
            } else {
                BossModConsentCard.renderHostPathConsentCard(wrapper, message.card, ctx.api);
            }
            return wrapper;
        }

        if (message.kind === 'note') {
            const memoryId = message.memoryId;
            const deskPath = String(message.deskPath || '').trim();
            const taskId = originTaskOpenId(message);
            const filePath = originFileOpenPath(message);
            const text = String(message.text || '');
            // Recorded even when nothing can act on it, so the path is never
            // lost between the phase that reads it and the phase that opens it.
            const openableFile = Boolean(filePath);
            const note = h('div', {
                class: openableFile ? 'note note-ok' : 'note',
                'data-desk-path': deskPath,
                'data-task-id': taskId,
                'data-tone': openableFile ? 'ok' : null,
            });
            // The desk fallback opens the AUTHOR's desk, so a note with no
            // author has no desk to open and the link does not render.
            const noteAgentId = String(message.authorAgentId || ctx.agentId || '');
            const canOpenFile = openableFile && (
                typeof ctx.openDeliverable === 'function'
                || (typeof ctx.openDesk === 'function' && Boolean(noteAgentId))
            );
            const canOpenTask = Boolean(taskId) && typeof ctx.navigate === 'function';
            const canOpenMemory = memoryId != null && typeof ctx.openMemory === 'function'
                && Boolean(noteAgentId);
            const openKind = canOpenMemory ? 'memory' : (canOpenTask ? 'task' : (canOpenFile ? 'file' : ''));
            if (openKind) note.setAttribute('data-open-kind', openKind);
            if (openKind === 'memory') {
                note.append(
                    originGlyph('memory'),
                    h('p', { class: 'note-text' }, originLink(text, () => {
                        ctx.openMemory(noteAgentId, memoryId);
                    })),
                );
            } else if (openKind === 'task') {
                note.append(
                    originGlyph('task'),
                    h('p', { class: 'note-text' }, originLink(text, () => {
                        ctx.navigate('tasks', { taskId });
                    })),
                );
            } else if (openKind === 'file') {
                const agentId = noteAgentId;
                const at = text.lastIndexOf(filePath);
                const prefix = at >= 0 ? text.slice(0, at) : '';
                const label = at >= 0 ? filePath : text;
                const fileLink = originLink(label, async () => {
                    if (typeof ctx.openDeliverable !== 'function') {
                        ctx.openDesk(agentId, filePath);
                        return;
                    }
                    fileLink.classList.remove('is-failed');
                    fileLink.removeAttribute('title');
                    try {
                        await ctx.openDeliverable(filePath, agentId);
                    } catch (err) {
                        // Same treatment as a Tasks deliverable card: a link
                        // that fails must say so, not silently do nothing.
                        fileLink.classList.add('is-failed');
                        fileLink.setAttribute('title', (err && err.message) || 'Could not open that path');
                        console.error('[event-cards] could not open', filePath, err);
                    }
                });
                note.append(
                    originGlyph('file'),
                    h('p', { class: 'note-text' }, prefix || null, fileLink),
                );
            } else {
                note.append(h('p', { class: 'note-text' }, text));
            }
            paintOriginGlyph(note);
            return note;
        }

        if (message.kind === 'event') {
            if (!message.card) {
                throw new Error('[event-cards] an event message carries no card payload');
            }
            const card = message.card;
            const treatment = EVENT_TONES[card.tone];
            if (!treatment) {
                throw new Error(`[event-cards] no treatment for event tone "${card.tone}"`);
            }
            const actions = Array.isArray(card.actions) ? card.actions : [];
            return h('div', { class: `event-card tone-${treatment}`, 'data-tone': card.tone },
                h('p', { class: 'event-card-title' }, String(card.title || '')),
                card.sub ? h('p', { class: 'event-card-sub' }, String(card.sub)) : null,
                card.error
                    ? h('p', { class: 'event-card-error', role: 'alert' }, String(card.error))
                    : null,
                actions.length
                    ? h('div', { class: 'event-card-actions' }, actions.map((action) => {
                        const button = h('button', {
                            class: `event-card-action ${action.tone || 'default'}`,
                            type: 'button',
                            onclick: () => action.onSelect(),
                        }, String(action.label));
                        button.disabled = action.disabled === true;
                        return button;
                    }))
                    : null);
        }

        throw new Error(`[event-cards] no renderer for kind "${message.kind}"`);
    }

    return { renderEventCard };
})();
