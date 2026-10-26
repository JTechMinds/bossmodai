/**
 * BossMod AI — how a conversation shows its approval settings: the
 * "Auto-approve safe commands" switch, a thread's Yolo mode switch, and the
 * header's Yolo badge, as chrome descriptors.
 *
 * A thread (thread-source.js) and a DM (agent-source.js) each have their own
 * auto-approve and Yolo flags, and Settings → Advanced has one global
 * auto-approve flag over both. The rule for how they combine lives here once,
 * so the thread and the DM cannot drift apart, and it mirrors the server's
 * resolver (core/bm_cli/approval_gate/gate.py `approval_mode`): Yolo outranks
 * Global auto-approve, which outranks the conversation's own flag. The server
 * keeps every flag rather than overwriting one with another, so turning the
 * winner off brings each conversation's own choice back.
 *
 * While something above the conversation's own auto-approve flag decides,
 * that switch shows ON (Yolo approves everything auto-approve would), is
 * disabled (changing it would do nothing), and its hint says what decides
 * and where to change it.
 *
 * A thread's Yolo switch sits in the `⋯`'s Advanced section, away from the
 * everyday settings, and asks before it goes on (core/yolo-mode.js). A DM has
 * no Yolo switch: an agent's Yolo is its desk `⋯`'s (context/desk-menu.js),
 * and the DM shows only its effect — the badge and the greyed switch.
 *
 * Data only, like every source: chrome.js builds the controls.
 */
const BossModApprovalControls = (() => {

    const AUTO_APPROVE_LABEL = 'Auto-approve safe commands';

    /** Shown under a disabled switch: where the setting that wins lives. */
    const GLOBAL_HINT = 'On for all conversations (Settings → Advanced)';

    /** The same "what + where to change it" line, when Yolo is what wins. */
    const YOLO_HINTS = Object.freeze({
        thread: 'Yolo mode is on (Advanced, below)',
        dm: 'Yolo mode is on (this agent’s desk)',
    });

    function requireCallback(name, fn) {
        if (typeof fn !== 'function') throw new Error(`[approval-controls] ${name} is required`);
    }

    /**
     * The auto-approve switch, with Yolo and Global outranking its own flag.
     *
     * @param {{id: string, own: boolean, global: boolean, yolo: boolean,
     *   yoloHint: string, onSelect: (enabled: boolean) => Promise<void>}} opts
     * @returns {object} A `kind: 'switch'` menu action in the Chat section.
     */
    function autoApproveSwitch({ id, own, global, yolo, yoloHint, onSelect }) {
        return {
            id,
            kind: 'switch',
            slot: 'menu',
            section: 'chat',
            label: AUTO_APPROVE_LABEL,
            pressed: yolo || global || own,
            disabled: yolo || global,
            hint: yolo ? yoloHint : (global ? GLOBAL_HINT : ''),
            onSelect,
        };
    }

    /**
     * A thread's approval controls.
     *
     * @param {object} channel  The channel summary the server last answered
     *   with: its `cli_auto_approve`, `cli_auto_approve_global` and `cli_yolo`.
     * @param {object} handlers
     * @param {(enabled: boolean) => Promise<void>} handlers.onAutoApprove
     *   Saves the thread's auto-approve flag. Never called while that switch
     *   is disabled.
     * @param {(enabled: boolean) => Promise<void>} handlers.onYolo  Saves the
     *   thread's Yolo flag. Turning it on is confirmed first; a cancelled
     *   confirm never calls it.
     * @returns {{actions: object[], badge: {label: string, icon: string}|null}}
     *   The auto-approve switch (Chat) and the Yolo switch (Advanced), and the
     *   header badge while the thread's Yolo is on.
     * @throws {Error} When `channel` or a handler is missing: a switch with no
     *   state or no saver looks live and is not.
     */
    function forThread(channel, handlers) {
        if (!channel) throw new Error('[approval-controls] forThread needs the channel');
        const { onAutoApprove, onYolo } = handlers || {};
        requireCallback('handlers.onAutoApprove', onAutoApprove);
        requireCallback('handlers.onYolo', onYolo);
        const yolo = channel.cli_yolo === true;
        return {
            actions: [
                autoApproveSwitch({
                    id: 'channel-cli-auto-approve',
                    own: channel.cli_auto_approve === true,
                    global: channel.cli_auto_approve_global === true,
                    yolo,
                    yoloHint: YOLO_HINTS.thread,
                    onSelect: onAutoApprove,
                }),
                {
                    id: 'channel-cli-yolo',
                    kind: 'switch',
                    slot: 'menu',
                    section: 'advanced',
                    label: BossModYoloMode.LABEL,
                    pressed: yolo,
                    hint: BossModYoloMode.HINTS.thread,
                    // On asks first; off never does, because it puts the
                    // safeguards back. A cancel leaves the flag alone, and the
                    // repaint after it puts the pill back.
                    onSelect: async (enabled) => {
                        if (enabled && !(await BossModYoloMode.confirmEnable('thread'))) return;
                        await onYolo(enabled);
                    },
                },
            ],
            badge: yolo ? BossModYoloMode.BADGE : null,
        };
    }

    /**
     * A DM's approval controls.
     *
     * @param {object} record  The agent payload the server last answered
     *   with: its `cli_auto_approve_dm`, `cli_auto_approve_global` and
     *   `cli_yolo_dm`.
     * @param {object} handlers
     * @param {(enabled: boolean) => Promise<void>} handlers.onAutoApprove
     *   Saves the agent's DM auto-approve flag. Never called while that switch
     *   is disabled.
     * @returns {{actions: object[], badge: {label: string, icon: string}|null}}
     *   The auto-approve switch only (the agent's Yolo switch is its desk's),
     *   and the header badge while the agent's Yolo is on.
     * @throws {Error} When `record` or the handler is missing.
     */
    function forAgent(record, handlers) {
        if (!record) throw new Error('[approval-controls] forAgent needs the agent record');
        const { onAutoApprove } = handlers || {};
        requireCallback('handlers.onAutoApprove', onAutoApprove);
        const yolo = record.cli_yolo_dm === true;
        return {
            actions: [autoApproveSwitch({
                id: 'agent-cli-auto-approve',
                own: record.cli_auto_approve_dm === true,
                global: record.cli_auto_approve_global === true,
                yolo,
                yoloHint: YOLO_HINTS.dm,
                onSelect: onAutoApprove,
            })],
            badge: yolo ? BossModYoloMode.BADGE : null,
        };
    }

    return { forThread, forAgent, GLOBAL_HINT, YOLO_HINTS };
})();
