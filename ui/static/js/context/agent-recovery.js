/**
 * BossMod AI — the agent form's feedback line and its destructive tools.
 *
 * Split from context/agent-edit.js, which owns saving. This owns telling the
 * operator what happened and the two irreversible actions that are not a save
 * at all: clearing an agent's chat history and resetting their runtime.
 *
 * Both are guarded by the shell's own dialog rather than `window.confirm`.
 * That is the last of the native dialogs outside settings/, and it goes for
 * the reason core/overlays.js exists: the browser's cannot be styled, cannot
 * be tested, and blocks the event loop. The guard itself stays — these two
 * and Delete are the three actions in the editor that cannot be undone.
 */
const BossModAgentRecovery = (() => {

    /** Feedback tones, and the utility classes each one paints. */
    const TONES = Object.freeze({
        busy: 'bg-slate-50 border border-bm-border text-bm-muted',
        ok: 'bg-emerald-50 border border-emerald-200 text-emerald-700',
        warn: 'bg-amber-50 border border-amber-200 text-amber-800',
        bad: 'bg-red-50 border border-red-200 text-red-700',
    });

    /**
     * Add the one feedback line the whole editor reports through.
     *
     * A LIVE REGION, and a focus target. Everything this line says is a status
     * message the operator did not ask for — a save in flight, a save refused,
     * a form that cannot save at all — so it is announced where it changes
     * (WCAG 2.2 SC 4.1.3) rather than only being visible. `tabindex="-1"` is
     * the second half of that: context/agent-dialog-footer.js hands the
     * keyboard to this line whenever it takes the primary away, because
     * disabling the button the operator is standing on drops focus to <body>
     * and the reason it was withheld is exactly what they need next.
     *
     * @param {HTMLElement} form  The line is appended after the actions row.
     * @returns {{element: HTMLElement, say: (tone: string, text: string) => void,
     *            hide: () => void}}
     * @throws {Error} On an unknown tone — a typo must not paint an unstyled
     *   bar that reads as neither success nor failure.
     */
    function createFeedback(form) {
        const element = document.createElement('div');
        element.id = 'agent-save-feedback';
        element.className = 'hidden mt-3 p-3 rounded-lg text-sm';
        element.setAttribute('role', 'status');
        element.setAttribute('aria-live', 'polite');
        // The whole line is one message; a partial read of a changed sentence
        // is worse than hearing it again.
        element.setAttribute('aria-atomic', 'true');
        element.setAttribute('tabindex', '-1');
        form.appendChild(element);
        return {
            element,
            say(tone, text) {
                if (!TONES[tone]) throw new Error(`[agent-recovery] unknown tone "${tone}"`);
                element.className = `mt-3 p-3 rounded-lg text-sm ${TONES[tone]}`;
                element.textContent = text;
            },
            hide() { element.className = 'hidden'; },
        };
    }

    /**
     * Guard an irreversible action behind a focus-trapped, Esc-dismissible
     * dialog.
     *
     * @param {string} title
     * @param {string} body
     * @param {string} confirmLabel
     * @param {() => void} onConfirm  Runs only on the destructive choice; Esc
     *   and "Keep it" resolve to nothing happening.
     * @returns {void}
     */
    function confirmDestructive(title, body, confirmLabel, onConfirm) {
        BossModOverlays.createModal({
            title,
            body,
            actions: [
                { label: confirmLabel, tone: 'danger', onSelect: () => onConfirm() },
                { label: 'Keep it', tone: 'quiet' },
            ],
        });
    }

    /**
     * Wire Clear Chat History and Reset Runtime.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.form  The `<form>` the two buttons live in —
     *   never the host it is published into. Publication MOVES the form out of
     *   that host and empties it, so a host-rooted lookup finds nothing (see
     *   the header of context/agent-form.js). Named `form` rather than
     *   `container` for exactly that reason: a parameter that invites a host
     *   is how the wrong node gets passed again.
     * @param {() => string|null} deps.agentId  Read at click time, not bound
     *   at wiring time: the form is re-rendered for a different agent without
     *   rebuilding these handlers.
     * @param {object} deps.feedback  From createFeedback.
     * @param {() => void} [deps.onSave]  Neither action saves the agent, but
     *   both change what the desk shows, so the host still refreshes.
     * @returns {void}
     */
    function bindRecoveryTools(deps) {
        const { form, agentId, feedback, onSave } = deps || {};
        if (!form) throw new Error('[agent-recovery] deps.form is required');
        if (typeof agentId !== 'function') throw new Error('[agent-recovery] deps.agentId is required');
        if (!feedback) throw new Error('[agent-recovery] deps.feedback is required');

        function bind(button, copy, run) {
            if (!button) return;
            button.addEventListener('click', () => {
                const id = agentId();
                if (!id) return;
                confirmDestructive(copy.title, copy.body, copy.confirmLabel, () => {
                    feedback.say('busy', copy.busy);
                    void run(id).then((result) => {
                        feedback.say('ok', copy.done(result));
                        if (onSave) onSave();
                    }).catch((err) => {
                        console.error(`[agent-recovery] ${copy.confirmLabel} failed:`, err);
                        feedback.say('bad', copy.failed);
                    });
                });
            });
        }

        bind(form.querySelector('#btn-clear-chat-history'), {
            title: 'Clear this chat history?',
            body: "This permanently deletes this agent's direct chat history with the human operator. Completed work, artifacts, and diagnostics are preserved.",
            confirmLabel: 'Clear chat history',
            busy: 'Clearing chat history...',
            done: (r) => `Cleared ${r.deleted_messages} chat message${r.deleted_messages === 1 ? '' : 's'}.`,
            failed: 'Clear chat failed — check console for details',
        }, BossModAgentApi.apiClearChatHistory);

        bind(form.querySelector('#btn-reset-runtime'), {
            title: 'Reset this agent runtime?',
            body: 'This forcibly resets the agent runtime, clears queued triggers, and may block the active task. Completed work history is preserved.',
            confirmLabel: 'Reset runtime',
            busy: 'Resetting runtime...',
            done: (r) => `Runtime reset. Cleared ${r.deleted_triggers} open trigger${r.deleted_triggers === 1 ? '' : 's'}.`,
            failed: 'Runtime reset failed — check console for details',
        }, BossModAgentApi.apiResetRuntime);
    }

    return { createFeedback, confirmDestructive, bindRecoveryTools };
})();
