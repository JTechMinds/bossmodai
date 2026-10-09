/**
 * BossMod AI — the "Auto-approve safe commands" switch a conversation's `⋯`
 * carries, as a chrome action descriptor.
 *
 * A thread (thread-source.js) and a DM (agent-source.js) each have their own
 * flag, and Settings → Advanced has one global flag over both. The rule for
 * how the two combine lives here once, so the thread switch and the DM switch
 * cannot drift apart: the server resolves auto-approve with the same rule
 * (core/bm_cli/approval_gate/gate.py `auto_approve_effective`).
 *
 * While the global flag is on, the switch shows ON (that is what applies),
 * is disabled (changing the conversation's own flag would do nothing), and
 * says why in a hint. The conversation's own flag is kept on the server, so
 * turning the global flag off brings each conversation's choice back.
 *
 * Data only, like every source: chrome.js builds the control.
 */
const BossModAutoApproveSwitch = (() => {

    const LABEL = 'Auto-approve safe commands';

    /** Shown under a disabled switch: where the setting that wins lives. */
    const GLOBAL_HINT = 'On for all conversations (Settings → Advanced)';

    /**
     * Build the menu switch descriptor for one conversation.
     *
     * @param {object} opts
     * @param {string} opts.id  The control's DOM id.
     * @param {boolean} opts.enabled  The conversation's own flag.
     * @param {boolean} opts.globalEnabled  Whether Global auto-approve is on.
     * @param {(enabled: boolean) => Promise<void>} opts.onSelect  Saves the
     *   conversation's own flag. Never called while the switch is disabled.
     * @returns {{id: string, kind: 'switch', slot: 'menu', section: 'chat', label: string,
     *   pressed: boolean, disabled: boolean, hint: string,
     *   onSelect: (enabled: boolean) => Promise<void>}}
     * @throws {Error} When `id` or `onSelect` is missing: either one absent is
     *   a switch that looks live and is not.
     */
    function describe(opts) {
        const id = opts && opts.id;
        const onSelect = opts && opts.onSelect;
        if (!id) throw new Error('[auto-approve-switch] opts.id is required');
        if (typeof onSelect !== 'function') {
            throw new Error('[auto-approve-switch] opts.onSelect is required');
        }
        const globalOn = opts.globalEnabled === true;
        return {
            id,
            kind: 'switch',
            slot: 'menu',
            section: 'chat',
            label: LABEL,
            pressed: globalOn || opts.enabled === true,
            disabled: globalOn,
            hint: globalOn ? GLOBAL_HINT : '',
            onSelect,
        };
    }

    return { describe, LABEL, GLOBAL_HINT };
})();
