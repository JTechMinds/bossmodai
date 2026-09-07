/**
 * BossMod AI — the LogRow shape, and the only file that reads a backend key.
 *
 * Activity entries and diagnostic summaries share almost no fields: one has
 * `timestamp`, `category`, `title` and an `agent_name` with no id; the other
 * has `created_at`, `status`, `action_name`, `error` and both an id and a name.
 * They are normalised HERE, once, into one LogRow — the same thing
 * conversation/sources/ does, and for the same reason: the last time this
 * codebase let three renderers each know their own wire format, it needed three
 * of everything.
 *
 * Downstream of this file nothing sees a snake_case key, and log-row.js cannot
 * tell which feed a row came from.
 */
const BossModLogShape = (() => {

    /**
     * @typedef {object} LogFact
     * @property {string} label
     * @property {string} value
     */

    /**
     * @typedef {object} LogRow
     * @property {string}  key        Stable dedupe key across both feeds.
     * @property {string}  at         ISO-8601, or '' when the feed gave none.
     * @property {string}  agentId    '' when the feed carries no id — the
     *   unified activity feed identifies agents by name alone.
     * @property {string}  agentName
     * @property {'agent'|'task'|'error'|'system'} type
     * @property {string}  text       The row's one line.
     * @property {string}  meta       The dimmer second line.
     * @property {boolean} active     A still-running activity.
     * @property {boolean} expandable Whether this row has more to show.
     * @property {string|null} diagnosticId  Non-null means the expansion has an
     *   execution trace to fetch as well as the facts already here.
     * @property {LogFact[]} facts    Detail already in hand, shown on expand.
     * @property {string} json        Pretty-printed extras, or '' when there
     *   are none. The activity feed's metadata carries keys no fact names, and
     *   dropping them would lose what the dock-era detail block showed.
     */

    /** The four types the filter bar offers. */
    const TYPES = Object.freeze(['agent', 'task', 'error', 'system']);

    const SOURCE_LABELS = Object.freeze({
        activity_log: 'Activity Log',
        activity: 'Runtime Activity',
        notification: 'Notification',
        diagnostic: 'Agent turn',
    });

    const TRIGGER_LABELS = Object.freeze({
        human_chat: 'Human Chat',
        peer_message: 'Peer Message',
        watchdog_status_ping: 'Watchdog',
        activity_resumed: 'Activity Resumed',
        task_assigned: 'Task Assigned',
        social: 'Social',
    });

    /** Longest trigger preview shown on a collapsed row. */
    const PREVIEW_CHARS = 120;

    /**
     * @param {string} source
     * @returns {string}
     */
    function sourceLabel(source) {
        return SOURCE_LABELS[source] || String(source || 'Unknown');
    }

    /**
     * @param {string} triggerType
     * @returns {string}
     */
    function triggerLabel(triggerType) {
        return TRIGGER_LABELS[triggerType] || String(triggerType || 'Trigger');
    }

    /**
     * The first readable line of a diagnostic's trigger payload.
     *
     * @param {string|object} raw  `trigger_data`, JSON text or already parsed.
     * @returns {string} '' when there is nothing to preview — a real answer for
     *   a watchdog ping, not a swallowed parse failure.
     */
    function triggerPreview(raw) {
        let parsed;
        try {
            parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
        } catch (err) {
            // Trigger payloads are written by the engine and are occasionally
            // plain text; that is not an error worth a console line every row.
            return typeof raw === 'string' ? clip(raw) : '';
        }
        if (!parsed || typeof parsed !== 'object') return '';
        const text = parsed.content || parsed.task_title || parsed.task_description || '';
        return clip(String(text));
    }

    /**
     * Pretty-print a metadata object, or '' when it holds nothing visible.
     *
     * Ported from activity.js's detail block, including its rule for "visible":
     * a key whose value is null or empty is not worth a line.
     *
     * @param {object} meta
     * @returns {string}
     */
    function formatMeta(meta) {
        if (!meta || typeof meta !== 'object') return '';
        const visible = Object.keys(meta)
            .filter((key) => meta[key] !== null && meta[key] !== undefined && meta[key] !== '');
        if (visible.length === 0) return '';
        return JSON.stringify(meta, null, 2);
    }

    function clip(text) {
        const value = String(text || '');
        return value.length > PREVIEW_CHARS ? `${value.slice(0, PREVIEW_CHARS - 3)}...` : value;
    }

    function fact(label, value) {
        return (value === null || value === undefined || value === '')
            ? null
            : { label, value: String(value) };
    }

    /**
     * One unified-feed entry as a LogRow.
     *
     * @param {object} entry
     * @returns {LogRow}
     * @throws {Error} On an entry with no id: it could not be deduped, and
     *   dropping it quietly would hide a backend change.
     */
    function fromActivity(entry) {
        if (!entry || entry.id === undefined || entry.id === null) {
            throw new Error('[log-shape] an activity entry carries no id');
        }
        const category = String(entry.category || 'system');
        const meta = entry.metadata && typeof entry.metadata === 'object' ? entry.metadata : {};
        const facts = [
            fact('Source', sourceLabel(entry.source)),
            fact('Type', entry.event),
            fact('Category', category),
            fact('Detail', entry.detail),
            fact('Task ID', entry.task_id),
            fact('Channel', meta.source_channel),
            fact('Status', meta.status),
            fact('Destination', meta.destination),
            fact('Kind', meta.kind === entry.event ? '' : meta.kind),
            fact('Policy', meta.policy === 'none' ? '' : meta.policy),
            fact('Ended', meta.ended_at),
            fact('Path', meta.target_path),
        ].filter(Boolean);

        return {
            key: `activity:${entry.source}:${entry.id}`,
            at: String(entry.timestamp || ''),
            agentId: '',
            agentName: String(entry.agent_name || 'System'),
            type: TYPES.indexOf(category) === -1 ? 'system' : category,
            text: String(entry.title || ''),
            meta: [sourceLabel(entry.source), entry.event].filter(Boolean).join(' · '),
            active: entry.is_active === true,
            expandable: facts.length > 0,
            diagnosticId: null,
            facts,
            json: formatMeta(meta),
        };
    }

    /**
     * One diagnostic summary as a LogRow.
     *
     * The type is decided by `error` being non-empty, NOT by `status`: the
     * dispatcher's crash path sets `error` and leaves `status` at its "success"
     * default, so a status check would miss the failures that matter most
     * (spec 5.1, verified against core/agent_loop/dispatcher.py).
     *
     * @param {object} row
     * @returns {LogRow}
     * @throws {Error} On a summary with no id.
     */
    function fromDiagnostic(row) {
        if (!row || row.id === undefined || row.id === null) {
            throw new Error('[log-shape] a diagnostic summary carries no id');
        }
        const id = String(row.id);
        const error = typeof row.error === 'string' ? row.error.trim() : '';
        const preview = triggerPreview(row.trigger_data);
        const facts = [
            fact('Source', sourceLabel('diagnostic')),
            fact('Trigger', triggerLabel(row.trigger_type)),
            fact('Mode', row.mode),
            fact('Status', row.status),
            fact('Model', row.model),
            fact('Tokens', row.total_tokens),
            fact('Duration', `${row.duration_ms || 0}ms`),
            fact('Preview', preview),
            fact('Error', error),
        ].filter(Boolean);

        return {
            key: `diagnostic:${id}`,
            at: String(row.created_at || ''),
            agentId: row.agent_id == null ? '' : String(row.agent_id),
            agentName: String(row.agent_name || 'System'),
            type: error ? 'error' : 'agent',
            text: `${triggerLabel(row.trigger_type)} → ${row.action_name || row.status || 'turn'}`,
            meta: [row.model || 'no model', `${row.duration_ms || 0}ms`,
                `${row.total_tokens || 0} tok`, error ? `— ${clip(error)}` : '']
                .filter(Boolean).join(' · '),
            active: false,
            expandable: true,
            diagnosticId: id,
            facts,
            // A diagnostic's payloads are sections of the fetched detail, not
            // one blob; nothing extra belongs here.
            json: '',
        };
    }

    return {
        TYPES, fromActivity, fromDiagnostic,
        sourceLabel, triggerLabel, triggerPreview, formatMeta,
    };
})();
