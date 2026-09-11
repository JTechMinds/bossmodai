/**
 * BossMod AI — what happens once the agent form is on screen.
 *
 * Split from context/agent-edit.js the way context/agent-recovery.js was: that
 * module owns the DIALOG — one at a time, two steps on the create path, a
 * footer per step — and this owns the form inside it. Building it, reading the
 * connections the submit path resolves against, the in-flight gate, the save,
 * and the delete confirmation.
 *
 * It knows nothing about steps, pickers or templates, and nothing here calls
 * back into the dialog: the caller passes what to do after a save and after a
 * delete, which is what lets one implementation serve both flows.
 *
 * ONE RENDER OWNS A HOST, AND WITH IT THE PRIMARY. The create dialog can put
 * two builds over one host — pick, Back, pick again — and each is four
 * requests long. So a render CLAIMS the dialog's primary, builds OFF-DOM, and
 * publishes only while it still holds the claim; a superseded render answers
 * `false` and writes nothing, including on the way out through a failure.
 * Staging is what makes that true rather than likely: buildFormHTML awaits
 * before it writes, so two builds sharing a container race inside it where no
 * check out here could separate them.
 *
 * The claim is context/agent-dialog-footer.js's, not a second one kept here.
 * One render and its save must be one claim: a save that finished after a
 * newer build took the dialog over used to re-enable the button that build was
 * holding down, and the operator's next click created an agent from the draft
 * they had already left.
 */
