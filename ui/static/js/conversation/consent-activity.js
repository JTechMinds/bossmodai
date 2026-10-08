/**
 * BossMod AI — which inline consent or approval cards an `activity` broadcast
 * settles. A pure mapping: no DOM, no state. The conversation hands each card
 * to BossModConsentCard.collapseGrantedConsentCards.
 */
const BossModConsentActivity = (() => {
    const CLI_APPROVAL_EVENTS = ['cli_approval_approved', 'cli_approval_rejected', 'cli_approval_resolved'];

    /**
     * The consent or approval cards one `activity` entry settles.
     *
     * `shell_executor_enabled` settles the Shell Executor consent card: the
     * entry's own `host_path_consent` when the server sent it, else one built
     * from the event. A `cli_approval_*` event settles the CLI approval card
     * for its command and cwd; rejected is always `'rejected'`, the others
     * take `entry.status`, defaulting to `'approved'`.
     * @param {object|null|undefined} entry  An `activity` bus payload.
     * @returns {object[]} Cards to collapse; `[]` for any other event or a
     *   missing entry. Never throws.
     */
    function settledCards(entry) {
        const event = String((entry && entry.event) || '');
        if (event === 'shell_executor_enabled') {
            return [entry.host_path_consent || {
                status: 'enabled',
                kind: 'shell_executor',
                grant_root: 'cli_shell_enabled',
                decision_note: entry.detail,
            }];
        }
        if (CLI_APPROVAL_EVENTS.includes(event)) {
            return [{
                kind: 'cli_approval',
                status: event === 'cli_approval_rejected'
                    ? 'rejected'
                    : ((entry && entry.status) || 'approved'),
                command: (entry && entry.command) || '',
                cwd: (entry && entry.cwd) || '',
                decision_note: (entry && entry.decision_note) || '',
            }];
        }
        return [];
    }

    return { settledCards };
})();
