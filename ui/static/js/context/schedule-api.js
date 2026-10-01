/**
 * BossMod AI — the schedules transport: the routes the desk's Schedules
 * section and the schedule layer call (api/routes/schedules.py).
 *
 * The `api` function is injected on every call, as everywhere on the desk,
 * so the harness can script it. Each call resolves with the JSON body (or
 * null for a 204) and rejects with the server's own sentence: a structured
 * refusal's `detail` (Run now's 409), whose `reason` and `task_id` ride on
 * the Error as `.reason` and `.taskId`; anything else is worded by the shared
 * `window.BossModApi.formatError` (api-client.js: a string `detail`, a 422's
 * messages joined, else the status).
 */
const BossModScheduleApi = (() => {
    /**
     * Send one request; resolve with its JSON body, or null for a 204.
     * @throws {Error} (rejects) A structured refusal's sentence, with
     *   `.reason`/`.taskId`; else `window.BossModApi.formatError`'s sentence; or the
     *   HTTP status for a body that is not JSON.
     */
    async function request(api, url, init) {
        if (typeof api !== 'function') throw new Error('[schedule-api] api is required');
        const res = await api(url, init);
        if (res.status === 204) return null;
        const body = await res.json().catch((err) => { throw new Error(`HTTP ${res.status}: ${err.message}`); });
        if (res.ok) return body;
        const detail = body && body.detail;
        // The one shape the shared formatter does not read: {reason, detail, task_id}.
        if (detail && typeof detail === 'object' && !Array.isArray(detail) && typeof detail.detail === 'string') {
            throw Object.assign(new Error(detail.detail), { reason: detail.reason, taskId: detail.task_id || null });
        }
        throw new Error(window.BossModApi.formatError(body, res.status));
    }

    const json = (method, payload) => ({
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const schedulesOf = (agentId) => `/api/agents/${encodeURIComponent(agentId)}/schedules`;
    const scheduleUrl = (scheduleId) => `/api/schedules/${encodeURIComponent(scheduleId)}`;

    /**
     * One agent's schedules, oldest first.
     * @returns {Promise<object[]>}
     * @throws {Error} (rejects) On a refusal, or an answer that is not a list.
     */
    async function list(api, agentId) {
        const rows = await request(api, schedulesOf(agentId), { cache: 'no-store' });
        if (!Array.isArray(rows)) throw new Error('the schedules answer is not a list');
        return rows;
    }

    /** Create a schedule for an agent. @returns {Promise<object>} The stored view. */
    function create(api, agentId, payload) {
        return request(api, schedulesOf(agentId), json('POST', payload));
    }

    /** Change only the fields in `payload`. @returns {Promise<object>} The stored view. */
    function update(api, scheduleId, payload) {
        return request(api, scheduleUrl(scheduleId), json('PATCH', payload));
    }

    /** Delete a schedule. @returns {Promise<null>} */
    function remove(api, scheduleId) {
        return request(api, scheduleUrl(scheduleId), { method: 'DELETE' });
    }

    /**
     * Run the saved schedule once, now (enabled or not).
     * @returns {Promise<{schedule: object, task: object}>}
     * @throws {Error} (rejects) A 409 refusal carries `.reason` ('vacation'
     *   or 'open') and `.taskId` (the open task, for 'open').
     */
    function runNow(api, scheduleId) {
        return request(api, `${scheduleUrl(scheduleId)}/run`, { method: 'POST' });
    }

    /**
     * The draft rule's summary and next `count` runs; no side effects.
     * @returns {Promise<{summary: string, next_runs: string[]}>}
     * @throws {Error} (rejects) With the server's 422 sentence for a bad rule.
     */
    function preview(api, recurrence, count) {
        return request(api, '/api/schedules/preview', json('POST', { recurrence, count }));
    }

    return { list, create, update, remove, runNow, preview };
})();
