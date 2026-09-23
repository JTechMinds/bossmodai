/**
 * BossMod AI — the task detail dialog.
 *
 * A panel modal whose body is one centred column: the status line, the facts,
 * the one callout the state calls for, the task itself, the deliverables, the
 * subtasks, what counts as done, and the activity. The title is the modal's
 * own head and is not repeated below it. The sections are pure builders in
 * task-detail-sections.js; this file composes them, owns the modal, and owns
 * the head's `⋯`.
 *
 * It never ends a task itself. Cancel lives behind the `⋯` and only asks: the
 * detail is handed `onCancel` at construction and never calls the cancel
 * route, so there is exactly one place in Tasks that can end a task.
 */
const BossModTaskDetail = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModTasksColumns;
    const SECTIONS = BossModTaskDetailSections;

    /** The `⋯`'s accessible name, its tooltip, and its panel's name. */
    const OPTIONS_LABEL = 'Task options';

    /**
     * Open one task in the shared modal.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.taskId
     * @param {object[]} deps.tasks  The place's current list, used to resolve
     *   the parent and children without a second request.
     * @param {(agentId: string) => (string|undefined)} deps.colorOf  Roster
     *   colours for the avatars in the facts and the activity.
     * @param {(taskId: string) => void} deps.onNavigate  Opens a related task
     *   as a layer over this one.
     * @param {(task: object) => void} deps.onCancel  The Tasks place owns
     *   cancelling; this panel only asks for it.
     * @param {(task: object) => void} deps.onOpenChat  Leaves for the task's
     *   conversation.
     * @param {() => void} [deps.onClose]
     * @returns {{close: () => void}}
     * @throws {Error} When a dependency is missing, or when the task id is not
     *   in the list — opening a panel for a task nobody can name would show an
     *   empty modal with no explanation.
     */
    function openTaskDetail(deps) {
        const {
            api, taskId, tasks, colorOf, onNavigate, onCancel, onOpenChat, onClose,
        } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-detail] deps.api is required');
        if (typeof colorOf !== 'function') throw new Error('[task-detail] deps.colorOf is required');
        if (typeof onNavigate !== 'function') throw new Error('[task-detail] deps.onNavigate is required');
        if (typeof onCancel !== 'function') throw new Error('[task-detail] deps.onCancel is required');
        if (typeof onOpenChat !== 'function') throw new Error('[task-detail] deps.onOpenChat is required');
        const task = (tasks || []).find((item) => item.id === taskId);
        if (!task) throw new Error(`[task-detail] no task "${taskId}" in the current list`);

        const children = tasks.filter((item) => item.parent_task_id === task.id);
        const events = BossModTaskEvents.createTaskEvents({ api, taskId: task.id, colorOf });
        const instructions = SECTIONS.instructions(task);

        const body = h('div', { class: 'task-detail' },
            h('div', { class: 'task-detail-column' },
                SECTIONS.statusLine(task),
                SECTIONS.facts(task, { tasks, colorOf, onNavigate }),
                SECTIONS.callout(task, { onOpenChat }),
                instructions,
                SECTIONS.deliverables(task, children, api),
                SECTIONS.subtasks(children, onNavigate),
                SECTIONS.doneContract(task),
                events.element));

        // A finished task can no longer be cancelled, and Cancel is all the
        // `⋯` holds — so a finished task has no `⋯` rather than an empty one.
        const optionsButton = COLUMNS.isTerminal(task.status) ? null : h('button', {
            class: 'header-icon-btn',
            id: 'task-options',
            type: 'button',
            'aria-label': OPTIONS_LABEL,
            'data-tooltip': OPTIONS_LABEL,
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggleOptions(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));

        /** The open `⋯` panel, or null. */
        let menu = null;

        const panel = BossModOverlays.createModal({
            title: task.title || 'Task',
            body,
            size: 'panel',
            tools: optionsButton ? [optionsButton] : [],
            // Read-only apart from Cancel task, which asks its own question:
            // nothing here can be lost to an outside click.
            actions: [],
            closeOnBackdrop: true,
            onClose: () => {
                // The panel hangs off this modal's head; its listeners must
                // not outlive the head they were put on.
                if (menu) menu.close();
                events.destroy();
                if (onClose) onClose();
            },
        });

        /**
         * Show Cancel, or put it away. Picking it closes the panel first, so
         * focus is back on the `⋯` and the confirmation layer returns there.
         * @returns {void}
         */
        function toggleOptions() {
            if (menu) {
                menu.close();
                return;
            }
            menu = BossModMenu.createMenu({
                anchor: optionsButton,
                label: OPTIONS_LABEL,
                items: [h('div', { class: 'menu-actions' },
                    h('button', {
                        class: 'menu-action',
                        id: 'ct-cancel-task-btn',
                        type: 'button',
                        onclick: () => { menu.close(); onCancel(task); },
                    }, 'Cancel task'))],
                container: panel.element.querySelector('.modal-head'),
                onClose: () => {
                    menu = null;
                    optionsButton.setAttribute('aria-expanded', 'false');
                },
            });
            optionsButton.setAttribute('aria-expanded', 'true');
        }

        // Measured once the modal is on screen, which is the first moment the
        // instructions have a height to compare against.
        if (instructions) SECTIONS.measureClamp(instructions);
        BossModIcons.paint(panel.element, 'task-detail');

        return { close: panel.close };
    }

    return { openTaskDetail };
})();
