/**
 * BossMod AI — the agent `⋯`: one agent's rows, for the DM header and the
 * desk head alike.
 *
 * An agent used to have two `⋯` menus: the DM header's offered a door to the
 * desk, and the desk head's held everything else, each handler bound to an
 * object the desk owned — so an agent setting reached from a chat took two
 * `⋯` clicks with a desk in between. This is the one definition of the rows
 * (words, glyphs, order, sections, danger tones, disabled rules) and of the
 * operations behind them, so both menus render the same rows and every row
 * works from either surface. Sections: Agent (the door to the other surface,
 * Memory, Edit role, Auto-approve safe commands), Manage (Diagnostics, Reset
 * runtime, Remove agent) and Advanced (Yolo mode).
 *
 * The rows are the conversation chrome's action vocabulary (conversation/
 * chrome.js `apply`); the two switches stay conversation/approval-controls.js's,
 * where the Yolo-over-Global rule and the Yolo confirm live. The DM paints
 * them through chrome.js, the desk head through context/desk-menu.js.
 *
 * STATELESS ABOUT THE AGENT: the surface hands its record in, with a setter,
 * on every `actions()` call, and a flag save gives the server's answer back
 * through the setter and tells an open DM to refetch (operator_invalidate
 * `chat`). OPERATIONS THROW, SURFACES REPORT: a row's `onSelect` rejects with
 * the operator's line below after logging the cause, and a destructive row's
 * confirm resolves only once it has closed, so a failure after Confirm still
 * reaches the surface's error line. An instance holds one Memory layer
 * (context/desk-memory.js) and the one role form (context/agent-edit.js); a
 * desk destroys its own with the desk, and the shell's lives for the page.
 * Every other module is named at call time, never at load.
 */
