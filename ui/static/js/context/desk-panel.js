/**
 * BossMod AI — the body and head of one agent's desk, for the desk modal.
 *
 * It composes rather than renders: Tasks is context/desk-tasks.js, Files is
 * context/desk-files.js, Notes is context/desk-notes.js, Extensions is
 * context/desk-extensions.js (hidden while no per-agent extension is enabled)
 * and Details is context/desk-actions.js, each owning its own request and its
 * own load generation. It never touches the modal: context/desk-dialog.js
 * owns that lifecycle and places what this returns — the body, the avatar
 * lead and the head tools.
 *
 * THE SECTION VOCABULARY IS THIS FILE'S. section() below is the one header: a
 * label, an optional right-aligned action, and the content under it — the
 * task detail's section voice (sentence case, muted), so the two panel modals
 * read as one family. A module renders content and nothing else, so a header
 * cannot drift one module at a time.
 *
 * TWO COLUMNS: the work (Tasks, Files) in the main column, and who the agent
 * is (About, Details, Notes, Extensions) in the aside. The actions on the
 * agent are the HEAD's, as in every other modal: Chat and Edit role as icon
 * tools, and Diagnostics, Reset runtime and Remove behind the `⋯`
 * (places/tasks/task-detail.js's pattern). Destructive actions belong behind
 * a menu and a confirm, not at the bottom of the reading flow.
 *
 * The done/fail contract is a DISCLOSURE: the agent's role contract is
 * reference material read once, not a standing alert. Editing the role opens
 * a layer over the desk (context/agent-edit.js), so the operator comes back to
 * the desk they opened.
 *
 * A TASK ROW opens the task as a layer over the desk too
 * (context/desk-task-opener.js), so ‹ comes back here; it used to switch to
 * the Tasks place, closing the desk with no way back. See all, Diagnostics
 * and Chat still leave: they are places, not details.
 */
