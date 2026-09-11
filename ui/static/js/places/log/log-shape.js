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

    /** Longest reply (or trigger) preview shown on a collapsed row. */
    const PREVIEW_CHARS = 120;

    /** Activity titles that hide the transcript behind a canned status line. */
    const CANNED_REPLY_ACTIVITY = /answered the request\s*$/;

    /** Closest-turn window when pairing a canned activity row to a diagnostic. */
    const ACTIVITY_TURN_MATCH_MS = 120000;

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

    /**
     * Collapsed-row preview: flatten whitespace, then clip to PREVIEW_CHARS.
     *
     * @param {string} text
     * @returns {string}
     */
    function previewText(text) {
        const value = String(text || '').replace(/\s+/g, ' ').trim();
        return clip(value);
    }

    function parseJsonish(raw) {
        if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw;
        if (typeof raw !== 'string') return null;
        let text = raw.trim();
        if (!text) return null;
        if (text.startsWith('```')) {
            text = text.split('\n')
                .filter((line) => !line.trim().startsWith('```'))
                .join('\n').trim();
        }
        try {
            const parsed = JSON.parse(text);
            return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
        } catch (err) {
            const start = text.indexOf('{');
            const end = text.lastIndexOf('}');
            if (start < 0 || end <= start) return null;
            try {
                const parsed = JSON.parse(text.slice(start, end + 1));
                return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
            } catch (inner) {
                return null;
            }
        }
    }

    function replyFrom(raw) {
        const parsed = parseJsonish(raw);
        if (!parsed) return '';
        for (const key of ['msg', 'reply']) {
            if (typeof parsed[key] === 'string' && parsed[key].trim()) return parsed[key];
        }
        if (parsed.data && typeof parsed.data === 'object' && typeof parsed.data.msg === 'string'
            && parsed.data.msg.trim()) {
            return parsed.data.msg;
        }
        for (const key of ['content', 'followUpMessage']) {
            if (typeof parsed[key] === 'string' && parsed[key].trim()) return parsed[key];
        }
        return '';
    }

    /**
     * The model-facing ``msg`` from one or more diagnostic payloads.
     *
     * A bare string that is already the reply (the summary field) is returned
     * as-is. JSON blobs are probed for ``msg``, then ``reply``, then
     * ``data.msg``. Trigger payloads are never passed in.
     *
     * @param {...(string|object|null|undefined)} payloads
     * @returns {string}
     */
    function extractReply(...payloads) {
        for (const raw of payloads) {
            if (raw == null || raw === '') continue;
            if (typeof raw === 'string') {
                const fromJson = replyFrom(raw);
                if (fromJson) return fromJson;
                // Summary rows carry the extracted reply as a plain string.
                // JSON that failed to yield a msg must not become the preview.
                const trimmed = raw.trim();
                if (trimmed && !trimmed.startsWith('{') && !trimmed.startsWith('[')
                    && !trimmed.startsWith('```')) {
                    return raw;
                }
                continue;
            }
            const text = replyFrom(raw);
            if (text) return text;
        }
        return '';
    }

    /**
     * Activity rows whose title is only "X answered the request".
     *
     * @param {object} row  A LogRow.
     * @returns {boolean}
     */
    function isCannedReplyActivity(row) {
        return CANNED_REPLY_ACTIVITY.test(String(row && row.text || ''));
    }

    /**
     * Point canned activity rows at the matching diagnostic so expand shows Reply.
     *
     * Mutates the activity rows in place. Each diagnostic is used at most once.
     *
     * @param {object[]} activityRows
     * @param {object[]} diagnosticRows
     * @returns {object[]} the same activityRows array.
     */
    function linkActivityRows(activityRows, diagnosticRows) {
        const unused = (diagnosticRows || []).filter((row) => row && row.diagnosticId);
        const taken = new Set();
        (activityRows || []).forEach((row) => {
            if (!isCannedReplyActivity(row)) return;
            let best = null;
            let bestDelta = Infinity;
            unused.forEach((diag) => {
                if (taken.has(diag.key)) return;
                if (row.agentName && diag.agentName && row.agentName !== diag.agentName) return;
                const delta = Math.abs(Date.parse(row.at || '') - Date.parse(diag.at || ''));
                if (Number.isNaN(delta) || delta > ACTIVITY_TURN_MATCH_MS) return;
                if (delta < bestDelta) {
                    best = diag;
                    bestDelta = delta;
                }
            });
            if (!best) return;
            taken.add(best.key);
            row.diagnosticId = best.diagnosticId;
            row.expandable = true;
            if (!row.facts.some((entry) => entry.label === 'Turn')) {
                const turn = fact('Turn', best.diagnosticId);
                if (turn) row.facts.push(turn);
            }
        });
        return activityRows;
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
        const reply = extractReply(row.reply);
        const heading = `${triggerLabel(row.trigger_type)} → ${row.action_name || row.status || 'turn'}`;
        const preview = previewText(reply) || triggerPreview(row.trigger_data);
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
            text: preview || heading,
            meta: [preview && preview !== heading ? heading : '',
                row.model || 'no model', `${row.duration_ms || 0}ms`,
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
        extractReply, previewText, linkActivityRows, isCannedReplyActivity,
        PREVIEW_CHARS,
    };
})();
