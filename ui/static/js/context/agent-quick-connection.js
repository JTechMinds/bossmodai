/**
 * BossMod AI — where the quick layout puts the AI connection question.
 *
 * One rule, and the two exports that carry it. The create form's quick state
 * asks the operator two questions, and this is the second: `liftConnection`
 * takes the connection control out of the matrix
 * context/agent-form-connections.js built and hands it back as a field to put
 * beside Name — and `aiQuestion` answers, later, where it put it.
 *
 * THE EMPTY SHAPE is why this is a module rather than a branch. With no
 * connection configured the matrix renders a link to Settings and no select at
 * all, and an agent created that way carries five null connections and fails
 * on its first turn. So the message comes up into the always-visible area with
 * everything else, and the field carries a `required` control the operator
 * cannot satisfy — the same native constraint validation the populated shape
 * relies on, rather than a second gate bolted onto the dialog's primary.
 *
 * THE GUARD is the other half of that, and this module owns it: the lifted
 * control stays `required` until at least one of the five `model_*` selects
 * holds a value, and it re-arms the moment they are all back at None. It
 * tracks the ANSWER and nothing else. The first version of this rule relaxed
 * `required` when the layout's "Review & customise" disclosure was opened —
 * and that disclosure is also where the template's specialty, description and
 * what-done live, so opening it to READ them disarmed the guard and a quick
 * create wrote five null connections over "Saved successfully". A panel toggle
 * is not an answer.
 *
 * Built with BossModDom.h, and it MOVES the form's own nodes rather than
 * rebuilding them, so every listener agent-form.js bound travels with them.
 */
