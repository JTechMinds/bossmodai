/**
 * BossMod AI — the visual treatment for every non-message row.
 *
 * Three kinds, two producers. A conversation SOURCE produces `request` (a
 * host-path consent ask) and `note` (a task-lifecycle receipt), and nothing
 * else. `event.*` is produced by needs/needs-bar.js, which renders its cards
 * through here rather than owning a second look for them.
 *
 * That split is spec 4.3 as reconciled: one renderer, one appearance per need.
 * Putting `event.*` in the transcript AND in the composer bar would render one
 * CLI approval twice in the same conversation, which is exactly what the
 * suppression rule in spec 5.5 exists to prevent. A source emitting `event` is
 * therefore a bug.
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
     * Build one event card.
     *
     * @param {object} message  A normalised Message with `kind !== 'message'`.
     * @param {object} ctx  Conversation capabilities.
     * @param {Function} ctx.api  Authenticated fetch helper; a consent card
     *   cannot resolve without it.
     * @param {(path: string) => void} [ctx.openDesk]  Optional (spec 4.1). The
     *   "Open in Desk" affordance renders only when it is injected — a control
     *   that renders but does nothing is worse than one that is absent. Phase
     *   2B supplies it with the context column.
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
            // The id and class are what collapseRelatedConsentCards and the
            // deep-link from the needs queue select on.
            const wrapper = h('div', {
                class: 'msg host-path-consent-card',
                id: `host-path-consent-${message.card.id}`,
            });
            BossModConsentCard.renderHostPathConsentCard(wrapper, message.card, ctx.api);
            return wrapper;
        }

        if (message.kind === 'note') {
            const deskPath = String(message.deskPath || '');
            // Recorded even when nothing can act on it, so the path is never
            // lost between the phase that reads it and the phase that opens it.
            const note = h('div', { class: 'note', 'data-desk-path': deskPath },
                h('p', { class: 'note-text' }, String(message.text || '')));
            if (deskPath && typeof ctx.openDesk === 'function') {
                note.append(h('button', {
                    class: 'note-action',
                    type: 'button',
                    onclick: () => ctx.openDesk(deskPath),
                }, 'Open in Desk'));
            }
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
