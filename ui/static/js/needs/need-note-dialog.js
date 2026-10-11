/**
 * BossMod AI — the optional note a need's action can carry.
 *
 * Some server-described actions say more than "do it": a Nudge hands blocked
 * work back to its agent, and a hint about what to try is the useful part.
 * Such an action carries a `note` descriptor (need-shape.js); this asks for
 * the note and hands the action back with it as the request body.
 *
 * Shared by the bell and the composer bar, which both run need actions, and
 * kept out of needs-store.js because it is UI. Mirrors
 * places/tasks/tasks-complete.js: a form, a growing textarea, and an outcome
 * settled exactly once by submit or by however the dialog closed.
 */
const BossModNeedNoteDialog = (() => {
    const { h } = BossModDom;

    const FORM_ID = 'need-note-form';
    const FIELD_ID = 'need-note-text';

    /**
     * Ask the operator for an action's optional note.
     *
     * @param {object} need    The need the action belongs to; its `sub`
     *   (title — reason) is shown as the dialog's hint line.
     * @param {object} action  A NeedAction carrying `note: { title, label }`.
     * @returns {Promise<object|null>} A copy of `action` whose `body` is
     *   `{ note }` — the trimmed text, or null when left blank — or null when
     *   the operator cancelled, in which case nothing should be sent.
     * @throws {Error} When `action.note` is missing: an action with nothing to
     *   ask must never open an empty dialog.
     */
    function ask(need, action) {
        if (!action || !action.note) throw new Error('[need-note-dialog] the action carries no note');
        return new Promise((resolve) => {
            let settled = false;
            const text = h('textarea', { class: 'field-textarea', id: FIELD_ID, 'data-autogrow': true });
            const grow = BossModAutoGrow.bind(text);
            let modal = null;
            const form = h('form', {
                class: 'assign-form',
                id: FORM_ID,
                onsubmit: (event) => {
                    event.preventDefault();
                    const note = text.value.trim();
                    settled = true;
                    modal.close();
                    resolve(Object.assign({}, action, { body: { note: note || null } }));
                },
            },
                need && need.sub ? h('p', { class: 'assign-hint' }, need.sub) : null,
                h('label', { class: 'assign-field' },
                    h('span', { class: 'assign-field-label' }, action.note.label), text));
            modal = BossModOverlays.createModal({
                title: action.note.title,
                body: form,
                actions: [
                    { label: action.label, tone: 'primary', form: FORM_ID },
                    { label: 'Cancel', tone: 'quiet' },
                ],
                // However it closed without sending: nothing happens.
                onClose: () => {
                    grow.destroy();
                    if (!settled) resolve(null);
                },
            });
        });
    }

    return { ask };
})();
