/**
 * BossMod AI — floor and vacation requests.
 *
 * I/O only: the header switcher, the floor settings and the On vacation
 * view draw; this file talks to /api/floors, the active thread list, and the
 * agent vacation routes.
 * Every non-2xx becomes a thrown Error carrying the server's own message,
 * so a caller can never mistake a refused request for an empty answer.
 */
const BossModFloorApi = (() => {

    /**
     * Turn a refused response into an Error with the server's words.
     *
     * FastAPI answers an HTTPException as `{detail: "..."}`; the floor delete's
     * 409 is `{code, agent_count, message}`. A body that is not JSON keeps its
     * status in the message rather than becoming a blank error.
     *
     * @param {Response} res
     * @returns {Promise<Error>} With `status`, and `code` / `agentCount` when
     *   the server named them.
     */
    async function failure(res) {
        const raw = await res.text();
        let body = null;
        try {
            body = raw ? JSON.parse(raw) : null;
        } catch (err) {
            body = null;
        }
        const detail = body && typeof body.detail === 'string' ? body.detail : '';
        const message = (body && body.message) || detail || raw || `Request failed (HTTP ${res.status})`;
        const error = new Error(message);
        error.status = res.status;
        if (body && body.code) error.code = body.code;
        if (body && typeof body.agent_count === 'number') error.agentCount = body.agent_count;
        return error;
    }

    /**
     * Build the floor requests over an injected fetch.
     *
     * @param {object} deps
     * @param {Function} deps.apiFetch  The shell's authenticated fetch.
     * @returns {{
     *   listFloors: () => Promise<Array<{id: string, name: string}>>,
     *   createFloor: (name: string) => Promise<object>,
     *   renameFloor: (id: string, name: string) => Promise<object>,
     *   deleteFloor: (id: string, occupants: ('send_home'|'delete'|null)) => Promise<object>,
     *   listVacation: () => Promise<Array<object>>,
     *   returnFromVacation: (agentId: string, floorId: string) => Promise<object>,
     *   listActiveThreads: () => Promise<Array<object>>,
     *   planMove: (floorId: string, choice: MoveChoice) => Promise<object>,
     *   applyMove: (floorId: string, choice: MoveChoice, fingerprint: string) => Promise<object>,
     *   listProjects: (floorId: string) => Promise<Array<{name: string, modified_at: string}>>,
     *   moveProject: (floorId: string, project: string, fromFloorId: string) => Promise<object>,
     * }} Each rejects with the Error from `failure` on a non-2xx, and with the
     *   network error itself when the request never completed. A move whose
     *   plan went stale rejects with `code` `plan_changed`. `MoveChoice` is
     *   `{agentIds: string[], channelIds: string[], excludeCompanionIds: string[]}`:
     *   what moves TO `floorId`.
     * @throws {Error} When apiFetch is missing.
     */
    function createFloorApi(deps) {
        const { apiFetch } = deps || {};
        if (typeof apiFetch !== 'function') throw new Error('[floor-api] deps.apiFetch is required');

        async function json(url, init) {
            const res = await apiFetch(url, init);
            if (!res.ok) throw await failure(res);
            return res.json();
        }

        function send(method, url, body) {
            return json(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        }

        /** Every floor, Lobby first. */
        async function listFloors() {
            const rows = await json('/api/floors');
            if (!Array.isArray(rows) || rows.length === 0) {
                // Lobby always exists server-side; an empty list is a broken answer.
                throw new Error('The server returned no floors.');
            }
            return rows;
        }

        /** The move body the server reads; one shape for the plan and the apply. */
        function moveBody(choice) {
            const { agentIds = [], channelIds = [], excludeCompanionIds = [] } = choice || {};
            return {
                agent_ids: agentIds,
                channel_ids: channelIds,
                exclude_companion_ids: excludeCompanionIds,
            };
        }

        const floorUrl = (id, rest) => `/api/floors/${encodeURIComponent(id)}${rest}`;

        return {
            listFloors,
            createFloor: (name) => send('POST', '/api/floors', { name }),
            renameFloor: (id, name) => send('PATCH', `/api/floors/${encodeURIComponent(id)}`, { name }),
            deleteFloor: (id, occupants) => {
                const qs = occupants ? `?occupants=${encodeURIComponent(occupants)}` : '';
                return json(`/api/floors/${encodeURIComponent(id)}${qs}`, { method: 'DELETE' });
            },
            listVacation: () => json('/api/agents/vacation'),
            returnFromVacation: (agentId, floorId) => send(
                'POST', `/api/agents/${encodeURIComponent(agentId)}/return`, { floor_id: floorId },
            ),
            listActiveThreads: () => json('/api/channels?status=active', { cache: 'no-store' }),
            planMove: (floorId, choice) => send('POST', floorUrl(floorId, '/move-plan'), moveBody(choice)),
            applyMove: (floorId, choice, fingerprint) => send(
                'POST', floorUrl(floorId, '/move'), { ...moveBody(choice), fingerprint },
            ),
            listProjects: (floorId) => json(floorUrl(floorId, '/projects'), { cache: 'no-store' }),
            moveProject: (floorId, project, fromFloorId) => send(
                'POST', floorUrl(floorId, '/projects/move'), { project, from_floor_id: fromFloorId },
            ),
        };
    }

    return { createFloorApi };
})();