const BossModAgentQuickConnection = (() => {
    const { h } = BossModDom;

    const COPY = Object.freeze({
        ai: 'AI',
        aiHint: 'Used for every activation type.',
        // The stand-in select's only option, and it sits directly above the
        // matrix's own "No connections configured. Add one in Settings". Said
        // twice it read as two findings; this is the placeholder the populated
        // shape uses ("— Set all connections —"), so the pair reads as one
        // thing: an empty control, then the sentence that explains it.
        aiNone: '— None available —',
    });

    /** Joins the stand-in select to the message that explains it. */
    const NONE_ID = 'quick-ai-none';

    /**
     * WHERE this form's AI connection question ended up, written on the form.
     *
     * context/agent-form-save.js refuses a create that would carry no
     * connection, and that refusal has to name a control the operator can
     * actually see and reach. The question has three homes and they are not
     * interchangeable: the matrix agent-form-connections.js built, in place;
     * the field lifted out of it to beside Name, with that matrix — and the
     * "AI Connections" heading over it — swept behind a collapsed disclosure;
     * and the unanswerable stand-in, which removes that heading's box
     * altogether. One sentence naming the heading is therefore wrong on the
     * second path and points at nothing on the third.
     *
     * So each lift says which one it made and the refusal ASKS. The save
     * handler learns no layout, and the answer comes from the only honest
     * source there is — the form itself.
     */
    const AI_QUESTION = 'data-ai-question';
    const IN_PLACE = 'matrix';
    const LIFTED = 'lifted';
    const UNAVAILABLE = 'unavailable';

    /**
     * Which of the three shapes a form's AI question is in.
     *
     * @param {HTMLElement} form  The `<form>` itself.
     * @returns {'matrix'|'lifted'|'unavailable'} A form this module never
     *   rearranged carries no attribute and is the matrix, in place.
     * @throws {Error} On an attribute this module did not write. A shape
     *   nobody has copy for would otherwise be refused with a sentence chosen
     *   for a different layout, which is the defect this vocabulary closes.
     */
    function aiQuestion(form) {
        const shape = form.getAttribute(AI_QUESTION);
        if (shape === null) return IN_PLACE;
        if (shape !== LIFTED && shape !== UNAVAILABLE) {
            throw new Error(`[quick-connection] the form claims AI question "${shape}"`);
        }
        return shape;
    }

    /**
     * The connection question, lifted out of the matrix to beside Name. The
     * matrix has two shapes and so does this: "Set All" when a connection is
     * configured, the link to Settings when none is. Both come up.
     *
     * @param {HTMLElement} form  The `<form>` itself, never the host it was
     *   published into: publication MOVES the form out of that host and leaves
     *   it empty, so a binding rooted on the host searches nothing for the
     *   rest of its life (see the header of context/agent-form.js).
     * @returns {{field: HTMLElement, select: HTMLElement, configured: boolean,
     *   guard: {release: () => void}|null}} `guard` is null for the shape that
     *   has nothing to answer with; see liftNoConnections.
     * @throws {Error} When the form has neither — the matrix always builds
     *   one, so this is then not the form this was written against.
     */
    function liftConnection(form) {
        const select = form.querySelector('select[name="model_all"]');
        return select ? liftSetAll(form, select) : liftNoConnections(form);
    }

    /**
     * Arm the connection guard on the lifted select.
     *
     * THE RULE, and the one place it is enforced: `required` stays on until at
     * least one of the five `model_*` selects holds a value, and it comes back
     * the moment they are all cleared to None. Live, on every change, in both
     * directions — not a one-way flip, and not tied to anything the layout
     * above it does with a disclosure.
     *
     * It reads THE FIVE and never the lifted select's own value, because those
     * five are the keys context/agent-submit.js sends: the guard asks exactly
     * the question the save will ask. A "Set All" that stopped reaching them —
     * the defect that shipped once already — therefore leaves the guard armed
     * and the submit refused, instead of letting a control the operator
     * answered stand in for an agent that has no connection at all.
     *
     * @param {HTMLElement} form  The `<form>`, for the reason above.
     * @param {HTMLElement} select  The lifted select the guard sits on.
     * @returns {{release: () => void}} `release` retires the guard and drops
     *   `required` for good. "Remove template" is its one caller: that leaves
     *   the quick layout for the full form, whose own documented semantics
     *   allow a null per activation type.
     * @throws {Error} When the matrix is missing one of the five. They are
     *   built by the same branch as the select this guard sits on, so an
     *   absent one means the guard cannot see what the save would read — and a
     *   guard that cannot see that is the hole it exists to close.
     */
    function armGuard(form, select) {
        const matrix = BossModAgentFields.MODEL_TYPES.map((type) => {
            const sel = form.querySelector(`select[name="${type.key}"]`);
            if (!sel) {
                throw new Error(`[quick-connection] the matrix has no ${type.key} select`);
            }
            return sel;
        });
        let armed = true;
        const sync = () => {
            if (!armed) return;
            if (matrix.some((sel) => sel.value)) select.removeAttribute('required');
            else select.setAttribute('required', '');
        };
        // The lifted select is watched as well as the five, because the fan-out
        // agent-form.js bound to it writes their values FROM SCRIPT, and a
        // value assigned that way fires no change event of its own. This
        // listener is registered after that one, so it reads the five after
        // they have been written; registered the other way round it would
        // simply stay armed one interaction longer, which is the safe side.
        select.addEventListener('change', sync);
        matrix.forEach((sel) => sel.addEventListener('change', sync));
        sync();
        return {
            release() {
                armed = false;
                select.removeAttribute('required');
            },
        };
    }

    /** "Set All", relabelled as THE connection question. MOVED, not rebuilt,
     *  so it keeps the `change` listener agent-form.js bound to it and still
     *  fans out to all five.
     *  @returns {{field, select, configured: true, guard: {release}}} */
    function liftSetAll(form, select) {
        // Captured before the move: afterwards the select's parent is the new
        // field. The wrapper is left empty by the move and goes with it.
        const wrap = select.closest('div');
        const label = form.querySelector('label[for="agent-connection-model_all"]');
        const field = h('div', { class: 'quick-ai' });
        if (label) {
            label.textContent = COPY.ai;
            label.setAttribute('class', 'quick-ai-label');
            field.append(label);
        } else {
            field.append(h('label', {
                class: 'quick-ai-label', for: 'agent-connection-model_all',
            }, COPY.ai));
        }
        field.append(select, h('p', { class: 'quick-ai-hint' }, COPY.aiHint));
        // Required until the question is answered, and armGuard is what sets
        // it: one rule, one place, rather than a value written here and a
        // condition for removing it written somewhere else. Native constraint
        // validation reaches it either way — the primary carries
        // `form="agent-form"`.
        const guard = armGuard(form, select);
        form.setAttribute(AI_QUESTION, LIFTED);
        // Emptied by the move, so it goes. The emptiness is CHECKED rather
        // than assumed: closest() would happily hand back a container that
        // still holds half the form if the markup ever changed shape.
        if (wrap && wrap !== form && !wrap.children.length) wrap.remove();
        // The rule that separated "Set All" from the grid follows it out; the
        // matrix is the only <hr> the form has.
        const rule = form.querySelector('hr');
        if (rule) rule.remove();
        return { field, select, configured: true, guard };
    }

    /**
     * The same question with no answer available. The matrix's "No connections
     * configured / Add one in Settings" is MOVED into the always-visible area:
     * the operator has to be told why they cannot proceed, and the sweep below
     * would file it behind the disclosure.
     *
     * The select is a stand-in writing to nothing — `buildSubmitData` reads
     * the five MODEL_TYPES keys, never `model_all`. It is here to be `required`
     * and unanswerable, which is what makes native constraint validation refuse
     * the submit: the mechanism the populated case already uses, not a second
     * one bolted onto the primary. Enabled on purpose — a disabled control is
     * exempt from validation.
     *
     * NO GUARD comes back with it, and that is the point: there are no five
     * selects to answer, so `required` here is conditional on nothing and
     * never comes off — not when the disclosure opens, and not when the
     * template is removed. The operator is told why in the notice beside it.
     *
     * @returns {{field: HTMLElement, select: HTMLElement, configured: false,
     *   guard: null}}
     * @throws {Error} When the link to Settings is absent too.
     */
    function liftNoConnections(form) {
        const link = form.querySelector('#btn-goto-connections');
        if (!link) {
            throw new Error('[quick-connection] the form has neither a select nor a link to Settings');
        }
        const notice = link.closest('p') || link;
        notice.setAttribute('id', NONE_ID);
        // Read BEFORE the move, while the notice is still in the matrix: a
        // moment later its closest div is the field being built here.
        const box = notice.closest('div');
        const select = h('select', {
            class: 'quick-ai-select', name: 'model_all',
            id: 'agent-connection-model_all', required: true,
            'aria-describedby': NONE_ID,
        }, h('option', { value: '' }, COPY.aiNone));
        const field = h('div', { class: 'quick-ai' },
            h('label', { class: 'quick-ai-label', for: 'agent-connection-model_all' },
                COPY.ai),
            select, notice);
        // A heading over nothing reads as a section that failed to load, so
        // what the move emptied goes with it. CHECKED, not assumed: a
        // container still holding a control is one this did not understand.
        if (box && box !== form && !box.querySelector('select')
            && !box.querySelector('button')) box.remove();
        form.setAttribute(AI_QUESTION, UNAVAILABLE);
        return { field, select, configured: false, guard: null };
    }

    return { COPY, NONE_ID, AI_QUESTION, aiQuestion, liftConnection };
})();