const BossModAgentMenu = (() => {
    /** The Agent section's heading; callers receive it from `actions()`. */
    const MENU_TITLE = 'Agent';

    const RESET_TITLE = 'Reset this agent’s runtime?';
    const RESET_BODY = 'This cancels active work, clears queued triggers, resets the agent to '
        + 'idle, and may block the active task. Completed work history is preserved.';
    const REMOVE_TITLE = 'Remove this agent?';

    /** What a row says when its request fails; the cause goes to the console. */
    const FAILED = Object.freeze({
        reset: 'Could not reset the runtime. Try again.',
        remove: 'Could not remove this agent. Try again.',
        yolo: 'Could not change Yolo mode. Try again.',
        autoApprove: 'Could not change auto-approve. Try again.',
    });

    /** The rows' words; a destructive row's word is also its confirm button's. */
    const LABELS = Object.freeze({
        openDesk: 'Open desk',
        openChat: 'Open chat',
        memory: 'Memory',
        edit: 'Edit role',
        diagnostics: 'Diagnostics',
        reset: 'Reset runtime',
        remove: 'Remove agent',
    });

    /**
     * Ask before a destructive action.
     *
     * A core/overlays.js modal: a layer over the desk when one is open, so
     * Cancel comes back to it, or a dialog of its own over a chat. Its body
     * holds nothing focusable, so focus lands on the LAST action — Cancel,
     * the safe default, as in core/yolo-mode.js `confirmEnable`.
     *
     * @param {{title: string, body: string, confirm: string}} spec  `confirm`
     *   labels the danger button.
     * @returns {Promise<boolean>} True only when the operator chose that
     *   button; Cancel, Esc, ✕ and an outside click resolve false. It resolves
     *   once the dialog has closed, however it closed.
     */
    function confirmDestructive({ title, body, confirm }) {
        return new Promise((resolve) => {
            let chosen = false;
            BossModOverlays.createModal({
                title,
                body,
                closeOnBackdrop: true,
                actions: [
                    { label: confirm, tone: 'danger', onSelect: () => { chosen = true; } },
                    { label: 'Cancel', tone: 'quiet' },
                ],
                // Runs after the chosen action's onSelect: a choice not
                // recorded by then is a dismissal.
                onClose: () => resolve(chosen),
            });
        });
    }

    /**
     * Run one request, and say what failed in the operator's words.
     *
     * @param {string} failure  The line the rejection carries.
     * @param {() => Promise<*>} work
     * @returns {Promise<*>} What `work` resolved with.
     * @throws {Error} With `failure` as its message and the original error
     *   as its `cause`, after logging that cause.
     */
    async function attempt(failure, work) {
        try {
            return await work();
        } catch (err) {
            console.error(`[agent-menu] ${failure}`, err);
            throw new Error(failure, { cause: err });
        }
    }

    /**
     * Build one agent menu.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper, for the Memory
     *   layer's reads.
     * @param {object} deps.store  Application store: the roster names the
     *   agent, and Chat's open conversation is left when it is removed.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     *   Diagnostics leaves for the Log through it.
     * @param {(agentId: string) => void} [deps.openDesk]  The door to the
     *   desk (the chat's menu): the Open desk row.
     * @param {(agentId: string) => void} [deps.openChat]  The door to the
     *   chat (the desk's menu): the Open chat row. At most one door; with
     *   neither there is no door row.
     * @param {() => void} [deps.onRemoved]  Called after a removal through
     *   this menu, once Chat has left that agent's DM.
     * @param {() => void} [deps.onEditClosed]  Called after the role form
     *   closes, however it closed — but not when `destroy()` closed it.
     * @returns {{ actions: (agentId: string, state: {agent: object|null,
     *   onAgentSaved: (agent: object) => void}) => {menuTitle: string,
     *   actions: object[], badge: object|null},
     *   openMemory: (agentId: string, focusId?: number) => void,
     *   destroy: () => void }}
     *   `actions` builds the rows for one agent now (see below). `openMemory`
     *   opens that agent's Memory — a layer over a desk, or its own dialog
     *   over a chat — with `focusId`'s row highlighted. `destroy` closes the
     *   role form and the Memory layer and silences the owner's hooks.
     * @throws {Error} When `api`, `store` or `navigate` is missing, when both
     *   doors are given, or when a given optional is not a function.
     */
    function create(deps) {
        const { api, store, navigate, openDesk, openChat, onRemoved, onEditClosed } = deps || {};
        if (typeof api !== 'function') throw new Error('[agent-menu] deps.api is required');
        if (!store) throw new Error('[agent-menu] deps.store is required');
        if (typeof navigate !== 'function') throw new Error('[agent-menu] deps.navigate is required');
        if (openDesk !== undefined && openChat !== undefined) {
            throw new Error('[agent-menu] give deps.openDesk or deps.openChat, not both');
        }
        Object.entries({ openDesk, openChat, onRemoved, onEditClosed }).forEach(([name, fn]) => {
            if (fn !== undefined && typeof fn !== 'function') {
                throw new Error(`[agent-menu] deps.${name} must be a function`);
            }
        });

        /** The door to the other surface, or null: one row, never two. */
        const door = (openDesk && { id: 'agent-open-desk', label: LABELS.openDesk, icon: 'lamp-desk', open: openDesk })
            || (openChat && { id: 'agent-open-chat', label: LABELS.openChat, icon: 'message-circle', open: openChat })
            || null;
        /** `{agentId, layer}`: the Memory layer for the agent last asked about. */
        let memory = null;
        /** The open role form's handle, or null. One at a time. */
        let edit = null;
        /** Set by destroy(): the owner is gone, so its hooks must not run. */
        let destroyed = false;

        /** The roster row for `agentId`, or null while the roster lacks it. */
        const rosterRow = (agentId) => store.getState().roster
            .find((item) => item && item.id === agentId) || null;

        /** After a removal through this menu: Chat leaves that DM, then the owner hears. */
        function removed(agentId) {
            BossModAgentRoutes.closeConversation(store, agentId, 'agent');
            // A desk that closed while the DELETE was in flight is not the
            // one open now, which may be someone else's.
            if (onRemoved && !destroyed) onRemoved();
        }

        async function resetRuntime(agentId) {
            if (!(await confirmDestructive({ title: RESET_TITLE, body: RESET_BODY, confirm: LABELS.reset }))) return;
            await attempt(FAILED.reset, () => BossModAgentApi.apiResetRuntime(agentId));
        }

        async function removeAgent(agentId) {
            const row = rosterRow(agentId);
            // The row is disabled without it: the warning names whose files go.
            if (!row) throw new Error('[agent-menu] Remove needs the agent in the roster');
            const body = BossModAgentApi.agentDeleteWarning(String(row.name));
            if (!(await confirmDestructive({ title: REMOVE_TITLE, body, confirm: LABELS.remove }))) return;
            await attempt(FAILED.remove, () => BossModAgentApi.apiDeleteAgent(agentId));
            removed(agentId);
        }

        /**
         * Open the role form — one at a time, since a second Edit click would
         * stack two forms over one agent.
         * @param {string} agentId
         * @returns {void}
         */
        function openEdit(agentId) {
            if (edit) return;
            const row = rosterRow(agentId);
            // The row is disabled until the roster has the agent; one removed
            // elsewhere since the panel painted leaves nothing to edit.
            if (!row) return;
            edit = BossModAgentEdit.openAgentModal({
                agent: row,
                onClosed: () => {
                    edit = null;
                    if (!destroyed && onEditClosed) onEditClosed();
                },
                onDeleted: () => removed(agentId),
            });
        }

        /**
         * Open one agent's Memory, `focusId`'s row highlighted.
         *
         * @param {string} agentId
         * @param {number} [focusId]  A memory's number, as a chat line carries it.
         * @returns {void}
         * @throws {Error} On an empty agent id, or (context/desk-memory.js) a
         *   `focusId` that is not a memory number.
         */
        function openMemory(agentId, focusId) {
            if (!agentId) throw new Error('[agent-menu] openMemory needs an agent id');
            if (!memory || memory.agentId !== agentId) {
                if (memory) memory.layer.destroy();
                memory = { agentId, layer: BossModDeskMemory.createDeskMemory({ api, agentId }) };
            }
            memory.layer.open(focusId);
        }

        /**
         * Save one of the agent's flags, adopt the answer, and tell an open DM.
         *
         * @param {string} failure  The line a failed write rejects with.
         * @param {() => Promise<object>} write  Answers the saved agent.
         * @param {(agent: object) => void} onAgentSaved
         * @returns {Promise<void>}
         */
        async function saveFlag(failure, write, onAgentSaved) {
            const saved = await attempt(failure, write);
            // The flag changed on the server, so an open DM refetches — the
            // one under a desk, too (conversation-focus-invalidate.js).
            BossModOperatorInvalidate.notifyLocal(['chat']);
            onAgentSaved(saved);
        }

        /**
         * The agent's rows, for one paint of one surface's `⋯`.
         *
         * @param {string} agentId
         * @param {{agent: object|null, onAgentSaved: (agent: object) => void}} state
         *   `agent` is the surface's record of the agent, or null while it
         *   loads; `onAgentSaved` adopts the server's answer to a flag save.
         * @returns {{menuTitle: string, actions: object[], badge: object|null}}
         *   The Agent heading; the rows in panel order, each `slot: 'menu'`
         *   with its `section`; and the Yolo badge while it is on (null
         *   otherwise, and while `agent` is null).
         * @throws {Error} On an empty `agentId`, an `agent` that is neither
         *   null nor an object, or a missing `onAgentSaved`.
         */
        function actions(agentId, state) {
            if (!agentId) throw new Error('[agent-menu] actions() needs an agent id');
            const { agent, onAgentSaved } = state || {};
            if (agent === undefined || typeof agent !== 'object') {
                throw new Error('[agent-menu] state.agent must be the agent record, or null');
            }
            if (typeof onAgentSaved !== 'function') {
                throw new Error('[agent-menu] state.onAgentSaved is required');
            }
            // Edit role edits this row, and Remove names it: both wait for it.
            const row = rosterRow(agentId);
            const approvals = agent
                ? BossModApprovalControls.forAgent(agent, {
                    onAutoApprove: (enabled) => saveFlag(FAILED.autoApprove,
                        () => BossModAgentApi.apiSetCliAutoApprove(agentId, enabled), onAgentSaved),
                    // Called only once approval-controls has confirmed an ON.
                    onYolo: async (enabled) => {
                        // Confirmed over a desk that has closed since.
                        if (destroyed) return;
                        await saveFlag(FAILED.yolo,
                            () => BossModAgentApi.apiSetCliYolo(agentId, enabled), onAgentSaved);
                    },
                })
                : null;
            // Each glyph is written out as a literal name, so the Lucide
            // subset build checks it (scripts/build_lucide_subset.cjs).
            const doorRow = door
                ? [{ id: door.id, label: door.label, icon: door.icon, section: 'subject',
                    onSelect: async () => door.open(agentId) }]
                : [];
            const rows = doorRow.concat([
                { id: 'agent-memory', label: LABELS.memory, icon: 'brain', section: 'subject',
                    onSelect: async () => openMemory(agentId) },
                { id: 'agent-edit', label: LABELS.edit, icon: 'pencil', section: 'subject', disabled: !row,
                    onSelect: async () => openEdit(agentId) },
                { id: 'agent-diagnostics', label: LABELS.diagnostics, icon: 'activity', section: 'manage',
                    onSelect: async () => navigate('log', { agentId }) },
                { id: 'agent-reset-runtime', label: LABELS.reset, icon: 'rotate-ccw', section: 'manage',
                    tone: 'danger', onSelect: () => resetRuntime(agentId) },
                { id: 'agent-remove', label: LABELS.remove, icon: 'trash-2', section: 'manage',
                    tone: 'danger', disabled: !row, onSelect: () => removeAgent(agentId) },
            ]).map((action) => ({ ...action, slot: 'menu' }));
            if (approvals) rows.push(...approvals.actions);
            return { menuTitle: MENU_TITLE, actions: rows, badge: approvals ? approvals.badge : null };
        }

        return {
            actions,
            openMemory,

            /**
             * Close the role form and the Memory layer; the owner's hooks
             * stay silent from here on.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                if (edit) edit.close();
                edit = null;
                if (memory) memory.layer.destroy();
                memory = null;
            },
        };
    }

    return { create };
})();
