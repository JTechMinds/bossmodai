/**
 * BossMod AI — how approval settings show: the "Auto-approve safe commands"
 * switch, the Yolo mode switch, and the header's Yolo badge, as chrome
 * descriptors, for a thread and for an agent.
 *
 * A thread (thread-source.js) and an agent each have their own auto-approve
 * and Yolo flags — an agent's cover its DM and its tasks not started in a
 * thread — and Settings → Advanced has one global auto-approve flag over
 * both. The rule for how they combine lives here once, so the two kinds
 * cannot drift apart, and it mirrors the server's resolver
 * (core/bm_cli/approval_gate/gate.py `approval_mode`): Yolo outranks Global
 * auto-approve, which outranks the own flag. The server keeps every flag
 * rather than overwriting one with another, so turning the winner off brings
 * each one's own choice back.
 *
 * While something above the own auto-approve flag decides, that switch shows
 * ON (Yolo approves everything auto-approve would), is disabled (changing it
 * would do nothing), and its hint says what decides and where to change it.
 *
 * Both kinds get both switches here. Yolo sits in the `⋯`'s Advanced
 * section, away from the everyday settings, and asks before it goes on
 * (core/yolo-mode.js). A thread's auto-approve sits in its Chat section,
 * where it is that thread's flag. An agent's two switches belong to the
 * agent `⋯` (context/agent-menu.js), which places them on both surfaces that
 * show it — the DM header and the desk head — with auto-approve in the Agent
 * section, saying its scope the way Yolo does.
 *
 * Data only, like every source: chrome.js and context/desk-menu.js build the
 * controls.
 */
const BossModApprovalControls = (() => {

    const AUTO_APPROVE_LABEL = 'Auto-approve safe commands';

    /** Shown under a disabled switch: where the setting that wins lives. */
    const GLOBAL_HINT = 'On for all conversations (Settings → Advanced)';

    /**
     * The same "what + where to change it" line, when Yolo is what wins. One
     * line for both kinds: Yolo sits in Advanced, below, on every surface.
     */
    const YOLO_HINT = 'Yolo mode is on (Advanced, below)';

    function requireCallback(name, fn) {
        if (typeof fn !== 'function') throw new Error(`[approval-controls] ${name} is required`);
    }

    /**
     * The auto-approve switch, with Yolo and Global outranking its own flag.
     *
     * @param {{id: string, section: string, scopeHint: string, own: boolean,
     *   global: boolean, yolo: boolean,
     *   onSelect: (enabled: boolean) => Promise<void>}} opts  `scopeHint` is
     *   the line shown while the own flag decides ('' for none).
     * @returns {object} A `kind: 'switch'` menu action in `section`.
     */
    function autoApproveSwitch({ id, section, scopeHint, own, global, yolo, onSelect }) {
        return {
            id,
            kind: 'switch',
            slot: 'menu',
            section,
            label: AUTO_APPROVE_LABEL,
            pressed: yolo || global || own,
            disabled: yolo || global,
            hint: yolo ? YOLO_HINT : (global ? GLOBAL_HINT : scopeHint),
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
                    section: 'chat',
                    scopeHint: '',
                    own: channel.cli_auto_approve === true,
                    global: channel.cli_auto_approve_global === true,
                    yolo,
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
     * An agent's approval controls, for its DM and its tasks not started in
     * a thread.
     *
     * @param {object} record  The agent payload the server last answered
     *   with: its `cli_auto_approve_dm`, `cli_auto_approve_global` and
     *   `cli_yolo_dm`.
     * @param {object} handlers
     * @param {(enabled: boolean) => Promise<void>} handlers.onAutoApprove
     *   Saves the agent's auto-approve flag. Never called while that switch
     *   is disabled.
     * @param {(enabled: boolean) => Promise<void>} handlers.onYolo  Saves the
     *   agent's Yolo flag. Turning it on is confirmed first; a cancelled
     *   confirm never calls it.
     * @returns {{actions: object[], badge: {label: string, icon: string}|null}}
     *   The auto-approve switch (the Agent section, with Yolo's scope line)
     *   and the Yolo switch (Advanced), and the header badge while the
     *   agent's Yolo is on.
     * @throws {Error} When `record` or a handler is missing.
     */
    function forAgent(record, handlers) {
        if (!record) throw new Error('[approval-controls] forAgent needs the agent record');
        const { onAutoApprove, onYolo } = handlers || {};
        requireCallback('handlers.onAutoApprove', onAutoApprove);
        requireCallback('handlers.onYolo', onYolo);
        const yolo = record.cli_yolo_dm === true;
        return {
            actions: [
                autoApproveSwitch({
                    id: 'agent-cli-auto-approve',
                    // The agent's flag, not one chat's: it sits with the
                    // agent, and says whom it covers, as Yolo's does.
                    section: 'subject',
                    scopeHint: BossModYoloMode.HINTS.agent,
                    own: record.cli_auto_approve_dm === true,
                    global: record.cli_auto_approve_global === true,
                    yolo,
                    onSelect: onAutoApprove,
                }),
                {
                    id: 'agent-cli-yolo',
                    kind: 'switch',
                    slot: 'menu',
                    section: 'advanced',
                    label: BossModYoloMode.LABEL,
                    pressed: yolo,
                    hint: BossModYoloMode.HINTS.agent,
                    // The thread's rule: on asks first, off never does.
                    onSelect: async (enabled) => {
                        if (enabled && !(await BossModYoloMode.confirmEnable('agent'))) return;
                        await onYolo(enabled);
                    },
                },
            ],
            badge: yolo ? BossModYoloMode.BADGE : null,
        };
    }

    return { forThread, forAgent, GLOBAL_HINT, YOLO_HINT };
})();
