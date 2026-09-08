/**
 * BossMod AI — the Need shape, and the engine event names that change it.
 *
 * Pure shaping: no store, no bus, no request. This is the ONE file in the
 * application that reads a snake_case backend key, which is what lets
 * test_ui_needs.py assert that nothing downstream of it ever sees one.
 *
 * Split out of needs-store.js so the conversion is testable without a fake bus
 * and so neither half approaches the ~300-line cap.
 */
const BossModNeedShape = (() => {

    /**
     * @typedef {object} NeedAction
     * @property {string} label
     * @property {string} href
     * @property {string} method  Upper-case. GET means inspect, not decide.
     * @property {string} tone
     */

    /**
     * @typedef {object} NeedTarget  Where "Show me" takes the operator.
     * @property {string} place            A place id from the registry.
     * @property {object} params           placeParams for that place.
     * @property {string|null} conversationId   Set for a chat target only.
     * @property {'agent'|'thread'|null} conversationKind
     */

    /**
     * @typedef {object} Need
     * @property {string} id
     * @property {'consent'|'approval'|'blocked'|'error'} kind
     * @property {string|null} agentId
     * @property {string} agentName
     * @property {string} title
     * @property {string} sub
     * @property {string} createdAt
     * @property {string|null} conversationId
     * @property {NeedAction[]} actions
     * @property {NeedTarget|null} target  Where to look. Null when a need has
     *   nowhere to send the operator — a consent with no conversation.
     * @property {string} [error]  Set only by a failed resolution.
     */

    /**
     * Which conversation kind a need's conversation is.
     *
     * api/routes/needs.py sets a need's conversation to the originating thread
     * when a consent came from one, and to the agent otherwise. So a
     * conversationId that is not the agent's own id is a thread id. Verified
     * against that file — the queue carries no conversation kind of its own.
     *
     * @param {string|null} conversationId
     * @param {string|null} agentId
     * @returns {'agent'|'thread'}
     */
    function conversationKind(conversationId, agentId) {
        return conversationId === agentId ? 'agent' : 'thread';
    }

    function chatTarget(conversationId, agentId) {
        if (!conversationId) return null;
        return {
            place: 'chat',
            params: {},
            conversationId,
            conversationKind: conversationKind(conversationId, agentId),
        };
    }

    /**
     * ONE table, keyed by kind, and the only place a kind decides anything
     * about where a need leads.
     *
     * Phase 2B performed the server-described GET for `blocked` and `error` and
     * refreshed, because Board and Log did not exist yet. They do now, so those
     * two inspections become navigations — without an `if (kind === …)` in the
     * popover or the bar, which is how four surfaces end up with four opinions.
     * The server-described `actions` are untouched: `target` is a client
     * concern and lives client-side.
     */
    const KIND_TARGETS = Object.freeze({
        blocked: (need) => ({ place: 'board', params: { taskId: need.id },
            conversationId: null, conversationKind: null }),
        error: (need) => ({ place: 'log', params: { diagnosticId: need.id },
            conversationId: null, conversationKind: null }),
        consent: (need) => chatTarget(need.conversationId, need.agentId),
        approval: (need) => chatTarget(need.conversationId, need.agentId),
    });

    /**
     * Where a need's "Show me" leads.
     *
     * @param {Need} need  Already camelCase.
     * @returns {NeedTarget|null} null for a kind with no mapping, which is a
     *   real answer — a fifth kind gets no navigation until it is given one,
     *   rather than a button that goes nowhere.
     */
    function targetFor(need) {
        const build = KIND_TARGETS[need.kind];
        if (!build) return null;
        return build(need);
    }

    /**
     * Activity event names that change what is waiting on the operator.
     *
     * Frozen, and every name is asserted to still be emitted somewhere in
     * `core/` or `api/` by test_api_needs.py — a renamed engine event must fail
     * a test rather than silently stale the queue (spec 5.4).
     *
     * The consent and approval names are `..._required`, not `..._requested`:
     * they are set in core/agent_loop/turn_helpers.py, actions_cli.py, and
     * core/bm_cli/results.py. The task half is the set that can move a task in
     * or out of `blocked` / `stalled`: an agent's block / complete / delegate
     * actions all broadcast the generic `status_changed`
     * (core/agent_loop/actions_lifecycle.py), retry exhaustion broadcasts
     * `task_stalled` (core/agent_loop/dispatcher.py, watchdog.py), and the
     * operator's own task routes broadcast the `task_*` names
     * (api/routes/tasks.py). There is no `task_blocked` event; a status check
     * on `task_*` alone would miss every agent-initiated block.
     *
     * An unrecognised name is ignored on purpose; the boot fetch and `resync`
     * bound how stale the queue can become if the engine adds one.
     */
    const ACTIVITY_TRIGGERS = Object.freeze([
        'host_path_consent_required',
        'host_path_consent_allowed_once',
        'host_path_consent_always_allowed',
        'host_path_consent_denied',
        'host_path_consent_resolved',
        'workspace_preference_required',
        'workspace_preference_cloned',
        'workspace_preference_branched',
        'workspace_preference_edit_host',
        'workspace_preference_cancelled',
        'cli_approval_required',
        'cli_approval_approved',
        'cli_approval_rejected',
        'cli_approval_resolved',
        'status_changed',
        'task_stalled',
        'task_created',
        'task_reused',
        'task_cancelled',
        'task_clarify',
    ]);

    /**
     * Normalise one server-described action.
     *
     * @param {object} raw
     * @returns {NeedAction}
     * @throws {Error} When the server described an action the client could not
     *   carry out. Rendering a button with no href is worse than reporting it.
     */
    function normaliseAction(raw) {
        if (!raw || !raw.label || !raw.href) {
            throw new Error('[need-shape] an action carries no label or href');
        }
        return {
            label: String(raw.label),
            href: String(raw.href),
            method: String(raw.method || 'POST').toUpperCase(),
            tone: String(raw.tone || 'default'),
        };
    }

    /**
     * Normalise one `GET /api/needs` row to the camelCase Need shape.
     *
     * @param {object} raw
     * @returns {Need}
     * @throws {Error} On a row with no id or kind: it could neither be deduped
     *   nor resolved, and dropping it quietly would hide a real backend change.
     */
    function normalise(raw) {
        if (!raw || !raw.id || !raw.kind) {
            throw new Error('[need-shape] a queue row carries no id or kind');
        }
        const need = {
            id: String(raw.id),
            kind: String(raw.kind),
            // A blocked task can legitimately be unassigned, so null is a real
            // value here rather than a swallowed failure.
            agentId: raw.agent_id == null ? null : String(raw.agent_id),
            agentName: String(raw.agent_name || 'Unknown'),
            title: String(raw.title || ''),
            sub: String(raw.sub || ''),
            createdAt: String(raw.created_at || ''),
            conversationId: raw.conversation_id == null ? null : String(raw.conversation_id),
            actions: (Array.isArray(raw.actions) ? raw.actions : []).map(normaliseAction),
        };
        need.target = targetFor(need);
        return need;
    }

    /**
     * Turn one `diagnostic` broadcast into an error Need.
     *
     * The predicate is `error` being non-empty, NOT `status`: the dispatcher's
     * crash path (core/agent_loop/dispatcher.py) sets `error` and leaves
     * `status` at its "success" default, so a status check would miss exactly
     * the failures that matter most (spec 5.1).
     *
     * @param {object} data  The broadcast payload.
     * @returns {Need|null} null when the diagnostic reports no error — that is
     *   a real answer about a healthy turn, not a swallowed failure. Most
     *   diagnostics are healthy, so this is the common case.
     */
    function normaliseDiagnostic(data) {
        if (!data || !data.id) return null;
        const message = typeof data.error === 'string' ? data.error.trim() : '';
        if (!message) return null;
        const id = String(data.id);
        const name = String(data.agent_name || 'An agent');
        const agentId = data.agent_id == null ? null : String(data.agent_id);
        const need = {
            id,
            kind: 'error',
            agentId,
            agentName: name,
            title: `${name} hit an error`,
            sub: message,
            createdAt: String(data.created_at || ''),
            // The agent's own conversation is where "show me" lands: an error
            // is something you take up with the agent that hit it.
            conversationId: agentId,
            actions: [{
                label: 'Open diagnostics',
                // GET /api/diagnostics/{id} — verified present in
                // api/routes/runtime.py before this href was written.
                href: `/api/diagnostics/${id}`,
                method: 'GET',
                tone: 'primary',
            }],
        };
        need.target = targetFor(need);
        return need;
    }

    return { ACTIVITY_TRIGGERS, KIND_TARGETS, normalise, normaliseDiagnostic, targetFor };
})();