const BossModAgentFormSave = (() => {
    const { h } = BossModDom;

    /** Said in the form, and again at any attempt to save through it. */
    const CONNECTIONS_FAILED = 'Couldn’t read your AI connections, so this form '
        + 'cannot save: the agent would be created with none. Close this dialog '
        + 'and open it again to retry.';

    // Said when a CREATE would write five null connections, worded per SHAPE:
    // one sentence is not true of both, because a form built with nothing
    // configured has no matrix to point at — it renders a link to Settings
    // where the selects would be. The form is asked which shape it is in, and
    // the module that RENDERED that shape is the one that answers.
    //
    // Two entries, down from three. The third was for a layout that lifted one
    // select out of the matrix and swept the rest behind a disclosure; nothing
    // is lifted or swept now, so there is no third place the question can be.
    const NO_CONNECTION_NEXT = Object.freeze({
        matrix: 'Choose one under AI Connections, or add a connection in Settings if you have none.',
        unavailable: 'Add a connection in Settings; the AI Connections section links there.',
    });
    const noConnection = (form) => 'This agent has no AI connection, so it would fail on its first turn. '
        + NO_CONNECTION_NEXT[BossModAgentFormConnections.aiQuestion(form)]
        + ' Nothing was saved; your draft is still here.';

    // Said when a CREATE is opened with nothing to choose from at all. Not the
    // same failure as CONNECTIONS_FAILED: that one is a read that did not
    // land, and this is a read that landed on an empty list. The operator can
    // act on this one — the section's own link goes to Settings — so it names
    // the fix rather than a retry.
    const NO_CONNECTIONS_CONFIGURED = 'No AI connection is configured, so this '
        + 'form cannot create an agent: it would fail on its first turn. Add one '
        + 'in Settings — the AI Connections section links there — then reopen '
        + 'this dialog.';

    /**
     * The connections the SUBMIT path resolves the operator's choice against.
     *
     * NOT agent-form.js's loadFormData read. That one is documented to answer
     * a failure with empty lists so the form still RENDERS, with its "no
     * connections configured" link to Settings saying what is missing. This
     * one decides what the agent is SAVED with, and there an empty list is
     * indistinguishable from "the operator has none": buildSubmitData would
     * write null for all five model types and the save would report success —
     * exactly the connectionless agent the whole dialog is shaped to refuse,
     * reached through a second door. So a failure is `null`, and null blocks
     * the save. An EMPTY list is a third answer and not this one: it means the
     * read landed and the operator has none configured, which `renderInline`
     * withholds the primary for.
     *
     * `res.ok` is part of that read: an error response carries a `{detail}`
     * object, and iterating one throws inside the save instead of here.
     *
     * @returns {Promise<object[]|null>} null when the read failed, which is
     *   reported to the operator rather than treated as "none configured".
     */
    async function readConnections() {
        try {
            const res = await apiFetch('/api/connections');
            if (!res.ok) throw new Error(`GET /api/connections answered ${res.status}`);
            const body = await res.json();
            if (!Array.isArray(body)) {
                throw new Error('GET /api/connections did not answer with a list');
            }
            return body;
        } catch (err) {
            console.error('[agent-form-save] the form could not read your AI connections:', err);
            return null;
        }
    }

    /**
     * Build the form and bind everything inside it, on a detached host.
     *
     * Detached because the caller publishes it only if this build is still the
     * one the operator is waiting for — and because the pinned primary submits
     * `#agent-form` BY ID, so a form that has not been published cannot be
     * reached by it at all.
     *
     * @param {object} deps
     * @param {object|null} deps.agent  null to create.
     * @param {object} deps.primary  The dialog's primary owner.
     * @param {object} deps.token    This render's claim on it. The save the
     *   form is wired with inherits it, so the save may move the button only
     *   while the form it reads is still the one on screen.
     * @param {(saved?: object) => void} deps.onSave
     * @param {() => void} deps.onDelete
     * @returns {Promise<{stage: HTMLElement, connections: object[]|null,
     *   feedback: object}>}
     * @throws {Error} When the form itself cannot be built.
     */
    async function stageForm({ agent, primary, token, onSave, onDelete }) {
        // Per render, not per module. Which agent this form is for, and whether
        // it creates or updates, belong to THIS form: as module state a second
        // form would rewrite the first one's identity, and an edit already open
        // would start saving as a create.
        let isCreating = !agent;
        let currentAgentId = agent?.id || null;
        if (agent?.id) {
            const full = await BossModAgentApi.fetchAgent(agent.id);
            if (full) {
                agent = { ...agent, ...full };
            }
        }

        const stage = h('div');
        await BossModAgentForm.buildFormHTML(stage, agent);

        const form = stage.querySelector('#agent-form');
        const deleteBtn = stage.querySelector('#btn-delete-agent');

        const RECOVERY = BossModAgentRecovery;
        const feedback = RECOVERY.createFeedback(form);
        const say = (tone, text) => feedback.say(tone, text);
        // The FORM, never the stage. Both work today because these bind
        // immediately and the nodes survive publication, but the stage is
        // EMPTIED by it — and a lookup rooted there is the exact shape that
        // silently killed "Set All". The rule is in agent-form.js's header.
        RECOVERY.bindRecoveryTools({
            form,
            agentId: () => currentAgentId,
            feedback,
            onSave: () => { if (onSave) onSave(); },
        });

        const connections = await readConnections();

        const hireSubmit = BossModGates.createInFlightGate();

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (hireSubmit.busy()) return;
            // Nothing to resolve the operator's answer against, so every
            // connection they chose would be saved as null. Refused here as
            // well as by the disabled primary, and said again at the moment of
            // the attempt. NOT because implicit submission escapes the button:
            // HTML's implicit-submission algorithm does honour a disabled
            // default button, so Enter in a text field is already blocked.
            // Because the button is a gate on one door — `requestSubmit()`
            // does not consult it — and because a refusal to write belongs
            // with the code that would do the writing rather than with
            // whichever control happens to be in front of it.
            if (!connections) {
                say('bad', CONNECTIONS_FAILED);
                return;
            }

            await hireSubmit.run(async () => {
                say('busy', 'Saving...');
                // The line now says what is happening, so it is where the
                // keyboard goes when the button the operator just pressed is
                // taken away from them.
                const heldTheKeyboard = primary.saving(token, feedback.element);

                let savedAgent = null;
                try {
                    const { agentData, promptHistoryPolicy } = await BossModAgentSubmit.buildSubmitData(form, connections);
                    // THE INVARIANT, enforced once, on what would actually be
                    // SENT. Five UI routes each produced an agent with no
                    // connection on any activation type; each was fixed at the
                    // control that exposed it and the next appeared. This is the
                    // last point that still knows the save is a create — and
                    // buildSubmitData is not, because telling a form-level
                    // mapper about create-from-edit would push a dialog-level
                    // rule into it. CREATE ONLY, deliberately: an existing
                    // agent may already have none, and refusing that save would
                    // trap the operator in a dialog they cannot leave with
                    // their other edits.
                    if (isCreating && BossModAgentFields.MODEL_TYPES
                        .every(({ key }) => agentData[key] == null)) {
                        say('bad', noConnection(form));
                        return; // `finally` gives the primary back for the retry.
                    }
                    if (isCreating) {
                        savedAgent = await BossModAgentApi.apiCreateAgent(agentData);
                    } else {
                        savedAgent = await BossModAgentApi.apiUpdateAgent(currentAgentId, agentData);
                    }

                    try {
                        await BossModAgentApi.apiUpdatePromptHistoryPolicy(savedAgent.id, promptHistoryPolicy);
                    } catch (policyErr) {
                        console.error('[agent-form-save] Prompt history policy save failed:', policyErr);
                        say('warn', 'Agent saved, but AI history settings failed to save.');
                        if (onSave) onSave(savedAgent);
                        return;
                    }

                    say('ok', 'Saved successfully');
                    setTimeout(() => feedback.hide(), 3000);
                    if (onSave) onSave(savedAgent);
                } catch (err) {
                    console.error('[agent-form-save] Save failed:', err);
                    say('bad', err?.message || 'Save failed — check console for details');
                } finally {
                    // Unconditional, because the owner already knows every
                    // case this used to test for: a create that succeeded has
                    // closed the dialog, and a build that superseded this form
                    // holds the claim. Both refuse the write on their own.
                    primary.ready(token, heldTheKeyboard);
                }
            });
        });

        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                if (!currentAgentId) return;
                RECOVERY.confirmDestructive(
                    'Delete this agent?', 'This cannot be undone.', 'Delete agent',
                    () => {
                        void BossModAgentApi.apiDeleteAgent(currentAgentId)
                            .then(() => { if (onDelete) onDelete(); })
                            .catch((err) => {
                                console.error('[agent-form-save] Delete failed:', err);
                                say('bad', err?.message || 'Delete failed — check console for details');
                            });
                    });
            });
        }

        return { stage, connections, feedback };
    }

    /**
     * Render the form into `container` and own everything that happens after.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.container
     * @param {object|null} deps.agent   null to create.
     * @param {object} deps.primary      The dialog's primary owner, from
     *   context/agent-dialog-footer.js. Required: without one the form's build
     *   race and its save would each be writing to the button unsupervised,
     *   which is the defect this parameter exists to have removed.
     * @param {(saved?: object) => void} deps.onSave  Called with the saved
     *   agent after a create/update, and with nothing after a recovery action,
     *   which saves nothing about the agent itself.
     * @param {() => void} deps.onDelete
     * @returns {Promise<boolean>} Whether THIS render is the one that landed.
     *   `false` means a later render over the same dialog superseded it, and
     *   this call has written nothing to it — no form, no feedback, no
     *   primary. A caller that records what is on screen may only record it on
     *   `true`.
     * @throws {Error} Only when the form itself cannot render AND this render
     *   still holds the claim; a failed SAVE becomes the in-form feedback and
     *   the draft is kept.
     */
    async function renderInline({ container, agent, primary, onSave, onDelete }) {
        if (!primary) throw new Error('[agent-form-save] deps.primary is required');
        const token = primary.claim();
        // WITHHELD, not left live. The create dialog pins `Create Agent` when
        // the step swaps, which is BEFORE this build lands, and it submits
        // `#agent-form` by id — the PREVIOUS pick's form, still mounted and
        // still listening, so a click during the build created an agent from
        // the template the operator had just left. Disabled and relabelled
        // rather than the stale form torn down: a teardown empties the panel
        // and leaves a live button pointing at nothing, which is the silent
        // dead primary `failed()` exists to have removed, while this is the
        // same busy vocabulary the in-flight save already paints.
        const heldTheKeyboard = primary.building(token);
        let staged;
        try {
            staged = await stageForm({ agent, primary, token, onSave, onDelete });
        } catch (err) {
            // A superseded render must not reach the dialog, and that includes
            // its failure: the caller's handler clears the host that the
            // winning render has already filled. Reported, not swallowed.
            if (primary.holds(token)) throw err;
            console.error('[agent-form-save] a superseded render failed:', err);
            return false;
        }
        if (!primary.holds(token)) return false;

        container.replaceChildren(...staged.stage.children);
        // THREE ANSWERS, not two, and the middle one is new. `null` is a read
        // that did not land; `[]` is a read that landed on an empty Settings.
        // They were the same branch once — both fell through to "ready" unless
        // the read had failed — and an operator with no connection configured
        // was handed a live `Create Agent` over a form whose matrix was a link
        // to Settings. The agent that made would have failed on its first turn.
        //
        // CREATE ONLY for the empty case: an existing agent may already have no
        // connection, and withholding the primary there would trap the operator
        // in a dialog they cannot leave with their other edits — the same scope
        // the submit handler's own refusal carries.
        if (staged.connections === null) {
            staged.feedback.say('bad', CONNECTIONS_FAILED);
            // Withheld WITH a reason, and the keyboard goes to the reason: the
            // button cannot take it back while it is disabled, and a dialog
            // that opens with focus on its primary would otherwise strand it.
            primary.blocked(token, staged.feedback.element, heldTheKeyboard);
        } else if (!agent && !staged.connections.length) {
            staged.feedback.say('bad', NO_CONNECTIONS_CONFIGURED);
            primary.blocked(token, staged.feedback.element, heldTheKeyboard);
        } else {
            primary.ready(token, heldTheKeyboard);
        }
        return true;
    }

    return { renderInline };
})();
