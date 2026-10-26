/**
 * BossMod AI — Yolo mode's words and its one warning.
 *
 * Yolo mode lets agents run commands without stopping for an Approve card or
 * a System AI review (core/bm_cli/approval_gate/gate.py `approval_mode`).
 * Never-allowed rules, the path jail and every consent card still apply.
 *
 * It is switched in two places — a thread's `⋯` → Advanced
 * (conversation/approval-controls.js) and an agent's desk `⋯` → Advanced
 * (context/desk-menu.js) — and both name it, explain it and warn before it
 * goes on in the same words. That is the second occurrence, so the words and
 * the warning live here once rather than drifting apart in two menus.
 *
 * Turning it OFF never asks: that puts the safeguards back.
 */
const BossModYoloMode = (() => {

    /** The switch's label, in both menus. */
    const LABEL = 'Yolo mode';

    /**
     * The conversation header's badge while Yolo applies. The glyph is
     * already in the vendored Lucide subset.
     */
    const BADGE = Object.freeze({ label: 'Yolo mode', icon: 'alert-triangle' });

    /** The line under each switch: who it covers. */
    const HINTS = Object.freeze({
        thread: 'Agents here run commands without asking you',
        agent: 'For its DM and tasks not started in a thread',
    });

    const TITLE = 'Turn on Yolo mode?';
    const CONFIRM = 'Turn on Yolo mode';
    const BODIES = Object.freeze({
        thread: "Agents in this thread won't stop to ask before running commands. "
            + 'That includes commands that delete files, install packages or push code.',
        agent: "This agent won't stop to ask before running commands in its DM or in tasks "
            + "that didn't start in a thread. That includes commands that delete files, "
            + 'install packages or push code.',
    });

    /**
     * Ask before Yolo mode goes on.
     *
     * A modal layer (core/overlays.js) whose body holds nothing focusable, so
     * focus lands on its LAST action — Cancel, the safe default, as in the
     * desk's other confirms (context/desk-actions.js `confirmThen`).
     *
     * @param {'thread'|'agent'} scope  What is being switched: a thread, or
     *   an agent's DM and its work with no origin thread. Picks the body copy.
     * @returns {Promise<boolean>} True only when the operator chose "Turn on
     *   Yolo mode"; Cancel, Esc, ✕ and a backdrop click resolve false. It
     *   resolves once the dialog has closed, however it closed.
     * @throws {Error} On an unknown scope — a warning that names the wrong
     *   thing is worse than none.
     */
    function confirmEnable(scope) {
        if (!Object.prototype.hasOwnProperty.call(BODIES, scope)) {
            throw new Error(`[yolo-mode] unknown scope "${scope}"`);
        }
        return new Promise((resolve) => {
            let chosen = false;
            BossModOverlays.createModal({
                title: TITLE,
                body: BODIES[scope],
                closeOnBackdrop: true,
                actions: [
                    { label: CONFIRM, tone: 'danger', onSelect: () => { chosen = true; } },
                    { label: 'Cancel', tone: 'quiet' },
                ],
                // Runs after the chosen action's onSelect, however the dialog
                // closed: a choice not recorded by then is a dismissal.
                onClose: () => resolve(chosen),
            });
        });
    }

    return { LABEL, BADGE, HINTS, confirmEnable };
})();
