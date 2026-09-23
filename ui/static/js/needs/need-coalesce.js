/**
 * BossMod AI — collapsing identical needs into one live card.
 *
 * THE SEAM. needs/need-shape.js turns ONE backend row into a Need and is the
 * only file that reads a snake_case key. This module works a level up, on a
 * LIST of Needs that are already camelCase: it decides which of them are the
 * same ask and folds each group into a single card with a count. Pure — no
 * store, no bus, no request — and it never sees a wire field.
 *
 * Split out of need-shape.js so the per-row conversion and the list-level
 * grouping can change independently; needs-store.js is the one caller.
 */
const BossModNeedCoalesce = (() => {

    /**
     * Identity used to coalesce identical live error, CLI-approval, and consent cards.
     *
     * Errors group by agent and message. Approvals group by agent and command.
     * Other kinds stay one-id-one-need.
     *
     * @param {Need} need
     * @returns {string}
     */
    function coalesceKey(need) {
        if (!need) return '';
        if (need.kind === 'error') return `error:${need.agentId || ''}:${need.sub || ''}`;
        if (need.kind === 'approval') {
            return `approval:${need.agentId || ''}:${need.sub || ''}:${need.cwd || ''}`;
        }
        if (need.kind === 'consent') {
            const flavor = need.cardKind || 'host_path';
            if (flavor === 'shell_executor') {
                return `consent:shell:${need.conversationId || need.agentId || ''}`;
            }
            return `consent:${flavor}:${need.conversationId || ''}:${need.sub || ''}`;
        }
        return String(need.id || '');
    }

    /**
     * Collapse identical error, CLI-approval, and consent needs into one live card.
     *
     * Diagnostics stay individual in the log. Duplicate pending Approves for
     * the same command, and identical consent waits, are one ask.
     *
     * @param {Need[]} needs
     * @returns {Need[]}
     */
    function coalesceNeeds(needs) {
        const list = Array.isArray(needs) ? needs : [];
        const others = [];
        const groups = new Map();
        list.forEach((need) => {
            if (!need || (need.kind !== 'error' && need.kind !== 'approval' && need.kind !== 'consent')) {
                others.push(need);
                return;
            }
            const key = coalesceKey(need);
            const existing = groups.get(key);
            if (!existing) {
                groups.set(key, Object.assign({}, need, {
                    count: 1,
                    groupedIds: need.groupedIds && need.groupedIds.length
                        ? need.groupedIds.slice()
                        : [need.id],
                }));
                return;
            }
            const incomingIsNewer = String(need.createdAt || '')
                .localeCompare(String(existing.createdAt || '')) >= 0;
            const live = incomingIsNewer ? need : existing;
            const count = (existing.count || 1) + 1;
            const groupedIds = (existing.groupedIds || [existing.id]).concat(
                need.groupedIds && need.groupedIds.length ? need.groupedIds : [need.id],
            );
            const title = count > 1 && live.kind === 'error'
                ? `${live.agentName} hit an error ×${count}`
                : live.title;
            groups.set(key, Object.assign({}, live, { count, groupedIds, title }));
        });
        return others.concat(Array.from(groups.values()));
    }

    return { coalesceKey, coalesceNeeds };
})();