const BossModDeskPanel = (() => {
    const { h, clear } = BossModDom;

    const NO_SPECIALTY = 'No specialty';
    const DONE_BAR_TITLE = 'What done looks like for this agent:';
    const NO_DONE_BAR = 'No done/fail bar set for this agent yet. Edit the role to add one.';
    const CONTRACT_SUMMARY = 'Done/fail contract';
    /** The head tools' accessible names — and their tooltips: one string each. */
    const LABELS = Object.freeze({ chat: 'Open chat', edit: 'Edit role', options: 'Desk options' });

    /**
     * One labelled section: a header row, an optional right-aligned action,
     * and the content under it.
     *
     * @param {string} title
     * @param {{label: string, icon?: string, onSelect: () => void}|null} action
     * @param {HTMLElement} content
     * @returns {HTMLElement}
     */
    function section(title, action, content) {
        return h('section', { class: 'desk-section' },
            h('div', { class: 'desk-section-head' },
                h('h3', {}, title),
                action
                    ? h('button', { class: 'btn-link desk-section-action', type: 'button', onclick: action.onSelect },
                        action.label,
                        action.icon ? h('i', { 'data-lucide': action.icon, 'aria-hidden': 'true' }) : null)
                    : null),
            content);
    }

    /** An icon-only head tool: the frame's icon-button shape. */
    function tool(id, icon, label, onclick, extra) {
        return h('button', {
            class: 'header-icon-btn', id, type: 'button',
            'aria-label': label, 'data-tooltip': label, onclick, ...(extra || {}),
        }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }));
    }

    /** A `⋯` row: glyph and label, `danger` for a destructive one. */
    function menuRow(id, icon, label, onclick, danger) {
        return h('button', {
            class: 'menu-action', id, type: 'button', 'data-tone': danger ? 'danger' : null, onclick,
        }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }), label);
    }

    /**
     * Build one agent's desk.
     *
     * @param {object} deps
     * @param {object}   deps.store  Application store; the roster names the agent.
     * @param {object}   deps.bus    Topic bus: the file browser's live writes
     *   and the agent's lane presence.
     * @param {Function} deps.api    Authenticated fetch helper.
     * @param {(placeId: string, params?: object) => void} deps.navigate  Leaves
     *   for another place. The dialog hands in one that closes the desk — and
     *   everything stacked on it — first.
     * @param {string}   deps.agentId
     * @param {string}   deps.initialPath  Where the file browser opens.
     * @param {() => void} deps.onRemoved  The agent was deleted — from Remove,
     *   from the role form's Delete, or elsewhere while this was open.
     * @param {(name: string) => void} deps.setTitle  The agent was renamed.
     * @param {(id: string, kind: string) => void} deps.openConversation
     *   Leaves for a conversation: the head's Chat tool (this agent's, kind
     *   `agent`) and a task layer's chat (the task's own target). The dialog
     *   hands in one that closes the desk and its layers first.
     * @returns {{ element: HTMLElement, lead: HTMLElement, tools: HTMLElement[],
     *   measure: () => void, destroy: () => void }} `lead` is the avatar for
     *   the title row (decorative: the name is the title). `measure` decides
     *   the description's clamp and must be called once the body is mounted.
     *   `destroy` also closes any task layer the desk opened.
     * @throws {Error} When any dependency is missing.
     */
    function createDeskPanel(deps) {
        const {
            store, bus, api, navigate, agentId, initialPath, onRemoved, setTitle, openConversation,
        } = deps || {};
        if (!store) throw new Error('[desk-panel] deps.store is required');
        if (!bus) throw new Error('[desk-panel] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[desk-panel] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[desk-panel] deps.navigate is required');
        if (!agentId) throw new Error('[desk-panel] deps.agentId is required');
        if (!initialPath) throw new Error('[desk-panel] deps.initialPath is required');
        if (typeof onRemoved !== 'function') throw new Error('[desk-panel] deps.onRemoved is required');
        if (typeof setTitle !== 'function') throw new Error('[desk-panel] deps.setTitle is required');
        if (typeof openConversation !== 'function') {
            throw new Error('[desk-panel] deps.openConversation is required');
        }

        const disposers = [];
        /** The roster row for this agent, or null while the roster is loading. */
        const agent = () => store.getState().roster.find((item) => item && item.id === agentId) || null;

        const lead = h('span', { class: 'desk-lead' });
        const roleEl = h('p', { class: 'desk-role' });
        const pillEl = h('span', { class: 'status-pill' });
        const aboutEl = h('div', { class: 'desk-about' });
        // Built once and refilled, so opening the disclosure survives a roster
        // tick: a <details> replaced on every world_update would snap shut
        // under an operator who had just opened it.
        const contractEl = h('div', { class: 'callout-body desk-bar' });

        const tasks = BossModDeskTasks.createDeskTasks({
            api, agentId, onOpenTask: (taskId) => { void taskOpener.open(taskId); },
        });
        // A row opens its task as a layer over this desk (context/desk-task-opener.js).
        const taskOpener = BossModDeskTaskOpener.create({ store, api, tasks, openConversation });
        const actions = BossModDeskActions.createDeskActions({ api, agentId, onRemoved });
        const files = BossModDeskFiles.createDeskFiles({ api, bus, agentId });
        // A folder inside /me/notes is the browser's job, not a second one.
        const notes = BossModDeskNotes.createDeskNotes({
            api, agentId, onOpenFolder: (path) => { void files.open(path); },
        });
        /** Built below; null until then, so an early onChange is a no-op. */
        let extensionsSection = null;
        const deskExtensions = BossModDeskExtensions.createDeskExtensions({
            agentId,
            agentName: () => { const who = agent(); return who ? String(who.name) : ''; },
            onChange: () => { if (extensionsSection) extensionsSection.hidden = deskExtensions.isEmpty(); },
        });
        /** The open role dialog, the open `⋯`, or null. One of each at a time. */
        let edit = null;
        let menu = null;
        /** Server lane note. Queued replaces the status label until a lane frees. */
        let laneNote = '';
        /** Set before teardown closes the dialog, so its onClosed does nothing. */
        let destroyed = false;
        /** The description on screen, its clamp, and whether it may measure yet. */
        let shownDescription = null;
        let clamp = null;
        let mounted = false;
        /** What the title says, and whether the agent has been seen at all. */
        let shownName = agent() ? String(agent().name) : null;
        let seen = Boolean(agent());

        const optionsBtn = tool('desk-options', 'ellipsis', LABELS.options, () => toggleOptions(),
            { 'aria-haspopup': 'dialog', 'aria-expanded': 'false' });
        const editBtn = tool('desk-edit', 'pencil', LABELS.edit, () => openEdit());
        const tools = [
            tool('desk-chat', 'message-circle', LABELS.chat, () => openConversation(agentId, 'agent')),
            editBtn,
            optionsBtn,
        ];

        const element = h('div', { class: 'desk' },
            h('div', { class: 'desk-grid' },
                h('div', { class: 'desk-main' },
                    section('Tasks', {
                        label: 'See all',
                        icon: 'chevron-right',
                        // Where "all of this agent's tasks" lives is a
                        // panel-level fact, not the list's.
                        onSelect: () => navigate('tasks', { agentFilter: agentId }),
                    }, tasks.element),
                    section('Files', null, files.element)),
                h('aside', { class: 'desk-aside', 'aria-label': 'About this agent' },
                    section('About', null, h('div', { class: 'desk-about-block' },
                        roleEl, pillEl, aboutEl,
                        h('details', { class: 'desk-contract' },
                            h('summary', {},
                                h('i', { 'data-lucide': 'scroll-text', 'aria-hidden': 'true' }),
                                CONTRACT_SUMMARY),
                            h('div', { class: 'callout', 'data-tone': 'warn' }, contractEl)))),
                    section('Details', null, actions.element),
                    section('Notes', null, notes.element),
                    extensionsSection = section('Extensions', null, deskExtensions.element))));
        extensionsSection.hidden = deskExtensions.isEmpty();

        /** Repaint who the agent is; the description only when its text changed. */
        function renderProfile() {
            const who = agent();
            clear(lead);
            clear(contractEl);
            // A role form needs the row it edits; until the roster has it,
            // Edit role is withheld rather than live and doing nothing.
            editBtn.disabled = !who;
            if (!who) {
                roleEl.textContent = 'Loading this desk…';
                pillEl.hidden = true;
                contractEl.append(NO_DONE_BAR);
                return;
            }
            lead.append(BossModAvatar.create({ name: who.name, color: who.color, size: 'md' }));
            roleEl.textContent = String(who.role || NO_SPECIALTY);
            pillEl.hidden = false;
            // The agent's status is the tone; an unknown one is the neutral pill.
            pillEl.setAttribute('data-status', String(who.status || ''));
            pillEl.textContent = laneNote
                || BossModAgentStatus.getStatusLabel(who.status, who.currentActivityKind);
            const description = who.description ? String(who.description) : '';
            if (description !== shownDescription) {
                shownDescription = description;
                clear(aboutEl);
                clamp = description
                    ? BossModClampedMarkdown.create({ text: description, moreLabel: 'Show more' })
                    : null;
                if (clamp) aboutEl.append(clamp.element);
                if (clamp && mounted) clamp.measure();
            }
            const bar = who.done_fail_bar ? String(who.done_fail_bar).trim() : '';
            contractEl.append(bar ? `${DONE_BAR_TITLE} ${bar}` : NO_DONE_BAR);
        }

        /**
         * Show the `⋯` panel, or put it away. Picking a row closes the panel
         * first, so focus is back on the `⋯` and a confirmation layer returns
         * there.
         * @returns {void}
         */
        function toggleOptions() {
            if (menu) {
                menu.close();
                return;
            }
            const pick = (run) => () => { menu.close(); run(); };
            menu = BossModMenu.createMenu({
                anchor: optionsBtn,
                label: LABELS.options,
                items: [h('div', { class: 'menu-actions' },
                    menuRow('desk-diagnostics', 'activity', 'Diagnostics',
                        pick(() => navigate('log', { agentId }))),
                    menuRow('desk-reset-runtime', 'rotate-ccw', 'Reset runtime',
                        pick(() => actions.confirmReset()), true),
                    menuRow('desk-remove', 'trash-2', 'Remove agent',
                        pick(() => actions.confirmRemove()), true))],
                container: optionsBtn.closest('.modal-head'),
                onClose: () => {
                    menu = null;
                    optionsBtn.setAttribute('aria-expanded', 'false');
                },
            });
            optionsBtn.setAttribute('aria-expanded', 'true');
            BossModIcons.paint(menu.element, 'desk-panel');
        }

        /**
         * Open the role form as a layer over the desk. One at a time — a
         * second Edit click would stack two forms over one agent.
         * @returns {void}
         */
        function openEdit() {
            if (edit) return;
            // The tool is disabled until the roster has this agent, and
            // openAgentModal throws on a missing one rather than creating.
            edit = BossModAgentEdit.openAgentModal({ agent: agent(), onClosed: closeEdit, onDeleted: onRemoved });
        }

        function closeEdit() {
            edit = null;
            // destroy() closes the dialog on its way out; refreshing sections
            // of a desk being torn down would fire requests nothing will paint.
            if (destroyed) return;
            renderProfile();
            void actions.refresh();
            // A role edit can rewrite the workspace; re-read it.
            void notes.refresh();
        }

        disposers.push(store.subscribe((s) => s.roster, () => {
            const who = agent();
            // Deleted elsewhere while this was open: the desk closes rather
            // than saying "Loading…" forever. Only once it has been SEEN, so a
            // roster still loading is not mistaken for a removal.
            if (!who && seen) {
                onRemoved();
                return;
            }
            if (who) {
                seen = true;
                if (String(who.name) !== shownName) {
                    shownName = String(who.name);
                    setTitle(shownName);
                }
            }
            renderProfile();
        }));
        disposers.push(bus.subscribe('agent_presence', (data) => {
            if (!data || data.agent_id !== agentId) return;
            const ahead = Math.max(0, Number(data.ahead) || 0);
            laneNote = data.phase === 'queued' ? `Queued (${ahead} ahead)` : '';
            renderProfile();
        }));

        renderProfile();
        void files.open(initialPath);
        // Built once; the sections that rebuild their own glyphs paint them.
        BossModIcons.paint(element, 'desk-panel');

        return {
            element,
            lead,
            tools,

            /** Decide the description's clamp, now that it has a height. */
            measure() {
                mounted = true;
                if (clamp) clamp.measure();
            },

            /**
             * Drain this desk and its sections, and close what it opened.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                disposers.splice(0).forEach((off) => off());
                if (menu) menu.close();
                actions.destroy();
                // A dialog outliving the desk that opened it would edit an
                // agent nobody is looking at any more.
                if (edit) edit.close();
                edit = null;
                // Nor may a task layer outlive the desk it was opened over.
                taskOpener.destroy();
                tasks.destroy();
                files.destroy();
                notes.destroy();
                deskExtensions.destroy();
            },
        };
    }

    return { createDeskPanel };
})();
